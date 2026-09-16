#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "driver/gpio.h"
#include "driver/spi_common.h"
#include "driver/spi_slave.h"
#include "esp_csi_gain_ctrl.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_rom_crc.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "soc/soc_caps.h"

#include "wg_spi_wire.h"
#include "wg_spi_trace.h"

#ifndef CONFIG_IDF_TARGET_ESP32C5
#error "This source supports only ESP32-C5"
#endif

#if ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(6, 0, 2)
#error "This source is pinned to ESP-IDF v6.0.2"
#endif

#if CONFIG_SOC_WIFI_MAC_VERSION_NUM != 3 || SOC_WIFI_MAC_VERSION_NUM != 3
#error "This source requires the ESP32-C5 MAC v3 CSI contract"
#endif

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "WGSP v1 wire structs require a little-endian target"
#endif

#ifndef CONFIG_WIFI_GUARD_BOARD_PINS_APPROVED
#error "Review the exact board pinout and enable WIFI_GUARD_BOARD_PINS_APPROVED"
#endif

_Static_assert(
    (unsigned)RX_BB_FORMAT_HE_SU == WG_SPI_RX_FORMAT_HE_SU,
    "Host WGSP decoder requires RX_BB_FORMAT_HE_SU == 4"
);
_Static_assert(WG_SPI_CSI_PAYLOAD_BYTES % 2U == 0U, "CSI payload must contain IQ pairs");
_Static_assert(WG_SPI_FRAME_BYTES % 4U == 0U, "SPI DMA frame must be word aligned");
_Static_assert(WG_SPI_HEADER_BYTES % 4U == 0U, "SPI DMA header must be word aligned");
_Static_assert(CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES >= 1, "SPI batch must not be empty");
_Static_assert(CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES <= 8, "SPI batch exceeds contract");
_Static_assert(
    CONFIG_WIFI_GUARD_MAX_QUEUE >= CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES,
    "CSI frame pool must hold one complete SPI batch"
);

#define WIFI_CONNECTED_BIT (1U << 0)
#define SPI_HOST SPI2_HOST
#define SPI_QUEUE_SIZE 1
#define SPI_BATCH_FRAMES ((size_t)CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES)
#define SPI_TRANSACTION_BYTES (SPI_BATCH_FRAMES * WG_SPI_FRAME_BYTES)
#define SPI_TASK_STACK_SIZE 4096U
#define UDP_TASK_STACK_SIZE 3072U
#define DIAGNOSTIC_TASK_STACK_SIZE 3072U
#define SPI_TASK_PRIORITY 7U
#define UDP_TASK_PRIORITY 5U
#define DIAGNOSTIC_TASK_PRIORITY 2U
#define DIAGNOSTIC_INTERVAL_MS 5000U

static const char *TAG = "wg_amfall_spi";
static const uint8_t PROFILE_FINGERPRINT[8] = WG_SPI_PROFILE_FINGERPRINT_BYTES;

typedef struct {
    uint64_t stream_epoch;
    uint64_t seq;
    uint64_t source_ts_us;
    int8_t rssi;
    int8_t noise_floor;
    uint8_t agc;
    int8_t fft;
    uint8_t channel;
    int8_t csi[WG_SPI_CSI_PAYLOAD_BYTES];
} capture_frame_t;

static capture_frame_t s_frame_pool[CONFIG_WIFI_GUARD_MAX_QUEUE];
static capture_frame_t *s_free_queue_storage[CONFIG_WIFI_GUARD_MAX_QUEUE];
static capture_frame_t *s_ready_queue_storage[CONFIG_WIFI_GUARD_MAX_QUEUE];
static StaticQueue_t s_free_queue_control;
static StaticQueue_t s_ready_queue_control;
static QueueHandle_t s_free_queue;
static QueueHandle_t s_ready_queue;

static EventGroupHandle_t s_wifi_events;
static esp_event_handler_instance_t s_wifi_event_instance;
static esp_event_handler_instance_t s_ip_event_instance;

