#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "fuentes.h"

#define ANCHO 284
#define ALTO 76

// RGB565 normal; pantalla.c lo da vuelta al dibujar
#define RGB(r, g, b) ((uint16_t)((((r) >> 3) << 11) | (((g) >> 2) << 5) | ((b) >> 3)))
#define COLOR_FONDO RGB(14, 16, 38)    // azul noche (igual que FONDO en generar_fuentes.py)
#define COLOR_TEXTO RGB(234, 246, 255) // blanco Cinnamoroll
#define COLOR_TENUE RGB(141, 136, 163)
#define COLOR_ACENTO RGB(255, 156, 194) // rosado pastel (ella)
#define COLOR_BIEN RGB(57, 255, 20)    // verde neón (él)
#define COLOR_MAL RGB(255, 77, 109)
#define COLOR_VIOLETA RGB(0, 183, 255)   // celeste eléctrico (él)

void pantalla_iniciar(void);
void pantalla_brillo(int por_mil, int ms_transicion);  // 0..1000
void pantalla_mostrar(void);                            // manda el lienzo a la pantalla
void pantalla_dar_vuelta(bool invertida);
void pantalla_colores_al_reves(bool al_reves);  // si los colores se ven como negativo (fondo blanco)
const uint16_t *pantalla_lienzo(void);                 // lo que se está mostrando (para capturas)              // 180°

void lienzo_limpiar(uint16_t color);
void lienzo_imagen(int x, int y, int w, int h, const uint16_t *rgb565);  // RGB565 normal
void lienzo_rect(int x, int y, int w, int h, uint16_t color);
int lienzo_texto(int x, int y, const fuente_t *f, uint16_t color, const char *s, int bytes);  // devuelve el ancho
int medir_texto(const fuente_t *f, const char *s, int bytes);

// Corta el texto en líneas de máximo `ancho` px, por palabras. Guarda inicio y largo (en bytes) de cada línea.
// Devuelve cuántas líneas necesita (aunque sean más que max_lineas).
int partir_lineas(const fuente_t *f, const char *s, int ancho, int *ini, int *largo, int max_lineas);
