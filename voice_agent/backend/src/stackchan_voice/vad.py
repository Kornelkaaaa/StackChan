"""Voice activity detection — energy-based, stateless, no extra dependencies.

A PCM frame is "speech" when its RMS amplitude (normalized to [0, 1] against
the int16 max) exceeds a threshold. Default ~0.02 (≈ −34 dBFS) empirically
passes spoken voice and rejects most ambient room tone.

Known Phase-1 limitation: pure RMS energy is brittle. Steady background noise
(HVAC, laptop fan, even the user's own breathing close to the mic) clears the
threshold and looks like speech to this detector, which delays the silence
timer firing and keeps Gemini Live sessions open longer than they should be.

Upgrade path when this hurts: Silero VAD —
  https://github.com/snakers4/silero-vad
A small ONNX model runnable under onnxruntime, ~few-ms latency per frame,
dramatically better discrimination. Drop-in replacement: keep the same
`is_speech(pcm: bytes) -> bool` contract and swap the implementation.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from math import sqrt


_INT16_MAX = 32768.0


@dataclass
class RmsVad:
    speech_rms_threshold: float = 0.02

    def is_speech(self, pcm_le16: bytes) -> bool:
        sample_count = len(pcm_le16) // 2
        if sample_count == 0:
            return False
        samples = struct.unpack(f"<{sample_count}h", pcm_le16[: sample_count * 2])
        sum_sq = sum(s * s for s in samples)
        rms_normalized = sqrt(sum_sq / sample_count) / _INT16_MAX
        return rms_normalized >= self.speech_rms_threshold