static portMUX_TYPE s_counter_lock = portMUX_INITIALIZER_UNLOCKED;
static uint64_t s_callback_seq;
static uint32_t s_source_dropped_total;
static uint32_t s_queue_dropped_total;
static uint32_t s_spi_dropped_total;
static uint32_t s_transport_errors_total;
static uint32_t s_csi_accepted_total;
static uint32_t s_csi_null_total;
static uint32_t s_csi_timestamp_rejected_total;
static uint32_t s_csi_estimate_rejected_total;
static uint32_t s_csi_format_rejected_total;
static uint32_t s_csi_channel_rejected_total;
static uint32_t s_csi_length_rejected_total;
static uint32_t s_csi_first_word_rejected_total;
static uint32_t s_csi_bssid_rejected_total;
static uint16_t s_csi_last_length;
static uint8_t s_csi_last_format;
static uint8_t s_csi_last_channel;
static uint8_t s_csi_last_estimate_valid;
static bool s_csi_last_first_word_invalid;
static int8_t s_csi_last_rssi;
static uint8_t s_csi_last_source_mac[6];

static portMUX_TYPE s_capture_lock = portMUX_INITIALIZER_UNLOCKED;
static bool s_capture_session_active;
static uint64_t s_capture_session_nonce;
static bool s_timestamp_initialized;
static uint32_t s_timestamp_last_raw;
static uint64_t s_timestamp_last_extended;

static portMUX_TYPE s_bssid_lock = portMUX_INITIALIZER_UNLOCKED;
static uint8_t s_ap_bssid[6];
static bool s_ap_bssid_valid;

static uint8_t *s_spi_frame;

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

static void saturating_increment(uint32_t *counter)
{
    portENTER_CRITICAL(&s_counter_lock);
    if (*counter != UINT32_MAX) {
        ++(*counter);
    }
    portEXIT_CRITICAL(&s_counter_lock);
}

static void saturating_increment_locked(uint32_t *counter)
{
    if (*counter != UINT32_MAX) {
        ++(*counter);
    }
}

static uint64_t next_callback_seq(void)
{
    uint64_t seq;

    portENTER_CRITICAL(&s_counter_lock);
    seq = s_callback_seq++;
    portEXIT_CRITICAL(&s_counter_lock);
    return seq;
}

static void counter_snapshot(
    uint32_t *source_dropped,
    uint32_t *queue_dropped,
    uint32_t *spi_dropped,
    uint32_t *transport_errors
)
{
    portENTER_CRITICAL(&s_counter_lock);
    *source_dropped = s_source_dropped_total;
    *queue_dropped = s_queue_dropped_total;
    *spi_dropped = s_spi_dropped_total;
    *transport_errors = s_transport_errors_total;
    portEXIT_CRITICAL(&s_counter_lock);
}

static uint64_t random_nonzero_nonce(void)
{
    uint64_t nonce = ((uint64_t)esp_random() << 32U) | (uint64_t)esp_random();
    return nonce == 0U ? 1U : nonce;
}

static void begin_capture_session(void)
{
    uint64_t nonce = random_nonzero_nonce();

    portENTER_CRITICAL(&s_capture_lock);
    if (nonce == s_capture_session_nonce) {
        ++nonce;
        if (nonce == 0U) {
            nonce = 1U;
        }
    }
    s_capture_session_nonce = nonce;
    s_capture_session_active = true;
    s_timestamp_initialized = false;
    s_timestamp_last_raw = 0;
    s_timestamp_last_extended = 0;
    portEXIT_CRITICAL(&s_capture_lock);
}

static void end_capture_session(void)
{
    portENTER_CRITICAL(&s_capture_lock);
    s_capture_session_active = false;
    s_timestamp_initialized = false;
    s_timestamp_last_raw = 0;
    s_timestamp_last_extended = 0;
    portEXIT_CRITICAL(&s_capture_lock);
}

static bool snapshot_source_clock(
    uint32_t raw_timestamp,
    uint64_t *stream_epoch,
    uint64_t *extended_timestamp
)
{
    bool valid = false;

    portENTER_CRITICAL(&s_capture_lock);
    if (s_capture_session_active && s_capture_session_nonce != 0U) {
        if (!s_timestamp_initialized) {
            s_timestamp_initialized = true;
            s_timestamp_last_raw = raw_timestamp;
            s_timestamp_last_extended = raw_timestamp;
        } else {
            const uint32_t modular_delta = raw_timestamp - s_timestamp_last_raw;
            s_timestamp_last_raw = raw_timestamp;
            s_timestamp_last_extended += modular_delta;
        }
        *stream_epoch = s_capture_session_nonce;
        *extended_timestamp = s_timestamp_last_extended;
        valid = true;
    }
    portEXIT_CRITICAL(&s_capture_lock);
    return valid;
}

