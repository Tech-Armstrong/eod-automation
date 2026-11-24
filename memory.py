"""Long-term memory that survives across chats and restarts, one per user.

The source of truth is the MySQL table `memories` (one markdown document per user). The agent works on a local
copy through a virtual folder `/memories/`:

    memories/<user-id>/AGENTS.md     <- refreshed from MySQL when the agent starts; loaded into its instructions at
                                        the start of every chat; saved back to MySQL after every turn (save_memory)

The agent updates it itself (people and their emails, the user's preferences, recurring tasks). Everything else the
agent writes stays in the chat's temporary scratch space.
"""
import os
import re
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from sqlalchemy import select, update

import db

MEMORY_ROOT = Path(os.getenv("MEMORY_DIR", Path(__file__).parent / "memories"))
MEMORY_FILE = "/memories/AGENTS.md"  # path as the agent sees it

TEMPLATE = """# What I know about this user

<!-- Kept up to date by the assistant. You can edit this file by hand; keep the headings. -->

## About me

## People
<!-- One line per person: Name (relationship/role): email -->

## Preferences

## Recurring tasks
"""


def user_dir(user_id: str) -> Path:
    """Folder for one user. The id is made filename-safe so it can never escape MEMORY_ROOT."""
    safe = re.sub(r"[^A-Za-z0-9._@-]", "_", user_id.strip()).strip(".") or "default"
    return MEMORY_ROOT / safe


def ensure_memory(user_id: str) -> Path:
    """Bring the local copy in line with MySQL and return its path.

    MySQL has it -> write it to disk. Only the disk has it (from before MySQL) -> upload it. Neither -> template.
    """
    folder = user_dir(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "AGENTS.md"
    with db.engine().begin() as c:
        row = c.execute(select(db.memories.c.content).where(db.memories.c.user_id == user_id)).first()
        if row is not None:
            path.write_text(row.content, encoding="utf-8")
        else:
            content = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE
            path.write_text(content, encoding="utf-8")
            db.ensure_user(c, user_id)
            c.execute(db.memories.insert().values(user_id=user_id, content=content, updated_at=db.utcnow()))
    return path


def save_memory(user_id: str) -> bool:
    """Copy the local file back to MySQL if the agent changed it. Call after every turn. Returns True if saved."""
    path = user_dir(user_id) / "AGENTS.md"
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    with db.engine().begin() as c:
        row = c.execute(select(db.memories.c.content).where(db.memories.c.user_id == user_id)).first()
        if row is not None and row.content == content:
            return False
        if row is None:
            db.ensure_user(c, user_id)
            c.execute(db.memories.insert().values(user_id=user_id, content=content, updated_at=db.utcnow()))
        else:
            c.execute(update(db.memories).where(db.memories.c.user_id == user_id)
                      .values(content=content, updated_at=db.utcnow()))
    return True


def build_backend(user_id: str) -> CompositeBackend:
    """Scratch files live in the chat state; anything under /memories/ goes to this user's local copy."""
    ensure_memory(user_id)
    return CompositeBackend(
        default=StateBackend(),
        routes={"/memories/": FilesystemBackend(root_dir=user_dir(user_id), virtual_mode=True)},
    )
