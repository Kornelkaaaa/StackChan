/* Stack-chan voice agent — entry point and high-level state machine.
 *
 * Chunk 5a scope (this file): boot, NVS init, WiFi connect, button polling.
 * On button press we log a stub message; the actual WebSocket + audio
 * session lands in chunk 5b.
 *
 * The polling-task pattern is deliberate (vs GPIO interrupts): it's
 * easier to reason about, has no ISR/queue-from-ISR ceremony, and 20 ms
 * polling is more than fast enough for a human button press. The cost is
 * one always-on task using ~50 µs of CPU per tick — negligible.
 */
#include <stdbool.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "board_config.h"
#include "wifi_sta.h"

static const char *TAG = "main";

typedef enum {
    STATE_IDLE,             /* waiting for button press */
    STATE_SESSION_ACTIVE,   /* (chunk 5b will fill this in) */
} app_state_t;

static app_state_t s_state = STATE_IDLE;

static void init_button(void) {
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BOOT_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,    /* idle high, pressed low */
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&cfg));
}

static void button_task(void *arg) {
    /* Simple debounce: edge is recognised only when the level has been
     * stable for `STABLE_TICKS` consecutive polls (20 ms each → 60 ms total). */
    const int STABLE_TICKS = 3;
    int last_stable = 1;
    int candidate = 1;
    int candidate_count = 0;

    while (true) {
        int now = gpio_get_level(BOOT_BUTTON_GPIO);
        if (now == candidate) {
            if (candidate_count < STABLE_TICKS) candidate_count++;
        } else {
            candidate = now;
            candidate_count = 1;
        }

        if (candidate_count >= STABLE_TICKS && candidate != last_stable) {
            last_stable = candidate;
            if (last_stable == 0) {  /* falling edge = press */
                ESP_LOGI(TAG, "button_pressed state=%d", s_state);
                if (s_state == STATE_IDLE) {
                    /* Stub for the audio + WS session that lands in chunk 5b.
                     * Pretend a session is active for 2 s so the state
                     * transition is observable in the log. */
                    s_state = STATE_SESSION_ACTIVE;
                    ESP_LOGI(TAG, "session_start_stub (chunk_5b_will_wire_ws_and_audio)");
                    vTaskDelay(pdMS_TO_TICKS(2000));
                    ESP_LOGI(TAG, "session_end_stub");
                    s_state = STATE_IDLE;
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "boot device_id=%s", CONFIG_VOICE_AGENT_DEVICE_ID);

    /* NVS is required for WiFi credentials storage. */
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    init_button();

    ESP_LOGI(TAG, "wifi_connecting ssid=%s", CONFIG_VOICE_AGENT_WIFI_SSID);
    wifi_sta_start();
    wifi_sta_wait_connected();
    ESP_LOGI(TAG, "wifi_connected");

    xTaskCreate(button_task, "btn", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "ready — press BOOT button to fire the stub session");
}
