"""Database tools for the assistant: fetch, insert and update rows in MySQL, safely.

Instead of letting the model write raw SQL, each tool takes a table name, filters and values:
- every query is limited to the signed-in user's own rows (user_id is added by the code, never by the model);
- only the tables and columns in TABLES below can be touched, each with its own read/insert/update rights;
- sign-in data (users, devices, connected_accounts) is never reachable;
- update_records asks the user for approval first (see agent.py); there is no delete.

Filters: {"column": value} for equals, or a suffix: __gte, __lte, __gt, __lt, __contains, __in.
Date columns accept '16-11-2026', '16 nov', 'today' etc.; dates come back as DD-MM-YYYY.
"""
from datetime import date, datetime

from langchain_core.tools import tool
from sqlalchemy import Date, DateTime, and_, func, select, update

import db
from datetime_tools import parse_day

# table -> rights. "read"/"insert"/"update" list the columns each action may use. user_id is handled automatically.
TABLES = {
    "daily_summaries": {
        "about": "one email summary per day (summary_date) for this user",
        "read": ["summary_date", "summary", "source", "created_at", "updated_at"],
        "insert": ["summary_date", "summary", "source"],
        "update": ["summary", "source"],
    },
    "monthly_summaries": {
        "about": "one consolidated summary per month (month = 1st day of the month) for this user",
        "read": ["month", "summary", "days_included", "created_at", "updated_at"],
        "insert": ["month", "summary", "days_included"],
        "update": ["summary", "days_included"],
    },
    "notes": {
        "about": "notes and to-dos the user saved (action_items = [{text, done}])",
        "read": ["id", "conversation_id", "title", "summary", "action_items", "notes", "created_at"],
        "insert": ["title", "summary", "action_items", "notes"],
        "update": ["title", "summary", "action_items", "notes"],
    },
    "leave_days": {
        "about": "days this user marked as leave (read only here; use mark_leave to change)",
        "read": ["leave_date", "created_at"],
        "insert": [],
        "update": [],
    },
    "conversations": {
        "about": "this user's chats (read only)",
        "read": ["id", "title", "started_at"],
        "insert": [],
        "update": [],
    },
    "messages": {
        "about": "both sides of each chat turn (read only); filter by conversation_id",
        "read": ["id", "conversation_id", "role", "content", "created_at"],
        "insert": [],
        "update": [],
    },
}
OPS = {"gte": "__ge__", "lte": "__le__", "gt": "__gt__", "lt": "__lt__"}
MAX_ROWS = 100


class ToolInputError(ValueError):
    pass


def _table(name: str):
    if name not in TABLES:
        raise ToolInputError(f"Unknown table '{name}'. Tables you can use: {', '.join(TABLES)}.")
    return getattr(db, name), TABLES[name]


def _coerce(col, value):
    """Turn friendly input into the column's type (dates in any common format)."""
    if value is None:
        return None
    if isinstance(col.type, Date) and not isinstance(col.type, DateTime):
        if isinstance(value, date):
            return value
        d = parse_day(str(value))
        return d.replace(day=1) if col.name == "month" else d
    if isinstance(col.type, DateTime) and isinstance(value, str):
        return datetime.combine(parse_day(value), datetime.min.time())
    return value


def _out(value):
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    return value


def _where(table, rules: dict, user_id: str, filters: dict | None):
    """user_id first (from the session, not the model), then the model's filters on readable columns."""
    if table.name == "messages":  # messages have no user_id: scope through the user's conversations
        own = select(db.conversations.c.id).where(db.conversations.c.user_id == user_id)
        conds = [table.c.conversation_id.in_(own)]
    else:
        conds = [table.c.user_id == user_id]
    for key, value in (filters or {}).items():
        name, _, op = key.partition("__")
        if name not in rules["read"]:
            raise ToolInputError(f"Can't filter {table.name} by '{name}'. Columns: {', '.join(rules['read'])}.")
        col = table.c[name]
        if op == "contains":
            conds.append(col.like(f"%{value}%"))
        elif op == "in":
            conds.append(col.in_([_coerce(col, v) for v in (value if isinstance(value, list) else [value])]))
        elif op in OPS:
            conds.append(getattr(col, OPS[op])(_coerce(col, value)))
        elif op == "":
            conds.append(col == _coerce(col, value))
        else:
            raise ToolInputError(f"Unknown filter '__{op}'. Use __gte, __lte, __gt, __lt, __contains or __in.")
    return and_(*conds)


def _check_columns(table_name: str, values: dict, allowed: list[str], action: str) -> None:
    if not allowed:
        raise ToolInputError(f"{table_name} is read only.")
    bad = [k for k in values if k not in allowed]
    if bad:
        raise ToolInputError(f"Can't {action} {', '.join(bad)} in {table_name}. Allowed: {', '.join(allowed)}.")


