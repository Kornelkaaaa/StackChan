idf_component_register(
    SRCS
        "main.c"
        "wifi_sta.c"
        "audio.c"
        "ws_client.c"
    INCLUDE_DIRS "."
    REQUIRES
        nvs_flash
        esp_wifi
        esp_event
        esp_netif
        driver
        esp_websocket_client
        esp_codec_dev
)
