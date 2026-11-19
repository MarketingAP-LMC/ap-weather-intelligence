"""Simulation scenarios (synthetic data) run through the real engine.

Every email, report and dashboard produced here carries a SIMULATION banner and
subject prefix. Synthetic NWS-format gridpoint data is parsed by the same code that
parses live data, so the simulation also exercises the parser and metrics.
"""
import math
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import db
from .analysis import assess, metrics
from .analysis import events as events_mod
from .config import ROOT
from .timeutil import UTC, iso, to_ct


def _f_to_c(v):
    return (v - 32.0) * 5.0 / 9.0


def _iv(start, hours):
    return f"{start.strftime('%Y-%m-%dT%H:%M:%S')}+00:00/PT{hours}H"


def synth_props(t0, lows, highs, qpf_daily=None, snow_daily=None, gust_daily=None, pop_daily=None, tstm_daily=None):
    """NWS-gridpoint-like properties. lows[k]/highs[k] = low on the morning of local day k / high on day k."""
    hours = 192
    temps = []
    d0 = to_ct(t0).date()
    for h in range(hours):
        t = t0 + timedelta(hours=h)
        lt = to_ct(t)
        k = min(len(lows) - 1, max(0, (lt.date() - d0).days))
        lo, hi = lows[k], highs[k]
        frac = 0.5 + 0.5 * math.cos(2 * math.pi * (lt.hour - 15) / 24.0)
        temps.append(lo + (hi - lo) * frac)
    props = {"updateTime": iso(t0)}
    props["temperature"] = {"uom": "wmoUnit:degC", "values": [{"validTime": _iv(t0 + timedelta(hours=i), 1), "value": _f_to_c(v)} for i, v in enumerate(temps)]}
    props["dewpoint"] = {"uom": "wmoUnit:degC", "values": [{"validTime": _iv(t0 + timedelta(hours=i), 1), "value": _f_to_c(v - 6)} for i, v in enumerate(temps)]}
    mx, mn = [], []
    for k in range(8):
        day = d0 + timedelta(days=k)
        start_day = datetime(day.year, day.month, day.day, 13, tzinfo=UTC)
        start_night = datetime(day.year, day.month, day.day, 1, tzinfo=UTC) + timedelta(days=1)
        mx.append({"validTime": _iv(start_day, 12), "value": _f_to_c(highs[min(k, len(highs) - 1)])})
        mn.append({"validTime": _iv(start_night, 13), "value": _f_to_c(lows[min(k + 1, len(lows) - 1)])})
    props["maxTemperature"] = {"uom": "wmoUnit:degC", "values": mx}
    props["minTemperature"] = {"uom": "wmoUnit:degC", "values": mn}

    def daily_6h(arr, unit_conv):
        vals = []
        for k in range(8):
            day = d0 + timedelta(days=k)
            base = datetime(day.year, day.month, day.day, 6, tzinfo=UTC)
            v = (arr or [0] * 8)[min(k, len(arr or [0]) - 1)] if arr else 0
            for q in range(4):
                vals.append({"validTime": _iv(base + timedelta(hours=6 * q), 6), "value": unit_conv(v / 4.0)})
        return vals
    props["quantitativePrecipitation"] = {"uom": "wmoUnit:mm", "values": daily_6h(qpf_daily, lambda x: x * 25.4)}
    props["snowfallAmount"] = {"uom": "wmoUnit:mm", "values": daily_6h(snow_daily, lambda x: x * 25.4)}
    props["iceAccumulation"] = {"uom": "wmoUnit:mm", "values": daily_6h(None, lambda x: 0)}

    def hourly_daily(arr, conv, default):
        out = []
        for i in range(hours):
            t = t0 + timedelta(hours=i)
            k = min(7, max(0, (to_ct(t).date() - d0).days))
            v = arr[min(k, len(arr) - 1)] if arr else default
            out.append({"validTime": _iv(t, 1), "value": conv(v)})
        return out
    props["windGust"] = {"uom": "wmoUnit:km_h-1", "values": hourly_daily(gust_daily, lambda x: x * 1.609344, 18)}
    props["windSpeed"] = {"uom": "wmoUnit:km_h-1", "values": hourly_daily(gust_daily, lambda x: x * 0.6 * 1.609344, 18)}
    props["probabilityOfPrecipitation"] = {"uom": "wmoUnit:percent", "values": hourly_daily(pop_daily, lambda x: x, 10)}
    props["probabilityOfThunder"] = {"uom": None, "values": hourly_daily(tstm_daily, lambda x: x, 0)}
    props["skyCover"] = {"uom": "wmoUnit:percent", "values": hourly_daily(None, lambda x: x, 40)}
    return props