static void set_ap_bssid(const uint8_t *bssid, bool valid)
{
    portENTER_CRITICAL(&s_bssid_lock);
    if (valid) {
        memcpy(s_ap_bssid, bssid, sizeof(s_ap_bssid));
    } else {
        memset(s_ap_bssid, 0, sizeof(s_ap_bssid));
    }
    s_ap_bssid_valid = valid;
    portEXIT_CRITICAL(&s_bssid_lock);
}

static bool source_matches_ap(const uint8_t *source_mac)
{
#ifdef CONFIG_WIFI_GUARD_FILTER_AP_BSSID
    uint8_t expected_bssid[sizeof(s_ap_bssid)];
    bool valid;

    portENTER_CRITICAL(&s_bssid_lock);
    memcpy(expected_bssid, s_ap_bssid, sizeof(expected_bssid));
    valid = s_ap_bssid_valid;
    portEXIT_CRITICAL(&s_bssid_lock);
    return valid && memcmp(source_mac, expected_bssid, sizeof(expected_bssid)) == 0;
#else
    (void)source_mac;
    return true;
#endif
}

static bool record_csi_validation(
    const wifi_csi_info_t *info,
    bool timestamp_valid,
    bool source_matches
)
{
    const bool estimate_valid = info->rx_ctrl.rx_channel_estimate_info_vld == 1U;
    const bool format_valid = info->rx_ctrl.cur_bb_format == (unsigned)RX_BB_FORMAT_HE_SU;
    const bool channel_valid = info->rx_ctrl.channel == CONFIG_WIFI_GUARD_EXPECTED_CHANNEL;
    const bool length_valid = info->len == WG_SPI_CSI_PAYLOAD_BYTES;
    const bool first_word_valid = !info->first_word_invalid;
    const bool accepted = timestamp_valid
        && estimate_valid
        && format_valid
        && channel_valid
        && length_valid
        && first_word_valid
        && source_matches;

    portENTER_CRITICAL(&s_counter_lock);
    s_csi_last_length = info->len;
    s_csi_last_format = (uint8_t)info->rx_ctrl.cur_bb_format;
    s_csi_last_channel = (uint8_t)info->rx_ctrl.channel;
    s_csi_last_estimate_valid = (uint8_t)info->rx_ctrl.rx_channel_estimate_info_vld;
    s_csi_last_first_word_invalid = info->first_word_invalid;
    s_csi_last_rssi = (int8_t)info->rx_ctrl.rssi;
    memcpy(s_csi_last_source_mac, info->mac, sizeof(s_csi_last_source_mac));

    if (!timestamp_valid) {
        saturating_increment_locked(&s_csi_timestamp_rejected_total);
    }
    if (!estimate_valid) {
        saturating_increment_locked(&s_csi_estimate_rejected_total);
    }
    if (!format_valid) {
        saturating_increment_locked(&s_csi_format_rejected_total);
    }
    if (!channel_valid) {
        saturating_increment_locked(&s_csi_channel_rejected_total);
    }
    if (!length_valid) {
        saturating_increment_locked(&s_csi_length_rejected_total);
    }
    if (!first_word_valid) {
        saturating_increment_locked(&s_csi_first_word_rejected_total);
    }
    if (!source_matches) {
        saturating_increment_locked(&s_csi_bssid_rejected_total);
    }
    if (accepted) {
        saturating_increment_locked(&s_csi_accepted_total);
    } else {
        saturating_increment_locked(&s_source_dropped_total);
    }
    portEXIT_CRITICAL(&s_counter_lock);
    return accepted;
}

static void wifi_csi_rx_callback(void *context, wifi_csi_info_t *info)
{
    (void)context;
    const uint64_t seq = next_callback_seq();

    if (info == NULL || info->buf == NULL) {
        portENTER_CRITICAL(&s_counter_lock);
        saturating_increment_locked(&s_csi_null_total);
        saturating_increment_locked(&s_source_dropped_total);
        portEXIT_CRITICAL(&s_counter_lock);
        return;
    }

    uint64_t stream_epoch = 0;
    uint64_t source_ts_us = 0;
    const bool timestamp_valid = snapshot_source_clock(
        (uint32_t)info->rx_ctrl.timestamp,
        &stream_epoch,
        &source_ts_us
    );

    if (!record_csi_validation(info, timestamp_valid, source_matches_ap(info->mac))) {
        return;
    }

    capture_frame_t *frame = NULL;
    if (xQueueReceive(s_free_queue, &frame, 0) != pdTRUE) {
        saturating_increment(&s_queue_dropped_total);
        return;
    }

    esp_csi_gain_ctrl_get_rx_gain(&info->rx_ctrl, &frame->agc, &frame->fft);
    frame->stream_epoch = stream_epoch;
    frame->seq = seq;
    frame->source_ts_us = source_ts_us;
    frame->rssi = (int8_t)info->rx_ctrl.rssi;
    frame->noise_floor = (int8_t)info->rx_ctrl.noise_floor;
    frame->channel = (uint8_t)info->rx_ctrl.channel;
    memcpy(frame->csi, info->buf, WG_SPI_CSI_PAYLOAD_BYTES);

    if (xQueueSend(s_ready_queue, &frame, 0) != pdTRUE) {
        saturating_increment(&s_queue_dropped_total);
        require_condition(xQueueSend(s_free_queue, &frame, 0) == pdTRUE);
    }
}

