"""Time helpers.

All internal timestamps are timezone-aware UTC. Reports display U.S. Central
Time (American Prime is based in Oklahoma). When the IANA database is not
available (Windows without the tzdata package) a built-in U.S. DST rule is used.
"""
import re
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc

try:  # pragma: no cover - depends on platform
    from zoneinfo import ZoneInfo

    _CT = ZoneInfo("America/Chicago")
except Exception:  # noqa: BLE001
    _CT = None


def utcnow():
    return datetime.now(UTC).replace(microsecond=0)


def _nth_sunday(year, month, n):
    d = date(year, month, 1)
    offset = (6 - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def us_dst_window_utc(year, std_offset_hours=-6):
    """Return (start_utc, end_utc) of U.S. daylight time for a zone with the given standard offset."""
    start_local = datetime.combine(_nth_sunday(year, 3, 2), time(2, 0))
    end_local = datetime.combine(_nth_sunday(year, 11, 1), time(2, 0))
    start = (start_local - timedelta(hours=std_offset_hours)).replace(tzinfo=UTC)
    end = (end_local - timedelta(hours=std_offset_hours + 1)).replace(tzinfo=UTC)
    return start, end


def to_ct(dt):
    """Convert an aware (or naive UTC) datetime to U.S. Central Time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if _CT is not None:
        return dt.astimezone(_CT)
    u = dt.astimezone(UTC)
    start, end = us_dst_window_utc(u.year, -6)
    if start <= u < end:
        return u.astimezone(timezone(timedelta(hours=-5), "CDT"))
    return u.astimezone(timezone(timedelta(hours=-6), "CST"))


def ct_date(dt):
    return to_ct(dt).date()


def ct_midnight_utc(d):
    """UTC instant of local midnight (Central) starting calendar date d."""
    guess = datetime.combine(d, time(6, 0)).replace(tzinfo=UTC)
    for _ in range(3):
        local = to_ct(guess)
        delta = datetime.combine(d, time(0, 0)) - local.replace(tzinfo=None)
        if delta == timedelta(0):
            break
        guess = guess + delta
    return guess


def fmt_ct(dt, with_date=True, with_tz=True):
    """Human format in Central Time, e.g. 'Thu Nov 19, 9:05 AM CST'."""
    if dt is None:
        return "n/a"
    d = to_ct(dt)
    hour12 = d.hour % 12 or 12
    ampm = "AM" if d.hour < 12 else "PM"
    clock = f"{hour12}:{d.minute:02d} {ampm}"
    out = f"{d:%a} {d:%b} {d.day}, {clock}" if with_date else clock
    if with_tz:
        out += f" {d.tzname()}"
    return out


def fmt_day(dt_or_date):
    if isinstance(dt_or_date, datetime):
        d = to_ct(dt_or_date)
    else:
        d = dt_or_date
    return f"{d:%a} {d:%b} {d.day}"


def iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    s = str(s).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(s):
    m = _DUR.match(s.strip())
    if not m:
        raise ValueError(f"unsupported ISO8601 duration: {s}")
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=se)


def parse_valid_time(vt):
    """NWS validTime '2026-11-19T08:00:00+00:00/PT3H' -> (start_utc, end_utc)."""
    start_s, dur_s = vt.split("/", 1)
    start = parse_iso(start_s)
    return start, start + parse_duration(dur_s)


def iso_week_key(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def hours_between(a, b):
    return (b - a).total_seconds() / 3600.0


def epoch_ms_to_dt(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)