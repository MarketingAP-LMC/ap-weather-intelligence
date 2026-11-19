"""Deliverable A - Immediate weather alert email (CRITICAL / HIGH)."""
from .. import db
from ..config import level_rank
from ..timeutil import fmt_ct, parse_iso
from . import common as C
from . import render as R

REASON_TEXT = {"new": "NEW", "upgrade": "UPGRADED", "update": "UPDATE", "expanded": "AREA EXPANDED"}


def _alert_item(ctx, it):
    cfg, now = ctx.cfg, ctx.now
    a, cls = it["alert"], it["alert"]["classification"]
    counties = [cfg.counties[f] for f in cls["monitored_fips"] if f in cfg.counties]
    body = []
    body.append(R.p(f"{REASON_TEXT.get(it['reason'], it['reason'].upper())} &middot; Official NWS product &middot; "
                    f"issued by {R.esc(a.get('sender') or 'NWS')}", size=12, color="#5b6573", raw=True))
    rows = [
        ("Event", R.esc(a["event"])),
        ("Priority", R.badge(cls["level"])),
        ("Detected", R.esc(fmt_ct(now))),
        ("NWS issued", R.esc(fmt_ct(parse_iso(a.get("sent"))))),
        ("In effect", R.esc(f"{fmt_ct(parse_iso(a.get('onset')))} to {fmt_ct(parse_iso(a.get('ends')))}")),
        ("States", R.esc(", ".join(it.get("states", [])))),
        ("Monitored ag counties", R.esc(f"{len(counties)}: " + C.county_names(counties, 12))),
        ("Areas", R.esc(C.regions_text(counties) or "-")),
        ("NWS severity / certainty", R.esc(f"{a.get('severity')} / {a.get('certainty')} / {a.get('urgency')}")),
    ]
    if a.get("max_hail_in") or a.get("max_wind_mph"):
        rows.append(("Storm details", R.esc(f"max hail {a.get('max_hail_in') or '-'} in; max wind {a.get('max_wind_mph') or '-'} mph"
                                             + (f"; tornado {a['tornado_detection']}" if a.get("tornado_detection") else ""))))
    body.append(R.kv(rows))
    body.append(R.callout("Why this level: " + " ".join(cls["reasons"])))
    if a.get("headline"):
        body.append(R.p(f"<b>NWS headline:</b> {R.esc(a['headline'])}", raw=True))
    desc = (a.get("description") or "").strip()
    if desc:
        body.append(R.small(desc[:1200] + ("..." if len(desc) > 1200 else "")))
    if a.get("instruction"):
        body.append(R.small("NWS instructions: " + a["instruction"][:600]))
    # current conditions vs previous forecast for the most relevant county
    body.append(_county_compare(ctx, cls["monitored_fips"]))
    crops = cls.get("crops", {})
    hazard = cls["hazard"]
    hz_for_text = {"tropical": "high_wind", "general": "harvest_rain", "fire": "high_wind"}.get(hazard, hazard)
    if crops:
        body.append(R.h3("Crops potentially affected"))
        for crop_id, info in sorted(crops.items(), key=lambda kv: -kv[1]["exposure"]):
            info = dict(info, level=cls["level"] if info["exposure"] >= 0.05 else "INFORMATIONAL")
            body.append(C.crop_impact_html(cfg, crop_id, hz_for_text, info))
        mon = C.monitor_items(cfg, [k for k, v in crops.items() if v["exposure"] >= 0.05], hz_for_text)
        if mon:
            body.append(R.h3("What producers should monitor"))
            body.append(R.ul(mon[:8]))
    body.append(R.small(f"Confidence: this is an official NWS {a['event'].lower()} (observed or expected hazard). "
                        "Crop impacts are potential, not confirmed; local severity varies within the warned area."))
    body.append(R.small("Sources: " + R.link(a["url"], "NWS alert (CAP)") + " &middot; " +
                        R.link("https://alerts.weather.gov/", "alerts.weather.gov"), raw=True))
    title = f"{a['event']} - {', '.join(it.get('states', []))}"
    return title, R.card("".join(body), cls["level"])