def _tables_help() -> str:
    return "\n".join(
        f"- {name}: {r['about']}. Read: {', '.join(r['read'])}."
        + (f" Insert: {', '.join(r['insert'])}." if r["insert"] else "")
        + (f" Update: {', '.join(r['update'])}." if r["update"] else "")
        for name, r in TABLES.items()
    )


def fetch(user_id: str, table: str, filters: dict | None = None, columns: list[str] | None = None,
          order_by: str | None = None, limit: int = 20) -> dict:
    t, rules = _table(table)
    cols = columns or rules["read"]
    _check_columns(table, {c: None for c in cols}, rules["read"], "read")
    query = select(*[t.c[c] for c in cols]).where(_where(t, rules, user_id, filters))
    if order_by:
        name, desc = order_by.lstrip("-"), order_by.startswith("-")
        if name not in rules["read"]:
            raise ToolInputError(f"Can't sort by '{name}'.")
        query = query.order_by(t.c[name].desc() if desc else t.c[name])
    limit = max(1, min(int(limit or 20), MAX_ROWS))
    with db.engine().connect() as c:
        total = c.execute(select(func.count()).select_from(t).where(_where(t, rules, user_id, filters))).scalar()
        rows = c.execute(query.limit(limit)).all()
    return {"rows": [{k: _out(v) for k, v in r._mapping.items()} for r in rows], "total_matching": total,
            "returned": len(rows)}


def insert(user_id: str, table: str, values: dict) -> dict:
    t, rules = _table(table)
    _check_columns(table, values, rules["insert"], "insert")
    row = {k: _coerce(t.c[k], v) for k, v in values.items()}
    now = db.utcnow()
    for stamp in ("created_at", "updated_at"):
        if stamp in t.c:
            row[stamp] = now
    with db.engine().begin() as c:
        db.ensure_user(c, user_id)
        try:
            c.execute(t.insert().values(user_id=user_id, **row))
        except Exception as e:
            if "Duplicate" in str(e) or "UNIQUE" in str(e):
                raise ToolInputError("A row for that date/month already exists. Use update_records to change it.") from e
            raise
    return {"inserted": {k: _out(v) for k, v in row.items() if k not in ("created_at", "updated_at")}}


def modify(user_id: str, table: str, filters: dict, values: dict) -> dict:
    t, rules = _table(table)
    if not filters:
        raise ToolInputError("Give filters that pick the rows to change (e.g. {'summary_date': '16-11-2026'}).")
    _check_columns(table, values, rules["update"], "update")
    row = {k: _coerce(t.c[k], v) for k, v in values.items()}
    if "updated_at" in t.c:
        row["updated_at"] = db.utcnow()
    with db.engine().begin() as c:
        n = c.execute(update(t).where(_where(t, rules, user_id, filters)).values(**row)).rowcount
    return {"updated_rows": n}


def make_db_tools(user_id: str) -> list:
    """The three tools, bound to one user. user_id never comes from the model."""

    def run(fn, *args):
        try:
            return fn(user_id, *args)
        except ToolInputError as e:
            return {"error": str(e)}

    @tool
    def fetch_records(table: str, filters: dict | None = None, columns: list[str] | None = None,
                      order_by: str | None = None, limit: int = 20) -> dict:
        """Read the user's saved data from the database.

        Args:
            table: One of the tables below.
            filters: {"column": value} for equals, or column__gte / __lte / __gt / __lt / __contains / __in.
                     Dates can be '16-11-2026', '16 nov', 'today'. E.g. {"summary_date__gte": "01-11-2026",
                     "summary_date__lte": "30-11-2026"} or {"summary__contains": "invoice"}.
            columns: Columns to return (default: all readable ones).
            order_by: A column, with '-' in front for newest/largest first, e.g. "-summary_date".
            limit: Max rows (1-100, default 20). total_matching says how many exist.

        Tables:
        """
        return run(fetch, table, filters, columns, order_by, limit)

    @tool
    def insert_record(table: str, values: dict) -> dict:
        """Add one row for the user (daily_summaries, monthly_summaries or notes). Dates as '16-11-2026'.
        For a day or month that already has a row, use update_records instead.

        Args:
            table: daily_summaries, monthly_summaries or notes.
            values: Column -> value, e.g. {"summary_date": "16-11-2026", "summary": "...", "source": "chat"}.
        """
        return run(insert, table, values)

    @tool
    def update_records(table: str, filters: dict, values: dict) -> dict:
        """Change existing rows of the user's data. The user is asked to approve the change first.

        Args:
            table: daily_summaries, monthly_summaries or notes.
            filters: Which rows, same format as fetch_records, e.g. {"summary_date": "16-11-2026"} or {"id": 12}.
            values: Columns to set, e.g. {"summary": "..."}.
        """
        return run(modify, table, filters, values)

    fetch_records.description += "\n" + _tables_help()
    return [fetch_records, insert_record, update_records]
