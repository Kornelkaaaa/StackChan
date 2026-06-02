/* WebSocket client wired to the Stack-chan wire protocol.
 *
 * Two callbacks the consumer registers in ws_client_init():
 *   * `evt_cb`   — fires on every JSON control message we know about
 *                  (session_opened, speaking_start/end, session_close)
 *                  plus the underlying socket connect/disconnect.
 *   * `audio_cb` — fires for every binary frame (Gemini's PCM audio).
 *
 * Both callbacks run in the WS task context — keep them short and
 * non-blocking. Push to a queue if you need to do work elsewhere.
 *
 * Lifetime: `ws_client_init` once; then `ws_client_connect` /
 * `ws_client_disconnect` as many times as needed.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WS_EVT_CONNECTED,
    WS_EVT_DISCONNECTED,
    WS_EVT_SESSION_OPENED,
    WS_EVT_SPEAKING_START,
    WS_EVT_SPEAKING_END,
    WS_EVT_SESSION_CLOSED,   /* server told us the session ended */
} ws_event_type_t;

typedef void (*ws_event_cb_t)(ws_event_type_t type);
typedef void (*ws_audio_cb_t)(const uint8_t *pcm, size_t bytes);

esp_err_t ws_client_init(const char *uri, ws_event_cb_t evt_cb, ws_audio_cb_t audio_cb);
esp_err_t ws_client_connect(void);
esp_err_t ws_client_disconnect(void);

esp_err_t ws_client_send_hello(const char *device_id, const char *fw_version, int sample_rate_hz);
esp_err_t ws_client_send_session_open(void);
esp_err_t ws_client_send_client_close(void);
esp_err_t ws_client_send_audio(const uint8_t *pcm, size_t bytes);

#ifdef __cplusplus
}
#endif
