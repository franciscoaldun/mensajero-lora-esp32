#pragma once
#include <stdint.h>
#include <stddef.h>

// Devuelve RGB565 en PSRAM (liberar con free) o NULL. Mantiene la proporción dentro de alto x ancho_max.
uint16_t *miniatura_hacer(const uint8_t *jpg, size_t largo, int alto, int ancho_max, int *w, int *h);
