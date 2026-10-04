#pragma once
#include <stdint.h>

typedef struct {
    uint32_t cp;      // código Unicode
    uint32_t pos;     // dónde empiezan sus datos
    uint8_t w, h;
    int8_t x, y;      // desplazamiento desde el origen (y desde la parte de arriba de la línea)
    uint8_t avance;
    uint8_t color;    // 0 = alfa de 4 bits, 1 = RGB565 ya mezclado sobre el fondo
} glifo_t;

typedef struct {
    const glifo_t *glifos;
    uint16_t n;
    const uint8_t *datos;
    uint8_t alto_linea;
    uint8_t ascenso;
} fuente_t;

extern const fuente_t fuente_grande, fuente_media, fuente_chica;
extern const fuente_t fuente_mini;  // Tahoma 10 px sin suavizar: 3 líneas en la OLED de 32 px
