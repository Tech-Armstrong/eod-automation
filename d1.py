"""Cloudflare D1 (SQLite in Cloudflare) as a DB-API 2.0 driver, so SQLAlchemy's SQLite dialect can use it.

Every statement is one HTTPS call to D1's official REST API:
    POST https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/raw
    body {"sql": "...", "params": [...]}   or   {"batch": [{"sql", "params"}, ...]}
Rows come back as result[0].results.columns / .rows; meta has changes and last_row_id.

Settings (.env): CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_D1_DATABASE_ID, CLOUDFLARE_API_TOKEN (permission "D1 Edit").

Limits to know:
- No transactions over HTTP: each statement commits on its own (commit/rollback are no-ops). Fine for testing.
- Each query is a network round trip (~100-300 ms), so it is slower than a local database.
"""
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal

import requests

apilevel = "2.0"
threadsafety = 1
paramstyle = "qmark"
sqlite_version = "3.45.0"  # D1 runs a recent SQLite; SQLAlchemy only uses this to pick features
sqlite_version_info = (3, 45, 0)

API_BASE = os.getenv("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")
BATCH_SIZE = 50
_session = requests.Session()


# ---------- DB-API exceptions ----------
class Warning(Exception):  # noqa: A001 - name required by DB-API
    pass


class Error(Exception):
    pass


class InterfaceError(Error):
    pass


class DatabaseError(Error):
    pass


class DataError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class IntegrityError(DatabaseError):
    pass


class InternalError(DatabaseError):
    pass


class ProgrammingError(DatabaseError):
    pass


class NotSupportedError(DatabaseError):
    pass


def _error_for(message: str) -> Error:
    low = message.lower()
    if "constraint failed" in low:
        return IntegrityError(message)
    if "syntax error" in low or "no such table" in low or "no such column" in low:
        return ProgrammingError(message)
    return OperationalError(message)


def _param(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        raise NotSupportedError("Binary values are not supported over the D1 HTTP API.")
    return v


def _prepare(sql: str) -> str:
    # D1 has one schema; SQLAlchemy writes PRAGMA main.table_info(...) etc.
    stripped = sql.lstrip()
    if stripped[:6].upper() == "PRAGMA":
        sql = sql.replace("PRAGMA main.", "PRAGMA ").replace('PRAGMA "main".', "PRAGMA ")
    return sql


# PRAGMAs SQLAlchemy may run that D1 doesn't support: answer them locally.
_LOCAL_PRAGMAS = {"pragma read_uncommitted": [("read_uncommitted",), [(0,)]]}


class Connection:
    def __init__(self, account_id: str, database_id: str, api_token: str, timeout: float = 30):
        if not (account_id and database_id and api_token):
            raise InterfaceError(
                "Set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_D1_DATABASE_ID and CLOUDFLARE_API_TOKEN in .env.")
        self.url = f"{API_BASE}/accounts/{account_id}/d1/database/{database_id}/raw"
        self.headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self.timeout = timeout
        self.isolation_level = None

    # SQLAlchemy / pysqlite hooks
    def create_function(self, *args, **kwargs):  # REGEXP etc. can't be registered on a remote database
        pass

    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def _post(self, body: dict) -> list[dict]:
        last = None
        for attempt in range(4):
            try:
                r = _session.post(self.url, headers=self.headers, data=json.dumps(body), timeout=self.timeout)
            except requests.RequestException as e:
                last = OperationalError(f"Can't reach Cloudflare D1: {e}")
            else:
                if r.status_code in (429, 500, 502, 503, 504):
                    last = OperationalError(f"Cloudflare D1 is busy (HTTP {r.status_code}).")
                else:
                    try:
                        data = r.json()
                    except ValueError:
                        raise OperationalError(f"Cloudflare D1 returned HTTP {r.status_code}: {r.text[:300]}")
                    if not data.get("success", False):
                        errors = data.get("errors") or []
                        msg = "; ".join(f"{e.get('message')} (code {e.get('code')})" for e in errors) or \
                              f"HTTP {r.status_code}"
                        raise _error_for(msg)
                    return data.get("result") or []
            time.sleep(0.5 * 2 ** attempt)
        raise last

    def run(self, sql: str, params=()) -> dict:
        return self._post({"sql": _prepare(sql), "params": [_param(p) for p in params or ()]})[0]

    def run_batch(self, statements: list[tuple[str, list]]) -> list[dict]:
        return self._post({"batch": [{"sql": _prepare(s), "params": [_param(p) for p in ps or ()]}
                                     for s, ps in statements]})


class Cursor:
    arraysize = 1

    def __init__(self, conn: Connection):
        self.connection = conn
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self._rows: list[tuple] = []
        self._pos = 0

    def _load(self, result: dict):
        res = result.get("results") or {}
        cols = res.get("columns") or []
        meta = result.get("meta") or {}
        self.description = [(c, None, None, None, None, None, None) for c in cols] if cols else None
        self._rows = [tuple(r) for r in res.get("rows") or []]
        self._pos = 0
        self.rowcount = meta.get("changes", -1) if not cols else -1
        self.lastrowid = meta.get("last_row_id")

    def execute(self, sql: str, params=()):
        norm = " ".join(sql.lower().split()).rstrip(";")
        if norm.startswith(("pragma temp.", 'pragma "temp".')):
            # D1 forbids the temp schema (SQLITE_AUTH) and the app never uses temp tables: report "nothing there".
            self.description = [(c, None, None, None, None, None, None)
                                for c in ("cid", "name", "type", "notnull", "dflt_value", "pk")]
            self._rows, self._pos, self.rowcount = [], 0, -1
            return self
        local = _LOCAL_PRAGMAS.get(norm)
        if local:
            cols, rows = local
            self.description = [(c, None, None, None, None, None, None) for c in cols]
            self._rows, self._pos, self.rowcount = list(rows), 0, -1
            return self
        self._load(self.connection.run(sql, params))
        return self

    def executemany(self, sql: str, seq_of_params):
        seq = list(seq_of_params)
        total = 0
        for i in range(0, len(seq), BATCH_SIZE):  # one HTTP call per 50 rows
            for result in self.connection.run_batch([(sql, p) for p in seq[i:i + BATCH_SIZE]]):
                total += (result.get("meta") or {}).get("changes", 0)
        self.description, self._rows, self.rowcount = None, [], total
        return self

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size=None):
        size = size or self.arraysize
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def close(self):
        self._rows = []

    def setinputsizes(self, *a):
        pass

    def setoutputsize(self, *a):
        pass

    def __iter__(self):
        return iter(self.fetchall())


def connect_from_env() -> Connection:
    return Connection(os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
                      os.getenv("CLOUDFLARE_D1_DATABASE_ID", "").strip(),
                      os.getenv("CLOUDFLARE_API_TOKEN", "").strip())
