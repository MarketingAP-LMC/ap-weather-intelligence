"""Group county/crop assessments into events, detect changes and decide notifications.

Event = one hazard in one state during one episode. Its key stays stable while the
episode continues so repeated forecast cycles update the same event instead of
creating duplicates. Notifications are sent for new HIGH/CRITICAL events, upgrades,
and material changes (area expansion, earlier timing, intensification) after a cooldown.
"""
import hashlib
from datetime import timedelta

from ..config import level_rank, max_level
from ..db import jdump, jload
from ..timeutil import hours_between, iso, parse_iso

KEY_METRIC = {
    "freeze": ("tmin7d", "min"), "extreme_cold": ("tmin7d", "min"), "heat": ("tmax7d", "max"),
    "heavy_rain": ("qpf72", "max"), "harvest_rain": ("qpf72", "max"), "severe_storm": ("spc_max", "max"),
    "high_wind": ("gust7d", "max"), "winter_storm": ("snow72", "max"), "drought": ("dm_d2", "max"),
    "outlook_cold": ("cpc_t814_below", "max"), "outlook_wet": ("cpc_p814_above", "max"),
    "outlook_dry": ("cpc_p814_below", "max"),
}
EXTRA_METRICS = {
    "freeze": ["hrs_le_32", "hrs_le_28", "run_le_28", "hrs_le_26", "hrs_le_24"],
    "extreme_cold": ["hrs_le_10", "hrs_le_5", "hrs_le_0"],
    "heat": ["days_ge_95", "days_ge_100"],
    "heavy_rain": ["qpf7d", "wpc_qpf72", "ero_max", "obs_pcpn7"],
    "harvest_rain": ["qpf7d", "pop72"],
    "severe_storm": ["spc_hail", "spc_wind", "spc_torn", "spc_sig", "gust72"],
    "high_wind": ["gust72"],
    "winter_storm": ["snow7d", "ice72"],
    "drought": ["dm_d1", "dm_d3", "dm_dsci", "dm_chg1", "dm_chg4", "obs_pcpn14"],
}


def _agg(values, how):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return min(vals) if how == "min" else max(vals)


def cluster(assessments, flats, counties, now, hazard_labels, crops_cfg):
    groups = {}
    for a in assessments:
        if level_rank(a["level"]) < level_rank("INFORMATIONAL"):
            continue
        groups.setdefault((a["hazard"], a["state"]), []).append(a)
    events = []
    for (hazard, state), items in groups.items():
        level = max_level(*[a["level"] for a in items])
        cnty = {}
        crops = {}
        for a in items:
            c = cnty.setdefault(a["fips"], {"fips": a["fips"], "name": a["county"], "level": "NONE", "crops": [],
                                            "lead_h": None, "region": counties.get(a["fips"], {}).get("region")})
            c["level"] = max_level(c["level"], a["level"])
            if a["crop"] not in c["crops"]:
                c["crops"].append(a["crop"])
            if a["lead_h"] is not None:
                c["lead_h"] = a["lead_h"] if c["lead_h"] is None else min(c["lead_h"], a["lead_h"])
            cr = crops.setdefault(a["crop"], {"crop": a["crop"], "level": "NONE", "counties": 0, "acres": 0,
                                              "exposure": 0.0, "stage_detail": a["stage_detail"],
                                              "stage_basis": a["stage_basis"], "intensity": "NONE",
                                              "reasons": a["reasons"], "phases": a["phases"]})
            if level_rank(a["level"]) > level_rank(cr["level"]):
                cr["reasons"] = a["reasons"]
            cr["level"] = max_level(cr["level"], a["level"])
            cr["intensity"] = max_level(cr["intensity"], a["intensity"])
            cr["counties"] += 1
            cr["acres"] += a["acres"]
            cr["exposure"] = max(cr["exposure"], a["exposure"])
        key_metric, how = KEY_METRIC.get(hazard, (None, "max"))
        fips_list = sorted(cnty.keys())
        stats = {}
        if key_metric:
            stats[key_metric] = _agg([flats.get(f, {}).get(key_metric) for f in fips_list], how)
            worst = None
            for f in fips_list:
                v = flats.get(f, {}).get(key_metric)
                if v is None:
                    continue
                if worst is None or (v < worst[1] if how == "min" else v > worst[1]):
                    worst = (f, v)
            if worst:
                stats["worst_fips"] = worst[0]
                stats["worst_county"] = counties.get(worst[0], {}).get("name")
        for m in EXTRA_METRICS.get(hazard, []):
            stats[m] = _agg([flats.get(f, {}).get(m) for f in fips_list], "max")
        leads = [c["lead_h"] for c in cnty.values() if c["lead_h"] is not None]
        start = now + timedelta(hours=min(leads)) if leads else now
        end = now + timedelta(hours=max(leads) + 24) if leads else now + timedelta(hours=72)
        top_level_counties = [c for c in cnty.values() if c["level"] == level]
        crop_names = [crops_cfg["crops"][k]["label"] for k in sorted(crops, key=lambda k: -level_rank(crops[k]["level"]))]
        title = f"{hazard_labels.get(hazard, hazard)} - {state}"
        events.append({
            "hazard": hazard, "state": state, "level": level, "title": title,
            "counties": sorted(cnty.values(), key=lambda c: (-level_rank(c["level"]), c["name"])),
            "crops": crops, "crop_names": crop_names, "stats": stats,
            "window_start": iso(start), "window_end": iso(end),
            "n_counties": len(cnty), "n_top": len(top_level_counties),
        })
    events.sort(key=lambda e: (-level_rank(e["level"]), -e["n_counties"]))
    return events


