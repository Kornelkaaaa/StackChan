/* Audio codec + I2S init. Mirrors the proven setup in
 * ../../../firmware/main/hal/board/cores3_audio_codec.cc, translated to C
 * and stripped to what we need: mono in / mono out, no AEC reference.
 *
 * Header docs explain the topology; this file is the implementation only.
 */
#include "audio.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/i2s_tdm.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_log.h"

#include "board_config.h"

static const char *TAG = "audio";

/* DMA tuning. Same magnitude as the factory firmware — enough buffer to
 * absorb scheduling jitter without adding noticeable latency. */
#define DMA_DESC_NUM   6
#define DMA_FRAME_NUM  240

/* I2C addresses for the two codecs. Defined in esp_codec_dev_defaults.h. */
#define AW88298_ADDR  AW88298_CODEC_DEFAULT_ADDR
#define ES7210_ADDR   ES7210_CODEC_DEFAULT_ADDR

/* Codec needs its own I2C bus — separate from the system one (if any).
 * We use I2C port 1; port 0 is reserved for future peripherals (IMU, etc.). */
#define CODEC_I2C_PORT  I2C_NUM_1

static i2s_chan_handle_t tx_handle = NULL;
static i2s_chan_handle_t rx_handle = NULL;

static i2c_master_bus_handle_t i2c_bus = NULL;

static const audio_codec_data_if_t *data_if = NULL;
static const audio_codec_ctrl_if_t *out_ctrl_if = NULL;
static const audio_codec_ctrl_if_t *in_ctrl_if = NULL;
static const audio_codec_if_t      *out_codec_if = NULL;
static const audio_codec_if_t      *in_codec_if = NULL;
static const audio_codec_gpio_if_t *gpio_if = NULL;

static esp_codec_dev_handle_t output_dev = NULL;
static esp_codec_dev_handle_t input_dev = NULL;

static bool mic_open = false;
static bool spk_open = false;


