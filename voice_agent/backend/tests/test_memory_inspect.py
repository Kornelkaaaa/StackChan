"""memory.db inspector — exercises each subcommand against a populated DB.

The tests use the async `db` module to set up fixtures (matches the rest of
the suite) and then call the CLI's command functions directly with a sync
sqlite3 connection. We don't go through argparse — that's argparse's job to
test, not ours.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from stackchan_voice import db
from stackchan_voice.cli import memory_inspect


def _open_sync(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


async def test_list_empty_db(tmp_path, capsys):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    conn = _open_sync(db_path)
    try:
        memory_inspect.cmd_list(conn)
    finally:
        conn.close()

    assert "(no sessions)" in capsys.readouterr().out


async def test_list_with_sessions_shows_header_and_row(tmp_path, capsys):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)
    async with db.connection(db_path) as conn_async:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn_async, now)
        await db.update_session_summary(
            conn_async, session_id=sid,
            summary="A test summary.", summary_model="gemini-2.5-flash",
        )

    conn = _open_sync(db_path)
    try:
        memory_inspect.cmd_list(conn)
    finally:
        conn.close()

    out = capsys.readouterr().out
    assert "id" in out and "started" in out
    assert str(sid) in out
    assert " y " in out  # summary column reads "y" when summary is set


async def test_show_includes_turns_and_summary(tmp_path, capsys):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)
    async with db.connection(db_path) as conn_async:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn_async, now)
        await db.insert_turn(
            conn_async,
            session_id=sid, turn_index=0,
            user_transcript="Hi Stack-chan",
            model_transcript="Hi Kornelia!",
            started_at=now, completed_at=now,
        )
        await db.update_session_summary(
            conn_async, session_id=sid,
            summary="They said hi.", summary_model="gemini-2.5-flash",
        )

    conn = _open_sync(db_path)
    try:
        memory_inspect.cmd_show(conn, sid)
    finally:
        conn.close()

    out = capsys.readouterr().out
    assert f"Session #{sid}" in out
    assert "Kornelia: Hi Stack-chan" in out
    assert "Stack-chan: Hi Kornelia!" in out
    assert "Summary" in out
    assert "They said hi." in out


async def test_show_missing_session_exits(tmp_path):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)

    conn = _open_sync(db_path)
    try:
        with pytest.raises(SystemExit):
            memory_inspect.cmd_show(conn, 9999)
    finally:
        conn.close()


async def test_stats_counts_sessions_turns_and_reasons(tmp_path, capsys):
    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)
    async with db.connection(db_path) as conn_async:
        now = datetime.now(timezone.utc).isoformat()
        for reason in ["silence", "silence", "timeout"]:
            sid = await db.insert_session(conn_async, now)
            await conn_async.execute(
                "UPDATE sessions SET end_reason = ?, ended_at = ? WHERE id = ?",
                (reason, now, sid),
            )
        await conn_async.commit()

    conn = _open_sync(db_path)
    try:
        memory_inspect.cmd_stats(conn)
    finally:
        conn.close()

    out = capsys.readouterr().out
    assert "Sessions:    3" in out
    assert "silence" in out and "2" in out
    assert "timeout" in out


def test_resolve_db_path_precedence(monkeypatch, tmp_path):
    """--db flag wins, then $DB_PATH, then default ./memory.db."""
    monkeypatch.delenv("DB_PATH", raising=False)
    assert memory_inspect._resolve_db_path(None).name == "memory.db"

    monkeypatch.setenv("DB_PATH", str(tmp_path / "from_env.db"))
    assert memory_inspect._resolve_db_path(None).name == "from_env.db"
    # Explicit --db overrides $DB_PATH.
    assert memory_inspect._resolve_db_path("override.db").name == "override.db"
