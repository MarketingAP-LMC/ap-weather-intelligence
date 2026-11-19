# Operations

## Schedule (all times U.S. Central)

| Job | When | Notes |
|---|---|---|
| Official NWS alert poll | every run (cron every 10 min) | immediate email for new/upgraded/expanded CRITICAL or HIGH alerts in monitored crop counties |
| Forecast cycle | every 3 h | 574 county forecasts + SPC/WPC/CPC + station observations; immediate email for new or materially changed CRITICAL/HIGH events |
| NASS Crop Progress check | every 6 h | weekly report Mondays ~3 pm CT, April-late November |
| Drought Monitor, PRISM observed | every 12 h | Drought Monitor map valid Tuesday, released Thursday |
| SPC storm reports | every 30 min | preliminary, shown as unverified |
| Daily report + content opportunities | 6:30 AM | sent once per day; caught up later if the slot was missed |
| Weekly outlook | Monday 7:00 AM | once per ISO week |
| Maintenance | after 3:00 AM | retention pruning, VACUUM |

Change times and thresholds in `config/settings.json` (schedule, notification policy, official alert levels) and `config/crops.json` (crop rules, stage calendars, citations). Changes take effect on the next run after they are pushed to the repository.

## Running jobs on demand

Actions -> *AP Weather Intelligence* -> Run workflow:
- `force` = `cycle` (new forecast cycle now), `daily`, `weekly`, `dashboard` or a comma list;
- **send test email** sends a clearly labelled delivery test.
A forced daily or weekly report that was already sent is regenerated on the dashboard but not emailed twice (de-duplication key per day/week).

## Monitoring health

- Dashboard -> **Data sources**: last success and last error per source, product issue times, latency, recent scheduler runs, email configuration (provider, configured yes/no; never the address).
- Every email lists data sources with publication and retrieval times; the header of the dashboard shows the age of the last forecast cycle (green < 4 h, amber < 8 h, red otherwise).
- GitHub Actions run history keeps logs for each run; failed runs are emailed to the repository owner by GitHub if notifications are enabled.

## Duplicate prevention and follow-ups

- Official alerts: one notification per VTEC event; updates of the same warning are emailed again only if the level rises or two or more monitored counties are added.
- Forecast events: one notification when an event first reaches HIGH/CRITICAL; follow-ups on upgrade (immediately) or material change (area +25%, timing >= 12 h earlier, or intensification beyond tolerance) after a 6-hour cooldown.
- Reports: one per day / week.
- All new items detected in one run are combined in one email.

## Recovery

- Source outage: recorded and reported; other sources keep working; a county whose forecast fails reuses its previous forecast (up to 6.5 h, flagged stale).
- Missed scheduler runs: the next run catches up the forecast cycle, daily and weekly reports.
- Email failure: retried on later runs up to 6 attempts; status visible in Alerts -> Email log.
- Database: the 5 most recent snapshots are kept as release assets (`state-db` release). To roll back, delete newer assets; the next run restores the newest remaining one. Restores are integrity-checked.

## Changing the monitored counties

`python tools/build_reference.py --coverage 0.8` rebuilds `config/counties.json` from the latest CDL year configured in the script (`CDL_YEAR`), the Census Gazetteer and NWS point lookups. Commit/upload the new file.

## Changing the recipient or provider

Update the GitHub secrets only (`ALERT_RECIPIENT`, `EMAIL_PROVIDER`, SMTP_* or RESEND_API_KEY). No code change is needed.

## Costs

Public repository: GitHub Actions and Pages at no cost. Email: Gmail/Workspace SMTP is included with the mailbox; Resend's free tier covers 3,000 emails/month. Expected volume: ~1 daily + 1 weekly + occasional immediate alerts and content emails.