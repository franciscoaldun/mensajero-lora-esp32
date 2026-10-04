// Mensajero LoRa en ESP32-S3 (va sellado contra el agua: todo se maneja por Wi-Fi o por radio).
//  - Pantallita: siempre con poco brillo; al llegar algo de él, brillo alto por N minutos.
//  - Ella lee y contesta desde el celular (red Wi-Fi propia, la página se abre sola).
//  - Actualizaciones por Wi-Fi; si una versión nueva no arranca bien, vuelve sola a la anterior.
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "nvs_flash.h"
#include "driver/temperature_sensor.h"
#include "pantalla.h"
#include "vista.h"
#include "almacen.h"
#include "enlace.h"
#include "portal.h"
#include "miniatura.h"
#include "consola.h"
#include "lora.h"

static const char *TAG = "mensajero";

static volatile int64_t brillo_hasta;
static volatile bool sin_leer;
static volatile int64_t llego_ms;     // cuándo llegó lo último de él (en esta sesión)
static volatile uint32_t llego_n;
static int pagina;
static int64_t inicio_pagina;
static char aviso[96];
static uint16_t color_aviso;
static int64_t aviso_hasta;
static temperature_sensor_handle_t sensor_temp;

static int64_t ahora_ms(void) { return esp_timer_get_time() / 1000; }

static void poner_aviso(const char *s, uint16_t color, int ms)
{
    strlcpy(aviso, s, sizeof(aviso));
    color_aviso = color;
    aviso_hasta = ahora_ms() + ms;
}

// Lo llama el enlace cuando llega un mensaje o una foto completa de él
static void al_llegar(uint32_t n)
{
    llego_n = n;
    llego_ms = ahora_ms();
    sin_leer = true;
    brillo_hasta = ahora_ms() + (int64_t)ajustes.minutos_alto * 60000;  // el ciclo principal sube el brillo
    pagina = 0;
    inicio_pagina = ahora_ms();
}

// Distancia aproximada por la señal (modelo log-distancia, exponente 3: ciudad y campo mezclados).
// Sólo orienta: puede equivocarse al doble o a la mitad. Se afina con la prueba en terreno.
static void distancia_txt(int rssi, int potencia, char *s, size_t t)
{
    double p1m = potencia + 4.0 - 31.6;  // dos antenas de ~2 dBi y la pérdida a 1 m en 906 MHz
    double d = pow(10.0, (p1m - rssi) / 30.0);
    if (d < 200) snprintf(s, t, "~%d m", (int)(d / 10 + 0.5) * 10);
    else if (d < 1000) snprintf(s, t, "~%d m", (int)(d / 100 + 0.5) * 100);
    else snprintf(s, t, "~%.1f km", d / 1000);
    for (char *p = s; *p; p++) if (*p == '.') *p = ',';
}

// Lo llama el enlace cuando él aparece al alcance o se pierde la señal
static void al_presencia(bool cerca, int rssi)
{
    char d[24], s[96];
    distancia_txt(rssi, 22, d, sizeof(d));
    if (cerca) {
        snprintf(s, sizeof(s), "\xF0\x9F\x92\x99 \xC2\xA1%s est\xC3\xA1 cerca! %s", ajustes.otro, d);
        poner_aviso(s, COLOR_VIOLETA, 90000);
        brillo_hasta = ahora_ms() + (int64_t)ajustes.minutos_alto * 60000;
    } else {
        snprintf(s, sizeof(s), "se perdi\xC3\xB3 la se\xC3\xB1" "al de %s (estaba a %s)", ajustes.otro, d);
        poner_aviso(s, COLOR_TENUE, 20000);
    }
}

