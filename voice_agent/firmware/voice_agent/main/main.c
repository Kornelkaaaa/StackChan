/* Stack-chan voice agent — app entry + session orchestration.
 *
 * State machine:
 *
 *   IDLE  --button press-->  CONNECTING_WS
 *                                  |
 *                                  v
 *   IDLE  <--server "session_close" / WS drop--  SESSION_ACTIVE  <-- SESSION_OPENED
 *
 * Three tasks during SESSION_ACTIVE:
 *   * mic_task        — codec.read_mic → ws.send_audio (~20 ms frames)
 *   * speaker_task    — drain speaker_q (filled by WS audio callback) → codec.write_speaker
 *   * (the main loop)  — handles state transitions, button polling when IDLE
 *
 * WS audio frames arrive in the WS event-handler context (can't block);
 * we malloc + memcpy into a PCM item and push to `speaker_q`. The
 * speaker_task consumes, plays, frees.
 *
 * GPIO 0 quirk reminder: the BOOT button shares its pin with I2S MCLK.
 * During SESSION_ACTIVE the I2S driver owns the pin, so button presses
 * are ignored — that's fine because the session ends on its own
 * (silence/timeout/server-close).
 */
#include <stdbool.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "audio.h"
#include "board_config.h"
#include "ws_client.h"
#include "wifi_sta.h"

static const char *TAG = "main";

/* === Frame sizing ============================================ */
/* 20 ms @ 24 kHz mono int16 = 480 samples = 960 bytes. */
#define FRAME_SAMPLES  (AUDIO_SAMPLE_RATE_HZ / 1000 * 20)
#define FRAME_BYTES    (FRAME_SAMPLES * 2)

#define SPEAKER_Q_DEPTH  16   /* ~320 ms of buffered audio at 20 ms/frame */

/* === State machine =========================================== */
typedef enum {
    APP_IDLE,
    APP_CONNECTING_WS,
    APP_SESSION_ACTIVE,
} app_state_t;

static EventGroupHandle_t s_events;
#define EVT_WS_CONNECTED     BIT0
#define EVT_SESSION_OPENED   BIT1
#define EVT_SESSION_CLOSED   BIT2
#define EVT_WS_DISCONNECTED  BIT3

static volatile app_state_t s_state = APP_IDLE;
static TaskHandle_t s_mic_task = NULL;
static TaskHandle_t s_speaker_task = NULL;

/* === Speaker queue: items are (malloc'd PCM, len) ============ */
typedef struct {
    uint8_t *data;
    size_t   len;
} pcm_item_t;

static QueueHandle_t s_speaker_q;


/* === WS callbacks (run in WS task — keep short) ============== */
static void on_ws_event(ws_event_type_t evt) {
    switch (evt) {
        case WS_EVT_CONNECTED:       xEventGroupSetBits(s_events, EVT_WS_CONNECTED); break;
        case WS_EVT_DISCONNECTED:    xEventGroupSetBits(s_events, EVT_WS_DISCONNECTED); break;
        case WS_EVT_SESSION_OPENED:  xEventGroupSetBits(s_events, EVT_SESSION_OPENED); break;
        case WS_EVT_SESSION_CLOSED:  xEventGroupSetBits(s_events, EVT_SESSION_CLOSED); break;
        case WS_EVT_SPEAKING_START:
        case WS_EVT_SPEAKING_END:
            /* Speaker state is purely driven by audio arriving in the
             * queue — these events are informational only for now. They'll
             * drive the face tint when the display lands in chunk 6. */
            break;
    }
}

static void on_ws_audio(const uint8_t *pcm, size_t bytes) {
    /* Allocate in PSRAM — the queue can buffer several frames during
     * scheduling jitter and we don't want to starve internal RAM. */
    uint8_t *copy = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy) {
        ESP_LOGE(TAG, "spk_malloc_failed bytes=%u — dropping frame", (unsigned)bytes);
        return;
    }
    memcpy(copy, pcm, bytes);
    pcm_item_t item = { .data = copy, .len = bytes };
    if (xQueueSend(s_speaker_q, &item, 0) != pdTRUE) {
        ESP_LOGW(TAG, "spk_q_full — dropping frame");
        free(copy);
    }
}


/* === Worker tasks ============================================ */
static void mic_task(void *arg) {
    (void)arg;
    int16_t *buf = heap_caps_malloc(FRAME_BYTES, MALLOC_CAP_INTERNAL);
    if (!buf) { ESP_LOGE(TAG, "mic_buf_alloc_failed"); vTaskDelete(NULL); }
    ESP_LOGI(TAG, "mic_task start frame=%d samples", FRAME_SAMPLES);

    while (s_state == APP_SESSION_ACTIVE) {
        if (audio_read_mic(buf, FRAME_SAMPLES) == FRAME_SAMPLES) {
            esp_err_t err = ws_client_send_audio((uint8_t *)buf, FRAME_BYTES);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "ws_send_audio_err %d", err);
                /* Don't bail — WS may reconnect or session may be closing. */
            }
        }
    }
    free(buf);
    ESP_LOGI(TAG, "mic_task end");
    vTaskDelete(NULL);
}

