# ESP32-C5 native 320 Hz transmitter

Status: **IMPLEMENTED AND USED IN THE VALIDATED 320 Hz Batch8 HARDWARE PATH**.

This project is the source-controlled 320 Hz replacement for the backed-up pair-test
sender. It creates a 5 GHz HE20 SoftAP on channel 48 and sends one 12-byte UDP
trigger to the associated receiver for each scheduler deadline.

At 320 Hz, the absolute trigger interval is exactly 3,125 microseconds.
The one-shot absolute scheduler therefore repeats six 2857 us intervals and one
2858 us interval. Seven intervals total exactly 20,000 us, avoiding the drift
caused by repeated relative 2857 us sleeps. Send errors, notification backlog,
and missed absolute deadlines are separately counted and logged.

The checked-in SSID/password and default 2 MB flash setting are compile-only
placeholders. Before flashing, create an untracked private credential overlay,
apply the reviewed 16 MB board overlay, verify the sender backup hash, and use a
fresh build directory. The paired transport has been measured at approximately
320 Hz; a one-hour stability run remains required.

The compile-only evidence is recorded in
`artifacts/verification/amfall-tx-firmware-offline-build-20260829.json`. That
image must not be flashed: its flash size and credentials are deliberately not
the reviewed hardware configuration.

## ESP32-C5-DevKitC-1 / ESP32-C5-WROOM-1U-N32

For the 32 MB ESP32-C5-WROOM-1U board, build with the dedicated hardware
overlay and an untracked private credential overlay:

```powershell
idf.py -B build-devkitc1-wroom1u-n32-private `
  -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.esp32-c5-devkitc1-wroom1u-n32.defaults;sdkconfig.private.defaults" `
  set-target esp32c5 build
```

The WROOM-1U module has an external-antenna connector. Attach a compatible
2.4/5 GHz antenna to ANT1 before starting the 5 GHz channel-48 SoftAP or doing
any RF validation. No external GPIO wiring is required for the transmitter;
USB supplies power and UART logging.
