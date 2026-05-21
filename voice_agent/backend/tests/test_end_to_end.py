"""End-to-end validation of the full Phase 1 backend loop.

Exercises:
    WS accept → client_hello → session_open → audio in → mock Gemini echo →
    TurnStart/SpeakingStart → audio out → turn-gap → TurnEnd/SpeakingEnd →
    DB turn row written → client_close → DB session row updated (ended_at,
    end_reason, turn_count) → summarizer called → DB summary written →
    SessionClose to client.

All offline: `MockGeminiLive` replaces the real client, `_call_gemini` in
the summarizer is stubbed. Tests inspect the resulting `memory.db` to prove
the pipeline persisted what we expect.

The unit tests for each component cover the pieces in isolation; this file
is what catches integration regressions where the pieces are individually
correct but wired together wrong.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from stackchan_voice import config as config_module
from stackchan_voice import summarizer
from stackchan_voice.gemini_live_mock import MockGeminiLive
from stackchan_voice.main import build_app


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    """Returns (build_fn, db_path). `build_fn(turn_gap_sec)` returns the app.

    Each invocation of the fixture sets up env vars + monkeypatches the
    summarizer's network seam to a deterministic stub. The same db_path
    is reused across `build_fn` calls so tests can build the app, exercise
    it, then read the db.
    """
    db_path = tmp_path / "memory.db"
    monkeypatch.setenv("GEMINI_API_KEY", "test-dummy")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(config_module, "_cached", None)

    async def fake_summarize(**_):
        # Leading/trailing whitespace is deliberate — verifies the strip
        # happens at the summarize_session layer, not just in _call_gemini.
        return "  They had a brief test exchange.  "

    monkeypatch.setattr(summarizer, "_call_gemini", fake_summarize)

    def build(
        turn_gap_sec: float = 0.2,
        fake_user_transcript: str | None = None,
        fake_model_transcript: str | None = None,
    ):
        settings = config_module.get_settings()
        return build_app(
            settings,
            gemini_factory=lambda _s, _rate: MockGeminiLive(
                turn_gap_sec=turn_gap_sec,
                fake_user_transcript=fake_user_transcript,
                fake_model_transcript=fake_model_transcript,
            ),
        )

    return build, db_path


def _hello_and_open(ws) -> int:
    ws.send_json({
        "type": "client_hello",
        "device_id": "e2e-test",
        "fw_version": "0.1.0",
    })
    ws.send_json({"type": "session_open"})
    opened = ws.receive_json()
    assert opened["type"] == "session_opened"
    return opened["session_id"]


def test_full_session_persists_and_summarizes(app_factory):
    build, db_path = app_factory
    app = build(
        fake_user_transcript="hi stack-chan",
        fake_model_transcript="hi kornelia!",
    )

    pcm = bytes([0x12, 0x34]) * 320  # one ~20 ms frame @ 16 kHz

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            sid = _hello_and_open(ws)

            ws.send_bytes(pcm)

            # Mock yields TurnStart on first audio → server sends SpeakingStart.
            assert ws.receive_json()["type"] == "speaking_start"
            # Echo.
            assert ws.receive_bytes() == pcm
            # After turn_gap_sec=0.2 of no audio, mock fires TurnEnd → SpeakingEnd.
            # receive_json blocks until the message arrives.
            assert ws.receive_json()["type"] == "speaking_end"

            ws.send_json({"type": "client_close"})

            # Wait for server-initiated SessionClose — proves the cleanup path
            # (DB writes, summarizer call) finished before we tear down.
            sc = ws.receive_json()
            assert sc["type"] == "session_close"
            assert sc["reason"] == "client_disconnect"

    # Inspect persisted state.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert session is not None
        assert session["ended_at"] is not None
        assert session["end_reason"] == "client_disconnect"
        assert session["turn_count"] == 1
        # Summarizer ran, output was stripped before persisting.
        assert session["summary"] == "They had a brief test exchange."
        assert session["summary_model"] == "gemini-2.5-flash"

        turns = conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
            (sid,),
        ).fetchall()
        assert len(turns) == 1
        assert turns[0]["turn_index"] == 0
        assert turns[0]["user_transcript"] == "hi stack-chan"
        assert turns[0]["model_transcript"] == "hi kornelia!"
    finally:
        conn.close()


def test_two_turns_in_one_session(app_factory):
    """Two send-bursts separated by a turn gap should write two turn rows."""
    build, db_path = app_factory
    app = build()

    pcm = bytes([0x12, 0x34]) * 320

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            sid = _hello_and_open(ws)

            for _ in range(2):
                ws.send_bytes(pcm)
                assert ws.receive_json()["type"] == "speaking_start"
                assert ws.receive_bytes() == pcm
                assert ws.receive_json()["type"] == "speaking_end"

            ws.send_json({"type": "client_close"})
            assert ws.receive_json()["type"] == "session_close"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        turns = conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
            (sid,),
        ).fetchall()
        assert [t["turn_index"] for t in turns] == [0, 1]

        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert session["turn_count"] == 2
    finally:
        conn.close()


def test_silent_session_has_no_turns_and_no_summary(app_factory):
    """Button-press with no real audio → session row, no turns, no summary call.

    Guards the summarizer's empty-session shortcut from the integration side.
    """
    build, db_path = app_factory
    app = build()

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            sid = _hello_and_open(ws)
            ws.send_json({"type": "client_close"})
            assert ws.receive_json()["type"] == "session_close"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert session["turn_count"] == 0
        assert session["summary"] is None       # summarizer short-circuited
        assert session["summary_model"] is None

        turns = conn.execute(
            "SELECT * FROM turns WHERE session_id = ?", (sid,)
        ).fetchall()
        assert turns == []
    finally:
        conn.close()
