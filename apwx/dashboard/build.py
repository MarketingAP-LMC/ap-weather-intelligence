"""Export dashboard data (JSON) and copy the static site into the published folder.

Output layout (served by GitHub Pages from the `data` branch, folder /docs):
  index.html, app.js, styles.css, assets/        static app (copied from site/)
  data/latest.json                              current state of everything
  data/hourly/<ST>.json                         next-72-hour hourly series per county
  data/history/<ST>.json                        forecast history per county (recent cycles)
  data/alerts.json, events.json, reports.json, content.json, sources.json
  reports/<kind>-<period>.html                  archived daily/weekly reports
"""
import json
import shutil
from datetime import timedelta

from .. import db
from ..analysis import metrics
from ..config import ROOT, level_rank, max_level
from ..notify import email
from ..reports import aggregate as A
from ..timeutil import fmt_ct, hours_between, iso, parse_iso, to_ct

SITE_SRC = ROOT / "site"


def _dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=str), encoding="utf-8")


def _county_level(assess_by_fips, fips):
    items = assess_by_fips.get(fips, [])
    lv = "NONE"
    for a in items:
        lv = max_level(lv, a["level"])
    return lv, [[a["hazard"], a["level"], a["crop"]] for a in sorted(items, key=lambda a: -level_rank(a["level"]))][:8]


