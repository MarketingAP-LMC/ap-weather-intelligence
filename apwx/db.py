"""SQLite persistence: forecast history, events, alerts, notifications, reports.

The database is a single file (journal_mode=DELETE) so it can be stored on a
git branch by the scheduled cloud job and restored on the next run.
"""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .timeutil import iso, parse_iso

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_utc TEXT NOT NULL, finished_utc TEXT,
  task TEXT NOT NULL, status TEXT NOT NULL, summary TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS source_status (
  source TEXT PRIMARY KEY, last_attempt_utc TEXT, last_success_utc TEXT, last_error TEXT,
  last_error_utc TEXT, last_latency_ms REAL, ok_count INTEGER DEFAULT 0, err_count INTEGER DEFAULT 0,
  product_time TEXT);
CREATE TABLE IF NOT EXISTS cycles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_utc TEXT NOT NULL UNIQUE,
  counties_ok INTEGER, counties_failed INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS county_snapshots (
  cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE, fips TEXT NOT NULL,
  data TEXT NOT NULL, PRIMARY KEY (cycle_id, fips));
CREATE TABLE IF NOT EXISTS latest_hourly (fips TEXT PRIMARY KEY, cycle_id INTEGER, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS county_history (
  cycle_utc TEXT NOT NULL, fips TEXT NOT NULL, tmin72 REAL, tmin7d REAL, tmax7d REAL, qpf72 REAL, qpf7d REAL,
  snow72 REAL, gust72 REAL, PRIMARY KEY (cycle_utc, fips));
CREATE TABLE IF NOT EXISTS nws_alerts (
  id TEXT PRIMARY KEY, track_key TEXT, event TEXT, severity TEXT, certainty TEXT, urgency TEXT,
  message_type TEXT, sent_utc TEXT, onset_utc TEXT, ends_utc TEXT, expires_utc TEXT, headline TEXT,
  area_desc TEXT, states TEXT, fips TEXT, monitored_fips TEXT, url TEXT, risk_level TEXT,
  data TEXT, first_seen_utc TEXT, last_seen_utc TEXT, active INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_alerts_track ON nws_alerts(track_key);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON nws_alerts(active);
CREATE TABLE IF NOT EXISTS events (
  event_key TEXT PRIMARY KEY, hazard TEXT, state TEXT, title TEXT, risk_level TEXT, status TEXT,
  first_detected_utc TEXT, last_updated_utc TEXT, window_start_utc TEXT, window_end_utc TEXT,
  fingerprint TEXT, last_notified_utc TEXT, last_notified_level TEXT, last_notified_fingerprint TEXT,
  last_notified_data TEXT, missed_cycles INTEGER DEFAULT 0, data TEXT);
CREATE TABLE IF NOT EXISTS event_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT, cycle_id INTEGER, recorded_utc TEXT,
  risk_level TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS idx_event_history_key ON event_history(event_key);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT UNIQUE, kind TEXT, subject TEXT,
  created_utc TEXT, sent_utc TEXT, status TEXT, attempts INTEGER DEFAULT 0, provider TEXT,
  error TEXT, items TEXT, html TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, period_key TEXT, created_utc TEXT,
  subject TEXT, html TEXT, summary TEXT, UNIQUE(kind, period_key));
CREATE TABLE IF NOT EXISTS content_ideas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea_key TEXT UNIQUE, created_utc TEXT, event_key TEXT,
  headline TEXT, crop TEXT, region TEXT, format TEXT, data TEXT, emailed_utc TEXT);
CREATE TABLE IF NOT EXISTS crop_progress (
  released TEXT, week_ending TEXT, state TEXT, crop TEXT, metric TEXT,
  this_week REAL, last_week REAL, last_year REAL, avg_5yr REAL, source_url TEXT,
  PRIMARY KEY (week_ending, state, crop, metric));
CREATE TABLE IF NOT EXISTS drought (
  map_date TEXT, fips TEXT, none REAL, d0 REAL, d1 REAL, d2 REAL, d3 REAL, d4 REAL,
  PRIMARY KEY (map_date, fips));
CREATE TABLE IF NOT EXISTS observed (
  obs_date TEXT, fips TEXT, pcpn REAL, maxt REAL, mint REAL, PRIMARY KEY (obs_date, fips));