static void write_u32_le(uint8_t *destination, uint32_t value)
{
    destination[0] = (uint8_t)(value & 0xffU);
    destination[1] = (uint8_t)((value >> 8U) & 0xffU);
    destination[2] = (uint8_t)((value >> 16U) & 0xffU);
    destination[3] = (uint8_t)((value >> 24U) & 0xffU);
}

static void set_ready(bool ready)
{
#ifdef CONFIG_WIFI_GUARD_SPI_READY_ACTIVE_HIGH
    const int active_level = 1;
#else
    const int active_level = 0;
#endif
    require_ok(gpio_set_level(
        CONFIG_WIFI_GUARD_SPI_READY_GPIO,
        ready ? active_level : !active_level
    ));
}

static bool losses_changed(
    const uint32_t current[4],
    const uint32_t previous[4]
)
{
    return current[0] != previous[0]
        || current[1] != previous[1]
        || current[2] != previous[2]
        || current[3] != previous[3];
}

static void compose_spi_frame(
    uint8_t *destination,
    const capture_frame_t *capture,
    const uint32_t counters[4],
    const uint32_t previous_counters[4]
)
{
    memset(destination, 0, WG_SPI_FRAME_BYTES);
    wg_spi_csi_header_v1_t *header = (wg_spi_csi_header_v1_t *)destination;
    const uint16_t flags = WG_SPI_FLAG_CSI_VALID
        | (losses_changed(counters, previous_counters)
            ? WG_SPI_FLAG_DROPPED_SINCE_PREVIOUS
            : 0U);

    header->magic[0] = WG_SPI_MAGIC_0;
    header->magic[1] = WG_SPI_MAGIC_1;
    header->magic[2] = WG_SPI_MAGIC_2;
    header->magic[3] = WG_SPI_MAGIC_3;
    header->version = WG_SPI_VERSION;
    header->kind = WG_SPI_KIND_CSI;
    header->flags_le = flags;
    header->header_size_le = WG_SPI_HEADER_BYTES;
    header->frame_size_le = WG_SPI_FRAME_BYTES;
    header->stream_epoch_le = capture->stream_epoch;
    header->frame_seq_le = capture->seq;
    header->source_timestamp_us_le = capture->source_ts_us;
    header->source_dropped_total_le = counters[0];
    header->queue_dropped_total_le = counters[1];
    header->spi_dropped_total_le = counters[2];
    header->transport_errors_total_le = counters[3];
    header->channel_le = capture->channel;
    header->bandwidth_mhz_le = 20U;
    header->subcarrier_count_le = WG_SPI_SUBCARRIER_COUNT;
    header->payload_len_le = WG_SPI_CSI_PAYLOAD_BYTES;
    header->rssi = capture->rssi;
    header->noise_floor = capture->noise_floor;
    header->agc_gain = capture->agc;
    header->fft_gain = capture->fft;
    header->iq_order = WG_SPI_IQ_ORDER_QI;
    header->rx_format = WG_SPI_RX_FORMAT_HE_SU;
    memcpy(header->profile_fingerprint, PROFILE_FINGERPRINT, sizeof(PROFILE_FINGERPRINT));
    memcpy(destination + WG_SPI_HEADER_BYTES, capture->csi, WG_SPI_CSI_PAYLOAD_BYTES);

    const uint32_t crc = esp_rom_crc32_le(
        0,
        destination,
        WG_SPI_FRAME_BYTES - WG_SPI_CRC_BYTES
    );
    write_u32_le(destination + WG_SPI_FRAME_BYTES - WG_SPI_CRC_BYTES, crc);
}

