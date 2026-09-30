# ESP32-C5 AM-Fall SPI receiver

Status: **Batch8 hardware path validated with Raspberry Pi edge 2.10.2**.

This is a separate clean-room ESP-IDF v6.0.2 project. It does not change the
accepted USB/WGCS receiver artifacts and it does not use the processing profile
from `esp32c5-main.zip`.

The receiver joins the configured 5 GHz HE20 network, accepts only valid HE-SU
CSI from the associated AP BSSID on channel 48, and copies each 490-byte native
QI frame into a bounded static queue. A dedicated task owns eight DMA frames
and serves them as one fixed SPI-slave transaction:

```text
READY high
  one 4608-byte transaction = 8 × (80-byte header + 490 CSI + 2 zero padding + 4 CRC32)
READY low
```

The transaction is queued before READY is asserted, so the Pi cannot race an
unprepared frame. The buffer stays immutable until the CS transaction returns.
This reduces the READY/CS handshake rate to 40 transactions/s while retaining
the native 320-frame/s stream. The matching Pi implementation uses one Linux
SPI ioctl per batch at 6 MHz and reopens the descriptor after an all-invalid
batch.
Source rejects, queue overflow, unexpected SPI lengths, and transport
errors have separate cumulative counters. Every frame carries profile
fingerprint `ec5291e55e14f8d2` for
`esp32c5-he20-5g-ch48-amfall-spi-320hz-v1`.

## Safety gate

`sdkconfig.defaults` deliberately leaves
`CONFIG_WIFI_GUARD_BOARD_PINS_APPROVED=n`. `sdkconfig.ci.defaults` contains
compile-only placeholder GPIOs and remains **MUST NOT BE FLASHED**.

The photographed TX and RX boards were identified as matching
`ESP32-C5-WIFI6-KIT-N16R8` carriers. The board-specific
`sdkconfig.waveshare-n16r8.defaults` overlay uses GPIO8/9/10/23/24 for
SCLK/MOSI/MISO/CS/READY. This avoids GPIO6 (`BAT_ADC`), GPIO11/12 (CH343 UART),
GPIO13/14 (Native USB), GPIO27 (RGB LED), and the relevant boot/JTAG strapping
pins. It records the 16 MB flash detected on both boards but does not enable or
depend on PSRAM.

The offline CI build completed on 2026-08-29 with ESP-IDF v6.0.2 and
`espressif/esp_csi_gain_ctrl` v0.1.5. Its application image SHA-256 is
`62f860c9ba6e6790ed11f44ad89df25624cd8befd2e01f5dbfe9b394f8d39417`.
That image also has empty Wi-Fi credentials and a 2 MB compile-only flash
setting, so it is evidence of compilation only and must not be flashed.

The reviewed N16R8 overlay build completed on 2026-08-30 with ESP-IDF v6.0.2.
Its 16 MB application image is 935,072 bytes with SHA-256
`afffe059a10ccdd5cfd1124b0384759f487c8ead0ae10d7eb7ed0e25c46ab12f`.
The image still has empty Wi-Fi credentials and has not been flashed; it proves
only that the reviewed pin and flash overlay compiles.

## Reproducible compile-only build

From an exported ESP-IDF v6.0.2 shell:

```powershell
idf.py -B build-ci -D IDF_TARGET=esp32c5 `
  -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.ci.defaults" build
```

For hardware, use the separate reviewed board overlay and an untracked private
overlay containing only Wi-Fi credentials. Use a fresh build directory. Never
turn the CI overlay into a hardware overlay by assumption.

The reviewed board build uses the checked-in overlay plus a private, untracked
credential overlay:

```powershell
idf.py -B build-waveshare-n16r8 -D IDF_TARGET=esp32c5 `
  -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.waveshare-n16r8.defaults;sdkconfig.private.defaults" build
```

Build output is still not authorization to flash. Re-check the saved full-flash
backup, wiring, power source, and generated sdkconfig before flashing.

The native-320 Batch8 hardware image was rebuilt and flashed with private
station credentials and the reviewed Waveshare GPIO overlay in a fresh
sdkconfig.
The generated configuration was checked without printing the password: SSID
`WIFI-GUARD-PAIR`, 5 GHz only, channel 48, GPIO 8/9/10/23/24, SPI mode 0,
16 MB, 80 MHz and DIO all passed. UART0/115200 maintenance output and INFO-level
startup/association logs are enabled through the board's CH343 bridge. The
checked-in board overlay now fixes `CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES=8` and
contains no credential. Password-bearing sdkconfig files and build directories
are ignored.

For the replacement ESP32-C5-DevKitC-1 with an ESP32-C5-WROOM-1U 32 MB
module, use the separate reviewed overlay. It keeps the same GPIO8/9/10/23/24
SPI/READY contract and changes only the carrier description and detected flash
size:

```powershell
idf.py -B build-devkitc1-wroom1u-n32 -D IDF_TARGET=esp32c5 `
  -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.esp32-c5-devkitc1-wroom1u-n32.defaults;sdkconfig.private.defaults" build
```

The WROOM-1U variant requires a suitable external dual-band antenna on ANT1
for reliable 2.4/5 GHz operation. The private credential overlay and full
32 MB factory-flash backup remain local and must not be committed.

## Hardware evidence and remaining gates

The validated paired path is ESP32-C5 receiver → WGSP v1 Batch8 → Raspberry Pi
`WgspBatch8Source`. A 30-second end-to-end run decoded 19,268 frames at
320.014 Hz, published 168 windows and reported five sequence-gap frames with no
transport error. Kafka offsets increased in the downstream pipeline. This is
transport evidence, not a claim about fall-classification accuracy.

- 3.3 V logic, common ground, continuity and power-off wiring inspection;
- full-flash backup re-verification immediately before any flash;
- one-hour soak and MQTT/Kafka outage recovery evidence.

A build success is not proof of correct GPIOs, sustained 320 Hz CSI, SPI signal quality,
or an end-to-end fall-detection result.