// ---------- comandos que llegan por radio desde el PC ----------
void comandos_ejecutar(const char *cmd, char *r, size_t largo)
{
    int v;
    if (strcmp(cmd, "estado") == 0) {
        size_t total, usado;
        almacen_espacio(&total, &usado);
        enlace_estado_t e;
        enlace_estado(&e);
        float temp = 0;
        if (sensor_temp) temperature_sensor_get_celsius(sensor_temp, &temp);
        int64_t s = esp_timer_get_time() / 1000000;
        time_t t = time(NULL);
        struct tm tm;
        localtime_r(&t, &tm);
        char hora[24] = "sin hora";
        if (t > 1700000000) strftime(hora, sizeof(hora), "%d-%m %H:%M", &tm);
        char pres[48], d[24];
        if (e.cerca) {
            distancia_txt(e.rssi_suave, e.potencia_otro, d, sizeof(d));
            snprintf(pres, sizeof(pres), "te oye a %d dBm %s", e.rssi_alla == 127 ? e.rssi_suave : e.rssi_alla, d);
        } else {
            snprintf(pres, sizeof(pres), "%s", e.hubo_contacto ? "sin señal tuya" : "aún no te oye");
        }
        snprintf(r, largo, "v%s · encendida %lldh%02lldm · %s · canal %02X %d dBm nivel %d · %s · %d sin entregar · "
                           "flash %.1f/%.1f MB · %lu msj · celulares %d · %.0f°C · %s · reset:%d",
                 esp_app_get_description()->version, s / 3600, (s / 60) % 60, hora, ajustes.canal, ajustes.potencia, e.nivel,
                 pres, e.pendientes, usado / 1048576.0, total / 1048576.0,
                 (unsigned long)almacen_ultimo(), portal_clientes(), temp, esp_ota_get_running_partition()->label,
                 (int)esp_reset_reason());
    } else if (strcmp(cmd, "reiniciar") == 0) {
        snprintf(r, largo, "reiniciando en 3 s");
        almacen_cerrar();
        esp_timer_handle_t tm;
        const esp_timer_create_args_t a = {.callback = (esp_timer_cb_t)esp_restart, .name = "reinicio"};
        esp_timer_create(&a, &tm);
        esp_timer_start_once(tm, 3000000);
    } else if (sscanf(cmd, "brillo bajo %d", &v) == 1) {
        ajustes.brillo_bajo = v < BRILLO_BAJO_MIN ? BRILLO_BAJO_MIN : v > 1000 ? 1000 : v;
        v = ajustes.brillo_bajo;
        ajustes_guardar();
        snprintf(r, largo, "brillo bajo = %d", v);
    } else if (sscanf(cmd, "brillo alto %d", &v) == 1) {
        ajustes.brillo_alto = v < BRILLO_BAJO_MIN ? BRILLO_BAJO_MIN : v > 1000 ? 1000 : v;
        v = ajustes.brillo_alto;
        ajustes_guardar();
        snprintf(r, largo, "brillo alto = %d", v);
    } else if (sscanf(cmd, "minutos %d", &v) == 1) {
        ajustes.minutos_alto = v;
        ajustes_guardar();
        snprintf(r, largo, "minutos con brillo alto = %d", v);
    } else if (sscanf(cmd, "potencia %d", &v) == 1) {
        snprintf(r, largo, "la potencia de radio es fija: 22 dBm (lo máximo, nunca se baja)");
    } else if (sscanf(cmd, "latido %d", &v) == 1 && v >= 30 && v <= 180) {
        ajustes.latido_s = v;
        ajustes_guardar();
        snprintf(r, largo, "latido cada %d s (se da por perdida la señal a los %d s)", v, v * 5 / 2 + 30);
    } else if (strncmp(cmd, "turbo ", 6) == 0) {
        ajustes.turbo = strncmp(cmd + 6, "si", 2) == 0 || strncmp(cmd + 6, "sí", 3) == 0 || cmd[6] == '1';
        ajustes_guardar();
        snprintf(r, largo, "turbo para fotos: %s", ajustes.turbo ? "sí (sólo con señal de sobra)" : "no (siempre máximo alcance)");
    } else if (strncmp(cmd, "nombre ", 7) == 0 && cmd[7]) {
        strlcpy(ajustes.otro, cmd + 7, sizeof(ajustes.otro));
        ajustes_guardar();
        snprintf(r, largo, "en su aparato apareces como \"%s\"", ajustes.otro);
    } else if (strcmp(cmd, "borrar historial") == 0) {
        enlace_olvidar_pendientes();
        almacen_borrar_todo();
        sin_leer = false;
        snprintf(r, largo, "historial borrado: la pantallita y la página quedan en blanco");
    } else if (strcmp(cmd, "olvidar") == 0) {
        enlace_olvidar_pendientes();
        snprintf(r, largo, "cola vaciada: lo que no llegó queda marcado y ya no se reintenta solo");
    } else if (strcmp(cmd, "colores") == 0) {  // si se ven como negativo (fondo blanco, fotos raras)
        ajustes.colores_al_reves = !ajustes.colores_al_reves;
        pantalla_colores_al_reves(ajustes.colores_al_reves);
        ajustes_guardar();
        snprintf(r, largo, "colores de la pantalla %s", ajustes.colores_al_reves ? "invertidos" : "normales");
    } else if (strcmp(cmd, "girar") == 0) {
        ajustes.girada = !ajustes.girada;
        pantalla_dar_vuelta(ajustes.girada);
        ajustes_guardar();
        snprintf(r, largo, "pantalla %s", ajustes.girada ? "dada vuelta" : "normal");
    } else if (strncmp(cmd, "wifi ", 5) == 0) {
        snprintf(r, largo, "la red es fija: \"%s\" (sin emojis, para que ningún celular la vea rara)", ajustes.ssid);
    } else {
        snprintf(r, largo, "comandos: estado | reiniciar | brillo bajo N | brillo alto N | minutos N | girar | colores | "
                           "latido N | turbo si|no | nombre X | borrar historial | potencia N | canal NN (34..4D; vuelve solo si se pierde el contacto) | olvidar | wifi NOMBRE;CLAVE");
    }
}

