// Pantallita OLED 0.91" (128x32) del aparato de él, en 3 líneas con letra nítida (Tahoma 10 px, 1 bit):
//   1) ♥ señal 87 · ~1,2 km · 20 s     (o "sin señal hace 5 min" / "buscándola…"; el ♥ late al oírla; NUEVO si hay)
//   2) su último mensaje               (si no cabe, se desplaza despacio de lado)
//   3) 14:32 · tx 22 · ✓ todo entregado   (o un aviso corto por unos segundos)
// El corazón va dibujado a mano (pixel a pixel): el de la letra a 10 px parecía un triángulo. Sin emojis de
// celular ni rayo: a este tamaño se veían enormes y estorbaban (03-oct, él).
#include <stdio.h>
#include <string.h>
#include "vista.h"

#define LETRA (&fuente_mini)
// Medido (Tahoma 10): el texto normal ocupa las filas 2 a 11 de cada línea (tildes y palos de b, d, l desde la
// 2; colas de g, p, y hasta la 11). Con líneas cada 11 px desde -2, el texto llena 0-9, 11-20 y 22-31: toda la
// pantalla, sin cortar colas abajo ni dejar filas vacías arriba.
#define Y1 (-2)
#define Y2 9
#define Y3 20

static void hace(char *s, int t, int64_t desde, int64_t ahora)
{
    int seg = desde ? (int)((ahora - desde) / 1000) : -1;
    if (seg < 0) s[0] = 0;
    else if (seg < 90) snprintf(s, t, "%d s", seg);
    else if (seg < 90 * 60) snprintf(s, t, "%d min", (seg + 30) / 60);
    else snprintf(s, t, "%d h", seg / 3600);
}

// Señal de 0 (casi no se oyen) a 100 (muy cerca): -120 dBm = 0, -30 dBm o más = 100
static int senal_100(int rssi)
{
    int p = (rssi + 120) * 100 / 90;
    return p < 0 ? 0 : p > 100 ? 100 : p;
}

// Corazón a mano: chico (7x6) y, al latir, grande (9x8). Cada fila: desde, hasta (y una segunda parte arriba).
static void corazon(int x, int y, bool grande, uint16_t color)
{
    static const int8_t chico[6][4] = {{1, 2, 4, 5}, {0, 6, -1, 0}, {0, 6, -1, 0}, {1, 5, -1, 0}, {2, 4, -1, 0}, {3, 3, -1, 0}};
    static const int8_t gran[8][4] = {{1, 3, 5, 7}, {0, 8, -1, 0}, {0, 8, -1, 0}, {0, 8, -1, 0},
                                      {1, 7, -1, 0}, {2, 6, -1, 0}, {3, 5, -1, 0}, {4, 4, -1, 0}};
    int filas = grande ? 8 : 6;
    for (int f = 0; f < filas; f++) {
        const int8_t *r = grande ? gran[f] : chico[f];
        lienzo_rect(x + r[0], y + f, r[1] - r[0] + 1, 1, color);
        if (r[2] >= 0) lienzo_rect(x + r[2], y + f, r[3] - r[2] + 1, 1, color);
    }
}

// Una línea: si cabe, quieta; si no, se desplaza despacio (pausa al comienzo y al final)
static void linea(int x0, int y, const char *s, int64_t t)
{
    int n = strlen(s);
    int ancho = ANCHO - x0;
    int w = medir_texto(LETRA, s, n);
    if (w <= ancho) {
        lienzo_texto(x0, y, LETRA, COLOR_TEXTO, s, n);
        return;
    }
    int recorrido = w - ancho + 4;
    int ciclo = 1500 + recorrido * 40 + 1500;  // ~25 px por segundo
    int fase = (int)(t % ciclo);
    int x = fase < 1500 ? 0 : fase < 1500 + recorrido * 40 ? (fase - 1500) / 40 : recorrido;
    lienzo_texto(x0 - x, y, LETRA, COLOR_TEXTO, s, n);
    if (x0 > 0) lienzo_rect(0, y < 0 ? 0 : y, x0, 11, COLOR_FONDO);  // que el texto no pase por encima del corazón
}

void vista_dibujar(const vista_t *v)
{
    int64_t t = v->ahora_ms;
    lienzo_limpiar(COLOR_FONDO);
    char s[128], h[24];

    // 1) ¿se escuchan? El corazón late (grande) un momento cada vez que llega algo de ella.
    bool late = v->oido_ms && t - v->oido_ms < 1200 && (t / 300) % 2 == 0;
    const char *nuevo = v->sin_leer ? "NUEVO \xC2\xB7 " : "";
    if (v->presencia == 1) {
        hace(h, sizeof(h), v->oido_ms, t);
        snprintf(s, sizeof(s), "%sse\xC3\xB1" "al %d \xC2\xB7 %s \xC2\xB7 %s", nuevo, senal_100(v->rssi),
                 v->distancia ? v->distancia : "", h);
    } else if (v->presencia == 2) {
        hace(h, sizeof(h), v->oido_ms, t);
        snprintf(s, sizeof(s), "%ssin se\xC3\xB1" "al hace %s", nuevo, h);
    } else {
        snprintf(s, sizeof(s), "%sbusc\xC3\xA1ndola\xE2\x80\xA6", nuevo);
    }
    linea(12, Y1, s, t);
    if (late) corazon(0, 1, true, COLOR_TEXTO);
    else corazon(1, 2, false, COLOR_TEXTO);

    // 2) su último mensaje
    linea(0, Y2, v->texto ? v->texto : "esperando mensajes", t);

    // 3) aviso del momento, o la hora y cómo va todo
    if (v->aviso) {
        linea(0, Y3, v->aviso, t);
    } else {
        size_t u = 0;
        // sin hora todavía: la pone el celular al abrir la página (o un latido del otro aparato si la sabe)
        u += snprintf(s + u, sizeof(s) - u, "%s \xC2\xB7 tx %d", v->hora ? v->hora : "--:--", v->potencia);
        if (v->pendientes > 0) snprintf(s + u, sizeof(s) - u, " \xC2\xB7 %d sin entregar", v->pendientes);
        else snprintf(s + u, sizeof(s) - u, " \xC2\xB7 \xE2\x9C\x93 todo entregado");
        linea(0, Y3, s, t);
    }
}
