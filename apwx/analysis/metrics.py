"""County forecast metrics computed from the NWS gridded forecast (NDFD via api.weather.gov).

All temperatures in deg F, precipitation in inches, wind in mph. Lead times are hours
from the top of the current hour. Daily rows use U.S. Central calendar days; the
"lo" value is the NWS overnight minimum for the night that follows that day.
"""
from datetime import timedelta

from ..sources import nws
from ..timeutil import ct_midnight_utc, iso, to_ct

COLD_THRESHOLDS = (36, 32, 30, 28, 26, 25, 24, 22, 20, 15, 10, 5, 0)
HEAT_THRESHOLDS = (86, 90, 95, 100)
GUST_THRESHOLDS = (40, 50, 58)
HOURS = 168
HOURLY_EXPORT = 72


def _r(v, n=1):
    return None if v is None else round(v, n)


def _runs(values, pred):
    total = best = cur = 0
    first = None
    for i, v in enumerate(values):
        if v is not None and pred(v):
            total += 1
            cur += 1
            best = max(best, cur)
            if first is None:
                first = i
        else:
            cur = 0
    return total, best, first


def _wmin(vals, h):
    x = [v for v in vals[:h] if v is not None]
    return min(x) if x else None


def _wmax(vals, h):
    x = [v for v in vals[:h] if v is not None]
    return max(x) if x else None


def _argext(vals, h, fn):
    best_i, best_v = None, None
    for i, v in enumerate(vals[:h]):
        if v is None:
            continue
        if best_v is None or fn(v, best_v):
            best_i, best_v = i, v
    return best_i


def compute(props, now):
    """Return (metrics, hourly) for one county gridpoint."""
    t0 = now.replace(minute=0, second=0, microsecond=0)
    temp = nws.hourly_series(nws.layer_intervals(props, "temperature"), t0, HOURS)
    dew = nws.hourly_series(nws.layer_intervals(props, "dewpoint"), t0, HOURLY_EXPORT)
    pop = nws.hourly_series(nws.layer_intervals(props, "probabilityOfPrecipitation"), t0, HOURS)
    gust = nws.hourly_series(nws.layer_intervals(props, "windGust"), t0, HOURS)
    wind = nws.hourly_series(nws.layer_intervals(props, "windSpeed"), t0, HOURS)
    thunder = nws.hourly_series(nws.layer_intervals(props, "probabilityOfThunder"), t0, HOURS)
    sky = nws.hourly_series(nws.layer_intervals(props, "skyCover"), t0, 3)
    qpf_iv = nws.layer_intervals(props, "quantitativePrecipitation")
    snow_iv = nws.layer_intervals(props, "snowfallAmount")
    ice_iv = nws.layer_intervals(props, "iceAccumulation")
    tmax_iv = nws.layer_intervals(props, "maxTemperature")
    tmin_iv = nws.layer_intervals(props, "minTemperature")

    valid = sum(1 for v in temp if v is not None)
    m = {"upd": iso(nws.update_time(props)), "valid_h": valid}
    for label, h in (("24", 24), ("72", 72), ("7d", HOURS)):
        m["tmin" + label] = _r(_wmin(temp, h))
        m["tmax" + label] = _r(_wmax(temp, h))
    i = _argext(temp, HOURS, lambda a, b: a < b)
    m["tmin7d_t"] = iso(t0 + timedelta(hours=i)) if i is not None else None
    i = _argext(temp, HOURS, lambda a, b: a > b)
    m["tmax7d_t"] = iso(t0 + timedelta(hours=i)) if i is not None else None

    cold = {}
    for thr in COLD_THRESHOLDS:
        total, consec, first = _runs(temp, lambda v, t=thr: v <= t)
        if total:
            cold[str(thr)] = [total, consec, first]
    m["cold"] = cold
    heat = {}
    for thr in HEAT_THRESHOLDS:
        total, consec, first = _runs(temp, lambda v, t=thr: v >= t)
        if total:
            heat[str(thr)] = [total, consec, first]
    m["heat"] = heat
    gusts = {}
    for thr in GUST_THRESHOLDS:
        total, consec, first = _runs(gust, lambda v, t=thr: v >= t)
        if total:
            gusts[str(thr)] = [total, consec, first]
    m["gusts"] = gusts

    for label, h in (("24", 24), ("72", 72), ("7d", HOURS)):
        m["qpf" + label] = _r(nws.window_sum(qpf_iv, t0, t0 + timedelta(hours=h))[0], 2)
    m["snow72"] = _r(nws.window_sum(snow_iv, t0, t0 + timedelta(hours=72))[0], 1)
    m["snow7d"] = _r(nws.window_sum(snow_iv, t0, t0 + timedelta(hours=HOURS))[0], 1)
    m["ice72"] = _r(nws.window_sum(ice_iv, t0, t0 + timedelta(hours=72))[0], 2)
    m["pop72"] = _wmax(pop, 72)
    m["pop7d"] = _wmax(pop, HOURS)
    m["gust72"] = _r(_wmax(gust, 72), 0)
    m["gust7d"] = _r(_wmax(gust, HOURS), 0)
    m["wind72"] = _r(_wmax(wind, 72), 0)
    m["thunder72"] = _wmax(thunder, 72)
    m["now"] = {"t": _r(temp[0]), "td": _r(dew[0]) if dew else None, "wind": _r(wind[0], 0),
                "gust": _r(gust[0], 0), "sky": sky[0] if sky else None, "pop": pop[0]}

    # Daily table (Central calendar days)
    days = []
    d0 = to_ct(t0).date()
    for k in range(8):
        d = d0 + timedelta(days=k)
        ds, de = ct_midnight_utc(d), ct_midnight_utc(d + timedelta(days=1))
        hi = [v for s, e, v in tmax_iv if to_ct(s).date() == d]
        lo = [v for s, e, v in tmin_iv if to_ct(s - timedelta(hours=12)).date() == d]
        idx = [j for j in range(HOURS) if ds <= t0 + timedelta(hours=j) < de]
        row = {
            "d": d.isoformat(),
            "hi": _r(hi[0], 0) if hi else None,
            "lo": _r(lo[0], 0) if lo else None,
            "qpf": _r(nws.window_sum(qpf_iv, ds, de)[0], 2),
            "snow": _r(nws.window_sum(snow_iv, ds, de)[0], 1),
            "pop": max((pop[j] for j in idx if pop[j] is not None), default=None),
            "gust": _r(max((gust[j] for j in idx if gust[j] is not None), default=None), 0) if idx else None,
            "tstm": max((thunder[j] for j in idx if thunder[j] is not None), default=None),
        }
        if row["hi"] is None and row["lo"] is None and row["qpf"] == 0 and row["pop"] is None:
            continue
        days.append(row)
    m["daily"] = days

    hourly = {"t0": iso(t0), "t": [_r(v, 0) for v in temp[:HOURLY_EXPORT]], "td": [_r(v, 0) for v in dew],
              "pop": pop[:HOURLY_EXPORT], "gust": [_r(v, 0) for v in gust[:HOURLY_EXPORT]],
              "wind": [_r(v, 0) for v in wind[:HOURLY_EXPORT]]}
    return m, hourly


