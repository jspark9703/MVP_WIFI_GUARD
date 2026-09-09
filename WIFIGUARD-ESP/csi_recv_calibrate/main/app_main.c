/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* Get Start Example

   This example code is in the Public Domain (or CC0 licensed, at your option.)

   Unless required by applicable law or agreed to in writing, this
   software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
   CONDITIONS OF ANY KIND, either express or implied.
*/

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

#include "nvs_flash.h"

#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_rom_uart.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_csi_gain_ctrl.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

// 1: 5GHz (channel 48), 0: 2.4GHz (channel 11)
#define USE_5G_BAND 1

#if USE_5G_BAND
#define CONFIG_LESS_INTERFERENCE_CHANNEL 48
#else
#define CONFIG_LESS_INTERFERENCE_CHANNEL 11
#endif

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#if USE_5G_BAND
#define CONFIG_WIFI_BAND_MODE WIFI_BAND_MODE_5G_ONLY
#else
#define CONFIG_WIFI_BAND_MODE WIFI_BAND_MODE_2G_ONLY
#endif
#define CONFIG_WIFI_2G_BANDWIDTHS WIFI_BW20
#define CONFIG_WIFI_5G_BANDWIDTHS WIFI_BW20
#define CONFIG_WIFI_2G_PROTOCOL WIFI_PROTOCOL_11AX
#define CONFIG_WIFI_5G_PROTOCOL WIFI_PROTOCOL_11AX
#else
#define CONFIG_WIFI_BANDWIDTH WIFI_BW20
#endif

#define CONFIG_ESP_NOW_RATE WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_ESP_NOW_PHYMODE WIFI_PHY_MODE_HE20
#define CONFIG_FORCE_GAIN 1

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF 0
#endif

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

static const uint8_t CONFIG_CSI_SEND_MAC[] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};
static const char *TAG = "csi_recv";

typedef enum {
    CSI_PHASE_IDLE = 0,      /* boot default: CSI packets are dropped, nothing streamed */
    CSI_PHASE_TRAINING,      /* "train" received: recording AGC gain samples for 1s */
    CSI_PHASE_STREAMING,     /* calibration window elapsed: normal binary streaming */
} csi_phase_t;

#define CSI_TRAIN_COMMAND       "train"
#define CSI_TRAIN_DURATION_US   1000000LL   /* ~1 second, wall-clock via esp_timer_get_time(). Host
                                                (tools/fall_detect) treats this as the AGC-settle "wait"
                                                phase, then captures its own 10s baseline-variance window
                                                from the CSI frames that start streaming once this elapses. */
#define CSI_CMD_TASK_STACK_SIZE 4096
#define CSI_CMD_TASK_PRIORITY   4
#define CSI_CMD_LINE_BUF_SIZE   32
#define CSI_CMD_POLL_PERIOD_MS  20

static volatile csi_phase_t s_csi_phase = CSI_PHASE_IDLE;
static volatile int64_t s_csi_training_start_us = 0;
static portMUX_TYPE s_csi_phase_lock = portMUX_INITIALIZER_UNLOCKED;

#define CSI_FRAME_MAGIC 0xA55A
#define CSI_FRAME_VERSION 1 
#define CSI_FRAME_TYPE_CSI 1
#define CSI_FRAME_MAX_PAYLOAD 612

typedef struct __attribute__((packed))
{
    uint16_t magic;
    uint16_t frame_len;
    uint8_t version;
    uint8_t frame_type;
    uint32_t seq;
    uint8_t mac[6];
    int8_t rssi;
    uint8_t rate;
    uint8_t sig_mode;
    uint8_t mcs;
    uint8_t cwb;
    uint8_t smoothing;
    uint8_t not_sounding;
    uint8_t aggregation;
    uint8_t stbc;
    uint8_t fec_coding;
    uint8_t sgi;
    int8_t noise_floor;
    uint8_t ampdu_cnt;
    uint8_t channel;
    uint8_t secondary_channel;
    uint8_t ant;
    uint16_t sig_len;
    uint16_t rx_state;
    uint32_t local_timestamp;
    int8_t fft_gain;
    uint8_t agc_gain;
    uint16_t csi_len;
    uint8_t first_word_invalid;
    uint8_t reserved;
} csi_binary_frame_header_t;

