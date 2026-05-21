"""WebSocket wire protocol — JSON text frames only.

Binary frames on the same socket carry raw PCM (16-bit LE @ 16 kHz mono) and
are not modeled here; this module covers only the JSON control messages.

Messages are discriminated by their `type` field; pydantic dispatches on it
so the WS handler can call `client_message_adapter.validate_python(json)`
and get the right concrete class back. `extra="forbid"` ensures any drift
between firmware and backend surfaces as a `ValidationError` at the seam
rather than as silent dropped fields downstream.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- Client (ESP32) -> Server -----------------------------------------------

class ClientHello(_Frame):
    type: Literal["client_hello"] = "client_hello"
    device_id: str
    fw_version: str
    # Rate at which the client will stream mic PCM (binary frames).
    # The backend pins `audio/pcm;rate=<this>` in the Gemini Live mime_type;
    # Gemini auto-resamples to its internal 16 kHz. Defaults to 16 kHz so
    # `fake_client.py` and old test fixtures keep working without changes.
    input_sample_rate_hz: int = 16000


class SessionOpen(_Frame):
    type: Literal["session_open"] = "session_open"


class ClientClose(_Frame):
    type: Literal["client_close"] = "client_close"


# ---- Server -> Client -------------------------------------------------------

class SessionOpened(_Frame):
    type: Literal["session_opened"] = "session_opened"
    session_id: int


class SpeakingStart(_Frame):
    type: Literal["speaking_start"] = "speaking_start"


class SpeakingEnd(_Frame):
    type: Literal["speaking_end"] = "speaking_end"


class SessionClose(_Frame):
    type: Literal["session_close"] = "session_close"
    reason: Literal["silence", "timeout", "client_disconnect", "error"]


# ---- Discriminated unions ---------------------------------------------------

ClientMessage = Annotated[
    Union[ClientHello, SessionOpen, ClientClose],
    Field(discriminator="type"),
]
ServerMessage = Annotated[
    Union[SessionOpened, SpeakingStart, SpeakingEnd, SessionClose],
    Field(discriminator="type"),
]

client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
