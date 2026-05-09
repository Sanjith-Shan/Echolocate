/*
 * Echolocate CSI receiver firmware (ESP32-S3, ESP-IDF 5.x).
 *
 * What this firmware does:
 *  1. Boots, connects to a WiFi AP (your phone hotspot) as STA.
 *  2. Pings the gateway every 100 ms to generate steady WiFi traffic.
 *  3. Captures CSI on every received packet via esp_wifi_set_csi_rx_cb().
 *  4. Streams each CSI sample over USB serial as one CSV line, exactly in the
 *     format expected by the Python backend (matches esp-csi csi_recv_router).
 *  5. ALSO runs an HTTP server on port 80 with these endpoints, so the firmware
 *     is independently testable from any browser/curl on the same network:
 *
 *        GET /              human-readable HTML status page
 *        GET /health        JSON: uptime, RSSI, ssid, ip, packet count
 *        GET /stats         JSON: rolling amplitude mean/variance, occupancy hint
 *        GET /csi/latest    JSON: most recent parsed CSI sample
 *
 *  6. Advertises itself on mDNS as "echolocate.local" for easy discovery.
 *
 * Wire your phone hotspot's SSID/password via `idf.py menuconfig` →
 *   "Echolocate Configuration".
 *
 * Build & flash:
 *   idf.py set-target esp32s3
 *   idf.py menuconfig    # set WiFi creds under "Echolocate Configuration"
 *   idf.py build
 *   idf.py -p /dev/cu.usbmodem* flash monitor
 *
 * After flash, the boot banner prints the IP. Test it:
 *   curl http://<ip>/health
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"

#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "nvs_flash.h"

#include "esp_http_server.h"
#include "mdns.h"

#include "lwip/err.h"
#include "lwip/sys.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "lwip/inet.h"
#include "ping/ping_sock.h"

/* ---------- Configurable constants ---------- */

#ifndef CONFIG_ECHOLOCATE_WIFI_SSID
#define CONFIG_ECHOLOCATE_WIFI_SSID "echolocate-hotspot"
#endif

#ifndef CONFIG_ECHOLOCATE_WIFI_PASSWORD
#define CONFIG_ECHOLOCATE_WIFI_PASSWORD "changeme123"
#endif

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static const char *TAG = "echolocate";

/* ---------- Shared state for HTTP handlers ---------- */

#define MAX_SUBCARRIERS 256  /* HT40 max */

typedef struct {
    int64_t boot_time_us;
    char    ssid[33];
    char    ip_str[16];
    int8_t  last_rssi;
    int8_t  last_noise_floor;
    uint32_t packet_count;
    uint32_t serial_dropped_count;

    /* Latest parsed CSI sample */
    int      latest_n_subcarriers;
    float    latest_amplitudes[MAX_SUBCARRIERS];
    int64_t  latest_sample_us;

    /* Rolling stats — Welford online */
    uint32_t welford_n;
    double   welford_mean;
    double   welford_m2;
} csi_state_t;

static csi_state_t g_state = {0};
static SemaphoreHandle_t g_state_mutex = NULL;
static EventGroupHandle_t s_wifi_event_group = NULL;
static int s_retry_num = 0;

/* ---------- Welford online statistics ---------- */

static void welford_update(double x) {
    g_state.welford_n += 1;
    double delta = x - g_state.welford_mean;
    g_state.welford_mean += delta / (double)g_state.welford_n;
    double delta2 = x - g_state.welford_mean;
    g_state.welford_m2 += delta * delta2;
}

static double welford_variance(void) {
    return (g_state.welford_n > 1)
        ? g_state.welford_m2 / (double)g_state.welford_n
        : 0.0;
}

