"""Email-safe HTML building blocks (inline styles, table layout) in American Prime branding.

Brand palette (American Prime Brand Style Guide): Navy #1b395f, Prime Green #13a959,
Royal Blue #1c1ec2, Jet Black #0f0f0f, Slate Gray #888888. Typeface: Helvetica.
"""
import html as _html
import re

from ..notify.email import LOGO_CID

NAVY = "#1b395f"
GREEN = "#13a959"
BLUE = "#1c1ec2"
JET = "#0f0f0f"
SLATE = "#888888"
BG = "#f3f5f8"
BORDER = "#dce2ea"
FONT = "Helvetica, 'Helvetica Neue', Arial, sans-serif"

LEVEL_STYLE = {
    "CRITICAL": ("#b42318", "#ffffff"),
    "HIGH": ("#e8590c", "#ffffff"),
    "MODERATE": ("#f5b400", "#2b1f00"),
    "INFORMATIONAL": ("#3b6fb6", "#ffffff"),
    "NONE": ("#e6ebf1", "#4b5563"),
}


def esc(s):
    return _html.escape("" if s is None else str(s))


def badge(level, small=False):
    bg, fg = LEVEL_STYLE.get(level or "NONE", LEVEL_STYLE["NONE"])
    size = "10px" if small else "11px"
    return (f'<span style="display:inline-block;padding:2px 7px;border-radius:3px;background:{bg};color:{fg};'
            f'font-size:{size};font-weight:700;letter-spacing:.04em;font-family:{FONT};white-space:nowrap">'
            f'{esc(level or "NONE")}</span>')


def p(text, size=14, color=JET, margin="0 0 10px", raw=False):
    body = text if raw else esc(text)
    return f'<p style="margin:{margin};font-size:{size}px;line-height:1.5;color:{color};font-family:{FONT}">{body}</p>'


def small(text, raw=False):
    return p(text, size=12, color="#5b6573", raw=raw)


def h2(text):
    return (f'<h2 style="margin:22px 0 8px;font-size:17px;line-height:1.3;color:{NAVY};font-family:{FONT};'
            f'border-bottom:2px solid {GREEN};padding-bottom:4px">{esc(text)}</h2>')


def h3(text, raw=False):
    return (f'<h3 style="margin:14px 0 6px;font-size:15px;color:{JET};font-family:{FONT}">'
            f'{text if raw else esc(text)}</h3>')


def ul(items, raw=False, size=14):
    lis = "".join(f'<li style="margin:0 0 5px">{i if raw else esc(i)}</li>' for i in items if i)
    return (f'<ul style="margin:0 0 10px 18px;padding:0;font-size:{size}px;line-height:1.45;color:{JET};'
            f'font-family:{FONT}">{lis}</ul>')


def link(url, text=None):
    return f'<a href="{esc(url)}" style="color:{BLUE};text-decoration:underline">{esc(text or url)}</a>'


def table(headers, rows, raw_cols=(), widths=None, size=12):
    th = "".join(
        f'<th style="text-align:left;padding:6px 8px;background:{NAVY};color:#fff;font-size:11px;font-weight:700;'
        f'font-family:{FONT};{("width:" + widths[i] + ";") if widths and i < len(widths) and widths[i] else ""}">'
        f'{esc(h)}</th>' for i, h in enumerate(headers))
    trs = []
    for ri, row in enumerate(rows):
        bg = "#ffffff" if ri % 2 == 0 else "#f7f9fb"
        tds = "".join(
            f'<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:{size}px;vertical-align:top;'
            f'font-family:{FONT};color:{JET}">{cell if (ci in raw_cols) else esc(cell)}</td>'
            for ci, cell in enumerate(row))
        trs.append(f'<tr style="background:{bg}">{tds}</tr>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
            f'style="border-collapse:collapse;margin:4px 0 12px;border:1px solid {BORDER}">'
            f'<tr>{th}</tr>{"".join(trs)}</table>')


def card(inner, level=None, pad=14):
    bar = LEVEL_STYLE.get(level, (GREEN, ""))[0] if level else GREEN
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 14px;'
            f'border:1px solid {BORDER};border-left:5px solid {bar};background:#ffffff">'
            f'<tr><td style="padding:{pad}px;font-family:{FONT}">{inner}</td></tr></table>')


def callout(text, color=NAVY, bg="#eef3f9", raw=False):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 12px">'
            f'<tr><td style="padding:10px 12px;background:{bg};border-left:4px solid {color};font-size:13px;'
            f'line-height:1.5;font-family:{FONT};color:{JET}">{text if raw else esc(text)}</td></tr></table>')


def kv(rows):
    trs = "".join(
        f'<tr><td style="padding:3px 10px 3px 0;font-size:12px;color:#5b6573;vertical-align:top;white-space:nowrap;'
        f'font-family:{FONT}">{esc(k)}</td><td style="padding:3px 0;font-size:13px;color:{JET};font-family:{FONT}">'
        f'{v}</td></tr>' for k, v in rows if v not in (None, ""))
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 10px">{trs}</table>'


