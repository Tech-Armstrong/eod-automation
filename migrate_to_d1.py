"""Copy everything from MySQL into Cloudflare D1 (one time, safe to re-run).

    python migrate_to_d1.py            # copy, then print row counts on both sides
    python migrate_to_d1.py --check    # only compare row counts

Needs both sets of settings in .env: the MYSQL_* values (source) and the CLOUDFLARE_* values (target).
Tables are created in D1 if missing. Rows that already exist in D1 (same key) are left as they are, so running
it twice doesn't duplicate anything. Tables are copied parents first (users before conversations, etc.).
"""
import os
import sys

from sqlalchemy import create_engine, func, select

import db


def mysql_engine():
    saved = os.environ.pop("DATABASE_URL", None)
    try:
        return create_engine(db.database_url(), future=True)
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved


def d1_engine():
    os.environ["DB_BACKEND"] = "d1"
    os.environ.pop("DATABASE_URL", None)
    db.engine.cache_clear()
    return db.engine()  # creates the tables in D1


def counts(eng) -> dict[str, int]:
    with eng.connect() as c:
        return {t.name: c.execute(select(func.count()).select_from(t)).scalar() for t in db.metadata.sorted_tables}


def copy(src, dst) -> None:
    for t in db.metadata.sorted_tables:  # parents before children (foreign keys)
        with src.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(select(t))]
        if not rows:
            print(f"  {t.name}: nothing to copy")
            continue
        with dst.begin() as c:
            c.execute(t.insert().prefix_with("OR IGNORE"), rows)  # sent to D1 in batches of 50
        print(f"  {t.name}: {len(rows)} rows")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    src = mysql_engine()
    print("Source: MySQL", src.url.database)
    try:
        dst = d1_engine()
        print("Target:", db.check())
    except Exception as e:  # show the readable reason, not a traceback
        sys.exit(f"Can't use Cloudflare D1: {getattr(e, 'orig', e)}\n"
                 "Check CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_D1_DATABASE_ID (the database's UUID) and "
                 "CLOUDFLARE_API_TOKEN (a token with D1 Edit permission) in .env.")
    if "--check" not in sys.argv:
        print("Copying...")
        copy(src, dst)
    a, b = counts(src), counts(dst)
    print(f"\n{'table':<20}{'mysql':>8}{'d1':>8}")
    for name in a:
        flag = "" if b[name] >= a[name] else "   <- missing rows"
        print(f"{name:<20}{a[name]:>8}{b[name]:>8}{flag}")


if __name__ == "__main__":
    main()
