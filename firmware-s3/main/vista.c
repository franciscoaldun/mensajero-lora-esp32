// Qué se dibuja en la pantallita. Sin nada de hardware: se compila igual en el PC (host_test).
//   arriba:  aviso o "♥ nuevo · hace 3 min"            ·  💙 -85 ▂▄▆█ (o "sin señal")
//   centro:  el último mensaje de él, en rosado (o su foto + texto)
//   abajo:   ♥ oído hace 20 s · ~1,2 km · tx 22 dBm        14:32
#include <stdio.h>
#include <string.h>
#include "vista.h"

#define MS_PAGINA 5000
#define COLOR_MENSAJE RGB(255, 190, 222)  // rosado pastel
#define COLOR_CIELO RGB(150, 214, 255)    // celeste Cinnamoroll
#define COLOR_PANEL RGB(30, 34, 74)
#define COLOR_AMBAR RGB(255, 200, 90)
#define COLOR_LILA RGB(196, 170, 255)
#define COLOR_SUAVE RGB(206, 210, 236)
#define COLOR_APAGADO RGB(60, 55, 72)

static int nivel_senal(int rssi) { return rssi > 0 ? 0 : rssi > -90 ? 4 : rssi > -105 ? 3 : rssi > -115 ? 2 : rssi > -125 ? 1 : 0; }

static uint16_t color_senal(int n) { return n >= 4 ? COLOR_BIEN : n == 3 ? RGB(190, 255, 90) : n == 2 ? COLOR_AMBAR : COLOR_MAL; }

static void dibujar_barras(int x, int y, int rssi, bool perdido)
{
    int n = perdido ? 1 : nivel_senal(rssi);
    uint16_t c = perdido ? COLOR_MAL : color_senal(n);
    for (int i = 0; i < 4; i++) {
        int h = 3 + i * 2;
        lienzo_rect(x + i * 4, y + 9 - h, 3, h, i < n ? c : COLOR_APAGADO);
    }
}

// "hace 20 s" · "hace 3 min" · "hace 2 h"
static void hace_segundos(char *s, int t, int64_t desde, int64_t ahora)
{
    int seg = (int)((ahora - desde) / 1000);
    if (seg < 0) seg = 0;
    if (seg < 90) snprintf(s, t, "hace %d s", seg);
    else if (seg < 90 * 60) snprintf(s, t, "hace %d min", (seg + 30) / 60);
    else if (seg < 48 * 3600) snprintf(s, t, "hace %d h", seg / 3600);
    else snprintf(s, t, "hace %d días", seg / 86400);
}

static void barra_de_arriba(const vista_t *v, int64_t t)
{
    char s[64];
    if (v->aviso) {
        lienzo_texto(4, 0, &fuente_chica, v->color_aviso, v->aviso, strlen(v->aviso));
    } else if (v->cantidad) {  // cuánto hace que llegó, con segundos (igual que el latido de abajo)
        if (v->llegada_ms) {
            char h[24];
            hace_segundos(h, sizeof(h), v->llegada_ms, t);
            snprintf(s, sizeof(s), "lleg\xC3\xB3 %s", h);
        } else {
            snprintf(s, sizeof(s), "guardado");
        }
        int x = 4;
        if (v->sin_leer && v->viendo == 0) {  // "♥ nuevo" parpadea hasta que ella lo lea
            bool encendido = (t / 600) % 2 == 0;
            x += lienzo_texto(x, 0, &fuente_chica, encendido ? COLOR_ACENTO : COLOR_APAGADO, "\xE2\x99\xA5 nuevo  ", 11);
        }
        lienzo_texto(x, 0, &fuente_chica, COLOR_TENUE, s, strlen(s));
    }
    if (v->presencia == 2) {  // sin señal de él: se dice claro, sin números viejos
        const char *ss = "sin se\xC3\xB1" "al";
        int w = medir_texto(&fuente_chica, ss, strlen(ss));
        lienzo_texto(ANCHO - 21 - w, 0, &fuente_chica, COLOR_MAL, ss, strlen(ss));
        dibujar_barras(ANCHO - 17, 3, 0, true);
        return;
    }
    int x_der = ANCHO - 21;
    if (v->rssi <= 0) {
        snprintf(s, sizeof(s), "%d dBm", v->rssi);
        int w = medir_texto(&fuente_chica, s, strlen(s));
        x_der -= w;
        lienzo_texto(x_der, 0, &fuente_chica, color_senal(nivel_senal(v->rssi)), s, strlen(s));
    }
    if (v->presencia == 1) {  // corazón celeste: él está al alcance
        const char *c = "\xF0\x9F\x92\x99";
        int w = medir_texto(&fuente_chica, c, strlen(c));
        lienzo_texto(x_der - w - 3, 0, &fuente_chica, COLOR_TEXTO, c, strlen(c));
    }
    dibujar_barras(ANCHO - 17, 3, v->rssi, false);
}

