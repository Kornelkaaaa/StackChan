# Stack-chan voice backend

Local backend for the Stack-chan voice agent. Accepts a WebSocket audio stream
from the M5Stack StackChan device, bridges it to the Gemini Live API, and logs
each turn to SQLite.

This is Phase 1 — see the project root for the full plan and later phases.

## Setup

```sh
cd voice_agent/backend
uv sync
cp .env.example .env
# then edit .env and set GEMINI_API_KEY
```

## Run the tests

```sh
uv run pytest
```

## Run the server

Lands in chunk 2. Once it does:

```sh
uv run python -m stackchan_voice.main
```