def _county_compare(ctx, fips_list):
    conn = ctx.conn
    cyc = db.latest_cycle(conn)
    if not cyc or not fips_list:
        return ""
    prev = db.previous_cycle(conn, cyc["id"])
    snaps = db.snapshots_for_cycle(conn, cyc["id"])
    prev_snaps = db.snapshots_for_cycle(conn, prev["id"]) if prev else {}
    rows = []
    for f in fips_list[:6]:
        s = (snaps.get(f) or {}).get("fc") or {}
        ps = (prev_snaps.get(f) or {}).get("fc") or {}
        if not s:
            continue
        c = ctx.cfg.counties[f]
        now_t = (s.get("now") or {}).get("t")

        def pair(k, unit):
            a, b = s.get(k), ps.get(k)
            if a is None:
                return "-"
            return f"{a:g}{unit}" + (f" (was {b:g})" if b is not None and b != a else "")
        rows.append([f"{c['name']}, {c['state']}", f"{now_t:.0f} F" if now_t is not None else "-",
                     pair("tmin72", " F"), pair("qpf72", " in"), pair("gust72", " mph")])
    if not rows:
        return ""
    when = fmt_ct(parse_iso(cyc["cycle_utc"]))
    head = R.small(f"NWS gridded forecast for monitored counties (cycle {when}); 'was' = previous forecast cycle.")
    return head + R.table(["County", "Now (fcst hr)", "Low next 72 h", "Rain next 72 h", "Max gust 72 h"], rows)


def _event_item(ctx, it):
    cfg, now = ctx.cfg, ctx.now
    ev = it["event"]
    level = ev["level"]
    counties = ev["counties"]
    crop_names = ", ".join(ev["crop_names"])
    body = []
    tag = REASON_TEXT.get(it["reason"], it["reason"].upper())
    body.append(R.p(f"{tag} &middot; Forecast-based assessment &middot; first detected "
                    f"{R.esc(fmt_ct(parse_iso(ev.get('first_detected'))))}", size=12, color="#5b6573", raw=True))
    rows = [
        ("Weather event", R.esc(C.hazard_label(cfg, ev["hazard"]))),
        ("Priority", R.badge(level)),
        ("Detected", R.esc(fmt_ct(now))),
        ("Expected timing", R.esc(C.timing_text(ev["window_start"], ev["window_end"], now))),
        ("State", R.esc(cfg.states[ev["state"]]["name"])),
        ("Counties", R.esc(f"{ev['n_counties']}: " + C.county_names(counties, 12))),
        ("Areas", R.esc(C.regions_text(counties) or "-")),
        ("Crops potentially affected", R.esc(crop_names)),
    ]
    body.append(R.kv(rows))
    if it.get("changes"):
        body.append(R.callout("What changed: " + "; ".join(it["changes"])))
    body.append(R.h3("Forecast details"))
    body.append(R.ul(C.stat_lines(ev)))
    cmp_lines = ev.get("changes_vs_previous") or []
    body.append(R.h3("Current conditions versus previous forecast"))
    obs_line = _obs_line(ctx, ev)
    if obs_line:
        body.append(R.small(obs_line))
    body.append(R.ul(cmp_lines) if cmp_lines else R.small("No change in the key measure since the previous forecast cycle."))
    body.append(_county_compare(ctx, [c["fips"] for c in counties]))
    body.append(R.h3("Why this level"))
    top_crop = max(ev["crops"].values(), key=lambda i: (level_rank(i["level"]), i["acres"]))
    body.append(R.ul(top_crop.get("reasons", [])))
    body.append(R.h3("Crop-specific impact analysis"))
    for crop_id, info in sorted(ev["crops"].items(), key=lambda kv: (-level_rank(kv[1]["level"]), -kv[1]["acres"])):
        body.append(C.crop_impact_html(cfg, crop_id, ev["hazard"], info))
    mon = C.monitor_items(cfg, list(ev["crops"].keys()), ev["hazard"])
    if mon:
        body.append(R.h3("Recommended monitoring for producers"))
        body.append(R.ul(mon[:8]))
    body.append(R.h3("Why this matters"))
    body.append(R.ul(C.why_it_matters(cfg, ev)))
    warn = _overlapping_alerts(ctx, [c["fips"] for c in counties])
    body.append(R.h3("Official weather warnings"))
    body.append(R.ul(warn, raw=True) if warn else R.small("No official NWS watch or warning is currently in effect for these counties; this is a forecast-based risk assessment."))
    ft = it.get("forecast_text")
    if ft:
        body.append(R.h3(f"NWS forecast wording - {ft['county']}, {ft['state']}"))
        body.append(R.ul([f"{pd['name']}: {pd['text']}" for pd in ft["periods"]], size=12))
    body.append(R.h3("Confidence and uncertainty"))
    body.append(R.small(C.confidence_text(ev, ev["crops"])))
    links = C.source_links_for_event(cfg, ev)
    body.append(R.small("Sources: " + " &middot; ".join(R.link(u, t) for t, u in links), raw=True))
    title = f"{C.hazard_label(cfg, ev['hazard'])} - {cfg.states[ev['state']]['name']} ({crop_names})"
    return title, R.card("".join(body), level)