static uint16_t csi_frame_checksum(const uint8_t *data, size_t len)
{
    uint32_t sum = 0;
    for (size_t i = 0; i < len; i++)
    {
        sum += data[i];
    }
    return (uint16_t)(sum & 0xFFFF);
}

static void csi_uart_write_raw(const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++)
    {
        esp_rom_uart_tx_one_char(data[i]);
    }
}

static void csi_send_binary_frame(const wifi_csi_info_t *info,
                                  const wifi_pkt_rx_ctrl_t *rx_ctrl,
                                  int seq,
                                  float compensate_gain,
                                  int8_t fft_gain,
                                  uint8_t agc_gain)
{
    csi_binary_frame_header_t header = {0};
    uint16_t payload_len = info->len;
    if (payload_len > CSI_FRAME_MAX_PAYLOAD)
    {
        payload_len = CSI_FRAME_MAX_PAYLOAD;
    }

    uint8_t frame_buf[sizeof(csi_binary_frame_header_t) + CSI_FRAME_MAX_PAYLOAD + sizeof(uint16_t)] = {0};
    int16_t scaled = 0;

    header.magic = CSI_FRAME_MAGIC;
    header.version = CSI_FRAME_VERSION;
    header.frame_type = CSI_FRAME_TYPE_CSI;
    header.seq = (uint32_t)seq;
    memcpy(header.mac, info->mac, sizeof(header.mac));
    header.rssi = rx_ctrl->rssi;
    header.rate = rx_ctrl->rate;
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
    header.sig_mode = 0;
    header.mcs = 0;
    header.cwb = 0;
    header.smoothing = 0;
    header.not_sounding = 0;
    header.aggregation = 0;
    header.stbc = 0;
    header.fec_coding = 0;
    header.sgi = 0;
    header.ampdu_cnt = 0;
    header.secondary_channel = 0;
    header.ant = 0;
#else
    header.sig_mode = rx_ctrl->sig_mode;
    header.mcs = rx_ctrl->mcs;
    header.cwb = rx_ctrl->cwb;
    header.smoothing = rx_ctrl->smoothing;
    header.not_sounding = rx_ctrl->not_sounding;
    header.aggregation = rx_ctrl->aggregation;
    header.stbc = rx_ctrl->stbc;
    header.fec_coding = rx_ctrl->fec_coding;
    header.sgi = rx_ctrl->sgi;
    header.ampdu_cnt = rx_ctrl->ampdu_cnt;
    header.secondary_channel = rx_ctrl->secondary_channel;
    header.ant = rx_ctrl->ant;
#endif
    header.noise_floor = rx_ctrl->noise_floor;
    header.channel = rx_ctrl->channel;
    header.sig_len = rx_ctrl->sig_len;
    header.rx_state = rx_ctrl->rx_state;
    header.local_timestamp = rx_ctrl->timestamp;
    header.fft_gain = fft_gain;
    header.agc_gain = agc_gain;
    header.csi_len = payload_len;
    header.first_word_invalid = info->first_word_invalid ? 1 : 0;
    header.frame_len = (uint16_t)(sizeof(csi_binary_frame_header_t) + payload_len + sizeof(uint16_t));

    memcpy(frame_buf, &header, sizeof(csi_binary_frame_header_t));
    for (uint16_t i = 0; i < payload_len; i++)
    {
        scaled = (int16_t)(compensate_gain * info->buf[i]);
        if (scaled > 127)
        {
            scaled = 127;
        }
        else if (scaled < -128)
        {
            scaled = -128;
        }
        frame_buf[sizeof(csi_binary_frame_header_t) + i] = (uint8_t)((int8_t)scaled);
    }

    uint16_t checksum = csi_frame_checksum(frame_buf, sizeof(csi_binary_frame_header_t) + payload_len);
    memcpy(frame_buf + sizeof(csi_binary_frame_header_t) + payload_len, &checksum, sizeof(checksum));

    csi_uart_write_raw(frame_buf, header.frame_len);
}

