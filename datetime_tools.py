"""Date/time tools so the agent works in India time (Asia/Kolkata) instead of Composio's UTC.

- current_datetime: what "now", "today" and "this year" mean for the user.
- resolve_date_range: turns "16 nov", "yesterday", "1 nov to 15 nov" into exact IST bounds plus the UTC / epoch
  values app APIs expect. A date without a year means the current year.
- to_local_time: converts UTC timestamps from app results (ISO strings or epoch seconds/ms) to IST for display.
"""
import os
import re
from datetime import date, datetime, time, timedelta, timezone

from dateutil import parser as dateparser
from langchain_core.tools import tool

TZ_NAME = os.getenv("AGENT_TIMEZONE", "Asia/Kolkata")
try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(TZ_NAME)  # needs the `tzdata` package on Windows
except Exception:  # IST has no DST, so a fixed offset is exact for Asia/Kolkata
    TZ = timezone(timedelta(hours=5, minutes=30), "IST")


def now() -> datetime:
    return datetime.now(TZ)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_day(text: str, today: date | None = None) -> date:
    """Parse one day. Missing year -> current year; missing month -> current month. Day-first, as in India (16/11)."""
    today = today or now().date()
    t = text.strip().lower()
    relative = {"today": 0, "yesterday": -1, "tomorrow": 1, "day before yesterday": -2}
    if t in relative:
        return today + timedelta(days=relative[t])
    if m := re.fullmatch(r"(\d+)\s+days?\s+ago", t):
        return today - timedelta(days=int(m.group(1)))
    default = datetime.combine(today, time())  # fills in whatever the text leaves out (year, month)
    return dateparser.parse(text, default=default, dayfirst=True, fuzzy=True).date()


@tool
def current_datetime() -> dict:
    """Current date and time in the user's time zone (India, Asia/Kolkata). Call this before interpreting
    'today', 'yesterday', 'this week', 'last month' or a date given without a year."""
    n = now()
    return {
        "timezone": TZ_NAME,
        "local": n.strftime("%A, %d %B %Y, %I:%M %p"),
        "local_iso": n.isoformat(timespec="seconds"),
        "date": n.date().isoformat(),
        "year": n.year,
        "utc_iso": _iso_utc(n),
        "epoch_seconds": int(n.timestamp()),
    }


@tool
def resolve_date_range(start: str, end: str | None = None) -> dict:
    """Turn a user's date or date range into exact bounds in India time (Asia/Kolkata), plus UTC equivalents.

    Use this before filtering emails, files or events by date. Dates without a year (e.g. '16 nov') are in the
    current year. Day-first numeric dates ('16/11') are supported, as are 'today', 'yesterday', 'N days ago'.

    Args:
        start: First day, e.g. '16 nov', '2026-11-16', 'yesterday'.
        end: Last day, inclusive. Omit for a single day.

    Returns `days` (each date with its correct weekday label, e.g. 'Fri, 13 Nov'), the whole-day window
    [start 00:00 IST, day after end 00:00 IST) as IST ISO, UTC ISO and epoch seconds, plus ready-made filters: `gmail_query` (Gmail `q` operators, epoch based so they are exact in IST) and
    `drive_query` (Google Drive `q` on modifiedTime).
    """
    first = parse_day(start)
    last = parse_day(end) if end else first
    if last < first:
        first, last = last, first
    lo = datetime.combine(first, time(), TZ)
    hi = datetime.combine(last + timedelta(days=1), time(), TZ)
    lo_epoch, hi_epoch = int(lo.timestamp()), int(hi.timestamp())
    span = (last - first).days + 1
    days = [first + timedelta(days=i) for i in range(span)] if span <= 31 else []
    return {
        # Exact weekday labels for each day in the range. Use these; never work out weekdays yourself.
        "days": [{"date": d.isoformat(), "label": d.strftime("%a, %d %b").replace(" 0", " ")} for d in days],
        "timezone": TZ_NAME,
        "label": first.strftime("%d %b %Y") + ("" if first == last else " to " + last.strftime("%d %b %Y")),
        "start_local": lo.isoformat(),
        "end_local_exclusive": hi.isoformat(),
        "start_utc": _iso_utc(lo),
        "end_utc_exclusive": _iso_utc(hi),
        "start_epoch": lo_epoch,
        "end_epoch_exclusive": hi_epoch,
        "gmail_query": f"after:{lo_epoch} before:{hi_epoch}",
        "drive_query": f"modifiedTime >= '{_iso_utc(lo)}' and modifiedTime < '{_iso_utc(hi)}'",
    }


@tool
def to_local_time(timestamps: list[str]) -> dict:
    """Convert timestamps from app results (UTC ISO strings like '2026-11-16T04:10:00Z', RFC 2822 email dates, or
    epoch seconds/milliseconds such as Gmail's internalDate) to India time (Asia/Kolkata) for showing the user."""
    out = {}
    for raw in timestamps:
        s = str(raw).strip()
        try:
            if re.fullmatch(r"\d{9,13}", s):
                n = int(s)
                dt = datetime.fromtimestamp(n / 1000 if n > 10**11 else n, timezone.utc)
            else:
                dt = dateparser.parse(s)
                if dt.tzinfo is None:  # app APIs return UTC when no offset is given
                    dt = dt.replace(tzinfo=timezone.utc)
            out[s] = dt.astimezone(TZ).strftime("%d %b %Y, %I:%M %p %Z")
        except Exception as e:
            out[s] = f"could not parse: {e}"
    return out


DATETIME_TOOLS = [current_datetime, resolve_date_range, to_local_time]
