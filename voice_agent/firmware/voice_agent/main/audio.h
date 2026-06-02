/* Audio I/O — codec init + read mic + write speaker.
 *
 * Mono 16-bit PCM at AUDIO_SAMPLE_RATE_HZ (24 kHz on this board).
 * All sample counts in this API are SAMPLES, not bytes.
 *
 * Codec topology (verified against ../../../firmware/main/hal/board/cores3_audio_codec.cc):
 *   I2S TX channel — STD mode — drives AW88298 amplifier → speaker
 *   I2S RX channel — TDM mode — pulls from ES7210 ADC → microphone
 *   Both channels share BCLK / WS / MCLK pins (see board_config.h).
 *   I2C bus 1 carries control to both codecs (AW88298 + ES7210 addresses).
 *
 * Lifetime: call `audio_init()` once at boot. Then `audio_enable_mic(true)`
 * / `audio_enable_speaker(true)` to open the codecs (each opens a DMA
 * channel), and `false` to close. You can flip these many times.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t audio_init(void);

esp_err_t audio_enable_mic(bool enable);
esp_err_t audio_enable_speaker(bool enable);

/* Blocks until `samples` int16s are read. Returns the number of samples
 * actually written into `out` (== `samples` on success). */
size_t audio_read_mic(int16_t *out, size_t samples);

/* Blocks until all `samples` are queued to the DMA. Returns samples written. */
size_t audio_write_speaker(const int16_t *in, size_t samples);

#ifdef __cplusplus
}
#endif
