# Stack-chan voice agent — device firmware

ESP-IDF firmware for the official M5Stack StackChan ESP32-S3 hardware.

This is the device side of the Phase 1 voice loop. The backend (in
`../../backend/`) handles Gemini Live; this firmware streams mic audio
up, plays the response back, and (in a follow-up chunk) drives the face
tint per state.

## Scope after chunk 5b

- Boot, NVS, WiFi STA connect
- BOOT-button polling (60 ms debounce) — triggers a session when IDLE
- AW88298 + ES7210 codec init via `esp_codec_dev`, I2S TDM (mic) + STD (speaker)
  at 24 kHz mono 16-bit
- `esp_websocket_client` wired to the wire protocol — JSON control frames
  built with `snprintf`, incoming JSON routed via substring match on `type`
- Per-session orchestration: `mic_task` streams audio up, `speaker_task`
  drains a PSRAM-allocated queue of incoming PCM frames, main loop waits
  on the server's `session_close` to wind down

What's NOT here yet (intentional, on the chunk-6 backlog):
- Face tint on the display — `speaking_start` / `speaking_end` events
  arrive and are observable in logs, but don't drive UI yet
- Animated face, servo movements, head-touch trigger

## Prerequisites

- ESP-IDF v5.3+ (the factory M5Stack firmware in `../../../firmware`
  builds against the same toolchain)
- Board powered by USB-C with serial access
- The backend running and reachable on your LAN

## Build, flash, monitor

```sh
cd voice_agent/firmware/voice_agent
idf.py set-target esp32s3
idf.py menuconfig          # set WiFi SSID/password + Backend URI + Device ID
idf.py build
idf.py -p <COM_PORT> flash monitor
```

First build triggers the managed-component fetch for
`esp_websocket_client` and `esp_codec_dev` — takes a minute the first
time, cached thereafter.

`<COM_PORT>` on Windows is usually `COM3`-ish (Device Manager). On
macOS/Linux: `/dev/cu.usbserial-*` or `/dev/ttyUSB*`. Exit monitor with
`Ctrl+]`.

## Configuration (`idf.py menuconfig` → **Stack-chan Voice Agent**)

| Field | Notes |
|---|---|
| WiFi SSID / Password | WPA2. Goes into sdkconfig, which is gitignored. |
| Backend WebSocket URI | `ws://<laptop-ip>:8765/ws`. Set the backend's `BACKEND_HOST=0.0.0.0` in `.env` so it's reachable on the LAN. |
| Device ID | Sent in `client_hello`. |

Find your laptop's LAN IP with `ipconfig` (Win) or `ifconfig` / `ip addr`
(mac/linux). The device and laptop must share the same WiFi network.

## Expected serial output

```
I (xxx) main: boot device_id=stackchan-01 sr=24000
I (xxx) main: wifi_connecting ssid=<your-ssid>
I (xxx) wifi_sta: got_ip 192.168.x.x
I (xxx) main: wifi_connected
I (xxx) audio: init i2c
I (xxx) audio: init i2s (mclk=0 bclk=34 ws=33 din=14 dout=13)
I (xxx) audio: init codecs
I (xxx) audio: audio_init done sr=24000
I (xxx) main: ready — press BOOT button to start a session
```

Press BOOT, then talk:

```
I (xxx) main: session_start
I (xxx) ws_client: ws_connected
I (xxx) ws_client: rx_evt "type":"session_opened"
I (xxx) main: session_active
I (xxx) main: mic_task start frame=480 samples
I (xxx) main: speaker_task start
I (xxx) ws_client: rx_evt "type":"speaking_start"
I (xxx) ws_client: rx_evt "type":"speaking_end"
... (after 30 s of silence) ...
I (xxx) ws_client: rx_evt "type":"session_close"
I (xxx) main: session_winding_down
I (xxx) main: session_done
```

The speaker should play back whatever Gemini says. You can also check
the backend log (`session_opened`, `summarizer_complete`, etc.) and
inspect the persisted conversation with `stackchan-memory show <id>`.

## Architecture in one diagram

```
                 +-------------+
   button --->   |  main loop  |  --(connect / wait / close)-->  ws_client
                 +-------------+
                       |
                       v
              +------------------+      +----------------+
              |    mic_task      |--->  | ws_client.send |---> backend (binary PCM)
              | codec.read_mic   |      +----------------+
              +------------------+
                                                  ^
                                                  |
                                           (binary frames)
                                                  |
              +------------------+      +-----------------+
              |  speaker_task    |<---  |  speaker_q      |<---  ws audio callback
              | codec.write_spk  |      |  (PCM, in PSRAM)|
              +------------------+      +-----------------+
```

## Known issues / Phase-2 work

- **GPIO 0 shared with I2S MCLK.** Button presses are ignored during a
  session (pin is owned by the I2S driver). The session ends on its own
  (silence / max-duration / backend error), so this is rarely felt in
  practice — but you can't "press the button to stop". Phase 2 options:
  reconfigure the pin around each session, or switch the trigger to the
  Si12T head-touch sensor.
- **No face/eye tint yet.** `speaking_start` / `speaking_end` fire and
  are logged; chunk 6 will hook them up to the display.
- **JSON parsing via `strstr`.** Fast, no extra deps, but assumes the
  backend never re-orders fields. Swap to cJSON if that ever changes.

## Where the code lives

```
main/
├── main.c              # app_main, state machine, mic/speaker tasks, button poll
├── wifi_sta.{h,c}      # WiFi STA + wait-for-IP
├── audio.{h,c}         # I2C, I2S (TDM + STD), AW88298 + ES7210 init, read/write
├── ws_client.{h,c}     # esp_websocket_client wrapper + protocol JSON
├── board_config.h      # pin map (vendored from the factory firmware)
├── idf_component.yml   # managed deps: esp_websocket_client, esp_codec_dev
├── CMakeLists.txt
└── Kconfig.projbuild   # menuconfig fields
```

`board_config.h` was lifted from
`../../../firmware/main/hal/board/config.h` and the audio init logic is
modelled on `../../../firmware/main/hal/board/cores3_audio_codec.cc` —
so the hardware-touching bits match known-working code.
