"""The undo log — one generic table behind every "Undo" toast.

`undo_log` is deliberately domain-free: `kind` names an action, `payload`
carries whatever reversing it needs, and nothing here knows what either means.
A module that owns a destructive action registers its own reverser with
``@undo.register("its_kind")``, so a new kind never edits this file.

Undo is a reversal that re-asserts prior state, not a transaction rollback.
The delete has already committed and the device may already have seen it, so
reversing runs the same restore path the trash page would.

A reverser is handed the connection ``apply()`` already holds and must not open
its own: two write transactions against one SQLite file deadlock.
"""
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Callable

logger = logging.getLogger(__name__)

_DEFAULT_TTL_S = 24 * 3600
# Undone and expired rows are kept well past both, so a repeat undo answers
# "already undone" and an expired one answers 410, instead of both 404ing.
_SWEEP_GRACE_S = 7 * 24 * 3600

Reverser = Callable[[sqlite3.Connection, dict], dict]
_REVERSERS: dict[str, Reverser] = {}


class UnknownToken(Exception):
    """No undo_log row carries this token."""


class ExpiredToken(Exception):
    """The token is past its TTL and was never used."""


class UnregisteredKind(Exception):
    """No module has registered a reversal for this kind."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_state_db())
    conn.row_factory = sqlite3.Row
    return conn


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS undo_log (
        token      TEXT PRIMARY KEY,
        kind       TEXT NOT NULL,
        label      TEXT NOT NULL DEFAULT '',
        payload    TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        undone_at  INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_undo_created ON undo_log (created_at DESC)",
)


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Create the table and sweep rows that are long past useful. Idempotent."""
    if conn is not None:
        _init(conn)
        return
    with _connect() as own:
        _init(own)


def _init(conn: sqlite3.Connection) -> None:
    for statement in _DDL:
        conn.execute(statement)
    conn.execute(
        "DELETE FROM undo_log WHERE expires_at < ?", (int(time.time()) - _SWEEP_GRACE_S,)
    )


def register(kind: str) -> Callable[[Reverser], Reverser]:
    """Decorator registering the function that reverses one kind of action."""
    def decorate(fn: Reverser) -> Reverser:
        _REVERSERS[kind] = fn
        return fn
    return decorate


def record(
    kind: str,
    label: str,
    payload: dict,
    ttl_s: int = _DEFAULT_TTL_S,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Log one reversible action and return its token.

    Pass `conn` from inside an open write transaction; a second connection
    would block on the lock that transaction already holds.
    """
    token = uuid.uuid4().hex
    now = int(time.time())
    row = (token, kind, label, json.dumps(payload or {}), now, now + ttl_s)
    sql = (
        "INSERT INTO undo_log (token, kind, label, payload, created_at, expires_at) "
        "VALUES (?,?,?,?,?,?)"
    )
    if conn is not None:
        _init(conn)
        conn.execute(sql, row)
    else:
        with _connect() as own:
            _init(own)
            own.execute(sql, row)
    return token


def _done(row: sqlite3.Row, already: bool, result: dict) -> dict:
    return {
        "ok": True,
        "already_undone": already,
        "kind": row["kind"],
        "label": row["label"],
        "result": result,
    }


def apply(token: str) -> dict:
    """Reverse the action behind `token`, exactly once."""
    init_db()
    now = int(time.time())
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM undo_log WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise UnknownToken(token)
        if row["undone_at"] is not None:
            conn.rollback()
            return _done(row, True, {})
        if row["expires_at"] <= now:
            raise ExpiredToken(token)
        reverser = _REVERSERS.get(row["kind"])
        if reverser is None:
            raise UnregisteredKind(row["kind"])
        # Claiming before reversing is what makes a double-click safe.
        claimed = conn.execute(
            "UPDATE undo_log SET undone_at = ? WHERE token = ? AND undone_at IS NULL",
            (now, token),
        )
        if claimed.rowcount == 0:
            conn.rollback()
            return _done(row, True, {})
        result = reverser(conn, json.loads(row["payload"] or "{}"))
        conn.commit()
        logger.info("Undo applied: %s %s", row["kind"], token[:8])
        return _done(row, False, result)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def recent(limit: int = 5) -> list[dict]:
    """Still-actionable entries, newest first. `payload` is never exposed."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT token, kind, label, created_at, expires_at FROM undo_log "
            "WHERE undone_at IS NULL AND expires_at > ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (int(time.time()), limit),
        ).fetchall()
    return [dict(r) for r in rows]