def baseline(lat, month):
    """Plausible quiet-weather lows/highs by latitude and month (synthetic)."""
    if month in (11, 12, 1, 2):
        lo = 62 - (lat - 25) * 1.9
    else:
        lo = 68 - (lat - 25) * 1.1
    hi = lo + 18
    return [round(lo + (i % 3) - 1) for i in range(9)], [round(hi + (i % 2)) for i in range(9)]


def alert_feature(event, phen, sig, office, etn, fips_list, onset, ends, headline, description, instruction="", extra=None, sent=None):
    same = ["0" + f for f in fips_list]
    vtec = f"/O.NEW.{office}.{phen}.{sig}.{etn:04d}.{onset:%y%m%dT%H%MZ}-{ends:%y%m%dT%H%MZ}/"
    params = {"VTEC": [vtec], "NWSheadline": [headline]}
    params.update(extra or {})
    aid = f"urn:oid:2.49.0.1.840.0.SIMULATION.{office}.{phen}{sig}.{etn}"
    return {"id": f"https://api.weather.gov/alerts/{aid}", "properties": {
        "id": aid, "event": event, "severity": "Severe", "certainty": "Likely", "urgency": "Expected", "status": "Actual",
        "messageType": "Alert", "sent": iso(sent or onset - timedelta(hours=10)), "effective": iso(sent or onset - timedelta(hours=10)),
        "onset": iso(onset), "expires": iso(ends), "ends": iso(ends), "headline": f"SIMULATION: {headline}",
        "description": "SIMULATION - synthetic test data. " + description, "instruction": instruction,
        "areaDesc": "; ".join(fips_list), "senderName": f"NWS {office[1:]} (SIMULATION)",
        "geocode": {"SAME": same, "UGC": []}, "parameters": params, "references": []}}


def _county_ids(cfg, state, names):
    want = {n.lower() for n in names}
    return [f for f, c in cfg.counties.items() if c["state"] == state and c["name"].lower().replace(" parish", "").replace(" county", "") in want]


SCENARIOS = {}


def scenario_sugarcane_freeze(cfg):
    now = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
    north = _county_ids(cfg, "LA", ["pointe coupee", "avoyelles", "st. landry", "iberville"])
    south = [f for f, c in cfg.counties.items() if c["state"] == "LA" and "sugarcane" in c.get("crops", []) and f not in north]
    fl = [f for f, c in cfg.counties.items() if c["state"] == "FL" and "sugarcane" in c.get("crops", [])]
    canola = [f for f, c in cfg.counties.items() if c["state"] in ("OK", "KS") and "canola" in c.get("crops", [])]
    nd = [f for f, c in cfg.counties.items() if c["state"] == "ND"]

    def mods(prev):
        m = {}
        for f in north:
            m[f] = {"lows": [38, 33, 25 if prev else 21, 27, 35, 40, 42, 44, 45], "highs": [58, 45, 40, 48, 58, 62, 64, 65, 66]}
        for f in south:
            m[f] = {"lows": [40, 35, 27 if prev else 24, 29, 37, 42, 44, 46, 47], "highs": [60, 47, 42, 50, 60, 64, 66, 66, 67]}
        for f in fl:
            m[f] = {"lows": [55, 48, 34 if prev else 30, 31, 40, 50, 55, 56, 57], "highs": [75, 66, 58, 62, 70, 75, 77, 78, 78]}
        for f in canola:
            m[f] = {"lows": [30, 22, 14 if prev else 9, 15, 24, 30, 32, 33, 33], "highs": [48, 35, 28, 35, 45, 52, 54, 55, 56]}
        for f in nd:
            m[f] = {"snow": [0, 3, 5, 1, 0, 0, 0, 0], "gust": [25, 40, 45, 30, 20, 20, 20, 20]}
        return m
    hard_freeze = alert_feature("Hard Freeze Warning", "HZ", "W", "KLCH", 7, [f for f in south if cfg.counties[f]["nws"]["grid"] == "LCH"],
                                datetime(2026, 12, 2, 6, tzinfo=UTC), datetime(2026, 12, 2, 15, tzinfo=UTC),
                                "HARD FREEZE WARNING IN EFFECT FROM MIDNIGHT TUESDAY NIGHT TO 9 AM CST WEDNESDAY",
                                "WHAT...Sub-freezing temperatures as low as 23 expected. WHERE...South central Louisiana. "
                                "IMPACTS...Frost and freeze conditions will kill crops and other sensitive vegetation.",
                                sent=now - timedelta(minutes=40))
    return {"name": "sugarcane_freeze", "title": "Arctic outbreak - Louisiana sugarcane hard freeze", "now": now,
            "cycles": [(now - timedelta(hours=3), mods(True)), (now, mods(False))], "alerts": [hard_freeze]}


