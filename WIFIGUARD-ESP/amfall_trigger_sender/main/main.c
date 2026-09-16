#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "esp_err.h"
#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "soc/soc_caps.h"

#ifndef CONFIG_IDF_TARGET_ESP32C5
#error "This source supports only ESP32-C5"
#endif

#if ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(6, 0, 2)
#error "This source is pinned to ESP-IDF v6.0.2"
#endif

#if CONFIG_SOC_WIFI_MAC_VERSION_NUM != 3 || SOC_WIFI_MAC_VERSION_NUM != 3
#error "This source requires the ESP32-C5 MAC v3 contract"
#endif

#define STA_IP_READY_BIT (1U << 0)
#define TRIGGER_TASK_STACK_SIZE 4096U
#define STATUS_TASK_STACK_SIZE 3072U
#define TRIGGER_TASK_PRIORITY 6U
#define STATUS_TASK_PRIORITY 4U
#define SCHEDULER_NUMERATOR_US 1000000U

static const char *TAG = "wg_amfall_tx";

typedef struct __attribute__((packed)) {
    uint8_t magic[4];
    uint64_t sequence_le;
} trigger_message_t;

_Static_assert(sizeof(trigger_message_t) == 12U, "trigger message must be 12 bytes");

typedef struct {
    uint32_t floor_interval_us;
    uint32_t remainder_us;
    uint32_t phase;
    int64_t next_deadline_us;
} fractional_scheduler_t;

static EventGroupHandle_t s_events;
static esp_event_handler_instance_t s_wifi_event_instance;
static esp_event_handler_instance_t s_ip_event_instance;
static TaskHandle_t s_trigger_task_handle;
static esp_timer_handle_t s_trigger_timer;

static portMUX_TYPE s_station_lock = portMUX_INITIALIZER_UNLOCKED;
static esp_ip4_addr_t s_station_ip;
static uint8_t s_station_mac[6];
static bool s_station_ip_valid;

static portMUX_TYPE s_counter_lock = portMUX_INITIALIZER_UNLOCKED;
static uint64_t s_trigger_sequence;
static uint64_t s_trigger_sent_total;
static uint64_t s_trigger_send_error_total;
static uint64_t s_trigger_schedule_miss_total;

static portMUX_TYPE s_schedule_lock = portMUX_INITIALIZER_UNLOCKED;
static fractional_scheduler_t s_scheduler;
static bool s_scheduler_active;

static void require_condition(bool condition)
{
    if (!condition) {
        abort();
    }
}

static void require_ok(esp_err_t error)
{
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "fatal ESP-IDF error: %s", esp_err_to_name(error));
        abort();
    }
}

static void initialize_nvs(void)
{
    esp_err_t result = nvs_flash_init();
    if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        require_ok(nvs_flash_erase());
        result = nvs_flash_init();
    }
    require_ok(result);
}

static void clear_station(void)
{
    portENTER_CRITICAL(&s_station_lock);
    memset(&s_station_ip, 0, sizeof(s_station_ip));
    memset(s_station_mac, 0, sizeof(s_station_mac));
    s_station_ip_valid = false;
    portEXIT_CRITICAL(&s_station_lock);
    xEventGroupClearBits(s_events, STA_IP_READY_BIT);
}

static void set_station(const ip_event_assigned_ip_to_client_t *assigned)
{
    portENTER_CRITICAL(&s_station_lock);
    s_station_ip = assigned->ip;
    memcpy(s_station_mac, assigned->mac, sizeof(s_station_mac));
    s_station_ip_valid = true;
    portEXIT_CRITICAL(&s_station_lock);
    xEventGroupSetBits(s_events, STA_IP_READY_BIT);
}

static bool station_snapshot(esp_ip4_addr_t *ip, uint8_t mac[6])
{
    bool valid;
    portENTER_CRITICAL(&s_station_lock);
    valid = s_station_ip_valid;
    if (valid) {
        *ip = s_station_ip;
        memcpy(mac, s_station_mac, 6);
    }
    portEXIT_CRITICAL(&s_station_lock);
    return valid;
}

static uint32_t scheduler_next_interval(fractional_scheduler_t *scheduler)
{
    uint32_t interval = scheduler->floor_interval_us;
    scheduler->phase += scheduler->remainder_us;
    if (scheduler->phase >= (uint32_t)CONFIG_WIFI_GUARD_TX_RATE_HZ) {
        scheduler->phase -= (uint32_t)CONFIG_WIFI_GUARD_TX_RATE_HZ;
        ++interval;
    }
    return interval;
}