static bool transmit_frame_batch(void)
{
    spi_slave_transaction_t transaction = {
        .length = SPI_TRANSACTION_BYTES * 8U,
        .tx_buffer = s_spi_frame,
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
        .user = wg_spi_trace_context(),
#endif
    };

    esp_err_t result = spi_slave_queue_trans(SPI_HOST, &transaction, portMAX_DELAY);
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
    wg_spi_trace_queued(result);
#endif
    if (result != ESP_OK) {
        saturating_increment(&s_transport_errors_total);
        return false;
    }

    set_ready(true);
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
    wg_spi_trace_ready(true);
#endif
    spi_slave_transaction_t *completed = NULL;
    const esp_err_t completed_result = spi_slave_get_trans_result(
        SPI_HOST,
        &completed,
        portMAX_DELAY
    );
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
    wg_spi_trace_returned();
#endif
    set_ready(false);
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
    wg_spi_trace_ready(false);
#endif

    const bool exact = completed_result == ESP_OK
        && completed == &transaction
        && completed->trans_len == SPI_TRANSACTION_BYTES * 8U;
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
    wg_spi_trace_result(completed_result, completed == &transaction,
        completed == &transaction ? completed->trans_len : UINT32_MAX, exact);
#endif
    if (!exact) {
        saturating_increment(&s_spi_dropped_total);
        if (completed_result != ESP_OK) {
            saturating_increment(&s_transport_errors_total);
        }
    }
    return exact;
}

static void spi_writer_task(void *argument)
{
    (void)argument;
    uint32_t previous_counters[4] = {0};

    for (;;) {
        capture_frame_t *frames[CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES] = {0};
        uint32_t counters[CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES][4] = {0};
        for (size_t index = 0; index < SPI_BATCH_FRAMES; ++index) {
            require_condition(
                xQueueReceive(s_ready_queue, &frames[index], portMAX_DELAY) == pdTRUE
            );
            counter_snapshot(
                &counters[index][0],
                &counters[index][1],
                &counters[index][2],
                &counters[index][3]
            );
            const uint32_t *prior = index == 0U
                ? previous_counters
                : counters[index - 1U];
            compose_spi_frame(
                s_spi_frame + index * WG_SPI_FRAME_BYTES,
                frames[index],
                counters[index],
                prior
            );
        }
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
        wg_spi_trace_begin(frames[0]->stream_epoch, frames[0]->seq);
#endif

        /* A short/noisy CS assertion must not discard source frames. Requeue the
         * same immutable batch until one exact master transaction completes. */
        while (!transmit_frame_batch()) {
            taskYIELD();
        }
        memcpy(
            previous_counters,
            counters[SPI_BATCH_FRAMES - 1U],
            sizeof(previous_counters)
        );
        for (size_t index = 0; index < SPI_BATCH_FRAMES; ++index) {
            require_condition(xQueueSend(s_free_queue, &frames[index], 0) == pdTRUE);
        }
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
        uint32_t trace_counters[4];
        counter_snapshot(
            &trace_counters[0],
            &trace_counters[1],
            &trace_counters[2],
            &trace_counters[3]
        );
        wg_spi_trace_freed(trace_counters);
#endif
    }
}

