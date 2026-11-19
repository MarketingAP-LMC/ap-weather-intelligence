"""Query NOAA/NWS ArcGIS map services (mapservices.weather.noaa.gov) as GeoJSON."""
from .. import http as net

BASE = "https://mapservices.weather.noaa.gov/vector/rest/services"
# Envelope covering the 12 monitored states (lon/lat)
REGION_BBOX = (-106.0, 24.0, -79.5, 49.5)


def query_layer(service, layer, offset=0.02, bbox=REGION_BBOX, source="noaa_mapservices", timeout=90):
    params = {
        "where": "1=1", "outFields": "*", "f": "geojson", "outSR": "4326", "geometryPrecision": "3",
        "maxAllowableOffset": str(offset), "returnGeometry": "true",
        "geometry": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}", "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
    }
    doc = net.fetch_json(f"{BASE}/{service}/MapServer/{layer}/query", params=params, source=source,
                         timeout=timeout, retries=3)
    return doc.get("features", []) or []