/* ---------- CSI callback: runs in WiFi task. Keep it short. ---------- */

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf || info->len <= 0) return;

    /* CSV header (printed once on boot to mark stream start):
     * type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel,
     *   local_timestamp,sig_len,rx_state,len,first_word,data
     */
    const wifi_pkt_rx_ctrl_t *rx = &info->rx_ctrl;
    int8_t *data = info->buf;
    int     len  = info->len;

    /* First-word-invalid is an ESP32-S3 hardware quirk: bytes 0..3 of CSI
     * are garbage when this flag is set. Skip them on the receiver side too,
     * but emit the raw stream — the Python parser already handles this. */
    int first_word_invalid = info->first_word_invalid ? 1 : 0;

    /* Print one CSV line. esp_log_write -> goes to UART0. */
    /* Keep this format byte-identical to the esp-csi csi_recv_router example
     * so the Python parser doesn't need to special-case our firmware. */
    printf("CSI_DATA,%" PRIu32 ",%02x:%02x:%02x:%02x:%02x:%02x,%d,%d,%d,%d,%d,%d,%" PRIu64 ",%d,%d,%d,%d,\"[",
           (uint32_t)rx->timestamp,
           info->mac[0], info->mac[1], info->mac[2],
           info->mac[3], info->mac[4], info->mac[5],
           rx->rssi,
           rx->rate,
           rx->noise_floor,
           0,            /* fft_gain — not exposed pre-IDF 5.2, set to 0 */
           0,            /* agc_gain — same */
           rx->channel,
           (uint64_t)esp_timer_get_time(),
           rx->sig_len,
           rx->rx_state,
           len,
           first_word_invalid);

    for (int i = 0; i < len; i++) {
        printf(i == len - 1 ? "%d" : "%d,", data[i]);
    }
    printf("]\"\n");

    /* Update shared state for the HTTP server.
     * Compute amplitudes inline from I/Q pairs (skip first 2 if quirk). */
    if (xSemaphoreTake(g_state_mutex, 0) == pdTRUE) {
        int start = first_word_invalid ? 4 : 0;  /* skip 4 bytes = 2 subcarriers */
        int n_pairs = (len - start) / 2;
        if (n_pairs > MAX_SUBCARRIERS) n_pairs = MAX_SUBCARRIERS;

        double sum_amp = 0.0;
        for (int i = 0; i < n_pairs; i++) {
            float I = (float)data[start + i * 2];
            float Q = (float)data[start + i * 2 + 1];
            float amp = sqrtf(I * I + Q * Q);
            g_state.latest_amplitudes[i] = amp;
            sum_amp += amp;
        }
        g_state.latest_n_subcarriers = n_pairs;
        g_state.latest_sample_us = esp_timer_get_time();

        if (n_pairs > 0) {
            double mean_amp = sum_amp / (double)n_pairs;
            welford_update(mean_amp);
        }

        g_state.last_rssi = rx->rssi;
        g_state.last_noise_floor = rx->noise_floor;
        g_state.packet_count += 1;

        xSemaphoreGive(g_state_mutex);
    } else {
        g_state.serial_dropped_count += 1;
    }
}

/* ---------- WiFi event handler ---------- */

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_num < 30) {
            esp_wifi_connect();
            s_retry_num++;
            ESP_LOGW(TAG, "WiFi disconnect — retry %d", s_retry_num);
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        snprintf(g_state.ip_str, sizeof(g_state.ip_str),
                 IPSTR, IP2STR(&event->ip_info.ip));
        ESP_LOGI(TAG, "===========================================");
        ESP_LOGI(TAG, " WiFi connected. IP: %s", g_state.ip_str);
        ESP_LOGI(TAG, " Test the firmware:");
        ESP_LOGI(TAG, "   curl http://%s/health", g_state.ip_str);
        ESP_LOGI(TAG, "   open http://%s/  (or echolocate.local)", g_state.ip_str);
        ESP_LOGI(TAG, "===========================================");
        s_retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* ---------- WiFi setup ---------- */

static void wifi_init_sta(void) {
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t any_id, got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, &any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, &got_ip));

    wifi_config_t wifi_config = { 0 };
    strncpy((char *)wifi_config.sta.ssid, CONFIG_ECHOLOCATE_WIFI_SSID,
            sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, CONFIG_ECHOLOCATE_WIFI_PASSWORD,
            sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.pmf_cfg.capable = true;

    strncpy(g_state.ssid, CONFIG_ECHOLOCATE_WIFI_SSID, sizeof(g_state.ssid) - 1);

    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "WiFi STA started — connecting to '%s'...",
             CONFIG_ECHOLOCATE_WIFI_SSID);
}

/* ---------- CSI configuration ---------- */

