"""Application configuration loaded from environment variables and `.env`.

`GEMINI_API_KEY` is declared with no default, so pydantic-settings raises
`ValidationError` at construction time if it is missing — a half-configured
server is worse than one that refuses to start.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    gemini_api_key: str = Field(
        ...,
        description="API key for the Gemini API. Required — startup fails without it.",
    )

    # Live (native-audio dialog) model for the realtime voice session.
    gemini_model_id: str = "gemini-3.1-flash-live-preview"
    # Text Flash model used for the one-shot post-session summary.
    summarizer_model_id: str = "gemini-2.5-flash"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8765

    db_path: Path = Path("memory.db")
    prompts_dir: Path = Path("prompts")

    log_level: str = "INFO"

    ws_ping_interval_sec: float = 20.0

    session_silence_timeout_sec: float = 30.0
    session_max_duration_sec: float = 300.0


_cached: Settings | None = None


def get_settings() -> Settings:
    """Lazy singleton. Tests can patch environment before the first call."""
    global _cached
    if _cached is None:
        _cached = Settings()  # type: ignore[call-arg]
    return _cached
