idf_component_register(
    SRCS "csi_recv_router_http.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_wifi esp_event esp_netif esp_http_server lwip mdns
             esp_timer esp_system esp_hw_support
)
