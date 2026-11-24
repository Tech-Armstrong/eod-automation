"""Company holidays: one list for everyone, stored in MySQL table `holidays`.

Where they come from:
- the `holidays/` folder: every PDF / Excel / CSV / TXT file in it is read when the server starts (oldest file
  first, so the newest file for a year wins);
- HR upload in the web app (role "Human Resources" only): the file is saved into `holidays/` and loaded at once.

Loading a file replaces the holidays of every year that file covers, so uploading a corrected list just works.

Holidays are skipped like leave days: no nightly summary, not part of EOD/weekly/monthly reports, never "missing".

The parser is line based and handles the usual formats, e.g.
    "4. 16 February 2026 Monday Maha Shivaratri"     (the 2026 PDF)
    "16-02-2026, Maha Shivaratri"  "2026-02-16,Maha Shivaratri"  "Feb 16, 2026 - Maha Shivaratri"
"""
import csv
import io
import os
import re
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, extract, select

import db

FOLDER = Path(os.getenv("HOLIDAYS_DIR", Path(__file__).parent / "holidays"))
ALLOWED = {".pdf", ".xlsx", ".csv", ".txt"}
MAX_BYTES = 10 * 1024 * 1024

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)"

_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]+([A-Za-z]{3,9})[\s\-/.,]+(\d{4})\b")
_MONTH_DAY_YEAR = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{4})\b")  # day first, as in India


def _find_date(line: str) -> tuple[date, tuple[int, int]] | None:
    """First date in the line, and where it is. None if there's no valid date."""
    for rx, order in ((_DAY_MONTH_YEAR, "dmy_name"), (_MONTH_DAY_YEAR, "mdy_name"), (_ISO, "ymd"), (_NUMERIC, "dmy")):
        for m in rx.finditer(line):
            try:
                if order == "dmy_name":
                    month = MONTHS.get(m.group(2).lower())
                    d = date(int(m.group(3)), month, int(m.group(1))) if month else None
                elif order == "mdy_name":
                    month = MONTHS.get(m.group(1).lower())
                    d = date(int(m.group(3)), month, int(m.group(2))) if month else None
                elif order == "ymd":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                else:
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                d = None
            if d:
                return d, m.span()
    return None


def _clean_name(line: str, span: tuple[int, int]) -> str:
    """Line minus serial number, date and the weekday right next to the date (the 'Day' column).
    Weekday words elsewhere stay, so 'Good Friday' keeps its name."""
    before, after = line[:span[0]], line[span[1]:]
    before = re.sub(r"^\s*\d{1,3}[.)]?\s+", "", before)  # serial number
    before = re.sub(rf"\b{WEEKDAYS}\b\.?[\s,;|:\-–—]*$", "", before, flags=re.I)  # 'Monday, 16 Feb 2026'
    after = re.sub(rf"^[\s,;|:\-–—]*\b{WEEKDAYS}\b\.?", "", after, flags=re.I)  # '16 Feb 2026 Monday'
    rest = re.sub(r"[\s,;|:\-–—]+", " ", f"{before} {after}").strip(" .")
    return rest[:255] or "Holiday"


def parse_lines(lines: list[str]) -> list[tuple[date, str]]:
    """[(date, name)] from text lines, de-duplicated (first name wins)."""
    out: dict[date, str] = {}
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        found = _find_date(line)
        if found and found[0] not in out:
            out[found[0]] = _clean_name(line, found[1])
    return sorted(out.items())


def file_lines(filename: str, data: bytes) -> list[str]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [ln for page in reader.pages for ln in (page.extract_text() or "").splitlines()]
    if ext == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [c.strftime("%Y-%m-%d") if isinstance(c, (date, datetime)) else str(c)
                         for c in row if c is not None and str(c).strip()]
                lines.append(" , ".join(cells))
        return lines
    if ext in {".csv", ".txt"}:
        text = data.decode("utf-8-sig", errors="replace")
        if ext == ".csv":
            return [" , ".join(row) for row in csv.reader(io.StringIO(text))]
        return text.splitlines()
    raise ValueError(f"Use a PDF, Excel (.xlsx) or CSV file, not {ext or 'this file type'}.")


def parse_file(filename: str, data: bytes) -> list[tuple[date, str]]:
    if len(data) > MAX_BYTES:
        raise ValueError("That file is larger than 10 MB.")
    items = parse_lines(file_lines(filename, data))
    if not items:
        raise ValueError("No dates found in that file. Each holiday needs a date like 16 February 2026 or 16-02-2026.")
    return items


def save(items: list[tuple[date, str]], source: str, uploaded_by: str | None = None) -> dict:
    """Replace the holidays of every year in `items` with `items`. Returns {years, count}."""
    years = sorted({d.year for d, _ in items})
    t = db.holidays
    with db.engine().begin() as c:
        c.execute(delete(t).where(extract("year", t.c.holiday_date).in_(years)))
        for d, name in items:
            c.execute(t.insert().values(holiday_date=d, name=name, source=source[:255], uploaded_by=uploaded_by,
                                        created_at=db.utcnow()))
    return {"years": years, "count": len(items)}


def get(start: date, end: date) -> list[dict]:
    t = db.holidays
    with db.engine().connect() as c:
        rows = c.execute(select(t).where(t.c.holiday_date >= start, t.c.holiday_date <= end)
                         .order_by(t.c.holiday_date)).all()
    return [{"date": r.holiday_date, "name": r.name} for r in rows]


def name_on(d: date) -> str | None:
    found = get(d, d)
    return found[0]["name"] if found else None


def safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "_", Path(name).stem).strip(" ._") or "holidays"
    return f"{stem[:80]}{Path(name).suffix.lower()}"


def import_folder(folder: Path = FOLDER) -> list[str]:
    """Load every holiday file in the folder (oldest first). Returns one line per file, for the startup log."""
    if not folder.exists():
        return []
    report = []
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() in ALLOWED), key=lambda p: p.stat().st_mtime)
    for p in files:
        try:
            got = save(parse_file(p.name, p.read_bytes()), p.name)
            report.append(f"{p.name}: {got['count']} holidays for {', '.join(map(str, got['years']))}")
        except Exception as e:
            report.append(f"{p.name}: skipped ({e})")
    return report