// ---------- pantalla ----------
static uint16_t *mini;
static int mini_w, mini_h;
static uint32_t mini_n;

static void dibujar(void)
{
    registro_t ultimo;
    bool hay = almacen_ultimo_mostrable(DE_EL, &ultimo);
    int64_t t = ahora_ms();

    // miniatura de la última foto que llegó completa
    if (hay && ultimo.tipo == ES_FOTO && ultimo.estado == RECIBIDO && ultimo.n != mini_n) {
        free(mini);
        mini = NULL;
        size_t largo;
        uint8_t *jpg = almacen_leer_foto(ultimo.n, &largo);
        if (jpg) {
            mini = miniatura_hacer(jpg, largo, 44, 100, &mini_w, &mini_h);  // cabe entre las dos barras
            free(jpg);
        }
        mini_n = ultimo.n;
    }

    enlace_estado_t e;
    enlace_estado(&e);
    const char *texto = NULL;
    char txt_foto[64];
    if (hay) {
        if (ultimo.tipo == ES_FOTO) {
            if (ultimo.estado == RECIBIDO) snprintf(txt_foto, sizeof(txt_foto), "te mandó una foto \xF0\x9F\x93\xB7 mírala en el celular");
            else snprintf(txt_foto, sizeof(txt_foto), "llegando una foto\xE2\x80\xA6 %d%%", ultimo.progreso / 10);
            texto = txt_foto;
        } else {
            texto = ultimo.texto;
        }
    }

    // ¿cuánto hace? con la hora real si se sabe; si no, con el reloj desde que se encendió
    int64_t llegada = 0;
    struct timeval tv;
    gettimeofday(&tv, NULL);
    int64_t cuando = hay ? (ultimo.ts_llegada ? ultimo.ts_llegada : ultimo.ts) : 0;  // desde que LLEGÓ
    if (hay && cuando && tv.tv_sec > 1700000000) llegada = t - (tv.tv_sec - cuando) * 1000;
    else if (hay && ultimo.n == llego_n) llegada = llego_ms;

    // aviso de arriba: avisos puntuales > radio con problemas > red Wi-Fi si aún no hay mensajes
    static char aviso_fijo[96];
    const char *av = NULL;
    uint16_t col = color_aviso;
    if (t < aviso_hasta) av = aviso;
    else if (!e.lora_conectado) av = "\xE2\x9A\xA0 sin radio: revisa el cable USB", col = COLOR_MAL;
    else if (!hay) {
        snprintf(aviso_fijo, sizeof(aviso_fijo), "red \xE2\x80\x9C%s\xE2\x80\x9D \xC2\xB7 192.168.4.1", ajustes.ssid);
        av = aviso_fijo;
        col = COLOR_TENUE;
    }

    char distancia[24], hora[8];
    distancia_txt(e.rssi_suave, e.potencia_otro > 0 ? e.potencia_otro : 22, distancia, sizeof(distancia));
    time_t ahora_s = tv.tv_sec;
    struct tm lt;
    localtime_r(&ahora_s, &lt);
    strftime(hora, sizeof(hora), "%H:%M", &lt);
    vista_t v = {
        .oido_ms = e.hubo_contacto ? e.visto_ms : 0, .latido_mandado_ms = e.latido_mandado_ms,
        .periodo_latido_s = ajustes.latido_s, .distancia = e.cerca ? distancia : NULL, .potencia = ajustes.potencia,
        .hora = tv.tv_sec > 1700000000 ? hora : NULL,
        .texto = texto, .llegada_ms = llegada, .cantidad = hay ? 1 : 0, .viendo = 0, .sin_leer = sin_leer,
        .rssi = e.ultimo_rssi, .aviso = av, .color_aviso = col, .ahora_ms = t,
        .presencia = !e.hubo_contacto ? 0 : e.cerca ? 1 : 2,
        .miniatura = (hay && ultimo.tipo == ES_FOTO && ultimo.n == mini_n) ? mini : NULL, .mini_w = mini_w, .mini_h = mini_h,
        .pagina = &pagina, .inicio_pagina = &inicio_pagina,
    };
    vista_dibujar(&v);
    pantalla_mostrar();
}

