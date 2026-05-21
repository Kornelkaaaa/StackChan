"""Shared pytest fixtures and import-time setup.

`GEMINI_API_KEY` is set here to a dummy value because `stackchan_voice.main`
constructs the FastAPI app at import time, which calls `Settings()`, which
fails fast when the key is missing. Tests that touch the real Gemini API
will override this in their own scope.
"""
from __future__ import annotations

import os

os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")
