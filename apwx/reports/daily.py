"""Deliverable B - Daily Agricultural Weather Intelligence Report."""
from datetime import timedelta

from .. import db
from ..config import level_rank
from ..timeutil import fmt_ct, hours_between, parse_iso, to_ct
from . import aggregate as A
from . import common as C
from . import render as R


def _fmt(v, unit="", nd=0):
    if v is None:
        return "-"
    return f"{v:.{nd}f}{unit}"


def developments(ctx, events, alerts, aggs, old_aggs, stages, reports24):
    """Rank candidate developments; returns list of dicts {level, score, text}."""
    cfg, now = ctx.cfg, ctx.now
    out = []
    for e in events:
        ev = db.jload(e["data"], {})
        if not ev:
            continue
        crops = ", ".join(ev.get("crop_names", []))
        lead = min([c["lead_h"] for c in ev.get("counties", []) if c.get("lead_h") is not None] or [None]) \
            if any(c.get("lead_h") is not None for c in ev.get("counties", [])) else None
        when = f", {C.timing_text(ev['window_start'], ev['window_end'], now)}" if lead is not None else ""
        stats = "; ".join(C.stat_lines(ev)[:2])
        n = ev["n_counties"]
        text = (f"{C.hazard_label(cfg, ev['hazard'])} in {cfg.states[ev['state']]['name']} for {crops} in "
                f"{n} monitored count{'y' if n == 1 else 'ies'}{when}. Key figures: {stats}.")
        impact = sum(i.get("acres", 0) * i.get("exposure", 0) for i in ev.get("crops", {}).values())
        out.append({"level": ev["level"], "score": level_rank(ev["level"]) * 1e12 + 1e9 + impact, "text": text,
                    "key": e["event_key"]})
    for a in alerts:
        cls = (db.jload(a["data"], {}) or {}).get("classification", {})
        n = len(cls.get("monitored_fips", []))
        text = (f"Official NWS {a['event']} in effect for {n} monitored count{'y' if n == 1 else 'ies'} "
                f"({a['states']}) until {fmt_ct(parse_iso(a['ends_utc']))}.")
        out.append({"level": a["risk_level"], "score": level_rank(a["risk_level"]) * 1e12 + 5e11, "text": text,
                    "key": a["id"]})
    # drought worsening
    for st, g in aggs.items():
        if g.get("dm_chg1_mean") is not None and g["dm_chg1_mean"] >= 10:
            out.append({"level": "INFORMATIONAL", "score": 1e12 + 2e8 + g["dm_chg1_mean"],
                        "text": f"Drought expanded in {cfg.states[st]['name']}: average DSCI up {g['dm_chg1_mean']:.0f} points "
                                f"week over week across monitored counties (U.S. Drought Monitor)."})
    # harvest / progress milestones
    for (st, crop), stg in stages.items():
        if stg.get("basis") == "nass" and stg.get("detail") and st in ("ND", "OK", "SD", "MN", "KS", "NE"):
            cum = stg.get("cumulative", {})
            h = cum.get("harvested")
            if h is not None and 0.6 <= h < 0.98:
                out.append({"level": "INFORMATIONAL", "score": 1e12 + 1e8 + (1 - h) * 1000,
                            "text": f"{C.crop_label(cfg, crop)} harvest in {cfg.states[st]['name']}: {stg['detail']}."})
    big = [r for r in reports24 if r.get("fips")]
    if big:
        kinds = {}
        for r in big:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        out.append({"level": "INFORMATIONAL", "score": 1e12 + 1.5e8 + len(big),
                    "text": "Preliminary SPC storm reports in monitored counties over the last 24 h: "
                            + ", ".join(f"{n} {k}" for k, n in kinds.items()) + " (unverified, may change)."})
    out.sort(key=lambda d: -d["score"])
    return out


