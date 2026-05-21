"""Session lifecycle as a pure state machine — no IO, no timers, no clock.

A `Session` represents one Gemini Live conversation that begins on a button
press and ends when one of:
  * `silence_timeout_sec` passes since the last incoming user-audio frame
  * `max_duration_sec` passes since `open()`
  * the client explicitly closes
  * the connection drops or errors

The current monotonic time is passed in as a parameter rather than read from
`time.monotonic()` directly. That keeps this module testable without sleeps
or freezegun-style patching — `test_session.py` drives transitions by
calling `should_close(now=...)` with arbitrary timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class State(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    CLOSED = "closed"


EndReason = Literal["silence", "timeout", "client_disconnect", "error"]


@dataclass
class Session:
    silence_timeout_sec: float
    max_duration_sec: float

    state: State = State.IDLE
    session_id: Optional[int] = None
    opened_at: Optional[float] = None       # monotonic seconds
    last_audio_at: Optional[float] = None   # monotonic seconds
    end_reason: Optional[EndReason] = None

    def open(self, *, session_id: int, now: float) -> None:
        if self.state is not State.IDLE:
            raise RuntimeError(f"open() requires IDLE state, got {self.state}")
        self.state = State.ACTIVE
        self.session_id = session_id
        self.opened_at = now
        self.last_audio_at = now

    def on_audio(self, *, now: float) -> None:
        """Mark that a user-audio frame just arrived. Resets the silence timer."""
        if self.state is State.ACTIVE:
            self.last_audio_at = now

    def should_close(self, *, now: float) -> Optional[EndReason]:
        """Return a close reason if the session has timed out, else None.

        Max-duration is checked before silence so a long but lively session
        reports the more informative end reason on close.
        """
        if self.state is not State.ACTIVE:
            return None
        assert self.opened_at is not None and self.last_audio_at is not None
        if now - self.opened_at >= self.max_duration_sec:
            return "timeout"
        if now - self.last_audio_at >= self.silence_timeout_sec:
            return "silence"
        return None

    def close(self, *, reason: EndReason) -> None:
        """Transition to CLOSED. Idempotent — first reason wins."""
        if self.state is State.CLOSED:
            return
        self.state = State.CLOSED
        self.end_reason = reason
