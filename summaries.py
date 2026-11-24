"""Daily email summaries per user, and a consolidated summary per month.

Tables (see db.py):
    daily_summaries    (user_id, summary_date)  -> summary      one row per user per day
    monthly_summaries  (user_id, month)         -> summary      built from that month's daily rows

How rows get there:
- The assistant calls `save_daily_summary` whenever it summarizes one day's emails (from the day rail or chat).
- `run_due_jobs()` (called every 30 minutes by server.py):
    * after DAILY_SUMMARY_HOUR (India time) it summarizes today for every signed-in user that has no row yet,
      and catches up yesterday if the server was off;
    * once a month is over, it consolidates that month's daily summaries into monthly_summaries.
Dates are shown as DD-MM-YYYY everywhere.
"""
import calendar
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from sqlalchemy import select, update

import company_holidays
import db
from datetime_tools import TZ, parse_day

log = logging.getLogger("summaries")

DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "23"))  # India time; nightly summary runs after this hour
AUTO_DAILY = os.getenv("AUTO_DAILY_SUMMARIES", "true").lower() not in {"0", "false", "no", "off"}
_job_lock = threading.Lock()


def fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def parse_month(text: str, today: date | None = None) -> date:
    """'11-2026', '2026-11', 'November 2026', 'nov' (this year), 'this month', 'last month' -> first day of month."""
    today = today or datetime.now(TZ).date()
    t = text.strip().lower()
    if t in {"this month", "current month"}:
        return month_start(today)
    if t in {"last month", "previous month"}:
        return month_start(month_start(today) - timedelta(days=1))
    parts = t.replace("/", "-").split("-")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        a, b = (int(p) for p in parts)
        year, month = (a, b) if a > 12 else (b, a)
        return date(year, month, 1)
    if any(ch.isalpha() for ch in t):  # 'November 2026', 'nov' -> read as the 1st of that month
        return month_start(parse_day(f"1 {text}", today))
    return month_start(parse_day(text, today))


# ---------- storage ----------
def _upsert(table, keys: dict, values: dict) -> None:
    with db.engine().begin() as c:
        db.ensure_user(c, keys["user_id"])
        cond = [table.c[k] == v for k, v in keys.items()]
        if c.execute(select(table).where(*cond)).first():
            c.execute(update(table).where(*cond).values(**values, updated_at=db.utcnow()))
        else:
            now = db.utcnow()
            c.execute(table.insert().values(**keys, **values, created_at=now, updated_at=now))


def save_daily(user_id: str, day: date, summary: str, source: str = "chat") -> None:
    _upsert(db.daily_summaries, {"user_id": user_id, "summary_date": day}, {"summary": summary.strip(), "source": source})


def get_daily(user_id: str, start: date, end: date) -> list[dict]:
    t = db.daily_summaries
    with db.engine().connect() as c:
        rows = c.execute(
            select(t).where(t.c.user_id == user_id, t.c.summary_date >= start, t.c.summary_date <= end)
            .order_by(t.c.summary_date)
        ).all()
    return [{"date": r.summary_date, "summary": r.summary, "source": r.source} for r in rows]


def save_monthly(user_id: str, month: date, summary: str, days_included: int) -> None:
    _upsert(db.monthly_summaries, {"user_id": user_id, "month": month_start(month)},
            {"summary": summary.strip(), "days_included": days_included})


def get_monthly(user_id: str, month: date) -> dict | None:
    t = db.monthly_summaries
    with db.engine().connect() as c:
        r = c.execute(select(t).where(t.c.user_id == user_id, t.c.month == month_start(month))).first()
    return {"month": r.month, "summary": r.summary, "days_included": r.days_included} if r else None


# ---------- leave days ----------
def get_leave(user_id: str, start: date, end: date) -> list[date]:
    t = db.leave_days
    with db.engine().connect() as c:
        rows = c.execute(select(t.c.leave_date).where(t.c.user_id == user_id, t.c.leave_date >= start,
                                                      t.c.leave_date <= end).order_by(t.c.leave_date)).all()
    return [r.leave_date for r in rows]


