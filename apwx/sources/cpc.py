"""NOAA Climate Prediction Center 6-10 and 8-14 day temperature/precipitation outlooks.

Model-informed probabilistic outlooks (not deterministic forecasts): probability of
above-, near- or below-normal conditions relative to 1991-2020 normals.
"""
from ..geo import PolygonIndex
from ..timeutil import epoch_ms_to_dt, iso
from .arcgis import query_layer

LAYERS = {
    "t610": ("outlooks/cpc_6_10_day_outlk", 0), "p610": ("outlooks/cpc_6_10_day_outlk", 1),
    "t814": ("outlooks/cpc_8_14_day_outlk", 0), "p814": ("outlooks/cpc_8_14_day_outlk", 1),
}
LINKS = {"610": "https://www.cpc.ncep.noaa.gov/products/predictions/610day/",
         "814": "https://www.cpc.ncep.noaa.gov/products/predictions/814day/"}


def fetch_products():
    out = {}
    for key, (svc, layer) in LAYERS.items():
        link = LINKS[key[1:]]
        try:
            feats = query_layer(svc, layer, offset=0.05, source="cpc_outlooks")
            p = feats[0]["properties"] if feats else {}
            out[key] = {"index": PolygonIndex(feats), "issue": iso(epoch_ms_to_dt(p.get("fcst_date"))),
                        "start": iso(epoch_ms_to_dt(p.get("start_date"))),
                        "end": iso(epoch_ms_to_dt(p.get("end_date"))), "url": link}
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": str(e), "url": link}
    return out


def _norm_cat(cat):
    c = (cat or "").strip().lower()
    if c.startswith("above"):
        return "A"
    if c.startswith("below"):
        return "B"
    if c.startswith("normal") or c.startswith("near"):
        return "N"
    return "EC"


def point_values(products, lon, lat):
    vals = {}
    for key, prod in products.items():
        idx = prod.get("index")
        if idx is None:
            continue
        hits = idx.query(lon, lat)
        best = None
        for h in hits:
            prob = float(h.get("prob") or 0)
            if best is None or prob > best[1]:
                best = (_norm_cat(h.get("cat")), prob)
        vals[key] = [best[0], int(best[1])] if best else ["EC", 33]
    return vals


CAT_WORDS = {"A": "above normal", "B": "below normal", "N": "near normal", "EC": "equal chances"}