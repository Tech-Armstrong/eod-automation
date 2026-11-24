"""Storage for the app (users, signed-in devices, connections, conversations, notes, memory).

Cloudflare D1 (for testing): set in .env
    DB_BACKEND=d1
    CLOUDFLARE_ACCOUNT_ID=...
    CLOUDFLARE_D1_DATABASE_ID=...
    CLOUDFLARE_API_TOKEN=...      (permission: D1 Edit)
It is SQLite, reached over Cloudflare's HTTPS API (see d1.py). Copy existing MySQL data with migrate_to_d1.py.

MySQL (default when DB_BACKEND is not d1). Connection settings come from .env:

    MYSQL_HOST=127.0.0.1
    MYSQL_PORT=3306
    MYSQL_USER=root
    MYSQL_PASSWORD=...
    MYSQL_DATABASE=eod-automation

or a full SQLAlchemy URL in DATABASE_URL (it wins if set). Tables are created on first use.
"""
import os
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text, create_engine, text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.engine import Engine

load_dotenv()

metadata = MetaData()

# Roles people pick at sign-in. Used later to tailor the assistant per role.
ROLES = ["Relationship Manager", "Back Office", "Human Resources", "Tech & Automation", "Research & Analytics", "Intern", "Sales"]
LongText = Text().with_variant(MEDIUMTEXT(), "mysql")  # up to 16 MB in MySQL
Email = String(320)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as UTC, naive


users = Table(
    "users", metadata,
    Column("id", Email, primary_key=True),  # the user's verified email, lower-case
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("last_login_at", DateTime),
    Column("role", String(64)),  # one of ROLES; set at sign-in
    # EOD reports always go to eod.DEFAULT_RECIPIENTS; the reporting manager (if any) is added in Cc.
    Column("has_manager", Boolean),  # NULL = not asked yet, False = no reporting manager
    Column("manager_email", Email),
    mysql_charset="utf8mb4",
)

devices = Table(
    "devices", metadata,
    Column("token_hash", String(64), primary_key=True),  # SHA-256 of the cookie; the raw token is never stored
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("last_seen_at", DateTime),
    mysql_charset="utf8mb4",
)

