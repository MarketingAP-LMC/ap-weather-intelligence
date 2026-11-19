"""End-to-end offline test: synthetic scenario through the whole engine (no network)."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apwx import db, simulate  # noqa: E402
from apwx.config import Config  # noqa: E402
from apwx.pipeline import daily_due, weekly_due  # noqa: E402
from apwx.timeutil import UTC  # noqa: E402


class ScheduleTests(unittest.TestCase):
    def test_daily_and_weekly_due(self):
        cfg = Config()
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(Path(td) / "s.sqlite")
            early = datetime(2026, 11, 19, 11, 0, tzinfo=UTC)   # 5:00 AM CST
            late = datetime(2026, 11, 19, 13, 0, tzinfo=UTC)    # 7:00 AM CST
            self.assertIsNone(daily_due(cfg, conn, early))
            self.assertEqual(daily_due(cfg, conn, late), "2026-11-19")
            db.meta_set(conn, "daily_sent_key", "2026-11-19")
            self.assertIsNone(daily_due(cfg, conn, late))
            self.assertEqual(daily_due(cfg, conn, late + timedelta(days=1)), "2026-11-20")
            mon = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)    # Monday 8 AM CST
            self.assertEqual(weekly_due(cfg, conn, mon), "2026-W48")
            db.meta_set(conn, "weekly_sent_key", "2026-W48")
            self.assertIsNone(weekly_due(cfg, conn, mon + timedelta(days=2)))
            conn.close()


class SimulationEndToEnd(unittest.TestCase):
    def test_sugarcane_freeze_scenario(self):
        cfg = Config()
        with tempfile.TemporaryDirectory() as td:
            out = simulate.run(cfg, "sugarcane_freeze", td)["sugarcane_freeze"]
            levels = out["cycles"][-1]["levels"]
            self.assertIn("CRITICAL", levels)
            emails = sorted(Path(td, "emails").glob("*.html"))
            self.assertGreaterEqual(len(emails), 4)   # 2 immediate + daily + weekly (+ content)
            subjects = [p.with_suffix(".txt").read_text(encoding="utf-8").splitlines()[0] for p in emails]
            self.assertTrue(all("SIMULATION" in s for s in subjects), subjects)
            html = emails[0].read_text(encoding="utf-8")
            self.assertIn("SIMULATION - TEST DATA", html)
            latest = json.loads(Path(td, "dashboard", "data", "latest.json").read_text(encoding="utf-8"))
            self.assertTrue(latest["simulation"])
            la = [s for s in latest["states"] if s["abbr"] == "LA"][0]
            self.assertEqual(la["level"], "CRITICAL")
            conn = db.connect(Path(td) / "simulation.sqlite")
            imm = [n for n in db.notifications_recent(conn, 50) if n["kind"] == "immediate"]
            self.assertEqual(len(imm), 2)  # first HIGH notice, then upgrade to CRITICAL - no duplicates
            conn.close()


if __name__ == "__main__":
    unittest.main()