static void csi_init(void) {
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = true,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    ESP_LOGI(TAG, "CSI capture enabled");

    /* Print CSV header so anything tailing the serial knows the schema */
    printf("CSI_HEADER,type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,"
           "channel,local_timestamp,sig_len,rx_state,len,first_word,data\n");
}

/* ---------- ICMP ping: steady AP responses to drive CSI capture ----------
 *
 * Why ICMP (not UDP-to-discard-port): WiFi CSI is captured on RECEIVED
 * packets. We need a reliable source of incoming traffic. iPhone hotspots
 * typically drop UDP to closed ports without responding. ICMP echo is part
 * of the AP's mandatory stack — every ping gets a reply. Pinging the
 * gateway at 10 Hz produces ~10 CSI samples/sec, matching the simulator.
 */

static uint32_t g_ping_replies = 0;

static void on_ping_success(esp_ping_handle_t hdl, void *args) {
    g_ping_replies++;
}

static void on_ping_timeout(esp_ping_handle_t hdl, void *args) {
    /* Don't log every timeout — they're expected if AP is briefly busy. */
}

static void on_ping_end(esp_ping_handle_t hdl, void *args) {
    /* Restart immediately for continuous traffic. */
    esp_ping_start(hdl);
}

static esp_err_t start_gateway_ping(void) {
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (!netif) return ESP_FAIL;

    esp_netif_ip_info_t ip_info;
    if (esp_netif_get_ip_info(netif, &ip_info) != ESP_OK || ip_info.gw.addr == 0) {
        return ESP_FAIL;
    }

    ip_addr_t target = {0};
    target.type = IPADDR_TYPE_V4;
    target.u_addr.ip4.addr = ip_info.gw.addr;

    esp_ping_config_t cfg = ESP_PING_DEFAULT_CONFIG();
    cfg.target_addr   = target;
    cfg.count         = 0;       /* 0 = ping forever */
    cfg.interval_ms   = 100;     /* 10 Hz — matches detector sample_rate */
    cfg.timeout_ms    = 800;
    cfg.data_size     = 32;
    cfg.tos           = 0;
    cfg.task_stack_size = 4096;
    cfg.task_prio     = 4;

    esp_ping_callbacks_t cbs = {
        .on_ping_success = on_ping_success,
        .on_ping_timeout = on_ping_timeout,
        .on_ping_end     = on_ping_end,
        .cb_args         = NULL,
    };

    esp_ping_handle_t hdl = NULL;
    esp_err_t err = esp_ping_new_session(&cfg, &cbs, &hdl);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ping_new_session failed: %d", err);
        return err;
    }

    err = esp_ping_start(hdl);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ping_start failed: %d", err);
        return err;
    }

    char gw_str[16];
    esp_ip4addr_ntoa(&ip_info.gw, gw_str, sizeof(gw_str));
    ESP_LOGI(TAG, "ICMP ping → %s @ 10 Hz (drives CSI capture)", gw_str);
    return ESP_OK;
}

/* ---------- HTTP handlers ---------- */