CREATE TABLE IF NOT EXISTS station_obs (station TEXT PRIMARY KEY, fips TEXT, obs_utc TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS alert_tracks (
  track_key TEXT PRIMARY KEY, event TEXT, first_seen_utc TEXT, last_seen_utc TEXT, last_level TEXT,
  last_notified_utc TEXT, last_notified_level TEXT, last_notified_fips TEXT, latest_alert_id TEXT);
CREATE TABLE IF NOT EXISTS storm_reports (
  id TEXT PRIMARY KEY, day TEXT, kind TEXT, time_hhmm TEXT, magnitude TEXT, location TEXT, county TEXT,
  state TEXT, fips TEXT, lat REAL, lon REAL, comments TEXT, first_seen_utc TEXT);
"""


def jdump(obj):
    return json.dumps(obj, separators=(",", ":"), default=str)


def jload(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    if meta_get(conn, "schema_version") is None:
        meta_set(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()
    return conn


# ---------------------------------------------------------------- meta
def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, None if value is None else str(value)))


def meta_get_time(conn, key):
    return parse_iso(meta_get(conn, key))


# ---------------------------------------------------------------- runs
def run_start(conn, task, now):
    cur = conn.execute("INSERT INTO runs(started_utc, task, status) VALUES(?, ?, 'running')", (iso(now), task))
    conn.commit()
    return cur.lastrowid


def run_finish(conn, run_id, status, summary=None, error=None, now=None):
    conn.execute("UPDATE runs SET finished_utc=?, status=?, summary=?, error=? WHERE id=?",
                 (iso(now), status, jdump(summary) if summary is not None else None, error, run_id))
    conn.commit()


def runs_recent(conn, limit=50):
    return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- source health
def save_health(conn, snapshot, product_times=None):
    product_times = product_times or {}
    for source, s in snapshot.items():
        row = conn.execute("SELECT * FROM source_status WHERE source=?", (source,)).fetchone()
        ok_count = (row["ok_count"] if row else 0) + s.get("ok", 0)
        err_count = (row["err_count"] if row else 0) + s.get("err", 0)
        last_success = s.get("last_success") or (row["last_success_utc"] if row else None)
        last_error = s.get("last_error") if s.get("err") else (row["last_error"] if row else None)
        last_error_utc = s.get("last_error_time") if s.get("err") else (row["last_error_utc"] if row else None)
        latency = s.get("latency_ms") if s.get("latency_ms") is not None else (row["last_latency_ms"] if row else None)
        ptime = product_times.get(source) or (row["product_time"] if row else None)
        conn.execute(
            "INSERT INTO source_status(source, last_attempt_utc, last_success_utc, last_error, last_error_utc,"
            " last_latency_ms, ok_count, err_count, product_time) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(source) DO UPDATE SET last_attempt_utc=excluded.last_attempt_utc,"
            " last_success_utc=excluded.last_success_utc, last_error=excluded.last_error,"
            " last_error_utc=excluded.last_error_utc, last_latency_ms=excluded.last_latency_ms,"
            " ok_count=excluded.ok_count, err_count=excluded.err_count, product_time=excluded.product_time",
            (source, s.get("last_attempt"), last_success, last_error, last_error_utc, latency,
             ok_count, err_count, ptime))
    for source, ptime in product_times.items():
        if source not in snapshot:
            conn.execute("UPDATE source_status SET product_time=? WHERE source=?", (ptime, source))
    conn.commit()


def source_status(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM source_status ORDER BY source")]


# ---------------------------------------------------------------- cycles / snapshots
def cycle_create(conn, now):
    cur = conn.execute("INSERT INTO cycles(cycle_utc) VALUES(?)", (iso(now),))
    return cur.lastrowid


def cycle_finish(conn, cycle_id, ok, failed, notes=None):
    conn.execute("UPDATE cycles SET counties_ok=?, counties_failed=?, notes=? WHERE id=?",
                 (ok, failed, jdump(notes) if notes is not None else None, cycle_id))
    conn.commit()


def snapshot_save(conn, cycle_id, fips, data):
    conn.execute("INSERT OR REPLACE INTO county_snapshots(cycle_id, fips, data) VALUES(?, ?, ?)",
                 (cycle_id, fips, jdump(data)))


def cycles(conn, limit=200):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM cycles WHERE counties_ok IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,))]


def latest_cycle(conn):
    row = conn.execute("SELECT * FROM cycles WHERE counties_ok IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def previous_cycle(conn, before_id):
    row = conn.execute("SELECT * FROM cycles WHERE id < ? AND counties_ok IS NOT NULL ORDER BY id DESC LIMIT 1",
                       (before_id,)).fetchone()
    return dict(row) if row else None


def cycle_at_or_before(conn, when):
    row = conn.execute("SELECT * FROM cycles WHERE cycle_utc <= ? AND counties_ok IS NOT NULL"
                       " ORDER BY cycle_utc DESC LIMIT 1", (iso(when),)).fetchone()
    return dict(row) if row else None


def snapshots_for_cycle(conn, cycle_id):
    return {r["fips"]: jload(r["data"], {}) for r in conn.execute(
        "SELECT fips, data FROM county_snapshots WHERE cycle_id=?", (cycle_id,))}


def snapshot_series(conn, fips, since):
    rows = conn.execute(
        "SELECT c.cycle_utc, s.data FROM county_snapshots s JOIN cycles c ON c.id=s.cycle_id"
        " WHERE s.fips=? AND c.cycle_utc>=? ORDER BY c.cycle_utc", (fips, iso(since)))
    return [(r["cycle_utc"], jload(r["data"], {})) for r in rows]


def hourly_save(conn, fips, cycle_id, data):
    conn.execute("INSERT OR REPLACE INTO latest_hourly(fips, cycle_id, data) VALUES(?, ?, ?)",
                 (fips, cycle_id, jdump(data)))


HISTORY_KEYS = ("tmin72", "tmin7d", "tmax7d", "qpf72", "qpf7d", "snow72", "gust72")


def history_save(conn, cycle_utc, fips, fc):
    conn.execute(f"INSERT OR REPLACE INTO county_history(cycle_utc, fips, {', '.join(HISTORY_KEYS)})"
                 f" VALUES(?, ?, {', '.join('?' for _ in HISTORY_KEYS)})",
                 [cycle_utc, fips] + [fc.get(k) for k in HISTORY_KEYS])


def history_since(conn, since):
    return [dict(r) for r in conn.execute("SELECT * FROM county_history WHERE cycle_utc>=? ORDER BY cycle_utc", (iso(since),))]


def hourly_all(conn):
    return {r["fips"]: jload(r["data"], {}) for r in conn.execute("SELECT fips, data FROM latest_hourly")}


# ---------------------------------------------------------------- NWS alerts
def alert_get(conn, alert_id):
    row = conn.execute("SELECT * FROM nws_alerts WHERE id=?", (alert_id,)).fetchone()
    return dict(row) if row else None


def alerts_by_track(conn, track_key):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM nws_alerts WHERE track_key=? ORDER BY sent_utc", (track_key,))]


def alert_upsert(conn, rec, now):
    """Insert or refresh an alert. Returns True if it was not seen before."""
    existing = alert_get(conn, rec["id"])
    cols = ["track_key", "event", "severity", "certainty", "urgency", "message_type", "sent_utc",
            "onset_utc", "ends_utc", "expires_utc", "headline", "area_desc", "states", "fips",
            "monitored_fips", "url", "risk_level", "data"]
    values = [rec.get(c) for c in cols]
    if existing:
        sets = ", ".join(f"{c}=?" for c in cols)
        conn.execute(f"UPDATE nws_alerts SET {sets}, last_seen_utc=?, active=1 WHERE id=?",
                     values + [iso(now), rec["id"]])
        return False
    conn.execute(
        f"INSERT INTO nws_alerts(id, {', '.join(cols)}, first_seen_utc, last_seen_utc, active)"
        f" VALUES(?, {', '.join('?' for _ in cols)}, ?, ?, 1)",
        [rec["id"]] + values + [iso(now), iso(now)])
    return True


def alerts_mark_inactive(conn, active_ids):
    active_ids = set(active_ids)
    rows = conn.execute("SELECT id FROM nws_alerts WHERE active=1").fetchall()
    gone = [r["id"] for r in rows if r["id"] not in active_ids]
    for aid in gone:
        conn.execute("UPDATE nws_alerts SET active=0 WHERE id=?", (aid,))
    return gone


def alerts_active(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM nws_alerts WHERE active=1 ORDER BY sent_utc DESC")]


def alerts_since(conn, since):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM nws_alerts WHERE first_seen_utc>=? ORDER BY first_seen_utc DESC", (iso(since),))]


# ---------------------------------------------------------------- events
EVENT_COLS = ["hazard", "state", "title", "risk_level", "status", "first_detected_utc", "last_updated_utc",
              "window_start_utc", "window_end_utc", "fingerprint", "last_notified_utc", "last_notified_level",
              "last_notified_fingerprint", "last_notified_data", "missed_cycles", "data"]


def event_get(conn, key):
    row = conn.execute("SELECT * FROM events WHERE event_key=?", (key,)).fetchone()
    return dict(row) if row else None


def event_save(conn, ev):
    values = [ev.get(c) for c in EVENT_COLS]
    conn.execute(
        f"INSERT INTO events(event_key, {', '.join(EVENT_COLS)}) VALUES(?, {', '.join('?' for _ in EVENT_COLS)})"
        f" ON CONFLICT(event_key) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in EVENT_COLS)}",
        [ev["event_key"]] + values)


def events_with_status(conn, status="active"):
    return [dict(r) for r in conn.execute("SELECT * FROM events WHERE status=? ORDER BY last_updated_utc DESC",
                                          (status,))]


def events_since(conn, since):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE last_updated_utc>=? ORDER BY last_updated_utc DESC", (iso(since),))]


def event_history_add(conn, key, cycle_id, now, level, data):
    conn.execute("INSERT INTO event_history(event_key, cycle_id, recorded_utc, risk_level, data) VALUES(?,?,?,?,?)",
                 (key, cycle_id, iso(now), level, jdump(data)))


def event_history(conn, key, limit=60):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM event_history WHERE event_key=? ORDER BY id DESC LIMIT ?", (key, limit))]


# ---------------------------------------------------------------- notifications
def notification_get(conn, dedup_key):
    row = conn.execute("SELECT * FROM notifications WHERE dedup_key=?", (dedup_key,)).fetchone()
    return dict(row) if row else None


def notification_create(conn, dedup_key, kind, subject, items, html, text, now):
    if notification_get(conn, dedup_key):
        return None
    cur = conn.execute(
        "INSERT INTO notifications(dedup_key, kind, subject, created_utc, status, attempts, items, html, text)"
        " VALUES(?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
        (dedup_key, kind, subject, iso(now), jdump(items), html, text))
    conn.commit()
    return cur.lastrowid


def notification_update(conn, nid, status, provider=None, error=None, sent_utc=None, bump_attempt=True):
    conn.execute(
        "UPDATE notifications SET status=?, provider=COALESCE(?, provider), error=?, sent_utc=COALESCE(?, sent_utc),"
        " attempts=attempts + ? WHERE id=?",
        (status, provider, error, iso(sent_utc) if sent_utc else None, 1 if bump_attempt else 0, nid))
    conn.commit()


def notifications_pending(conn, max_attempts=6):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM notifications WHERE status IN ('pending','failed') AND attempts < ? ORDER BY id",
        (max_attempts,))]


def notifications_recent(conn, limit=200, kinds=None):
    if kinds:
        q = "SELECT id, dedup_key, kind, subject, created_utc, sent_utc, status, attempts, provider, error, items" \
            f" FROM notifications WHERE kind IN ({','.join('?' for _ in kinds)}) ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in conn.execute(q, list(kinds) + [limit])]
    return [dict(r) for r in conn.execute(
        "SELECT id, dedup_key, kind, subject, created_utc, sent_utc, status, attempts, provider, error, items"
        " FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]


def notification_html(conn, nid):
    row = conn.execute("SELECT html FROM notifications WHERE id=?", (nid,)).fetchone()
    return row["html"] if row else None


# ---------------------------------------------------------------- reports
def report_save(conn, kind, period_key, subject, html, summary, now):
    conn.execute(
        "INSERT INTO reports(kind, period_key, created_utc, subject, html, summary) VALUES(?,?,?,?,?,?)"
        " ON CONFLICT(kind, period_key) DO UPDATE SET created_utc=excluded.created_utc, subject=excluded.subject,"
        " html=excluded.html, summary=excluded.summary",
        (kind, period_key, iso(now), subject, html, jdump(summary)))
    conn.commit()


def report_get(conn, kind, period_key):
    row = conn.execute("SELECT * FROM reports WHERE kind=? AND period_key=?", (kind, period_key)).fetchone()
    return dict(row) if row else None


def reports_recent(conn, kind=None, limit=60):
    if kind:
        rows = conn.execute("SELECT id, kind, period_key, created_utc, subject, summary FROM reports"
                            " WHERE kind=? ORDER BY period_key DESC LIMIT ?", (kind, limit))
    else:
        rows = conn.execute("SELECT id, kind, period_key, created_utc, subject, summary FROM reports"
                            " ORDER BY created_utc DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def report_previous(conn, kind, before_period):
    row = conn.execute("SELECT * FROM reports WHERE kind=? AND period_key<? ORDER BY period_key DESC LIMIT 1",
                       (kind, before_period)).fetchone()
    return dict(row) if row else None


def report_html(conn, report_id):
    row = conn.execute("SELECT html FROM reports WHERE id=?", (report_id,)).fetchone()
    return row["html"] if row else None


# ---------------------------------------------------------------- content ideas
def idea_save(conn, idea, now):
    if conn.execute("SELECT 1 FROM content_ideas WHERE idea_key=?", (idea["idea_key"],)).fetchone():
        return False
    conn.execute(
        "INSERT INTO content_ideas(idea_key, created_utc, event_key, headline, crop, region, format, data)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (idea["idea_key"], iso(now), idea.get("event_key"), idea["headline"], idea.get("crop"),
         idea.get("region"), idea.get("format"), jdump(idea)))
    return True


def ideas_unemailed(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM content_ideas WHERE emailed_utc IS NULL ORDER BY id")]


def ideas_mark_emailed(conn, ids, now):
    for i in ids:
        conn.execute("UPDATE content_ideas SET emailed_utc=? WHERE id=?", (iso(now), i))
    conn.commit()


def ideas_recent(conn, limit=100):
    return [dict(r) for r in conn.execute("SELECT * FROM content_ideas ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- crop progress / drought / observed
def progress_save(conn, rows):
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO crop_progress(released, week_ending, state, crop, metric, this_week, last_week,"
            " last_year, avg_5yr, source_url) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (r["released"], r["week_ending"], r["state"], r["crop"], r["metric"], r.get("this_week"),
             r.get("last_week"), r.get("last_year"), r.get("avg_5yr"), r.get("source_url")))
    conn.commit()


def progress_latest(conn):
    """Latest week's rows per (state, crop): {(state, crop): {"week_ending":..., metrics...}}."""
    out = {}
    rows = conn.execute(
        "SELECT p.* FROM crop_progress p JOIN (SELECT state, crop, MAX(week_ending) AS we FROM crop_progress"
        " GROUP BY state, crop) m ON p.state=m.state AND p.crop=m.crop AND p.week_ending=m.we")
    for r in rows:
        d = out.setdefault((r["state"], r["crop"]), {"week_ending": r["week_ending"], "released": r["released"],
                                                      "source_url": r["source_url"], "metrics": {}})
        d["metrics"][r["metric"]] = {"this_week": r["this_week"], "last_week": r["last_week"],
                                     "last_year": r["last_year"], "avg_5yr": r["avg_5yr"]}
    return out


def drought_save(conn, rows):
    for r in rows:
        conn.execute("INSERT OR REPLACE INTO drought(map_date, fips, none, d0, d1, d2, d3, d4) VALUES(?,?,?,?,?,?,?,?)",
                     (r["map_date"], r["fips"], r["none"], r["d0"], r["d1"], r["d2"], r["d3"], r["d4"]))
    conn.commit()


def drought_dates(conn, limit=10):
    return [r["map_date"] for r in conn.execute(
        "SELECT DISTINCT map_date FROM drought ORDER BY map_date DESC LIMIT ?", (limit,))]


def drought_for_date(conn, map_date):
    return {r["fips"]: dict(r) for r in conn.execute("SELECT * FROM drought WHERE map_date=?", (map_date,))}


def observed_save(conn, rows):
    for r in rows:
        conn.execute("INSERT OR REPLACE INTO observed(obs_date, fips, pcpn, maxt, mint) VALUES(?,?,?,?,?)",
                     (r["obs_date"], r["fips"], r.get("pcpn"), r.get("maxt"), r.get("mint")))
    conn.commit()


def observed_range(conn, start_date, end_date):
    out = {}
    for r in conn.execute("SELECT * FROM observed WHERE obs_date>=? AND obs_date<=? ORDER BY obs_date",
                          (start_date, end_date)):
        out.setdefault(r["fips"], []).append(dict(r))
    return out


def observed_last_date(conn):
    row = conn.execute("SELECT MAX(obs_date) AS d FROM observed").fetchone()
    return row["d"] if row else None


def station_obs_save(conn, station, fips, obs_utc, data):
    conn.execute("INSERT OR REPLACE INTO station_obs(station, fips, obs_utc, data) VALUES(?,?,?,?)",
                 (station, fips, obs_utc, jdump(data)))


def station_obs_all(conn):
    return {r["station"]: {"fips": r["fips"], "obs_utc": r["obs_utc"], **(jload(r["data"], {}) or {})}
            for r in conn.execute("SELECT * FROM station_obs")}


# ---------------------------------------------------------------- alert tracks / storm reports
def track_get(conn, track_key):
    row = conn.execute("SELECT * FROM alert_tracks WHERE track_key=?", (track_key,)).fetchone()
    return dict(row) if row else None


def track_touch(conn, track_key, event, level, alert_id, now):
    row = track_get(conn, track_key)
    if row:
        conn.execute("UPDATE alert_tracks SET last_seen_utc=?, last_level=?, latest_alert_id=? WHERE track_key=?",
                     (iso(now), level, alert_id, track_key))
    else:
        conn.execute("INSERT INTO alert_tracks(track_key, event, first_seen_utc, last_seen_utc, last_level,"
                     " latest_alert_id) VALUES(?,?,?,?,?,?)", (track_key, event, iso(now), iso(now), level, alert_id))


def track_notified(conn, track_key, level, fips, now):
    conn.execute("UPDATE alert_tracks SET last_notified_utc=?, last_notified_level=?, last_notified_fips=?"
                 " WHERE track_key=?", (iso(now), level, ",".join(sorted(fips)), track_key))


def storm_reports_save(conn, reports, now):
    new = 0
    for r in reports:
        if conn.execute("SELECT 1 FROM storm_reports WHERE id=?", (r["id"],)).fetchone():
            continue
        conn.execute("INSERT INTO storm_reports(id, day, kind, time_hhmm, magnitude, location, county, state, fips,"
                     " lat, lon, comments, first_seen_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r["id"], r["day"], r["kind"], r["time_hhmm"], r["magnitude"], r["location"], r["county"],
                      r["state"], r.get("fips"), r["lat"], r["lon"], r.get("comments", "")[:500], iso(now)))
        new += 1
    conn.commit()
    return new


