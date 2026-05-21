/* Minimal WiFi STA wrapper for ESP-IDF.
 *
 * Two calls, called in order from `app_main`:
 *   wifi_sta_start()           — kick off the connect (non-blocking)
 *   wifi_sta_wait_connected()  — block until we have an IP
 *
 * Credentials come from sdkconfig (set via `idf.py menuconfig`).
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

void wifi_sta_start(void);
void wifi_sta_wait_connected(void);

#ifdef __cplusplus
}
#endif
