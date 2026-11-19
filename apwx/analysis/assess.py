"""Crop-specific impact assessment and risk classification.

For every monitored county, each crop grown there (CDL acreage above the presence
threshold) is evaluated against each hazard rule in config/crops.json:

  1. Weather intensity level  - highest rule level whose conditions are all met by the
     county's metrics (NWS forecast, SPC/WPC outlooks, Drought Monitor, observations).
  2. Crop exposure            - share of the crop in phases vulnerable to the hazard
     (NASS Crop Progress or typical calendar), weighted by phase vulnerability.
  3. Adjustments              - low exposure lowers the level; forecasts more than
     `lead_cap_hours` away are capped (developing conditions = MODERATE).

Every assessment carries the reasons used, so each risk level is explainable.
"""
import re

from ..config import level_rank, shift_level
from .stages import exposure

OPS = {
    "<=": lambda a, b: a <= b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b, "==": lambda a, b: a == b,
}

SPC_NAMES = {0: "none", 1: "general thunderstorms", 2: "Marginal", 3: "Slight", 4: "Enhanced", 5: "Moderate", 6: "High"}
ERO_NAMES = {0: "none", 1: "Marginal", 2: "Slight", 3: "Moderate", 4: "High"}

METRIC_TEXT = [
    (r"^tmin(24|72|7d)$", "lowest forecast temperature ({w})", "F"),
    (r"^tmax(24|72|7d)$", "highest forecast temperature ({w})", "F"),
    (r"^hrs_le_(\d+)$", "hours at or below {t} F in the next 7 days", "h"),
    (r"^run_le_(\d+)$", "longest continuous stretch at or below {t} F", "h"),
    (r"^lead_le_(\d+)$", "hours until temperatures first reach {t} F", "h"),
    (r"^hrs_ge_(\d+)$", "hours at or above {t} F in the next 7 days", "h"),
    (r"^days_ge_(\d+)$", "days with a forecast high of {t} F or more", "d"),
    (r"^qpf(24|72|7d)$", "NWS forecast rainfall ({w})", "in"),
    (r"^wpc_qpf(72|7d)$", "WPC forecast rainfall ({w}, lower bound of band)", "in"),
    (r"^snow(72|7d)$", "forecast snowfall ({w})", "in"),
    (r"^ice72$", "forecast ice accumulation (72 h)", "in"),
    (r"^gust(72|7d)$", "highest forecast wind gust ({w})", "mph"),
    (r"^ero_max$", "WPC Excessive Rainfall Outlook (Days 1-3)", "ero"),
    (r"^spc_max$", "SPC convective outlook (Days 1-3)", "spc"),
    (r"^spc_hail$", "SPC probability of severe hail within 25 mi (Days 1-2)", "%"),
    (r"^spc_wind$", "SPC probability of damaging wind within 25 mi (Days 1-2)", "%"),
    (r"^spc_torn$", "SPC probability of a tornado within 25 mi (Days 1-2)", "%"),
    (r"^spc_sig$", "SPC significant-severe area (Day 1)", "flag"),
    (r"^dm_d(\d)$", "share of county in Drought Monitor D{t} or worse", "%"),
    (r"^dm_chg1$", "one-week change in Drought Severity and Coverage Index", "pts"),
    (r"^dm_chg4$", "four-week change in Drought Severity and Coverage Index", "pts"),
    (r"^obs_pcpn(\d+)$", "observed precipitation, last {t} days (PRISM via RCC-ACIS)", "in"),
    (r"^obs_mint7$", "lowest observed daily minimum, last 7 days (PRISM via RCC-ACIS)", "F"),
    (r"^cpc_(t|p)(610|814)_(above|below)$", "CPC {d}-day probability of {c} {v}", "%"),
]
WINDOW = {"24": "next 24 h", "72": "next 72 h", "7d": "next 7 days"}


def describe_metric(key, value):
    for pattern, template, unit in METRIC_TEXT:
        m = re.match(pattern, key)
        if not m:
            continue
        g = m.groups()
        label = template
        if "{w}" in label:
            label = label.replace("{w}", WINDOW.get(g[0], g[0]))
        if "{t}" in label:
            label = label.replace("{t}", g[0])
        if "{d}" in label:
            d = {"610": "6-10", "814": "8-14"}[g[1]]
            label = label.replace("{d}", d).replace("{v}", "temperature" if g[0] == "t" else "precipitation")
            label = label.replace("{c}", "above-normal" if g[2] == "above" else "below-normal")
        if unit == "F":
            return f"{label}: {value:.0f} F"
        if unit == "in":
            return f"{label}: {value:.2f} in"
        if unit == "spc":
            return f"{label}: {SPC_NAMES.get(int(value), value)}"
        if unit == "ero":
            return f"{label}: {ERO_NAMES.get(int(value), value)}"
        if unit == "flag":
            return label
        if unit == "%":
            return f"{label}: {value:.0f}%"
        return f"{label}: {value:g} {unit}"
    return f"{key}: {value}"


def eval_conditions(conds, f):
    for metric, op, value in conds:
        v = f.get(metric)
        if v is None or not OPS[op](v, value):
            return False
    return True


def intensity(levels, f):
    """Highest satisfied level spec (levels listed low -> high)."""
    best = None
    for spec in levels:
        ok = eval_conditions(spec.get("all", []), f)
        if ok and spec.get("any"):
            ok = any(eval_conditions([c], f) for c in spec["any"])
        if ok and (best is None or level_rank(spec["level"]) >= level_rank(best["level"])):
            best = spec
    return best


