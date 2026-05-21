/* Pin map for the official M5Stack StackChan ESP32-S3 board.
 *
 * Verified against firmware/main/hal/board/config.h in this repo (the
 * factory firmware), so these numbers are correct for the actual hardware.
 *
 * Hardware quirk: GPIO 0 is BOTH the BOOT button AND the I2S MCLK pin
 * on this board. Chunk 5a only reads it as a button (audio isn't running
 * yet). Chunk 5b will need to either reconfigure the pin between
 * button/MCLK modes, or use the head-touch sensor (Si12T over I2C) as the
 * session trigger instead.
 */
#pragma once

#include <driver/gpio.h>

#define BOOT_BUTTON_GPIO        GPIO_NUM_0

/* Audio pins (chunk 5b). Listed here so all hardware pin numbers live
 * in one file and the conflict above is obvious in context. */
#define AUDIO_I2S_GPIO_MCLK     GPIO_NUM_0    /* CONFLICTS with BOOT_BUTTON_GPIO */
#define AUDIO_I2S_GPIO_WS       GPIO_NUM_33
#define AUDIO_I2S_GPIO_BCLK     GPIO_NUM_34
#define AUDIO_I2S_GPIO_DIN      GPIO_NUM_14   /* mic data in */
#define AUDIO_I2S_GPIO_DOUT     GPIO_NUM_13   /* speaker data out */

#define AUDIO_CODEC_I2C_SDA_PIN GPIO_NUM_12
#define AUDIO_CODEC_I2C_SCL_PIN GPIO_NUM_11

/* Native audio sample rate of the board's codec (both directions).
 * Gemini Live auto-resamples 24 kHz input to its internal 16 kHz, so we
 * don't need to do any DSP on the device. */
#define AUDIO_SAMPLE_RATE_HZ    24000
