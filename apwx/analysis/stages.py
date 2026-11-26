"""Crop development / exposure model.

Never assumes every field is at the same stage: each (state, crop) gets a
distribution over phases (e.g. 6% mature but unharvested, 94% harvested).

Basis, in order of preference:
  1. USDA NASS weekly Crop Progress for that state and crop (if the latest report is
     recent enough and the crop is reported for the state);
  2. a typical calendar (USDA NASS Agricultural Handbook 628 usual planting/harvest dates,
     extension crop development timelines) - labelled "typical calendar" with lower confidence.
"""
from datetime import date, timedelta


def _md(year, mmdd):
    m, d = (int(x) for x in mmdd.split("-"))
    return date(year, m, d)


def window_fraction(d, window):
    """Cumulative fraction (0..1) of a seasonal operation (planting/harvest) completed by date d.

    window = [begin, most_active_begin, most_active_end, end] as 'MM-DD'. Windows that
    cross the new year (e.g. sugarcane harvest Sep-Jan) are handled.
    """
    if not window:
        return None
    b, mb, me, e = window
    for year in (d.year - 1, d.year):
        db, dmb, dme, de = (_md(year, x) for x in (b, mb, me, e))
        # roll dates forward so they are increasing
        seq = [db]
        for x in (dmb, dme, de):
            while x < seq[-1]:
                x = date(x.year + 1, x.month, x.day)
            seq.append(x)
        db, dmb, dme, de = seq
        if db <= d <= de + timedelta(days=200):
            if d <= db:
                return 0.0
            if d >= de:
                return 1.0
            pts = [(db, 0.0), (dmb, 0.15), (dme, 0.85), (de, 1.0)]
            for (d1, f1), (d2, f2) in zip(pts, pts[1:]):
                if d1 <= d <= d2:
                    span = (d2 - d1).days or 1
                    return f1 + (f2 - f1) * (d - d1).days / span
    # before the season starts this year
    return 0.0


def _calendar_for(crop_cfg, state):
    cal = crop_cfg.get("calendar", {})
    return cal.get(state) or cal.get("default")


def _progress_metrics_to_cumulative(metrics, sequence):
    """NASS percent values -> cumulative fractions for each milestone (monotone non-increasing)."""
    cum = {}
    prev = 1.0
    for m in sequence:
        v = metrics.get(m, {}).get("this_week") if m in metrics else None
        if v is None:
            continue
        frac = max(0.0, min(prev, v / 100.0))
        cum[m] = frac
        prev = frac
    return cum


def _phases_from_cumulative(cum, phase_defs):
    """phase_defs: {phase: [start_milestone_or_null, end_milestone_or_null]}"""
    out = {}
    for phase, (start, end) in phase_defs.items():
        s = 1.0 if start is None else cum.get(start)
        e = 0.0 if end is None else cum.get(end)
        if s is None or e is None:
            continue
        out[phase] = max(0.0, s - e)
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


def calendar_cumulative(crop_cfg, state, d):
    cal = _calendar_for(crop_cfg, state)
    if not cal:
        return None
    cum = {}
    seq_offsets = crop_cfg.get("milestone_offsets_days", {})
    plant = cal.get("plant")
    harvest = cal.get("harvest")
    prev = 1.0
    for m in crop_cfg.get("nass_sequence", []):
        if m == "planted":
            v = window_fraction(d, plant)
        elif m == "harvested":
            v = window_fraction(d, harvest)
        elif m in seq_offsets and plant:
            v = window_fraction(d - timedelta(days=seq_offsets[m]), plant)
        else:
            continue
        if v is None:
            continue
        v = max(0.0, min(prev, v))
        cum[m] = v
        prev = v
    # harvest cannot exceed maturity; enforce monotonic order already handled by prev
    return cum