connected_accounts = Table(
    "connected_accounts", metadata,
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("toolkit", String(64), primary_key=True),
    Column("account_id", String(128), nullable=False),  # Composio connected account id
    Column("verified_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)

conversations = Table(
    "conversations", metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(255)),
    Column("started_at", DateTime, nullable=False, default=utcnow),
    Index("ix_conversations_user_started", "user_id", "started_at"),
    mysql_charset="utf8mb4",
)

messages = Table(
    "messages", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("conversation_id", String(32), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("role", String(16), nullable=False),  # "user" | "assistant"
    Column("content", LongText, nullable=False),
    Column("todos", JSON),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)

notes = Table(
    "notes", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("conversation_id", String(32), ForeignKey("conversations.id", ondelete="SET NULL")),
    Column("title", String(255), nullable=False),
    Column("summary", LongText),
    Column("action_items", JSON),  # [{"text": ..., "done": bool}]
    Column("notes", LongText),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)

memories = Table(
    "memories", metadata,
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("content", LongText, nullable=False),  # the user's AGENTS.md
    Column("updated_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)


daily_summaries = Table(
    "daily_summaries", metadata,
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("summary_date", Date, primary_key=True),  # India date the emails arrived; shown as DD-MM-YYYY
    Column("summary", LongText, nullable=False),
    Column("source", String(16), nullable=False, default="chat"),  # "chat" (user asked) | "auto" (nightly job)
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("updated_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)

monthly_summaries = Table(
    "monthly_summaries", metadata,
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("month", Date, primary_key=True),  # first day of the month, e.g. 2026-11-01 for November 2026
    Column("summary", LongText, nullable=False),  # consolidated from that month's daily summaries
    Column("days_included", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("updated_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)


leave_days = Table(
    "leave_days", metadata,
    Column("user_id", Email, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("leave_date", Date, primary_key=True),  # a day the user was/is on leave: skipped for summaries & reports
    Column("created_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)


holidays = Table(
    "holidays", metadata,  # company-wide: the same for every user
    Column("holiday_date", Date, primary_key=True),
    Column("name", String(255), nullable=False),
    Column("source", String(255)),  # file it came from
    Column("uploaded_by", Email),  # HR user who uploaded it (NULL = loaded from the holidays/ folder)
    Column("created_at", DateTime, nullable=False, default=utcnow),
    mysql_charset="utf8mb4",
)


def backend() -> str:
    """'url' (DATABASE_URL set), 'd1' (Cloudflare D1, DB_BACKEND=d1) or 'mysql' (default)."""
    if os.getenv("DATABASE_URL"):
        return "url"
    return "d1" if os.getenv("DB_BACKEND", "mysql").strip().lower() == "d1" else "mysql"


def database_url() -> str:
    if url := os.getenv("DATABASE_URL"):
        return url
    user = quote_plus(os.getenv("MYSQL_USER", "root"))
    password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    name = quote_plus(os.getenv("MYSQL_DATABASE", "eod-automation"))
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


@lru_cache(maxsize=1)
def engine() -> Engine:
    if backend() == "d1":
        import d1
        from sqlalchemy.pool import NullPool

        # SQLite dialect, but every statement goes to Cloudflare D1 over HTTPS (see d1.py)
        eng = create_engine("sqlite://", module=d1, creator=d1.connect_from_env, poolclass=NullPool)
    else:
        eng = create_engine(database_url(), pool_pre_ping=True, pool_recycle=1800, future=True)
    metadata.create_all(eng)  # creates missing tables; never drops or alters existing ones
    _add_missing_columns(eng)
    return eng


# Columns added after the first release: create_all doesn't add columns to existing tables, so do it here.
NEW_COLUMNS = [
    ("users", "role", "VARCHAR(64) NULL"),
    ("users", "has_manager", "BOOLEAN NULL"),
    ("users", "manager_email", "VARCHAR(320) NULL"),
]


def _add_missing_columns(eng: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(eng)
    with eng.begin() as c:
        for table, column, ddl in NEW_COLUMNS:
            if column not in {col["name"] for col in insp.get_columns(table)}:
                c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def check() -> str:
    """Connect and create tables. Returns a short description, or raises with a readable message."""
    try:
        with engine().connect() as c:
            if engine().dialect.name == "mysql":
                version = c.execute(text("SELECT VERSION()")).scalar()
                schema = c.execute(text("SELECT DATABASE()")).scalar()
                return f"MySQL {version}, schema `{schema}`"
            if backend() == "d1":
                # (D1 blocks sqlite_version() and similar functions, so only read the schema table)
                tables = c.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table'")).scalar()
                db_id = os.getenv("CLOUDFLARE_D1_DATABASE_ID", "")
                return f"Cloudflare D1, database {db_id[:8]}..., {tables} tables"
            return engine().dialect.name
    except Exception as e:  # make the common setup mistakes obvious
        engine.cache_clear()
        msg = str(getattr(e, "orig", e))
        hint = ""
        if backend() == "d1":
            if "Authentication" in msg or "code 10000" in msg or "code 9109" in msg:
                hint = " Check CLOUDFLARE_API_TOKEN (it needs the 'D1 Edit' permission for this account)."
            elif "not found" in msg.lower() or "code 7404" in msg or "code 7500" in msg:
                hint = " Check CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_D1_DATABASE_ID."
            raise RuntimeError(f"Could not connect to Cloudflare D1: {msg.rstrip('.')}.{hint}") from e
        if "Access denied" in msg:
            hint = " Check MYSQL_USER and MYSQL_PASSWORD in .env."
        elif "Unknown database" in msg:
            hint = " Check MYSQL_DATABASE in .env matches the schema name exactly."
        elif "Can't connect" in msg or "Connection refused" in msg:
            hint = " Is the MySQL80 service running, and are MYSQL_HOST/MYSQL_PORT right?"
        raise RuntimeError(f"Could not connect to the database: {msg}.{hint}") from e


def ensure_user(conn, user_id: str) -> None:
    """Insert the user row if it's missing (portable upsert-ignore)."""
    if conn.execute(users.select().where(users.c.id == user_id)).first() is None:
        conn.execute(users.insert().values(id=user_id, created_at=utcnow()))
