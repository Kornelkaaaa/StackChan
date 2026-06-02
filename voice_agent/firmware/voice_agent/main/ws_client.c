/* esp_websocket_client wrapper that speaks the Stack-chan wire protocol.
 *
 * Outgoing control frames are hand-built JSON strings (snprintf) — three
 * message shapes total, none with variable structure. No cJSON needed for
 * sending. Incoming control frames are parsed with a substring match on
 * the `type` field — robust enough for the four message types we care
 * about and avoids pulling cJSON into the link.
 *
 * Outgoing binary frames are raw PCM bytes passed through `ws_client_send_audio`.
 * Incoming binary frames trigger `audio_cb` directly.
 */
#include "ws_client.h"

#include <string.h>

#include "esp_log.h"
#include "esp_websocket_client.h"

static const char *TAG = "ws_client";

static esp_websocket_client_handle_t s_client = NULL;
static ws_event_cb_t s_evt_cb = NULL;
static ws_audio_cb_t s_audio_cb = NULL;

static void route_text(const char *text, int len) {
    /* Tiny lookup table. Order matters only for log clarity. */
    static const struct {
        const char *needle;
        ws_event_type_t evt;
    } kinds[] = {
        { "\"type\":\"session_opened\"",  WS_EVT_SESSION_OPENED  },
        { "\"type\":\"speaking_start\"",  WS_EVT_SPEAKING_START  },
        { "\"type\":\"speaking_end\"",    WS_EVT_SPEAKING_END    },
        { "\"type\":\"session_close\"",   WS_EVT_SESSION_CLOSED  },
    };
    for (size_t i = 0; i < sizeof(kinds) / sizeof(kinds[0]); ++i) {
        if (strstr(text, kinds[i].needle)) {
            ESP_LOGI(TAG, "rx_evt %s", kinds[i].needle);
            if (s_evt_cb) s_evt_cb(kinds[i].evt);
            return;
        }
    }
    ESP_LOGW(TAG, "unknown_text len=%d head='%.40s'", len, text);
}

static void on_ws_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)base;
    esp_websocket_event_data_t *ev = (esp_websocket_event_data_t *)data;

    switch ((int)id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "ws_connected");
            if (s_evt_cb) s_evt_cb(WS_EVT_CONNECTED);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGI(TAG, "ws_disconnected");
            if (s_evt_cb) s_evt_cb(WS_EVT_DISCONNECTED);
            break;
        case WEBSOCKET_EVENT_DATA:
            /* op_code 0x1 = TEXT, 0x2 = BINARY (RFC 6455). */
            if (ev->op_code == 0x1 && ev->data_len > 0) {
                /* Frames are not null-terminated; route_text handles len explicitly. */
                route_text((const char *)ev->data_ptr, ev->data_len);
            } else if (ev->op_code == 0x2 && ev->data_len > 0) {
                if (s_audio_cb) s_audio_cb((const uint8_t *)ev->data_ptr, ev->data_len);
            }
            break;
        default:
            break;
    }
}

esp_err_t ws_client_init(const char *uri, ws_event_cb_t evt_cb, ws_audio_cb_t audio_cb) {
    s_evt_cb = evt_cb;
    s_audio_cb = audio_cb;

    /* Ping is server-initiated in our setup (uvicorn sends every 20s),
     * but enabling client pings as a backup costs nothing. */
    esp_websocket_client_config_t cfg = {
        .uri                  = uri,
        .reconnect_timeout_ms = 3000,
        .network_timeout_ms   = 10000,
        .ping_interval_sec    = 30,
        .disable_auto_reconnect = true,  /* we explicitly connect/disconnect per session */
    };
    s_client = esp_websocket_client_init(&cfg);
    if (!s_client) return ESP_FAIL;

    return esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, on_ws_event, NULL);
}

esp_err_t ws_client_connect(void) {
    if (!s_client) return ESP_ERR_INVALID_STATE;
    return esp_websocket_client_start(s_client);
}

esp_err_t ws_client_disconnect(void) {
    if (!s_client) return ESP_ERR_INVALID_STATE;
    /* `stop` is graceful; `destroy` would tear down handlers. */
    return esp_websocket_client_stop(s_client);
}

static esp_err_t send_text(const char *text) {
    if (!s_client) return ESP_ERR_INVALID_STATE;
    int n = esp_websocket_client_send_text(s_client, text, strlen(text), portMAX_DELAY);
    return n >= 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t ws_client_send_hello(const char *device_id, const char *fw_version, int sample_rate_hz) {
    char buf[192];
    /* Fields are constrained and not user-input; snprintf is safe here. */
    snprintf(buf, sizeof(buf),
        "{\"type\":\"client_hello\","
         "\"device_id\":\"%s\","
         "\"fw_version\":\"%s\","
         "\"input_sample_rate_hz\":%d}",
        device_id, fw_version, sample_rate_hz);
    return send_text(buf);
}

esp_err_t ws_client_send_session_open(void) {
    return send_text("{\"type\":\"session_open\"}");
}

esp_err_t ws_client_send_client_close(void) {
    return send_text("{\"type\":\"client_close\"}");
}

esp_err_t ws_client_send_audio(const uint8_t *pcm, size_t bytes) {
    if (!s_client) return ESP_ERR_INVALID_STATE;
    int n = esp_websocket_client_send_bin(s_client, (const char *)pcm, bytes, portMAX_DELAY);
    return n >= 0 ? ESP_OK : ESP_FAIL;
}
