# Stack-chan voice agent — device firmware

ESP-IDF firmware for the official M5Stack StackChan ESP32-S3 hardware.

This is the device side of the Phase 1 voice loop. The backend (in
`../../backend/`) handles Gemini Live; this firmware streams mic audio
up, plays the response back, and drives the state UI.

## Chunk 5a scope (current)

- Boot, NVS init, WiFi STA connect (credentials via `idf.py menuconfig`)
- BOOT-button polling with 60 ms software debounce
- State-machine scaffold: `IDLE → STATE_SESSION_ACTIVE → IDLE`
- Stub session: logs `session_start_stub` and `session_end_stub` around
  a 2-second wait — no audio, no WebSocket yet.

Verifiable end state: flash, board boots, joins WiFi, prints
`button_pressed → session_start_stub → session_end_stub` over the serial
monitor when you press the BOOT button.

## Chunk 5b (next)

Audio codec init (AW88298 amp + ES7210 mic, both via the
`esp_codec_dev` managed component, modelled on
`firmware/main/hal/board/cores3_audio_codec.cc`), I2S driver, WebSocket
client (`esp_websocket_client`), bidirectional audio streaming, face
tinting on the display.

## Prerequisites

- ESP-IDF v5.3 or newer. The existing firmware in `../../../firmware`
  builds against the same toolchain, so if `idf.py build` works there,
  it'll work here.
- The board powered by USB-C with serial access.

## Build, flash, monitor

```sh
cd voice_agent/firmware/voice_agent
idf.py set-target esp32s3
idf.py menuconfig        # set: Stack-chan Voice Agent → WiFi SSID / Password / Backend URI / Device ID
idf.py build
idf.py -p <COM_PORT> flash monitor
```

`<COM_PORT>` on Windows is typically `COM3` or similar — check Device
Manager. On macOS/Linux it's `/dev/cu.usbserial-*` or `/dev/ttyUSB*`.

Exit the monitor with `Ctrl+]`.

## Configuration

All settings live in sdkconfig (gitignored by ESP-IDF), populated via
`idf.py menuconfig` under **Stack-chan Voice Agent**:

| Field | What it is |
|---|---|
| WiFi SSID | Your network name |
| WiFi Password | WPA2 password |
| Backend WebSocket URI | `ws://<laptop-ip>:8765/ws` — only used in 5b |
| Device ID | Sent in `client_hello` — only used in 5b |

The laptop's IP on the same LAN: `ipconfig` (Win) or `ifconfig` /
`ip addr` (mac/linux). Make sure the backend is bound to `0.0.0.0`, not
`127.0.0.1`, if you want the device to reach it — see
`BACKEND_HOST` in `../../backend/.env.example`.

## Known hardware quirk

**GPIO 0 is shared between the BOOT button and the I2S MCLK pin on
this board.** Chunk 5a only uses it as a button, so this isn't a
problem yet. Chunk 5b will need to either:

  1. Reconfigure GPIO 0 between input (button) and output (MCLK) modes
     around each session — button only readable when idle.
  2. Use the head-touch sensor (Si12T over I2C) as the session trigger
     instead, leaving GPIO 0 permanently as MCLK.

I'll make that call once chunk 5b audio is partially working and we
can measure how each option behaves.

## Where the code lives

```
main/
├── main.c             # app_main, state machine, button polling
├── wifi_sta.c/.h      # WiFi STA connect + wait
├── board_config.h     # pin map verified against the factory firmware
├── CMakeLists.txt
└── Kconfig.projbuild  # the `menuconfig` fields above
```

The pin map in `board_config.h` was lifted from
`../../../firmware/main/hal/board/config.h` so we know it matches the
actual hardware.
