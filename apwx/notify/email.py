"""Email delivery - the platform's only notification channel.

Providers (choose with EMAIL_PROVIDER):
  smtp    any SMTP server: Gmail / Google Workspace (app password), Microsoft 365,
          Brevo, Mailjet, Amazon SES SMTP, ...
  resend  Resend HTTP API (https://resend.com)

All secrets come from environment variables (GitHub Actions secrets in the cloud):
  ALERT_RECIPIENT   recipient address (required; never hard-coded, never logged)
  EMAIL_FROM        sender address (defaults to SMTP_USERNAME for SMTP)
  SMTP_HOST, SMTP_PORT (587), SMTP_USERNAME, SMTP_PASSWORD, SMTP_SECURITY (starttls|ssl)
  RESEND_API_KEY

If configuration is missing or APWX_DRY_RUN=1, the message is written to the outbox
folder and the status is 'dry-run'. A dry run is never reported as delivered.
"""
import json
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from .. import http as net
from ..config import ROOT, env, env_flag
from ..timeutil import utcnow

RESEND_URL = "https://api.resend.com/emails"
LOGO_CID = "ap-logo"


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask(addr):
    if not addr or "@" not in addr:
        return "(unset)"
    return "***@***"


def scrub(text, s=None):
    """Remove addresses and credentials from error text before it reaches logs or the database
    (both are public in a public GitHub repository)."""
    if not text:
        return text
    for key in ("smtp_password", "resend_key"):
        secret = (s or {}).get(key)
        if secret:
            text = text.replace(secret, "***")
    return EMAIL_RE.sub("***@***", text)


def settings_from_env():
    recipient = env("ALERT_RECIPIENT")
    provider = (env("EMAIL_PROVIDER") or "").lower()
    if not provider:
        provider = "resend" if env("RESEND_API_KEY") else ("smtp" if env("SMTP_HOST") else "")
    s = {
        "recipient": recipient, "provider": provider, "dry_run": env_flag("APWX_DRY_RUN"),
        "from_addr": env("EMAIL_FROM") or env("SMTP_USERNAME"), "from_name": env("EMAIL_FROM_NAME"),
        "smtp_host": env("SMTP_HOST"), "smtp_port": int(env("SMTP_PORT", "587")),
        "smtp_user": env("SMTP_USERNAME"), "smtp_password": env("SMTP_PASSWORD"),
        "smtp_security": (env("SMTP_SECURITY") or "starttls").lower(), "resend_key": env("RESEND_API_KEY"),
    }
    missing = []
    if not recipient:
        missing.append("ALERT_RECIPIENT")
    if provider == "smtp":
        for k, name in (("smtp_host", "SMTP_HOST"), ("smtp_user", "SMTP_USERNAME"), ("smtp_password", "SMTP_PASSWORD")):
            if not s[k]:
                missing.append(name)
    elif provider == "resend":
        if not s["resend_key"]:
            missing.append("RESEND_API_KEY")
        if not s["from_addr"]:
            missing.append("EMAIL_FROM")
    else:
        missing.append("EMAIL_PROVIDER (smtp or resend)")
    s["missing"] = missing
    return s


def describe():
    """Non-secret description of the email configuration, for logs and status pages."""
    s = settings_from_env()
    return {"provider": s["provider"] or None, "recipient": mask(s["recipient"]), "dry_run": s["dry_run"],
            "configured": not s["missing"], "missing": s["missing"]}


def _logo_bytes(cfg):
    rel = cfg.get("email", "logo_file", default=None) if cfg else None
    if not rel:
        return None
    p = ROOT / rel
    return p.read_bytes() if p.exists() else None


def build_message(subject, html, text, sender, recipient, logo=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="ap-weather-intelligence")
    msg["X-AP-Weather"] = "automated"
    msg.set_content(text)
    if logo:
        msg.add_alternative(html, subtype="html")
        msg.get_payload()[1].add_related(logo, maintype="image", subtype="png", cid=f"<{LOGO_CID}>")
    else:
        msg.add_alternative(html.replace(f"cid:{LOGO_CID}", ""), subtype="html")
    return msg


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def write_outbox(cfg, subject, html, text, kind, reason):
    out = cfg.outbox_dir
    out.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    base = out / f"{stamp}_{kind}_{_slug(subject)}"
    base.with_suffix(".html").write_text(html, encoding="utf-8")
    base.with_suffix(".txt").write_text(f"Subject: {subject}\nStatus: DRY RUN ({reason})\n\n{text}", encoding="utf-8")
    return str(base.with_suffix(".html"))


def _send_smtp(s, msg):
    ctx = ssl.create_default_context()
    if s["smtp_security"] == "ssl":
        with smtplib.SMTP_SSL(s["smtp_host"], s["smtp_port"], context=ctx, timeout=60) as server:
            server.login(s["smtp_user"], s["smtp_password"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(s["smtp_host"], s["smtp_port"], timeout=60) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(s["smtp_user"], s["smtp_password"])
            server.send_message(msg)


def _send_resend(s, subject, html, text, sender, logo_url=None):
    body = {"from": sender, "to": [s["recipient"]], "subject": subject, "html": html, "text": text}
    if logo_url:
        body["html"] = html.replace(f"cid:{LOGO_CID}", logo_url)
    net.fetch(RESEND_URL, json_body=body, headers={"Authorization": f"Bearer {s['resend_key']}"},
              source="email_resend", timeout=60, retries=2)


def send(cfg, subject, html, text, kind="report"):
    """Send one email. Returns dict(status, provider, error, outbox)."""
    s = settings_from_env()
    if s["dry_run"] or s["missing"]:
        reason = "APWX_DRY_RUN=1" if s["dry_run"] else "missing " + ", ".join(s["missing"])
        path = write_outbox(cfg, subject, html, text, kind, reason)
        return {"status": "dry-run", "provider": None, "error": reason, "outbox": path}
    name = s["from_name"] or cfg.get("email", "from_name", default="American Prime Weather Intelligence")
    sender = formataddr((name, s["from_addr"] or s["smtp_user"]))
    last = None
    for attempt in range(3):
        try:
            if s["provider"] == "smtp":
                msg = build_message(subject, html, text, sender, s["recipient"], _logo_bytes(cfg))
                _send_smtp(s, msg)
            else:
                logo_url = (cfg.dashboard_url.rstrip("/") + "/assets/ap-logo-white.png") if cfg.dashboard_url else None
                _send_resend(s, subject, html, text, sender, logo_url)
            return {"status": "sent", "provider": s["provider"], "error": None, "outbox": None}
        except Exception as e:  # noqa: BLE001
            last = scrub(f"{type(e).__name__}: {str(e)[:300]}", s)
            time.sleep(3 * (attempt + 1))
    return {"status": "failed", "provider": s["provider"], "error": last, "outbox": None}