static void udp_sink_task(void *argument)
{
    (void)argument;
    uint8_t trigger_payload[64];

    for (;;) {
        xEventGroupWaitBits(
            s_wifi_events,
            WIFI_CONNECTED_BIT,
            pdFALSE,
            pdTRUE,
            portMAX_DELAY
        );
        const int socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
        if (socket_fd < 0) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        const int reuse_address = 1;
        (void)setsockopt(
            socket_fd,
            SOL_SOCKET,
            SO_REUSEADDR,
            &reuse_address,
            sizeof(reuse_address)
        );
        const struct sockaddr_in listen_address = {
            .sin_family = AF_INET,
            .sin_port = htons((uint16_t)CONFIG_WIFI_GUARD_UDP_PORT),
            .sin_addr.s_addr = htonl(INADDR_ANY),
        };
        if (bind(
                socket_fd,
                (const struct sockaddr *)&listen_address,
                sizeof(listen_address)
            ) != 0) {
            close(socket_fd);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        while (recvfrom(socket_fd, trigger_payload, sizeof(trigger_payload), 0, NULL, NULL) >= 0) {
            /* The received HE20 SU PPDU is the CSI trigger. */
        }
        close(socket_fd);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

static void diagnostic_task(void *argument)
{
    (void)argument;

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(DIAGNOSTIC_INTERVAL_MS));
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
        wg_spi_trace_log();
#endif

        uint64_t callbacks;
        uint32_t accepted;
        uint32_t null_info;
        uint32_t timestamp_rejected;
        uint32_t estimate_rejected;
        uint32_t format_rejected;
        uint32_t channel_rejected;
        uint32_t length_rejected;
        uint32_t first_word_rejected;
        uint32_t bssid_rejected;
        uint16_t last_length;
        uint8_t last_format;
        uint8_t last_channel;
        uint8_t last_estimate_valid;
        bool last_first_word_invalid;
        int8_t last_rssi;
        uint8_t last_source_mac[6];

        portENTER_CRITICAL(&s_counter_lock);
        callbacks = s_callback_seq;
        accepted = s_csi_accepted_total;
        null_info = s_csi_null_total;
        timestamp_rejected = s_csi_timestamp_rejected_total;
        estimate_rejected = s_csi_estimate_rejected_total;
        format_rejected = s_csi_format_rejected_total;
        channel_rejected = s_csi_channel_rejected_total;
        length_rejected = s_csi_length_rejected_total;
        first_word_rejected = s_csi_first_word_rejected_total;
        bssid_rejected = s_csi_bssid_rejected_total;
        last_length = s_csi_last_length;
        last_format = s_csi_last_format;
        last_channel = s_csi_last_channel;
        last_estimate_valid = s_csi_last_estimate_valid;
        last_first_word_invalid = s_csi_last_first_word_invalid;
        last_rssi = s_csi_last_rssi;
        memcpy(last_source_mac, s_csi_last_source_mac, sizeof(last_source_mac));
        portEXIT_CRITICAL(&s_counter_lock);

        ESP_LOGI(
            TAG,
            "CSI diag callbacks=%llu accepted=%u rejects(null=%u clock=%u estimate=%u format=%u channel=%u len=%u first_word=%u bssid=%u) last(len=%u format=%u channel=%u estimate=%u first_word=%u rssi=%d mac=%02x:%02x:%02x:%02x:%02x:%02x) queues(free=%u ready=%u)",
            (unsigned long long)callbacks,
            (unsigned)accepted,
            (unsigned)null_info,
            (unsigned)timestamp_rejected,
            (unsigned)estimate_rejected,
            (unsigned)format_rejected,
            (unsigned)channel_rejected,
            (unsigned)length_rejected,
            (unsigned)first_word_rejected,
            (unsigned)bssid_rejected,
            (unsigned)last_length,
            (unsigned)last_format,
            (unsigned)last_channel,
            (unsigned)last_estimate_valid,
            (unsigned)last_first_word_invalid,
            (int)last_rssi,
            (unsigned)last_source_mac[0],
            (unsigned)last_source_mac[1],
            (unsigned)last_source_mac[2],
            (unsigned)last_source_mac[3],
            (unsigned)last_source_mac[4],
            (unsigned)last_source_mac[5],
            (unsigned)uxQueueMessagesWaiting(s_free_queue),
            (unsigned)uxQueueMessagesWaiting(s_ready_queue)
        );
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
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
        const wifi_event_sta_connected_t *connected = event_data;
        set_ap_bssid(NULL, false);
        begin_capture_session();
        set_ap_bssid(connected->bssid, true);
        ESP_LOGI(
            TAG,
            "Wi-Fi connected channel=%u aid=%u; CSI session started",
            (unsigned)connected->channel,
            (unsigned)connected->aid
        );
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *disconnected = event_data;
        ESP_LOGW(
            TAG,
            "Wi-Fi disconnected reason=%u; CSI session stopped, reconnecting",
            (unsigned)disconnected->reason
        );
        end_capture_session();
        set_ap_bssid(NULL, false);
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);
        (void)esp_wifi_connect();
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "station IP acquired; UDP CSI trigger sink ready");
    }
}

static void initialize_frame_pool(void)
{
    s_free_queue = xQueueCreateStatic(
        CONFIG_WIFI_GUARD_MAX_QUEUE,
        sizeof(capture_frame_t *),
        (uint8_t *)s_free_queue_storage,
        &s_free_queue_control
    );
    s_ready_queue = xQueueCreateStatic(
        CONFIG_WIFI_GUARD_MAX_QUEUE,
        sizeof(capture_frame_t *),
        (uint8_t *)s_ready_queue_storage,
        &s_ready_queue_control
    );
    require_condition(s_free_queue != NULL && s_ready_queue != NULL);
    for (size_t index = 0; index < (size_t)CONFIG_WIFI_GUARD_MAX_QUEUE; ++index) {
        capture_frame_t *frame = &s_frame_pool[index];
        require_condition(xQueueSend(s_free_queue, &frame, 0) == pdTRUE);
    }
}

