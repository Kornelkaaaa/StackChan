"""SQLite schema and async accessors for sessions and turns.

The schema is intentionally minimal — Phase 2 RAG can add an embeddings table
as a non-breaking ALTER. `PRAGMA foreign_keys = ON` must be set on every
connection: SQLite parses FOREIGN KEY constraints by default but does not
enforce them without that pragma, so `test_foreign_keys_are_enforced` guards
against future regressions.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT    NOT NULL,
    ended_at      TEXT,
    end_reason    TEXT,
    summary       TEXT,
    summary_model TEXT,
    turn_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_index       INTEGER NOT NULL,
    user_transcript  TEXT,
    model_transcript TEXT,
    started_at       TEXT    NOT NULL,
    completed_at     TEXT    NOT NULL,
    UNIQUE(session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_turns_session     ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_started_at  ON turns(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_started  ON sessions(started_at);
"""


async def init_db(db_path: Path) -> None:
    """Create the schema if missing. Idempotent — safe to call on every startup."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
    logger.info("db_initialised", extra={"db_path": str(db_path)})


@asynccontextmanager
async def connection(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection with FK enforcement and Row factory; close on exit.

    Use as `async with connection(path) as conn:`. The context manager owns
    the underlying worker thread; mixing this with a separate `await
    aiosqlite.connect(...)` on the same Connection object will try to start
    that thread twice.
    """
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = aiosqlite.Row
        yield conn


async def insert_session(conn: aiosqlite.Connection, started_at: str) -> int:
    cur = await conn.execute(
        "INSERT INTO sessions (started_at) VALUES (?)", (started_at,)
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_turn(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    turn_index: int,
    user_transcript: str | None,
    model_transcript: str | None,
    started_at: str,
    completed_at: str,
) -> int:
    cur = await conn.execute(
        "INSERT INTO turns "
        "(session_id, turn_index, user_transcript, model_transcript, started_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, turn_index, user_transcript, model_transcript, started_at, completed_at),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def get_session_turns(
    conn: aiosqlite.Connection, session_id: int
) -> list[aiosqlite.Row]:
    """Return all turns for a session, ordered by turn_index ascending."""
    async with conn.execute(
        "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index ASC",
        (session_id,),
    ) as cur:
        return list(await cur.fetchall())


async def update_session_summary(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    summary: str,
    summary_model: str,
) -> None:
    await conn.execute(
        "UPDATE sessions SET summary = ?, summary_model = ? WHERE id = ?",
        (summary, summary_model, session_id),
    )
    await conn.commit()
