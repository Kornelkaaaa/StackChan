"""Offline echo backend — the real WS / session / DB stack, but the Gemini
Live client is swapped for the in-repo `MockGeminiLive` (echoes audio back).

Lets you exercise `fake_client.py` end-to-end with NO API key and NO Gemini
spend: it proves the WebSocket handshake, the audio framing round-trip, the
session state machine, and SQLite persistence. The post-session summarizer
still tries the real API and fails gracefully (row keeps summary = NULL).

This is a dev/demo helper, not production. The real entry point is
`python -m stackchan_voice.main`.

    uv run python scripts/mock_server.py
"""
from __future__ import annotations

import uvicorn

from stackchan_voice.config import get_settings
from stackchan_voice.gemini_live_mock import MockGeminiLive
from stackchan_voice.main import build_app

settings = get_settings()

app = build_app(
    settings,
    gemini_factory=lambda _settings, _rate: MockGeminiLive(
        fake_user_transcript="(mock) hello stack-chan",
        fake_model_transcript="(mock) echoing your audio back",
    ),
)


def main() -> None:
    uvicorn.run(
        app,
        host=settings.backend_host,
        port=settings.backend_port,
        log_config=None,
        ws_ping_interval=settings.ws_ping_interval_sec,
        ws_ping_timeout=settings.ws_ping_interval_sec * 2,
    )


if __name__ == "__main__":
    main()
