// El LoRa DX-LR32 soldado directo al ESP32-C3 (sin adaptador USB): UART1 a 9600 baudios.
//   LoRa TXD -> GPIO7 (entra al C3) · LoRa RXD <- GPIO6 (sale del C3) · AUX -> GPIO5 · M0 -> GPIO3 · M1 -> GPIO4
// M0/M1: con AT+SWITCH0 (el modo de siempre) son SALIDAS del propio LoRa (las deja en bajo), así que el C3 sólo
// los LEE: nunca los maneja, para no chocar con el módulo. El modo AT se entra con "+++" como en el aparato de ella.
// Misma configuración, mismo "+++" que se espera a que el módulo termine de transmitir, mismo relleno para los
// paquetes de 31, 63, 95... bytes (el DX-LR32 se los guarda), misma revisión que corrige lo que no calce.
#include <string.h>
#include <stdio.h>
#include "lora.h"
#include "secreto.h"
#include "protocolo.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"

#define UART_LORA UART_NUM_1
#define PIN_TX 6   // al RXD del LoRa
#define PIN_RX 7   // desde el TXD del LoRa
#define PIN_AUX 5
#define PIN_M0 3
#define PIN_M1 4

static const char *TAG = "lora";
static volatile bool conectado, listo, en_modo_at;
static volatile bool responde = true;  // false = no entró al modo AT al revisarlo (¿sin energía?, ¿cable suelto?)
static SemaphoreHandle_t cerrojo_tx;
static StreamBufferHandle_t respuestas_at;   // lo que llega mientras estamos en modo AT
static lora_al_recibir_t avisar;
static lora_suceso_t suceso;
static volatile int64_t libre_ms;  // hasta cuándo el módulo está transmitiendo lo último que se le dio

static int64_t ms_ahora(void) { return esp_timer_get_time() / 1000; }
void lora_al_suceso(lora_suceso_t cb) { suceso = cb; }

// La configuración de siempre (igual que configurar.py en el PC). Sin AT+KEY: no se puede leer,
// así que sólo se escribe si el módulo aparece reseteado (otros valores fuera de lugar).
static char potencia[4] = "22";
static char canal[4] = "38";
static const struct { const char *nombre, *valor; } ESPERADO[] = {
    {"MODE", "0"}, {"LEVEL", "0"}, {"POWE", potencia}, {"CHANNEL", canal}, {"SLEEP", "2"}, {"SWITCH", "0"},
    {"OPENKEY", "1"}, {"PACKET", "3"}, {"DRSSI", "1"}, {"LBT", "0"}, {"IQ", "1"}, {"CRC", "1"},
};

// Todo lo que entrega el LoRa: en modo AT son respuestas; si no, paquetes para el enlace
static void tarea_rx(void *arg)
{
    uint8_t b[256];
    for (;;) {
        int n = uart_read_bytes(UART_LORA, b, sizeof(b), pdMS_TO_TICKS(20));
        if (n <= 0) continue;
        responde = true;  // si habla, está vivo
        if (en_modo_at) xStreamBufferSend(respuestas_at, b, n, 0);
        else if (avisar) avisar(b, n);
    }
}

static void escribir(const char *s) { uart_write_bytes(UART_LORA, s, strlen(s)); }

// AUX en alto = el LoRa está ocupado (recibiendo, transmitiendo o cambiando de modo). Escribirle justo ahí le hace
// perder bytes (medido el 03-oct: se cayó 1 byte de un paquete mientras le llegaba otro y el mensaje llegó roto).
// Si AUX quedara pegado en alto (cable suelto), al rato se deja de esperarlo para no trabar la radio.
static bool aux_sirve = true;
static void esperar_aux_libre(int max_ms)
{
    if (!aux_sirve) return;
    int64_t t0 = ms_ahora();
    while (gpio_get_level(PIN_AUX)) {
        if (ms_ahora() - t0 > max_ms) {
            ESP_LOGW(TAG, "AUX lleva %d ms ocupado: dejo de esperarlo (¿cable de AUX suelto?)", max_ms);
            aux_sirve = false;
            if (suceso) suceso('!', "AUX pegado", max_ms);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(2));
    }
}

