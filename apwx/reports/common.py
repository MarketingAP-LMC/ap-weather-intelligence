"""Shared narrative helpers for emails, reports and the dashboard."""
from ..analysis.assess import describe_metric
from ..sources import cpc as cpc_src
from ..sources import spc as spc_src
from ..sources import wpc as wpc_src
from ..timeutil import fmt_ct, fmt_day, hours_between, parse_iso
from . import render as R

CROP_ORDER = ["soybeans", "corn", "winter_wheat", "canola", "sunflower", "sugarcane"]


def crop_label(cfg, crop_id):
    return cfg.crops["crops"].get(crop_id, {}).get("label", crop_id.title())


def hazard_label(cfg, hazard):
    return cfg.get("hazard_labels", default={}).get(hazard, hazard.replace("_", " ").title())


def hazard_text(cfg, crop_id, hazard):
    return (cfg.crops["crops"].get(crop_id, {}).get("hazards", {}).get(hazard) or {})


def refs(cfg, ids):
    out = []
    table = cfg.crops.get("references", {})
    for i in ids or []:
        r = table.get(i)
        if r:
            out.append(r)
    return out


def refs_html(cfg, ids):
    items = []
    for r in refs(cfg, ids):
        pub = f" ({r.get('publisher')}{', ' + str(r['year']) if r.get('year') else ''})" if r.get("publisher") else ""
        items.append(f"{R.link(r['url'], r['title'])}{R.esc(pub)}")
    return items


def county_names(counties, n=8):
    names = [c["name"].replace(" County", "").replace(" Parish", " Par.") for c in counties]
    if len(names) <= n:
        return ", ".join(names)
    return ", ".join(names[:n]) + f" (+{len(names) - n} more)"


def regions_text(counties):
    regs = {}
    for c in counties:
        if c.get("region"):
            regs[c["region"]] = regs.get(c["region"], 0) + 1
    return ", ".join(f"{k}" for k, _ in sorted(regs.items(), key=lambda kv: -kv[1]))


def timing_text(start_iso, end_iso, now):
    s, e = parse_iso(start_iso), parse_iso(end_iso)
    if not s:
        return "timing not determined"
    lead = hours_between(now, s)
    if lead <= 1:
        start = "starting now or within the next hour"
    else:
        start = f"starting about {fmt_ct(s)}, in roughly {round(lead)} h"
    if e and e > s:
        return f"{start}, through about {fmt_day(e)}"
    return start


def state_share(cfg, state, crop, fips_list):
    total = 0.0
    for f in fips_list:
        c = cfg.counties.get(f)
        if c and c["state"] == state:
            total += (c.get("share") or {}).get(crop, 0.0)
    return round(total, 1)


def why_it_matters(cfg, ev):
    parts = []
    fips = [c["fips"] for c in ev["counties"]]
    for crop_id, info in sorted(ev["crops"].items(), key=lambda kv: -kv[1]["acres"]):
        share = state_share(cfg, ev["state"], crop_id, fips)
        st_name = cfg.states[ev["state"]]["name"]
        stage = info.get("stage_detail") or ""
        line = (f"The affected counties hold about {share:.0f}% of {st_name}'s {crop_label(cfg, crop_id).lower()} acreage "
                f"(about {info['acres'] / 1000:,.0f}k acres in the USDA Cropland Data Layer 2025).")
        if stage:
            line += f" {stage}"
        parts.append(line)
    return parts


def stat_lines(ev):
    import re as _re
    lines = []
    seen = {}
    for k, v in ev["stats"].items():
        if k in ("worst_fips", "worst_county") or v is None:
            continue
        if k.startswith("spc_") and k != "spc_max" and not v:
            continue
        family = _re.sub(r"(24|72|7d)$", "", k)
        if family in seen and seen[family] == v:
            continue
        seen[family] = v
        text = describe_metric(k, v)
        lines.append(text[0].upper() + text[1:])
    if ev["stats"].get("worst_county"):
        lines.append(f"Most affected county by this measure: {ev['stats']['worst_county']}")
    return lines