def build(ctx, stages, now=None):
    cfg, conn = ctx.cfg, ctx.conn
    now = now or ctx.now
    cyc, snaps, old, old_snaps = A.snapshots_now_and_before(conn, now, 24)
    aggs = A.state_aggregates(cfg, snaps) if snaps else {}
    old_aggs = A.state_aggregates(cfg, old_snaps) if old_snaps else {}
    events = db.events_with_status(conn, "active")
    alerts = db.alerts_active(conn)
    assessments = db.jload(db.meta_get(conn, "latest_assessments"), []) or []
    matrix = A.crop_matrix(cfg, assessments)
    levels = A.state_levels(cfg, events, alerts)
    reports24 = db.storm_reports_since(conn, now - timedelta(hours=24))
    progress = db.progress_latest(conn)
    devs = developments(ctx, events, alerts, aggs, old_aggs, stages, reports24)
    top = devs[:5]
    ct = to_ct(now)
    body = []
    banner = None
    if ctx.simulation:
        banner = R.banner("SIMULATION - TEST DATA - NOT A REAL WEATHER EVENT", "sim")
    # 1 executive summary
    body.append(R.h2("1. Executive summary"))
    n_hi = sum(1 for e in events if e["risk_level"] in ("CRITICAL", "HIGH"))
    n_mod = sum(1 for e in events if e["risk_level"] == "MODERATE")
    n_al = sum(1 for a in alerts if a["risk_level"] in ("CRITICAL", "HIGH"))
    summary_line = (f"{n_hi} high/critical and {n_mod} moderate forecast-based risk events are active across the 12 "
                    f"monitored states, with {len(alerts)} agriculturally relevant official NWS alerts ({n_al} high/critical).")
    if not n_hi and not n_al:
        summary_line += " No high-priority threat to monitored crops is currently identified."
    body.append(R.p(summary_line))
    body.append(R.h3("Top agricultural developments today"))
    if top:
        body.append(R.ul([f"{R.badge(d['level'], True)} {R.esc(d['text'])}" for d in top], raw=True))
    else:
        body.append(R.p("Quiet day: no notable crop-weather developments detected."))
    # 2 developments across states
    body.append(R.h2("2. Most relevant weather developments"))
    if devs[5:12]:
        body.append(R.ul([f"{R.badge(d['level'], True)} {R.esc(d['text'])}" for d in devs[5:12]], raw=True))
    else:
        body.append(R.small("No additional developments beyond the top items above."))
    # 3 map
    body.append(R.h2("3. Regional overview"))
    body.append(R.tile_map(levels, cfg.all_states, "Highest active risk level per monitored state (events and official alerts)."))
    rows = []
    for st in cfg.monitored_states:
        g = aggs.get(st, {})
        rows.append([R.badge(levels.get(st, "NONE"), True), cfg.states[st]["name"],
                     _fmt(g.get("tmin72"), " F"), _fmt(g.get("tmax7d"), " F"), _fmt(g.get("qpf72"), " in", 2),
                     _fmt(g.get("qpf7d_mean"), " in", 2), C.spc_text(g.get("spc_max") or 0),
                     f"{g.get('dm_d1_counties', 0)}/{g.get('n', 0)}"])
    body.append(R.table(["Risk", "State", "Coldest low 72h", "Warmest high 7d", "Max rain 72h", "Avg rain 7d",
                         "SPC max D1-3", "Counties D1+"], rows, raw_cols=(0,)))
    body.append(R.small("Values are across monitored agricultural counties (NWS gridded forecast at each county point; "
                        "drought = counties with at least half their area in D1 or worse)."))
    # 4 state by state
    body.append(R.h2("4. State-by-state analysis"))
    for st in sorted(cfg.monitored_states, key=lambda s: -level_rank(levels.get(s))):
        g = aggs.get(st, {})
        s_events = [e for e in events if e["state"] == st]
        lines = []
        if g:
            tf = cfg.counties.get(g.get("tmin7d_fips") or "", {}).get("name", "")
            lines.append(f"Forecast (next 7 days): lows down to {_fmt(g.get('tmin7d'), ' F')} ({tf}); highs up to "
                         f"{_fmt(g.get('tmax7d'), ' F')}; rainfall up to {_fmt(g.get('qpf7d'), ' in', 2)} "
                         f"(county average {_fmt(g.get('qpf7d_mean'), ' in', 2)}).")
            if g.get("obs_pcpn7_mean") is not None:
                lines.append(f"Observed last 7 days (PRISM via RCC-ACIS): average {g['obs_pcpn7_mean']:.2f} in, "
                             f"wettest county {_fmt(g.get('obs_pcpn7'), ' in', 2)}.")
            if g.get("dm_d1_mean") is not None:
                lines.append(f"Drought Monitor: {g['dm_d1_counties']} of {g['n']} monitored counties have at least half "
                             f"their area in D1+; average D2+ coverage {g.get('dm_d2_mean') or 0:.0f}%.")
        prog = progress.get((st, "topsoil"))
        if prog:
            m = prog["metrics"]
            vs, sh = (m.get("very_short") or {}).get("this_week"), (m.get("short") or {}).get("this_week")
            ad, su = (m.get("adequate") or {}).get("this_week"), (m.get("surplus") or {}).get("this_week")
            fw = (progress.get((st, "fieldwork")) or {}).get("metrics", {}).get("days_suitable", {})
            line = (f"USDA NASS (week ending {prog['week_ending']}): topsoil moisture {_fmt((vs or 0) + (sh or 0))}% short/very short, "
                    f"{_fmt(ad)}% adequate, {_fmt(su)}% surplus")
            if fw.get("this_week") is not None:
                line += f"; {fw['this_week']:.1f} days suitable for fieldwork (prior week {fw.get('last_week') or 0:.1f})"
            lines.append(line + ".")
        crop_bits = []
        for crop in C.CROP_ORDER:
            stg = stages.get((st, crop))
            if stg and stg.get("phases") and any(crop in (c.get("crops") or []) for c in cfg.counties_in_state(st)):
                crop_bits.append(f"{C.crop_label(cfg, crop)}: {stg.get('detail')}")
        for e in s_events:
            ev = db.jload(e["data"], {})
            n = ev.get("n_counties") or 0
            lines.append(f"[{e['risk_level']}] {C.hazard_label(cfg, e['hazard'])}: {n} count{'y' if n == 1 else 'ies'} "
                         f"({C.county_names(ev.get('counties', []), 5)}).")
        body.append(R.h3(f"{cfg.states[st]['name']} " + R.badge(levels.get(st, "NONE"), True), raw=True))
        body.append(R.ul(lines or ["No forecast data available."], size=13))
        if crop_bits:
            body.append(R.small(" | ".join(crop_bits)))
    # 5 crop matrix
    body.append(R.h2("5. Crop-specific risk assessment"))
    head = ["State"] + [C.crop_label(cfg, c) for c in C.CROP_ORDER]
    mrows = []
    for st in cfg.monitored_states:
        row = [cfg.states[st]["abbr"]]
        for crop in C.CROP_ORDER:
            grown = any(crop in (c.get("crops") or []) for c in cfg.counties_in_state(st))
            if not grown:
                row.append('<span style="color:#b4bcc6">not monitored</span>')
                continue
            cell = matrix.get((st, crop))
            if cell:
                hz = ", ".join(f"{C.hazard_label(cfg, h).split(' (')[0]}" for h, lv in sorted(
                    cell["hazards"].items(), key=lambda kv: -level_rank(kv[1]))[:2])
                row.append(f"{R.badge(cell['level'], True)}<br><span style='font-size:11px'>{R.esc(hz)}</span>")
            else:
                row.append(R.badge("NONE", True))
        mrows.append(row)
    body.append(R.table(head, mrows, raw_cols=tuple(range(1, 6))))
    expl = []
    for (st, crop), cell in sorted(matrix.items(), key=lambda kv: -level_rank(kv[1]["level"]))[:6]:
        if level_rank(cell["level"]) >= level_rank("MODERATE") and cell["top"]:
            t = cell["top"]
            expl.append(f"{st} {C.crop_label(cfg, crop)} - {cell['level']}: " + " | ".join(t["reasons"][:3]))
    if expl:
        body.append(R.small("Why: "))
        body.append(R.ul(expl, size=12))
    # 6 changes in last 24h
    body.append(R.h2("6. Significant forecast changes (last 24 hours)"))
    ch = []
    for st in cfg.monitored_states:
        g, o = aggs.get(st, {}), old_aggs.get(st, {})
        if not g or not o:
            continue
        for key, label, unit, tol, nd in (("tmin7d", "coldest 7-day low", " F", 3, 0), ("tmax7d", "warmest 7-day high", " F", 3, 0),
                                          ("qpf72", "max 72-h rainfall", " in", 0.5, 2), ("qpf7d_mean", "average 7-day rainfall", " in", 0.4, 2),
                                          ("snow72", "max 72-h snowfall", " in", 2, 1)):
            a, b = g.get(key), o.get(key)
            if a is not None and b is not None and abs(a - b) >= tol:
                ch.append(f"{cfg.states[st]['name']}: {label} now {a:.{nd}f}{unit} (was {b:.{nd}f}{unit} 24 h ago).")
        if (g.get("spc_max") or 0) != (o.get("spc_max") or 0):
            ch.append(f"{cfg.states[st]['name']}: SPC outlook now {C.spc_text(g.get('spc_max') or 0)} "
                      f"(was {C.spc_text(o.get('spc_max') or 0)}).")
    new_events = [e for e in events if parse_iso(e["first_detected_utc"]) >= now - timedelta(hours=24)]
    for e in new_events:
        if level_rank(e["risk_level"]) >= level_rank("MODERATE"):
            ch.append(f"New: {e['title']} ({e['risk_level']}).")
    n_info = sum(1 for e in new_events if e["risk_level"] == "INFORMATIONAL")
    if n_info:
        ch.append(f"{n_info} new informational item{'s' if n_info != 1 else ''} (see section 2 and the dashboard).")
    ended = [e for e in db.events_since(conn, now - timedelta(hours=24)) if e["status"] == "ended"]
    for e in ended:
        ch.append(f"Ended / no longer forecast: {e['title']}.")
    body.append(R.ul(ch[:20]) if ch else R.small("No significant changes in the monitored forecast measures over the last 24 hours."))
    if old:
        body.append(R.small(f"Comparison cycles: {fmt_ct(parse_iso(old['cycle_utc']))} vs {fmt_ct(parse_iso(cyc['cycle_utc']))}."))
    # 7 next 24-72h
    body.append(R.h2("7. Risks expected over the next 24-72 hours"))
    near = []
    for e in events:
        ev = db.jload(e["data"], {})
        s = parse_iso(ev.get("window_start"))
        if s and hours_between(now, s) <= 72:
            near.append(f"{R.badge(e['risk_level'], True)} {R.esc(e['title'])}: {R.esc(C.timing_text(ev['window_start'], ev['window_end'], now))}; "
                        f"{R.esc(', '.join(ev.get('crop_names', [])))} in {ev.get('n_counties')} counties.")
    body.append(R.ul(near, raw=True) if near else R.small("No crop-relevant hazards forecast in the next 72 hours."))
    # 8 extended
    body.append(R.h2("8. Extended outlook (days 4-14)"))
    later = []
    for e in events:
        ev = db.jload(e["data"], {})
        s = parse_iso(ev.get("window_start"))
        if s and hours_between(now, s) > 72:
            later.append(f"{e['title']} ({e['risk_level']}): {C.timing_text(ev['window_start'], ev['window_end'], now)}.")
    if later:
        body.append(R.ul(later))
    orows = []
    for st in cfg.monitored_states:
        g = aggs.get(st, {})
        def dom(k):
            d = g.get(k)
            if not d:
                return "-"
            if d["cat"] == "EC":
                return "equal chances"
            word = {"A": "above", "B": "below", "N": "near"}.get(d["cat"], d["cat"])
            return f"{word} ({d['pmin']}-{d['pmax']}%)"
        orows.append([cfg.states[st]["abbr"], dom("t610"), dom("p610"), dom("t814"), dom("p814"), _fmt(g.get("wpc_qpf7d_mean"), " in", 2)])
    body.append(R.table(["State", "6-10d temp", "6-10d precip", "8-14d temp", "8-14d precip", "WPC 7-day rain (avg)"], orows))
    body.append(R.small("CPC outlooks are probabilistic, model-informed outlooks of the chance of above-, near- or "
                        "below-normal conditions (1991-2020 normals), not deterministic forecasts. WPC values are the "
                        "lower bound of the forecast rainfall band."))
    # 9 official alerts
    body.append(R.h2("9. Official alerts and warnings"))
    if alerts:
        arows = []
        for a in sorted(alerts, key=lambda a: -level_rank(a["risk_level"]))[:30]:
            n = len((a.get("monitored_fips") or "").split(","))
            arows.append([R.badge(a["risk_level"], True), R.esc(a["event"]), R.esc(a["states"]), str(n),
                          R.esc(fmt_ct(parse_iso(a["ends_utc"]))), R.link(a["url"], "NWS")])
        body.append(R.table(["Level", "Event", "States", "Ag counties", "Until", "Link"], arows, raw_cols=(0, 1, 2, 4, 5)))
    else:
        body.append(R.small("No agriculturally relevant NWS watches, warnings or advisories are active in monitored counties."))
    if reports24:
        rr = [[r["kind"], r["magnitude"], f"{r['location']} ({r['county']}, {r['state']})", r["time_hhmm"] + "Z"]
              for r in reports24 if r.get("fips")][:15]
        if rr:
            body.append(R.h3("Preliminary storm reports in monitored counties (SPC, last 24 h)"))
            body.append(R.table(["Type", "Magnitude", "Location", "Time"], rr))
    # 10 sources
    body.append(R.h2("10. Sources and publication timestamps"))
    srows = []
    labels = {s["id"]: s for s in cfg.sources["data_sources"]}
    for s in db.source_status(conn):
        lab = labels.get(s["source"], {})
        ok = s.get("last_success_utc")
        status = "OK" if ok and (not s.get("last_error_utc") or s["last_error_utc"] <= ok) else "ERROR"
        srows.append([lab.get("name", s["source"]), lab.get("kind", ""), s.get("product_time") or "-",
                      fmt_ct(parse_iso(ok)) if ok else "never", status])
    body.append(R.table(["Source", "Type", "Product issued / valid", "Last retrieved", "Status"], srows))
    if cyc:
        body.append(R.small(f"Forecast cycle used: {fmt_ct(parse_iso(cyc['cycle_utc']))} ({cyc['counties_ok']} counties ok, "
                            f"{cyc['counties_failed']} failed). Observed = measured; forecast = NWS/WPC predictions; "
                            "outlook = SPC/WPC/CPC probabilistic guidance."))
    title = f"Daily Agricultural Weather Intelligence - {ct:%A, %B} {ct.day}, {ct.year}"
    top_level = top[0]["level"] if top else "NONE"
    prefix = cfg.get("notify", "subject_prefix", default="[AP Weather]")
    sim = "SIMULATION - " if ctx.simulation else ""
    subject = f"{prefix} {sim}Daily report {ct:%b} {ct.day}: " + (top[0]["text"][:90] if top else "quiet day")
    html = R.page(cfg, title, f"Daily report - {fmt_ct(now)}", "".join(body),
                  preheader=(top[0]["text"][:140] if top else "No high-priority crop weather threats today"), top_banner=banner)
    summary = {"top": [{"level": d["level"], "text": d["text"]} for d in top], "levels": levels,
               "aggs": aggs, "cycle": cyc["cycle_utc"] if cyc else None, "top_level": top_level}
    return subject, html, R.to_text(html), summary