"""Build config/counties.json - the monitored agricultural counties.

Sources (all public, no credentials):
  * U.S. Census Bureau 2024 Gazetteer county file (internal points, land area)
  * USDA NASS Cropland Data Layer (CDL) 2025 county acreage statistics (CropScape)
  * NWS API /points and /stations (forecast grid cell, zones, nearest observing station)

Selection rule (documented in docs/METHODOLOGY.md):
  For each state and crop, counties are ranked by CDL acreage of that crop and
  kept until they cover COVERAGE of the state's total acreage of that crop,
  as long as each kept county has at least MIN_ACRES of the crop. The union
  across the monitored crops is the monitored county set.

Usage:  python tools/build_reference.py [--coverage 0.8] [--skip-nws]
"""
import argparse
import csv
import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apwx import http as net  # noqa: E402

GAZ_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip"
CDL_YEAR = 2025
CDL_CACHE = "https://nassgeodata.gmu.edu/webservice/nass_data_cache/byfips/CDL_{year}_{fips}.csv"
CDL_SERVICE = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLStat?year={year}&fips={fips}&format=csv"
NWS = "https://api.weather.gov"

STATES = {"OK": "40", "ND": "38", "SD": "46", "KS": "20", "NE": "31", "MN": "27",
          "IA": "19", "IL": "17", "IN": "18", "OH": "39", "LA": "22", "FL": "12", "CA": "06"}
# States monitored for only some crops (all other states: every crop in CDL_CODES).
STATE_CROPS = {"CA": {"winter_wheat"}}

# CDL category codes (USDA NASS CDL legend). Double-crop classes count toward each crop named.
CDL_CODES = {
    "corn": {1, 225, 226, 228, 237, 241},
    "soybeans": {5, 26, 239, 240, 241, 254},
    "sunflower": {6},
    "canola": {31},
    "sugarcane": {45},
    "winter_wheat": {24, 26, 225, 236, 238},
}
MIN_ACRES = {"corn": 5000, "soybeans": 5000, "sunflower": 2000, "canola": 1000, "sugarcane": 1000,
             "winter_wheat": 5000}

CACHE = ROOT / "reference" / "cache"


def log(msg):
    print(msg, flush=True)


def load_gazetteer():
    CACHE.mkdir(parents=True, exist_ok=True)
    zpath = CACHE / "2024_Gaz_counties_national.zip"
    if not zpath.exists():
        log("downloading Census Gazetteer ...")
        zpath.write_bytes(net.fetch(GAZ_URL, source="census", timeout=120))
    with zipfile.ZipFile(zpath) as z:
        name = [n for n in z.namelist() if n.endswith(".txt")][0]
        text = z.read(name).decode("latin-1")
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    header = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        rec = {header[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header))}
        if rec["USPS"] in STATES:
            out.append({
                "fips": rec["GEOID"], "state": rec["USPS"], "name": rec["NAME"],
                "lat": round(float(rec["INTPTLAT"]), 4), "lon": round(float(rec["INTPTLONG"]), 4),
                "aland_sqmi": round(float(rec["ALAND_SQMI"]), 1),
            })
    return out


def fetch_cdl(fips):
    cache = CACHE / "cdl" / f"CDL_{CDL_YEAR}_{fips}.csv"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    try:
        text = net.fetch_text(CDL_CACHE.format(year=CDL_YEAR, fips=fips), source="nass_cdl", timeout=60, retries=2)
    except net.FetchError:
        xml = net.fetch_text(CDL_SERVICE.format(year=CDL_YEAR, fips=fips), source="nass_cdl", timeout=120)
        url = xml.split("<returnURL>")[1].split("</returnURL>")[0]
        text = net.fetch_text(url, source="nass_cdl", timeout=60)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def parse_cdl(text):
    acres = {k: 0.0 for k in CDL_CODES}
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        code = int(parts[0])
        try:
            a = float(parts[3])
        except ValueError:
            continue
        for crop, codes in CDL_CODES.items():
            if code in codes:
                acres[crop] += a
    return {k: round(v) for k, v in acres.items()}


def select_counties(counties, coverage):
    by_state = {}
    for c in counties:
        by_state.setdefault(c["state"], []).append(c)
    totals = {}
    selected = {}
    for st, items in by_state.items():
        totals[st] = {}
        for crop in CDL_CODES:
            if crop not in STATE_CROPS.get(st, CDL_CODES):
                continue
            total = sum(c["acres"][crop] for c in items)
            totals[st][crop] = total
            if total <= 0:
                continue
            ranked = sorted(items, key=lambda c: c["acres"][crop], reverse=True)
            cum = 0.0
            for c in ranked:
                if c["acres"][crop] < MIN_ACRES[crop]:
                    break
                c.setdefault("share", {})[crop] = round(100.0 * c["acres"][crop] / total, 2)
                if cum / total < coverage:
                    selected.setdefault(c["fips"], c).setdefault("selected_for", []).append(crop)
                cum += c["acres"][crop]
    return list(selected.values()), totals