static esp_err_t root_get_handler(httpd_req_t *req) {
    char buf[1024];
    int n_subcarriers, packets;
    int rssi;
    char ip[16], ssid[33];
    double mean, var;
    int64_t uptime_s;

    xSemaphoreTake(g_state_mutex, portMAX_DELAY);
    n_subcarriers = g_state.latest_n_subcarriers;
    packets = g_state.packet_count;
    rssi = g_state.last_rssi;
    mean = g_state.welford_mean;
    var = welford_variance();
    strcpy(ip, g_state.ip_str);
    strcpy(ssid, g_state.ssid);
    uptime_s = (esp_timer_get_time() - g_state.boot_time_us) / 1000000;
    xSemaphoreGive(g_state_mutex);

    snprintf(buf, sizeof(buf),
        "<!DOCTYPE html><html><head><meta charset=utf-8>"
        "<title>Echolocate ESP32</title>"
        "<meta http-equiv=refresh content=2>"
        "<style>body{font-family:system-ui;margin:2em;background:#0f172a;color:#e2e8f0}"
        "h1{color:#0ea5e9}code{background:#1e293b;padding:.1em .3em;border-radius:3px}"
        "table{border-collapse:collapse}td{padding:.3em 1em;border-bottom:1px solid #334155}</style>"
        "</head><body>"
        "<h1>Echolocate CSI Sensor</h1>"
        "<p>Firmware is alive. Page auto-refreshes every 2s.</p>"
        "<table>"
        "<tr><td>WiFi SSID</td><td><code>%s</code></td></tr>"
        "<tr><td>IP address</td><td><code>%s</code></td></tr>"
        "<tr><td>RSSI</td><td>%d dBm</td></tr>"
        "<tr><td>Uptime</td><td>%" PRId64 " s</td></tr>"
        "<tr><td>CSI packets received</td><td>%d</td></tr>"
        "<tr><td>Subcarriers / sample</td><td>%d</td></tr>"
        "<tr><td>Rolling mean amplitude</td><td>%.2f</td></tr>"
        "<tr><td>Rolling variance</td><td>%.2f</td></tr>"
        "</table>"
        "<h3>Test endpoints</h3>"
        "<ul>"
        "<li><a style=color:#0ea5e9 href=/health>/health</a> — JSON status</li>"
        "<li><a style=color:#0ea5e9 href=/stats>/stats</a> — JSON rolling stats</li>"
        "<li><a style=color:#0ea5e9 href=/csi/latest>/csi/latest</a> — JSON latest CSI sample</li>"
        "</ul>"
        "</body></html>",
        ssid, ip, rssi, uptime_s, packets, n_subcarriers, mean, var);

    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, buf, strlen(buf));
}

static esp_err_t health_get_handler(httpd_req_t *req) {
    char buf[512];
    int packets, rssi;
    char ip[16], ssid[33];
    int64_t uptime_s;
    int free_heap = (int)esp_get_free_heap_size();

    xSemaphoreTake(g_state_mutex, portMAX_DELAY);
    packets = g_state.packet_count;
    rssi = g_state.last_rssi;
    strcpy(ip, g_state.ip_str);
    strcpy(ssid, g_state.ssid);
    uptime_s = (esp_timer_get_time() - g_state.boot_time_us) / 1000000;
    xSemaphoreGive(g_state_mutex);

    snprintf(buf, sizeof(buf),
        "{"
        "\"ok\":true,"
        "\"firmware\":\"echolocate-csi-1.1\","
        "\"chip\":\"esp32s3\","
        "\"ssid\":\"%s\","
        "\"ip\":\"%s\","
        "\"rssi\":%d,"
        "\"uptime_s\":%" PRId64 ","
        "\"free_heap\":%d,"
        "\"packets_received\":%d,"
        "\"ping_replies\":%" PRIu32
        "}",
        ssid, ip, rssi, uptime_s, free_heap, packets, g_ping_replies);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, buf, strlen(buf));
}

static esp_err_t stats_get_handler(httpd_req_t *req) {
    char buf[512];
    int n;
    double mean, var, std;
    uint32_t welford_n;

    xSemaphoreTake(g_state_mutex, portMAX_DELAY);
    n = g_state.latest_n_subcarriers;
    mean = g_state.welford_mean;
    var = welford_variance();
    welford_n = g_state.welford_n;
    xSemaphoreGive(g_state_mutex);

    std = sqrt(var);

    /* Quick occupancy hint based on variance — only meaningful after a few
     * hundred samples. The real classification happens in the backend. */
    const char *hint = "calibrating";
    if (welford_n > 200) {
        if (var < 5.0)        hint = "empty";
        else if (var < 20.0)  hint = "low";
        else if (var < 80.0)  hint = "moderate";
        else                  hint = "high";
    }

    snprintf(buf, sizeof(buf),
        "{"
        "\"samples\":%" PRIu32 ","
        "\"subcarriers_per_sample\":%d,"
        "\"rolling_mean_amplitude\":%.4f,"
        "\"rolling_variance\":%.4f,"
        "\"rolling_std\":%.4f,"
        "\"occupancy_hint\":\"%s\""
        "}",
        welford_n, n, mean, var, std, hint);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, buf, strlen(buf));
}