// El brillo lo maneja SÓLO el ciclo principal, y se vuelve a aplicar en cada vuelta. Antes lo cambiaban
// dos tareas con el "fade" del LEDC: si una orden llegaba mientras otra transición seguía en curso, el chip
// la ignoraba y la pantalla quedaba oscura aunque llegaran mensajes. Sube rápido (~0,75 s), baja suave (~3 s).
#define BRILLO_AL_TRANSMITIR 40  // mientras la radio transmite (menos consumo = el voltaje no cae)
static volatile int64_t transmite_hasta;
static int brillo_actual = -1;

// Lo llama el enlace justo ANTES de transmitir: la pantalla baja al tiro (no espera la vuelta del ciclo)
static void al_transmitir(int ms)
{
    transmite_hasta = ahora_ms() + ms;
    pantalla_brillo(BRILLO_AL_TRANSMITIR, 0);
}

static void ajustar_brillo(int64_t t)
{
    int objetivo = t < brillo_hasta ? ajustes.brillo_alto : ajustes.brillo_bajo;
    if (objetivo < BRILLO_BAJO_MIN) objetivo = BRILLO_BAJO_MIN;
    if (objetivo > 1000) objetivo = 1000;
    int actual = brillo_actual < 0 ? objetivo : brillo_actual;
    int paso = objetivo > actual ? 250 : 60;
    if (actual < objetivo) actual = actual + paso > objetivo ? objetivo : actual + paso;
    else if (actual > objetivo) actual = actual - paso < objetivo ? objetivo : actual - paso;
    brillo_actual = actual;
    pantalla_brillo(t < transmite_hasta ? BRILLO_AL_TRANSMITIR : actual, 0);
}