def tile_map(levels, all_states, caption=None):
    """Email-safe tile-grid map of the U.S.; monitored states colored by risk level."""
    grid = {}
    for abbr, s in all_states.items():
        r, c = s["tile"]
        grid[(r, c)] = abbr
    rows = []
    for r in range(8):
        cells = []
        for c in range(12):
            abbr = grid.get((r, c))
            if not abbr:
                cells.append('<td style="width:34px;height:30px;padding:0"></td>')
                continue
            if abbr in levels:
                bg, fg = LEVEL_STYLE.get(levels[abbr] or "NONE", LEVEL_STYLE["NONE"])
                if levels[abbr] in (None, "NONE"):
                    bg, fg = "#d9f2e4", "#0b5c33"
                weight = "700"
            else:
                bg, fg, weight = "#f1f3f6", "#b4bcc6", "400"
            cells.append(f'<td style="width:34px;height:30px;padding:0;text-align:center;background:{bg};color:{fg};'
                         f'font-size:11px;font-weight:{weight};font-family:{FONT};border:2px solid #ffffff">{abbr}</td>')
        rows.append(f'<tr>{"".join(cells)}</tr>')
    legend = " ".join(f'{badge(lv, True)}' for lv in ("CRITICAL", "HIGH", "MODERATE", "INFORMATIONAL"))
    legend += (f' <span style="display:inline-block;padding:2px 7px;border-radius:3px;background:#d9f2e4;color:#0b5c33;'
               f'font-size:10px;font-weight:700;font-family:{FONT}">NO ALERT</span>')
    cap = small(caption) if caption else ""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:6px 0">'
            f'{"".join(rows)}</table><div style="margin:4px 0 8px">{legend}</div>{cap}')


def banner(text, kind="sim"):
    colors = {"sim": ("#fff3c4", "#7a5a00", "#f5b400"), "dry": ("#e8f1fb", "#1b395f", "#3b6fb6"),
              "warn": ("#fde8e7", "#8a1c14", "#b42318")}
    bg, fg, bd = colors.get(kind, colors["dry"])
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr><td style="padding:10px 14px;'
            f'background:{bg};color:{fg};border:2px dashed {bd};font-size:13px;font-weight:700;text-align:center;'
            f'font-family:{FONT}">{esc(text)}</td></tr></table>')


def page(cfg, title, subtitle, body, preheader="", top_banner=None, dashboard_url=None, sources_note=None):
    name = cfg.get("platform_name", default="AP Weather Intelligence")
    org = cfg.get("org_name", default="American Prime")
    dash = dashboard_url or cfg.dashboard_url
    dash_link = link(dash, "Open the AP Weather Intelligence dashboard") if dash else ""
    dash_html = (f'<p style="margin:10px 0 0;font-size:12px;font-family:{FONT}">{dash_link}</p>') if dash else ""
    banner_html = top_banner or ""
    src = sources_note or ("Data: NOAA National Weather Service (forecasts, alerts, observations), Storm Prediction "
                           "Center, Weather Prediction Center, Climate Prediction Center, U.S. Drought Monitor, NOAA "
                           "Regional Climate Centers (RCC-ACIS / PRISM), USDA NASS (Crop Progress, Cropland Data Layer).")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title></head>
<body style="margin:0;padding:0;background:{BG}">
<span style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(preheader)}</span>
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:{BG}"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" cellpadding="0" cellspacing="0" width="680" style="max-width:680px;width:100%;background:#ffffff;border:1px solid {BORDER}">
<tr><td style="background:{NAVY};padding:16px 20px">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
<td style="width:54px;vertical-align:middle"><img src="cid:{LOGO_CID}" width="46" height="46" alt="AP" style="display:block;border:0"></td>
<td style="vertical-align:middle;font-family:{FONT}"><div style="color:#ffffff;font-size:12px;letter-spacing:.14em;font-weight:700">{esc(org.upper())}</div>
<div style="color:#cfe3d8;font-size:12px;letter-spacing:.06em">{esc(name)}</div></td>
<td style="text-align:right;vertical-align:middle;font-family:{FONT};color:#cfd8e3;font-size:11px">{esc(subtitle)}</td></tr></table>
</td></tr>
<tr><td style="height:4px;background:{GREEN};line-height:4px;font-size:0">&nbsp;</td></tr>
<tr><td style="padding:18px 20px 6px">{banner_html}
<h1 style="margin:10px 0 6px;font-size:21px;line-height:1.25;color:{JET};font-family:{FONT}">{esc(title)}</h1>
{body}
</td></tr>
<tr><td style="padding:14px 20px 18px;background:#f7f9fb;border-top:1px solid {BORDER}">
<p style="margin:0 0 6px;font-size:11px;line-height:1.5;color:#5b6573;font-family:{FONT}"><b>How to read this:</b> risk levels describe <i>potential</i> crop impacts estimated from official forecasts, outlooks and observations. They are not confirmed crop damage. Field conditions vary within counties; growth stages are estimated from USDA reports or typical calendars. For life-safety decisions always follow official National Weather Service products.</p>
<p style="margin:0;font-size:11px;line-height:1.5;color:#5b6573;font-family:{FONT}">{esc(src)}</p>
{dash_html}
<p style="margin:8px 0 0;font-size:11px;color:{SLATE};font-family:{FONT}">{esc(org)} &middot; {esc(name)} &middot; automated message</p>
</td></tr></table></td></tr></table></body></html>"""


def to_text(html):
    """Plain-text alternative for the multipart email."""
    t = re.sub(r"(?is)<(script|style|head).*?</\1>", "", html)
    t = re.sub(r"(?is)<span style=\"display:none.*?</span>", "", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|h1|h2|h3|tr|table)>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "- ", t)
    t = re.sub(r"(?i)</li>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", " | ", t)
    t = re.sub(r'(?is)<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()