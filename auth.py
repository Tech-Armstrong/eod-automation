"""Sign-in for the web app.

How someone gets in:
1. They type their email address.
2. They sign in with Google through Composio's hosted link (this also connects Gmail for the agent).
3. Back on our callback, we ask Gmail which address actually signed in. Only if it matches the typed address does
   that browser get a device cookie. A mismatch removes the new connection.

The cookie holds a random token. MySQL (table `devices`) keeps only its SHA-256 hash, so the database can't be used
to log in. A new device or browser repeats step 2. The confirmed Composio connection per user and app is kept in
`connected_accounts`, and the agent only ever uses that one.
"""
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select, update

import db

COOKIE = "eod_device"
COOKIE_MAX_AGE = 60 * 60 * 24 * 90  # 90 days
PENDING_TTL = 15 * 60  # a sign-in link must be finished within 15 minutes

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.Lock()
_pending: dict[str, dict] = {}  # state -> {user_id, toolkit, account_id, created, role}  (short-lived, in memory)


def normalize_email(raw: str) -> str | None:
    e = (raw or "").strip().lower()
    return e if EMAIL_RE.match(e) else None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- devices ----------
def issue_device(user_id: str) -> str:
    """New device token for a verified user. Returns the raw token (goes into the cookie only)."""
    token = secrets.token_urlsafe(32)
    now = db.utcnow()
    with db.engine().begin() as c:
        db.ensure_user(c, user_id)
        c.execute(db.devices.insert().values(token_hash=_hash(token), user_id=user_id, created_at=now, last_seen_at=now))
        c.execute(update(db.users).where(db.users.c.id == user_id).values(last_login_at=now))
    return token


def device_user(token: str | None) -> str | None:
    if not token:
        return None
    with db.engine().begin() as c:
        row = c.execute(select(db.devices).where(db.devices.c.token_hash == _hash(token))).first()
        if not row:
            return None
        if db.utcnow() - row.created_at > timedelta(seconds=COOKIE_MAX_AGE):
            c.execute(delete(db.devices).where(db.devices.c.token_hash == row.token_hash))
            return None
        if not row.last_seen_at or db.utcnow() - row.last_seen_at > timedelta(hours=1):  # don't write on every call
            c.execute(update(db.devices).where(db.devices.c.token_hash == row.token_hash).values(last_seen_at=db.utcnow()))
        return row.user_id


def revoke_device(token: str | None) -> None:
    if token:
        with db.engine().begin() as c:
            c.execute(delete(db.devices).where(db.devices.c.token_hash == _hash(token)))


# ---------- confirmed connections ----------
def verified_accounts(user_id: str) -> dict[str, str]:
    with db.engine().connect() as c:
        rows = c.execute(select(db.connected_accounts).where(db.connected_accounts.c.user_id == user_id)).all()
    return {r.toolkit: r.account_id for r in rows}


def set_verified_account(user_id: str, toolkit: str, account_id: str) -> None:
    """Pin the agent to the connection the user just completed, so a stray connection can never be used."""
    t = db.connected_accounts
    with db.engine().begin() as c:
        db.ensure_user(c, user_id)
        exists = c.execute(select(t).where(t.c.user_id == user_id, t.c.toolkit == toolkit)).first()
        if exists:
            c.execute(update(t).where(t.c.user_id == user_id, t.c.toolkit == toolkit)
                      .values(account_id=account_id, verified_at=db.utcnow()))
        else:
            c.execute(t.insert().values(user_id=user_id, toolkit=toolkit, account_id=account_id, verified_at=db.utcnow()))


# ---------- role ----------
def valid_role(role: str | None) -> str | None:
    """The canonical role name if it's one of db.ROLES (case-insensitive), else None."""
    wanted = (role or "").strip().lower()
    return next((r for r in db.ROLES if r.lower() == wanted), None)


def get_role(user_id: str) -> str | None:
    with db.engine().connect() as c:
        return c.execute(select(db.users.c.role).where(db.users.c.id == user_id)).scalar()