def is_leave(user_id: str, d: date) -> bool:
    return bool(get_leave(user_id, d, d))


def set_leave(user_id: str, start: date, end: date | None = None, on: bool = True) -> list[date]:
    """Mark (on=True) or unmark every day from start to end. Returns the days changed."""
    from sqlalchemy import delete

    end = end or start
    start, end = min(start, end), max(start, end)
    if (end - start).days > 92:
        raise ValueError("Mark at most 3 months of leave at a time.")
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    t = db.leave_days
    changed = []
    with db.engine().begin() as c:
        db.ensure_user(c, user_id)
        existing = {r.leave_date for r in c.execute(
            select(t.c.leave_date).where(t.c.user_id == user_id, t.c.leave_date >= start, t.c.leave_date <= end)).all()}
        for d in days:
            if on and d not in existing:
                c.execute(t.insert().values(user_id=user_id, leave_date=d, created_at=db.utcnow()))
                changed.append(d)
            elif not on and d in existing:
                c.execute(delete(t).where(t.c.user_id == user_id, t.c.leave_date == d))
                changed.append(d)
    return changed


# ---------- monthly consolidation ----------
CONSOLIDATE_PROMPT = """You write a consolidated monthly summary of someone's email from their daily summaries.

Write in plain, friendly language for a non-technical reader, in markdown:
- Start with 2-3 sentences giving the overall picture of the month.
- **Main topics**: a bold heading per recurring project, client or theme, with 2-4 sentences each on what happened
  over the month (how it progressed, key decisions, files exchanged, outcomes). Group related days together.
- **Key dates**: short bullets for the few days with something important (dates as DD-MM-YYYY).
- **Still open**: requests or follow-ups that the summaries show were not resolved by month end. Skip if none.
- If there were leave days or company holidays, end with one line each, e.g. "On leave: 12-11-2026, 13-11-2026."
  and "Holidays: 09-11-2026 Diwali/Deepavali, 10-11-2026 Balipadyami." Never treat them as missing work.
Use only what the daily summaries say. Do not invent facts, numbers or outcomes. Do not list every day."""


MONTH_SUBAGENT_PROMPT = CONSOLIDATE_PROMPT + """

How to work:
1. Call read_daily_summaries for the month you are given.
2. Write the consolidated summary as described above.
3. Call save_monthly_summary with the month and the full summary text.
4. Reply with the summary you saved. If there are no daily summaries, say so in one line and save nothing."""

MONTH_SUBAGENT_DESCRIPTION = (
    "Writes and saves the consolidated summary of one month from the user's saved daily summaries "
    "(overview, main topics, key dates, what's still open). Give it the month, e.g. 'November 2026' or '11-2026'. "
    "Use it whenever the user asks for a monthly summary, a month's overview, or to consolidate a month."
)


def build_month_summarizer(user_id: str, llm, on_save=None):
    """Subagent that reads a month's daily rows and saves the consolidated summary. Bound to one user."""
    from langchain.agents import create_agent

    @tool
    def read_daily_summaries(month: str) -> dict:
        """All saved daily summaries of a month, oldest first.

        Args:
            month: e.g. '11-2026', 'November 2026', 'nov', 'this month', 'last month'.
        """
        m = parse_month(month)
        days = get_daily(user_id, m, month_end(m))
        return {"month": m.strftime("%B %Y"), "days": len(days),
                "daily_summaries": [{"date": fmt(d["date"]), "summary": d["summary"]} for d in days],
                "leave_days": [fmt(d) for d in get_leave(user_id, m, month_end(m))],
                "holidays": [{"date": fmt(h["date"]), "name": h["name"]}
                             for h in company_holidays.get(m, month_end(m))]}

    @tool
    def save_monthly_summary(month: str, summary: str) -> str:
        """Save (or replace) the consolidated summary for a month.

        Args:
            month: The month, e.g. '11-2026'.
            summary: The full consolidated summary (markdown).
        """
        m = parse_month(month)
        n = len(get_daily(user_id, m, month_end(m)))
        if not n:
            return f"Not saved: there are no daily summaries for {m.strftime('%B %Y')}."
        save_monthly(user_id, m, summary, n)
        if on_save:
            on_save(m)
        return f"Saved the summary for {m.strftime('%B %Y')} ({n} days)."

    return create_agent(
        model=llm,
        tools=[read_daily_summaries, save_monthly_summary],
        system_prompt=MONTH_SUBAGENT_PROMPT,
        name="month-summarizer",
    )


