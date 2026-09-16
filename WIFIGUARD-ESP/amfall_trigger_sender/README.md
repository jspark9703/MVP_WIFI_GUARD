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
