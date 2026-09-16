#include "wg_spi_trace.h"

#ifdef CONFIG_WIFI_GUARD_SPI_COMPLETION_TRACE
#include <limits.h>
#include <string.h>
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

#define TRACE_LIMIT 64U
#define TRACE_PRINT_BATCH 8U
#define TRACE_EXPECTED_BITS ((uint32_t)CONFIG_WIFI_GUARD_SPI_BATCH_FRAMES * 576U * 8U)

typedef struct {
    uint32_t id;
    uint64_t epoch;
    uint64_t seq;
    int64_t begin_us;
    int64_t queued_us;
    int64_t ready_high_us;
    int64_t result_us;
    int64_t ready_low_us;
    int64_t freed_us;
    /* Written only in SPI ISR, read by writer only AFTER get_trans_result.
     * The logger reads immutable copies, never these live ISR fields. */
    volatile int64_t setup_us;
    volatile int64_t done_us;
    volatile uint32_t setup_calls;
    volatile uint32_t done_calls;
    volatile uint32_t done_bits;
    int queue_rc;
    int result_rc;
    uint32_t result_bits;
    bool pointer_match;
    bool exact;
    uint32_t counters[4];
} trace_record_t;

static const char *TAG = "wg_spi_trace";
static trace_record_t s_current;
/* Append-only first-N capture: no rollover and no ISR queue/logging overhead. */
static trace_record_t s_records[TRACE_LIMIT];
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_started;
static uint32_t s_freed;
static uint32_t s_exact;
static uint32_t s_short;
static uint32_t s_other;
static uint32_t s_setup_calls;
static uint32_t s_done_calls;
static uint32_t s_stored;
static uint32_t s_emitted;
static uint32_t s_omitted;
static uint32_t s_active_id;
static uint64_t s_active_seq;

static void increment(uint32_t *value)
{
    if (*value != UINT32_MAX) {
        ++*value;
    }
}

static void add_saturating(uint32_t *value, uint32_t amount)
{
    *value = UINT32_MAX - *value < amount ? UINT32_MAX : *value + amount;
}

void wg_spi_trace_begin(uint64_t epoch, uint64_t seq)
{
    memset(&s_current, 0, sizeof(s_current));
    s_current.epoch = epoch;
    s_current.seq = seq;
    s_current.begin_us = esp_timer_get_time();
    s_current.result_rc = -1; /* get_trans_result not called yet */
    s_current.result_bits = UINT32_MAX;
    portENTER_CRITICAL(&s_lock);
    increment(&s_started);
    s_current.id = s_started;
    s_active_id = s_started;
    s_active_seq = seq;
    portEXIT_CRITICAL(&s_lock);
}

void *wg_spi_trace_context(void)
{
    return &s_current;
}

void IRAM_ATTR wg_spi_trace_post_setup(spi_slave_transaction_t *transaction)
{
    trace_record_t *record = transaction->user;
    record->setup_us = esp_timer_get_time();
    if (record->setup_calls != UINT32_MAX) {
        ++record->setup_calls;
    }
}

void IRAM_ATTR wg_spi_trace_post_trans(spi_slave_transaction_t *transaction)
{
    trace_record_t *record = transaction->user;
    record->done_us = esp_timer_get_time();
    record->done_bits = (uint32_t)transaction->trans_len;
    if (record->done_calls != UINT32_MAX) {
        ++record->done_calls;
    }
}

void wg_spi_trace_queued(esp_err_t result)
{
    s_current.queued_us = esp_timer_get_time();
    s_current.queue_rc = result;
}

void wg_spi_trace_ready(bool high)
{
    if (high) {
        s_current.ready_high_us = esp_timer_get_time();
    } else {
        s_current.ready_low_us = esp_timer_get_time();
    }
}

void wg_spi_trace_returned(void)
{
    s_current.result_us = esp_timer_get_time();
}