static void scheduler_start(void)
{
    portENTER_CRITICAL(&s_schedule_lock);
    s_scheduler.floor_interval_us =
        SCHEDULER_NUMERATOR_US / (uint32_t)CONFIG_WIFI_GUARD_TX_RATE_HZ;
    s_scheduler.remainder_us =
        SCHEDULER_NUMERATOR_US % (uint32_t)CONFIG_WIFI_GUARD_TX_RATE_HZ;
    s_scheduler.phase = 0;
    s_scheduler.next_deadline_us = esp_timer_get_time()
        + (int64_t)scheduler_next_interval(&s_scheduler);
    s_scheduler_active = true;
    const uint64_t delay_us = (uint64_t)(s_scheduler.next_deadline_us - esp_timer_get_time());
    portEXIT_CRITICAL(&s_schedule_lock);
    require_ok(esp_timer_start_once(s_trigger_timer, delay_us > 0U ? delay_us : 1U));
}

static void scheduler_stop(void)
{
    portENTER_CRITICAL(&s_schedule_lock);
    s_scheduler_active = false;
    portEXIT_CRITICAL(&s_schedule_lock);
    if (esp_timer_is_active(s_trigger_timer)) {
        require_ok(esp_timer_stop(s_trigger_timer));
    }
}

static void trigger_timer_callback(void *argument)
{
    (void)argument;
    uint32_t missed = 0;
    uint64_t delay_us = 1;

    portENTER_CRITICAL(&s_schedule_lock);
    if (s_scheduler_active) {
        if (s_trigger_task_handle != NULL) {
            xTaskNotifyGive(s_trigger_task_handle);
        }
        int64_t now = esp_timer_get_time();
        do {
            s_scheduler.next_deadline_us +=
                (int64_t)scheduler_next_interval(&s_scheduler);
            if (s_scheduler.next_deadline_us <= now) {
                ++missed;
            }
        } while (s_scheduler.next_deadline_us <= now);
        delay_us = (uint64_t)(s_scheduler.next_deadline_us - now);
    }
    const bool active = s_scheduler_active;
    portEXIT_CRITICAL(&s_schedule_lock);

    if (missed != 0U) {
        portENTER_CRITICAL(&s_counter_lock);
        s_trigger_schedule_miss_total += missed;
        portEXIT_CRITICAL(&s_counter_lock);
    }
    if (active) {
        const esp_err_t result = esp_timer_start_once(s_trigger_timer, delay_us);
        if (result != ESP_OK) {
            portENTER_CRITICAL(&s_counter_lock);
            ++s_trigger_schedule_miss_total;
            portEXIT_CRITICAL(&s_counter_lock);
        }
    }
}

static uint64_t next_trigger_sequence(void)
{
    uint64_t sequence;
    portENTER_CRITICAL(&s_counter_lock);
    sequence = s_trigger_sequence++;
    portEXIT_CRITICAL(&s_counter_lock);
    return sequence;
}

static void record_send(bool success, uint32_t notifications)
{
    portENTER_CRITICAL(&s_counter_lock);
    if (notifications > 1U) {
        s_trigger_schedule_miss_total += (uint64_t)(notifications - 1U);
    }
    if (success) {
        ++s_trigger_sent_total;
    } else {
        ++s_trigger_send_error_total;
    }
    portEXIT_CRITICAL(&s_counter_lock);
}

static void trigger_task(void *argument)
{
    (void)argument;
    s_trigger_task_handle = xTaskGetCurrentTaskHandle();
    const esp_timer_create_args_t timer_args = {
        .callback = trigger_timer_callback,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "wg_amfall_trigger",
        .skip_unhandled_events = false,
    };
    require_ok(esp_timer_create(&timer_args, &s_trigger_timer));

    for (;;) {
        xEventGroupWaitBits(s_events, STA_IP_READY_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        esp_ip4_addr_t station_ip = {0};
        uint8_t station_mac[6] = {0};
        if (!station_snapshot(&station_ip, station_mac)) {
            continue;
        }
        const int socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
        if (socket_fd < 0) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        const struct sockaddr_in destination = {
            .sin_family = AF_INET,
            .sin_port = htons((uint16_t)CONFIG_WIFI_GUARD_TX_TRIGGER_PORT),
            .sin_addr.s_addr = station_ip.addr,
        };
        (void)ulTaskNotifyTake(pdTRUE, 0);
        scheduler_start();
        ESP_LOGI(
            TAG,
            "%d Hz trigger stream target=" IPSTR ":%d floor=%" PRIu32 " remainder=%" PRIu32,
            CONFIG_WIFI_GUARD_TX_RATE_HZ,
            IP2STR(&station_ip),
            CONFIG_WIFI_GUARD_TX_TRIGGER_PORT,
            s_scheduler.floor_interval_us,
            s_scheduler.remainder_us
        );

        while ((xEventGroupGetBits(s_events) & STA_IP_READY_BIT) != 0U) {
            const uint32_t notifications = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
            if (notifications == 0U) {
                continue;
            }
            const trigger_message_t trigger = {
                .magic = {'W', 'G', 'T', 'R'},
                .sequence_le = next_trigger_sequence(),
            };
            const ssize_t sent = sendto(
                socket_fd,
                &trigger,
                sizeof(trigger),
                0,
                (const struct sockaddr *)&destination,
                sizeof(destination)
            );
            record_send(sent == (ssize_t)sizeof(trigger), notifications);
        }
        scheduler_stop();
        close(socket_fd);
        ESP_LOGW(TAG, "trigger stream stopped; waiting for receiver IP");
    }
}

static void status_task(void *argument)
{
    (void)argument;
    uint64_t previous_sent = 0;
    int64_t previous_us = esp_timer_get_time();
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        uint64_t sent;
        uint64_t errors;
        uint64_t misses;
        portENTER_CRITICAL(&s_counter_lock);
        sent = s_trigger_sent_total;
        errors = s_trigger_send_error_total;
        misses = s_trigger_schedule_miss_total;
        portEXIT_CRITICAL(&s_counter_lock);
        const int64_t now_us = esp_timer_get_time();
        const double observed_hz = now_us > previous_us
            ? (double)(sent - previous_sent) * 1000000.0 / (double)(now_us - previous_us)
            : 0.0;
        ESP_LOGI(
            TAG,
            "tx_status observed_hz=%.3f sent=%" PRIu64 " errors=%" PRIu64
            " schedule_misses=%" PRIu64,
            observed_hz,
            sent,
            errors,
            misses
        );
        previous_sent = sent;
        previous_us = now_us;
    }
}