def region_label(c, bounds):
    x0, y0, x1, y1 = bounds
    fx = (c["lon"] - x0) / max(1e-6, (x1 - x0))
    fy = (c["lat"] - y0) / max(1e-6, (y1 - y0))
    ns = "north" if fy > 2 / 3 else ("south" if fy < 1 / 3 else "")
    ew = "east" if fx > 2 / 3 else ("west" if fx < 1 / 3 else "")
    if ns and ew:
        return f"{ns}{ew}"
    if ns:
        return f"{ns}-central"
    if ew:
        return f"{ew}-central"
    return "central"


def nws_lookup(c):
    try:
        p = net.fetch_json(f"{NWS}/points/{c['lat']},{c['lon']}", accept="application/geo+json",
                           source="nws_points", timeout=30)["properties"]
        info = {"grid": p["gridId"], "x": p["gridX"], "y": p["gridY"],
                "zone": (p.get("forecastZone") or "").rsplit("/", 1)[-1],
                "county_zone": (p.get("county") or "").rsplit("/", 1)[-1],
                "tz": p.get("timeZone")}
        try:
            st = net.fetch_json(p["observationStations"], params={"limit": 1}, accept="application/geo+json",
                                source="nws_points", timeout=30)
            if st.get("features"):
                sp = st["features"][0]["properties"]
                info["station"] = sp["stationIdentifier"]
                info["station_name"] = sp.get("name")
        except net.FetchError as e:
            info["station_error"] = str(e)
        return c["fips"], info
    except Exception as e:  # noqa: BLE001
        return c["fips"], {"error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", type=float, default=0.8)
    ap.add_argument("--skip-nws", action="store_true")
    args = ap.parse_args()

    gaz = load_gazetteer()
    log(f"gazetteer counties in {len(STATES)} states: {len(gaz)}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        texts = list(ex.map(lambda c: (c["fips"], fetch_cdl(c["fips"])), gaz))
    cdl = {f: parse_cdl(t) for f, t in texts}
    for c in gaz:
        c["acres"] = cdl[c["fips"]]
    # full acreage table (all counties) for transparency
    with open(ROOT / "reference" / f"cdl_{CDL_YEAR}_county_acreage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fips", "state", "county", "corn_ac", "soybeans_ac", "sunflower_ac", "canola_ac", "sugarcane_ac",
                    "winter_wheat_ac"])
        for c in sorted(gaz, key=lambda x: x["fips"]):
            a = c["acres"]
            w.writerow([c["fips"], c["state"], c["name"], a["corn"], a["soybeans"], a["sunflower"], a["canola"],
                        a["sugarcane"], a["winter_wheat"]])

    selected, totals = select_counties(gaz, args.coverage)
    bounds = {}
    for st in STATES:
        pts = [c for c in gaz if c["state"] == st]
        bounds[st] = (min(c["lon"] for c in pts), min(c["lat"] for c in pts),
                      max(c["lon"] for c in pts), max(c["lat"] for c in pts))
    for c in selected:
        c["region"] = region_label(c, bounds[c["state"]])
        c["crops"] = [k for k, _ in sorted(((k, v) for k, v in c["acres"].items() if v >= MIN_ACRES[k]),
                                           key=lambda kv: kv[1], reverse=True)]
        c["share"] = {k: round(100.0 * c["acres"][k] / totals[c["state"]][k], 2)
                      for k in c["acres"] if totals[c["state"]][k] > 0 and c["acres"][k] > 0}
    log(f"selected counties: {len(selected)}")
    for st in STATES:
        n = sum(1 for c in selected if c["state"] == st)
        log(f"  {st}: {n} counties; state CDL acres: " + ", ".join(f"{k}={v:,}" for k, v in totals[st].items() if v))

    if not args.skip_nws:
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = dict(ex.map(nws_lookup, selected))
        bad = 0
        for c in selected:
            c["nws"] = res.get(c["fips"], {})
            if "error" in c["nws"]:
                bad += 1
        log(f"NWS point lookups failed: {bad}")

    doc = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": (f"Counties ranked by USDA NASS Cropland Data Layer {CDL_YEAR} acreage per crop; kept until "
                   f"{int(args.coverage * 100)}% of each state's CDL acreage of that crop is covered, with minimum "
                   f"county acreage thresholds. Weather is sampled at the Census county internal point."),
        "coverage": args.coverage,
        "min_acres": MIN_ACRES,
        "cdl_codes": {k: sorted(v) for k, v in CDL_CODES.items()},
        "sources": [
            {"name": f"USDA NASS Cropland Data Layer {CDL_YEAR} (CropScape county statistics)",
             "url": "https://nassgeodata.gmu.edu/CropScape/"},
            {"name": "U.S. Census Bureau 2024 Gazetteer Files (counties)",
             "url": "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html"},
            {"name": "National Weather Service API (/points, /stations)", "url": "https://www.weather.gov/documentation/services-web-api"},
        ],
        "state_totals_acres": totals,
        "counties": sorted(selected, key=lambda c: (c["state"], c["name"])),
    }
    out = ROOT / "config" / "counties.json"
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    log(f"wrote {out}")


if __name__ == "__main__":
    main()