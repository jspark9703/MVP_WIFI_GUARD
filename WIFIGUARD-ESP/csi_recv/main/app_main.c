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

#include "nvs_flash.h"

#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_rom_uart.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_csi_gain_ctrl.h"
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
    static int s_count = 0;
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;
#if CONFIG_GAIN_CONTROL
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (s_count < 100)
    {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    }
    else if (s_count == 100)
    {
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
#endif
    }
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
}
