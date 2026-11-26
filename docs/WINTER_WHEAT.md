# Winter wheat (added 2026-11-26)

Winter wheat is now defined as a monitored crop (`winter_wheat`). It does **nothing on the live dashboard until the county list is rebuilt**, because `config/counties.json` and `reference/cdl_2025_county_acreage.csv` do not yet contain winter wheat acres.

## What changed

| File | Change |
|---|---|
| `config/crops.json` | New crop `winter_wheat`: calendar stage model, freeze and extreme-cold rules, reference S80 (K-State C646). |
| `apwx/analysis/stages.py` | The calendar phase-window model now serves `winter_wheat` as well as `winter_canola`. |
| `apwx/sources/nass.py` | Parses the "Winter Wheat ..." tables of the weekly Crop Progress report (planted, emerged, condition; headed and harvested in season). |
| `apwx/reports/common.py` | Report crop order includes winter wheat. |
| `tools/build_reference.py` | CDL classes for winter wheat (24, and double-crop 26, 225, 236, 238), minimum 5,000 acres per county, new CSV column. |
| `site/app.js` | The "Monitored counties" card counts states and crops from the data instead of the fixed text "12 states, 5 crops". |
| `tests/test_core.py` | Tests for the stage model, freeze exposure and NASS parsing (fixture: Crop Progress, Nov 16, 2026). |

## To finish (needs internet access and the owner)

1. Rebuild the county list on a machine with internet access:
   `python tools/build_reference.py` (downloads Census, CDL 2025 and NWS point data). Review the new counties and commit `config/counties.json` and the CSV.
2. Verify reference S80 (URL and the stage temperatures: jointing 24 F, boot 28 F, heading/flowering 30 F, milk/dough 28 F, tillering about 12 F, about 2 hours).
3. Verify the phase windows in `crops.json` (`calendar_source` says they are APPROXIMATE) against USDA usual planting/harvest dates (S01) and state extension calendars.
4. Decide whether to monitor more winter wheat states. Texas and Colorado are major winter wheat states but are not in `config/states.json`; adding them needs state entries, NASS state names and a county rebuild.
5. Other hazards (drought, heavy rain, heat at grain fill) are not defined for winter wheat yet; add them only with cited thresholds.

## Known limitation

The engine applies one temperature ladder to all phases and uses phase weights for sensitivity. A 30 F night during jointing can therefore raise a MODERATE event, even though K-State lists 24 F as the injury point at jointing. The weights (jointing 0.5) reduce, but do not remove, this effect.