def scenario_spring_storms(cfg):
    now = datetime(2027, 5, 18, 15, 0, tzinfo=UTC)
    ks_ok = [f for f, c in cfg.counties.items() if c["state"] in ("KS", "OK") and 35.0 <= c["lat"] <= 39.5 and -98.5 <= c["lon"] <= -95.0]
    ia_il = [f for f, c in cfg.counties.items() if c["state"] in ("IA", "IL") and 41.0 <= c["lat"] <= 42.6]
    nd_mn = [f for f, c in cfg.counties.items() if c["state"] in ("ND", "MN") and c["lat"] >= 47.5]

    def mods(prev):
        m = {}
        for f in ks_ok:
            m[f] = {"spc": {"d1_cat": 4 if prev else 5, "d1_hail": 30 if prev else 45, "d1_wind": 30, "d1_torn": 10 if prev else 15,
                            "d1_sighail": 1, "d2_cat": 3}, "gust": [45, 55, 30, 25, 20, 20, 20, 20], "qpf": [0.2, 1.5, 0.3, 0, 0, 0, 0, 0]}
        for f in ia_il:
            m[f] = {"qpf": [0.5, 1.6 if prev else 2.2, 1.2 if prev else 1.6, 0.3, 0, 0, 0, 0], "wpc": {"ero1": 2, "ero2": 2 if prev else 3}}
        for f in nd_mn:
            m[f] = {"lows": [40, 33, 30 if prev else 29, 34, 42, 45, 46, 47, 48], "highs": [62, 55, 52, 60, 66, 68, 70, 71, 72]}
        return m
    kay = _county_ids(cfg, "OK", ["kay", "grant", "noble"])
    tor = alert_feature("Tornado Warning", "TO", "W", "KOUN", 41, kay[:2], now + timedelta(minutes=5), now + timedelta(minutes=50),
                        "TORNADO WARNING FOR KAY AND GRANT COUNTIES",
                        "At 10:05 AM CDT, a severe thunderstorm capable of producing a tornado was located near Blackwell.",
                        "TAKE COVER NOW!", {"tornadoDetection": ["RADAR INDICATED"], "maxHailSize": ["1.75"]}, sent=now)
    polk = _county_ids(cfg, "IA", ["story", "marshall", "boone"])
    ffw = alert_feature("Flash Flood Warning", "FF", "W", "KDMX", 18, polk, now + timedelta(hours=1), now + timedelta(hours=6),
                        "FLASH FLOOD WARNING FOR CENTRAL IOWA", "Doppler radar indicated heavy rain; 2 to 3 inches have fallen.",
                        "Turn around, don't drown.", {"flashFloodDamageThreat": ["CONSIDERABLE"]}, sent=now)
    return {"name": "spring_storms", "title": "Spring severe outbreak and heavy rain", "now": now,
            "cycles": [(now - timedelta(hours=3), mods(True)), (now, mods(False))], "alerts": [tor, ffw]}


SCENARIOS = {"sugarcane_freeze": scenario_sugarcane_freeze, "spring_storms": scenario_spring_storms}