// Abajo: ¿se escuchan? hace cuánto, a qué distancia, con qué potencia, y la hora
static void barra_de_abajo(const vista_t *v, int64_t t)
{
    const int y = 59;
    lienzo_rect(0, 60, ANCHO, ALTO - 60, COLOR_PANEL);
    int x = 4;
    // el corazón late: rosado fuerte apenas se oye algo de él; después, el color dice cómo están
    uint16_t c_corazon;
    bool fresco = v->oido_ms && t - v->oido_ms < (int64_t)v->periodo_latido_s * 1000 + 30000;
    if (v->oido_ms && t - v->oido_ms < 2000) c_corazon = (t / 250) % 2 ? COLOR_ACENTO : COLOR_MENSAJE;
    else if (v->presencia == 1) c_corazon = fresco ? COLOR_BIEN : COLOR_AMBAR;
    else if (v->presencia == 2) c_corazon = COLOR_MAL;
    else c_corazon = COLOR_TENUE;
    x += lienzo_texto(x, y, &fuente_chica, c_corazon, "\xE2\x99\xA5", 3);
    if (v->latido_mandado_ms && t - v->latido_mandado_ms < 1500)  // acaba de mandar su latido
        x += lienzo_texto(x, y, &fuente_chica, COLOR_CIELO, "\xC2\xBB", 2);
    x += 3;
    char s[48];
    if (v->oido_ms) {
        char h[24];
        hace_segundos(h, sizeof(h), v->oido_ms, t);
        snprintf(s, sizeof(s), "o\xC3\xAD" "do %s", h);
    } else {
        snprintf(s, sizeof(s), "busc\xC3\xA1ndolo\xE2\x80\xA6");
    }
    x += lienzo_texto(x, y, &fuente_chica, v->presencia == 2 ? COLOR_MAL : COLOR_SUAVE, s, strlen(s));

    int w_hora = v->hora ? medir_texto(&fuente_chica, v->hora, strlen(v->hora)) : 0;
    int limite = ANCHO - 4 - w_hora - 6;
    const char *punto = " \xC2\xB7 ";
    int w_punto = medir_texto(&fuente_chica, punto, strlen(punto));
    if (v->presencia == 1 && v->distancia) {
        int w = medir_texto(&fuente_chica, v->distancia, strlen(v->distancia));
        if (x + w_punto + w <= limite) {
            x += lienzo_texto(x, y, &fuente_chica, COLOR_TENUE, punto, strlen(punto));
            x += lienzo_texto(x, y, &fuente_chica, COLOR_CIELO, v->distancia, strlen(v->distancia));
        }
    }
    snprintf(s, sizeof(s), "tx %d dBm", v->potencia);
    int w = medir_texto(&fuente_chica, s, strlen(s));
    if (x + w_punto + w <= limite) {
        x += lienzo_texto(x, y, &fuente_chica, COLOR_TENUE, punto, strlen(punto));
        x += lienzo_texto(x, y, &fuente_chica, COLOR_LILA, s, strlen(s));
    }
    if (v->hora) lienzo_texto(ANCHO - 4 - w_hora, y, &fuente_chica, COLOR_TENUE, v->hora, strlen(v->hora));
}

void vista_dibujar(const vista_t *v)
{
    int64_t t = v->ahora_ms;
    lienzo_limpiar(COLOR_FONDO);
    barra_de_arriba(v, t);

    // el mensaje (entre las dos barras)
    const int arriba = 15, alto = 45;
    int margen = 6, ancho = ANCHO - 2 * margen;
    if (v->miniatura) {  // foto a la izquierda; el texto se corre
        int mh = v->mini_h > alto ? alto : v->mini_h;
        int my = arriba + (alto - mh) / 2;
        lienzo_imagen(4, my, v->mini_w, mh, v->miniatura);
        margen = v->mini_w + 10;
        ancho = ANCHO - margen - 6;
    }
    const char *txt = v->texto ? v->texto : "esperando mensajes \xE2\x99\xA5";
    uint16_t color = v->texto ? COLOR_MENSAJE : COLOR_CIELO;
    struct { const fuente_t *f; int paso, lineas; } opciones[] = {
        {&fuente_grande, 29, 1}, {&fuente_media, 20, 2}, {&fuente_chica, 14, 3},
    };
    int ini[24], largo[24], n = 0, k;
    for (k = 0; k < 3; k++) {
        n = partir_lineas(opciones[k].f, txt, ancho, ini, largo, 24);
        if (n <= opciones[k].lineas) break;
    }
    if (k == 3) k = 1;  // no cabe ni en chica: páginas en letra media, que se lee mejor
    n = partir_lineas(opciones[k].f, txt, ancho, ini, largo, 24);
    const fuente_t *f = opciones[k].f;
    int por_pag = opciones[k].lineas, paginas = (n + por_pag - 1) / por_pag;
    if (paginas > 1 && t - *v->inicio_pagina > MS_PAGINA) {
        *v->pagina = (*v->pagina + 1) % paginas;
        *v->inicio_pagina = t;
    }
    if (*v->pagina >= paginas) *v->pagina = 0;
    int pagina = *v->pagina;
    int desde = pagina * por_pag, hasta = desde + por_pag < n ? desde + por_pag : n;
    int usadas = hasta - desde;
    int y0 = arriba + (alto - usadas * opciones[k].paso) / 2 - (f->alto_linea - opciones[k].paso) / 2;
    for (int i = desde; i < hasta; i++) {
        int w = medir_texto(f, txt + ini[i], largo[i]);
        int x = usadas == 1 && paginas == 1 ? margen + (ancho - w) / 2 : margen;  // una sola línea: centrada
        lienzo_texto(x, y0 + (i - desde) * opciones[k].paso, f, color, txt + ini[i], largo[i]);
    }
    if (paginas > 1) {  // puntitos de página
        for (int i = 0; i < paginas; i++)
            lienzo_rect(ANCHO - 6, arriba + 4 + i * 6, 3, 3, i == pagina ? COLOR_ACENTO : COLOR_APAGADO);
    }
    barra_de_abajo(v, t);
}
