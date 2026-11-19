"""Official NWS alert processing: agricultural relevance and risk classification.

Official warnings over monitored agricultural counties start from the level set in
config/settings.json (warnings = CRITICAL by default, watches = HIGH, advisories =
MODERATE). The level is lowered when no monitored crop in those counties is in a
stage vulnerable to the hazard (e.g. a Winter Storm Warning after harvest is complete),
but never below the event's configured floor (life-threatening warnings stay >= HIGH).
"""
from ..config import level_rank, max_level, shift_level
from .assess import crops_present
from .stages import exposure


def classify(alert, cfg, stages):
    rules = cfg.get("alerts", "event_rules", default={})
    rule = rules.get(alert["event"])
    if rule is None:
        return None
    monitored = [f for f in alert["fips"] if f in cfg.counties]
    if not monitored:
        return None
    hazard = rule.get("hazard", "general")
    base = rule["level"]
    floor = rule.get("floor", "INFORMATIONAL")
    risk = cfg.get("risk", default={})
    crops = {}
    for f in monitored:
        county = cfg.counties[f]
        for crop_id in crops_present(county, cfg.crops):
            crop_cfg = cfg.crops["crops"][crop_id]
            stage = stages.get((county["state"], crop_id))
            if hazard == "tropical":
                exp = max(exposure(stage, crop_cfg, "high_wind")[0], exposure(stage, crop_cfg, "heavy_rain")[0])
            elif hazard in ("general", "fire"):
                exp = max(exposure(stage, crop_cfg, "high_wind")[0], exposure(stage, crop_cfg, "harvest_rain")[0])
            else:
                exp = exposure(stage, crop_cfg, hazard)[0]
            c = crops.setdefault(crop_id, {"crop": crop_id, "exposure": 0.0, "counties": 0, "acres": 0,
                                           "stage_detail": stage.get("detail") if stage else ""})
            c["exposure"] = max(c["exposure"], exp)
            c["counties"] += 1
            c["acres"] += county["acres"].get(crop_id, 0)
    max_exp = max((c["exposure"] for c in crops.values()), default=0.0)
    level = base
    reasons = [f"Official NWS {alert['event']} covering {len(monitored)} monitored agricultural "
               f"count{'y' if len(monitored) == 1 else 'ies'} (base level {base})."]
    if alert.get("message_type") == "Cancel":
        level = "INFORMATIONAL"
        reasons.append("This message cancels the alert.")
    elif max_exp < risk.get("exposure_low", 0.05):
        level = max_level("INFORMATIONAL", floor)
        reasons.append("Monitored crops in these counties are not currently in a stage vulnerable to this hazard "
                       f"(max exposure index {max_exp:.2f}); level set to {level}.")
    elif max_exp < risk.get("exposure_partial", 0.25):
        level = max_level(shift_level(base, -1), floor)
        reasons.append(f"Only part of the crop is in a vulnerable stage (max exposure index {max_exp:.2f}); "
                       f"level lowered to {level}.")
    else:
        reasons.append(f"Crops in vulnerable stages are present (max exposure index {max_exp:.2f}).")
    destructive = cfg.get("alerts", "severe_tstorm_destructive", default={})
    if alert["event"] == "Severe Thunderstorm Warning":
        hail, wind = alert.get("max_hail_in"), alert.get("max_wind_mph")
        if (hail and hail >= destructive.get("hail_in", 1.75)) or (wind and wind >= destructive.get("wind_mph", 70)):
            reasons.append(f"Destructive-criteria storm (hail {hail or '-'} in, wind {wind or '-'} mph).")
            level = max_level(level, "HIGH")
    if alert["event"] == "Tornado Warning" and (alert.get("tornado_detection") or "").upper() == "OBSERVED":
        reasons.append("Tornado reported as OBSERVED by NWS.")
    return {"level": level, "base": base, "hazard": hazard, "monitored_fips": monitored, "crops": crops,
            "max_exposure": round(max_exp, 3), "reasons": reasons}


def track_needs_notify(track_row, alert, classification, immediate_levels):
    """Decide whether an alert version should trigger an immediate email."""
    if classification["level"] not in immediate_levels:
        return None
    if alert.get("message_type") == "Cancel":
        return None
    if track_row is None or not track_row.get("last_notified_utc"):
        return "new"
    if level_rank(classification["level"]) > level_rank(track_row.get("last_notified_level")):
        return "upgrade"
    prev = set((track_row.get("last_notified_fips") or "").split(",")) - {""}
    added = set(classification["monitored_fips"]) - prev
    if len(added) >= 2:
        return "expanded"
    vt = alert.get("vtec") or {}
    if vt.get("action") in ("UPG", "EXA", "EXB") and added:
        return "expanded"
    return None