def build_snaps(cfg, t, mods):
    snaps = {}
    month = to_ct(t).month
    for f, c in cfg.counties.items():
        m = mods.get(f, {})
        lows, highs = baseline(c["lat"], month)
        lows, highs = m.get("lows", lows), m.get("highs", highs)
        props = synth_props(t, lows, highs, qpf_daily=m.get("qpf"), snow_daily=m.get("snow"), gust_daily=m.get("gust"),
                            pop_daily=[60 if (m.get("qpf") or [0])[min(i, len(m.get("qpf") or [0]) - 1)] > 0.1 else 10 for i in range(8)])
        fc, hourly = metrics.compute(props, t)
        spc = {"d1_cat": 0, "d2_cat": 0, "d3_cat": 0, "d1_hail": 0, "d1_wind": 0, "d1_torn": 0, "d2_hail": 0, "d2_wind": 0,
               "d2_torn": 0, "d3_prob": 0, "d1_sigtorn": 0, "d1_sighail": 0, "d1_sigwind": 0}
        spc.update(m.get("spc", {}))
        wpc = {"qpf72": fc["qpf72"] or 0, "qpf7d": fc["qpf7d"] or 0, "ero1": 0, "ero2": 0, "ero3": 0}
        wpc.update(m.get("wpc", {}))
        snaps[f] = ({"t": iso(t), "fc": fc, "spc": spc, "wpc": wpc, "cpc": m.get("cpc", {"t610": ["EC", 33], "p610": ["EC", 33],
                                                                                      "t814": ["EC", 33], "p814": ["EC", 33]})}, hourly)
    return snaps


def run(cfg, scenario="all", out=None):
    from .pipeline import Ctx, compute_stages, poll_alerts, reconcile_events, run_daily, run_weekly, send_immediate
    names = list(SCENARIOS) if scenario in (None, "all") else [scenario]
    results = {}
    for name in names:
        sc = SCENARIOS[name](cfg)
        base = Path(out) if out else ROOT / "samples" / f"simulation_{name}"
        if base.exists():
            shutil.rmtree(base)
        base.mkdir(parents=True)
        os.environ["APWX_DRY_RUN"] = "1"
        os.environ["APWX_DB"] = str(base / "simulation.sqlite")
        os.environ["APWX_OUTBOX"] = str(base / "emails")
        os.environ["APWX_SITE_DIR"] = str(base / "dashboard")
        conn = db.connect(cfg.db_path)
        summary = {"scenario": sc["title"], "now": iso(sc["now"]), "cycles": []}
        for i, (t, mods) in enumerate(sc["cycles"]):
            ctx = Ctx(cfg, conn, t, simulation=True)
            stages = compute_stages(cfg, conn, t)
            if i == len(sc["cycles"]) - 1:
                poll_alerts(ctx, stages, features=sc["alerts"])
            cycle_id = db.cycle_create(conn, t)
            snaps = build_snaps(cfg, t, mods)
            flats = {}
            for f, (snap, hourly) in snaps.items():
                db.snapshot_save(conn, cycle_id, f, snap)
                db.hourly_save(conn, f, cycle_id, hourly)
                db.history_save(conn, iso(t), f, snap["fc"])
                flats[f] = metrics.flatten(snap)
            db.cycle_finish(conn, cycle_id, len(snaps), 0, {"simulation": True})
            assessments = []
            for f, c in cfg.counties.items():
                assessments += assess.assess_county(c, snaps[f][0], flats[f], stages, cfg.crops, cfg.get("risk", default={}))
            evs = events_mod.cluster(assessments, flats, cfg.counties, t, cfg.get("hazard_labels", default={}), cfg.crops)
            db.meta_set(conn, "latest_assessments", db.jdump(assessments))
            db.meta_set(conn, "last_cycle_utc", iso(t))
            reconcile_events(ctx, evs, cycle_id)
            conn.commit()
            imm = send_immediate(ctx)
            summary["cycles"].append({"t": iso(t), "assessments": len(assessments), "events": len(evs),
                                      "levels": sorted({e["level"] for e in evs}), "immediate": imm})
        ctx = Ctx(cfg, conn, sc["now"], simulation=True)
        stages = compute_stages(cfg, conn, sc["now"])
        summary["daily"] = run_daily(ctx, stages, to_ct(sc["now"]).date().isoformat())
        summary["weekly"] = run_weekly(ctx, stages, "SIM-" + to_ct(sc["now"]).strftime("%G-W%V"))
        from .dashboard import build as dash
        summary["dashboard"] = dash.build(ctx, stages)
        conn.close()
        summary["outputs"] = str(base)
        results[name] = summary
    for k in ("APWX_DB", "APWX_OUTBOX", "APWX_SITE_DIR"):
        os.environ.pop(k, None)
    return results