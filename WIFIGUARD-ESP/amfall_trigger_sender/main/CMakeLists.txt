idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_event
        esp_hw_support
        esp_netif
        esp_timer
        esp_wifi
        lwip
        nvs_flash
)
