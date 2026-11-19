"""NOAA Weather Prediction Center: QPF (72 h, 7 day) and Excessive Rainfall Outlook (Days 1-3).

Served as polygons through mapservices.weather.noaa.gov (precip/wpc_qpf, hazards/wpc_precip_hazards).
"""
from ..geo import PolygonIndex
from .arcgis import query_layer

QPF_SERVICE = "precip/wpc_qpf"
ERO_SERVICE = "hazards/wpc_precip_hazards"
QPF_LAYERS = {"qpf72": 9, "qpf7d": 11}
ERO_LAYERS = {"ero1": 0, "ero2": 1, "ero3": 2}
ERO_RANK = {"marginal": 1, "slight": 2, "moderate": 3, "high": 4}
ERO_NAMES = {0: "None", 1: "Marginal", 2: "Slight", 3: "Moderate", 4: "High"}
LINKS = {"qpf": "https://www.wpc.ncep.noaa.gov/qpf/qpf2.shtml", "ero": "https://www.wpc.ncep.noaa.gov/qpf/excessive_rainfall_outlook_ero.php"}


def _ero_rank(text):
    t = (text or "").lower()
    for k, v in ERO_RANK.items():
        if t.startswith(k):
            return v
    return 0


def fetch_products():
    out = {}
    for key, layer in QPF_LAYERS.items():
        try:
            feats = query_layer(QPF_SERVICE, layer, offset=0.02, source="wpc_qpf")
            p = feats[0]["properties"] if feats else {}
            out[key] = {"index": PolygonIndex(feats), "issue": p.get("issue_time"), "valid": p.get("valid_time"),
                        "url": LINKS["qpf"]}
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": str(e), "url": LINKS["qpf"]}
    for key, layer in ERO_LAYERS.items():
        try:
            feats = query_layer(ERO_SERVICE, layer, offset=0.02, source="wpc_ero")
            p = feats[0]["properties"] if feats else {}
            out[key] = {"index": PolygonIndex(feats), "issue": p.get("issue_time"), "valid": p.get("valid_time"),
                        "url": LINKS["ero"]}
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": str(e), "url": LINKS["ero"]}
    return out


def point_values(products, lon, lat):
    vals = {}
    for key, prod in products.items():
        idx = prod.get("index")
        if idx is None:
            continue
        hits = idx.query(lon, lat)
        if key.startswith("qpf"):
            vals[key] = max((float(h.get("qpf") or 0) for h in hits), default=0.0)
        else:
            vals[key] = max((_ero_rank(h.get("outlook")) for h in hits), default=0)
    return vals