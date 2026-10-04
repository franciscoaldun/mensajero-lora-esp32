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
#include "esp_log.h"
#include "usb/usb_host.h"
#include "usb/cdc_acm_host.h"
#include "usb/vcp_ch34x.h"

static const char *TAG = "lora";
static cdc_acm_dev_hdl_t disp;
static volatile bool conectado, listo, en_modo_at;
static SemaphoreHandle_t se_fue, cerrojo_tx;
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

static void tarea_usb(void *arg)
{
    for (;;) {
        uint32_t ev;
        usb_host_lib_handle_events(portMAX_DELAY, &ev);
        if (ev & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
    }
}

static bool al_recibir(const uint8_t *datos, size_t n, void *arg)
{
    if (en_modo_at) xStreamBufferSend(respuestas_at, datos, n, 0);
    else if (avisar) avisar(datos, n);
    return true;
}

static void al_evento(const cdc_acm_host_dev_event_data_t *e, void *arg)
{
    if (e->type == CDC_ACM_HOST_DEVICE_DISCONNECTED) {
        ESP_LOGW(TAG, "módulo desenchufado");
        if (suceso) suceso('U', "desenchufado", 0);
        conectado = listo = false;
        cdc_acm_host_close(e->data.cdc_hdl);
        disp = NULL;
        xSemaphoreGive(se_fue);
    } else if (e->type == CDC_ACM_HOST_ERROR) {
        ESP_LOGE(TAG, "error USB %d", e->data.error);
    }
}

static void escribir(const char *s)
{
    if (disp) cdc_acm_host_data_tx_blocking(disp, (const uint8_t *)s, strlen(s), 1000);
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

// Directo con "+++" (antes se probaba con "AT", pero en modo normal esas letras salían por el aire como
// un paquete basura). Si responde "Exit" es que ya estaba en modo AT y salió: se entra de nuevo.
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
    vTaskDelay(pdMS_TO_TICKS(200));
}

bool lora_comandos_at(const char *comandos, char *salida, size_t largo_salida)
{
    if (!conectado) {
        snprintf(salida, largo_salida, "sin módulo");
        return false;
    }
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
        ESP_LOGE(TAG, "el módulo no entra al modo AT");
        en_modo_at = false;
        xSemaphoreGive(cerrojo_tx);
        if (suceso) suceso('@', "revisar SIN AT", (int)(ms_ahora() - t0));
        return;
    }
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

static void tarea_conexion(void *arg)
{
    const cdc_acm_host_device_config_t cfg = {
        .connection_timeout_ms = 1000, .out_buffer_size = 512, .in_buffer_size = 512,
        .event_cb = al_evento, .data_cb = al_recibir,
    };
    int vueltas = 0;
    for (;;) {
        cdc_acm_dev_hdl_t d = NULL;
        esp_err_t e = ch34x_vcp_open(CH34X_PID_AUTO, 0, &cfg, &d);
        if (e != ESP_OK) {
            if (vueltas++ % 5 == 0) {
                usb_host_lib_info_t info = {0};
                usb_host_lib_info(&info);
                ESP_LOGW(TAG, "buscando el módulo (%s); dispositivos USB vistos: %d", esp_err_to_name(e), info.num_devices);
            }
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        disp = d;
        cdc_acm_line_coding_t lc = {.dwDTERate = 9600, .bCharFormat = 0, .bParityType = 0, .bDataBits = 8};
        cdc_acm_host_line_coding_set(disp, &lc);
        cdc_acm_host_set_control_line_state(disp, true, true);  // igual que lo deja Windows
        conectado = true;
        ESP_LOGI(TAG, "módulo enchufado");
        if (suceso) suceso('U', "enchufado", 0);
        vTaskDelay(pdMS_TO_TICKS(300));
        lora_revisar_configuracion();
        listo = conectado;
        xSemaphoreTake(se_fue, portMAX_DELAY);  // hasta que lo desenchufen
    }
}

static void nuevo_dispositivo(usb_device_handle_t d)
{
    const usb_device_desc_t *desc;
    if (usb_host_get_device_descriptor(d, &desc) == ESP_OK)
        ESP_LOGI(TAG, "apareció un USB: VID %04X PID %04X", desc->idVendor, desc->idProduct);
}

void lora_iniciar(lora_al_recibir_t al_recibir_cb)
{
    avisar = al_recibir_cb;
    se_fue = xSemaphoreCreateBinary();
    cerrojo_tx = xSemaphoreCreateMutex();
    respuestas_at = xStreamBufferCreate(512, 1);
    const usb_host_config_t h = {.skip_phy_setup = false, .intr_flags = ESP_INTR_FLAG_LOWMED};
    ESP_ERROR_CHECK(usb_host_install(&h));
    xTaskCreate(tarea_usb, "usb", 4096, NULL, 10, NULL);
    const cdc_acm_host_driver_config_t dc = {
        .driver_task_stack_size = 4096, .driver_task_priority = 9, .xCoreID = 0, .new_dev_cb = nuevo_dispositivo,
    };
    ESP_ERROR_CHECK(cdc_acm_host_install(&dc));
    xTaskCreate(tarea_conexion, "lora_usb", 4096, NULL, 5, NULL);
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
    if (!aplicar || !conectado) return true;  // sin módulo: se le pone al conectarlo (revisar_configuracion)
    char cmd[16], r[64];
    snprintf(cmd, sizeof(cmd), "AT+CHANNEL%02X", c);
    return lora_comandos_at(cmd, r, sizeof(r)) && strstr(r, "OK") != NULL;
}

bool lora_conectado(void) { return conectado; }
bool lora_listo(void) { return listo; }

bool lora_mandar(const uint8_t *datos, size_t n)
{
    if (!listo) return false;
    // El DX-LR32 se GUARDA sin transmitir lo que mide 31, 63, 95... bytes (largo % 32 == 31) hasta que le
    // llega otra cosa (medido: siempre, en los dos módulos). Un 0x00 adelante lo evita; el que recibe lo
    // salta porque busca el 0xD5 del comienzo.
    uint8_t con_relleno[MAX_PAQUETE + 1];
    if (n % 32 == 31 && n < sizeof(con_relleno)) {
        con_relleno[0] = 0x00;
        memcpy(con_relleno + 1, datos, n);
        datos = con_relleno;
        n++;
    }
    xSemaphoreTake(cerrojo_tx, portMAX_DELAY);
    esp_err_t e = disp ? cdc_acm_host_data_tx_blocking(disp, datos, n, 2000) : ESP_FAIL;
    if (e == ESP_OK) libre_ms = ms_ahora() + ms_en_aire(n) + 150;
    xSemaphoreGive(cerrojo_tx);
    return e == ESP_OK;
}