def month_subagent(user_id: str, llm) -> dict:
    """The subagent in the form create_deep_agent(subagents=[...]) expects."""
    return {"name": "month-summarizer", "description": MONTH_SUBAGENT_DESCRIPTION,
            "runnable": build_month_summarizer(user_id, llm)}


def consolidate_month(user_id: str, month: date, llm=None) -> dict | None:
    """Run the month-summarizer subagent for one month (used by the month-end job). Returns the stored summary,
    or None if the month has no daily summaries."""
    start = month_start(month)
    if not get_daily(user_id, start, month_end(start)):
        return None
    if llm is None:
        from agent import default_llm

        llm = default_llm()
    saved = []
    agent = build_month_summarizer(user_id, llm, on_save=saved.append)
    result = agent.invoke({"messages": [HumanMessage(
        f"Consolidate {start.strftime('%B %Y')} (month {start.strftime('%m-%Y')}) and save it.")]})
    if start not in saved:  # the model answered without calling the save tool: keep its answer anyway
        days = len(get_daily(user_id, start, month_end(start)))
        save_monthly(user_id, start, result["messages"][-1].text, days)
    return get_monthly(user_id, start)


# ---------- tools for the agent (bound to one user) ----------
def summary_tools(user_id: str) -> list:
    @tool
    def save_daily_summary(day: str, summary: str) -> str:
        """Save the summary of ONE day's emails so it is kept for later and for the monthly summary.

        Call this every time you summarize the emails of a single day (e.g. 'summarize 16 nov', 'today's emails'),
        with exactly the summary you show the user. Saving again for the same day replaces the old one.

        Args:
            day: The day the summary covers, e.g. '16-11-2026', '16 nov', 'today', 'yesterday'.
            summary: The summary text (markdown).
        """
        d = parse_day(day)
        save_daily(user_id, d, summary, "chat")
        return f"Saved the summary for {fmt(d)}."

    @tool
    def get_saved_summaries(start: str, end: str | None = None) -> dict:
        """Read summaries already saved for a day or a date range, plus monthly summaries for months in the range,
        the user's leave days and company holidays in the range.

        Use this first for questions about past days or months ('what happened last week', 'how was November'),
        before searching email again.

        Args:
            start: First day, e.g. '01-11-2026', '1 nov'.
            end: Last day (inclusive). Omit for a single day.
        """
        a = parse_day(start)
        b = parse_day(end) if end else a
        a, b = min(a, b), max(a, b)
        daily = [{"date": fmt(d["date"]), "summary": d["summary"]} for d in get_daily(user_id, a, b)]
        months, m = [], month_start(a)
        while m <= b:
            if got := get_monthly(user_id, m):
                months.append({"month": m.strftime("%B %Y"), "days_included": got["days_included"], "summary": got["summary"]})
            m = month_start(month_end(m) + timedelta(days=1))
        leave = get_leave(user_id, a, b)
        hols = company_holidays.get(a, b)
        saved = {d["date"] for d in daily}
        off = {fmt(d) for d in leave} | {fmt(h["date"]) for h in hols}  # days off are never "missing"
        missing = (b - a).days + 1 - len(daily) - len(off - saved)
        return {"daily": daily, "monthly": months, "leave_days": [fmt(d) for d in leave],
                "holidays": [{"date": fmt(h["date"]), "name": h["name"]} for h in hols], "missing_days": missing}

    @tool
    def mark_leave(start: str, end: str | None = None, on_leave: bool = True) -> str:
        """Mark days the user is (or was) on leave, or unmark them. Leave days are skipped for daily summaries,
        monthly summaries and EOD reports.

        Use when the user says things like "I'm on leave tomorrow", "I was off on 12 nov", "on leave 22-26 Dec",
        or "I wasn't on leave on the 14th".

        Args:
            start: First day, e.g. '22-12-2026', '22 dec', 'tomorrow'.
            end: Last day (inclusive) for a range. Omit for one day.
            on_leave: True to mark as leave, False to remove the mark.
        """
        a = parse_day(start)
        b = parse_day(end) if end else a
        try:
            changed = set_leave(user_id, a, b, on_leave)
        except ValueError as e:
            return str(e)
        span = fmt(min(a, b)) + ("" if a == b else f" to {fmt(max(a, b))}")
        if not changed:
            return f"Nothing to change for {span}."
        return f"{'Marked' if on_leave else 'Removed'} leave for {span} ({len(changed)} day{'s' if len(changed) != 1 else ''})."

    return [save_daily_summary, get_saved_summaries, mark_leave]


