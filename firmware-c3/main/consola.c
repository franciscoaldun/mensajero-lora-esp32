// Consola por el USB-C del C3 (USB Serial/JTAG) para probar y controlar el aparato desde el PC sin Wi-Fi.
// Cada respuesta de máquina empieza con "@@" para separarla de los mensajes de registro.
//   ella <texto>        como si ella lo mandara desde el celular
//   foto <bytes>        luego van los bytes del JPEG: como si ella mandara esa foto
//   lista <k>           los últimos k registros (JSON, uno por línea)
//   estado              estado interno (radio, envío en curso, memoria)
//   comando <texto>     igual que un comando que llega por radio desde el PC
//   captura             lo que muestra la pantallita (RGB565 en base64)
//   reintentar <n>      reintenta un mensaje de ella
//   base <s>            canal de emergencia en s segundos sin contacto (para probarlo; 0 = los 30 min)
//   reiniciar
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "consola.h"
#include "almacen.h"
#include "enlace.h"
#include "pantalla.h"
#include "portal.h"
#include "lora.h"
#include "driver/gpio.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"


// Por el USB del C3 una escritura puede salir a medias si el PC no alcanzó a leer: se insiste hasta mandar todo
static void escribir_todo(const void *d, size_t n)
{
    const char *p = d;
    for (int vueltas = 0; n && vueltas < 50; vueltas++) {
        int k = usb_serial_jtag_write_bytes(p, n, pdMS_TO_TICKS(100));
        if (k > 0) {
            p += k;
            n -= k;
        }
    }
}

static void escribir(const char *s) { escribir_todo(s, strlen(s)); }

static void base64(const uint8_t *d, size_t n)
{
    static const char t[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char b[260];
    size_t k = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = d[i] << 16 | (i + 1 < n ? d[i + 1] << 8 : 0) | (i + 2 < n ? d[i + 2] : 0);
        b[k++] = t[(v >> 18) & 63];
        b[k++] = t[(v >> 12) & 63];
        b[k++] = i + 1 < n ? t[(v >> 6) & 63] : '=';
        b[k++] = i + 2 < n ? t[v & 63] : '=';
        if (k >= 256) {
            escribir_todo(b, k);
            k = 0;
        }
    }
    escribir_todo(b, k);
}

static void json_registro(const registro_t *r)
{
    char o[MAX_TEXTO * 2 + 200];
    size_t u = snprintf(o, sizeof(o), "@@L {\"n\":%lu,\"de\":%d,\"tipo\":%d,\"estado\":%d,\"rssi\":%d,\"ts\":%lld,\"llegada\":%lld,"
                        "\"prog\":%d,\"intentos\":%d,\"seq\":%u,\"leido\":%d,\"recibo\":%d,\"texto\":\"",
                        (unsigned long)r->n, r->de, r->tipo, r->estado, r->rssi, (long long)r->ts, (long long)r->ts_llegada,
                        r->progreso, r->intentos, r->seq, r->leido, r->recibo);
    for (const char *s = r->texto; *s && u + 8 < sizeof(o); s++) {
        if (*s == '"' || *s == '\\') o[u++] = '\\';
        if (*s == '\n') { o[u++] = '\\'; o[u++] = 'n'; continue; }
        o[u++] = *s;
    }
    u += snprintf(o + u, sizeof(o) - u, "\"}\n");
    escribir_todo(o, u);
}

