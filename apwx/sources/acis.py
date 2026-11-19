"""Observed daily county-mean precipitation and temperature from NOAA Regional Climate
Centers' Applied Climate Information System (RCC-ACIS), PRISM grid (grid 21).

These are gridded analyses of station observations (observed, not forecast); the most
recent day can be provisional.
"""
from .. import http as net

URL = "https://data.rcc-acis.org/GridData"
LINK = "https://www.rcc-acis.org/"


def fetch_state_daily(state, state_fips, sdate, edate, grid="21"):
    params = {"state": state, "sdate": sdate.strftime("%Y%m%d"), "edate": edate.strftime("%Y%m%d"), "grid": grid,
              "elems": [{"name": "pcpn", "area_reduce": "county_mean"},
                        {"name": "maxt", "area_reduce": "county_mean"},
                        {"name": "mint", "area_reduce": "county_mean"}]}
    doc = net.fetch_json(URL, json_body=params, source="acis", timeout=90, retries=3)
    rows = []
    for rec in doc.get("data", []) or []:
        day = rec[0]
        pc, mx, mn = (rec[1] if len(rec) > 1 else {}), (rec[2] if len(rec) > 2 else {}), (rec[3] if len(rec) > 3 else {})
        for fips, val in (pc or {}).items():
            if not str(fips).startswith(state_fips):
                continue
            rows.append({"obs_date": day, "fips": str(fips), "pcpn": _clean(val), "maxt": _clean(mx.get(fips)),
                         "mint": _clean(mn.get(fips))})
    return rows


def _clean(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < -900:
        return None
    return round(f, 2)