static void wifi_init()
{
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if CONFIG_IDF_TARGET_ESP32C5
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
        .ghz_5g = CONFIG_WIFI_5G_PROTOCOL};
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
        .ghz_5g = CONFIG_WIFI_5G_BANDWIDTHS};
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
    };
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
    };
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#else
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, CONFIG_WIFI_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_start());
#endif

    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
#if CONFIG_IDF_TARGET_ESP32C5
    if ((CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY && CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW20) || (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_5G_ONLY && CONFIG_WIFI_5G_BANDWIDTHS == WIFI_BW20))
    {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    }
    else
    {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    if (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY && CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW20)
    {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    }
    else
    {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#else
    if (CONFIG_WIFI_BANDWIDTH == WIFI_BW20)
    {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    }
    else
    {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#endif

    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CONFIG_CSI_SEND_MAC));
}

static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
    esp_now_rate_config_t rate_config = {
        .phymode = CONFIG_ESP_NOW_PHYMODE,
        .rate = CONFIG_ESP_NOW_RATE, //  WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false};
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));
}

static void csi_train_start(void)
{
    portENTER_CRITICAL(&s_csi_phase_lock);
    s_csi_phase = CSI_PHASE_TRAINING;
    s_csi_training_start_us = esp_timer_get_time();
    portEXIT_CRITICAL(&s_csi_phase_lock);

#if CONFIG_GAIN_CONTROL
    esp_csi_gain_ctrl_reset_rx_gain_baseline();
    ESP_LOGI(TAG, "train command received, starting %d ms AGC calibration window",
             (int)(CSI_TRAIN_DURATION_US / 1000));
#else
    ESP_LOGI(TAG, "train command received (CONFIG_GAIN_CONTROL not enabled for this target, "
                  "waiting out the %d ms window with no calibration, then streaming)",
             (int)(CSI_TRAIN_DURATION_US / 1000));
#endif
}

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf)
    {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

    if (memcmp(info->mac, CONFIG_CSI_SEND_MAC, 6))
    {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    csi_phase_t phase;
    int64_t training_start_us;
    portENTER_CRITICAL(&s_csi_phase_lock);
    phase = s_csi_phase;
    training_start_us = s_csi_training_start_us;
    portEXIT_CRITICAL(&s_csi_phase_lock);

    if (phase == CSI_PHASE_IDLE)
    {
        return; /* waiting for "train" over serial */
    }

    static int s_count = 0;
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;

    if (phase == CSI_PHASE_TRAINING)
    {
#if CONFIG_GAIN_CONTROL
        esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
#endif
        if (esp_timer_get_time() - training_start_us >= CSI_TRAIN_DURATION_US)
        {
#if CONFIG_GAIN_CONTROL
            uint8_t agc_gain_baseline = 0;
            int8_t fft_gain_baseline = 0;
            if (esp_csi_gain_ctrl_get_gain_status() != RX_GAIN_READY)
            {
                ESP_LOGW(TAG, "AGC baseline not fully settled after %d ms training window, "
                              "using best-effort values",
                         (int)(CSI_TRAIN_DURATION_US / 1000));
            }
            esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
            esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
            ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
#endif
            ESP_LOGI(TAG, "AGC baseline ready (agc=%d, fft=%d), streaming starting",
                     agc_gain_baseline, fft_gain_baseline);
#else
            ESP_LOGI(TAG, "training window elapsed, streaming starting");
#endif
            s_count = 0;
            portENTER_CRITICAL(&s_csi_phase_lock);
            s_csi_phase = CSI_PHASE_STREAMING;
            portEXIT_CRITICAL(&s_csi_phase_lock);
        }
        return; /* never stream while training, including the transition packet itself */
    }

    /* phase == CSI_PHASE_STREAMING */
#if CONFIG_GAIN_CONTROL
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
    // Avoid per-packet INFO logs that can congest UART and cause monitor lag.
    ESP_LOGD(TAG, "compensate_gain %f, agc_gain %d, fft_gain %d", compensate_gain, agc_gain, fft_gain);
#endif

    if (!s_count)
    {
        ESP_LOGI(TAG, "================ CSI RECV BINARY ================");
    }
    csi_send_binary_frame(info, rx_ctrl, s_count, compensate_gain, fft_gain, agc_gain);
    s_count++;
}

static void wifi_csi_init()
{
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    /**< default config */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_legacy = false,
        .acquire_csi_force_lltf = CSI_FORCE_LLTF,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
        .acquire_csi_vht = false,
        .acquire_csi_su = true,
        .acquire_csi_mu = true,
        .acquire_csi_dcm = false,
        .acquire_csi_beamformed = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg = 0,
        .dump_ack_en = false,
        .reserved = false};
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_legacy = false,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
        .acquire_csi_su = true,
        .acquire_csi_mu = true,
        .acquire_csi_dcm = true,
        .acquire_csi_beamformed = true,
        .acquire_csi_he_stbc = 2,
        .val_scale_cfg = false,
        .dump_ack_en = false,
        .reserved = false};