def fingerprint(ev):
    raw = jdump([ev["level"], sorted(c["fips"] for c in ev["counties"]), ev["window_start"][:13],
                 {k: (round(v, 1) if isinstance(v, float) else v) for k, v in ev["stats"].items()
                  if k not in ("worst_fips", "worst_county")}])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def material_changes(prev, ev, mc):
    """List human-readable material changes between a previously notified event and now."""
    if not prev:
        return []
    changes = []
    prev_fips = {c["fips"] for c in prev.get("counties", [])}
    now_fips = {c["fips"] for c in ev["counties"]}
    added = now_fips - prev_fips
    removed = prev_fips - now_fips
    if added and len(added) >= max(mc.get("counties_added_min", 2),
                                   mc.get("counties_added_pct", 25) / 100.0 * max(1, len(prev_fips))):
        names = [c["name"] for c in ev["counties"] if c["fips"] in added]
        changes.append(f"Area expanded by {len(added)} counties ({', '.join(names[:6])}{'...' if len(names) > 6 else ''})")
    if removed and len(removed) >= max(mc.get("counties_added_min", 2), 0.5 * max(1, len(prev_fips))):
        changes.append(f"Area contracted by {len(removed)} counties")
    ps, ns = parse_iso(prev.get("window_start")), parse_iso(ev["window_start"])
    if ps and ns:
        shift = hours_between(ns, ps)
        if shift >= mc.get("timing_shift_hours", 12):
            changes.append(f"Expected about {round(shift)} h earlier than previously forecast")
        elif -shift >= mc.get("timing_shift_hours", 12) * 2:
            changes.append(f"Expected about {round(-shift)} h later than previously forecast")
    hz = ev["hazard"]
    km, how = KEY_METRIC.get(hz, (None, None))
    pv, nv = (prev.get("stats") or {}).get(km), ev["stats"].get(km)
    if km and pv is not None and nv is not None:
        delta = nv - pv
        worse = -delta if how == "min" else delta
        tol = mc.get("tolerance", {}).get(hz)
        if tol is not None and worse >= tol:
            changes.append(f"Intensified: {km} {pv:g} -> {nv:g}")
    return changes


def notification_reason(ev, row, mc, cooldown_h, now, immediate_levels):
    if ev["level"] not in immediate_levels:
        return None, []
    if row is None or not row.get("last_notified_utc"):
        return "new", []
    last_level = row.get("last_notified_level")
    if level_rank(ev["level"]) > level_rank(last_level):
        return "upgrade", [f"Risk level raised from {last_level} to {ev['level']}"]
    prev = jload(row.get("last_notified_data"), {})
    changes = material_changes(prev, ev, mc)
    if changes:
        since = hours_between(parse_iso(row["last_notified_utc"]), now)
        if since >= cooldown_h:
            return "update", changes
    return None, changes


def compare_previous(prev_data, ev):
    """Short lines comparing the event's key metric with the previous cycle's value."""
    if not prev_data:
        return []
    lines = []
    km, _ = KEY_METRIC.get(ev["hazard"], (None, None))
    if km:
        pv, nv = (prev_data.get("stats") or {}).get(km), ev["stats"].get(km)
        if pv is not None and nv is not None and pv != nv:
            lines.append(f"{km}: {pv:g} (previous cycle) -> {nv:g} (now)")
    if prev_data.get("level") and prev_data["level"] != ev["level"]:
        lines.append(f"Risk level: {prev_data['level']} -> {ev['level']}")
    pn, nn = prev_data.get("n_counties"), ev["n_counties"]
    if pn is not None and pn != nn:
        lines.append(f"Counties affected: {pn} -> {nn}")
    return lines