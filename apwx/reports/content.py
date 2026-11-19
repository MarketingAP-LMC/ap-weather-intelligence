"""Deliverable D - Agricultural content opportunities for American Prime's Instagram.

Ideas are generated only from verified data the platform already holds (events, NWS
forecasts, USDA reports, Drought Monitor). Headlines are factual, not sensational.
Hashtags: one state tag, one crop tag, one topic tag (state tags are unverified).
"""
from .. import db
from ..config import level_rank
from ..timeutil import fmt_ct, parse_iso, to_ct
from . import common as C
from . import render as R

HEADLINES = {
    "freeze": "{state} {crop}: forecast lows near {tmin:.0f} F - what growers are watching",
    "extreme_cold": "Cold snap and {crop}: what {state} growers watch for winter survival",
    "heat": "Heat ahead for {state} {crop}: why crop stage matters",
    "heavy_rain": "{state}: up to {qpf:.1f} in of rain forecast - what it means for {crop} fields",
    "harvest_rain": "Rain in the forecast for {state}: where the {crop} harvest stands",
    "severe_storm": "Storm risk over {state} {crop} counties: hail and wind basics",
    "high_wind": "Strong winds forecast in {state}: lodging risk for standing {crop}",
    "winter_storm": "Snow in the forecast for {state}: where the {crop} harvest stands",
    "drought": "Drought check: {state} {crop} counties in this week's Drought Monitor",
    "outlook_cold": "{state} 8-14 day outlook leans colder: what it means for {crop}",
    "outlook_wet": "{state} 8-14 day outlook leans wetter: what it means for {crop}",
    "outlook_dry": "{state} 8-14 day outlook leans drier: what it means for {crop}",
}


def _format_for(ev, lead):
    hz = ev["hazard"]
    if level_rank(ev["level"]) >= level_rank("HIGH") and lead is not None and lead <= 72:
        return "Reel", "Fast-moving, map-friendly event; AP Weather Watch Reels have reached the most accounts so far."
    if hz in ("drought", "outlook_cold", "outlook_wet", "outlook_dry"):
        return "Carousel", "Explainer that rewards saving: map + what the categories mean + crop angle."
    if hz in ("harvest_rain", "winter_storm", "heavy_rain"):
        return "Story", "Timely field-operations update; low production cost, pairs with a poll or question sticker."
    return "Educational post", "Evergreen explainer on how this hazard affects the crop at its current stage."


def _facts(cfg, ev, cyc_time):
    facts = []
    fc_src = f"NWS gridded forecast, cycle {fmt_ct(cyc_time)}" if cyc_time else "NWS gridded forecast"
    for line in C.stat_lines(ev)[:3]:
        facts.append({"text": line, "source": fc_src if not line.startswith(("share", "Drought", "one-week")) else "U.S. Drought Monitor",
                      "url": "https://www.weather.gov/"})
    fips = [c["fips"] for c in ev["counties"]]
    for crop, info in ev["crops"].items():
        share = C.state_share(cfg, ev["state"], crop, fips)
        facts.append({"text": f"The {ev['n_counties']} affected counties hold about {share:.0f}% of {cfg.states[ev['state']]['name']}'s "
                              f"{C.crop_label(cfg, crop).lower()} acreage", "source": "USDA NASS Cropland Data Layer 2025",
                      "url": "https://nassgeodata.gmu.edu/CropScape/"})
        if info.get("stage_detail"):
            src = "USDA NASS Crop Progress" if info.get("stage_basis") == "nass" else "Typical calendar estimate (USDA usual planting/harvest dates)"
            facts.append({"text": info["stage_detail"], "source": src,
                          "url": "https://www.nass.usda.gov/Publications/National_Crop_Progress/"})
        break
    return facts


def _caption(cfg, ev, crop, headline_vals):
    st_name = cfg.states[ev["state"]]["name"]
    hz = C.hazard_label(cfg, ev["hazard"]).lower()
    t = C.hazard_text(cfg, crop, ev["hazard"])
    lines = [f"AP Weather Watch - {st_name}."]
    lines.append(f"Official forecasts show {hz} for {ev['n_counties']} {crop_word(cfg, crop)}-growing counties "
                 f"({C.county_names(ev['counties'], 4)}).")
    if t.get("vulnerable"):
        lines.append(f"Why it matters: {t['vulnerable'].rstrip('.')}.")
    if t.get("monitor"):
        lines.append(f"What growers watch: {t['monitor'][0].rstrip('.')}.")
    lines.append("This is a forecast, not observed damage - conditions vary field to field. Check your local NWS forecast for updates.")
    return " ".join(lines)


def crop_word(cfg, crop):
    return C.crop_label(cfg, crop).lower()


