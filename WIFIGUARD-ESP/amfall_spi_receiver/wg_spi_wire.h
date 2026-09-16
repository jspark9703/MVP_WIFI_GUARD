/*
 * WIFI-GUARD ESP32-C5 -> Raspberry Pi SPI CSI contract.
 *
 * Versioned clean-room wire contract shared by the C5 SPI-slave firmware and
 * the Raspberry Pi parser. Multi-byte fields are little-endian. The payload is
 * signed int8 interleaved IQ (or QI when iq_order=1), followed by 0..3 zero
 * padding bytes and a little-endian IEEE CRC32 over header+payload+padding.
 * The fixed logical frame is clocked in one physical CS transaction while
 * READY and immutable frame ownership are retained by the slave state machine.
 */
#pragma once

#include <stdint.h>

#define WG_SPI_MAGIC_0 'W'
#define WG_SPI_MAGIC_1 'G'
#define WG_SPI_MAGIC_2 'S'
#define WG_SPI_MAGIC_3 'P'
#define WG_SPI_VERSION 1u
#define WG_SPI_KIND_CSI 1u

#define WG_SPI_FLAG_CSI_VALID (1u << 0)
#define WG_SPI_FLAG_FIRST_WORD_INVALID (1u << 1)
#define WG_SPI_FLAG_TRUNCATED (1u << 2)
#define WG_SPI_FLAG_CALIBRATING (1u << 3)
#define WG_SPI_FLAG_DROPPED_SINCE_PREVIOUS (1u << 4)

#define WG_SPI_IQ_ORDER_IQ 0u
#define WG_SPI_IQ_ORDER_QI 1u
#define WG_SPI_RX_FORMAT_HE_SU 4u
#define WG_SPI_CSI_PAYLOAD_BYTES 490u
#define WG_SPI_SUBCARRIER_COUNT 245u
#define WG_SPI_HEADER_BYTES 80u
#define WG_SPI_PADDING_BYTES 2u
#define WG_SPI_CRC_BYTES 4u
#define WG_SPI_FRAME_BYTES 576u

#define WG_SPI_PROFILE_ID "esp32c5-he20-5g-ch48-amfall-spi-320hz-v1"
#define WG_SPI_PROFILE_FINGERPRINT_BYTES \
    { 0xecu, 0x52u, 0x91u, 0xe5u, 0x5eu, 0x14u, 0xf8u, 0xd2u }

typedef struct __attribute__((packed)) {
    uint8_t magic[4];
    uint8_t version;
    uint8_t kind;
    uint16_t flags_le;
    uint16_t header_size_le;
    uint16_t reserved_le;
    uint32_t frame_size_le;
    uint64_t stream_epoch_le;
    uint64_t frame_seq_le;
    uint64_t source_timestamp_us_le;
    uint32_t source_dropped_total_le;
    uint32_t queue_dropped_total_le;
    uint32_t spi_dropped_total_le;
    uint32_t transport_errors_total_le;
    uint16_t channel_le;
    uint16_t bandwidth_mhz_le;
    uint16_t subcarrier_count_le;
    uint16_t payload_len_le;
    int8_t rssi;
    int8_t noise_floor;
    uint8_t agc_gain;
    int8_t fft_gain;
    uint8_t iq_order;
    uint8_t rx_format;
    uint16_t reserved2_le;
    uint8_t profile_fingerprint[8];
} wg_spi_csi_header_v1_t;

_Static_assert(sizeof(wg_spi_csi_header_v1_t) == 80u, "WG SPI v1 header must be 80 bytes");
_Static_assert(
    WG_SPI_HEADER_BYTES + WG_SPI_CSI_PAYLOAD_BYTES + WG_SPI_PADDING_BYTES
        + WG_SPI_CRC_BYTES == WG_SPI_FRAME_BYTES,
    "WG SPI v1 frame geometry must remain fixed"
);