// avisa en la pantallita cuando un mensaje de ella llega (o no) al PC
static void vigilar_mensajes_de_ella(void)
{
    static uint32_t n_visto;
    static uint8_t estado_visto = 255;
    registro_t r;
    if (!almacen_ultimo_de(DE_ELLA, &r)) return;
    static uint8_t leido_visto;
    if (r.n != n_visto) {
        n_visto = r.n;
        estado_visto = r.estado;
        leido_visto = r.leido;
        if (r.estado == ENVIANDO) poner_aviso("mandando\xE2\x80\xA6", COLOR_ACENTO, 60000);
        return;
    }
    if (r.leido != leido_visto) {
        leido_visto = r.leido;
        if (r.leido) poner_aviso("\xF0\x9F\x91\x80 ley\xC3\xB3 tu mensaje", COLOR_BIEN, 10000);
    }
    if (r.estado == estado_visto) return;
    estado_visto = r.estado;
    if (r.estado == ENTREGADO) poner_aviso("\xE2\x9C\x93\xE2\x9C\x93 le lleg\xC3\xB3 tu mensaje", COLOR_BIEN, 8000);
    else if (r.estado == FALLIDO) poner_aviso("\xE2\x9C\x97 todav\xC3\xAD" "a no le llega: se reintenta solo", COLOR_MAL, 10000);
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    const esp_partition_t *corriendo = esp_ota_get_running_partition();
    esp_ota_img_states_t estado_img;
    bool a_prueba = esp_ota_get_state_partition(corriendo, &estado_img) == ESP_OK && estado_img == ESP_OTA_IMG_PENDING_VERIFY;
    ESP_LOGI(TAG, "versión %s en %s%s", esp_app_get_description()->version, corriendo->label, a_prueba ? " (a prueba)" : "");

    // Hora de Chile continental: -4, y -3 en verano (del primer sábado de septiembre al de abril)
    setenv("TZ", "<-04>4<-03>,M9.1.6/24,M4.1.6/24", 1);
    tzset();
    ajustes_cargar();
    almacen_iniciar();
    pantalla_iniciar();
    pantalla_dar_vuelta(ajustes.girada);
    pantalla_colores_al_reves(ajustes.colores_al_reves);
    poner_aviso("encendiendo\xE2\x80\xA6", COLOR_TENUE, 4000);
    dibujar();
    pantalla_brillo(ajustes.brillo_bajo, 0);

    temperature_sensor_config_t tc = TEMPERATURE_SENSOR_CONFIG_DEFAULT(10, 80);
    if (temperature_sensor_install(&tc, &sensor_temp) == ESP_OK) temperature_sensor_enable(sensor_temp);
    else sensor_temp = NULL;

    lora_fijar_potencia(ajustes.potencia, false);
    lora_fijar_canal(ajustes.canal, false);
    enlace_al_transmitir(al_transmitir);
    enlace_iniciar(al_llegar, al_presencia);
    consola_iniciar();
    portal_iniciar();

    bool confirmada = !a_prueba;
    // Vigilante: si este ciclo se traba 20 s (p. ej. dos tareas esperándose), el aparato se reinicia solo.
    // Sellado no puede quedar colgado para siempre.
    esp_task_wdt_add(NULL);
    int64_t inicio = ahora_ms(), visita_vista = 0;
    for (;;) {
        int64_t t = ahora_ms();
        // ella abrió la página después de que llegó el mensaje: ya lo vio
        int64_t visita = portal_ultima_visita_ms();
        if (sin_leer && visita > llego_ms && visita != visita_vista) {
            sin_leer = false;
            visita_vista = visita;
        }
        ajustar_brillo(t);
        vigilar_mensajes_de_ella();
        dibujar();

        // Una versión nueva se da por buena al minuto si el Wi-Fi y la página andan; si se cae antes, vuelve la anterior
        if (!confirmada && t - inicio > 60000 && portal_listo()) {
            esp_ota_mark_app_valid_cancel_rollback();
            confirmada = true;
            ESP_LOGW(TAG, "versión nueva confirmada");
        }
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}