// Manda un texto en modo AT y junta la respuesta hasta `espera_ms` o hasta ver `fin`
static int at_preguntar(const char *s, char *r, int largo, int espera_ms, const char *fin)
{
    xStreamBufferReset(respuestas_at);
    escribir(s);
    int n = 0;
    TickType_t limite = xTaskGetTickCount() + pdMS_TO_TICKS(espera_ms);
    while (xTaskGetTickCount() < limite && n < largo - 1) {
        size_t k = xStreamBufferReceive(respuestas_at, r + n, largo - 1 - n, pdMS_TO_TICKS(30));
        n += k;
        r[n] = 0;
        if (fin && strstr(r, fin)) break;
    }
    r[n] = 0;
    return n;
}

// Directo con "+++". Si responde "Exit" es que ya estaba en modo AT y salió: se entra de nuevo.
static bool entrar_at(void)
{
    char r[96];
    for (int i = 0; i < 4; i++) {
        at_preguntar("+++\r\n", r, sizeof(r), 1500, " AT");
        if (strstr(r, "Entry")) return true;
        vTaskDelay(pdMS_TO_TICKS(strstr(r, "Exit") ? 1500 : 400));  // tras "Exit" el módulo se reinicia
    }
    return false;
}

static void salir_at(void)
{
    char r[96];
    at_preguntar("+++\r\n", r, sizeof(r), 2500, "Power on");  // el módulo se reinicia con lo nuevo
    vTaskDelay(pdMS_TO_TICKS(300));  // que termine de arrancar antes de darle un paquete
}

// El "+++" tiene que llegar solo: escrito mientras el módulo todavía junta o transmite un paquete, sale
// por el aire pegado a ese paquete (y al otro le llega un '+' donde va el byte de la señal).
static void esperar_silencio(void)
{
    while (ms_ahora() < libre_ms) vTaskDelay(pdMS_TO_TICKS(10));
    esperar_aux_libre(9000);
    vTaskDelay(pdMS_TO_TICKS(200));
}

bool lora_comandos_at(const char *comandos, char *salida, size_t largo_salida)
{
    xSemaphoreTake(cerrojo_tx, portMAX_DELAY);
    esperar_silencio();
    int64_t t0 = ms_ahora();
    en_modo_at = true;
    bool ok = entrar_at();
    size_t usado = 0;
    salida[0] = 0;
    if (ok) {
        char copia[256], r[160];
        strlcpy(copia, comandos, sizeof(copia));
        for (char *c = strtok(copia, ";"); c; c = strtok(NULL, ";")) {
            while (*c == ' ') c++;
            char linea[80];
            snprintf(linea, sizeof(linea), "%s\r\n", c);
            at_preguntar(linea, r, sizeof(r), 800, "\r\n");
            for (char *p = r; *p; p++) if (*p == '\r' || *p == '\n') *p = ' ';
            usado += snprintf(salida + usado, usado < largo_salida ? largo_salida - usado : 0, "%s=>%s; ", c, r);
            if (usado >= largo_salida) usado = largo_salida - 1;
        }
        salir_at();
    } else {
        snprintf(salida, largo_salida, "no entró al modo AT");
    }
    en_modo_at = false;
    xSemaphoreGive(cerrojo_tx);
    if (suceso) {
        char nota[40];
        snprintf(nota, sizeof(nota), "%.17s %s", comandos, ok ? "ok" : "SIN AT");
        suceso('@', nota, (int)(ms_ahora() - t0));
    }
    return ok;
}

void lora_revisar_configuracion(void)
{
    xSemaphoreTake(cerrojo_tx, portMAX_DELAY);
    esperar_silencio();
    int64_t t0 = ms_ahora();
    en_modo_at = true;
    if (!entrar_at()) {
        ESP_LOGE(TAG, "el módulo no entra al modo AT (¿cables TXD/RXD al revés o sin 5 V?)");
        responde = false;  // la pantallita avisa "sin radio"
        en_modo_at = false;
        xSemaphoreGive(cerrojo_tx);
        if (suceso) suceso('@', "revisar SIN AT", (int)(ms_ahora() - t0));
        return;
    }
    responde = true;
    int malos = 0;
    char r[96], cmd[48];
    for (size_t i = 0; i < sizeof(ESPERADO) / sizeof(ESPERADO[0]); i++) {
        snprintf(cmd, sizeof(cmd), "AT+%s\r\n", ESPERADO[i].nombre);
        at_preguntar(cmd, r, sizeof(r), 600, "\r\n");
        char *igual = strchr(r, '=');
        if (!igual || strncmp(igual + 1, ESPERADO[i].valor, strlen(ESPERADO[i].valor)) != 0) {
            ESP_LOGW(TAG, "%s distinto (%s); se corrige", ESPERADO[i].nombre, r);
            snprintf(cmd, sizeof(cmd), "AT+%s%s\r\n", ESPERADO[i].nombre, ESPERADO[i].valor);
            at_preguntar(cmd, r, sizeof(r), 600, "OK");
            malos++;
        }
    }
    if (malos >= 3) {  // parece reseteado de fábrica: también vuelve la clave de la pareja
        snprintf(cmd, sizeof(cmd), "AT+KEY%u\r\n", (unsigned)CLAVE_MODULO);
        at_preguntar(cmd, r, sizeof(r), 600, "OK");
        ESP_LOGW(TAG, "clave del módulo repuesta");
    }
    salir_at();
    ESP_LOGI(TAG, "configuración revisada (%d corregidos)", malos);
    en_modo_at = false;
    xSemaphoreGive(cerrojo_tx);
    if (suceso) {
        char nota[32];
        snprintf(nota, sizeof(nota), "revisar: %d corregidos", malos);
        suceso('@', nota, (int)(ms_ahora() - t0));
    }
}

