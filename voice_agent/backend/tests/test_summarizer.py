"""Summarizer logic, with the Gemini network call monkeypatched.

We never want a real API call from a test run — flaky and would cost money.
The summarizer's `_call_gemini` is the single seam where the network would
happen; replacing it lets us assert on the prompt we build and on the
empty-session shortcut.
"""
from __future__ import annotations

from datetime import datetime, timezone

from stackchan_voice import db, summarizer


async def test_empty_session_returns_none_and_makes_no_call(tmp_path, monkeypatch):
    calls: list[dict] = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        return "should-not-run"

    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)
    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)

        result = await summarizer.summarize_session(
            conn, session_id=sid, api_key="x", model="gemini-2.5-flash",
        )

    assert result is None
    assert calls == []


async def test_summary_builds_transcript_and_returns_text(tmp_path, monkeypatch):
    captured: dict = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return "  Kornelia and Stack-chan chatted about lunch.  "

    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)
    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)
        await db.insert_turn(
            conn,
            session_id=sid, turn_index=0,
            user_transcript="What should I eat for lunch?",
            model_transcript="How about a sandwich?",
            started_at=now, completed_at=now,
        )
        await db.insert_turn(
            conn,
            session_id=sid, turn_index=1,
            user_transcript="Boring. Anything else?",
            model_transcript="Try the ramen place.",
            started_at=now, completed_at=now,
        )

        result = await summarizer.summarize_session(
            conn, session_id=sid, api_key="x", model="gemini-2.5-flash",
        )

    # Whitespace was stripped.
    assert result == "Kornelia and Stack-chan chatted about lunch."
    # The prompt got both turns, attributed correctly, in order.
    assert "Kornelia: What should I eat for lunch?" in captured["prompt"]
    assert "Stack-chan: How about a sandwich?" in captured["prompt"]
    assert "Kornelia: Boring. Anything else?" in captured["prompt"]
    assert "Stack-chan: Try the ramen place." in captured["prompt"]
    # The configured model + key were forwarded.
    assert captured["api_key"] == "x"
    assert captured["model"] == "gemini-2.5-flash"


async def test_turn_with_only_one_side_transcribed(tmp_path, monkeypatch):
    """If one side of a turn has no transcript, the other side is still included."""
    captured: dict = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return "fine"

    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    db_path = tmp_path / "memory.db"
    await db.init_db(db_path)
    async with db.connection(db_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        sid = await db.insert_session(conn, now)
        await db.insert_turn(
            conn,
            session_id=sid, turn_index=0,
            user_transcript=None,  # mic glitch — no user audio transcribed
            model_transcript="I didn't catch that, can you say again?",
            started_at=now, completed_at=now,
        )

        result = await summarizer.summarize_session(
            conn, session_id=sid, api_key="x", model="gemini-2.5-flash",
        )

    assert result == "fine"
    prompt = captured["prompt"]
    assert "Stack-chan: I didn't catch that" in prompt
    # No "Kornelia:" prefix should appear when user_transcript is None.
    assert "Kornelia:" not in prompt
