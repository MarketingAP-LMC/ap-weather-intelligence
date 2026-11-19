"""Deliverable C - Weekly Agricultural Outlook."""
from datetime import timedelta

from .. import db
from ..config import level_rank
from ..timeutil import fmt_ct, parse_iso, to_ct
from . import aggregate as A
from . import common as C
from . import render as R


def _f(v, unit="", nd=0):
    return "-" if v is None else f"{v:.{nd}f}{unit}"


def _dom(d):
    if not d:
        return "-"
    if d["cat"] == "EC":
        return "equal chances"
    return f"{ {'A': 'above', 'B': 'below', 'N': 'near'}.get(d['cat'], d['cat']) } normal ({d['pmin']}-{d['pmax']}%)"


def build(ctx, stages, week_key):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    cyc, snaps, old, old_snaps = A.snapshots_now_and_before(conn, now, 24 * 7)
    aggs = A.state_aggregates(cfg, snaps) if snaps else {}
    events = db.events_with_status(conn, "active")
    alerts = db.alerts_active(conn)
    assessments = db.jload(db.meta_get(conn, "latest_assessments"), []) or []
    matrix = A.crop_matrix(cfg, assessments)
    levels = A.state_levels(cfg, events, alerts)
    prev = db.report_previous(conn, "weekly", week_key)
    prev_sum = db.jload(prev["summary"], {}) if prev else {}
    body = []
    banner = R.banner("SIMULATION - TEST DATA - NOT A REAL WEATHER EVENT", "sim") if ctx.simulation else None
    body.append(R.p(f"Outlook for the week of {to_ct(now):%B} {to_ct(now).day}, {to_ct(now).year}: weather trends, "
                    f"crop exposure and implications for the next 7-14 days across the 12 monitored states."))
    body.append(R.tile_map(levels, cfg.all_states, "Highest active risk level per state at the time of this outlook."))
    # major trends
    body.append(R.h2("Major weather trends"))
    rows = []
    for st in cfg.monitored_states:
        g = aggs.get(st, {})
        rows.append([cfg.states[st]["name"], _f(g.get("obs_pcpn7_mean"), " in", 2), _f(g.get("tmin7d"), " F"),
                     _f(g.get("tmax7d"), " F"), _f(g.get("qpf7d_mean"), " in", 2), _f(g.get("qpf7d"), " in", 2)])
    body.append(R.table(["State", "Observed rain last 7 d (avg)", "Coldest fcst low 7 d", "Warmest fcst high 7 d",
                         "Fcst rain 7 d (avg)", "Fcst rain 7 d (max)"], rows))
    body.append(R.small("Observed = PRISM county means via NOAA RCC-ACIS (most recent day provisional). Forecast = NWS gridded forecast at monitored county points."))
    # precipitation outlook
    body.append(R.h2("Regional precipitation outlook"))
    prow = [[cfg.states[st]["abbr"], _f(aggs.get(st, {}).get("wpc_qpf7d_mean"), " in", 2), _dom(aggs.get(st, {}).get("p610")),
             _dom(aggs.get(st, {}).get("p814"))] for st in cfg.monitored_states]
    body.append(R.table(["State", "WPC 7-day rain (avg, lower bound)", "CPC 6-10 day precip", "CPC 8-14 day precip"], prow))
    # temperature trends
    body.append(R.h2("Temperature trends"))
    trow = [[cfg.states[st]["abbr"], _f(aggs.get(st, {}).get("tmin7d"), " F"), _f(aggs.get(st, {}).get("tmax7d"), " F"),
             _dom(aggs.get(st, {}).get("t610")), _dom(aggs.get(st, {}).get("t814"))] for st in cfg.monitored_states]
    body.append(R.table(["State", "Coldest low (7 d)", "Warmest high (7 d)", "CPC 6-10 day temp", "CPC 8-14 day temp"], trow))
    # drought / flooding
    body.append(R.h2("Emerging drought or flooding concerns"))
    dl = []
    for st in cfg.monitored_states:
        g = aggs.get(st, {})
        if g.get("dm_chg1_mean") is not None and abs(g["dm_chg1_mean"]) >= 5:
            word = "worsened" if g["dm_chg1_mean"] > 0 else "improved"
            dl.append(f"{cfg.states[st]['name']}: drought {word} (average DSCI change {g['dm_chg1_mean']:+.0f} pts in a week); "
                      f"{g.get('dm_d1_counties', 0)} of {g.get('n', 0)} monitored counties at least half in D1+.")
        if (g.get("obs_pcpn7") or 0) >= 3:
            dl.append(f"{cfg.states[st]['name']}: wet - up to {g['obs_pcpn7']:.1f} in observed in the last 7 days.")
        if (g.get("ero_max") or 0) >= 2:
            dl.append(f"{cfg.states[st]['name']}: WPC Excessive Rainfall Outlook {C.ero_text(g['ero_max'])} risk in Days 1-3.")
    body.append(R.ul(dl) if dl else R.small("No significant drought expansion or flooding signal this week."))
    # crop exposure
    body.append(R.h2("Crop-specific exposure"))
    for crop in C.CROP_ORDER:
        lines = []
        for st in cfg.monitored_states:
            if not any(crop in (c.get("crops") or []) for c in cfg.counties_in_state(st)):
                continue
            stg = stages.get((st, crop))
            cell = matrix.get((st, crop))
            lvl = cell["level"] if cell else "NONE"
            hz = ", ".join(C.hazard_label(cfg, h) for h in (cell or {}).get("hazards", {})) if cell else "no active hazard"
            lines.append(f"{R.badge(lvl, True)} <b>{st}</b> - {R.esc(hz)}. {R.esc((stg or {}).get('detail', ''))}")
        if lines:
            body.append(R.h3(C.crop_label(cfg, crop)))
            body.append(R.ul(lines, raw=True, size=13))
    # attention
    body.append(R.h2("States and counties requiring additional attention"))
    by_county = {}
    for a in assessments:
        k = a["fips"]
        cur = by_county.setdefault(k, {"level": "NONE", "hazards": set(), "crops": set(), "a": a})
        if level_rank(a["level"]) > level_rank(cur["level"]):
            cur["level"], cur["a"] = a["level"], a
        cur["hazards"].add(a["hazard"])
        cur["crops"].add(a["crop"])
    ranked = sorted(by_county.items(), key=lambda kv: (-level_rank(kv[1]["level"]), -len(kv[1]["hazards"])))[:15]
    if ranked:
        rrows = [[R.badge(v["level"], True), f"{cfg.counties[f]['name']}, {cfg.counties[f]['state']}",
                  ", ".join(C.hazard_label(cfg, h) for h in v["hazards"]), ", ".join(C.crop_label(cfg, c) for c in v["crops"])]
                 for f, v in ranked if level_rank(v["level"]) >= level_rank("MODERATE")]
        body.append(R.table(["Level", "County", "Hazards", "Crops"], rrows, raw_cols=(0,)) if rrows else
                    R.small("No county is above INFORMATIONAL this week."))
    # implications
    body.append(R.h2("Potential agricultural implications (next 7-14 days)"))
    impl = []
    for crop in C.CROP_ORDER:
        hazards = {}
        for (st, cr), cell in matrix.items():
            if cr != crop:
                continue
            for h, lv in cell["hazards"].items():
                if level_rank(lv) >= level_rank("INFORMATIONAL"):
                    hazards.setdefault(h, []).append(st)
        for h, sts in hazards.items():
            t = C.hazard_text(cfg, crop, h)
            if t.get("medium_term"):
                impl.append(f"{C.crop_label(cfg, crop)} ({', '.join(sorted(set(sts)))}) - {C.hazard_label(cfg, h)}: {t['medium_term']}")
    body.append(R.ul(impl[:14]) if impl else R.small("No crop-relevant hazards identified for the next 7-14 days; continue routine monitoring."))
    # changes vs previous weekly
    body.append(R.h2("Changes compared with the previous weekly outlook"))
    chg = []
    if prev_sum:
        for st in cfg.monitored_states:
            a, b = levels.get(st), (prev_sum.get("levels") or {}).get(st)
            if b and a != b:
                chg.append(f"{cfg.states[st]['name']}: risk {b} -> {a}.")
            ga, gb = aggs.get(st, {}), (prev_sum.get("aggs") or {}).get(st, {})
            if ga.get("dm_d1_counties") is not None and gb.get("dm_d1_counties") is not None and ga["dm_d1_counties"] != gb["dm_d1_counties"]:
                chg.append(f"{cfg.states[st]['name']}: counties at least half in D1+ {gb['dm_d1_counties']} -> {ga['dm_d1_counties']}.")
        body.append(R.ul(chg) if chg else R.small("No material change versus last week's outlook."))
        body.append(R.small(f"Previous outlook: {prev['period_key']} ({fmt_ct(parse_iso(prev['created_utc']))})."))
    else:
        body.append(R.small("This is the first weekly outlook stored by the platform, so there is no previous outlook to compare."))
    ct = to_ct(now)
    title = f"Weekly Agricultural Outlook - week of {ct:%B} {ct.day}, {ct.year}"
    prefix = cfg.get("notify", "subject_prefix", default="[AP Weather]")
    sim = "SIMULATION - " if ctx.simulation else ""
    n_hi = sum(1 for e in events if e["risk_level"] in ("CRITICAL", "HIGH"))
    subject = f"{prefix} {sim}Weekly outlook {week_key}: {n_hi} high-priority events, {len(alerts)} official alerts"
    html = R.page(cfg, title, f"Weekly outlook - {fmt_ct(now)}", "".join(body), preheader=subject, top_banner=banner)
    summary = {"levels": levels, "aggs": aggs, "top": [{"state": s, "level": lv} for s, lv in levels.items() if lv != "NONE"]}
    return subject, html, R.to_text(html), summary