def generate(ctx, stages):
    """Create new content ideas from active events; returns number of new ideas."""
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    ccfg = cfg.get("content", default={})
    if not ccfg.get("enabled", True):
        return 0
    min_level = ccfg.get("min_level", "MODERATE")
    cyc = db.latest_cycle(conn)
    cyc_time = parse_iso(cyc["cycle_utc"]) if cyc else None
    evs = [db.jload(e["data"], {}) for e in db.events_with_status(conn, "active")]
    evs = [e for e in evs if e and level_rank(e.get("level")) >= level_rank(min_level)]
    evs.sort(key=lambda e: (-level_rank(e["level"]), -e["n_counties"]))
    new = 0
    for ev in evs[: ccfg.get("max_ideas_per_day", 5)]:
        crop = max(ev["crops"].items(), key=lambda kv: (level_rank(kv[1]["level"]), kv[1]["acres"]))[0]
        leads = [c["lead_h"] for c in ev["counties"] if c.get("lead_h") is not None]
        lead = min(leads) if leads else None
        st = cfg.states[ev["state"]]
        vals = {"state": st["name"], "crop": C.crop_label(cfg, crop).lower(),
                "tmin": ev["stats"].get("tmin7d") or 0, "qpf": ev["stats"].get("qpf72") or ev["stats"].get("qpf7d") or 0}
        headline = HEADLINES.get(ev["hazard"], "{state} {crop}: weather to watch").format(**vals)
        headline = headline[0].upper() + headline[1:]
        fmt, why_fmt = _format_for(ev, lead)
        tags = [st.get("tag", ""), ccfg.get("crop_tags", {}).get(crop, ""), ccfg.get("topic_tags", {}).get(ev["hazard"], "#agweather")]
        idea = {
            "idea_key": f"{ev['event_key']}:{ev['level']}", "event_key": ev["event_key"], "level": ev["level"],
            "headline": headline, "crop": C.crop_label(cfg, crop), "region": f"{st['name']} ({C.county_names(ev['counties'], 5)})",
            "series": f"{ccfg.get('series_name', 'AP Weather Watch')} - {st['name']}",
            "why": " ".join(C.why_it_matters(cfg, ev)[:1]) or C.hazard_text(cfg, crop, ev["hazard"]).get("short_term", ""),
            "facts": _facts(cfg, ev, cyc_time), "format": fmt, "format_reason": why_fmt,
            "caption": _caption(cfg, ev, crop, vals),
            "visual": (f"{ccfg.get('series_name', 'AP Weather Watch')} - {st['name']} template: county map of the "
                       f"{ev['n_counties']} affected counties shaded by the key forecast value; frame 2: crop stage now "
                       f"(USDA); frame 3: what to watch. Brand navy #1b395f / Prime Green #13a959, label 'Forecast - not "
                       f"observed', source line (NWS, USDA NASS). The existing AP_Weather_Watch_Kit renderer can produce it."),
            "hashtags": [t for t in tags if t], "hashtags_verified": bool(ccfg.get("tags_verified")),
            "created": now.isoformat(), "lead_h": lead,
        }
        if db.idea_save(conn, idea, now):
            new += 1
    conn.commit()
    return new


def idea_card(idea):
    facts = [f"{R.esc(f['text'])} - <i>{R.esc(f['source'])}</i>" for f in idea.get("facts", [])]
    tags = " ".join(idea.get("hashtags", []))
    note = "" if idea.get("hashtags_verified") else " (state tag unverified - check before posting)"
    inner = (R.h3(R.esc(idea["headline"]) + " " + R.badge(idea.get("level", "NONE"), True), raw=True) +
             R.kv([("Series", R.esc(idea.get("series"))), ("Crop", R.esc(idea.get("crop"))), ("Region", R.esc(idea.get("region"))),
                   ("Format", R.esc(f"{idea.get('format')} - {idea.get('format_reason')}"))]) +
             R.p(f"<b>Why it matters to farmers:</b> {R.esc(idea.get('why'))}", size=13, raw=True) +
             R.p("<b>Verified facts and sources:</b>", size=13, raw=True) + R.ul(facts, raw=True, size=12) +
             R.p(f"<b>Suggested caption:</b> {R.esc(idea.get('caption'))}", size=13, raw=True) +
             R.p(f"<b>Visual direction:</b> {R.esc(idea.get('visual'))}", size=13, raw=True) +
             R.p(f"<b>Hashtags:</b> {R.esc(tags)}{R.esc(note)}", size=13, raw=True))
    return R.card(inner, idea.get("level"))


def build_email(ctx, ideas):
    cfg, now = ctx.cfg, ctx.now
    cards = "".join(idea_card(db.jload(i["data"], {})) for i in ideas)
    intro = R.p(f"{len(ideas)} content opportunit{'y' if len(ideas) == 1 else 'ies'} based on today's verified weather "
                "and crop data. Each idea uses official forecasts and USDA data only; please review facts before posting.")
    banner = R.banner("SIMULATION - TEST DATA - NOT A REAL WEATHER EVENT", "sim") if ctx.simulation else None
    prefix = cfg.get("notify", "subject_prefix", default="[AP Weather]")
    sim = "SIMULATION - " if ctx.simulation else ""
    first = db.jload(ideas[0]["data"], {}).get("headline", "") if ideas else ""
    ct = to_ct(now)
    subject = f"{prefix} {sim}Content opportunities {ct:%b} {ct.day}: {first[:80]}"
    html = R.page(cfg, "Agricultural content opportunities", f"Content - {fmt_ct(now)}", intro + cards,
                  preheader=first, top_banner=banner)
    return subject, html, R.to_text(html)