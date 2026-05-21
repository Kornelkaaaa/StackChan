"""WebSocket endpoint smoke tests using FastAPI's TestClient.

These cover the protocol handshake and a one-frame audio echo round-trip
through `MockGeminiLive` (injected via the gemini_factory parameter so
no real Gemini API call happens during tests). End-to-end audio streaming
against the real client is exercised manually via `fake_client.py`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stackchan_voice import config as config_module
from stackchan_voice.gemini_live_mock import MockGeminiLive
from stackchan_voice.main import build_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-dummy")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_memory.db"))
    monkeypatch.setattr(config_module, "_cached", None)
    settings = config_module.get_settings()
    # Inject the mock — the real GeminiLive would require network + API key.
    # Factory signature is (settings, input_sample_rate_hz); the mock ignores rate.
    return build_app(settings, gemini_factory=lambda _s, _rate: MockGeminiLive())


def test_handshake_opens_a_session(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "client_hello",
                "device_id": "test", "fw_version": "0.0.0",
            })
            ws.send_json({"type": "session_open"})

            opened = ws.receive_json()
            assert opened["type"] == "session_opened"
            assert isinstance(opened["session_id"], int)
            assert opened["session_id"] >= 1

            ws.send_json({"type": "client_close"})

            # Server may or may not get the SessionClose ack out before the
            # WS context tears down; that's fine for this smoke test.


def test_audio_frame_is_echoed(app):
    """One PCM frame in, the same frame back, framed by SpeakingStart/End."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "client_hello",
                "device_id": "test", "fw_version": "0.0.0",
            })
            ws.send_json({"type": "session_open"})
            assert ws.receive_json()["type"] == "session_opened"

            pcm = bytes([0x10, 0x20]) * 320  # 640 B ≈ one 20 ms frame
            ws.send_bytes(pcm)

            speaking_start = ws.receive_json()
            assert speaking_start["type"] == "speaking_start"

            echoed = ws.receive_bytes()
            assert echoed == pcm

            ws.send_json({"type": "client_close"})


def test_unknown_control_message_is_ignored(app):
    """Invalid JSON before session_open shouldn't kill the connection."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "client_hello",
                "device_id": "test", "fw_version": "0.0.0",
            })
            ws.send_text("{not even valid json")
            ws.send_json({"type": "garbage_type"})
            ws.send_json({"type": "session_open"})

            opened = ws.receive_json()
            assert opened["type"] == "session_opened"

            ws.send_json({"type": "client_close"})
