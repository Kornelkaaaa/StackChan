"""Mock Gemini Live client — echoes incoming audio, defines the chunk-3 contract.

The shape of this class is the contract the real Gemini Live client must
satisfy in chunk 3: an async context manager exposing `send_audio(pcm)` and
an async-iterable `receive()` of `GeminiEvent` items. Everything else in the
backend speaks only to that contract, so the swap should be a one-file change.

Turn semantics in the mock:
  * a turn starts on the first audio frame after the last TurnEnd;
  * the turn ends when no audio has arrived for `turn_gap_sec` (coarse stand-in
    for what the real model does with native VAD).
  * the mock emits `TurnStart`, then `AudioOut` frames echoing the inbound audio,
    then `TurnEnd`. The server uses `TurnStart`/`TurnEnd` to drive the client's
    speaking-state UI.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Union


@dataclass
class AudioOut:
    pcm: bytes


@dataclass
class TurnStart:
    pass


@dataclass
class TurnEnd:
    user_transcript: str | None = None
    model_transcript: str | None = None


GeminiEvent = Union[AudioOut, TurnStart, TurnEnd]


class MockGeminiLive:
    def __init__(
        self,
        *,
        turn_gap_sec: float = 0.6,
        fake_user_transcript: str | None = None,
        fake_model_transcript: str | None = None,
    ) -> None:
        self._turn_gap_sec = turn_gap_sec
        self._fake_user_transcript = fake_user_transcript
        self._fake_model_transcript = fake_model_transcript
        self._inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False

    async def __aenter__(self) -> "MockGeminiLive":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed:
            raise RuntimeError("send_audio called on closed mock")
        await self._inbound.put(pcm)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._inbound.put(None)  # sentinel unblocks receive()

    async def receive(self) -> AsyncIterator[GeminiEvent]:
        in_turn = False
        while True:
            try:
                pcm = await asyncio.wait_for(
                    self._inbound.get(),
                    timeout=self._turn_gap_sec if in_turn else None,
                )
            except asyncio.TimeoutError:
                yield TurnEnd(
                    user_transcript=self._fake_user_transcript,
                    model_transcript=self._fake_model_transcript,
                )
                in_turn = False
                continue
            if pcm is None:
                if in_turn:
                    yield TurnEnd(
                        user_transcript=self._fake_user_transcript,
                        model_transcript=self._fake_model_transcript,
                    )
                return
            if not in_turn:
                yield TurnStart()
                in_turn = True
            yield AudioOut(pcm=pcm)
