#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "pantalla.h"

typedef struct {
    const char *texto;      // NULL = todavía no llega ninguno
    int64_t llegada_ms;     // 0 = guardado de antes de reiniciar
    int cantidad, viendo;
    bool sin_leer;
    int rssi;               // > 0 = sin datos
    const char *aviso;      // NULL = sin aviso
    uint16_t color_aviso;
    int64_t ahora_ms;
    int presencia;          // 0 = todavía no se sabe · 1 = él está cerca · 2 = se perdió la señal
    const uint16_t *miniatura;  // foto (RGB565) a la izquierda del texto, o NULL
    int mini_w, mini_h;
    int *pagina;            // la vista avanza las páginas sola
    int64_t *inicio_pagina;
    // línea de abajo: ¿se escuchan?
    int64_t oido_ms;            // última vez que se oyó algo de él (0 = nunca)
    int64_t latido_mandado_ms;  // último latido que mandó este aparato (0 = ninguno)
    int periodo_latido_s;       // cada cuánto se dan la mano cuando están cerca
    const char *distancia;      // "~1,2 km" o NULL
    int potencia;               // dBm con que transmite este aparato
    const char *hora;           // "14:32" o NULL
    int celulares, pendientes, nivel;  // celulares conectados al Wi-Fi, mensajes sin entregar, nivel de radio
} vista_t;

void vista_dibujar(const vista_t *v);
