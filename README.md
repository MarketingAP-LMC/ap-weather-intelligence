# AP Weather Intelligence

Agricultural weather intelligence and early-warning platform for **American Prime**. It monitors official U.S. weather data for **soybeans, corn, canola, sunflowers and sugarcane** across **574 agricultural counties** in Oklahoma, North Dakota, South Dakota, Kansas, Nebraska, Minnesota, Iowa, Illinois, Indiana, Ohio, Louisiana and Florida. It detects meaningful changes, evaluates crop-specific impacts, classifies risk (CRITICAL / HIGH / MODERATE / INFORMATIONAL, each with a written reason) and delivers everything **by email**, with a web dashboard.

| Deliverable | Where |
|---|---|
| Immediate weather alerts (CRITICAL/HIGH) | `apwx/reports/immediate.py` - sent within the run that detects them |
| Daily agricultural weather intelligence report | `apwx/reports/daily.py` - 6:30 AM CT |
| Weekly agricultural outlook | `apwx/reports/weekly.py` - Mondays 7:00 AM CT |
| Agricultural content opportunities | `apwx/reports/content.py` - with the daily report when events warrant |
| Web dashboard (map, counties, crop risk, comparisons, forecast history, alerts, reports, content, source status) | `site/` + `apwx/dashboard/build.py`, published to GitHub Pages |
| Cloud deployment (scheduled, persistent, self-recovering) | `.github/workflows/ap-weather.yml`, `tools/state_store.py` |

**Status (Nov 19, 2026):** built and tested end to end on this laptop against live data (dry-run email). Cloud deployment and real email delivery are **waiting for credentials** that only American Prime can create - see [docs/SETUP.md](docs/SETUP.md). Target: public repository `MarketingAP-LMC/ap-weather-intelligence`, dashboard at `https://marketingap-lmc.github.io/ap-weather-intelligence/`. The system must not be considered operational until the first cloud run and a received test email have been confirmed.

## How it works (one paragraph)

Every 10 minutes a GitHub Actions job restores the SQLite database (stored as versioned GitHub Release assets), polls **NWS active alerts** for the 12 states and emails any new CRITICAL/HIGH alert affecting monitored crop counties. Every 3 hours it also pulls the **NWS gridded forecast** for each county, **SPC** convective outlooks, **WPC** rainfall forecasts and Excessive Rainfall Outlooks, **CPC** 6-10/8-14 day outlooks, the **U.S. Drought Monitor**, **PRISM observed precipitation** (NOAA RCC-ACIS) and **USDA NASS Crop Progress**. It estimates crop stage, evaluates crop- and stage-specific rules sourced from land-grant extension research, compares against previous forecasts, and notifies on new or materially changed risks without duplicates. Daily and weekly reports, content ideas and the dashboard are produced on schedule; missed runs are caught up automatically.

## Repository layout

```
apwx/                 platform package (Python standard library only)
  sources/            NWS, SPC, WPC, CPC, Drought Monitor, RCC-ACIS, NASS adapters
  analysis/           metrics, crop stages/exposure, assessment, events, official alerts
  reports/            email builders (immediate, daily, weekly, content) and HTML kit
  notify/email.py     SMTP / Resend delivery, dry-run outbox, retries
  dashboard/build.py  dashboard data export
  pipeline.py         tick orchestration (due jobs, catch-up, isolation, persistence flags)
  simulate.py         labelled SIMULATION scenarios through the real engine
config/               settings.json, crops.json (thresholds + citations), states.json, sources.json, counties.json
site/                 static dashboard (HTML/CSS/JS, d3 + us-atlas from CDN)
tools/                build_reference.py, state_store.py, publish_to_github.py
tests/                unit + end-to-end tests (python -m unittest discover -s tests)
samples/              labelled sample outputs (simulations and a live dry run)
docs/                 ARCHITECTURE, METHODOLOGY, SETUP, OPERATIONS, TESTING
```

## Commands

```bash
python -m apwx selftest                          # live connectivity check of every source
python -m apwx tick --force cycle,daily,weekly   # one full run (set APWX_DRY_RUN=1 to write emails to outbox/)
python -m apwx status                            # config, email setup (no secrets), runs, source health
python -m apwx test-email                        # send a labelled delivery test to ALERT_RECIPIENT
python -m apwx simulate --scenario all           # SIMULATION samples into samples/
python -m unittest discover -s tests -v          # tests
```

Documentation: [Architecture](docs/ARCHITECTURE.md) - [Methodology](docs/METHODOLOGY.md) - [Setup and credentials](docs/SETUP.md) - [Operations](docs/OPERATIONS.md) - [Testing and limitations](docs/TESTING.md).