static void initialize_spi(void)
{
    const int gpio_values[] = {
        CONFIG_WIFI_GUARD_SPI_SCLK_GPIO,
        CONFIG_WIFI_GUARD_SPI_MOSI_GPIO,
        CONFIG_WIFI_GUARD_SPI_MISO_GPIO,
        CONFIG_WIFI_GUARD_SPI_CS_GPIO,
        CONFIG_WIFI_GUARD_SPI_READY_GPIO,
    };
    for (size_t left = 0; left < sizeof(gpio_values) / sizeof(gpio_values[0]); ++left) {
        for (size_t right = left + 1; right < sizeof(gpio_values) / sizeof(gpio_values[0]); ++right) {
            require_condition(gpio_values[left] != gpio_values[right]);
        }
    }

    const gpio_config_t ready_config = {
        .pin_bit_mask = 1ULL << CONFIG_WIFI_GUARD_SPI_READY_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    require_ok(gpio_config(&ready_config));
    set_ready(false);

    const spi_bus_config_t bus_config = {
        .mosi_io_num = CONFIG_WIFI_GUARD_SPI_MOSI_GPIO,
        .miso_io_num = CONFIG_WIFI_GUARD_SPI_MISO_GPIO,
        .sclk_io_num = CONFIG_WIFI_GUARD_SPI_SCLK_GPIO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = SPI_TRANSACTION_BYTES,
        .flags = SPICOMMON_BUSFLAG_SLAVE,
    };
    const spi_slave_interface_config_t slave_config = {
        .spics_io_num = CONFIG_WIFI_GUARD_SPI_CS_GPIO,
        .queue_size = SPI_QUEUE_SIZE,
        .mode = CONFIG_WIFI_GUARD_SPI_MODE,
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
        .post_setup_cb = wg_spi_trace_post_setup,
        .post_trans_cb = wg_spi_trace_post_trans,
#endif
    };
    require_ok(spi_slave_initialize(
        SPI_HOST,
        &bus_config,
        &slave_config,
        SPI_DMA_CH_AUTO
    ));
#ifdef CONFIG_WIFI_GUARD_SPI_CS_PULLUP
    /* Apply only the CS bias, after the SPI driver has configured its input.
     * Do not use gpio_config/reset here: those can replace the SPI pin routing.
     * This is an opt-in experiment, not evidence that CS noise caused a drop. */
    require_ok(gpio_set_pull_mode(CONFIG_WIFI_GUARD_SPI_CS_GPIO, GPIO_PULLUP_ONLY));
    ESP_LOGI(TAG, "SPI_CS_PULLUP_V1 gpio=%d initial_level=%d; CS bias only; hardware validation required",
        CONFIG_WIFI_GUARD_SPI_CS_GPIO, gpio_get_level(CONFIG_WIFI_GUARD_SPI_CS_GPIO));
    require_ok(gpio_dump_io_configuration(stdout, 1ULL << CONFIG_WIFI_GUARD_SPI_CS_GPIO));
#endif
    s_spi_frame = spi_bus_dma_memory_alloc(SPI_HOST, SPI_TRANSACTION_BYTES, 0);
    require_condition(s_spi_frame != NULL && ((uintptr_t)s_spi_frame % 4U) == 0U);
    require_condition(xTaskCreate(
        spi_writer_task,
        "wg_spi_writer",
        SPI_TASK_STACK_SIZE,
        NULL,
        SPI_TASK_PRIORITY,
        NULL
    ) == pdPASS);
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

static wifi_band_mode_t configured_band_mode(void)
{
#if defined(CONFIG_WIFI_GUARD_WIFI_BAND_5G_ONLY)
    return WIFI_BAND_MODE_5G_ONLY;
#elif defined(CONFIG_WIFI_GUARD_WIFI_BAND_2G_ONLY)
    return WIFI_BAND_MODE_2G_ONLY;
#else
    return WIFI_BAND_MODE_AUTO;
#endif
}

static void initialize_wifi(void)
{
    require_ok(esp_netif_init());
    require_ok(esp_event_loop_create_default());
    require_condition(esp_netif_create_default_wifi_sta() != NULL);
    s_wifi_events = xEventGroupCreate();
    require_condition(s_wifi_events != NULL);
    require_ok(esp_event_handler_instance_register(
        WIFI_EVENT,
        ESP_EVENT_ANY_ID,
        network_event_handler,
        NULL,
        &s_wifi_event_instance
    ));
    require_ok(esp_event_handler_instance_register(
        IP_EVENT,
        IP_EVENT_STA_GOT_IP,
        network_event_handler,
        NULL,
        &s_ip_event_instance
    ));

    wifi_init_config_t wifi_init_config = WIFI_INIT_CONFIG_DEFAULT();
    require_ok(esp_wifi_init(&wifi_init_config));
    require_ok(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    require_ok(esp_wifi_set_mode(WIFI_MODE_STA));

    wifi_config_t wifi_config = {0};
    const size_t ssid_length = strlen(CONFIG_WIFI_GUARD_WIFI_SSID);
    const size_t password_length = strlen(CONFIG_WIFI_GUARD_WIFI_PASSWORD);
    require_condition(ssid_length > 0 && ssid_length <= sizeof(wifi_config.sta.ssid));
    require_condition(password_length <= sizeof(wifi_config.sta.password));
    memcpy(wifi_config.sta.ssid, CONFIG_WIFI_GUARD_WIFI_SSID, ssid_length);
    memcpy(wifi_config.sta.password, CONFIG_WIFI_GUARD_WIFI_PASSWORD, password_length);
    require_ok(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    require_ok(esp_wifi_start());
    require_ok(esp_wifi_set_band_mode(configured_band_mode()));

    wifi_protocols_t protocols = {
        .ghz_2g = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_11AX,
        .ghz_5g = WIFI_PROTOCOL_11A | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_11AC | WIFI_PROTOCOL_11AX,
    };
    require_ok(esp_wifi_set_protocols(WIFI_IF_STA, &protocols));
    wifi_bandwidths_t bandwidths = {
        .ghz_2g = WIFI_BW20,
        .ghz_5g = WIFI_BW20,
    };
    require_ok(esp_wifi_set_bandwidths(WIFI_IF_STA, &bandwidths));
    require_ok(esp_wifi_set_ps(WIFI_PS_NONE));
}

static void initialize_csi(void)
{
    const wifi_csi_config_t csi_config = {
        .enable = 1,
        .acquire_csi_legacy = 0,
        .acquire_csi_force_lltf = 0,
        .acquire_csi_ht20 = 0,
        .acquire_csi_ht40 = 0,
        .acquire_csi_vht = 0,
        .acquire_csi_su = 1,
        .acquire_csi_mu = 0,
        .acquire_csi_dcm = 0,
        .acquire_csi_beamformed = 0,
        .acquire_csi_he_stbc_mode = ESP_CSI_ACQUIRE_STBC_HELTF1,
        .val_scale_cfg = 0,
        .dump_ack_en = 0,
        .lltf_bit_mode = 1,
        .reserved = 0,
    };
    require_ok(esp_wifi_set_csi_rx_cb(wifi_csi_rx_callback, NULL));
    require_ok(esp_wifi_set_csi_config(&csi_config));
    require_ok(esp_wifi_set_csi(true));
}

void app_main(void)
{
#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
    ESP_LOGW(TAG, "SPI_COMPLETION_TRACE_V1 diagnostic-only first-64 attempts; not a load-test image");
#endif
    ESP_LOGI(
        TAG,
        "WGSP v1 profile=%s frame=%u bytes batch=%u transaction_bytes=%u SPI=%d/%d/%d/%d READY=%d",
        WG_SPI_PROFILE_ID,
        WG_SPI_FRAME_BYTES,
        CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES,
        (unsigned)SPI_TRANSACTION_BYTES,
        CONFIG_WIFI_GUARD_SPI_SCLK_GPIO,
        CONFIG_WIFI_GUARD_SPI_MOSI_GPIO,
        CONFIG_WIFI_GUARD_SPI_MISO_GPIO,
        CONFIG_WIFI_GUARD_SPI_CS_GPIO,
        CONFIG_WIFI_GUARD_SPI_READY_GPIO
    );
    initialize_nvs();
    initialize_frame_pool();
    initialize_spi();
    initialize_wifi();
    initialize_csi();
    require_condition(xTaskCreate(
        udp_sink_task,
        "wg_udp_sink",
        UDP_TASK_STACK_SIZE,
        NULL,
        UDP_TASK_PRIORITY,
        NULL
    ) == pdPASS);
    require_condition(xTaskCreate(
        diagnostic_task,
        "wg_csi_diag",
        DIAGNOSTIC_TASK_STACK_SIZE,
        NULL,
        DIAGNOSTIC_TASK_PRIORITY,
        NULL
    ) == pdPASS);
    require_ok(esp_wifi_connect());
}
