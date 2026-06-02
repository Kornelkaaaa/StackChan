# Stack-chan voice backend

Local Python backend for the Stack-chan voice agent. Accepts a WebSocket
audio stream from the StackChan device (or `fake_client.py` for testing),
bridges it to the Gemini Live API, logs each turn to SQLite, and runs a
one-shot Gemini Flash summary on session close.

Phase 1, chunks 1–4 + 5a complete. Phase 1 chunk 5b (device audio
streaming) is the remaining piece — see `../firmware/voice_agent/`.

## Setup

```sh
cd voice_agent/backend
uv sync
cp .env.example .env
# Edit .env and at minimum set GEMINI_API_KEY.
```

## Configuration (`.env`)

Required:

| Key | What it does |
|---|---|
| `GEMINI_API_KEY` | Your Gemini API key. Startup fails with a `ValidationError` if missing. |

Optional (defaults shown):

| Key | Default | Notes |
|---|---|---|
| `GEMINI_MODEL_ID` | `gemini-3.1-flash-live-preview` | Live native-audio model. Verified current as of May 2026. |
| `SUMMARIZER_MODEL_ID` | `gemini-2.5-flash` | Text Flash model for the post-session summary. |
| `BACKEND_HOST` | `127.0.0.1` | Set to `0.0.0.0` to accept connections from the ESP32 on the same LAN. |
| `BACKEND_PORT` | `8765` | |
| `DB_PATH` | `memory.db` | SQLite file. Relative paths are resolved from the working directory at startup. |
| `PROMPTS_DIR` | `prompts` | Directory containing `personality.md`. |
| `LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. JSON-formatted on stdout. |
| `WS_PING_INTERVAL_SEC` | `20` | Server-initiated WS ping. ESP32 Wi-Fi stacks silently drop idle sockets at ~60 s; pinging well below keeps the socket alive. |
| `SESSION_SILENCE_TIMEOUT_SEC` | `30` | Session ends after this many seconds of no user audio. |
| `SESSION_MAX_DURATION_SEC` | `300` | Hard ceiling per session regardless of activity. |

The input audio sample rate is **not** an env var — it's negotiated
per session via `ClientHello.input_sample_rate_hz`. `fake_client.py`
auto-detects from the WAV header; the ESP32 firmware sends 24000.

## Run the server

```sh
uv run python -m stackchan_voice.main
```

You'll see JSON log lines like:

```
{"ts":"...","level":"INFO","logger":"stackchan_voice.main","msg":"app_started","host":"127.0.0.1","port":8765,"model":"gemini-3.1-flash-live-preview"}
```

Ctrl+C cleanly shuts down (lifespan unwinds, DB connections close).

## Run the tests

```sh
uv run pytest -v
```

49 tests at the time of writing — protocol round-trips, session state
machine, VAD, DB helpers, summarizer (mocked), WS endpoint smoke tests,
end-to-end pipeline (DB persistence + summarizer integration), memory
inspector. All offline — no real Gemini calls.

## Talk to the backend with a fake client

`scripts/fake_client.py` streams a WAV file to the server as if it
were the ESP32, and writes Gemini's reply to another WAV.

```sh
# Any mono 16-bit WAV at any sample rate. Convert if needed:
ffmpeg -i input.mp3 -ar 16000 -ac 1 -acodec pcm_s16le sample.wav

# In a second terminal (server already running):
uv run python scripts/fake_client.py --input sample.wav --output reply.wav
```

`reply.wav` is 24 kHz mono — that's Gemini Live's fixed output rate.
Play it in any audio app.

## Inspect the conversation database

After at least one session has happened, the SQLite file has data:

```sh
uv run stackchan-memory list           # all sessions, newest first
uv run stackchan-memory show 1         # transcripts + summary for session 1
uv run stackchan-memory stats          # totals + end-reason histogram
uv run stackchan-memory --db other.db list
```

`--db` overrides `$DB_PATH` overrides `./memory.db`.

## Personality

The agent's system prompt lives in `prompts/personality.md`. Edit it
freely; changes take effect on the next backend restart (it's loaded
once at app build).

## Where the code lives

```
src/stackchan_voice/
├── main.py             # FastAPI app + uvicorn entry. Wires the Gemini factory.
├── config.py           # pydantic-settings; hard-fails on missing GEMINI_API_KEY.
├── logging_setup.py    # JSON formatter, one record per stdout line.
├── protocol.py         # WS wire-protocol messages (pydantic discriminated unions).
├── session.py          # IDLE → ACTIVE → CLOSED state machine (pure, no IO).
├── vad.py              # RMS energy VAD. Phase-1 limitation noted; upgrade = Silero.
├── db.py               # SQLite schema + async helpers (aiosqlite).
├── ws_server.py        # WS endpoint, per-session TaskGroup orchestration.
├── gemini_live.py      # Real Gemini Live client (google-genai 2.5).
├── gemini_live_mock.py # Echo mock; defines the contract the real client satisfies.
├── summarizer.py       # One-shot Flash text call on session close.
└── cli/
    └── memory_inspect.py # `stackchan-memory` console entry point.
```

## See also

- `../firmware/voice_agent/` — ESP-IDF firmware for the StackChan device.
- `../../firmware/` — the factory M5Stack StackChan firmware (untouched,
  referenced for the pin map in `voice_agent/firmware/voice_agent/main/board_config.h`).