#else
    wifi_csi_config_t csi_config = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = true,
        .manu_scale = false,
        .shift = false,
    };
#endif
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void csi_train_cmd_task(void *arg)
{
    (void)arg;

    int fd = fileno(stdin);
    if (fd >= 0)
    {
        int flags = fcntl(fd, F_GETFL, 0);
        if (flags >= 0)
        {
            fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        }
    }

    char line_buf[CSI_CMD_LINE_BUF_SIZE];
    size_t line_len = 0;

    while (1)
    {
        uint8_t rx_buf[32];
        ssize_t read_len = read(fd, rx_buf, sizeof(rx_buf));

        if (read_len > 0)
        {
            for (ssize_t i = 0; i < read_len; i++)
            {
                char ch = (char)rx_buf[i];

                if (ch == '\r' || ch == '\n')
                {
                    if (line_len > 0)
                    {
                        line_buf[line_len] = '\0';
                        if (strcmp(line_buf, CSI_TRAIN_COMMAND) == 0)
                        {
                            csi_train_start();
                        }
                        line_len = 0;
                    }
                    continue;
                }

                if (line_len < (sizeof(line_buf) - 1))
                {
                    line_buf[line_len++] = ch;
                }
                else
                {
                    line_len = 0; /* overflow: discard and resync on next terminator */
                }
            }
        }
        else if (read_len < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
        {
            ESP_LOGD(TAG, "stdin read error: errno=%d", errno);
        }

        vTaskDelay(pdMS_TO_TICKS(CSI_CMD_POLL_PERIOD_MS));
    }
}

void app_main()
{
    /**
     * @brief Initialize NVS
     */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /**
     * @brief Initialize Wi-Fi
     */
    wifi_init();

    /**
     * @brief Initialize ESP-NOW
     *        ESP-NOW protocol see: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html
     */

    esp_now_peer_info_t peer = {
        .channel = CONFIG_LESS_INTERFERENCE_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };

    wifi_esp_now_init(peer);

    wifi_csi_init();

    BaseType_t task_ok = xTaskCreate(csi_train_cmd_task, "csi_train_cmd",
                                      CSI_CMD_TASK_STACK_SIZE, NULL,
                                      CSI_CMD_TASK_PRIORITY, NULL);
    if (task_ok != pdPASS)
    {
        ESP_LOGE(TAG, "failed to create csi_train_cmd_task");
    }

    ESP_LOGI(TAG, "waiting for 'train' command over serial to start AGC calibration");
}
