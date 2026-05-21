"""FastAPI app + uvicorn entrypoint.

Run with `python -m stackchan_voice.main` after `uv sync` and a populated
`.env`. The module-level `app` symbol is also importable for ASGI tooling
(`uvicorn stackchan_voice.main:app`) and for tests via `build_app(settings,
gemini_factory=...)`.

The Gemini client is dependency-injected via `gemini_factory` so tests can
substitute `MockGeminiLive` without touching the real API.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket

from . import db as db_module
from .config import Settings, get_settings
from .gemini_live import GeminiLive
from .logging_setup import configure_logging
from .ws_server import GeminiFactory, websocket_endpoint


def _load_personality(prompts_dir: Path) -> str:
    """Read the system-instruction text from `prompts/personality.md`.

    Loaded once per `build_app` call. Kornelia can edit the file freely;
    changes take effect on next backend restart.
    """
    personality_path = prompts_dir / "personality.md"
    return personality_path.read_text(encoding="utf-8").strip()


def _real_gemini_factory(settings: Settings, input_sample_rate_hz: int) -> GeminiLive:
    return GeminiLive(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model_id,
        system_instruction=_load_personality(settings.prompts_dir),
        input_sample_rate_hz=input_sample_rate_hz,
    )


def build_app(
    settings: Settings | None = None,
    gemini_factory: GeminiFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    gemini_factory = gemini_factory or _real_gemini_factory
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await db_module.init_db(settings.db_path)
        logger.info(
            "app_started",
            extra={
                "host": settings.backend_host,
                "port": settings.backend_port,
                "model": settings.gemini_model_id,
            },
        )
        yield
        logger.info("app_stopped")

    app = FastAPI(title="Stack-chan voice backend", lifespan=lifespan)

    @app.websocket("/ws")
    async def ws_route(websocket: WebSocket) -> None:
        await websocket_endpoint(websocket, settings, gemini_factory)

    return app


# Built at import time so `uvicorn stackchan_voice.main:app` works.
# Requires GEMINI_API_KEY in env — that's intentional (fail fast).
app = build_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "stackchan_voice.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        log_config=None,
        ws_ping_interval=settings.ws_ping_interval_sec,
        ws_ping_timeout=settings.ws_ping_interval_sec * 2,
    )


if __name__ == "__main__":
    main()
