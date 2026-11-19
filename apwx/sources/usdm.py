"""U.S. Drought Monitor county statistics (percent area in each category, cumulative).

API: https://usdmdataservices.unl.edu/api/  - d0 = percent of county in D0-D4, d1 = D1-D4, etc.
Maps are valid Tuesday and released Thursday.
"""
from .. import http as net

URL = "https://usdmdataservices.unl.edu/api/CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent"
LINK = "https://droughtmonitor.unl.edu/"


def fetch_state(state, start, end):
    params = {"aoi": state, "startdate": f"{start.month}/{start.day}/{start.year}",
              "enddate": f"{end.month}/{end.day}/{end.year}", "statisticsType": "1"}
    rows = net.fetch_json(URL, params=params, accept="application/json", source="usdm", timeout=60, retries=3)
    out = []
    for r in rows or []:
        try:
            out.append({"map_date": (r.get("mapDate") or "")[:10], "fips": str(r["fips"]).zfill(5),
                        "none": float(r.get("none") or 0), "d0": float(r.get("d0") or 0),
                        "d1": float(r.get("d1") or 0), "d2": float(r.get("d2") or 0),
                        "d3": float(r.get("d3") or 0), "d4": float(r.get("d4") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def dsci(row):
    """Drought Severity and Coverage Index (0-500) from cumulative percentages."""
    if not row:
        return None
    return round(row["d0"] + row["d1"] + row["d2"] + row["d3"] + row["d4"], 1)


def category_label(row):
    if not row:
        return "n/a"
    for key, label in (("d4", "D4 Exceptional"), ("d3", "D3 Extreme"), ("d2", "D2 Severe"),
                       ("d1", "D1 Moderate"), ("d0", "D0 Abnormally dry")):
        if row.get(key, 0) >= 50:
            return label
    return "None (less than half of area in D0+)"