static void tarea(void *arg)
{
    static char linea[512];
    int n = 0;
    for (;;) {
        uint8_t c;
        if (usb_serial_jtag_read_bytes(&c, 1, portMAX_DELAY) != 1) continue;
        if (c == '\r') continue;
        if (c != '\n') {
            if (n < (int)sizeof(linea) - 1) linea[n++] = c;
            continue;
        }
        linea[n] = 0;
        n = 0;
        char r[400];
        if (strncmp(linea, "ella ", 5) == 0) {
            snprintf(r, sizeof(r), "@@OK %lu\n", (unsigned long)enlace_mandar_texto(linea + 5));
            escribir(r);
        } else if (strncmp(linea, "foto ", 5) == 0) {
            size_t largo = strtoul(linea + 5, NULL, 10);
            uint8_t *b = largo && largo <= 96 * 1024 ? malloc(largo) : NULL;
            if (!b) {
                escribir("@@ERROR largo\n");
                continue;
            }
            escribir("@@LISTO\n");
            size_t k = 0;
            while (k < largo) {
                int x = usb_serial_jtag_read_bytes(b + k, largo - k, pdMS_TO_TICKS(5000));
                if (x <= 0) break;
                k += x;
            }
            if (k == largo) snprintf(r, sizeof(r), "@@OK %lu\n", (unsigned long)enlace_mandar_foto(b, largo));
            else snprintf(r, sizeof(r), "@@ERROR llegaron %u de %u\n", (unsigned)k, (unsigned)largo);
            free(b);
            escribir(r);
        } else if (strncmp(linea, "lista", 5) == 0) {
            int k = atoi(linea + 5);
            if (k <= 0) k = 10;
            registro_t *l = malloc(sizeof(registro_t) * MAX_REGISTROS);
            uint32_t ult = almacen_ultimo();
            int m = almacen_desde(ult > (uint32_t)k ? ult - k : 0, l, MAX_REGISTROS);
            for (int i = 0; i < m; i++) json_registro(&l[i]);
            free(l);
            escribir("@@FIN\n");
        } else if (strcmp(linea, "estado") == 0) {
            char d[300];
            enlace_depurar(d, sizeof(d));
            snprintf(r, sizeof(r), "@@E %s,\"ram\":%u,\"psram\":%u,\"seg\":%lld,\"celulares\":%d}\n", d,
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)0,
                     (long long)(esp_timer_get_time() / 1000000), portal_clientes());
            escribir(r);
        } else if (strncmp(linea, "comando ", 8) == 0) {
            char resp[300];
            comandos_ejecutar(linea + 8, resp, sizeof(resp));
            snprintf(r, sizeof(r), "@@C %s\n", resp);
            escribir(r);
        } else if (strcmp(linea, "captura") == 0) {
            // en pedazos numerados de 768 bytes (1024 en base64), para que nada se mezcle con el registro
            const uint8_t *l = (const uint8_t *)pantalla_lienzo();
            int total = ANCHO * ALTO * 2, pedazos = (total + 767) / 768;
            for (int i = 0; i < pedazos; i++) {
                snprintf(r, sizeof(r), "@@P %d %d ", i, pedazos);
                escribir(r);
                int largo = (i + 1) * 768 <= total ? 768 : total - i * 768;
                base64(l + i * 768, largo);
                escribir("\n");
            }
        } else if (strncmp(linea, "at ", 3) == 0) {  // directo al módulo LoRa (modo AT), p.ej. "at AT+POWE10;AT+HELP"
            char resp[380];
            lora_comandos_at(linea + 3, resp, sizeof(resp));
            for (char *p = resp; *p; p++) if (*p == '\n' || *p == '\r') *p = ' ';
            snprintf(r, sizeof(r), "@@A %s\n", resp);
            escribir(r);
        } else if (strcmp(linea, "leer") == 0) {  // como si ella abriera la página y viera todo
            int k = almacen_marcar_leidos(almacen_ultimo());
            if (k) enlace_recibos_ya();
            snprintf(r, sizeof(r), "@@OK %d\n", k);
            escribir(r);
        } else if (strcmp(linea, "bitacora") == 0) {
            enlace_bitacora(escribir);
        } else if (strcmp(linea, "presencia") == 0) {
            char pj[900];
            enlace_presencia_json(pj, sizeof(pj));
            escribir("@@J ");
            escribir(pj);
            escribir("\n");
        } else if (strncmp(linea, "reintentar ", 11) == 0) {
            enlace_reintentar(strtoul(linea + 11, NULL, 10));
            escribir("@@OK\n");
        } else if (strncmp(linea, "prueba tx ", 10) == 0) {  // n transmisiones seguidas a toda potencia
            int n = 0, largo = 0;
            sscanf(linea + 10, "%d %d", &n, &largo);
            enlace_prueba_tx(n, largo);
            escribir("@@OK\n");
        } else if (strcmp(linea, "pines") == 0) {  // cómo ve el C3 los pines del LoRa (M0, M1, AUX)
            snprintf(r, sizeof(r), "@@P M0=%d M1=%d AUX=%d\n", gpio_get_level(3), gpio_get_level(4), gpio_get_level(5));
            escribir(r);
        } else if (strncmp(linea, "aparato ", 8) == 0) {  // un comando por radio al OTRO aparato
            escribir(enlace_mandar_comando(linea + 8) ? "@@OK\n" : "@@ERROR hay otro esperando respuesta\n");
        } else if (strcmp(linea, "respuestas") == 0) {  // latidos, respuestas del otro y radio (lo de la página)
            static char x[3072];
            enlace_extra_json(x, sizeof(x));
            escribir("@@R {");
            escribir(x);
            escribir("}\n");
        } else if (strncmp(linea, "base ", 5) == 0) {
            enlace_prueba_canal_base(atoi(linea + 5));
            escribir("@@OK\n");
        } else if (strcmp(linea, "reiniciar") == 0) {
            escribir("@@OK\n");
            almacen_cerrar();
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
        } else if (linea[0]) {
            escribir("@@ERROR comando desconocido\n");
        }
    }
}

void consola_iniciar(void)
{
    usb_serial_jtag_driver_config_t c = {.tx_buffer_size = 2048, .rx_buffer_size = 2048};
    usb_serial_jtag_driver_install(&c);
    usb_serial_jtag_vfs_use_driver();  // printf/registro también por el driver: las líneas no se mezclan
    xTaskCreate(tarea, "consola", 6144, NULL, 3, NULL);
}
