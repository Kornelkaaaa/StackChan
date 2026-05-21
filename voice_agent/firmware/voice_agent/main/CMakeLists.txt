idf_component_register(
    SRCS
        "main.c"
        "wifi_sta.c"
    INCLUDE_DIRS "."
    REQUIRES
        nvs_flash
        esp_wifi
        esp_event
        esp_netif
        driver
)
