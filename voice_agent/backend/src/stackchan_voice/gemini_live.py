"""Real Gemini Live client — implements the contract documented in
`gemini_live_mock.py` so the WS server's outbound loop is unchanged.

Verified against `google-genai==2.5.x` directly from the installed SDK
before writing (`LiveConnectConfig.model_fields` enumerated):
  * `system_instruction`, `input_audio_transcription`, `output_audio_transcription`,
    `response_modalities` are real fields;
  * `client.aio.live.connect(model=..., config=...)` is an async context
    manager yielding an `AsyncSession`;
  * audio frames are sent as `types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")`.

Input audio is 16 kHz 16-bit LE PCM (we send that). Output audio is **24 kHz**
PCM — this is per the live-guide and is different from the input rate. The
WS server forwards the bytes verbatim; the device speaker must be configured
for 24 kHz playback.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from google import genai
from google.genai import types

from .gemini_live_mock import AudioOut, GeminiEvent, TurnEnd, TurnStart

logger = logging.getLogger(__name__)


class GeminiLive:
    """Async-context-manager-shaped wrapper around the Live API session.

    Lifetime: `__aenter__` opens the SDK connect context and stashes the
    resulting AsyncSession; `__aexit__` closes it. Between those, callers use
    `send_audio()` and iterate `receive()` the same way they do with
    `MockGeminiLive`.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        system_instruction: str,
        input_sample_rate_hz: int = 16000,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._system_instruction = system_instruction
        self._input_sample_rate_hz = input_sample_rate_hz
        self._connect_ctx = None  # the SDK's async-cm from live.connect(...)
        self._session = None      # the AsyncSession yielded by it

    async def __aenter__(self) -> "GeminiLive":
        client = genai.Client(api_key=self._api_key)
        config: types.LiveConnectConfigDict = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._system_instruction,
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }
        self._connect_ctx = client.aio.live.connect(model=self._model, config=config)
        self._session = await self._connect_ctx.__aenter__()
        logger.info("gemini_live_connected", extra={"model": self._model})
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._connect_ctx is not None:
            await self._connect_ctx.__aexit__(exc_type, exc, tb)
            self._connect_ctx = None
            self._session = None
        logger.info("gemini_live_disconnected")

    async def send_audio(self, pcm: bytes) -> None:
        assert self._session is not None, "send_audio outside of async-with"
        mime = f"audio/pcm;rate={self._input_sample_rate_hz}"
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=mime)
        )

    async def receive(self) -> AsyncIterator[GeminiEvent]:
        """Map SDK responses onto our internal GeminiEvent stream.

        One Gemini server-content message may carry transcription deltas,
        audio chunks, and the turn-complete flag — possibly all at once.
        We emit:
          * `TurnStart` once, just before the first AudioOut in a turn;
          * `AudioOut(pcm)` for every audio chunk inside `model_turn.parts`;
          * `TurnEnd(user_transcript, model_transcript)` when `turn_complete`
            is set, with the deltas accumulated across the whole turn.
        """
        assert self._session is not None, "receive outside of async-with"

        user_buf: list[str] = []
        model_buf: list[str] = []
        in_turn = False

        async for response in self._session.receive():
            sc = response.server_content
            if sc is None:
                continue

            if sc.input_transcription and sc.input_transcription.text:
                user_buf.append(sc.input_transcription.text)

            if sc.output_transcription and sc.output_transcription.text:
                model_buf.append(sc.output_transcription.text)

            if sc.model_turn and sc.model_turn.parts:
                for part in sc.model_turn.parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        if not in_turn:
                            yield TurnStart()
                            in_turn = True
                        yield AudioOut(pcm=inline.data)

            if sc.turn_complete:
                user_text = "".join(user_buf).strip() or None
                model_text = "".join(model_buf).strip() or None
                yield TurnEnd(user_transcript=user_text, model_transcript=model_text)
                user_buf.clear()
                model_buf.clear()
                in_turn = False