# ---------- background jobs ----------
def _signed_in_users() -> list[str]:
    """Users who completed the web sign-in with Gmail (the only ones the nightly job can read mail for)."""
    t = db.connected_accounts
    with db.engine().connect() as c:
        return [r.user_id for r in c.execute(select(t.c.user_id).where(t.c.toolkit == "gmail")).all()]


def _has_daily(user_id: str, d: date) -> bool:
    return bool(get_daily(user_id, d, d))


def _auto_summarize_day(agent_for, user_id: str, d: date) -> None:
    """Ask the user's agent for that day's summary in a throwaway thread and store the reply."""
    agent = agent_for(user_id)
    config = {"configurable": {"thread_id": f"auto-{uuid.uuid4().hex}"}}
    prompt = (
        f"Summarize the emails I received on {d.strftime('%d %B %Y')}. This is an automatic nightly summary: "
        "only read, do not send or change anything, and do not ask questions. If there were no emails, say so in one line."
    )
    result = agent.invoke({"messages": [HumanMessage(prompt)]}, config)
    if result.get("__interrupt__"):
        log.warning("Nightly summary for %s on %s wanted an approval; skipped.", user_id, fmt(d))
        return
    if not _has_daily(user_id, d):  # the agent may already have saved it with save_daily_summary
        save_daily(user_id, d, result["messages"][-1].text, "auto")


def run_due_jobs(agent_for, now: datetime | None = None) -> dict:
    """Do whatever is due. Safe to call often; each step checks the database first."""
    if not _job_lock.acquire(blocking=False):
        return {"skipped": "already running"}
    done = {"daily": 0, "monthly": 0, "errors": 0}
    try:
        now = now or datetime.now(TZ)
        today = now.date()
        for user_id in _signed_in_users():
            if AUTO_DAILY:
                due = [today - timedelta(days=1)] + ([today] if now.hour >= DAILY_SUMMARY_HOUR else [])
                for d in due:
                    # leave days and company holidays are skipped
                    if not _has_daily(user_id, d) and not is_leave(user_id, d) and not company_holidays.name_on(d):
                        try:
                            _auto_summarize_day(agent_for, user_id, d)
                            done["daily"] += 1
                        except Exception:
                            done["errors"] += 1
                            log.exception("Nightly summary failed for %s on %s", user_id, fmt(d))
            last_month = month_start(month_start(today) - timedelta(days=1))
            existing = get_monthly(user_id, last_month)
            stored_days = len(get_daily(user_id, last_month, month_end(last_month)))
            # build it once the month is over, and rebuild if more days were saved since (e.g. a late catch-up)
            if stored_days and (not existing or existing["days_included"] < stored_days):
                try:
                    if consolidate_month(user_id, last_month):
                        done["monthly"] += 1
                except Exception:
                    done["errors"] += 1
                    log.exception("Monthly summary failed for %s, %s", user_id, last_month.strftime("%B %Y"))
    finally:
        _job_lock.release()
    return done
