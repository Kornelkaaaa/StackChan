"""One-shot session summarization via Gemini Flash text.

Runs once per session, on close, against the rows in the `turns` table.
Output is written back to `sessions.summary`. If the session had zero
turns (button-press with no actual speech), we skip the call entirely.

The Gemini network call is encapsulated in `_call_gemini` so tests can
monkeypatch it without standing up a real client.
"""
from __future__ import annotations

import logging

import aiosqlite
from google import genai

from . import db as db_module

logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """\
Summarize this conversation between Kornelia and Stack-chan in 1-3 sentences.
Focus on what the user wanted, what was discussed, and any decisions or
commitments. Write in past tense, third person ("Kornelia asked...", "Stack-chan
suggested..."). If the conversation was empty or off-topic, say so briefly.

---
{transcript}
---
"""


async def _call_gemini(*, api_key: str, model: str, prompt: str) -> str:
    """Single Flash text call. Isolated so tests can replace it."""
    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(model=model, contents=prompt)
    return response.text or ""


def _format_transcript(turns: list[aiosqlite.Row]) -> str:
    lines: list[str] = []
    for t in turns:
        user = t["user_transcript"]
        model = t["model_transcript"]
        if user:
            lines.append(f"Kornelia: {user}")
        if model:
            lines.append(f"Stack-chan: {model}")
    return "\n".join(lines)


async def summarize_session(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    api_key: str,
    model: str,
) -> str | None:
    """Build a summary for `session_id` and return it.

    Returns None if the session had no transcribable turns (no Gemini call made).
    Caller is responsible for persisting the result via
    `db.update_session_summary`.
    """
    turns = await db_module.get_session_turns(conn, session_id)
    transcript = _format_transcript(turns)
    if not transcript:
        logger.info("summarizer_skipped_empty", extra={"session_id": session_id})
        return None

    prompt = _PROMPT_TEMPLATE.format(transcript=transcript)
    raw = await _call_gemini(api_key=api_key, model=model, prompt=prompt)
    summary = raw.strip()
    logger.info(
        "summarizer_complete",
        extra={"session_id": session_id, "summary_chars": len(summary)},
    )
    return summary