static esp_err_t init_i2c(void) {
    i2c_master_bus_config_t cfg = {
        .clk_source       = I2C_CLK_SRC_DEFAULT,
        .i2c_port         = CODEC_I2C_PORT,
        .sda_io_num       = AUDIO_CODEC_I2C_SDA_PIN,
        .scl_io_num       = AUDIO_CODEC_I2C_SCL_PIN,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    return i2c_new_master_bus(&cfg, &i2c_bus);
}

static esp_err_t init_i2s(void) {
    /* One I2S peripheral, two channels (TX + RX). Both run as master at
     * AUDIO_SAMPLE_RATE_HZ. */
    i2s_chan_config_t chan_cfg = {
        .id                  = I2S_NUM_0,
        .role                = I2S_ROLE_MASTER,
        .dma_desc_num        = DMA_DESC_NUM,
        .dma_frame_num       = DMA_FRAME_NUM,
        .auto_clear_after_cb = true,
        .auto_clear_before_cb = false,
        .intr_priority       = 0,
    };
    esp_err_t err = i2s_new_channel(&chan_cfg, &tx_handle, &rx_handle);
    if (err != ESP_OK) return err;

    /* TX: standard I2S, drives the AW88298 amp. Stereo slot config so the
     * codec's frame layout matches — we'll only use one channel of output
     * via the codec-dev layer below. */
    i2s_std_config_t tx_cfg = {
        .clk_cfg = {
            .sample_rate_hz = AUDIO_SAMPLE_RATE_HZ,
            .clk_src        = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple  = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode      = I2S_SLOT_MODE_STEREO,
            .slot_mask      = I2S_STD_SLOT_BOTH,
            .ws_width       = I2S_DATA_BIT_WIDTH_16BIT,
            .ws_pol         = false,
            .bit_shift      = true,
            .left_align     = true,
            .big_endian     = false,
            .bit_order_lsb  = false,
        },
        .gpio_cfg = {
            .mclk = AUDIO_I2S_GPIO_MCLK,
            .bclk = AUDIO_I2S_GPIO_BCLK,
            .ws   = AUDIO_I2S_GPIO_WS,
            .dout = AUDIO_I2S_GPIO_DOUT,
            .din  = I2S_GPIO_UNUSED,
        },
    };
    err = i2s_channel_init_std_mode(tx_handle, &tx_cfg);
    if (err != ESP_OK) return err;

    /* RX: TDM mode, four-slot — required by the ES7210 ADC. The codec-dev
     * layer hides the slot multiplexing; we just see 16-bit samples. */
    i2s_tdm_config_t rx_cfg = {
        .clk_cfg = {
            .sample_rate_hz = AUDIO_SAMPLE_RATE_HZ,
            .clk_src        = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple  = I2S_MCLK_MULTIPLE_256,
            .bclk_div       = 8,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode      = I2S_SLOT_MODE_STEREO,
            .slot_mask      = I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3,
            .ws_width       = I2S_TDM_AUTO_WS_WIDTH,
            .ws_pol         = false,
            .bit_shift      = true,
            .left_align     = false,
            .big_endian     = false,
            .bit_order_lsb  = false,
            .skip_mask      = false,
            .total_slot     = I2S_TDM_AUTO_SLOT_NUM,
        },
        .gpio_cfg = {
            .mclk = AUDIO_I2S_GPIO_MCLK,
            .bclk = AUDIO_I2S_GPIO_BCLK,
            .ws   = AUDIO_I2S_GPIO_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = AUDIO_I2S_GPIO_DIN,
        },
    };
    err = i2s_channel_init_tdm_mode(rx_handle, &rx_cfg);
    if (err != ESP_OK) return err;

    err = i2s_channel_enable(tx_handle);
    if (err != ESP_OK) return err;
    err = i2s_channel_enable(rx_handle);
    return err;
}

static esp_err_t init_codecs(void) {
    audio_codec_i2s_cfg_t i2s_cfg = {
        .port       = I2S_NUM_0,
        .rx_handle  = rx_handle,
        .tx_handle  = tx_handle,
    };
    data_if = audio_codec_new_i2s_data(&i2s_cfg);
    if (!data_if) return ESP_FAIL;

    gpio_if = audio_codec_new_gpio();
    if (!gpio_if) return ESP_FAIL;

    /* Output: AW88298 amplifier. */
    audio_codec_i2c_cfg_t out_i2c = {
        .port       = CODEC_I2C_PORT,
        .addr       = AW88298_ADDR,
        .bus_handle = i2c_bus,
    };
    out_ctrl_if = audio_codec_new_i2c_ctrl(&out_i2c);
    if (!out_ctrl_if) return ESP_FAIL;

    aw88298_codec_cfg_t aw_cfg = {
        .ctrl_if   = out_ctrl_if,
        .gpio_if   = gpio_if,
        .reset_pin = GPIO_NUM_NC,
        .hw_gain = {
            .pa_voltage        = 5.0,
            .codec_dac_voltage = 3.3,
            .pa_gain           = 1,
        },
    };
    out_codec_if = aw88298_codec_new(&aw_cfg);
    if (!out_codec_if) return ESP_FAIL;

    esp_codec_dev_cfg_t out_dev = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = out_codec_if,
        .data_if  = data_if,
    };
    output_dev = esp_codec_dev_new(&out_dev);
    if (!output_dev) return ESP_FAIL;

    /* Input: ES7210 ADC, three mics selected by hardware, codec-dev
     * collapses them to a single channel for us. */
    audio_codec_i2c_cfg_t in_i2c = {
        .port       = CODEC_I2C_PORT,
        .addr       = ES7210_ADDR,
        .bus_handle = i2c_bus,
    };
    in_ctrl_if = audio_codec_new_i2c_ctrl(&in_i2c);
    if (!in_ctrl_if) return ESP_FAIL;

    es7210_codec_cfg_t es_cfg = {
        .ctrl_if      = in_ctrl_if,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3,
    };
    in_codec_if = es7210_codec_new(&es_cfg);
    if (!in_codec_if) return ESP_FAIL;

    esp_codec_dev_cfg_t in_dev = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = in_codec_if,
        .data_if  = data_if,
    };
    input_dev = esp_codec_dev_new(&in_dev);
    if (!input_dev) return ESP_FAIL;

    return ESP_OK;
}