def flatten(snap):
    """Flat dict of numeric metrics for rule evaluation (missing values are omitted)."""
    f = {}
    fc = snap.get("fc") or {}
    for k in ("tmin24", "tmin72", "tmin7d", "tmax24", "tmax72", "tmax7d", "qpf24", "qpf72", "qpf7d", "snow72",
              "snow7d", "ice72", "pop72", "pop7d", "gust72", "gust7d", "wind72", "thunder72"):
        if fc.get(k) is not None:
            f[k] = fc[k]
    for thr, (total, consec, first) in (fc.get("cold") or {}).items():
        f[f"hrs_le_{thr}"] = total
        f[f"run_le_{thr}"] = consec
        f[f"lead_le_{thr}"] = first
    for thr, (total, consec, first) in (fc.get("heat") or {}).items():
        f[f"hrs_ge_{thr}"] = total
        f[f"run_ge_{thr}"] = consec
        f[f"lead_ge_{thr}"] = first
    for thr, (total, consec, first) in (fc.get("gusts") or {}).items():
        f[f"hrs_gust_{thr}"] = total
        f[f"lead_gust_{thr}"] = first
    daily = fc.get("daily") or []
    for thr in HEAT_THRESHOLDS:
        f[f"days_ge_{thr}"] = sum(1 for r in daily if r.get("hi") is not None and r["hi"] >= thr)
    for thr, key in ((0.5, "rain05"), (1.0, "rain1"), (2.0, "rain2")):
        idx = next((i for i, r in enumerate(daily) if (r.get("qpf") or 0) >= thr), None)
        if idx is not None:
            f[f"lead_{key}"] = idx * 24
    idx = next((i for i, r in enumerate(daily) if (r.get("snow") or 0) >= 1.0), None)
    if idx is not None:
        f["lead_snow1"] = idx * 24
    spc = snap.get("spc") or {}
    for k, v in spc.items():
        f["spc_" + k] = v
    if spc:
        f["spc_max"] = max(spc.get("d1_cat", 0), spc.get("d2_cat", 0), spc.get("d3_cat", 0))
        f["spc_hail"] = max(spc.get("d1_hail", 0), spc.get("d2_hail", 0))
        f["spc_wind"] = max(spc.get("d1_wind", 0), spc.get("d2_wind", 0))
        f["spc_torn"] = max(spc.get("d1_torn", 0), spc.get("d2_torn", 0))
        f["spc_prob"] = max(f["spc_hail"], f["spc_wind"], spc.get("d3_prob", 0))
        f["spc_sig"] = max(spc.get("d1_sigtorn", 0), spc.get("d1_sighail", 0), spc.get("d1_sigwind", 0))
        lead = None
        for day, key in ((0, "d1_cat"), (24, "d2_cat"), (48, "d3_cat")):
            if spc.get(key, 0) >= 2:
                lead = day
                break
        if lead is not None:
            f["lead_spc"] = lead
    wpc = snap.get("wpc") or {}
    for k, v in wpc.items():
        f["wpc_" + k] = v
    if wpc:
        f["ero_max"] = max(wpc.get("ero1", 0), wpc.get("ero2", 0), wpc.get("ero3", 0))
        for day, key in ((0, "ero1"), (24, "ero2"), (48, "ero3")):
            if wpc.get(key, 0) >= 1:
                f["lead_ero"] = day
                break
    dm = snap.get("dm") or {}
    for k in ("d0", "d1", "d2", "d3", "d4", "dsci", "chg1", "chg4"):
        if dm.get(k) is not None:
            f["dm_" + k] = dm[k]
    obs = snap.get("obs") or {}
    for k in ("pcpn1", "pcpn7", "pcpn14", "mint7", "maxt7"):
        if obs.get(k) is not None:
            f["obs_" + k] = obs[k]
    cpc = snap.get("cpc") or {}
    for key, val in cpc.items():
        if not val:
            continue
        cat, prob = val
        f[f"cpc_{key}_above"] = prob if cat == "A" else 0
        f[f"cpc_{key}_below"] = prob if cat == "B" else 0
    return f