def _requirement_text(metric, op, value, cur):
    m = re.match(r"^(lead|run|hrs)_(le|ge)_(\d+)$", metric)
    if m:
        kind, rel, t = m.groups()
        cmp = f"at or {'below' if rel == 'le' else 'above'} {t} F"
        if kind == "lead":
            when = "not in the 7-day forecast" if cur is None else f"first expected in about {cur:g} h"
            return f"requires temperatures {cmp} within {value:g} h ({when})"
        unit = "consecutive hours" if kind == "run" else "hours"
        return f"requires at least {value:g} {unit} {cmp} (forecast: {0 if cur is None else cur:g} h)"
    m = re.match(r"^days_ge_(\d+)$", metric)
    if m:
        return f"requires at least {value:g} days with highs of {m.group(1)} F or more (forecast: {0 if cur is None else cur:g})"
    if metric.startswith("tmin"):
        return f"requires a forecast low {op} {value:g} F (lowest forecast: {'n/a' if cur is None else f'{cur:g} F'})"
    label = describe_metric(metric, value).split(": ")[0]
    return f"requires {label} {op} {value:g} (now {'n/a' if cur is None else f'{cur:g}'})"


def next_level_gap(levels, achieved, f):
    """Explain why the next level up was not reached (first unmet condition)."""
    higher = [s for s in levels if level_rank(s["level"]) > level_rank(achieved["level"])]
    if not higher:
        return None
    nxt = higher[0]
    for metric, op, value in nxt.get("all", []):
        v = f.get(metric)
        if v is None or not OPS[op](v, value):
            return f"Not {nxt['level']}: " + _requirement_text(metric, op, value, v)
    return None


def crops_present(county, crops_cfg):
    out = []
    for crop_id, acres in (county.get("acres") or {}).items():
        cfg = crops_cfg["crops"].get(crop_id)
        if cfg and acres >= cfg.get("presence_acres", 1000):
            out.append(crop_id)
    return out


def assess_county(county, snap, flat, stages, crops_cfg, risk_cfg):
    results = []
    state = county["state"]
    for crop_id in crops_present(county, crops_cfg):
        crop_cfg = crops_cfg["crops"][crop_id]
        if state in crop_cfg.get("exclude_states", []):
            continue
        stage = stages.get((state, crop_id))
        model = (stage or {}).get("model")
        for hazard_id, hz in crop_cfg.get("hazards", {}).items():
            levels = hz.get("levels_by_model", {}).get(model) or hz.get("levels", [])
            spec = intensity(levels, flat)
            if spec is None:
                continue
            exp, contrib = exposure(stage, crop_cfg, hazard_id)
            if exp < risk_cfg.get("exposure_none", 0.02):
                continue
            level = spec["level"]
            reasons = []
            met = [describe_metric(m, flat[m]) for m, _, _ in spec.get("all", []) if m in flat]
            reasons.append("Weather: " + "; ".join(met))
            phase_txt = ", ".join(f"{crop_cfg.get('phase_labels', {}).get(p, p)} ~{round(fr * 100)}%"
                                  for p, fr, w in contrib[:3])
            reasons.append(f"Crop exposure index {exp:.2f} ({phase_txt}; basis: {stage.get('basis') if stage else 'n/a'})")
            if exp < risk_cfg.get("exposure_low", 0.05):
                if level_rank(level) > level_rank("INFORMATIONAL"):
                    reasons.append("Level capped at INFORMATIONAL: very little of the crop is in a vulnerable stage")
                level = "INFORMATIONAL"
            elif exp < risk_cfg.get("exposure_partial", 0.25):
                new = shift_level(level, -1)
                if new != level:
                    reasons.append(f"Lowered one level ({level} to {new}): only part of the crop is in a vulnerable stage")
                level = new
            lead = flat.get(spec["lead"]) if spec.get("lead") else None
            cap_h = risk_cfg.get("lead_cap_hours", 72)
            cap_level = risk_cfg.get("lead_cap_level", "MODERATE")
            if lead is not None and lead > cap_h and level_rank(level) > level_rank(cap_level):
                reasons.append(f"Capped at {cap_level}: expected more than {cap_h} h away (forecast confidence lower)")
                level = cap_level
            if level == "NONE":
                continue
            gap = next_level_gap(levels, spec, flat)
            if gap:
                reasons.append(gap)
            if lead is None:
                conf = "medium"
            elif lead <= 48:
                conf = "high"
            elif lead <= 120:
                conf = "medium"
            else:
                conf = "low"
            if stage and stage.get("basis") == "calendar" and conf == "high":
                conf = "medium"
            results.append({
                "fips": county["fips"], "county": county["name"], "state": state, "crop": crop_id,
                "hazard": hazard_id, "intensity": spec["level"], "level": level, "exposure": exp,
                "phases": [[p, round(fr, 3), w] for p, fr, w in contrib],
                "stage_basis": stage.get("basis") if stage else None,
                "stage_detail": stage.get("detail") if stage else "",
                "lead_h": lead, "rule": spec.get("id") or spec["level"],
                "metrics": {m: flat.get(m) for m, _, _ in spec.get("all", []) if m in flat},
                "reasons": reasons, "confidence": conf, "acres": county["acres"].get(crop_id, 0),
            })
    return results