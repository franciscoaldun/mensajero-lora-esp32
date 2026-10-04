// Pantalla OLED SSD1306 de 0.91" (128x32) por I2C. Se dibuja en un lienzo RGB565 igual que en el aparato de ella
// (mismas letras y emojis) y al mostrar se pasa a blanco y negro: un punto se prende si es claro.
#include <string.h>
#include <stdlib.h>
#include "pantalla.h"
#ifndef SIMULADOR
#include "driver/i2c_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_ssd1306.h"
#include "esp_log.h"
#endif

#define PIN_SDA 8
#define PIN_SCL 9
#define DIRECCION 0x3C

#ifdef SIMULADOR
static uint16_t lienzo_sim[ANCHO * ALTO];
static uint16_t *lienzo = lienzo_sim;
const uint16_t *pantalla_lienzo(void) { return lienzo; }
#else
static const char *TAG = "pantalla";
static esp_lcd_panel_handle_t panel;
static esp_lcd_panel_io_handle_t io;
static uint16_t *lienzo;           // ANCHO*ALTO RGB565 (bytes dados vuelta, como en el S3)
static uint8_t paginas[ANCHO * ALTO / 8];  // lo que va a la OLED: 4 páginas de 8 filas
static bool espejo;
const uint16_t *pantalla_lienzo(void) { return lienzo; }
#endif

static inline uint16_t dar_vuelta(uint16_t c) { return (uint16_t)((c << 8) | (c >> 8)); }

#ifndef SIMULADOR
void pantalla_iniciar(void)
{
    i2c_master_bus_handle_t bus;
    const i2c_master_bus_config_t bc = {.clk_source = I2C_CLK_SRC_DEFAULT, .i2c_port = 0, .sda_io_num = PIN_SDA,
                                        .scl_io_num = PIN_SCL, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true};
    // sin pantalla (o mal soldada) el aparato igual funciona: se dibuja en memoria y no se muestra
    if (i2c_new_master_bus(&bc, &bus) != ESP_OK) {
        ESP_LOGE(TAG, "no pude abrir el I2C de la pantalla");
        lienzo = calloc(ANCHO * ALTO, 2);
        return;
    }
    const esp_lcd_panel_io_i2c_config_t ic = {.dev_addr = DIRECCION, .scl_speed_hz = 400000, .control_phase_bytes = 1,
                                              .lcd_cmd_bits = 8, .lcd_param_bits = 8, .dc_bit_offset = 6};
    lienzo = calloc(ANCHO * ALTO, 2);
    assert(lienzo);
    esp_lcd_panel_ssd1306_config_t sc = {.height = ALTO};
    const esp_lcd_panel_dev_config_t pc = {.bits_per_pixel = 1, .reset_gpio_num = -1, .vendor_config = &sc};
    if (i2c_master_probe(bus, DIRECCION, 100) != ESP_OK || esp_lcd_new_panel_io_i2c(bus, &ic, &io) != ESP_OK ||
        esp_lcd_new_panel_ssd1306(io, &pc, &panel) != ESP_OK || esp_lcd_panel_reset(panel) != ESP_OK ||
        esp_lcd_panel_init(panel) != ESP_OK || esp_lcd_panel_disp_on_off(panel, true) != ESP_OK) {
        ESP_LOGE(TAG, "no encuentro la pantalla OLED (SDA=8, SCK=9, VCC=3.3): sigo sin pantalla");
        panel = NULL;
        io = NULL;
        return;
    }
    lienzo_limpiar(COLOR_FONDO);
    pantalla_mostrar();
    ESP_LOGI(TAG, "lista");
}

void pantalla_colores_al_reves(bool al_reves)
{
    if (panel) esp_lcd_panel_invert_color(panel, al_reves);
}

void pantalla_dar_vuelta(bool invertida)
{
    espejo = invertida;
    if (panel) esp_lcd_panel_mirror(panel, invertida, invertida);
}

// "Brillo" de la OLED = contraste (0..255). Nunca del todo apagada.
void pantalla_brillo(int por_mil, int ms)
{
    (void)ms;
    if (!io) return;
    if (por_mil < 0) por_mil = 0;
    if (por_mil > 1000) por_mil = 1000;
    uint8_t c = (uint8_t)(8 + por_mil * 247 / 1000);
    esp_lcd_panel_io_tx_param(io, 0x81, &c, 1);
}

void pantalla_mostrar(void)
{
    if (!panel) return;
    memset(paginas, 0, sizeof(paginas));
    for (int y = 0; y < ALTO; y++)
        for (int x = 0; x < ANCHO; x++) {
            uint16_t c = dar_vuelta(lienzo[y * ANCHO + x]);
            int r = (c >> 11) << 3, g = ((c >> 5) & 63) << 2, b = (c & 31) << 3;
            if ((r * 3 + g * 6 + b) / 10 > 80) paginas[(y / 8) * ANCHO + x] |= 1 << (y % 8);
        }
    if (panel) esp_lcd_panel_draw_bitmap(panel, 0, 0, ANCHO, ALTO, paginas);
}
#endif

void lienzo_limpiar(uint16_t color)
{
    lienzo_rect(0, 0, ANCHO, ALTO, color);
}

void lienzo_imagen(int x, int y, int w, int h, const uint16_t *rgb565)
{
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) {
            int px = x + i, py = y + j;
            if (px >= 0 && px < ANCHO && py >= 0 && py < ALTO) lienzo[py * ANCHO + px] = dar_vuelta(rgb565[j * w + i]);
        }
}

