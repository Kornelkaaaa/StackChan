"""WebSocket endpoint + per-connection session orchestration.

A connection's lifetime: accept → expect ClientHello → loop {wait for
SessionOpen → run one session → loop}. The WS stays open across sessions
per the wire protocol; each button-press opens a new Gemini Live session
on the same socket.

A *session* runs three concurrent tasks under `asyncio.TaskGroup`:
  * inbound: read WS frames, VAD on PCM, forward to Gemini
  * outbound: read Gemini events, forward audio + control to WS,
    write completed turns to SQLite
  * watchdog: poll the Session state machine for silence/timeout closure

When one task signals end-of-session (via `_SessionEndSignal`), TaskGroup
cancels the siblings and the handler proceeds to cleanup: end-of-session row
update, summarizer call (Gemini Flash text), summary write-back, SessionClose
JSON to the client.

The Gemini client is dependency-injected via a `gemini_factory` callable so
tests can swap in `MockGeminiLive` without standing up a real session.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from . import db as db_module
from .config import Settings
from .gemini_live_mock import AudioOut, TurnEnd, TurnStart
from .protocol import (
    ClientClose,
    ClientHello,
    SessionClose,
    SessionOpen,
    SessionOpened,
    SpeakingEnd,
    SpeakingStart,
    client_message_adapter,
)
from .session import EndReason, Session
from .summarizer import summarize_session
from .vad import RmsVad

logger = logging.getLogger(__name__)

# Tradeoff: smaller = more accurate close timing, larger = less CPU on idle
# sessions. 500ms is well below the 30s silence threshold so it's invisible
# to the user.
_WATCHDOG_TICK_SEC = 0.5


# A factory that, given Settings and the per-session input sample rate,
# returns an async-context-manager-shaped Gemini client. Both `GeminiLive`
# and `MockGeminiLive` satisfy this — the mock is used in tests; the real
# client is wired in `main.py` for production. Using `Any` rather than a
# Protocol because the `receive()` method is an async generator, which is
# awkward to express as a Protocol.
GeminiFactory = Callable[[Settings, int], Any]


class _SessionEndSignal(Exception):
    """Raised by a session task to cooperatively end the whole session."""
    def __init__(self, reason: EndReason) -> None:
        super().__init__(reason)
        self.reason: EndReason = reason


async def websocket_endpoint(
    ws: WebSocket,
    settings: Settings,
    gemini_factory: GeminiFactory,
) -> None:
    await ws.accept()
    logger.info("ws_connected", extra={"client": str(ws.client)})
    try:
        hello = await _expect_client_hello(ws)
        if hello is None:
            return
        while True:
            msg = await _recv_control(ws)
            if msg is None:  # client disconnected
                return
            if isinstance(msg, ClientClose):
                logger.info("client_close_idle")
                return
            if isinstance(msg, SessionOpen):
                await _run_one_session(ws, settings, gemini_factory, hello)
                continue
            logger.warning(
                "unexpected_idle_message",
                extra={"got": type(msg).__name__},
            )
    finally:
        with suppress(Exception):
            await ws.close()
        logger.info("ws_closed")


async def _expect_client_hello(ws: WebSocket) -> ClientHello | None:
    msg = await _recv_control(ws)
    if not isinstance(msg, ClientHello):
        logger.warning(
            "missing_client_hello",
            extra={"got": type(msg).__name__ if msg else None},
        )
        return None
    logger.info(
        "client_hello",
        extra={
            "device_id": msg.device_id,
            "fw_version": msg.fw_version,
            "input_sr_hz": msg.input_sample_rate_hz,
        },
    )
    return msg


async def _recv_control(ws: WebSocket):
    """Receive one JSON control message. Returns None on disconnect.

    Stray binary frames outside a session are dropped — there's no Gemini
    session to send them to and the client shouldn't be sending audio yet.
    """
    while True:
        try:
            raw = await ws.receive()
        except WebSocketDisconnect:
            return None
        if raw.get("type") == "websocket.disconnect":
            return None
        text = raw.get("text")
        if text is None:
            continue
        try:
            return client_message_adapter.validate_json(text)
        except ValidationError:
            logger.warning("invalid_client_message", extra={"raw": text[:200]})
            continue


async def _send(ws: WebSocket, msg: BaseModel) -> None:
    await ws.send_text(msg.model_dump_json())


async def _run_one_session(
    ws: WebSocket,
    settings: Settings,
    gemini_factory: GeminiFactory,
    hello: ClientHello,
) -> None:
    """Run one Gemini Live conversation on this WebSocket.

    The aiosqlite connection is scoped to this function so it is always
    fully closed before we return to the outer per-WS message loop.
    """
    session = Session(
        silence_timeout_sec=settings.session_silence_timeout_sec,
        max_duration_sec=settings.session_max_duration_sec,
    )
    vad = RmsVad()

    async with db_module.connection(settings.db_path) as db:
        started_at = datetime.now(timezone.utc).isoformat()
        session_id = await db_module.insert_session(db, started_at)
        session.open(session_id=session_id, now=time.monotonic())
        await _send(ws, SessionOpened(session_id=session_id))
        logger.info("session_opened", extra={"session_id": session_id})

        # Mutable per-session turn state. The outbound loop is a nested
        # async function so it can close over these via `nonlocal`.
        turn_index = 0
        turn_started_at: str | None = None

        async def outbound_loop(gemini) -> None:
            nonlocal turn_index, turn_started_at
            async for event in gemini.receive():
                if isinstance(event, TurnStart):
                    turn_started_at = datetime.now(timezone.utc).isoformat()
                    await _send(ws, SpeakingStart())
                elif isinstance(event, AudioOut):
                    # Gemini Live output is 24 kHz PCM (input was 16 kHz).
                    # The device speaker config must match — see chunk 5.
                    await ws.send_bytes(event.pcm)
                elif isinstance(event, TurnEnd):
                    # Persist FIRST, signal SECOND. Two reasons:
                    #  (a) the client uses SpeakingEnd as the "turn done"
                    #      signal — if it arrives before the row is durable
                    #      and the connection then drops, we lose a turn
                    #      that the user already considers complete;
                    #  (b) a cooperative TaskGroup cancellation arriving
                    #      mid-INSERT can abort the write — the integration
                    #      test_two_turns_in_one_session catches exactly that.
                    completed_at = datetime.now(timezone.utc).isoformat()
                    await db_module.insert_turn(
                        db,
                        session_id=session_id,
                        turn_index=turn_index,
                        user_transcript=event.user_transcript,
                        model_transcript=event.model_transcript,
                        started_at=turn_started_at or completed_at,
                        completed_at=completed_at,
                    )
                    turn_index += 1
                    turn_started_at = None
                    await _send(ws, SpeakingEnd())

        end_reason: EndReason = "error"
        try:
            async with gemini_factory(settings, hello.input_sample_rate_hz) as gemini:
                try:
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(_inbound_loop(ws, session, vad, gemini), name="inbound")
                        tg.create_task(outbound_loop(gemini), name="outbound")
                        tg.create_task(_watchdog_loop(session), name="watchdog")
                except* _SessionEndSignal as eg:
                    end_reason = eg.exceptions[0].reason  # type: ignore[union-attr]
                except* WebSocketDisconnect:
                    end_reason = "client_disconnect"
        except Exception:
            logger.exception("session_unhandled_error", extra={"session_id": session_id})
            end_reason = "error"

        session.close(reason=end_reason)
        ended_at = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE sessions SET ended_at = ?, end_reason = ?, turn_count = ? WHERE id = ?",
            (ended_at, end_reason, turn_index, session_id),
        )
        await db.commit()

        # Summarizer is best-effort: a failure here mustn't take down the
        # session close path. Worst case the row simply has summary = NULL.
        try:
            summary = await summarize_session(
                db,
                session_id=session_id,
                api_key=settings.gemini_api_key,
                model=settings.summarizer_model_id,
            )
            if summary:
                await db_module.update_session_summary(
                    db,
                    session_id=session_id,
                    summary=summary,
                    summary_model=settings.summarizer_model_id,
                )
        except Exception:
            logger.exception("summarizer_error", extra={"session_id": session_id})

    with suppress(Exception):
        await _send(ws, SessionClose(reason=end_reason))
    logger.info(
        "session_closed",
        extra={"session_id": session_id, "end_reason": end_reason, "turns": turn_index},
    )


async def _inbound_loop(
    ws: WebSocket,
    session: Session,
    vad: RmsVad,
    gemini,
) -> None:
    while True:
        raw = await ws.receive()
        if raw.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(code=raw.get("code", 1000))
        text = raw.get("text")
        if text is not None:
            try:
                msg = client_message_adapter.validate_json(text)
            except ValidationError:
                logger.warning("invalid_message_in_session", extra={"raw": text[:200]})
                continue
            if isinstance(msg, ClientClose):
                raise _SessionEndSignal("client_disconnect")
            # ClientHello / SessionOpen inside a session don't make sense — ignore.
            continue
        pcm = raw.get("bytes")
        if pcm is None:
            continue
        if vad.is_speech(pcm):
            session.on_audio(now=time.monotonic())
        await gemini.send_audio(pcm)


async def _watchdog_loop(session: Session) -> None:
    while True:
        await asyncio.sleep(_WATCHDOG_TICK_SEC)
        reason = session.should_close(now=time.monotonic())
        if reason is not None:
            raise _SessionEndSignal(reason)