esp_err_t audio_init(void) {
    ESP_LOGI(TAG, "init i2c");
    esp_err_t err = init_i2c();
    if (err != ESP_OK) { ESP_LOGE(TAG, "i2c init failed: %d", err); return err; }

    ESP_LOGI(TAG, "init i2s (mclk=%d bclk=%d ws=%d din=%d dout=%d)",
             AUDIO_I2S_GPIO_MCLK, AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS,
             AUDIO_I2S_GPIO_DIN, AUDIO_I2S_GPIO_DOUT);
    err = init_i2s();
    if (err != ESP_OK) { ESP_LOGE(TAG, "i2s init failed: %d", err); return err; }

    ESP_LOGI(TAG, "init codecs");
    err = init_codecs();
    if (err != ESP_OK) { ESP_LOGE(TAG, "codec init failed: %d", err); return err; }

    ESP_LOGI(TAG, "audio_init done sr=%d", AUDIO_SAMPLE_RATE_HZ);
    return ESP_OK;
}

esp_err_t audio_enable_mic(bool enable) {
    if (enable == mic_open) return ESP_OK;
    if (enable) {
        /* The ES7210 surfaces as a stereo (2-channel) stream over I2S even
         * though we only care about mic1; the channel_mask asks codec-dev
         * to hand us just slot 0. */
        esp_codec_dev_sample_info_t fs = {
            .bits_per_sample = 16,
            .channel         = 2,
            .channel_mask    = ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0),
            .sample_rate     = AUDIO_SAMPLE_RATE_HZ,
            .mclk_multiple   = 0,
        };
        esp_err_t err = esp_codec_dev_open(input_dev, &fs);
        if (err != ESP_OK) return err;
        /* 60 ≈ midrange mic gain. Tune later if speech is too quiet/loud. */
        esp_codec_dev_set_in_channel_gain(input_dev, ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0), 60);
        mic_open = true;
    } else {
        esp_codec_dev_close(input_dev);
        mic_open = false;
    }
    return ESP_OK;
}

esp_err_t audio_enable_speaker(bool enable) {
    if (enable == spk_open) return ESP_OK;
    if (enable) {
        esp_codec_dev_sample_info_t fs = {
            .bits_per_sample = 16,
            .channel         = 1,
            .channel_mask    = 0,
            .sample_rate     = AUDIO_SAMPLE_RATE_HZ,
            .mclk_multiple   = 0,
        };
        esp_err_t err = esp_codec_dev_open(output_dev, &fs);
        if (err != ESP_OK) return err;
        esp_codec_dev_set_out_vol(output_dev, 70);  /* 0–100; 70 ≈ comfortable */
        spk_open = true;
    } else {
        esp_codec_dev_close(output_dev);
        spk_open = false;
    }
    return ESP_OK;
}

size_t audio_read_mic(int16_t *out, size_t samples) {
    if (!mic_open) return 0;
    esp_err_t err = esp_codec_dev_read(input_dev, (void *)out, samples * sizeof(int16_t));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "mic read err %d", err);
        return 0;
    }
    return samples;
}

size_t audio_write_speaker(const int16_t *in, size_t samples) {
    if (!spk_open) return 0;
    esp_err_t err = esp_codec_dev_write(output_dev, (void *)in, samples * sizeof(int16_t));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "spk write err %d", err);
        return 0;
    }
    return samples;
}