def confidence_text(ev, crops):
    leads = [c["lead_h"] for c in ev["counties"] if c.get("lead_h") is not None]
    lead = min(leads) if leads else None
    if lead is None:
        t = "Timing based on outlooks or observations rather than an hourly forecast."
    elif lead <= 48:
        t = f"Expected within ~{round(lead)} h: forecast confidence is relatively high, but exact temperatures and amounts can still shift by a few degrees or tenths of an inch."
    elif lead <= 120:
        t = f"Expected in ~{round(lead / 24)} days: moderate confidence; details are likely to change in later forecasts."
    else:
        t = "More than five days out: low confidence; treat as an early signal."
    bases = {i.get("stage_basis") for i in crops.values()}
    if "calendar" in bases:
        t += " Crop stage shares are estimated from typical planting/harvest calendars (no current USDA report for this crop/state), which adds uncertainty."
    elif "nass" in bases:
        t += " Crop stage shares come from the latest USDA NASS Crop Progress report (state level; individual fields vary)."
    return t


def source_links_for_event(cfg, ev):
    links = [("NWS forecast for the most affected county", None)]
    out = []
    worst = ev["stats"].get("worst_fips") or (ev["counties"][0]["fips"] if ev["counties"] else None)
    c = cfg.counties.get(worst)
    if c:
        out.append(("NWS point forecast (" + c["name"] + ", " + c["state"] + ")",
                    f"https://forecast.weather.gov/MapClick.php?lat={c['lat']}&lon={c['lon']}"))
    hz = ev["hazard"]
    if hz in ("severe_storm",):
        out.append(("SPC convective outlooks", "https://www.spc.noaa.gov/products/outlook/"))
    if hz in ("heavy_rain", "harvest_rain"):
        out.append(("WPC Excessive Rainfall Outlook", wpc_src.LINKS["ero"]))
        out.append(("WPC QPF", wpc_src.LINKS["qpf"]))
    if hz == "drought":
        out.append(("U.S. Drought Monitor", "https://droughtmonitor.unl.edu/"))
    if hz.startswith("outlook"):
        out.append(("CPC 6-10 day outlook", cpc_src.LINKS["610"]))
        out.append(("CPC 8-14 day outlook", cpc_src.LINKS["814"]))
    out.append(("NWS active alerts", "https://alerts.weather.gov/"))
    if any(i.get("stage_basis") == "nass" for i in ev["crops"].values()):
        out.append(("USDA NASS Crop Progress", "https://www.nass.usda.gov/Publications/National_Crop_Progress/"))
    del links
    return out


def crop_impact_html(cfg, crop_id, hazard, info):
    """Crop-specific impact analysis block."""
    t = hazard_text(cfg, crop_id, hazard)
    rows = []
    stage = info.get("stage_detail")
    if stage:
        rows.append(("Crop stage now", R.esc(stage)))
    if info.get("exposure") is not None:
        rows.append(("Exposure index", R.esc(f"{info['exposure']:.2f} (0 = no crop in vulnerable stages, 1 = all of it)")))
    if t.get("vulnerable"):
        rows.append(("Most vulnerable stages", R.esc(t["vulnerable"])))
    if t.get("short_term"):
        rows.append(("Short term (days)", R.esc(t["short_term"])))
    if t.get("medium_term"):
        rows.append(("Medium term (weeks)", R.esc(t["medium_term"])))
    if t.get("implications"):
        rows.append(("Development, quality, yield, field work", R.esc(t["implications"])))
    html = R.h3(f"{crop_label(cfg, crop_id)} " + R.badge(info.get("level", "NONE"), True), raw=True) + R.kv(rows)
    refs_list = refs_html(cfg, t.get("sources"))
    if refs_list:
        html += R.small("Agronomic basis: " + "; ".join(refs_list), raw=True)
    return html


def monitor_items(cfg, crop_ids, hazard):
    seen, out = set(), []
    for crop_id in crop_ids:
        for m in hazard_text(cfg, crop_id, hazard).get("monitor", []):
            if m not in seen:
                seen.add(m)
                out.append(f"{crop_label(cfg, crop_id)}: {m}")
    return out


def spc_text(ordinal):
    return spc_src.ordinal_name(ordinal)


def ero_text(rank):
    return wpc_src.ERO_NAMES.get(rank, "None")


def cpc_text(val):
    if not val:
        return "n/a"
    cat, prob = val
    if cat == "EC":
        return "equal chances"
    return f"{prob}% {cpc_src.CAT_WORDS.get(cat, cat)}"