def set_role(user_id: str, role: str) -> None:
    role = valid_role(role)
    if not role:
        raise ValueError(f"Role must be one of: {', '.join(db.ROLES)}")
    with db.engine().begin() as c:
        db.ensure_user(c, user_id)
        c.execute(update(db.users).where(db.users.c.id == user_id).values(role=role))


# ---------- reporting manager (Cc on EOD reports) ----------
def clean_manager(user_id: str, has_manager: bool | None, manager_email: str | None) -> tuple[bool, str | None]:
    """Validate the sign-in answer. Raises ValueError with a readable message."""
    if has_manager is None:
        raise ValueError("Tell us whether you have a reporting manager.")
    if not has_manager:
        return False, None
    email = normalize_email(manager_email or "")
    if not email:
        raise ValueError("Enter your reporting manager's email address.")
    if email == user_id:
        raise ValueError("Enter your manager's email, not your own.")
    return True, email


def get_manager(user_id: str) -> dict:
    """{has_manager: None | bool, manager_email}. None means the user hasn't answered yet."""
    with db.engine().connect() as c:
        row = c.execute(select(db.users.c.has_manager, db.users.c.manager_email).where(db.users.c.id == user_id)).first()
    if not row:
        return {"has_manager": None, "manager_email": None}
    return {"has_manager": None if row.has_manager is None else bool(row.has_manager),
            "manager_email": row.manager_email if row.has_manager else None}


def set_manager(user_id: str, has_manager: bool | None, manager_email: str | None = None) -> dict:
    has, email = clean_manager(user_id, has_manager, manager_email)
    with db.engine().begin() as c:
        db.ensure_user(c, user_id)
        c.execute(update(db.users).where(db.users.c.id == user_id).values(has_manager=has, manager_email=email))
    return {"has_manager": has, "manager_email": email}


# ---------- pending sign-in links (in memory) ----------
def new_pending(user_id: str, toolkit: str, role: str | None = None, manager: dict | None = None) -> str:
    """One-time state for a sign-in link, so the callback knows who and what it is for."""
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if now - v["created"] > PENDING_TTL]:
            _pending.pop(k, None)
        # role is only applied after Google sign-in proves the email is theirs
        _pending[state] = {"user_id": user_id, "toolkit": toolkit, "account_id": None, "created": now,
                           "role": valid_role(role), "manager": manager}  # manager: already validated, or None
    return state


def set_pending_account(state: str, account_id: str) -> None:
    with _lock:
        if state in _pending:
            _pending[state]["account_id"] = account_id


def take_pending(state: str) -> dict | None:
    with _lock:
        p = _pending.pop(state, None)
    if p and time.time() - p["created"] <= PENDING_TTL:
        return p
    return None


# ---------- one-time import of the old JSON files ----------
def import_legacy_files(data_dir: Path) -> int:
    """Move data/devices.json and data/accounts.json (from before MySQL) into the database. Returns rows imported."""
    imported = 0
    dev_file, acc_file = data_dir / "devices.json", data_dir / "accounts.json"
    with db.engine().begin() as c:
        if dev_file.exists():
            for token_hash, entry in json.loads(dev_file.read_text(encoding="utf-8") or "{}").items():
                if c.execute(select(db.devices).where(db.devices.c.token_hash == token_hash)).first():
                    continue
                db.ensure_user(c, entry["user_id"])
                created = db.utcnow() - timedelta(seconds=max(0, time.time() - entry.get("created", time.time())))
                c.execute(db.devices.insert().values(token_hash=token_hash, user_id=entry["user_id"], created_at=created))
                imported += 1
            dev_file.rename(dev_file.with_suffix(".json.imported"))
    if acc_file.exists():
        for user_id, toolkits in json.loads(acc_file.read_text(encoding="utf-8") or "{}").items():
            for toolkit, account_id in toolkits.items():
                set_verified_account(user_id, toolkit, account_id)
                imported += 1
        acc_file.rename(acc_file.with_suffix(".json.imported"))
    return imported
