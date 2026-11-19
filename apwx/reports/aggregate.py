"""State-level aggregation of county snapshots (used by reports and the dashboard)."""
from collections import Counter
from datetime import timedelta

from .. import db
from ..analysis import metrics
from ..config import level_rank, max_level
from ..timeutil import parse_iso


def _ext(fl, key, how):
    vals = [(f, x[key]) for f, x in fl if x.get(key) is not None]
    if not vals:
        return None, None
    f, v = (min if how == "min" else max)(vals, key=lambda t: t[1])
    return v, f


def _mean(fl, key):
    vals = [x[key] for _, x in fl if x.get(key) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _dominant(snaps_state, key):
    cats = Counter()
    probs = {}
    for s in snaps_state:
        v = (s.get("cpc") or {}).get(key)
        if not v:
            continue
        cats[v[0]] += 1
        probs.setdefault(v[0], []).append(v[1])
    if not cats:
        return None
    cat, n = cats.most_common(1)[0]
    ps = probs[cat]
    return {"cat": cat, "share": round(100 * n / sum(cats.values())), "pmin": min(ps), "pmax": max(ps)}


def state_aggregates(cfg, snaps):
    out = {}
    for st in cfg.monitored_states:
        items = [(f, s) for f, s in snaps.items() if cfg.counties.get(f, {}).get("state") == st]
        fl = [(f, metrics.flatten(s)) for f, s in items]
        agg = {"n": len(fl)}
        for key, how in (("tmin72", "min"), ("tmin7d", "min"), ("tmax7d", "max"), ("qpf72", "max"), ("qpf7d", "max"),
                         ("snow72", "max"), ("gust72", "max"), ("spc_max", "max"), ("ero_max", "max"),
                         ("wpc_qpf7d", "max"), ("obs_pcpn7", "max"), ("dm_d2", "max")):
            v, f = _ext(fl, key, how)
            agg[key] = v
            agg[key + "_fips"] = f
        for key in ("qpf72", "qpf7d", "obs_pcpn7", "dm_d1", "dm_d2", "dm_dsci", "dm_chg1", "wpc_qpf7d"):
            agg[key + "_mean"] = _mean(fl, key)
        agg["dm_d1_counties"] = sum(1 for _, x in fl if (x.get("dm_d1") or 0) >= 50)
        agg["dm_d2_counties"] = sum(1 for _, x in fl if (x.get("dm_d2") or 0) >= 50)
        for k in ("t610", "p610", "t814", "p814"):
            agg[k] = _dominant([s for _, s in items], k)
        out[st] = agg
    return out


def state_levels(cfg, events, alerts):
    levels = {st: "NONE" for st in cfg.monitored_states}
    for ev in events:
        st = ev.get("state")
        if st in levels:
            levels[st] = max_level(levels[st], ev.get("risk_level") or ev.get("level"))
    for a in alerts:
        for st in (a.get("states") or "").split(","):
            if st in levels:
                levels[st] = max_level(levels[st], a.get("risk_level"))
    return levels


def crop_matrix(cfg, assessments):
    """{(state, crop): {"level", "hazards": {hazard: level}, "reasons"}}"""
    m = {}
    for a in assessments:
        k = (a["state"], a["crop"])
        cell = m.setdefault(k, {"level": "NONE", "hazards": {}, "top": None})
        cell["hazards"][a["hazard"]] = max_level(cell["hazards"].get(a["hazard"], "NONE"), a["level"])
        if level_rank(a["level"]) > level_rank(cell["level"]) or cell["top"] is None:
            cell["level"] = max_level(cell["level"], a["level"])
            cell["top"] = a
    return m


def snapshots_now_and_before(conn, now, hours=24):
    cyc = db.latest_cycle(conn)
    if not cyc:
        return None, {}, None, {}
    snaps = db.snapshots_for_cycle(conn, cyc["id"])
    target = parse_iso(cyc["cycle_utc"]) - timedelta(hours=hours)
    old = db.cycle_at_or_before(conn, target + timedelta(minutes=45))
    old_snaps = db.snapshots_for_cycle(conn, old["id"]) if old and old["id"] != cyc["id"] else {}
    return cyc, snaps, old, old_snaps