void wg_spi_trace_result(esp_err_t result, bool pointer_match, size_t bits, bool exact)
{
    s_current.result_rc = result;
    s_current.pointer_match = pointer_match;
    s_current.result_bits = (uint32_t)bits;
    s_current.exact = exact;
}

void wg_spi_trace_freed(const uint32_t counters[4])
{
    /* Called AFTER returning the capture frame. Callbacks are finished now. */
    s_current.freed_us = esp_timer_get_time();
    memcpy(s_current.counters, counters, sizeof(s_current.counters));
    portENTER_CRITICAL(&s_lock);
    increment(&s_freed);
    if (s_current.exact) {
        increment(&s_exact);
    } else if (s_current.result_rc == ESP_OK && s_current.pointer_match
               && s_current.result_bits < TRACE_EXPECTED_BITS) {
        increment(&s_short);
    } else {
        increment(&s_other);
    }
    add_saturating(&s_setup_calls, s_current.setup_calls);
    add_saturating(&s_done_calls, s_current.done_calls);
    if (s_stored < TRACE_LIMIT) {
        s_records[s_stored++] = s_current;
    } else {
        increment(&s_omitted);
    }
    s_active_id = 0;
    portEXIT_CRITICAL(&s_lock);
}

void wg_spi_trace_log(void)
{
    for (unsigned index = 0; index < TRACE_PRINT_BATCH; ++index) {
        trace_record_t record;
        portENTER_CRITICAL(&s_lock);
        const bool available = s_emitted < s_stored;
        if (available) {
            record = s_records[s_emitted++];
        }
        portEXIT_CRITICAL(&s_lock);
        if (!available) {
            break;
        }
        ESP_LOGI(TAG,
            "SPI_TRACE v=1 id=%u epoch=%016llx seq=%llu setup=%u done=%u "
            "bits=%u isr_bits=%u qrc=%d rrc=%d ptr=%u exact=%u "
            "begin_us=%lld queued_us=%lld setup_us=%lld high_us=%lld "
            "done_us=%lld result_us=%lld low_us=%lld freed_us=%lld "
            "drops=%u/%u/%u/%u",
            (unsigned)record.id, (unsigned long long)record.epoch,
            (unsigned long long)record.seq, (unsigned)record.setup_calls,
            (unsigned)record.done_calls, (unsigned)record.result_bits,
            (unsigned)record.done_bits, record.queue_rc, record.result_rc,
            (unsigned)record.pointer_match, (unsigned)record.exact,
            (long long)record.begin_us, (long long)record.queued_us,
            (long long)record.setup_us, (long long)record.ready_high_us,
            (long long)record.done_us, (long long)record.result_us,
            (long long)record.ready_low_us, (long long)record.freed_us,
            (unsigned)record.counters[0], (unsigned)record.counters[1],
            (unsigned)record.counters[2], (unsigned)record.counters[3]);
    }
    uint32_t started, freed, exact, short_results, other, setups, dones;
    uint32_t stored, emitted, omitted, active;
    uint64_t active_seq;
    portENTER_CRITICAL(&s_lock);
    started = s_started;
    freed = s_freed;
    exact = s_exact;
    short_results = s_short;
    other = s_other;
    setups = s_setup_calls;
    dones = s_done_calls;
    stored = s_stored;
    emitted = s_emitted;
    omitted = s_omitted;
    active = s_active_id;
    active_seq = s_active_seq;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGI(TAG,
        "SPI_TOTAL v=1 now_us=%lld started=%u freed=%u exact=%u short=%u other=%u "
        "setup_retired=%u done_retired=%u active_id=%u active_seq=%llu "
        "stored=%u emitted=%u omitted=%u limit=%u",
        (long long)esp_timer_get_time(), (unsigned)started, (unsigned)freed,
        (unsigned)exact, (unsigned)short_results, (unsigned)other,
        (unsigned)setups, (unsigned)dones, (unsigned)active,
        (unsigned long long)active_seq, (unsigned)stored, (unsigned)emitted,
        (unsigned)omitted, TRACE_LIMIT);
}
#endif