static esp_err_t csi_latest_get_handler(httpd_req_t *req) {
    /* Stream the latest amplitudes array as JSON. With up to 256 subcarriers
     * at ~10 chars each, allocate generously. */
    static char buf[4096];
    int n;
    int rssi;
    int64_t sample_us;
    float amps[MAX_SUBCARRIERS];

    xSemaphoreTake(g_state_mutex, portMAX_DELAY);
    n = g_state.latest_n_subcarriers;
    rssi = g_state.last_rssi;
    sample_us = g_state.latest_sample_us;
    memcpy(amps, g_state.latest_amplitudes, sizeof(float) * n);
    xSemaphoreGive(g_state_mutex);

    int written = snprintf(buf, sizeof(buf),
        "{\"sample_us\":%" PRId64 ",\"rssi\":%d,\"n\":%d,\"amplitudes\":[",
        sample_us, rssi, n);

    for (int i = 0; i < n && written < (int)sizeof(buf) - 16; i++) {
        written += snprintf(buf + written, sizeof(buf) - written,
                            i == n - 1 ? "%.2f" : "%.2f,", amps[i]);
    }
    if (written < (int)sizeof(buf) - 2) {
        written += snprintf(buf + written, sizeof(buf) - written, "]}");
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, buf, written);
}

static httpd_handle_t start_webserver(void) {
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.lru_purge_enable = true;
    cfg.max_uri_handlers = 8;
    cfg.stack_size = 8192;

    httpd_handle_t server = NULL;
    if (httpd_start(&server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start HTTP server");
        return NULL;
    }

    httpd_uri_t root_uri    = { .uri="/",            .method=HTTP_GET, .handler=root_get_handler };
    httpd_uri_t health_uri  = { .uri="/health",      .method=HTTP_GET, .handler=health_get_handler };
    httpd_uri_t stats_uri   = { .uri="/stats",       .method=HTTP_GET, .handler=stats_get_handler };
    httpd_uri_t csi_uri     = { .uri="/csi/latest",  .method=HTTP_GET, .handler=csi_latest_get_handler };

    httpd_register_uri_handler(server, &root_uri);
    httpd_register_uri_handler(server, &health_uri);
    httpd_register_uri_handler(server, &stats_uri);
    httpd_register_uri_handler(server, &csi_uri);

    ESP_LOGI(TAG, "HTTP server listening on :80");
    return server;
}

/* ---------- mDNS so hostname is discoverable ---------- */

static void start_mdns(void) {
    if (mdns_init() != ESP_OK) {
        ESP_LOGW(TAG, "mDNS init failed");
        return;
    }
    mdns_hostname_set("echolocate");
    mdns_instance_name_set("Echolocate CSI Sensor");
    mdns_service_add(NULL, "_http", "_tcp", 80, NULL, 0);
    ESP_LOGI(TAG, "mDNS: echolocate.local");
}

/* ---------- app_main ---------- */

void app_main(void) {
    /* NVS */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    g_state_mutex = xSemaphoreCreateMutex();
    g_state.boot_time_us = esp_timer_get_time();

    ESP_LOGI(TAG, "Echolocate CSI firmware starting");

    wifi_init_sta();

    /* Wait for WiFi up to 30 s before continuing. If it fails we still bring
     * up the HTTP server on AP fallback (out of scope for hackathon — log
     * and reboot). */
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(30000));
    if (!(bits & WIFI_CONNECTED_BIT)) {
        ESP_LOGE(TAG, "WiFi failed to connect within 30s — restarting");
        esp_restart();
    }

    csi_init();
    start_webserver();
    start_mdns();

    /* Generate steady traffic so CSI keeps flowing. ICMP ping to the AP at
     * 10 Hz — much more reliable than UDP-to-discard for iPhone hotspots. */
    if (start_gateway_ping() != ESP_OK) {
        ESP_LOGW(TAG, "ping setup failed — CSI will only capture on beacons (slower)");
    }

    /* Periodic boot-banner reminder so the user can find the IP from monitor */
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(30000));
        ESP_LOGI(TAG, "alive  ip=%s  packets=%" PRIu32 "  rssi=%d  ping_replies=%" PRIu32,
                 g_state.ip_str, g_state.packet_count, g_state.last_rssi, g_ping_replies);
    }
}
