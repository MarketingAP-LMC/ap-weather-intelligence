"""Command line entry point:  python -m apwx <command> [options]

Commands
  tick         poll official alerts and run every job that is due (what the scheduler runs)
  status       show configuration, email setup (no secrets), last runs and data-source health
  test-email   send a clearly labelled delivery test to ALERT_RECIPIENT
  simulate     generate sample alerts/reports from synthetic data (clearly labelled SIMULATION)
  selftest     quick live connectivity check of every data source (no database writes)
  dashboard    rebuild the dashboard files from the database
"""
import argparse
import json
import os
import sys

from . import db
from . import http as net
from .config import Config
from .notify import email
from .timeutil import fmt_ct, utcnow


def cmd_tick(cfg, args):
    from .pipeline import tick
    force = [f.strip() for f in (args.force or "").split(",") if f.strip()]
    status, summary, errors = tick(cfg, force=force, build_dashboard=not args.no_dashboard)
    print(json.dumps({"status": status, "summary": summary, "errors": errors}, indent=1, default=str))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        o = summary.get("_outputs", {})
        with open(out, "a", encoding="utf-8") as fh:
            for k in ("persist", "dashboard", "keepalive"):
                fh.write(f"{k}={'true' if o.get(k) else 'false'}\n")
    return 0 if status in ("ok", "partial") else 1


def cmd_status(cfg, args):
    print(f"Platform: {cfg.get('platform_name')}  counties={len(cfg.counties)}  states={','.join(cfg.monitored_states)}")
    print(f"Database: {cfg.db_path}")
    print(f"Email: {json.dumps(email.describe())}")
    if not cfg.db_path.exists():
        print("Database not created yet - run `python -m apwx tick` first.")
        return 0
    conn = db.connect(cfg.db_path)
    for k in ("last_tick_utc", "last_cycle_utc", "daily_sent_key", "weekly_sent_key", "nass_latest"):
        print(f"  {k}: {db.meta_get(conn, k)}")
    print("Recent runs:")
    for r in db.runs_recent(conn, 8):
        print(f"  {r['started_utc']} {r['task']} {r['status']} {(r['error'] or '')[:120]}")
    print("Sources:")
    for s in db.source_status(conn):
        print(f"  {s['source']:<20} ok={s['ok_count']:<5} err={s['err_count']:<4} last_success={s['last_success_utc']} "
              f"product={s['product_time'] or '-'} last_error={(s['last_error'] or '')[:80]}")
    return 0


def cmd_test_email(cfg, args):
    from .reports import render as R
    now = utcnow()
    body = (R.p("This is a delivery test from the American Prime agricultural weather intelligence platform. "
                "If you received it, email delivery is working.") +
            R.kv([("Sent", R.esc(fmt_ct(now))), ("Provider", R.esc(email.describe()["provider"] or "-")),
                  ("Monitored counties", str(len(cfg.counties)))]))
    html = R.page(cfg, "Email delivery test", f"Test - {fmt_ct(now)}", body, top_banner=R.banner("TEST MESSAGE - NOT A WEATHER ALERT", "dry"))
    subject = f"{cfg.get('notify', 'subject_prefix', default='[AP Weather]')} TEST - email delivery check {fmt_ct(now)}"
    res = email.send(cfg, subject, html, R.to_text(html), "test")
    print(json.dumps(res, indent=1))
    if cfg.db_path.parent.exists():
        conn = db.connect(cfg.db_path)
        nid = db.notification_create(conn, f"test:{now.isoformat()}", "test", subject, [], html, R.to_text(html), now)
        if nid:
            db.notification_update(conn, nid, res["status"], provider=res["provider"], error=res["error"],
                                   sent_utc=now if res["status"] == "sent" else None)
    return 0 if res["status"] == "sent" else 2


def cmd_selftest(cfg, args):
    from datetime import timedelta
    from .sources import acis, cpc, nass, nws, spc, usdm, wpc
    from .timeutil import to_ct
    results = {}

    def check(name, fn):
        try:
            out = fn()
            results[name] = {"ok": True, "detail": out}
        except Exception as e:  # noqa: BLE001
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    c = next(iter(cfg.counties.values()))
    today = to_ct(utcnow()).date()
    check("nws_alerts", lambda: f"{len(nws.fetch_active_alerts(cfg.monitored_states))} active alerts in 12 states")
    check("nws_forecast", lambda: f"gridpoint updated {nws.fetch_gridpoint(c['nws']['grid'], c['nws']['x'], c['nws']['y']).get('updateTime')}")
    check("spc_outlooks", lambda: f"day1 issued {spc.fetch_outlooks()['d1_cat'].get('issue')}")
    check("wpc", lambda: f"7-day QPF issued {wpc.fetch_products()['qpf7d'].get('issue')}")
    check("cpc", lambda: f"8-14 day issued {cpc.fetch_products()['t814'].get('issue')}")
    check("usdm", lambda: f"{len(usdm.fetch_state('OK', today - timedelta(days=14), today))} OK county rows")
    check("acis", lambda: f"{len(acis.fetch_state_daily('OK', '40', today - timedelta(days=3), today - timedelta(days=1)))} OK county-days")
    check("nass", lambda: (lambda p: f"released {p['released']} rows={len(p['rows'])}" if p else "no report found")(nass.fetch_latest(today)))
    check("spc_reports", lambda: f"{len(spc.fetch_storm_reports('yesterday'))} reports yesterday")
    print(json.dumps(results, indent=1))
    return 0 if all(r["ok"] for r in results.values()) else 1


def cmd_dashboard(cfg, args):
    from .dashboard import build as dash
    from .pipeline import Ctx, compute_stages
    conn = db.connect(cfg.db_path)
    now = utcnow()
    ctx = Ctx(cfg, conn, now)
    print(json.dumps(dash.build(ctx, compute_stages(cfg, conn, now)), indent=1))
    return 0


def cmd_simulate(cfg, args):
    from . import simulate
    out = simulate.run(cfg, args.scenario, args.out)
    print(json.dumps(out, indent=1, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="apwx", description="American Prime agricultural weather intelligence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--force", help="comma list: cycle,daily,weekly,dashboard")
    t.add_argument("--no-dashboard", action="store_true")
    sub.add_parser("status")
    sub.add_parser("test-email")
    sub.add_parser("selftest")
    sub.add_parser("dashboard")
    s = sub.add_parser("simulate")
    s.add_argument("--scenario", default="all", help="sugarcane_freeze | spring_storms | all")
    s.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    cfg = Config()
    net.HEALTH.reset()
    return {"tick": cmd_tick, "status": cmd_status, "test-email": cmd_test_email, "selftest": cmd_selftest,
            "dashboard": cmd_dashboard, "simulate": cmd_simulate}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())