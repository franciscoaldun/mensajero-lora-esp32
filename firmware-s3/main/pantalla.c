// Pantalla ST7789P3 de 2,25" (76x284) usada de lado (284 de ancho x 76 de alto), más el brillo por PWM.
#include <string.h>
#include "pantalla.h"
#ifndef SIMULADOR
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_st7789.h"
#include "esp_log.h"
#endif

// ESP32-S3 DevKitC N16R8: GPIO 14..9 seguidos en la misma tira, en el orden de los pines de la pantalla.
// (VCC de la pantalla va a 3V3, no a 5V)
#define PIN_SCL 14
#define PIN_SDA 13
#define PIN_RST 12
#define PIN_DC 11
#define PIN_CS 10
#define PIN_BL 9

// Orientación: se puede dar vuelta 180° desde la página de ajustes (queda guardado), sin reflashear
#define GIRAR_XY true
static bool espejo_x = false, espejo_y = true;
// El controlador es de 240x320; la ventana visible está corrida (82 y 18 px)
#define DESFASE_X 18
#define DESFASE_Y 82

#ifdef SIMULADOR
static uint16_t lienzo_sim[ANCHO * ALTO];
static uint16_t *lienzo = lienzo_sim;
const uint16_t *pantalla_lienzo(void) { return lienzo; }
#else
static const char *TAG = "pantalla";
static esp_lcd_panel_handle_t panel;
static uint16_t *lienzo;  // ANCHO*ALTO, ya en el orden de bytes que pide la pantalla
const uint16_t *pantalla_lienzo(void) { return lienzo; }
#endif

static bool colores_al_reves;  // inversión de color del panel (ver pantalla_iniciar)

static inline uint16_t dar_vuelta(uint16_t c) { return (uint16_t)((c << 8) | (c >> 8)); }

#ifndef SIMULADOR
void pantalla_iniciar(void)
{
    // Brillo: PWM de 5 kHz, 10 bits, con transiciones suaves
    ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0, .freq_hz = 5000, .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    ledc_channel_config_t c = {
        .gpio_num = PIN_BL, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0, .duty = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&c));
    ESP_ERROR_CHECK(ledc_fade_func_install(0));

    spi_bus_config_t bus = {
        .sclk_io_num = PIN_SCL, .mosi_io_num = PIN_SDA, .miso_io_num = -1,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = ANCHO * ALTO * 2,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_spi_config_t io_cfg = {
        .cs_gpio_num = PIN_CS, .dc_gpio_num = PIN_DC, .spi_mode = 0, .pclk_hz = 40 * 1000 * 1000,
        .trans_queue_depth = 4, .lcd_cmd_bits = 8, .lcd_param_bits = 8,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io));
    esp_lcd_panel_dev_config_t dev = {
        .reset_gpio_num = PIN_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &dev, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    // Este panel (ST7789P3 76x284) muestra los colores al revés CON la inversión: el fondo azul noche se
    // veía blanco y las fotos en negativo. Sin inversión de partida; el ajuste "colores" la da vuelta.
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel, colores_al_reves));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel, GIRAR_XY));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, espejo_x, espejo_y));
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel, DESFASE_X, DESFASE_Y));

    lienzo = heap_caps_malloc(ANCHO * ALTO * 2, MALLOC_CAP_DMA);
    assert(lienzo);
    lienzo_limpiar(COLOR_FONDO);
    pantalla_mostrar();
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
    ESP_LOGI(TAG, "lista");
}

void pantalla_colores_al_reves(bool al_reves)
{
    colores_al_reves = al_reves;
    if (panel) esp_lcd_panel_invert_color(panel, al_reves);
}

void pantalla_dar_vuelta(bool invertida)
{
    espejo_x = invertida;
    espejo_y = !invertida;
    if (panel) esp_lcd_panel_mirror(panel, espejo_x, espejo_y);
}

void pantalla_brillo(int por_mil, int ms)
{
    if (por_mil < 0) por_mil = 0;
    if (por_mil > 1000) por_mil = 1000;
    uint32_t duty = (uint32_t)por_mil * 1023 / 1000;
    if (ms <= 0) {
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
    } else {
        ledc_set_fade_time_and_start(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty, ms, LEDC_FADE_NO_WAIT);
    }
}

void pantalla_mostrar(void)
{
    esp_lcd_panel_draw_bitmap(panel, 0, 0, ANCHO, ALTO, lienzo);
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
