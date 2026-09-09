# Optimize `csi_send_binary_frame` in `csi_recv_calibrate`

## Context

The user pasted an external code review of `csi_recv_calibrate/main/app_main.c` proposing three optimizations to the per-packet CSI binary-frame send path: (1) move the ~660-byte `frame_buf` off the stack, (2) replace a per-subcarrier float gain-compensation multiply with fixed-point integer math (ESP32-C5 has no hardware FPU), and (3) replace the byte-by-byte `esp_rom_uart_tx_one_char()` transmit loop with the ESP-IDF `uart_write_bytes()` driver API.

Research (3 parallel Explore agents) found:
- **UART0 is shared** by console stdout, the custom non-blocking stdin reader that waits for the literal `"train"` command (`csi_train_cmd_task`, same file — this is the calibration handshake the whole `csi_recv_calibrate` variant exists for), and CSI frame TX. No UART driver is currently installed anywhere in the repo. Installing one (required for `uart_write_bytes()`) risks breaking the `train` stdin path and needs physical hardware validation that isn't possible in this session. **Change 3 is explicitly out of scope for this pass**, per user decision.
- ESP32-C5 is RV32IMC — confirmed no hardware FPU (`sdkconfig`: `CONFIG_COMPILER_FLOAT_LIB_FROM_RVFPLIB=y`), so the float-loop concern (change 2) is real. However, measured against the (unchanged) UART byte-loop, the float loop is only ~0.3ms/packet vs. ~7.16ms/packet for UART TX at 921600 baud — the UART loop is the actual dominant cost, not the float loop, contrary to the original review's severity ranking. The user is aware and has chosen not to touch UART TX in this pass; a worker-task/queue offload to unblock the WiFi callback from that 7ms blocking wait is documented here as **future work**, not implemented now.
- `wifi_csi_rx_cb` already relies on unlocked function-local `static` variables (`s_count`, `agc_gain`, `fft_gain`), confirming ESP-IDF invokes this callback serially from one internal task — making `frame_buf` static is safe by the same argument.
- The frame-serialization code is byte-for-byte duplicated in `csi_recv/main/app_main.c`. **Not touched this pass** — user chose to scope this to `csi_recv_calibrate` only.

## Scope

File: `csi_recv_calibrate/main/app_main.c`, function `csi_send_binary_frame()` (~lines 156–237) and the constant block after `CSI_FRAME_MAX_PAYLOAD` (~line 102). Two changes only.

## Change 1 — `frame_buf`: static, drop redundant zero-init

Replace:
```c
uint8_t frame_buf[sizeof(csi_binary_frame_header_t) + CSI_FRAME_MAX_PAYLOAD + sizeof(uint16_t)] = {0};
```
with a function-local `static` array **without** an initializer (moves ~660B from stack to `.bss`; also eliminates a full-buffer memset that ran every packet for no reason — every transmitted byte is already unconditionally rewritten below).

**Keep** `csi_binary_frame_header_t header = {0};` as-is — `header.reserved` is never explicitly assigned anywhere in the function, so this initializer is the only thing zeroing it before it's memcpy'd into `frame_buf` and sent over the wire. Dropping it would leak uninitialized stack bytes.

Safety argument (only `[0, header.frame_len)` is ever transmitted via `csi_uart_write_raw(frame_buf, header.frame_len)`, and that whole range is rewritten every call — header memcpy, payload loop, checksum memcpy — regardless of what a previous, possibly-larger packet left at higher offsets) applies even though `payload_len` legitimately varies call to call (depends on CSI acquisition mode/bandwidth, not always the 612 cap).

## Change 2 — fixed-point gain compensation

Add near the other `CSI_FRAME_*` constants:
```c
#define CSI_GAIN_FRAC_BITS 8
#define CSI_GAIN_SCALE (1 << CSI_GAIN_FRAC_BITS)  /* 256 */
```

Replace the per-subcarrier loop body. Current:
```c
scaled = (int16_t)(compensate_gain * info->buf[i]);
```
New: compute `gain_q` **once per packet** (not per subcarrier), then per-subcarrier use integer multiply + truncating divide:
```c
int32_t gain_q = (int32_t)(compensate_gain * CSI_GAIN_SCALE +
                            (compensate_gain >= 0.0f ? 0.5f : -0.5f)); /* round-to-nearest */
...
for (uint16_t i = 0; i < payload_len; i++) {
    scaled = ((int32_t)info->buf[i] * gain_q) / CSI_GAIN_SCALE; /* '/' truncates toward zero,
                                                                     matching the original cast */
    if (scaled > 127) scaled = 127;
    else if (scaled < -128) scaled = -128;
    frame_buf[sizeof(csi_binary_frame_header_t) + i] = (uint8_t)((int8_t)scaled);
}
```
Widen `scaled` from `int16_t` to `int32_t` (declared near the top of the function, matching current style) so the intermediate product can't wrap before the clamp runs.

**Must use `/CSI_GAIN_SCALE` (division), not `>>CSI_GAIN_FRAC_BITS`** — C integer division truncates toward zero (matches the original float cast's truncation direction), while `>>` on a signed value floors, which would silently shift negative-sample results down by up to 1 count. Since `CSI_GAIN_SCALE` is a compile-time power-of-two constant, the compiler still emits an efficient shift+correction sequence for the `/`, so there's no performance cost to getting this right.

Add a cheap one-time-per-packet clamp on `compensate_gain` before computing `gain_q`, guarding against `esp_csi_gain_ctrl_get_gain_compensation()` (precompiled component, undocumented output range) ever returning something large enough to overflow the `float`→`int32_t` cast:
```c
if (compensate_gain > 512.0f) compensate_gain = 512.0f;
else if (compensate_gain < -512.0f) compensate_gain = -512.0f;
```
512 is ~2.7 orders of magnitude past the expected order-1 gain value, so it's a no-op in normal operation. (This isn't a new risk — the same unguarded cast already exists in the current code — just cheap hardening while touching this line.)

Rationale for scale=256: resolution 1/256 (~0.4%) is finer than the int8 sample's own precision (~0.8%/LSB), so no additional visible error is introduced beyond what clamping to int8 already loses.

## Explicitly out of scope (do not implement)

- Any change to `csi_uart_write_raw()` / the UART transmission mechanism (stays `esp_rom_uart_tx_one_char()`).
- `uart_driver_install()` / `uart_write_bytes()`.
- Worker-task + queue offload of TX from the WiFi callback (legitimate future idea to address the real ~7ms/packet bottleneck, but needs its own buffer-pool/backpressure design and hardware validation — not this pass).
- Any edit to `csi_recv/main/app_main.c` (byte-for-byte duplicate of this code, intentionally left unmirrored per user decision).

## Verification

1. `idf.py build` from `csi_recv_calibrate/` (ESP-IDF v5.5.3 env per existing `build/` dir) — expect a clean build, no new warnings around the widened `int32_t scaled` or the fixed-point arithmetic.
2. Flash + hardware smoke test: send `train` over serial, confirm the AGC window still completes and streaming resumes (confirms change 1's static buffer didn't disturb anything, and that out-of-scope UART/stdin path is untouched).
3. Use `tools/stride/main.py`'s parser against the live UART port: confirm frames keep parsing with checksum/`frame_len` validation passing, no increase in corrupt/dropped frames.
4. Compare sample magnitudes against a pre-change capture from the same physical setup — should match within ~1 count given fixed-point resolution is finer than the int8 sample LSB.
5. Confirm untouched: `csi_uart_write_raw()` body, no driver install calls, `csi_recv/main/app_main.c` unmodified.