static void speaker_task(void *arg) {
    (void)arg;
    ESP_LOGI(TAG, "speaker_task start");
    while (s_state == APP_SESSION_ACTIVE) {
        pcm_item_t item;
        if (xQueueReceive(s_speaker_q, &item, pdMS_TO_TICKS(100)) == pdTRUE) {
            audio_write_speaker((int16_t *)item.data, item.len / 2);
            free(item.data);
        }
    }
    /* Drain any trailing audio so the user hears Stack-chan's last word. */
    pcm_item_t item;
    while (xQueueReceive(s_speaker_q, &item, 0) == pdTRUE) {
        audio_write_speaker((int16_t *)item.data, item.len / 2);
        free(item.data);
    }
    ESP_LOGI(TAG, "speaker_task end");
    vTaskDelete(NULL);
}


/* === Button polling ========================================== */
static bool button_pressed_edge(void) {
    /* 60 ms debounce — same as chunk 5a. Only called when IDLE, so the
     * GPIO 0 vs MCLK conflict doesn't apply. */
    static int last_stable = 1, candidate = 1, count = 0;
    int now = gpio_get_level(BOOT_BUTTON_GPIO);
    if (now == candidate) {
        if (count < 3) count++;
    } else {
        candidate = now; count = 1;
    }
    if (count >= 3 && candidate != last_stable) {
        last_stable = candidate;
        return last_stable == 0;
    }
    return false;
}

static void init_button(void) {
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BOOT_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&cfg));
}


/* === Session orchestration =================================== */
static void start_session(void) {
    ESP_LOGI(TAG, "session_start");
    s_state = APP_CONNECTING_WS;

    xEventGroupClearBits(s_events,
        EVT_WS_CONNECTED | EVT_SESSION_OPENED | EVT_SESSION_CLOSED | EVT_WS_DISCONNECTED);

    ESP_ERROR_CHECK(ws_client_connect());

    /* Wait up to 5s for the socket to come up. */
    EventBits_t bits = xEventGroupWaitBits(s_events,
        EVT_WS_CONNECTED | EVT_WS_DISCONNECTED, pdFALSE, pdFALSE, pdMS_TO_TICKS(5000));
    if (!(bits & EVT_WS_CONNECTED)) {
        ESP_LOGE(TAG, "ws_connect_timeout — aborting session");
        ws_client_disconnect();
        s_state = APP_IDLE;
        return;
    }

    ws_client_send_hello(CONFIG_VOICE_AGENT_DEVICE_ID, "0.2.0", AUDIO_SAMPLE_RATE_HZ);
    ws_client_send_session_open();

    bits = xEventGroupWaitBits(s_events,
        EVT_SESSION_OPENED | EVT_WS_DISCONNECTED, pdFALSE, pdFALSE, pdMS_TO_TICKS(5000));
    if (!(bits & EVT_SESSION_OPENED)) {
        ESP_LOGE(TAG, "session_open_timeout");
        ws_client_disconnect();
        s_state = APP_IDLE;
        return;
    }

    /* Open the codec channels and spin up the audio workers. */
    audio_enable_mic(true);
    audio_enable_speaker(true);
    s_state = APP_SESSION_ACTIVE;
    xTaskCreate(mic_task,     "mic", 4096, NULL, 6, &s_mic_task);
    xTaskCreate(speaker_task, "spk", 4096, NULL, 6, &s_speaker_task);
    ESP_LOGI(TAG, "session_active");

    /* Block until the server closes the session (silence, max-duration,
     * or error) — or until the socket drops. */
    xEventGroupWaitBits(s_events,
        EVT_SESSION_CLOSED | EVT_WS_DISCONNECTED, pdFALSE, pdFALSE, portMAX_DELAY);

    ESP_LOGI(TAG, "session_winding_down");
    s_state = APP_IDLE;   /* signals workers to exit their loops */

    /* Give workers a moment to notice and drain. */
    vTaskDelay(pdMS_TO_TICKS(200));

    audio_enable_mic(false);
    audio_enable_speaker(false);
    ws_client_disconnect();

    ESP_LOGI(TAG, "session_done");
}


/* === Entry point ============================================= */
void app_main(void) {
    ESP_LOGI(TAG, "boot device_id=%s sr=%d", CONFIG_VOICE_AGENT_DEVICE_ID, AUDIO_SAMPLE_RATE_HZ);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    s_events = xEventGroupCreate();
    s_speaker_q = xQueueCreate(SPEAKER_Q_DEPTH, sizeof(pcm_item_t));

    init_button();

    ESP_LOGI(TAG, "wifi_connecting ssid=%s", CONFIG_VOICE_AGENT_WIFI_SSID);
    wifi_sta_start();
    wifi_sta_wait_connected();
    ESP_LOGI(TAG, "wifi_connected");

    ESP_ERROR_CHECK(audio_init());
    ESP_ERROR_CHECK(ws_client_init(CONFIG_VOICE_AGENT_BACKEND_URI, on_ws_event, on_ws_audio));

    ESP_LOGI(TAG, "ready — press BOOT button to start a session");

    while (true) {
        if (s_state == APP_IDLE && button_pressed_edge()) {
            start_session();
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
