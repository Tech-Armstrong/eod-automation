"""Composio client and per-user session."""
import os
from functools import lru_cache

from composio import Composio
from composio_langchain import LangchainProvider
from dotenv import load_dotenv

load_dotenv()

TOOLKITS = [t.strip() for t in os.getenv("COMPOSIO_TOOLKITS", "gmail,googledrive").split(",") if t.strip()]


@lru_cache(maxsize=1)
def client() -> Composio:
    # Reads COMPOSIO_API_KEY from the environment.
    return Composio(provider=LangchainProvider())


def toolkit_status(user_id: str) -> list[dict]:
    """[{slug, name, logo, connected}] for every toolkit in TOOLKITS, in that order."""
    from auth import verified_accounts

    details = get_session(user_id).toolkits(toolkits=TOOLKITS)
    by_slug = {t.slug.lower(): t for t in details.items}
    pinned = verified_accounts(user_id)
    out = []
    for slug in TOOLKITS:
        t = by_slug.get(slug.lower())
        if slug in pinned:  # confirmed through the web sign-in: only that exact connection counts
            try:
                connected = account_status(pinned[slug]) == "ACTIVE"
            except Exception:
                connected = False
        else:
            connected = bool(t and (t.is_no_auth or (t.connection and t.connection.is_active)))
        out.append({
            "slug": slug,
            "name": getattr(t, "name", None) or slug.title(),
            "logo": getattr(t, "logo", None),
            "connected": connected,
        })
    return out


def start_link(user_id: str, toolkit: str, callback_url: str) -> tuple[str, str]:
    """Start Composio's hosted sign-in for one toolkit. Returns (redirect_url, connected_account_id)."""
    req = get_session(user_id).authorize(toolkit, callback_url=callback_url)
    return req.redirect_url, req.id


def account_status(connected_account_id: str) -> str:
    return str(getattr(client().connected_accounts.get(connected_account_id), "status", "") or "").upper()


def delete_account(connected_account_id: str) -> None:
    try:
        client().connected_accounts.delete(connected_account_id)
    except Exception:  # already gone
        pass


def remove_older_accounts(user_id: str, toolkit: str, keep_id: str) -> None:
    """Signing in again on a new device creates another connection; keep only the newest one."""
    try:
        res = client().connected_accounts.list(user_ids=[user_id], toolkit_slugs=[toolkit], limit=50)
    except Exception:
        return
    for acc in getattr(res, "items", []) or []:
        if getattr(acc, "id", None) and acc.id != keep_id:
            delete_account(acc.id)


def _find_email(value) -> str | None:
    """Gmail profile responses nest differently between versions; find the address wherever it is."""
    if isinstance(value, dict):
        for key in ("emailAddress", "email_address", "email"):
            if isinstance(value.get(key), str) and "@" in value[key]:
                return value[key]
        for v in value.values():
            if found := _find_email(v):
                return found
    elif isinstance(value, list):
        for v in value:
            if found := _find_email(v):
                return found
    return None


def gmail_address(user_id: str, connected_account_id: str) -> str | None:
    """The address of the Gmail account that was just connected (proves who signed in)."""
    res = client().tools.execute(
        "GMAIL_GET_PROFILE",
        {"user_id": "me"},
        user_id=user_id,
        connected_account_id=connected_account_id,
        dangerously_skip_version_check=True,
    )
    data = res if isinstance(res, dict) else getattr(res, "__dict__", {})
    return _find_email(data)


def get_session(user_id: str):
    """One Composio session per end user. user_id is YOUR id for the user
    (e.g. their email or DB id); Composio stores their Google tokens under it."""
    return client().create(
        user_id=user_id,
        toolkits=TOOLKITS,
        manage_connections=False,   # sign-in happens via connect.py / your API, not the LLM
        sandbox={"enable": False},  # no remote code-execution / bash tools
    )


def agent_session(user_id: str):
    """Session for the agent. Apps confirmed through the web sign-in are pinned to exactly that connection, so a
    connection someone else started under this id is never used."""
    from auth import verified_accounts

    pinned = {k: v for k, v in verified_accounts(user_id).items() if k in TOOLKITS}
    if not pinned:  # signed up with connect.py: nothing to pin
        return get_session(user_id)
    return client().create(
        user_id=user_id,
        toolkits=TOOLKITS,
        manage_connections=False,
        sandbox={"enable": False},
        connected_accounts=pinned,
    )
