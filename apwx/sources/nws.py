"""National Weather Service API adapter (https://api.weather.gov).

Products used:
  * /alerts/active?area=..   official watches, warnings, advisories (CAP)
  * /gridpoints/{wfo}/{x},{y} raw NDFD gridded forecast (hourly temperature, QPF, snow,
                              ice, wind gust, PoP, thunder) out to ~7 days
  * /gridpoints/.../forecast  12-hour text forecast periods (used in alert emails)
  * /stations/{id}/observations/latest  observed conditions
"""
import re

from .. import http as net
from ..timeutil import parse_iso, parse_valid_time

BASE = "https://api.weather.gov"
GEO = "application/geo+json"

_VTEC = re.compile(r"/([OTEX])\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/")


def parse_vtec(vtec):
    if not vtec:
        return None
    m = _VTEC.search(vtec)
    if not m:
        return None
    pclass, action, office, phen, sig, etn, begin, end = m.groups()
    return {"class": pclass, "action": action, "office": office, "phen": phen, "sig": sig,
            "etn": etn, "begin": begin, "end": end}


def _first(params, key):
    v = params.get(key)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _num(s):
    if s is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(s))
    return float(m.group(0)) if m else None


def same_to_fips(codes):
    """SAME codes 'PSSCCC' -> 5-digit county FIPS 'SSCCC' (drops statewide/zone codes)."""
    out = set()
    for s in codes or []:
        s = str(s).strip()
        if len(s) == 6 and s.isdigit() and s[3:] != "000":
            out.add(s[1:])
    return sorted(out)


def fetch_active_alerts(states):
    doc = net.fetch_json(f"{BASE}/alerts/active", params={"area": ",".join(states)}, accept=GEO,
                         source="nws_alerts", timeout=30, retries=3)
    return doc.get("features", [])


def parse_alert(feature):
    p = feature.get("properties") or {}
    params = p.get("parameters") or {}
    geocode = p.get("geocode") or {}
    vtec = parse_vtec(_first(params, "VTEC"))
    fips = same_to_fips(geocode.get("SAME"))
    if vtec:
        track = f"{vtec['office']}.{vtec['phen']}.{vtec['sig']}.{vtec['etn']}"
    else:
        track = f"{p.get('event', '')}|{p.get('senderName', '')}|{(p.get('onset') or p.get('effective') or '')[:13]}"
    return {
        "id": p.get("id") or feature.get("id"),
        "url": feature.get("id") or f"{BASE}/alerts/{p.get('id')}",
        "event": p.get("event"),
        "severity": p.get("severity"),
        "certainty": p.get("certainty"),
        "urgency": p.get("urgency"),
        "status": p.get("status"),
        "message_type": p.get("messageType"),
        "sent": p.get("sent"),
        "effective": p.get("effective"),
        "onset": p.get("onset") or p.get("effective"),
        "expires": p.get("expires"),
        "ends": p.get("ends") or p.get("expires"),
        "headline": _first(params, "NWSheadline") or p.get("headline"),
        "full_headline": p.get("headline"),
        "description": p.get("description") or "",
        "instruction": p.get("instruction") or "",
        "area_desc": p.get("areaDesc") or "",
        "sender": p.get("senderName"),
        "fips": fips,
        "ugc": geocode.get("UGC") or [],
        "vtec": vtec,
        "track_key": track,
        "max_hail_in": _num(_first(params, "maxHailSize")),
        "max_wind_mph": _num(_first(params, "maxWindGust")),
        "tornado_detection": _first(params, "tornadoDetection"),
        "damage_threat": (_first(params, "thunderstormDamageThreat") or _first(params, "tornadoDamageThreat")
                          or _first(params, "flashFloodDamageThreat")),
        "references": [r.get("identifier") for r in (p.get("references") or []) if r.get("identifier")],
    }


def fetch_point(lat, lon):
    return net.fetch_json(f"{BASE}/points/{lat},{lon}", accept=GEO, source="nws_points", timeout=30)["properties"]