def build(ctx, stages):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    out = cfg.site_dir
    out.mkdir(parents=True, exist_ok=True)
    for item in SITE_SRC.iterdir():
        dest = out / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    (out / ".nojekyll").write_text("", encoding="utf-8")

    cyc = db.latest_cycle(conn)
    snaps = db.snapshots_for_cycle(conn, cyc["id"]) if cyc else {}
    assessments = db.jload(db.meta_get(conn, "latest_assessments"), []) or []
    by_fips = {}
    for a in assessments:
        by_fips.setdefault(a["fips"], []).append(a)
    events = db.events_with_status(conn, "active")
    alerts = db.alerts_active(conn)
    levels = A.state_levels(cfg, events, alerts)
    aggs = A.state_aggregates(cfg, snaps) if snaps else {}
    matrix = A.crop_matrix(cfg, assessments)
    obs = db.station_obs_all(conn)

    counties = []
    for f, c in cfg.counties.items():
        s = snaps.get(f, {})
        fc = s.get("fc") or {}
        lv, hz = _county_level(by_fips, f)
        al = [a["event"] for a in alerts if f in (a.get("monitored_fips") or "").split(",")]
        if al:
            for a in alerts:
                if f in (a.get("monitored_fips") or "").split(","):
                    lv = max_level(lv, a["risk_level"])
        counties.append({
            "f": f, "n": c["name"], "s": c["state"], "lat": c["lat"], "lon": c["lon"], "r": c.get("region"),
            "c": c.get("crops", []), "a": c.get("acres", {}), "sh": c.get("share", {}), "lv": lv, "hz": hz, "al": al,
            "st": (c.get("nws") or {}).get("station"),
            "fc": {k: fc.get(k) for k in ("tmin24", "tmin72", "tmin7d", "tmin7d_t", "tmax7d", "qpf24", "qpf72", "qpf7d",
                                          "snow72", "ice72", "gust72", "pop72", "upd", "stale")} | {"now": fc.get("now"),
                                                                                                 "daily": fc.get("daily")},
            "spc": s.get("spc", {}), "wpc": s.get("wpc", {}), "cpc": s.get("cpc", {}), "dm": s.get("dm"), "obs": s.get("obs"),
        })
    ev_out = []
    for e in events:
        d = db.jload(e["data"], {})
        hist = db.event_history(conn, e["event_key"], 40)
        d.update({"event_key": e["event_key"], "first_detected": e["first_detected_utc"], "last_updated": e["last_updated_utc"],
                  "last_notified": e["last_notified_utc"],
                  "history": [{"t": h["recorded_utc"], "level": h["risk_level"], **(db.jload(h["data"], {}) or {})} for h in reversed(hist)]})
        ev_out.append(d)
    al_out = []
    for a in alerts:
        d = db.jload(a["data"], {}) or {}
        al_out.append({"id": a["id"], "event": a["event"], "level": a["risk_level"], "states": a["states"],
                       "fips": (a.get("monitored_fips") or "").split(","), "onset": a["onset_utc"], "ends": a["ends_utc"],
                       "headline": a["headline"], "url": a["url"], "severity": a["severity"],
                       "reasons": (d.get("classification") or {}).get("reasons", []), "sender": d.get("sender"),
                       "description": (d.get("description") or "")[:1500], "instruction": (d.get("instruction") or "")[:600]})
    st_out = []
    for st, s in cfg.states.items():
        stg = {}
        for crop in cfg.crops["crops"]:
            e = stages.get((st, crop))
            if e and any(crop in (c.get("crops") or []) for c in cfg.counties_in_state(st)):
                stg[crop] = {"basis": e.get("basis"), "detail": e.get("detail"), "phases": e.get("phases"),
                             "as_of": e.get("as_of"), "confidence": e.get("confidence")}
        st_out.append({"abbr": st, "fips": s["fips"], "name": s["name"], "tier": s.get("tier"), "level": levels.get(st, "NONE"),
                       "agg": aggs.get(st, {}), "stages": stg,
                       "n_counties": sum(1 for c in cfg.counties.values() if c["state"] == st)})
    mx = [{"state": st, "crop": crop, "level": cell["level"], "hazards": cell["hazards"],
           "reasons": (cell.get("top") or {}).get("reasons", []), "county": (cell.get("top") or {}).get("county")}
          for (st, crop), cell in matrix.items()]
    sched = cfg.settings.get("schedule", {})
    last_cycle = db.meta_get_time(conn, "last_cycle_utc")
    latest = {
        "generated": iso(now), "generated_ct": fmt_ct(now), "simulation": ctx.simulation,
        "platform": {"name": cfg.get("platform_name"), "org": cfg.get("org_name"), "tagline": cfg.get("tagline")},
        "cycle": {"utc": cyc["cycle_utc"] if cyc else None, "ct": fmt_ct(parse_iso(cyc["cycle_utc"])) if cyc else None,
                  "ok": cyc["counties_ok"] if cyc else 0, "failed": cyc["counties_failed"] if cyc else 0},
        "next_cycle": iso(last_cycle + timedelta(hours=sched.get("forecast_cycle_hours", 3))) if last_cycle else None,
        "schedule": {"daily_ct": f"{sched.get('daily_report_hour_ct', 6)}:{sched.get('daily_report_minute_ct', 30):02d}",
                     "weekly": f"weekday {sched.get('weekly_report_weekday', 0)} {sched.get('weekly_report_hour_ct', 7)}:00 CT"},
        "states": st_out, "counties": counties, "events": ev_out, "alerts": al_out, "matrix": mx,
        "crops": {k: v["label"] for k, v in cfg.crops["crops"].items()},
        "hazard_labels": cfg.get("hazard_labels", default={}),
        "observations": [dict(v, station=k, county=cfg.counties.get(v.get("fips"), {}).get("name"),
                              state=cfg.counties.get(v.get("fips"), {}).get("state")) for k, v in obs.items()],
        "email": email.describe() | {"recipient": "configured" if email.describe()["recipient"] != "(unset)" else "not set"},
        "last_tick": db.meta_get(conn, "last_tick_utc"),
    }
    _dump(out / "data" / "latest.json", latest)

    hourly = db.hourly_all(conn)
    by_state = {}
    for f, h in hourly.items():
        c = cfg.counties.get(f)
        if c:
            by_state.setdefault(c["state"], {})[f] = h
    for st, d in by_state.items():
        _dump(out / "data" / "hourly" / f"{st}.json", d)

    rows = db.history_since(conn, now - timedelta(days=10))
    times = sorted({r["cycle_utc"] for r in rows})
    tindex = {t: i for i, t in enumerate(times)}
    keys = ("tmin7d", "tmin72", "tmax7d", "qpf72", "qpf7d", "snow72")
    hist = {st: {"t": times, "counties": {}, "state": {k: [None] * len(times) for k in keys}} for st in cfg.monitored_states}
    per_state = {}
    for r in rows:
        cc = cfg.counties.get(r["fips"])
        if not cc:
            continue
        i = tindex[r["cycle_utc"]]
        row = hist[cc["state"]]["counties"].setdefault(r["fips"], {k: [None] * len(times) for k in keys})
        bucket = per_state.setdefault((cc["state"], i), {k: [] for k in keys})
        for k in keys:
            row[k][i] = r[k]
            if r[k] is not None:
                bucket[k].append(r[k])
    for (st, i), bucket in per_state.items():
        for k in keys:
            vals = bucket[k]
            if vals:
                hist[st]["state"][k][i] = min(vals) if k.startswith("tmin") else (
                    round(sum(vals) / len(vals), 2) if k.startswith("qpf") else max(vals))
    for st, d in hist.items():
        _dump(out / "data" / "history" / f"{st}.json", d)

    recent_alerts = db.alerts_since(conn, now - timedelta(days=30))
    _dump(out / "data" / "alerts.json", {
        "active": al_out,
        "history": [{"id": a["id"], "event": a["event"], "level": a["risk_level"], "states": a["states"],
                     "n": len((a.get("monitored_fips") or "").split(",")), "first_seen": a["first_seen_utc"],
                     "onset": a["onset_utc"], "ends": a["ends_utc"], "active": a["active"], "url": a["url"]}
                    for a in recent_alerts],
        "notifications": db.notifications_recent(conn, 200),
        "storm_reports": db.storm_reports_since(conn, now - timedelta(days=3)),
    })
    ended = [e for e in db.events_since(conn, now - timedelta(days=14)) if e["status"] != "active"]
    _dump(out / "data" / "events.json", {"active": ev_out, "recent_ended": [
        {"event_key": e["event_key"], "title": e["title"], "level": e["risk_level"], "first": e["first_detected_utc"],
         "last": e["last_updated_utc"]} for e in ended]})
    reps = db.reports_recent(conn, None, 90)
    rep_dir = out / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    rep_list = []
    for r in reps:
        fname = f"{r['kind']}-{r['period_key']}.html"
        path = rep_dir / fname
        if not path.exists() or r["period_key"] >= (to_ct(now).date() - timedelta(days=8)).isoformat():
            html = db.report_html(conn, r["id"]) or ""
            path.write_text(html.replace("cid:ap-logo", "../assets/ap-logo-white.png"), encoding="utf-8")
        rep_list.append({"kind": r["kind"], "period": r["period_key"], "created": r["created_utc"], "subject": r["subject"],
                         "file": f"reports/{fname}", "summary": (db.jload(r["summary"], {}) or {}).get("top", [])})
    _dump(out / "data" / "reports.json", rep_list)
    _dump(out / "data" / "content.json", [db.jload(i["data"], {}) | {"created_utc": i["created_utc"], "emailed": i["emailed_utc"]}
                                          for i in db.ideas_recent(conn, 60)])
    srcs = {s["id"]: s for s in cfg.sources["data_sources"]}
    status = []
    for s in db.source_status(conn):
        meta = srcs.get(s["source"], {})
        ok = s.get("last_success_utc")
        age = hours_between(parse_iso(ok), now) if ok else None
        status.append(s | {"name": meta.get("name", s["source"]), "agency": meta.get("agency"), "kind": meta.get("kind"),
                           "info": meta.get("info"), "cadence": meta.get("cadence"), "age_h": round(age, 2) if age is not None else None,
                           "healthy": bool(ok) and (not s.get("last_error_utc") or s["last_error_utc"] <= ok)})
    _dump(out / "data" / "sources.json", {"sources": status, "runs": db.runs_recent(conn, 30),
                                          "registry": cfg.sources["data_sources"]})
    return {"counties": len(counties), "events": len(ev_out), "alerts": len(al_out), "reports": len(rep_list)}