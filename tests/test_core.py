"""Unit tests (standard library unittest; no network)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apwx import db, geo  # noqa: E402
from apwx.analysis import alerts as alerts_mod  # noqa: E402
from apwx.analysis import assess, metrics, stages  # noqa: E402
from apwx.analysis import events as events_mod  # noqa: E402
from apwx.config import Config, max_level, shift_level  # noqa: E402
from apwx.notify import email  # noqa: E402
from apwx.simulate import alert_feature, synth_props  # noqa: E402
from apwx.sources import nass, nws  # noqa: E402
from apwx.timeutil import UTC, fmt_ct, iso_week_key, parse_valid_time, to_ct, us_dst_window_utc  # noqa: E402

CFG = Config()


class TimeTests(unittest.TestCase):
    def test_dst_window_2026(self):
        s, e = us_dst_window_utc(2026, -6)
        self.assertEqual(s, datetime(2026, 3, 8, 8, 0, tzinfo=UTC))
        self.assertEqual(e, datetime(2026, 11, 1, 7, 0, tzinfo=UTC))

    def test_ct_conversion_and_format(self):
        winter = to_ct(datetime(2026, 11, 19, 15, 0, tzinfo=UTC))
        summer = to_ct(datetime(2026, 7, 1, 15, 0, tzinfo=UTC))
        self.assertEqual(winter.hour, 9)
        self.assertEqual(summer.hour, 10)
        self.assertIn("CST", fmt_ct(datetime(2026, 11, 19, 15, 5, tzinfo=UTC)))
        self.assertTrue(fmt_ct(datetime(2026, 11, 19, 15, 5, tzinfo=UTC)).startswith("Thu Nov 19, 9:05 AM"))

    def test_valid_time_and_week(self):
        s, e = parse_valid_time("2026-11-19T08:00:00+00:00/P1DT7H")
        self.assertEqual((e - s), timedelta(days=1, hours=7))
        self.assertEqual(iso_week_key(date(2026, 11, 19)), "2026-W47")


class GeoTests(unittest.TestCase):
    def test_polygon_with_hole(self):
        outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
        self.assertTrue(geo.point_in_polygon(2, 2, [outer, hole]))
        self.assertFalse(geo.point_in_polygon(5, 5, [outer, hole]))
        self.assertFalse(geo.point_in_polygon(11, 5, [outer, hole]))

    def test_index(self):
        feats = [{"type": "Feature", "properties": {"v": 1}, "geometry": {"type": "MultiPolygon", "coordinates": [[[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]]}}]
        idx = geo.PolygonIndex(feats)
        self.assertEqual(idx.query(1, 1)[0]["v"], 1)
        self.assertEqual(idx.query(3, 3), [])


class NwsParseTests(unittest.TestCase):
    def test_same_and_vtec(self):
        self.assertEqual(nws.same_to_fips(["022045", "122099", "022000", "abc"]), ["22045", "22099"])
        v = nws.parse_vtec("/O.NEW.KLCH.HZ.W.0007.261202T0600Z-261202T1500Z/")
        self.assertEqual((v["action"], v["office"], v["phen"], v["sig"], v["etn"]), ("NEW", "KLCH", "HZ", "W", "0007"))

    def test_parse_alert(self):
        f = alert_feature("Hard Freeze Warning", "HZ", "W", "KLCH", 7, ["22045", "22101"],
                          datetime(2026, 12, 2, 6, tzinfo=UTC), datetime(2026, 12, 2, 15, tzinfo=UTC), "HEADLINE", "desc")
        a = nws.parse_alert(f)
        self.assertEqual(a["fips"], ["22045", "22101"])
        self.assertEqual(a["track_key"], "KLCH.HZ.W.0007")
        self.assertEqual(a["event"], "Hard Freeze Warning")

    def test_series_and_accumulation(self):
        t0 = datetime(2026, 11, 19, 12, tzinfo=UTC)
        props = {"temperature": {"uom": "wmoUnit:degC", "values": [{"validTime": "2026-11-19T12:00:00+00:00/PT3H", "value": 0}]},
                 "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": [{"validTime": "2026-11-19T12:00:00+00:00/PT6H", "value": 25.4}]}}
        temps = nws.hourly_series(nws.layer_intervals(props, "temperature"), t0, 5)
        self.assertEqual([round(x) if x is not None else None for x in temps], [32, 32, 32, None, None])
        total, _ = nws.window_sum(nws.layer_intervals(props, "quantitativePrecipitation"), t0, t0 + timedelta(hours=3))
        self.assertAlmostEqual(total, 0.5, places=3)


class MetricsTests(unittest.TestCase):
    def test_compute_from_synthetic_gridpoint(self):
        t0 = datetime(2026, 11, 30, 14, tzinfo=UTC)
        props = synth_props(t0, [40, 35, 24, 29, 37, 42, 44, 46, 47], [60, 47, 42, 50, 60, 64, 66, 66, 67],
                            qpf_daily=[0, 0.4, 0, 0, 1.2, 0, 0, 0])
        m, hourly = metrics.compute(props, t0)
        self.assertLessEqual(m["tmin7d"], 24.5)
        self.assertIn("28", m["cold"])
        total, consec, first = m["cold"]["28"]
        self.assertGreaterEqual(consec, 3)
        self.assertTrue(30 <= first <= 50)
        self.assertAlmostEqual(m["qpf7d"], 1.6, delta=0.05)
        self.assertEqual(len(hourly["t"]), 72)
        f = metrics.flatten({"fc": m})
        self.assertIn("run_le_25", f)
        self.assertIn("lead_le_28", f)


class NassTests(unittest.TestCase):
    def test_parse_fixture(self):
        text = (ROOT / "tests" / "fixtures" / "prog4626.txt").read_text(encoding="latin-1")
        rep = nass.parse_report(text, "fixture")
        self.assertEqual(str(rep["released"]), "2026-11-16")
        self.assertEqual(str(rep["week_ending"]), "2026-11-15")
        rows = {(r["state"], r["crop"], r["metric"]): r for r in rep["rows"]}
        self.assertIn(("ND", "corn", "harvested"), rows)
        self.assertIn(("ND", "sunflower", "harvested"), rows)
        r = rows[("ND", "corn", "harvested")]
        self.assertTrue(0 <= r["this_week"] <= 100 and r["last_week"] <= r["this_week"])
        sd_sun = rows[("SD", "sunflower", "harvested")]
        self.assertEqual((sd_sun["last_year"], sd_sun["last_week"], sd_sun["this_week"], sd_sun["avg_5yr"]), (73, 73, 83, 83))
        self.assertEqual(rows[("IA", "fieldwork", "days_suitable")]["this_week"], 5.9)
        self.assertTrue(any(k[1] == "topsoil" for k in rows))

    def test_candidate_urls(self):
        urls = nass.candidate_urls(date(2026, 11, 19))
        self.assertTrue(urls[0][1].endswith("prog4626.txt"))


class StageTests(unittest.TestCase):
    def test_window_fraction(self):
        w = ["10-01", "10-10", "11-10", "11-20"]
        self.assertEqual(stages.window_fraction(date(2026, 9, 1), w), 0.0)
        self.assertEqual(stages.window_fraction(date(2026, 12, 1), w), 1.0)
        mid = stages.window_fraction(date(2026, 10, 25), w)
        self.assertTrue(0.15 < mid < 0.85)
        cross = ["09-20", "10-05", "12-20", "01-10"]
        self.assertTrue(0.6 < stages.window_fraction(date(2026, 11, 30), cross) < 0.75)
        self.assertTrue(stages.window_fraction(date(2027, 1, 5), cross) > 0.9)

    def test_nass_based_distribution(self):
        prog = {("ND", "corn"): {"week_ending": "2026-11-15", "metrics": {"harvested": {"this_week": 91, "last_week": 86, "avg_5yr": 88, "last_year": 93}}}}
        e = stages.estimate("corn", CFG.crop("corn"), "ND", date(2026, 11, 19), prog)
        self.assertEqual(e["basis"], "nass")
        self.assertAlmostEqual(e["phases"]["harvested"], 0.91, places=2)
        self.assertAlmostEqual(sum(e["phases"].values()), 1.0, places=6)
        exp, _ = stages.exposure(e, CFG.crop("corn"), "winter_storm")
        self.assertAlmostEqual(exp, 0.09, places=2)

    def test_sugarcane_and_winter_canola(self):
        e = stages.estimate("sugarcane", CFG.crop("sugarcane"), "LA", date(2026, 11, 30), {})
        self.assertIn("standing_unharvested", e["phases"])
        c = dict(CFG.crop("canola"), stage_model="winter_canola")
        w = stages.estimate("canola", c, "OK", date(2026, 12, 15), {})
        self.assertEqual(w["model"], "winter_canola")
        self.assertIn("dormancy", w["phases"])
        exp, _ = stages.exposure(w, CFG.crop("canola"), "extreme_cold")
        self.assertEqual(exp, 1.0)


def _county(fips, state, acres, name="Test County"):
    return {"fips": fips, "name": name, "state": state, "acres": acres, "region": "central", "lat": 30, "lon": -91}


class AssessTests(unittest.TestCase):
    def setUp(self):
        self.risk = CFG.get("risk")
        self.sugar_stage = {("LA", "sugarcane"): {"basis": "calendar", "phases": {"standing_unharvested": 0.4, "harvested_stubble": 0.6},
                                                  "detail": "test", "model": "sugarcane"}}

    def test_sugarcane_freeze_levels(self):
        c = _county("22045", "LA", {"sugarcane": 50000, "corn": 0, "soybeans": 0})
        flat = {"tmin7d": 21, "lead_le_22": 40, "lead_le_25": 34, "lead_le_32": 20, "lead_le_36": 10, "run_le_25": 6, "hrs_le_28": 9}
        res = [a for a in assess.assess_county(c, {}, flat, self.sugar_stage, CFG.crops, self.risk) if a["hazard"] == "freeze"]
        self.assertEqual(res[0]["level"], "CRITICAL")
        flat2 = dict(flat, tmin7d=24, lead_le_22=None)
        flat2.pop("lead_le_22")
        res2 = [a for a in assess.assess_county(c, {}, flat2, self.sugar_stage, CFG.crops, self.risk) if a["hazard"] == "freeze"]
        self.assertEqual(res2[0]["level"], "HIGH")
        self.assertTrue(any(r.startswith("Not CRITICAL") for r in res2[0]["reasons"]))

    def test_lead_time_cap(self):
        c = _county("22045", "LA", {"sugarcane": 50000})
        flat = {"tmin7d": 24, "lead_le_25": 120, "lead_le_32": 100, "lead_le_36": 90}
        res = [a for a in assess.assess_county(c, {}, flat, self.sugar_stage, CFG.crops, self.risk) if a["hazard"] == "freeze"]
        self.assertEqual(res[0]["intensity"], "HIGH")
        self.assertEqual(res[0]["level"], "MODERATE")

    def test_low_exposure_downgrade(self):
        st = {("IA", "corn"): {"basis": "nass", "phases": {"harvested": 0.97, "mature_standing": 0.03}, "detail": "97% harvested", "model": "progress"}}
        c = _county("19153", "IA", {"corn": 100000})
        flat = {"gust72": 62, "lead_gust_58": 20, "lead_gust_50": 18, "lead_gust_40": 10}
        res = [a for a in assess.assess_county(c, {}, flat, st, CFG.crops, self.risk) if a["hazard"] == "high_wind"]
        self.assertTrue(res == [] or res[0]["level"] == "INFORMATIONAL")


class EventTests(unittest.TestCase):
    def _ev(self, level, fips, tmin=24, start="2026-12-02T09:00:00Z"):
        return {"hazard": "freeze", "state": "LA", "level": level, "counties": [{"fips": f, "name": f} for f in fips],
                "stats": {"tmin7d": tmin}, "window_start": start, "window_end": start, "n_counties": len(fips), "crops": {}}

    def test_notification_reasons(self):
        mc = CFG.get("notify", "material_change")
        now = datetime(2026, 11, 30, 14, tzinfo=UTC)
        ev = self._ev("HIGH", ["1", "2"])
        self.assertEqual(events_mod.notification_reason(ev, None, mc, 6, now, ["CRITICAL", "HIGH"])[0], "new")
        row = {"last_notified_utc": "2026-11-30T13:00:00Z", "last_notified_level": "HIGH", "last_notified_data": db.jdump(ev)}
        self.assertIsNone(events_mod.notification_reason(ev, row, mc, 6, now, ["CRITICAL", "HIGH"])[0])
        up = self._ev("CRITICAL", ["1", "2"])
        self.assertEqual(events_mod.notification_reason(up, row, mc, 6, now, ["CRITICAL", "HIGH"])[0], "upgrade")
        bigger = self._ev("HIGH", ["1", "2", "3", "4", "5"], tmin=20)
        r, ch = events_mod.notification_reason(bigger, row, mc, 6, now, ["CRITICAL", "HIGH"])
        self.assertIsNone(r)
        self.assertTrue(ch)
        later = now + timedelta(hours=7)
        self.assertEqual(events_mod.notification_reason(bigger, row, mc, 6, later, ["CRITICAL", "HIGH"])[0], "update")
        self.assertIsNone(events_mod.notification_reason(self._ev("MODERATE", ["1"]), None, mc, 6, now, ["CRITICAL", "HIGH"])[0])


class AlertClassificationTests(unittest.TestCase):
    def test_floor_and_exposure(self):
        fips = next(f for f, c in CFG.counties.items() if c["state"] == "IA")
        stg = {("IA", k): {"basis": "nass", "phases": {"harvested": 1.0}, "detail": "", "model": "progress"} for k in CFG.crops["crops"]}
        a = {"event": "Tornado Warning", "fips": [fips], "message_type": "Alert"}
        cls = alerts_mod.classify(a, CFG, stg)
        self.assertEqual(cls["level"], "HIGH")
        w = alerts_mod.classify({"event": "Winter Storm Warning", "fips": [fips], "message_type": "Alert"}, CFG, stg)
        self.assertEqual(w["level"], "INFORMATIONAL")

    def test_sugarcane_hard_freeze_is_critical(self):
        fips = [f for f, c in CFG.counties.items() if c["state"] == "LA" and "sugarcane" in c["crops"]][:3]
        stg = {("LA", "sugarcane"): {"basis": "calendar", "phases": {"standing_unharvested": 0.4, "harvested_stubble": 0.6}, "detail": "", "model": "sugarcane"}}
        cls = alerts_mod.classify({"event": "Hard Freeze Warning", "fips": fips, "message_type": "Alert"}, CFG, stg)
        self.assertEqual(cls["level"], "CRITICAL")
        self.assertIsNone(alerts_mod.classify({"event": "Air Quality Alert", "fips": fips, "message_type": "Alert"}, CFG, stg))


class EmailTests(unittest.TestCase):
    def test_dry_run_and_message(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["APWX_OUTBOX"] = td
            for k in ("ALERT_RECIPIENT", "EMAIL_PROVIDER", "SMTP_HOST", "RESEND_API_KEY"):
                os.environ.pop(k, None)
            res = email.send(CFG, "Subject", "<p>hi</p>", "hi", "test")
            self.assertEqual(res["status"], "dry-run")
            self.assertTrue(Path(res["outbox"]).exists())
            os.environ.pop("APWX_OUTBOX")
        msg = email.build_message("S", "<img src='cid:ap-logo'>", "t", "AP <a@example.com>", "b@example.com", b"\x89PNG")
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertIn("ap-logo", msg.as_string())
        self.assertEqual(email.mask("someone@example.com"), "***@***")
        cleaned = email.scrub("SMTPSenderRefused(553, b'5.7.1 <a.b@example.org> denied', 'c@example.net') pw=s3cret",
                              {"smtp_password": "s3cret"})
        self.assertNotIn("example.org", cleaned)
        self.assertNotIn("example.net", cleaned)
        self.assertNotIn("s3cret", cleaned)

    def test_no_hardcoded_recipient(self):
        import re
        pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for p in list((ROOT / "apwx").rglob("*.py")) + list((ROOT / "config").glob("*.json")):
            found = [m for m in pattern.findall(p.read_text(encoding="utf-8")) if not m.endswith("example.com")]
            self.assertEqual(found, [], f"email-like literal in {p}")


class DbTests(unittest.TestCase):
    def test_roundtrip_and_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(Path(td) / "t.sqlite")
            now = datetime(2026, 11, 19, 15, tzinfo=UTC)
            rec = {"id": "a1", "track_key": "K.HZ.W.1", "event": "Hard Freeze Warning", "risk_level": "CRITICAL", "fips": "22045",
                   "monitored_fips": "22045", "data": "{}"}
            self.assertTrue(db.alert_upsert(conn, rec, now))
            self.assertFalse(db.alert_upsert(conn, rec, now))
            self.assertIsNotNone(db.notification_create(conn, "k1", "immediate", "s", [], "<p>", "t", now))
            self.assertIsNone(db.notification_create(conn, "k1", "immediate", "s", [], "<p>", "t", now))
            db.report_save(conn, "daily", "2026-11-19", "subj", "<html>", {"top": []}, now)
            self.assertEqual(db.report_get(conn, "daily", "2026-11-19")["subject"], "subj")
            cid = db.cycle_create(conn, now - timedelta(days=30))
            db.snapshot_save(conn, cid, "22045", {"x": 1})
            db.cycle_finish(conn, cid, 1, 0)
            self.assertEqual(db.prune(conn, now)["cycles_pruned"], 1)
            conn.close()


class LevelTests(unittest.TestCase):
    def test_level_helpers(self):
        self.assertEqual(max_level("MODERATE", "HIGH", "INFORMATIONAL"), "HIGH")
        self.assertEqual(shift_level("CRITICAL", -1), "HIGH")
        self.assertEqual(shift_level("INFORMATIONAL", -3), "NONE")


if __name__ == "__main__":
    unittest.main()