"""Generate a tiny mono 16-bit WAV for offline fake-client testing — no
ffmpeg required. Writes a ~2 s 440 Hz tone at 16 kHz to sample.wav.

    uv run python scripts/make_sample_wav.py
"""
from __future__ import annotations

import math
import struct
import wave

SAMPLE_RATE = 16000
SECONDS = 2.0
FREQ_HZ = 440.0
AMPLITUDE = 12000  # well under int16 max (32767)


def main() -> None:
    n = int(SAMPLE_RATE * SECONDS)
    frames = bytearray()
    for i in range(n):
        sample = int(AMPLITUDE * math.sin(2 * math.pi * FREQ_HZ * i / SAMPLE_RATE))
        frames += struct.pack("<h", sample)
    with wave.open("sample.wav", "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(frames))
    print(f"wrote sample.wav ({n} samples, {SECONDS}s @ {SAMPLE_RATE} Hz)")


if __name__ == "__main__":
    main()
