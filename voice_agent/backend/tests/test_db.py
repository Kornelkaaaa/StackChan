from datetime import datetime, timezone

import aiosqlite
import pytest

from stackchan_voice import db


async def test_init_creates_expected_tables(tmp_path):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}

    assert {"sessions", "turns"}.issubset(tables)


async def test_foreign_keys_are_enforced(tmp_path):
    """Without `PRAGMA foreign_keys = ON` this insert would silently succeed.

    Regression guard — losing FK enforcement would let orphan turns accumulate.
    """
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    now = datetime.now(timezone.utc).isoformat()
    async with db.connection(db_path) as conn:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO turns "
                "(session_id, turn_index, started_at, completed_at) "
                "VALUES (?, ?, ?, ?)",
                (9999, 0, now, now),
            )
            await conn.commit()


async def test_insert_session_returns_rowid(tmp_path):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)
        assert sid >= 1


async def test_insert_turn_and_fetch_in_order(tmp_path):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)

        # Insert turns out of order — they should come back ordered by turn_index.
        await db.insert_turn(
            conn,
            session_id=sid, turn_index=1,
            user_transcript="second q", model_transcript="second a",
            started_at=now, completed_at=now,
        )
        await db.insert_turn(
            conn,
            session_id=sid, turn_index=0,
            user_transcript="first q", model_transcript="first a",
            started_at=now, completed_at=now,
        )

        rows = await db.get_session_turns(conn, sid)
        assert [r["turn_index"] for r in rows] == [0, 1]
        assert rows[0]["user_transcript"] == "first q"
        assert rows[1]["model_transcript"] == "second a"


async def test_unique_constraint_on_session_turn_index(tmp_path):
    """Two rows with the same (session_id, turn_index) are rejected."""
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)
        await db.insert_turn(
            conn,
            session_id=sid, turn_index=0,
            user_transcript=None, model_transcript=None,
            started_at=now, completed_at=now,
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await db.insert_turn(
                conn,
                session_id=sid, turn_index=0,
                user_transcript=None, model_transcript=None,
                started_at=now, completed_at=now,
            )


async def test_update_session_summary(tmp_path):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)
        await db.update_session_summary(
            conn, session_id=sid,
            summary="They discussed the weather briefly.",
            summary_model="gemini-2.5-flash",
        )

        async with conn.execute(
            "SELECT summary, summary_model FROM sessions WHERE id = ?",
            (sid,),
        ) as cur:
            row = await cur.fetchone()
        assert row["summary"] == "They discussed the weather briefly."
        assert row["summary_model"] == "gemini-2.5-flash"
