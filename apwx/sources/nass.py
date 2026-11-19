"""USDA NASS weekly Crop Progress (national report, state-level rows).

Text report: https://release.nass.usda.gov/reports/progWWYY.txt (WW = ISO week of the
Sunday the report covers). Released Mondays ~4 pm ET, April through late November.
"""
import re
from datetime import datetime, timedelta

from .. import http as net

URL = "https://release.nass.usda.gov/reports/prog{ww:02d}{yy:02d}.txt"
LINK = "https://www.nass.usda.gov/Publications/National_Crop_Progress/"

STATE_NAMES = {
    "Oklahoma": "OK", "North Dakota": "ND", "South Dakota": "SD", "Kansas": "KS", "Nebraska": "NE",
    "Minnesota": "MN", "Iowa": "IA", "Illinois": "IL", "Indiana": "IN", "Ohio": "OH", "Louisiana": "LA",
    "Florida": "FL",
}
CROPS = {"Corn": "corn", "Soybeans": "soybeans", "Sunflowers": "sunflower", "Canola": "canola"}
TITLE_ANY = re.compile(r"^(.+?)\s+-\s+Selected States")
CROP_TITLE = re.compile(r"^(Corn|Soybeans|Sunflowers|Canola)\s+(.+)$")
ROW = re.compile(r"^([A-Z][A-Za-z .]+?)\s*\.{2,}\s*:(.*)$")
RELEASED = re.compile(r"Released\s+([A-Z][a-z]+ \d{1,2}, \d{4})")
NUM = re.compile(r"\(NA\)|NA|-|\d+(?:\.\d+)?")


def classify_title(title):
    """Map a table title to what we store; None for tables we ignore (resets the parser)."""
    t = title.strip()
    m = CROP_TITLE.match(t)
    if m:
        metric = m.group(2).strip().lower()
        return ("crop", CROPS[m.group(1)], "condition" if metric.startswith("condition") else metric.replace(" ", "_"))
    if t.startswith("Topsoil Moisture"):
        return ("moisture", "topsoil", None)
    if t.startswith("Subsoil Moisture"):
        return ("moisture", "subsoil", None)
    if t.startswith("Days Suitable for Fieldwork"):
        return ("fieldwork", "fieldwork", "days_suitable")
    return None


def candidate_urls(today, back_weeks=4):
    """URLs for the most recent report weeks, newest first."""
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    out = []
    for k in range(back_weeks):
        sunday = last_sunday - timedelta(days=7 * k)
        iy, iw, _ = sunday.isocalendar()
        out.append((sunday, URL.format(ww=iw, yy=iy % 100)))
    return out


def _nums(s):
    """NASS conventions: '-' represents zero, (NA) / NA = not available."""
    vals = []
    for tok in NUM.findall(s):
        if tok == "-":
            vals.append(0.0)
        elif "NA" in tok:
            vals.append(None)
        else:
            vals.append(float(tok))
    return vals


def parse_report(text, source_url=""):
    m = RELEASED.search(text)
    released = datetime.strptime(m.group(1), "%B %d, %Y").date() if m else None
    week_ending = (released - timedelta(days=1)) if released else None
    rows = []
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        t = TITLE_ANY.match(line.strip())
        if t:
            current = classify_title(t.group(1))
            continue
        if current is None:
            continue
        r = ROW.match(line.strip())
        if not r:
            continue
        name = r.group(1).strip()
        st = STATE_NAMES.get(name)
        if not st:
            continue
        vals = _nums(r.group(2))
        kind, crop, metric = current
        if kind == "crop" and metric != "condition" and len(vals) >= 4:
            rows.append({"released": str(released), "week_ending": str(week_ending), "state": st, "crop": crop,
                         "metric": metric, "last_year": vals[0], "last_week": vals[1], "this_week": vals[2],
                         "avg_5yr": vals[3], "source_url": source_url})
        elif kind == "crop" and metric == "condition" and len(vals) >= 5:
            for label, v in zip(("cond_vp", "cond_p", "cond_f", "cond_g", "cond_ex"), vals[:5]):
                rows.append({"released": str(released), "week_ending": str(week_ending), "state": st, "crop": crop,
                             "metric": label, "this_week": v, "source_url": source_url})
        elif kind == "fieldwork" and len(vals) >= 3:
            rows.append({"released": str(released), "week_ending": str(week_ending), "state": st, "crop": "fieldwork",
                         "metric": "days_suitable", "last_year": vals[0], "last_week": vals[1], "this_week": vals[2],
                         "source_url": source_url})
        elif kind == "moisture" and len(vals) >= 4:
            for label, v in zip(("very_short", "short", "adequate", "surplus"), vals[:4]):
                rows.append({"released": str(released), "week_ending": str(week_ending), "state": st, "crop": crop,
                             "metric": label, "this_week": v, "source_url": source_url})
    return {"released": released, "week_ending": week_ending, "rows": rows}


def fetch_latest(today):
    """Fetch the newest available weekly report; returns parsed dict or None."""
    for sunday, url in candidate_urls(today):
        try:
            text = net.fetch_text(url, encoding="latin-1", source="nass_crop_progress", timeout=45, retries=1)
        except net.FetchError as e:
            if e.status == 404:
                continue
            raise
        parsed = parse_report(text, url)
        if parsed["rows"]:
            parsed["url"] = url
            return parsed
    return None