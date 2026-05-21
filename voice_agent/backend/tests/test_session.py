"""Session lifecycle state machine."""
from __future__ import annotations

import pytest

from stackchan_voice.session import Session, State


@pytest.fixture
def session():
    return Session(silence_timeout_sec=30.0, max_duration_sec=300.0)


def test_starts_idle(session):
    assert session.state is State.IDLE
    assert session.session_id is None


def test_open_transitions_to_active(session):
    session.open(session_id=7, now=100.0)
    assert session.state is State.ACTIVE
    assert session.session_id == 7
    assert session.opened_at == 100.0
    assert session.last_audio_at == 100.0


def test_double_open_is_rejected(session):
    session.open(session_id=1, now=0.0)
    with pytest.raises(RuntimeError):
        session.open(session_id=2, now=1.0)


def test_silence_timeout_triggers_close(session):
    session.open(session_id=1, now=0.0)
    assert session.should_close(now=29.9) is None
    assert session.should_close(now=30.0) == "silence"


def test_on_audio_resets_silence_timer(session):
    session.open(session_id=1, now=0.0)
    session.on_audio(now=20.0)
    assert session.should_close(now=49.9) is None
    assert session.should_close(now=50.0) == "silence"


def test_max_duration_triggers_close_even_with_constant_audio(session):
    session.open(session_id=1, now=0.0)
    for t in range(0, 300, 10):
        session.on_audio(now=float(t))
    assert session.should_close(now=300.0) == "timeout"


def test_max_duration_wins_when_both_apply(session):
    """When silence and max-duration would both fire, max-duration is reported.

    Documents the deliberate priority in `should_close`: the more informative
    end reason takes precedence.
    """
    session.open(session_id=1, now=0.0)
    assert session.should_close(now=400.0) == "timeout"


def test_close_is_idempotent_and_first_reason_wins(session):
    session.open(session_id=1, now=0.0)
    session.close(reason="silence")
    session.close(reason="error")  # ignored
    assert session.state is State.CLOSED
    assert session.end_reason == "silence"


def test_should_close_returns_none_after_close(session):
    session.open(session_id=1, now=0.0)
    session.close(reason="client_disconnect")
    assert session.should_close(now=99999.0) is None


def test_on_audio_ignored_outside_active_state(session):
    session.on_audio(now=1.0)
    assert session.last_audio_at is None

    session.open(session_id=1, now=0.0)
    session.close(reason="error")
    last = session.last_audio_at
    session.on_audio(now=999.0)
    assert session.last_audio_at == last
