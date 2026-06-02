"""Fake ESP32 client.

Connects to the backend WS endpoint, streams a WAV file as if it were
the StackChan microphone, and writes Gemini's audio response to a
second WAV. With the real Gemini Live client wired in (chunk 3+), the
reply is whatever Stack-chan actually says back.

Usage:
    uv run python scripts/fake_client.py --input sample.wav --output reply.wav

Input WAV: mono 16-bit PCM at any sample rate — the rate is auto-detected
and forwarded to the backend via `client_hello.input_sample_rate_hz`.
Gemini Live auto-resamples server-side, so 16 kHz / 24 kHz / 48 kHz all work.
Output WAV is written at 24 kHz (Gemini Live always returns 24 kHz audio).

Convert anything to a mono 16-bit WAV with:
    ffmpeg -i input.mp3 -ar 16000 -ac 1 -acodec pcm_s16le sample.wav
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import wave
from pathlib import Path

import websockets

logger = logging.getLogger("fake_client")

_OUTPUT_SAMPLE_RATE = 24000  # Gemini Live always returns 24 kHz
_FRAME_MS = 20
# The real device streams its mic continuously, so trailing silence/noise
# always follows the user's speech — that speech→silence transition is what
# Gemini Live's automatic VAD uses to detect end-of-turn. A WAV file stops
# abruptly, so we append silence to reproduce the device's behaviour and let
# the model decide the turn is over.
_TRAILING_SILENCE_SEC = 1.5
# After the last audio frame, hold the connection open so the model can
# detect end-of-speech, think, and stream its reply back. The real Gemini
# Live native-audio model needs several seconds; the echo mock is instant.
# We close as soon as the model finishes its turn (speaking_end), falling
# back to this ceiling if no reply ever arrives.
_REPLY_TIMEOUT_SEC = 30.0


def _read_wav(path: Path) -> tuple[bytes, int]:
    """Returns (pcm_bytes, sample_rate_hz). Requires mono 16-bit PCM."""
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1:
            raise SystemExit(f"WAV must be mono, got {wf.getnchannels()} channels")
        if wf.getsampwidth() != 2:
            raise SystemExit(f"WAV must be 16-bit, got {wf.getsampwidth() * 8}-bit")
        return wf.readframes(wf.getnframes()), wf.getframerate()


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


async def _stream_audio(ws, pcm: bytes, sample_rate_hz: int) -> None:
    """Stream PCM at real-time speed in 20 ms chunks."""
    frame_bytes = (sample_rate_hz // 1000) * _FRAME_MS * 2  # 16-bit samples
    frame_period = _FRAME_MS / 1000.0
    for i in range(0, len(pcm), frame_bytes):
        chunk = pcm[i : i + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
        await ws.send(chunk)
        await asyncio.sleep(frame_period)


async def run(input_wav: Path, output_wav: Path, url: str) -> None:
    pcm_in, sample_rate_hz = _read_wav(input_wav)
    logger.info(
        "loaded_wav input=%s bytes=%d sr=%d",
        input_wav, len(pcm_in), sample_rate_hz,
    )

    received_pcm = bytearray()
    server_closed = asyncio.Event()
    turn_done = asyncio.Event()  # set when the model finishes a reply turn

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "type": "client_hello",
            "device_id": "fake-client",
            "fw_version": "0.0.0",
            "input_sample_rate_hz": sample_rate_hz,
        }))
        await ws.send(json.dumps({"type": "session_open"}))

        opened = json.loads(await ws.recv())
        assert opened.get("type") == "session_opened", opened
        logger.info("session_opened id=%s", opened.get("session_id"))

        async def receive_loop() -> None:
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    received_pcm.extend(message)
                else:
                    event = json.loads(message)
                    logger.info("server_event: %s", event)
                    etype = event.get("type")
                    if etype == "speaking_end":
                        turn_done.set()
                    elif etype == "session_close":
                        server_closed.set()
                        return

        recv_task = asyncio.create_task(receive_loop())
        try:
            await _stream_audio(ws, pcm_in, sample_rate_hz)
            # Trailing silence so Gemini's VAD sees end-of-speech (see note above).
            silence = b"\x00" * (int(sample_rate_hz * _TRAILING_SILENCE_SEC) * 2)
            await _stream_audio(ws, silence, sample_rate_hz)
            logger.info("audio_sent waiting_for_reply (up to %.0fs)", _REPLY_TIMEOUT_SEC)
            try:
                await asyncio.wait_for(turn_done.wait(), timeout=_REPLY_TIMEOUT_SEC)
                logger.info("reply_complete")
            except asyncio.TimeoutError:
                logger.warning("no_reply_within_%.0fs", _REPLY_TIMEOUT_SEC)
            await ws.send(json.dumps({"type": "client_close"}))
            try:
                await asyncio.wait_for(server_closed.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("server_close_not_received_within_5s")
        finally:
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass

    _write_wav(output_wav, bytes(received_pcm), sample_rate=_OUTPUT_SAMPLE_RATE)
    logger.info(
        "wrote_wav output=%s bytes=%d sr=%d",
        output_wav, len(received_pcm), _OUTPUT_SAMPLE_RATE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reply.wav"))
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run(args.input, args.output, args.url))


if __name__ == "__main__":
    main()
