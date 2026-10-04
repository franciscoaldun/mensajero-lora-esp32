// Achica una foto JPEG a una miniatura RGB565 para mostrarla al lado del texto en la pantallita.
#include <stdlib.h>
#include "miniatura.h"
#include "jpeg_decoder.h"
#include "esp_heap_caps.h"

uint16_t *miniatura_hacer(const uint8_t *jpg, size_t largo, int alto, int ancho_max, int *w, int *h)
{
    esp_jpeg_image_cfg_t c = {.indata = (uint8_t *)jpg, .indata_size = largo, .out_format = JPEG_IMAGE_FORMAT_RGB565};
    esp_jpeg_image_output_t info;
    if (esp_jpeg_get_image_info(&c, &info) != ESP_OK || !info.width || !info.height) return NULL;
    // la escala más chica que siga dejando la imagen más alta que la miniatura
    esp_jpeg_image_scale_t esc = JPEG_IMAGE_SCALE_0;
    int div = 1;
    while (esc < JPEG_IMAGE_SCALE_1_8 && info.height / (div * 2) >= alto) {
        esc++;
        div *= 2;
    }
    int dw = (info.width + div - 1) / div, dh = (info.height + div - 1) / div;
    uint16_t *dec = heap_caps_malloc(dw * dh * 2 + 64, MALLOC_CAP_SPIRAM);
    if (!dec) return NULL;
    c.outbuf = (uint8_t *)dec;
    c.outbuf_size = dw * dh * 2 + 64;
    c.out_scale = esc;
    esp_jpeg_image_output_t sal;
    if (esp_jpeg_decode(&c, &sal) != ESP_OK) {
        free(dec);
        return NULL;
    }
    int mh = alto, mw = sal.width * alto / sal.height;
    if (mw > ancho_max) {
        mw = ancho_max;
        mh = sal.height * ancho_max / sal.width;
    }
    if (mw < 1 || mh < 1) {
        free(dec);
        return NULL;
    }
    uint16_t *mini = heap_caps_malloc(mw * mh * 2, MALLOC_CAP_SPIRAM);
    if (mini) {
        for (int y = 0; y < mh; y++)
            for (int x = 0; x < mw; x++)
                mini[y * mw + x] = dec[(y * sal.height / mh) * sal.width + x * sal.width / mw];
    }
    free(dec);
    *w = mw;
    *h = mh;
    return mini;
}