static void network_event_handler(
    void *argument,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data
)
{
    (void)argument;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_STADISCONNECTED) {
        clear_station();
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_ASSIGNED_IP_TO_CLIENT) {
        const ip_event_assigned_ip_to_client_t *assigned = event_data;
        set_station(assigned);
        ESP_LOGI(
            TAG,
            "receiver IP assigned ip=" IPSTR " mac=" MACSTR,
            IP2STR(&assigned->ip),
            MAC2STR(assigned->mac)
        );
    }
}

static void initialize_wifi(void)
{
    const size_t ssid_length = strlen(CONFIG_WIFI_GUARD_TX_SSID);
    const size_t password_length = strlen(CONFIG_WIFI_GUARD_TX_PASSWORD);
    require_condition(ssid_length > 0U && ssid_length <= 32U);
    require_condition(password_length >= 8U && password_length <= 63U);

    require_ok(esp_netif_init());
    require_ok(esp_event_loop_create_default());
    require_condition(esp_netif_create_default_wifi_ap() != NULL);
    s_events = xEventGroupCreate();
    require_condition(s_events != NULL);
    require_ok(esp_event_handler_instance_register(
        WIFI_EVENT,
        ESP_EVENT_ANY_ID,
        network_event_handler,
        NULL,
        &s_wifi_event_instance
    ));
    require_ok(esp_event_handler_instance_register(
        IP_EVENT,
        IP_EVENT_ASSIGNED_IP_TO_CLIENT,
        network_event_handler,
        NULL,
        &s_ip_event_instance
    ));

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    require_ok(esp_wifi_init(&init_config));
    require_ok(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    require_ok(esp_wifi_set_mode(WIFI_MODE_AP));
    wifi_protocols_t protocols = {
        .ghz_2g = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_11AX,
        .ghz_5g = WIFI_PROTOCOL_11A | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_11AC | WIFI_PROTOCOL_11AX,
    };
    require_ok(esp_wifi_set_protocols(WIFI_IF_AP, &protocols));
    wifi_bandwidths_t bandwidths = {
        .ghz_2g = WIFI_BW20,
        .ghz_5g = WIFI_BW20,
    };
    require_ok(esp_wifi_set_bandwidths(WIFI_IF_AP, &bandwidths));

    wifi_config_t config = {0};
    memcpy(config.ap.ssid, CONFIG_WIFI_GUARD_TX_SSID, ssid_length);
    config.ap.ssid_len = (uint8_t)ssid_length;
    memcpy(config.ap.password, CONFIG_WIFI_GUARD_TX_PASSWORD, password_length);
    config.ap.channel = (uint8_t)CONFIG_WIFI_GUARD_TX_CHANNEL;
    config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    config.ap.max_connection = 1;
    config.ap.beacon_interval = 100;
    require_ok(esp_wifi_set_config(WIFI_IF_AP, &config));
    require_ok(esp_wifi_start());
    require_ok(esp_wifi_set_band_mode(WIFI_BAND_MODE_5G_ONLY));
    ESP_LOGI(
        TAG,
        "SoftAP ready channel=%d trigger_rate_hz=%d",
        CONFIG_WIFI_GUARD_TX_CHANNEL,
        CONFIG_WIFI_GUARD_TX_RATE_HZ
    );
}

void app_main(void)
{
    initialize_nvs();
    initialize_wifi();
    require_condition(xTaskCreate(
        trigger_task,
        "wg_amfall_trigger",
        TRIGGER_TASK_STACK_SIZE,
        NULL,
        TRIGGER_TASK_PRIORITY,
        NULL
    ) == pdPASS);
    require_condition(xTaskCreate(
        status_task,
        "wg_amfall_status",
        STATUS_TASK_STACK_SIZE,
        NULL,
        STATUS_TASK_PRIORITY,
        NULL
    ) == pdPASS);
}
