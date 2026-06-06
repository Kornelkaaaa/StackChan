# Stack-chan voice agent

A personal voice AI agent running on the official M5Stack **StackChan** (ESP32-S3 /
CoreS3) hardware, with a Python backend on the laptop that bridges audio to
**Gemini Live**. You tap the device, talk, and Stack-chan talks back — cheerful,
a little sassy, one or two sentences. Every conversation is logged to SQLite and
auto-summarised.

```
  StackChan device  ──mic PCM──►  Python backend  ──►  Gemini Live
   (CoreS3, Arduino)  ◄─reply──    (FastAPI + WS)   ◄──  (audio in/out)
                                        │
                                        ▼
                                   SQLite (memory.db)  ── per-session summary
```

- **`backend/`** — Python backend (FastAPI + WebSocket → Gemini Live → SQLite). See [backend/README.md](backend/README.md).
- **`firmware/voice_agent/`** — ESP-IDF device firmware (full-duplex). Reference implementation; see [firmware README](firmware/voice_agent/README.md).
- **`firmware_arduino/`** — Arduino / M5Unified device firmware (half-duplex). The path we're building on now (not started yet).

---

## 📍 Where we left off — 2026-06-06

### ✅ Backend — Phase 1 done and verified **live**
The full pipeline works end-to-end against the real Gemini API (tested with
`scripts/fake_client.py`, no hardware needed):

- voice in → Gemini transcribes → Stack-chan reply audio → SQLite persistence →
  one-shot `gemini-2.5-flash` summary on session close. All confirmed working.
- Live model: **`gemini-3.1-flash-live-preview`** (current as of June 2026), via
  `google-genai` 2.5.0. Input 16 kHz PCM, output 24 kHz PCM.
- Committed + pushed to the fork. Chunks 1–5a complete.

### 🔁 Firmware — pivoting from ESP-IDF to Arduino
- An **ESP-IDF** version of the device firmware (chunk 5) is written, committed,
  and pushed — but **never flashed/verified on hardware**. It lives in
  `firmware/voice_agent/` and stays as a reference.
- **Decision (2026-06-06):** build the device firmware in the **Arduino IDE with
  M5Unified** instead. Reasons: M5Unified handles the CoreS3 audio codec for us,
  and it plugs into the Stack-chan ecosystem we need next — `m5stack-avatar`
  (face) and `ServoEasing` (servos).
- **Key constraint:** on CoreS3, M5Unified is **half-duplex** — mic and speaker
  can't run at once. So the device is turn-based: it streams mic while LISTENING,
  then switches to the speaker when the backend sends `speaking_start`, and back
  on `speaking_end`. This reuses the existing backend protocol with **no backend
  changes**. Trade-off: no barge-in (can't interrupt mid-reply) — fine for Phase 1.

### ⏭️ Next steps
1. Install the **WebSockets** Arduino library (links2004 / Markus Sattler) — the
   only missing dependency (M5Unified, M5GFX, ArduinoJson already installed).
2. Build the Arduino sketch in chunks: **A** skeleton (Wi-Fi + screen) → **B**
   WebSocket + protocol handshake → **C** half-duplex audio → **D** face tint.
3. Flash + verify the full device loop on hardware.
4. Then: chunk 6 (face tint per state) → expression phase (servos + Gemini
   function calling, design proposal first).

### 🛠️ Environment notes (this machine)
- `uv` is **not on the PowerShell PATH** — run the backend via the venv directly:
  `voice_agent\backend\.venv\Scripts\python.exe -m stackchan_voice.main`.
- Laptop Wi-Fi LAN IP: **`192.168.1.158`** → device connects to
  `ws://192.168.1.158:8765/ws`. Run the backend with `BACKEND_HOST=0.0.0.0` so the
  device can reach it; device and laptop must share the same Wi-Fi.
- `voice_agent/backend/.env` holds the real `GEMINI_API_KEY` (gitignored).
