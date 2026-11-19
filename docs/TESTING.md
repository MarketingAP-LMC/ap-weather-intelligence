# Testing results and remaining limitations

Tested on Nov 19, 2026 on the development laptop (Windows 11, Python 3.13.17, standard library only).

## Results

| Area | Test | Result |
|---|---|---|
| Unit tests (24) | time zones and U.S. DST fallback; point-in-polygon with holes; NWS SAME/VTEC/alert parsing; hourly expansion and 6-hour accumulation prorating; metrics computed from NWS-format gridpoint data; NASS parser on the real Nov 16, 2026 Crop Progress report (incl. fieldwork days and soil moisture); crop stage model (NASS, calendar, sugarcane, winter canola) and exposure; sugarcane freeze CRITICAL/HIGH and "why not higher" text; 72-hour lead-time cap; low-exposure cap; notification logic (new, cooldown, upgrade, material update); official-alert classification (floors, exposure, LA hard freeze = CRITICAL, ignored event types); email dry-run outbox, MIME structure with inline logo, address masking, no hard-coded address in code or config; database upsert, de-duplication, reports, retention | **24/24 pass** |
| End-to-end (2) | daily/weekly scheduling incl. catch-up and send-once; full synthetic scenario through the real engine: HIGH notice then CRITICAL upgrade, exactly two immediate emails (no duplicates), SIMULATION labels in every subject and body, dashboard state CRITICAL | **2/2 pass** |
| Live connectivity (`python -m apwx selftest`) | NWS alerts, NWS gridpoints, SPC outlooks, WPC QPF/ERO, CPC outlooks, Drought Monitor, RCC-ACIS, NASS Crop Progress, SPC storm reports | **9/9 sources OK** |
| Live end-to-end run (real data, email dry-run) | 574/574 county forecasts; 37-40 NWS alerts fetched, 2 relevant to monitored crop counties; NASS 165 rows; Drought Monitor 5,958 county-weeks; PRISM 8,610 county-days; 153 crop assessments in 12 events; daily report, weekly outlook, content email and dashboard produced; full run 2 min 20 s | **pass** |
| Idempotency | second run right after: no new emails, no new cycle, no reports re-sent, no state upload, no dashboard rebuild (alert-only tick ~1 s) | **pass** |
| Forecast comparison | previous-cycle and 24-hour comparisons, "was/now" tables and event change lines verified in simulation (coldest low 25 -> 21 F, HIGH -> CRITICAL) | **pass** |
| Dashboard | rendered in a browser from live and simulated data: KPIs, county map (993 county shapes, 12 layers, crop/state/hazard filters, search), county panel (forecast hour, station observation, 72-h chart, 7-day table, outlooks, drought, crop stage, history), crop matrix, alerts, reports, content, source status; no console errors | **pass** |
| Database persistence | local SQLite across runs; 4.2 MB per cycle snapshot set (0.6 MB gzipped) | **pass (local)** |

Defects found during testing and fixed: NASS rows from unrelated tables credited to the previous crop table; daily ranking dominated by acreage; sugarcane flood exposure too high for a crop that tolerates temporary flooding; zero-level assessments cluttering outputs; spring-freeze rule applied to winter canola rosettes in the fall; several wording issues (plurals, duplicate figures, alarming hazard labels).

## Not yet tested (needs credentials only American Prime can create)

- **Real email delivery** (SMTP or Resend) - run the workflow with *send test email*.
- **Cloud scheduled execution** on GitHub Actions, **release-asset persistence** (`tools/state_store.py`) and **GitHub Pages deployment**.
- `tools/publish_to_github.py` (needs a repository and token).

The platform should not be described as operational until the first cloud run is green and the test email has been received.

## Remaining limitations

- **Alert latency** is bounded by polling: about 10-20 minutes on GitHub Actions (schedule every 10 min plus queueing; GitHub can delay or skip scheduled runs at peak times). See ARCHITECTURE.md for faster options.
- **Public dashboard** on GitHub Pages (no secrets or addresses are published).
- **Crop stage** is state-level (NASS) or calendar-based; NASS weekly reports pause from December to April. OK/LA/FL corn and soybeans, all canola and sugarcane always use calendars.
- **One forecast point per county**; microclimates (low spots) are not resolved.
- **Evidence gaps** (documented in METHODOLOGY.md): UF/IFAS sugarcane freeze categories, winter-canola lethal temperatures and snow cover, wind/hail size thresholds, sunflower/soybean heat thresholds.
- **Soil moisture** uses NASS topsoil/subsoil ratings (state level, weekly) and the Drought Monitor; no modelled soil-moisture grid is ingested.
- **Hashtags**: state tags (#OKag, #NDag...) are unverified and flagged in every content idea.