def storm_reports_since(conn, since):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM storm_reports WHERE first_seen_utc>=? ORDER BY first_seen_utc DESC", (iso(since),))]


# ---------------------------------------------------------------- retention
def prune(conn, now, snapshot_hours=30, history_days=120, runs_days=30, notif_html_days=30, county_history_days=35):
    cutoff = iso(now - timedelta(hours=snapshot_hours))
    conn.execute("DELETE FROM county_history WHERE cycle_utc < ?", (iso(now - timedelta(days=county_history_days)),))
    old_cycles = [r["id"] for r in conn.execute("SELECT id FROM cycles WHERE cycle_utc < ?", (cutoff,))]
    for cid in old_cycles:
        conn.execute("DELETE FROM county_snapshots WHERE cycle_id=?", (cid,))
    conn.execute("DELETE FROM event_history WHERE recorded_utc < ?", (iso(now - timedelta(days=history_days)),))
    conn.execute("DELETE FROM runs WHERE started_utc < ?", (iso(now - timedelta(days=runs_days)),))
    conn.execute("UPDATE notifications SET html=NULL, text=NULL WHERE created_utc < ? AND status='sent'",
                 (iso(now - timedelta(days=notif_html_days)),))
    conn.execute("DELETE FROM observed WHERE obs_date < ?", ((now - timedelta(days=120)).strftime("%Y-%m-%d"),))
    conn.execute("DELETE FROM drought WHERE map_date < ?", ((now - timedelta(days=400)).strftime("%Y-%m-%d"),))
    conn.commit()
    return {"cycles_pruned": len(old_cycles)}