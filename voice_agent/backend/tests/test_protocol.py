"""Round-trip serialization and validation for the WS wire-protocol messages."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackchan_voice.protocol import (
    ClientClose,
    ClientHello,
    SessionClose,
    SessionOpen,
    SessionOpened,
    SpeakingEnd,
    SpeakingStart,
    client_message_adapter,
    server_message_adapter,
)


@pytest.mark.parametrize(
    "msg",
    [
        ClientHello(device_id="stack-001", fw_version="0.1.0"),
        SessionOpen(),
        ClientClose(),
    ],
)
def test_client_messages_round_trip(msg):
    rebuilt = client_message_adapter.validate_python(msg.model_dump())
    assert rebuilt == msg


@pytest.mark.parametrize(
    "msg",
    [
        SessionOpened(session_id=42),
        SpeakingStart(),
        SpeakingEnd(),
        SessionClose(reason="silence"),
        SessionClose(reason="timeout"),
        SessionClose(reason="client_disconnect"),
        SessionClose(reason="error"),
    ],
)
def test_server_messages_round_trip(msg):
    rebuilt = server_message_adapter.validate_python(msg.model_dump())
    assert rebuilt == msg


def test_unknown_message_type_is_rejected():
    with pytest.raises(ValidationError):
        client_message_adapter.validate_python({"type": "not_a_real_type"})


def test_invalid_close_reason_is_rejected():
    with pytest.raises(ValidationError):
        server_message_adapter.validate_python(
            {"type": "session_close", "reason": "bogus"}
        )


def test_extra_field_is_rejected():
    """`extra='forbid'` catches firmware/backend protocol drift at the seam."""
    with pytest.raises(ValidationError):
        client_message_adapter.validate_python(
            {"type": "session_open", "extra_junk": 1}
        )
