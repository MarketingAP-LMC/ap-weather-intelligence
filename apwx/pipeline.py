"""Orchestration.

A `tick` (run every 5-10 minutes by the scheduler) always polls official NWS alerts
and then runs whatever is due: the 3-hourly forecast cycle, auxiliary data refreshes,
the daily report, the weekly outlook, content opportunities, delivery retries,
maintenance and the dashboard build. Because "due" is computed from the database,
missed or delayed scheduler runs are caught up automatically on the next tick.
"""
import hashlib
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, time, timedelta

from . import db
from . import http as net
from .analysis import alerts as alerts_mod
from .analysis import assess, metrics
from .analysis import events as events_mod
from .analysis import stages as stages_mod
from .config import level_rank
from .notify import email
from .sources import acis, cpc, nass, nws, spc, usdm, wpc
from .timeutil import hours_between, iso, iso_week_key, parse_iso, to_ct, utcnow


class Ctx:
    def __init__(self, cfg, conn, now, log=print, simulation=False):
        self.cfg, self.conn, self.now, self.log, self.simulation = cfg, conn, now, log, simulation
        self.immediate = []
        self.changed = False
        self.product_times = {}
        self.summary = {}
        self.errors = []


def step(ctx, name, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - isolate failures; other steps keep running
        ctx.errors.append(f"{name}: {type(e).__name__}: {str(e)[:300]}")
        ctx.log(f"[error] {name}: {type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
        return None


def _due(conn, key, hours, now):
    last = db.meta_get_time(conn, key)
    return last is None or hours_between(last, now) >= hours


# ------------------------------------------------------------------ crop stages
def compute_stages(cfg, conn, now):
    prog = db.progress_latest(conn)
    d = to_ct(now).date()
    out = {}
    for st in cfg.monitored_states:
        for crop_id, crop_cfg in cfg.crops["crops"].items():
            if crop_cfg.get("states") and st not in crop_cfg["states"]:
                continue
            model = crop_cfg.get("stage_model_by_state", {}).get(st)
            c = dict(crop_cfg)
            if model:
                c["stage_model"] = model
            out[(st, crop_id)] = stages_mod.estimate(crop_id, c, st, d, prog)
    return out


# ------------------------------------------------------------------ auxiliary data
def refresh_nass(ctx, force=False):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    if not force and not _due(conn, "nass_checked_utc", cfg.get("schedule", "nass_refresh_hours", default=6), now):
        return None
    parsed = nass.fetch_latest(to_ct(now).date())
    db.meta_set(conn, "nass_checked_utc", iso(now))
    if parsed:
        db.progress_save(conn, parsed["rows"])
        db.meta_set(conn, "nass_latest", f"{parsed['released']}|{parsed['week_ending']}|{parsed['url']}")
        ctx.product_times["nass_crop_progress"] = str(parsed["released"])
        return {"released": str(parsed["released"]), "rows": len(parsed["rows"])}
    conn.commit()
    return {"released": None}


def refresh_drought(ctx, force=False):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    if not force and not _due(conn, "usdm_checked_utc", cfg.get("schedule", "drought_refresh_hours", default=12), now):
        return None
    end = to_ct(now).date()
    start = end - timedelta(days=36)
    n = 0
    for st in cfg.monitored_states:
        rows = usdm.fetch_state(st, start, end)
        db.drought_save(conn, rows)
        n += len(rows)
    db.meta_set(conn, "usdm_checked_utc", iso(now))
    conn.commit()
    dates = db.drought_dates(conn, 1)
    if dates:
        ctx.product_times["usdm"] = dates[0]
    return {"rows": n, "latest_map": dates[0] if dates else None}


def refresh_observed(ctx, force=False):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    if not force and not _due(conn, "acis_checked_utc", cfg.get("schedule", "observed_refresh_hours", default=12), now):
        return None
    edate = to_ct(now).date() - timedelta(days=1)
    sdate = edate - timedelta(days=14)
    n = 0
    for st, s in cfg.states.items():
        rows = [r for r in acis.fetch_state_daily(st, s["fips"], sdate, edate) if r["fips"] in cfg.counties]
        db.observed_save(conn, rows)
        n += len(rows)
    db.meta_set(conn, "acis_checked_utc", iso(now))
    conn.commit()
    last = db.observed_last_date(conn)
    if last:
        ctx.product_times["acis"] = last
    return {"rows": n, "through": last}


def _norm_county(name):
    n = re.sub(r"[^A-Z ]", "", (name or "").upper().replace("SAINT", "ST"))
    return re.sub(r"\b(COUNTY|PARISH)\b", "", n).strip()


def refresh_storm_reports(ctx, force=False):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    minutes = cfg.get("schedule", "storm_reports_refresh_minutes", default=30)
    if not force and not _due(conn, "spc_reports_checked_utc", minutes / 60.0, now):
        return None
    index = {(c["state"], _norm_county(c["name"])): f for f, c in cfg.counties.items()}
    new = 0
    for day in ("today", "yesterday"):
        reports = spc.fetch_storm_reports(day)
        keep = []
        for r in reports:
            if r["state"] not in cfg.states:
                continue
            r["fips"] = index.get((r["state"], _norm_county(r["county"])))
            keep.append(r)
        new += db.storm_reports_save(conn, keep, now)
    db.meta_set(conn, "spc_reports_checked_utc", iso(now))
    conn.commit()
    if new:
        ctx.changed = True
    return {"new_reports": new}


def drought_summary(conn):
    dates = db.drought_dates(conn, 6)
    if not dates:
        return {}, None
    latest = db.drought_for_date(conn, dates[0])
    prev = db.drought_for_date(conn, dates[1]) if len(dates) > 1 else {}
    prev4 = db.drought_for_date(conn, dates[4]) if len(dates) > 4 else {}
    out = {}
    for fips, r in latest.items():
        d = {"date": dates[0], "d0": r["d0"], "d1": r["d1"], "d2": r["d2"], "d3": r["d3"], "d4": r["d4"],
             "dsci": usdm.dsci(r)}
        if fips in prev:
            d["chg1"] = round(d["dsci"] - usdm.dsci(prev[fips]), 1)
        if fips in prev4:
            d["chg4"] = round(d["dsci"] - usdm.dsci(prev4[fips]), 1)
        out[fips] = d
    return out, dates[0]


def observed_summary(conn):
    last = db.observed_last_date(conn)
    if not last:
        return {}, None
    end = date.fromisoformat(last)
    rows = db.observed_range(conn, (end - timedelta(days=13)).isoformat(), last)
    out = {}
    for fips, rs in rows.items():
        def total(n):
            vals = [r["pcpn"] for r in rs if r["pcpn"] is not None and date.fromisoformat(r["obs_date"]) > end - timedelta(days=n)]
            return round(sum(vals), 2) if vals else None
        last7 = [r for r in rs if date.fromisoformat(r["obs_date"]) > end - timedelta(days=7)]
        mins = [r["mint"] for r in last7 if r["mint"] is not None]
        maxs = [r["maxt"] for r in last7 if r["maxt"] is not None]
        out[fips] = {"through": last, "pcpn1": total(1), "pcpn7": total(7), "pcpn14": total(14),
                     "mint7": round(min(mins), 1) if mins else None, "maxt7": round(max(maxs), 1) if maxs else None}
    return out, last


# ------------------------------------------------------------------ forecast cycle
def fetch_county_forecast(county, now):
    info = county.get("nws") or {}
    try:
        props = nws.fetch_gridpoint(info["grid"], info["x"], info["y"])
    except net.FetchError as e:
        if e.status not in (404, 301):
            raise
        p = nws.fetch_point(county["lat"], county["lon"])
        info.update(grid=p["gridId"], x=p["gridX"], y=p["gridY"])
        props = nws.fetch_gridpoint(info["grid"], info["x"], info["y"])
    m, hourly = metrics.compute(props, now)
    if m["valid_h"] < 24:
        raise ValueError(f"insufficient forecast data ({m['valid_h']} h)")
    return m, hourly


def run_cycle(ctx, stages):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    ctx.log(f"[cycle] start {iso(now)} for {len(cfg.counties)} counties")
    prev_cycle = db.latest_cycle(conn)
    prev_snaps = db.snapshots_for_cycle(conn, prev_cycle["id"]) if prev_cycle else {}
    cycle_id = db.cycle_create(conn, now)
    results, failures = {}, {}
    conc = cfg.get("fetch", "nws_concurrency", default=3)
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = {ex.submit(fetch_county_forecast, c, now): f for f, c in cfg.counties.items()}
        for fut in as_completed(futs):
            f = futs[fut]
            try:
                results[f] = fut.result()
            except Exception as e:  # noqa: BLE001
                failures[f] = f"{type(e).__name__}: {str(e)[:160]}"
    spc_o = step(ctx, "spc_outlooks", spc.fetch_outlooks) or {}
    wpc_p = step(ctx, "wpc_products", wpc.fetch_products) or {}
    cpc_p = step(ctx, "cpc_products", cpc.fetch_products) or {}
    for key, prod in list(spc_o.items()) + list(wpc_p.items()) + list(cpc_p.items()):
        if prod.get("issue"):
            src = "spc_outlooks" if key in spc_o else ("cpc_outlooks" if key in cpc_p else ("wpc_qpf" if key.startswith("qpf") else "wpc_ero"))
            ctx.product_times[src] = prod["issue"]
    dm, dm_date = drought_summary(conn)
    obs, obs_date = observed_summary(conn)
    snaps, flats = {}, {}
    stale = 0
    for f, c in cfg.counties.items():
        snap = {"t": iso(now)}
        if f in results:
            snap["fc"], hourly = results[f]
            db.hourly_save(conn, f, cycle_id, hourly)
        elif prev_snaps.get(f, {}).get("fc") and prev_cycle and hours_between(parse_iso(prev_cycle["cycle_utc"]), now) <= 6.5:
            snap["fc"] = dict(prev_snaps[f]["fc"], stale=True)
            stale += 1
        snap["spc"] = spc.point_values(spc_o, c["lon"], c["lat"]) if spc_o else {}
        snap["wpc"] = wpc.point_values(wpc_p, c["lon"], c["lat"]) if wpc_p else {}
        snap["cpc"] = cpc.point_values(cpc_p, c["lon"], c["lat"]) if cpc_p else {}
        if f in dm:
            snap["dm"] = dm[f]
        if f in obs:
            snap["obs"] = obs[f]
        snaps[f] = snap
        flats[f] = metrics.flatten(snap)
        db.snapshot_save(conn, cycle_id, f, snap)
        if snap.get("fc") and not snap["fc"].get("stale"):
            db.history_save(conn, iso(now), f, snap["fc"])
    notes = {"failures": dict(list(failures.items())[:25]), "n_failures": len(failures), "stale_reused": stale,
             "spc_issue": (spc_o.get("d1_cat") or {}).get("issue"), "drought_map": dm_date, "observed_through": obs_date}
    db.cycle_finish(conn, cycle_id, len(results), len(failures), notes)
    ctx.log(f"[cycle] forecasts ok={len(results)} failed={len(failures)} stale_reused={stale}")
    step(ctx, "station_obs", refresh_station_obs, ctx)

    assessments = []
    for f, c in cfg.counties.items():
        assessments += assess.assess_county(c, snaps[f], flats[f], stages, cfg.crops, cfg.get("risk", default={}))
    labels = cfg.get("hazard_labels", default={})
    evs = events_mod.cluster(assessments, flats, cfg.counties, now, labels, cfg.crops)
    db.meta_set(conn, "latest_assessments", db.jdump(assessments))
    db.meta_set(conn, "last_cycle_utc", iso(now))
    db.meta_set(conn, "last_cycle_id", cycle_id)
    items = reconcile_events(ctx, evs, cycle_id)
    conn.commit()
    ctx.changed = True
    return {"cycle_id": cycle_id, "ok": len(results), "failed": len(failures), "assessments": len(assessments),
            "events": len(evs), "immediate": len(items)}


def refresh_station_obs(ctx):
    cfg, conn = ctx.cfg, ctx.conn
    n_per = cfg.get("fetch", "obs_stations_per_state", default=6)
    targets = []
    for st in cfg.monitored_states:
        cs = sorted(cfg.counties_in_state(st), key=lambda c: -sum(c["acres"].values()))
        seen = set()
        for c in cs:
            s = (c.get("nws") or {}).get("station")
            if s and s not in seen:
                seen.add(s)
                targets.append((s, c["fips"]))
            if len(seen) >= n_per:
                break
    ok = 0

    def one(t):
        return t, nws.fetch_latest_obs(t[0])

    with ThreadPoolExecutor(max_workers=3) as ex:
        for fut in as_completed([ex.submit(one, t) for t in targets]):
            try:
                (station, fips), ob = fut.result()
                db.station_obs_save(conn, station, fips, ob.get("time"), ob)
                ok += 1
            except Exception:  # noqa: BLE001
                continue
    conn.commit()
    return ok


def reconcile_events(ctx, evs, cycle_id):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    notify = cfg.get("notify", default={})
    risk = cfg.get("risk", default={})
    immediate_levels = risk.get("immediate_levels", ["CRITICAL", "HIGH"])
    active = {(r["hazard"], r["state"]): r for r in db.events_with_status(conn, "active")}
    seen, items = set(), []
    for ev in evs:
        row = active.get((ev["hazard"], ev["state"]))
        key = row["event_key"] if row else f"{ev['hazard']}:{ev['state']}:{to_ct(now):%Y%m%d%H%M}"
        ev["event_key"] = key
        prev_data = db.jload(row["data"], None) if row else None
        ev["changes_vs_previous"] = events_mod.compare_previous(prev_data, ev)
        ev["first_detected"] = row["first_detected_utc"] if row else iso(now)
        reason, changes = events_mod.notification_reason(
            ev, row, notify.get("material_change", {}), notify.get("followup_cooldown_hours", 6), now, immediate_levels)
        rec = {
            "event_key": key, "hazard": ev["hazard"], "state": ev["state"], "title": ev["title"],
            "risk_level": ev["level"], "status": "active", "first_detected_utc": ev["first_detected"],
            "last_updated_utc": iso(now), "window_start_utc": ev["window_start"], "window_end_utc": ev["window_end"],
            "fingerprint": events_mod.fingerprint(ev), "missed_cycles": 0, "data": db.jdump(ev),
            "last_notified_utc": row.get("last_notified_utc") if row else None,
            "last_notified_level": row.get("last_notified_level") if row else None,
            "last_notified_fingerprint": row.get("last_notified_fingerprint") if row else None,
            "last_notified_data": row.get("last_notified_data") if row else None,
        }
        if reason:
            rec.update(last_notified_utc=iso(now), last_notified_level=ev["level"],
                       last_notified_fingerprint=rec["fingerprint"], last_notified_data=db.jdump(ev))
            items.append({"type": "event", "key": f"event:{key}:{rec['fingerprint']}", "level": ev["level"],
                          "reason": reason, "changes": changes, "event": ev, "prev": prev_data})
        db.event_save(conn, rec)
        db.event_history_add(conn, key, cycle_id, now, ev["level"],
                             {"level": ev["level"], "n_counties": ev["n_counties"], "stats": ev["stats"],
                              "window_start": ev["window_start"]})
        seen.add(key)
    end_after = risk.get("event_end_after_missed_cycles", 2)
    for row in active.values():
        if row["event_key"] in seen:
            continue
        missed = (row.get("missed_cycles") or 0) + 1
        row["missed_cycles"] = missed
        if missed >= end_after:
            row["status"] = "ended"
        row["last_updated_utc"] = iso(now)
        db.event_save(conn, row)
    # attach official NWS text forecast for the worst county of each immediate event
    max_text = 0 if ctx.simulation else cfg.get("fetch", "forecast_text_max_counties", default=12)
    for it in items[:max_text]:
        f = it["event"]["stats"].get("worst_fips") or (it["event"]["counties"][0]["fips"] if it["event"]["counties"] else None)
        c = cfg.counties.get(f)
        if not c:
            continue
        try:
            periods = nws.fetch_forecast_periods(c["nws"]["grid"], c["nws"]["x"], c["nws"]["y"])
            it["forecast_text"] = {"county": c["name"], "state": c["state"],
                                   "periods": [{"name": pd.get("name"), "text": pd.get("detailedForecast")} for pd in periods[:4]]}
        except Exception:  # noqa: BLE001
            pass
    ctx.immediate.extend(items)
    return items


# ------------------------------------------------------------------ official alerts
def poll_alerts(ctx, stages, features=None):
    cfg, conn, now = ctx.cfg, ctx.conn, ctx.now
    feats = features if features is not None else nws.fetch_active_alerts(cfg.monitored_states)
    immediate_levels = cfg.get("risk", "immediate_levels", default=["CRITICAL", "HIGH"])
    parsed = sorted((nws.parse_alert(f) for f in feats), key=lambda a: a.get("sent") or "")
    active_ids, items, relevant, new = [], [], 0, 0
    for a in parsed:
        if a.get("status") != "Actual":
            continue
        cls = alerts_mod.classify(a, cfg, stages)
        if cls is None:
            continue
        relevant += 1
        active_ids.append(a["id"])
        states = sorted({cfg.counties[f]["state"] for f in cls["monitored_fips"]})
        a["classification"] = cls
        rec = {"track_key": a["track_key"], "event": a["event"], "severity": a["severity"], "certainty": a["certainty"],
               "urgency": a["urgency"], "message_type": a["message_type"], "sent_utc": a["sent"],
               "onset_utc": a["onset"], "ends_utc": a["ends"], "expires_utc": a["expires"], "headline": a["headline"],
               "area_desc": a["area_desc"][:1000], "states": ",".join(states), "fips": ",".join(a["fips"]),
               "monitored_fips": ",".join(cls["monitored_fips"]), "url": a["url"], "risk_level": cls["level"],
               "data": db.jdump({k: v for k, v in a.items() if k not in ("classification",)} | {"classification": cls})}
        rec["id"] = a["id"]
        if db.alert_upsert(conn, rec, now):
            new += 1
            ctx.changed = True
        track = db.track_get(conn, a["track_key"])
        reason = alerts_mod.track_needs_notify(track, a, cls, immediate_levels)
        db.track_touch(conn, a["track_key"], a["event"], cls["level"], a["id"], now)
        if reason:
            db.track_notified(conn, a["track_key"], cls["level"], cls["monitored_fips"], now)
            items.append({"type": "alert", "key": f"alert:{a['track_key']}:{a['id']}", "level": cls["level"],
                          "reason": reason, "alert": a, "states": states})
    gone = db.alerts_mark_inactive(conn, active_ids)
    if gone:
        ctx.changed = True
    conn.commit()
    ctx.immediate.extend(items)
    return {"fetched": len(feats), "relevant": relevant, "new": new, "expired": len(gone), "immediate": len(items)}


# ------------------------------------------------------------------ delivery
def deliver(ctx, nid, subject, html, text, kind):
    res = email.send(ctx.cfg, subject, html, text, kind)
    db.notification_update(ctx.conn, nid, res["status"], provider=res["provider"], error=res["error"],
                           sent_utc=ctx.now if res["status"] in ("sent", "dry-run") else None)
    ctx.log(f"[email] {kind} '{subject}' -> {res['status']}" + (f" ({res['error']})" if res["error"] else ""))
    ctx.changed = True
    return res


def send_immediate(ctx):
    from .reports import immediate
    items = ctx.immediate
    if not items:
        return None
    items.sort(key=lambda i: -level_rank(i["level"]))
    max_n = ctx.cfg.get("notify", "max_items_per_email", default=30)
    shown, overflow = items[:max_n], max(0, len(items) - max_n)
    subject, html, text = immediate.build(ctx, shown, overflow)
    dedup = "immediate:" + hashlib.sha1("|".join(sorted(i["key"] for i in items)).encode()).hexdigest()[:20]
    summary = [{"type": i["type"], "level": i["level"], "reason": i["reason"], "key": i["key"]} for i in items]
    nid = db.notification_create(ctx.conn, dedup, "immediate", subject, summary, html, text, ctx.now)
    if nid is None:
        return {"duplicate": True}
    res = deliver(ctx, nid, subject, html, text, "immediate")
    return {"items": len(items), "status": res["status"]}


def retry_pending(ctx):
    max_attempts = ctx.cfg.get("notify", "max_send_attempts", default=6)
    done = []
    for n in db.notifications_pending(ctx.conn, max_attempts):
        if n["attempts"] == 0 or n["status"] == "failed":
            if n["status"] == "pending" and n["created_utc"] == iso(ctx.now):
                continue
            res = deliver(ctx, n["id"], n["subject"], n["html"] or "", n["text"] or "", n["kind"])
            done.append((n["id"], res["status"]))
    return done


# ------------------------------------------------------------------ reports
def daily_due(cfg, conn, now):
    ct = to_ct(now)
    key = ct.date().isoformat()
    target = time(cfg.get("schedule", "daily_report_hour_ct", default=6), cfg.get("schedule", "daily_report_minute_ct", default=30))
    if ct.time() < target or db.meta_get(conn, "daily_sent_key") == key:
        return None
    return key


def weekly_due(cfg, conn, now):
    ct = to_ct(now)
    key = iso_week_key(ct.date())
    wd = cfg.get("schedule", "weekly_report_weekday", default=0)
    target = time(cfg.get("schedule", "weekly_report_hour_ct", default=7), cfg.get("schedule", "weekly_report_minute_ct", default=0))
    if ct.weekday() < wd or (ct.weekday() == wd and ct.time() < target):
        return None
    if db.meta_get(conn, "weekly_sent_key") == key:
        return None
    return key


def run_daily(ctx, stages, key):
    from .reports import content, daily
    subject, html, text, summary = daily.build(ctx, stages)
    db.report_save(ctx.conn, "daily", key, subject, html, summary, ctx.now)
    nid = db.notification_create(ctx.conn, f"daily:{key}", "daily", subject, summary.get("top", []), html, text, ctx.now)
    status = deliver(ctx, nid, subject, html, text, "daily")["status"] if nid else "duplicate"
    db.meta_set(ctx.conn, "daily_sent_key", key)
    ctx.conn.commit()
    out = {"daily": status}
    ideas = content.generate(ctx, stages)
    out["ideas_new"] = ideas
    if ctx.cfg.get("schedule", "content_email_with_daily", default=True):
        pending = db.ideas_unemailed(ctx.conn)
        if pending:
            subj, chtml, ctext = content.build_email(ctx, pending)
            cid = db.notification_create(ctx.conn, f"content:{key}", "content", subj, [i["idea_key"] for i in pending],
                                         chtml, ctext, ctx.now)
            if cid:
                out["content"] = deliver(ctx, cid, subj, chtml, ctext, "content")["status"]
            db.ideas_mark_emailed(ctx.conn, [i["id"] for i in pending], ctx.now)
    return out


def run_weekly(ctx, stages, key):
    from .reports import weekly
    subject, html, text, summary = weekly.build(ctx, stages, key)
    db.report_save(ctx.conn, "weekly", key, subject, html, summary, ctx.now)
    nid = db.notification_create(ctx.conn, f"weekly:{key}", "weekly", subject, summary.get("top", []), html, text, ctx.now)
    status = deliver(ctx, nid, subject, html, text, "weekly")["status"] if nid else "duplicate"
    db.meta_set(ctx.conn, "weekly_sent_key", key)
    ctx.conn.commit()
    return {"weekly": status}


def maintenance(ctx):
    ct = to_ct(ctx.now)
    key = ct.date().isoformat()
    if db.meta_get(ctx.conn, "maintenance_key") == key or ct.hour < ctx.cfg.get("schedule", "maintenance_hour_ct", default=3):
        return None
    r = ctx.cfg.get("retention", default={})
    res = db.prune(ctx.conn, ctx.now, r.get("snapshot_hours", 30), r.get("history_days", 120), r.get("runs_days", 30),
                   r.get("notification_html_days", 30), r.get("county_history_days", 35))
    db.meta_set(ctx.conn, "maintenance_key", key)
    ctx.conn.commit()
    ctx.conn.execute("VACUUM")
    return res


# ------------------------------------------------------------------ tick
def tick(cfg, now=None, force=(), log=print, build_dashboard=True):
    now = now or utcnow()
    net.HEALTH.reset()
    conn = db.connect(cfg.db_path)
    ctx = Ctx(cfg, conn, now, log)
    run_id = db.run_start(conn, "tick", now)
    force = set(force or ())
    stages = compute_stages(cfg, conn, now)
    ctx.summary["alerts"] = step(ctx, "alerts", poll_alerts, ctx, stages)

    cycle_hours = cfg.get("schedule", "forecast_cycle_hours", default=3)
    early = cfg.get("schedule", "forecast_cycle_early_minutes", default=15) / 60.0
    last_cycle = db.meta_get_time(conn, "last_cycle_utc")
    cycle_due = "cycle" in force or last_cycle is None or hours_between(last_cycle, now) >= cycle_hours - early
    daily_key = daily_due(cfg, conn, now) if "daily" not in force else to_ct(now).date().isoformat()
    weekly_key = weekly_due(cfg, conn, now) if "weekly" not in force else iso_week_key(to_ct(now).date())
    if (daily_key or weekly_key) and last_cycle and hours_between(last_cycle, now) > 4:
        cycle_due = True
    if cycle_due:
        ctx.summary["nass"] = step(ctx, "nass", refresh_nass, ctx, "cycle" in force)
        ctx.summary["drought"] = step(ctx, "drought", refresh_drought, ctx)
        ctx.summary["observed"] = step(ctx, "observed", refresh_observed, ctx)
        stages = compute_stages(cfg, conn, now)
        ctx.summary["cycle"] = step(ctx, "cycle", run_cycle, ctx, stages)
    ctx.summary["storm_reports"] = step(ctx, "storm_reports", refresh_storm_reports, ctx)
    ctx.summary["immediate"] = step(ctx, "immediate", send_immediate, ctx)
    if daily_key:
        ctx.summary["daily"] = step(ctx, "daily", run_daily, ctx, stages, daily_key)
    if weekly_key:
        ctx.summary["weekly"] = step(ctx, "weekly", run_weekly, ctx, stages, weekly_key)
    ctx.summary["retries"] = step(ctx, "retries", retry_pending, ctx)
    ctx.summary["maintenance"] = step(ctx, "maintenance", maintenance, ctx)
    db.save_health(conn, net.HEALTH.snapshot(), ctx.product_times)
    last_build = db.meta_get_time(conn, "dashboard_built_utc")
    if build_dashboard and (ctx.changed or "dashboard" in force or last_build is None or hours_between(last_build, now) >= 1):
        from .dashboard import build as dash
        ctx.summary["dashboard"] = step(ctx, "dashboard", dash.build, ctx, stages)
        db.meta_set(conn, "dashboard_built_utc", iso(now))
    db.meta_set(conn, "last_tick_utc", iso(now))
    hb = cfg.get("schedule", "heartbeat_commit_minutes", default=60) / 60.0
    last_persist = db.meta_get_time(conn, "last_persist_utc")
    persist = bool(ctx.changed or ctx.summary.get("cycle") or ctx.summary.get("immediate") or daily_key or weekly_key
                   or last_persist is None or hours_between(last_persist, now) >= hb)
    if persist:
        db.meta_set(conn, "last_persist_utc", iso(now))
    last_keep = db.meta_get_time(conn, "keepalive_utc")
    keepalive = last_keep is None or hours_between(last_keep, now) >= 24 * 30
    if keepalive:
        db.meta_set(conn, "keepalive_utc", iso(now))
    ctx.summary["_outputs"] = {"persist": persist, "dashboard": bool(ctx.summary.get("dashboard")), "keepalive": keepalive}
    status = "ok" if not ctx.errors else ("partial" if len(ctx.errors) < 4 else "error")
    db.run_finish(conn, run_id, status, ctx.summary, "\n".join(ctx.errors) or None, utcnow())
    conn.commit()
    conn.close()
    return status, ctx.summary, ctx.errors