"""NOAA Storm Prediction Center: convective outlooks (Day 1-3) and storm reports.

Outlook GeoJSON: https://www.spc.noaa.gov/products/outlook/
Storm reports:   https://www.spc.noaa.gov/climo/reports/
"""
import csv
import hashlib
import io

from .. import http as net
from ..geo import PolygonIndex

OUTLOOK_BASE = "https://www.spc.noaa.gov/products/outlook/"
REPORTS_BASE = "https://www.spc.noaa.gov/climo/reports/"

CAT_LABELS = {0: "None", 2: "TSTM", 3: "MRGL", 4: "SLGT", 5: "ENH", 6: "MDT", 8: "HIGH"}
CAT_NAMES = {2: "General thunderstorms", 3: "Marginal", 4: "Slight", 5: "Enhanced", 6: "Moderate", 8: "High"}
# SPC categorical DN -> ordinal 0..6 for comparisons
CAT_ORDINAL = {0: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 6}

PRODUCTS = {
    "d1_cat": "day1otlk_cat", "d1_torn": "day1otlk_torn", "d1_hail": "day1otlk_hail", "d1_wind": "day1otlk_wind",
    "d1_sigtorn": "day1otlk_sigtorn", "d1_sighail": "day1otlk_sighail", "d1_sigwind": "day1otlk_sigwind",
    "d2_cat": "day2otlk_cat", "d2_torn": "day2otlk_torn", "d2_hail": "day2otlk_hail", "d2_wind": "day2otlk_wind",
    "d3_cat": "day3otlk_cat", "d3_prob": "day3otlk_prob",
}


def fetch_outlooks():
    """Return {key: {"index": PolygonIndex, "issue": str, "valid": str, "expire": str, "url": str}}."""
    out = {}
    for key, name in PRODUCTS.items():
        url = f"{OUTLOOK_BASE}{name}.lyr.geojson"
        try:
            doc = net.fetch_json(url, source="spc_outlooks", timeout=45, retries=2)
        except net.FetchError as e:
            out[key] = {"error": str(e), "url": url}
            continue
        feats = doc.get("features", [])
        props = feats[0]["properties"] if feats else {}
        out[key] = {"index": PolygonIndex(feats), "issue": props.get("ISSUE_ISO"), "valid": props.get("VALID_ISO"),
                    "expire": props.get("EXPIRE_ISO"), "url": url}
    return out


def point_values(outlooks, lon, lat):
    vals = {}
    for key, prod in outlooks.items():
        idx = prod.get("index")
        if idx is None:
            continue
        hits = idx.query(lon, lat)
        if key.endswith("_cat"):
            dn = max((int(h.get("DN") or 0) for h in hits), default=0)
            vals[key] = CAT_ORDINAL.get(dn, 0)
        elif "sig" in key:
            vals[key] = 1 if hits else 0
        else:
            vals[key] = max((int(float(h.get("DN") or 0)) for h in hits), default=0)
    return vals


def ordinal_label(ordinal):
    inv = {v: k for k, v in CAT_ORDINAL.items()}
    return CAT_LABELS.get(inv.get(ordinal, 0), "None")


def ordinal_name(ordinal):
    inv = {v: k for k, v in CAT_ORDINAL.items()}
    return CAT_NAMES.get(inv.get(ordinal, 0), "No severe risk")


def fetch_storm_reports(day="today"):
    """Parse SPC filtered storm reports CSV (tornado, wind, hail sections)."""
    url = f"{REPORTS_BASE}{day}_filtered.csv"
    text = net.fetch_text(url, source="spc_reports", timeout=30, retries=2)
    reports = []
    kind = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("Time,"):
            col2 = line.split(",")[1].strip()
            kind = {"F_Scale": "tornado", "Speed": "wind", "Size": "hail"}.get(col2, "other")
            continue
        row = next(csv.reader(io.StringIO(line)))
        if len(row) < 7 or kind is None:
            continue
        try:
            lat, lon = float(row[5]), float(row[6])
        except ValueError:
            continue
        mag = row[1].strip()
        rid = hashlib.sha1(f"{day}|{kind}|{line}".encode()).hexdigest()[:16]
        reports.append({"id": rid, "kind": kind, "time_hhmm": row[0], "magnitude": mag, "location": row[2],
                        "county": row[3], "state": row[4], "lat": lat, "lon": lon,
                        "comments": row[7] if len(row) > 7 else "", "day": day, "url": url})
    return reports