def estimate(crop_id, crop_cfg, state, d, progress=None, max_age_days=10):
    """Return a stage estimate dict for (state, crop) on date d."""
    model = crop_cfg.get("stage_model", "progress")
    result = {"crop": crop_id, "state": state, "basis": None, "as_of": None, "phases": {}, "detail": "",
              "confidence": "low", "model": model}
    if model == "progress":
        rec = (progress or {}).get((state, crop_id))
        if rec and rec.get("week_ending"):
            we = date.fromisoformat(rec["week_ending"])
            age = (d - we).days
            cum = _progress_metrics_to_cumulative(rec["metrics"], crop_cfg.get("nass_sequence", []))
            # A NASS report that only lists a late milestone (e.g. harvested) still implies earlier ones = 100%.
            if cum:
                seq = crop_cfg.get("nass_sequence", [])
                last_idx = max(seq.index(k) for k in cum)
                for k in seq[:last_idx]:
                    cum.setdefault(k, 1.0)
                # fill later milestones from the calendar only if missing and earlier ones complete
                cal_cum = calendar_cumulative(crop_cfg, state, d) or {}
                for k in seq[last_idx + 1:]:
                    if k not in cum and k in cal_cum:
                        cum[k] = min(cum[seq[last_idx]], cal_cum[k])
                if age <= max_age_days or _season_over(cum):
                    result.update(basis="nass", as_of=rec["week_ending"], confidence="high" if age <= 8 else "medium",
                                  phases=_phases_from_cumulative(cum, crop_cfg["phases"]),
                                  detail=_nass_detail(crop_cfg, rec), source_url=rec.get("source_url"))
                    result["cumulative"] = cum
                    return result
        cum = calendar_cumulative(crop_cfg, state, d)
        if cum:
            result.update(basis="calendar", as_of=d.isoformat(), confidence="low",
                          phases=_phases_from_cumulative(cum, crop_cfg["phases"]),
                          detail=_calendar_detail(crop_cfg, state, cum))
            result["cumulative"] = cum
        return result
    if model == "sugarcane":
        cal = _calendar_for(crop_cfg, state)
        h = window_fraction(d, cal["harvest"])
        in_season = _in_window(d, cal["harvest"])
        growth = cal.get("grand_growth")
        if in_season:
            phases = {"standing_unharvested": 1.0 - h, "harvested_stubble": h}
            detail = (f"Harvest season ({_window_text(cal['harvest'])}); about {round(100 * (1 - h))}% of cane estimated "
                      f"still standing by typical calendar (not an observed report).")
        elif growth and _in_window(d, growth):
            phases = {"grand_growth": 1.0}
            detail = "Grand growth period (typical calendar)."
        else:
            phases = {"stubble_dormant": 1.0}
            detail = "Between harvest and spring regrowth: ratoon stubble and plant cane in the field (typical calendar)."
        result.update(basis="calendar", as_of=d.isoformat(), confidence="low", phases=phases, detail=detail)
        return result
    if model in ("winter_canola", "winter_wheat"):
        cal = _calendar_for(crop_cfg, state)
        phases = {}
        for phase, window in cal.get("phase_windows", {}).items():
            if _in_simple_window(d, window):
                phases[phase] = 1.0
        if not phases:
            phases = {"off_season": 1.0}
        name = next(iter(phases))
        result.update(basis="calendar", as_of=d.isoformat(), confidence="low", phases=phases,
                      detail=f"{crop_cfg.get('label', crop_id).capitalize() if model == 'winter_wheat' else 'Winter canola'} typical calendar: "
                             f"{crop_cfg.get('phase_labels', {}).get(name, name)}.")
        return result
    return result


def _calendar_detail(crop_cfg, state, cum):
    cal = _calendar_for(crop_cfg, state) or {}
    h, p = cum.get("harvested"), cum.get("planted")
    src = "USDA usual dates" if crop_cfg.get("calendar_source", "").startswith("S01") and state in crop_cfg.get("calendar", {}) else "typical agronomic window"
    if h is not None and h >= 0.98:
        return f"Harvest normally complete by now (typical calendar; {src}: harvest ends ~{_window_text(cal['harvest']).split(' - ')[1]}); not an observed report."
    if h is not None and h > 0.0:
        return f"About {round(h * 100)}% harvested by typical calendar ({src}: {_window_text(cal['harvest'])}); not an observed report."
    if p is not None and p < 0.98:
        return f"About {round(p * 100)}% planted by typical calendar ({src}: {_window_text(cal['plant'])}); not an observed report."
    return f"In season, before harvest (typical calendar; {src}); not an observed report."


def _season_over(cum):
    return cum.get("harvested", 0) >= 0.97


def _in_window(d, window):
    f = window_fraction(d, window)
    return f is not None and 0.0 < f < 1.0


def _in_simple_window(d, window):
    a, b = window
    da, db = _md(d.year, a), _md(d.year, b)
    if da <= db:
        return da <= d <= db
    return d >= da or d <= db


def _window_text(window):
    import calendar
    b, _, _, e = window
    bm, bd = (int(x) for x in b.split("-"))
    em, ed = (int(x) for x in e.split("-"))
    return f"{calendar.month_abbr[bm]} {bd} - {calendar.month_abbr[em]} {ed}"


def _nass_detail(crop_cfg, rec):
    parts = []
    for m in reversed(crop_cfg.get("nass_sequence", [])):
        v = rec["metrics"].get(m)
        if v and v.get("this_week") is not None:
            avg = v.get("avg_5yr")
            ly = v.get("last_year")
            s = f"{int(v['this_week'])}% {m.replace('_', ' ')}"
            extras = []
            if v.get("last_week") is not None:
                extras.append(f"prior week {int(v['last_week'])}%")
            if avg is not None:
                extras.append(f"5-yr avg {int(avg)}%")
            if ly is not None:
                extras.append(f"last year {int(ly)}%")
            if extras:
                s += " (" + ", ".join(extras) + ")"
            parts.append(s)
            break
    return f"USDA NASS Crop Progress, week ending {rec['week_ending']}: " + ("; ".join(parts) if parts else "reported")


def exposure(stage, crop_cfg, hazard):
    """Sum of phase fractions weighted by the crop's vulnerability to the hazard in each phase."""
    hz = crop_cfg.get("hazards", {}).get(hazard) or {}
    weights = hz.get("phase_weights", {})
    model = (stage or {}).get("model")
    if model and model in hz.get("phase_weights_by_model", {}):
        weights = hz["phase_weights_by_model"][model]
    if not stage or not stage.get("phases"):
        return 0.0, []
    total = 0.0
    contrib = []
    for phase, frac in stage["phases"].items():
        w = weights.get(phase, 0.0)
        if w > 0 and frac > 0:
            total += frac * w
            contrib.append((phase, frac, w))
    contrib.sort(key=lambda x: x[1] * x[2], reverse=True)
    return round(total, 3), contrib