static void tarea_arranque(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(1200));  // que el LoRa termine de encender
    conectado = true;
    if (suceso) suceso('U', "cableado", 0);
    lora_revisar_configuracion();
    listo = true;
    vTaskDelete(NULL);
}

void lora_iniciar(lora_al_recibir_t al_recibir_cb)
{
    avisar = al_recibir_cb;
    cerrojo_tx = xSemaphoreCreateMutex();
    respuestas_at = xStreamBufferCreate(512, 1);
    // M0, M1 y AUX sólo se leen (M0/M1 los maneja el propio LoRa mientras esté en AT+SWITCH0)
    const gpio_config_t g = {.pin_bit_mask = (1ULL << PIN_M0) | (1ULL << PIN_M1) | (1ULL << PIN_AUX),
                             .mode = GPIO_MODE_INPUT, .pull_up_en = 0, .pull_down_en = 1, .intr_type = GPIO_INTR_DISABLE};
    gpio_config(&g);
    const uart_config_t c = {.baud_rate = 9600, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                             .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT};
    ESP_ERROR_CHECK(uart_driver_install(UART_LORA, 4096, 1024, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_LORA, &c));
    ESP_ERROR_CHECK(uart_set_pin(UART_LORA, PIN_TX, PIN_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    xTaskCreate(tarea_rx, "lora_rx", 3072, NULL, 9, NULL);
    xTaskCreate(tarea_arranque, "lora_ini", 4096, NULL, 5, NULL);
}

void lora_fijar_potencia(int dbm, bool aplicar)
{
    snprintf(potencia, sizeof(potencia), "%d", dbm);
    if (aplicar && conectado) {
        char cmd[16], r[64];
        snprintf(cmd, sizeof(cmd), "AT+POWE%d", dbm);
        lora_comandos_at(cmd, r, sizeof(r));
    }
}

bool lora_fijar_canal(int c, bool aplicar)
{
    snprintf(canal, sizeof(canal), "%02X", c);
    if (!aplicar || !conectado) return true;
    char cmd[16], r[64];
    snprintf(cmd, sizeof(cmd), "AT+CHANNEL%02X", c);
    return lora_comandos_at(cmd, r, sizeof(r)) && strstr(r, "OK") != NULL;
}

bool lora_conectado(void) { return conectado && responde; }
bool lora_listo(void) { return listo; }

bool lora_mandar(const uint8_t *datos, size_t n)
{
    if (!listo) return false;
    // El DX-LR32 se GUARDA sin transmitir lo que mide 31, 63, 95... bytes (largo % 32 == 31) hasta que le
    // llega otra cosa (medido). Un 0x00 adelante lo evita; el que recibe lo salta porque busca el 0xD5.
    uint8_t con_relleno[MAX_PAQUETE + 1];
    if (n % 32 == 31 && n < sizeof(con_relleno)) {
        con_relleno[0] = 0x00;
        memcpy(con_relleno + 1, datos, n);
        datos = con_relleno;
        n++;
    }
    xSemaphoreTake(cerrojo_tx, portMAX_DELAY);
    esperar_aux_libre(9000);  // un paquete largo tarda ~7 s en llegar entero
    int e = uart_write_bytes(UART_LORA, datos, n);
    uart_wait_tx_done(UART_LORA, pdMS_TO_TICKS(500));
    if (e == (int)n) libre_ms = ms_ahora() + ms_en_aire(n) + 150;
    xSemaphoreGive(cerrojo_tx);
    return e == (int)n;
}