def _obs_line(ctx, ev):
    obs = db.station_obs_all(ctx.conn)
    fips = {c["fips"] for c in ev["counties"]}
    for st, o in obs.items():
        if o.get("fips") in fips and o.get("temp_f") is not None:
            return (f"Latest observation at {st} ({ctx.cfg.counties[o['fips']]['name']}): {o['temp_f']:.0f} F, "
                    f"dew point {o.get('dewpoint_f') or '-'} F, wind {o.get('wind_mph') or '-'} mph"
                    f"{', ' + o['text'] if o.get('text') else ''} at {fmt_ct(parse_iso(o.get('time')))} (NWS observed).")
    return None


def _overlapping_alerts(ctx, fips_list):
    fs = set(fips_list)
    out = []
    for a in db.alerts_active(ctx.conn):
        mf = set((a.get("monitored_fips") or "").split(","))
        if fs & mf:
            out.append(f"{R.badge(a['risk_level'], True)} {R.esc(a['event'])} until "
                       f"{R.esc(fmt_ct(parse_iso(a['ends_utc'])))} - {R.link(a['url'], 'NWS alert')}")
    return out[:8]


def build(ctx, items, overflow=0):
    cfg, now = ctx.cfg, ctx.now
    cards, titles = [], []
    for it in items:
        if it["type"] == "alert":
            t, html = _alert_item(ctx, it)
        else:
            t, html = _event_item(ctx, it)
        titles.append((it["level"], t))
        cards.append(html)
    top_level, top_title = titles[0]
    more = f" (+{len(items) - 1} more)" if len(items) > 1 else ""
    prefix = cfg.get("notify", "subject_prefix", default="[AP Weather]")
    sim = "SIMULATION - " if ctx.simulation else ""
    subject = f"{prefix} {sim}{top_level}: {top_title}{more}"
    intro = R.p(f"{len(items)} new or materially changed item{'s' if len(items) != 1 else ''} detected at "
                f"{fmt_ct(now)}. Highest priority: {top_level}.")
    summary_rows = [[R.badge(lv, True), R.esc(t)] for lv, t in titles]
    body = intro + R.table(["Priority", "Item"], summary_rows, raw_cols=(0, 1)) + "".join(cards)
    if overflow:
        body += R.callout(f"{overflow} additional lower-priority items are listed on the dashboard.")
    banner = R.banner("SIMULATION - TEST DATA - NOT A REAL WEATHER EVENT", "sim") if ctx.simulation else None
    html = R.page(cfg, f"{top_level} alert: {top_title}", f"Immediate alert - {fmt_ct(now)}", body,
                  preheader=f"{top_level}: {top_title}", top_banner=banner)
    return subject, html, R.to_text(html)