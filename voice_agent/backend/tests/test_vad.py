"""RMS energy VAD."""
from __future__ import annotations

import math
import struct

from stackchan_voice.vad import RmsVad


def _silence(num_samples: int) -> bytes:
    return b"\x00\x00" * num_samples


def _tone(num_samples: int, amplitude: int, freq_hz: int = 440, sr: int = 16000) -> bytes:
    """Generate a single-tone PCM frame for VAD tests."""
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq_hz * n / sr))
        for n in range(num_samples)
    ]
    return struct.pack(f"<{num_samples}h", *samples)


def test_silence_is_not_speech():
    vad = RmsVad()
    assert vad.is_speech(_silence(320)) is False


def test_empty_frame_is_not_speech():
    vad = RmsVad()
    assert vad.is_speech(b"") is False


def test_loud_tone_is_speech():
    vad = RmsVad()
    # Half-scale int16 sine — well above the 0.02 default threshold.
    assert vad.is_speech(_tone(320, amplitude=16000)) is True


def test_quiet_tone_below_threshold():
    vad = RmsVad()
    # ~0.005 normalized RMS, well below 0.02.
    assert vad.is_speech(_tone(320, amplitude=200)) is False


def test_threshold_is_configurable():
    quiet = _tone(320, amplitude=200)
    assert RmsVad(speech_rms_threshold=0.001).is_speech(quiet) is True
    assert RmsVad(speech_rms_threshold=0.5).is_speech(quiet) is False
