#pragma once

#include "sdkconfig.h"

#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
#include <stdbool.h>
#include <stdint.h>
#include "driver/spi_slave.h"
#include "esp_err.h"

/* Single writer owns the descriptor and context until get_trans_result returns.
 * No CSI bytes, PSK, allocation, logging or GPIO writes in the ISR callbacks. */
void wg_spi_trace_begin(uint64_t epoch, uint64_t seq);
void *wg_spi_trace_context(void);
void wg_spi_trace_queued(esp_err_t result);
void wg_spi_trace_ready(bool high);
void wg_spi_trace_returned(void);
void wg_spi_trace_result(esp_err_t result, bool pointer_match, size_t bits, bool exact);
void wg_spi_trace_freed(const uint32_t counters[4]);
void wg_spi_trace_post_setup(spi_slave_transaction_t *transaction);
void wg_spi_trace_post_trans(spi_slave_transaction_t *transaction);
void wg_spi_trace_log(void);
#endif