void lienzo_rect(int x, int y, int w, int h, uint16_t color)
{
    uint16_t c = dar_vuelta(color);
    for (int j = y < 0 ? 0 : y; j < y + h && j < ALTO; j++)
        for (int i = x < 0 ? 0 : x; i < x + w && i < ANCHO; i++)
            lienzo[j * ANCHO + i] = c;
}

// ---- texto ----

// Lee un carácter UTF-8; avanza *p. Devuelve 0xFFFD si viene roto.
static uint32_t leer_utf8(const char **p, const char *fin)
{
    const uint8_t *s = (const uint8_t *)*p;
    uint32_t c = s[0];
    int n = c < 0x80 ? 0 : (c >> 5) == 6 ? 1 : (c >> 4) == 14 ? 2 : (c >> 3) == 30 ? 3 : -1;
    if (n < 0 || (const char *)s + n >= fin + (n ? 0 : 1)) {
        (*p)++;
        return 0xFFFD;
    }
    if (n) c &= 0x3F >> n;
    for (int i = 1; i <= n; i++) c = (c << 6) | (s[i] & 0x3F);
    *p += n + 1;
    return c;
}

static const glifo_t *buscar(const fuente_t *f, uint32_t cp)
{
    int a = 0, b = f->n - 1;
    while (a <= b) {
        int m = (a + b) / 2;
        if (f->glifos[m].cp == cp) return &f->glifos[m];
        if (f->glifos[m].cp < cp) a = m + 1; else b = m - 1;
    }
    return NULL;
}

// Carácter a dibujar: se ignoran los modificadores de emoji y lo desconocido sale como '?'
static const glifo_t *glifo_de(const fuente_t *f, uint32_t cp)
{
    if (cp == 0xFE0F || cp == 0x200D || (cp >= 0x1F3FB && cp <= 0x1F3FF)) return NULL;
    if (cp == '\n' || cp == '\r' || cp == '\t') cp = ' ';
    const glifo_t *g = buscar(f, cp);
    return g ? g : buscar(f, '?');
}

static void dibujar_glifo(int x0, int y0, const fuente_t *f, const glifo_t *g, uint16_t color)
{
    const uint8_t *d = f->datos + g->pos;
    int r = color >> 11, v = (color >> 5) & 0x3F, a = color & 0x1F;
    for (int j = 0; j < g->h; j++) {
        int y = y0 + g->y + j;
        for (int i = 0; i < g->w; i++) {
            int x = x0 + g->x + i, k = j * g->w + i;
            if (y < 0 || y >= ALTO || x < 0 || x >= ANCHO) continue;
            uint16_t *px = &lienzo[y * ANCHO + x];
            if (g->color) {
                *px = (uint16_t)((d[k * 2 + 1] << 8) | d[k * 2]);  // ya viene big endian
                continue;
            }
            int alfa = (k & 1) ? (d[k >> 1] & 0x0F) : (d[k >> 1] >> 4);
            if (!alfa) continue;
            uint16_t bajo = dar_vuelta(*px);
            int br = bajo >> 11, bv = (bajo >> 5) & 0x3F, ba = bajo & 0x1F;
            br += (r - br) * alfa / 15;
            bv += (v - bv) * alfa / 15;
            ba += (a - ba) * alfa / 15;
            *px = dar_vuelta((uint16_t)((br << 11) | (bv << 5) | ba));
        }
    }
}

int lienzo_texto(int x, int y, const fuente_t *f, uint16_t color, const char *s, int bytes)
{
    const char *p = s, *fin = s + bytes;
    int x0 = x;
    while (p < fin && *p) {
        const glifo_t *g = glifo_de(f, leer_utf8(&p, fin));
        if (!g) continue;
        dibujar_glifo(x, y, f, g, color);
        x += g->avance;
    }
    return x - x0;
}

int medir_texto(const fuente_t *f, const char *s, int bytes)
{
    const char *p = s, *fin = s + bytes;
    int w = 0;
    while (p < fin && *p) {
        const glifo_t *g = glifo_de(f, leer_utf8(&p, fin));
        if (g) w += g->avance;
    }
    return w;
}

int partir_lineas(const fuente_t *f, const char *s, int ancho, int *ini, int *largo, int max_lineas)
{
    int total = strlen(s), n = 0, pos = 0;
    while (pos < total) {
        while (pos < total && s[pos] == ' ') pos++;  // sin espacios al comienzo de la línea
        if (pos >= total) break;
        int fin = pos, ultimo_corte = -1, w = 0;
        while (fin < total && s[fin] != '\n') {
            const char *p = s + fin;
            const glifo_t *g = glifo_de(f, leer_utf8(&p, s + total));
            int a = g ? g->avance : 0;
            if (w + a > ancho && fin > pos) break;
            if (s[fin] == ' ') ultimo_corte = fin;
            w += a;
            fin = p - s;
        }
        int corte = fin;
        if (fin < total && s[fin] != '\n' && ultimo_corte > pos) corte = ultimo_corte;  // cortar en la palabra
        if (n < max_lineas) {
            ini[n] = pos;
            largo[n] = corte - pos;
        }
        n++;
        pos = corte;
        if (pos < total && s[pos] == '\n') pos++;
    }
    return n;
}