def fetch_gridpoint(grid, x, y, retries=3):
    return net.fetch_json(f"{BASE}/gridpoints/{grid}/{x},{y}", accept=GEO, source="nws_forecast",
                          timeout=45, retries=retries)["properties"]


def fetch_forecast_periods(grid, x, y):
    doc = net.fetch_json(f"{BASE}/gridpoints/{grid}/{x},{y}/forecast", accept=GEO, source="nws_forecast_text",
                         timeout=30, retries=2)
    return doc.get("properties", {}).get("periods", [])


def _val(obj, conv=None):
    if not isinstance(obj, dict):
        return None
    v = obj.get("value")
    if v is None:
        return None
    unit = obj.get("unitCode", "")
    if conv == "temp":
        return round(v * 9 / 5 + 32, 1) if unit.endswith("degC") else round(v, 1)
    if conv == "speed":
        return round(v / 1.609344, 1) if "km_h" in unit else round(v * 2.236936, 1) if "m_s" in unit else round(v, 1)
    if conv == "precip":
        return round(v / 25.4, 2) if unit.endswith("mm") else round(v * 39.3701, 2) if unit.endswith(":m") else v
    return round(v, 1) if isinstance(v, float) else v


def fetch_latest_obs(station):
    p = net.fetch_json(f"{BASE}/stations/{station}/observations/latest", accept=GEO, source="nws_obs",
                       timeout=30, retries=2)["properties"]
    return {
        "station": station,
        "time": p.get("timestamp"),
        "temp_f": _val(p.get("temperature"), "temp"),
        "dewpoint_f": _val(p.get("dewpoint"), "temp"),
        "wind_mph": _val(p.get("windSpeed"), "speed"),
        "gust_mph": _val(p.get("windGust"), "speed"),
        "rh": _val(p.get("relativeHumidity")),
        "precip_6h_in": _val(p.get("precipitationLast6Hours"), "precip"),
        "text": p.get("textDescription"),
    }


# ---------------------------------------------------------------- gridpoint parsing
def _conv(value, uom):
    if value is None:
        return None
    uom = uom or ""
    if uom.endswith("degC"):
        return value * 9.0 / 5.0 + 32.0
    if uom.endswith(":mm"):
        return value / 25.4
    if "km_h" in uom:
        return value / 1.609344
    if uom.endswith(":m"):
        return value
    return value


def layer_intervals(props, name):
    """[(start_utc, end_utc, value_in_us_units)] for a gridpoint layer, skipping nulls."""
    layer = props.get(name) or {}
    uom = layer.get("uom")
    out = []
    for v in layer.get("values") or []:
        val = v.get("value")
        if val is None or isinstance(val, (list, dict)):
            continue
        try:
            s, e = parse_valid_time(v["validTime"])
        except (KeyError, ValueError):
            continue
        out.append((s, e, _conv(val, uom)))
    return out


def hourly_series(intervals, start, hours):
    """Expand state-variable intervals (temperature, PoP, gust) into hourly samples.

    Returns a list of length `hours` (values or None) for hours start, start+1h, ...
    """
    out = [None] * hours
    for s, e, val in intervals:
        i0 = int((s - start).total_seconds() // 3600)
        i1 = int(-(-(e - start).total_seconds() // 3600))
        for i in range(max(0, i0), min(hours, i1)):
            out[i] = val
    return out


def window_sum(intervals, w_start, w_end):
    """Sum an accumulation layer (QPF, snow, ice) over [w_start, w_end), prorating partial overlaps."""
    total = 0.0
    covered = 0.0
    for s, e, val in intervals:
        span = (e - s).total_seconds()
        if span <= 0:
            continue
        overlap = (min(e, w_end) - max(s, w_start)).total_seconds()
        if overlap > 0:
            total += val * overlap / span
            covered += overlap
    return total, covered / 3600.0


def update_time(props):
    return parse_iso(props.get("updateTime"))