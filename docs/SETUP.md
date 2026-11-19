# Setup, deployment and credentials

Everything except the items below is already built and tested. These steps need accounts and secrets that only American Prime can create - nothing here has been created on your behalf, and no password, token or key is ever needed by Claude.

**Decided (Nov 19, 2026):** public repository on the GitHub account **MarketingAP-LMC**.

- Repository: `https://github.com/MarketingAP-LMC/ap-weather-intelligence`
- Dashboard after the first deploy: `https://marketingap-lmc.github.io/ap-weather-intelligence/` (already set as `dashboard_url` in `config/settings.json`)

## Required credentials and settings

| Item | Where you create it | Stored as |
|---|---|---|
| Repository `ap-weather-intelligence` (public) | github.com/new | - |
| Upload token (only to publish the code; delete afterwards) | GitHub settings, fine-grained token | Windows user variable `GITHUB_TOKEN`, set with `tools/set_github_token.py` |
| Email sending credentials (choose **one** option below) | your mail provider | GitHub Actions **secrets** |
| Recipient address (American Prime's growth mailbox) | - | secret `ALERT_RECIPIENT` |
| Optional: NWS contact User-Agent | - | variable `NWS_USER_AGENT` (e.g. `AmericanPrime-AgWeather/1.0 (contact: ...)`) |
| Optional: sender display name | - | variable `EMAIL_FROM_NAME` |

### Email option A - the growth mailbox's own SMTP (recommended if it is Google Workspace / Gmail)
1. Google account of the mailbox -> Security -> enable **2-Step Verification**, then create an **App password** (Workspace admins must allow app passwords).
2. Secrets: `EMAIL_PROVIDER` = `smtp`, `SMTP_HOST` = `smtp.gmail.com`, `SMTP_PORT` = `587`, `SMTP_SECURITY` = `starttls`, `SMTP_USERNAME` = the mailbox address, `SMTP_PASSWORD` = the app password, `EMAIL_FROM` = the mailbox address, `ALERT_RECIPIENT` = the mailbox address.
   Microsoft 365: `smtp.office365.com`, port 587, starttls (SMTP AUTH must be enabled for the mailbox). Other providers (Brevo, Mailjet, Amazon SES SMTP) work the same way.

### Email option B - Resend API
1. Create a Resend account with the growth mailbox and an API key. Without a verified domain Resend only delivers to the account owner's address, which is exactly the recipient here; sender `onboarding@resend.dev`.
2. Secrets: `EMAIL_PROVIDER` = `resend`, `RESEND_API_KEY`, `EMAIL_FROM` (verified sender or `onboarding@resend.dev`), `ALERT_RECIPIENT`.

## Deploy

1. **Create the repository:** open https://github.com/new while signed in as MarketingAP-LMC -> Repository name `ap-weather-intelligence` -> **Public** -> leave "Add a README" unticked -> *Create repository*.
2. **Create an upload token:** https://github.com/settings/personal-access-tokens/new -> name `ap-weather upload`, expiration 7 days -> Repository access: *Only select repositories* -> `ap-weather-intelligence` -> Repository permissions: **Contents: Read and write**, **Workflows: Read and write** (Metadata read-only is added automatically) -> *Generate token* -> copy it.
3. **Save the token on this laptop** (never paste it into a chat): in a terminal in this folder run
   `python tools\set_github_token.py` and paste the token at the hidden prompt.
4. **Upload the code:** `python tools\publish_to_github.py --repo MarketingAP-LMC/ap-weather-intelligence`, then remove the local token with `python tools\set_github_token.py --remove` (and delete the token on GitHub if you like). Browser alternative: Add file -> Upload files, in batches of up to 100 files, including `.github` and excluding `state/`, `outbox/` and `reference/cache/`.
5. **Secrets:** repository Settings -> Secrets and variables -> Actions -> *New repository secret* (list above).
6. **Pages:** Settings -> Pages -> Build and deployment -> Source: **GitHub Actions** (do this before the first run, otherwise the dashboard deploy step fails and must be re-run).
7. **First run:** Actions tab -> enable workflows if asked -> *AP Weather Intelligence* -> *Run workflow*, with `force` = `cycle,daily,weekly` and **send test email** ticked.
8. **Verify:** the run finishes green; the mailbox receives the TEST email, a daily report and a weekly outlook (forced) and any immediate alerts - check spam/junk the first time and mark as "not spam"; the dashboard opens at the URL above.
9. From then on the schedule runs every 10 minutes automatically. Dashboard -> *Data sources* shows source health and the last scheduler runs.

## What is public in a public repository

The code, the Actions run logs, the dashboard and the `state-db` release assets (database snapshots: weather data, risk assessments, reports and email texts) can be seen by anyone. Email addresses and credentials are never written to any of them: secrets stay in GitHub's encrypted store and are masked in logs, the recipient is never logged or stored, and email error messages are scrubbed of addresses before they are logged or saved.

## Run locally (optional)

Requires only Python 3.11+ (standard library). `$env:APWX_DRY_RUN='1'` (PowerShell) writes emails to `outbox/` instead of sending. To test real delivery locally, set the same variables as the secrets in your own shell and run `python -m apwx test-email`.

## Private dashboard (optional)

GitHub Pages sites are public. For a private dashboard, publish `state/docs` to Cloudflare Pages behind Cloudflare Access (free for small teams), or move the tick to Cloud Run behind Identity-Aware Proxy. Both need an additional account.