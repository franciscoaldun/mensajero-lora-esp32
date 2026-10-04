#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/time.h>
#include "portal.h"
#include "almacen.h"
#include "enlace.h"
#include "pantalla.h"
#include "secreto.h"
#include "lora.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "nvs.h"
#include "lwip/sockets.h"

static const char *TAG = "portal";
ajustes_t ajustes;
static int clientes;
static int64_t ultima_visita;
static bool listo;

extern const uint8_t pagina_gz_inicio[] asm("_binary_index_html_gz_start");
extern const uint8_t pagina_gz_fin[] asm("_binary_index_html_gz_end");

// ---------------- ajustes (NVS) ----------------
void ajustes_cargar(void)
{
    strlcpy(ajustes.ssid, WIFI_NOMBRE, sizeof(ajustes.ssid));
    strlcpy(ajustes.clave, WIFI_CLAVE, sizeof(ajustes.clave));
    ajustes.brillo_bajo = 220;
    ajustes.brillo_alto = 1000;
    ajustes.minutos_alto = 10;
    ajustes.girada = false;
    ajustes.colores_al_reves = false;
    ajustes.potencia = 22;
    ajustes.canal = 0x38;
    ajustes.latido_s = 60;  // el corazón late cada minuto (pedido de él)
    ajustes.turbo = true;
    strlcpy(ajustes.otro, "mi amor", sizeof(ajustes.otro));
    nvs_handle_t h;
    if (nvs_open("ajustes", NVS_READONLY, &h) != ESP_OK) return;
    size_t n;  // nombre y clave de la red: siempre los de fábrica (lo guardado antes podía venir roto)
    int32_t v;
    if (nvs_get_i32(h, "bajo", &v) == ESP_OK) ajustes.brillo_bajo = v < BRILLO_BAJO_MIN ? 220 : v;  // lo viejo era casi negro
    if (nvs_get_i32(h, "alto", &v) == ESP_OK) ajustes.brillo_alto = v;
    if (nvs_get_i32(h, "min", &v) == ESP_OK) ajustes.minutos_alto = v;
    ajustes.potencia = 22;  // la potencia del LoRa NO se baja nunca
    ajustes.canal = 0x38;  // canal FIJO: se ignora lo guardado, así nunca quedan en canales distintos
    if (nvs_get_i32(h, "lat", &v) == ESP_OK && v >= 30 && v <= 180) ajustes.latido_s = v == 150 ? 60 : v;  // 150 era el de antes
    uint8_t tb;
    if (nvs_get_u8(h, "turbo", &tb) == ESP_OK) ajustes.turbo = tb;
    n = sizeof(ajustes.otro);
    nvs_get_str(h, "otro", ajustes.otro, &n);
    uint8_t g;
    if (nvs_get_u8(h, "girada", &g) == ESP_OK) ajustes.girada = g;
    if (nvs_get_u8(h, "inv", &g) == ESP_OK) ajustes.colores_al_reves = g;
    nvs_close(h);
}

void ajustes_guardar(void)
{
    nvs_handle_t h;
    if (nvs_open("ajustes", NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_str(h, "ssid", ajustes.ssid);
    nvs_set_str(h, "clave", ajustes.clave);
    nvs_set_i32(h, "bajo", ajustes.brillo_bajo);
    nvs_set_i32(h, "alto", ajustes.brillo_alto);
    nvs_set_i32(h, "min", ajustes.minutos_alto);
    nvs_set_i32(h, "pot", ajustes.potencia);
    nvs_set_i32(h, "canal", ajustes.canal);
    nvs_set_i32(h, "lat", ajustes.latido_s);
    nvs_set_u8(h, "turbo", ajustes.turbo);
    nvs_set_str(h, "otro", ajustes.otro);
    nvs_set_u8(h, "girada", ajustes.girada);
    nvs_set_u8(h, "inv", ajustes.colores_al_reves);
    nvs_commit(h);
    nvs_close(h);
}

int portal_clientes(void) { return clientes; }
int64_t portal_ultima_visita_ms(void) { return ultima_visita; }
bool portal_listo(void) { return listo; }

// ---------------- DNS: todo nombre apunta a nosotros ----------------
static void tarea_dns(void *arg)
{
    int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in dir = {.sin_family = AF_INET, .sin_port = htons(53), .sin_addr.s_addr = htonl(INADDR_ANY)};
    bind(s, (struct sockaddr *)&dir, sizeof(dir));
    uint8_t b[512];
    for (;;) {
        struct sockaddr_in de;
        socklen_t l = sizeof(de);
        int n = recvfrom(s, b, sizeof(b) - 16, 0, (struct sockaddr *)&de, &l);
        if (n < 12) continue;
        int q = 12;  // saltar el nombre preguntado
        while (q < n && b[q]) q += b[q] + 1;
        q += 5;      // 0 final + tipo + clase
        if (q > n) continue;
        b[2] = 0x81; b[3] = 0x80;          // respuesta, sin error
        b[6] = 0; b[7] = 1;                // 1 respuesta
        b[8] = b[9] = b[10] = b[11] = 0;
        uint8_t r[] = {0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4, 192, 168, 4, 1};
        memcpy(b + q, r, sizeof(r));
        sendto(s, b, q + sizeof(r), 0, (struct sockaddr *)&de, l);
    }
}

// ---------------- utilidades HTTP ----------------
// Los ajustes ya no piden clave aparte: los protege la clave del Wi-Fi (más simple, pedido de él)
static bool es_admin(httpd_req_t *req)
{
    (void)req;
    return true;
}

static esp_err_t negar(httpd_req_t *req)
{
    httpd_resp_set_status(req, "403 Forbidden");
    return httpd_resp_sendstr(req, "{\"error\":\"clave\"}");
}

static long param_num(httpd_req_t *req, const char *nombre, long defecto)
{
    char q[160], v[24];
    if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return defecto;
    if (httpd_query_key_value(q, nombre, v, sizeof(v)) != ESP_OK) return defecto;
    return strtol(v, NULL, 10);
}

// lee todo el cuerpo a un buffer en PSRAM
static uint8_t *leer_cuerpo(httpd_req_t *req, size_t maximo, size_t *largo)
{
    if (req->content_len == 0 || req->content_len > maximo) return NULL;
    uint8_t *b = malloc(req->content_len + 1);
    if (!b) return NULL;
    size_t k = 0;
    while (k < req->content_len) {
        int r = httpd_req_recv(req, (char *)b + k, req->content_len - k);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (r <= 0) {
            free(b);
            return NULL;
        }
        k += r;
    }
    b[k] = 0;
    *largo = k;
    return b;
}

static void json_texto(char *o, size_t cap, size_t *u, const char *s)
{
    *u += snprintf(o + *u, *u < cap ? cap - *u : 0, "\"");
    for (; *s && *u + 8 < cap; s++) {
        unsigned char c = *s;
        if (c == '"' || c == '\\') o[(*u)++] = '\\', o[(*u)++] = c;
        else if (c == '\n') o[(*u)++] = '\\', o[(*u)++] = 'n';
        else if (c < 0x20) *u += snprintf(o + *u, cap - *u, "\\u%04x", c);
        else o[(*u)++] = c;
    }
    *u += snprintf(o + *u, *u < cap ? cap - *u : 0, "\"");
}

// ---------------- páginas ----------------
static esp_err_t h_inicio(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "Content-Encoding", "gzip");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, (const char *)pagina_gz_inicio, pagina_gz_fin - pagina_gz_inicio);
}

static esp_err_t h_redirigir(httpd_req_t *req, httpd_err_code_t err)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    return httpd_resp_send(req, "", 0);
}

// Celulares que ya abrieron la página. A los iPhone se les dice "hay internet" después de eso:
// si no, al cerrar la ventanita del portal se desconectan de la red.
static uint32_t visitantes[8];
static uint32_t ip_de(httpd_req_t *req)
{
    struct sockaddr_in6 d;
    socklen_t l = sizeof(d);
    if (getpeername(httpd_req_to_sockfd(req), (struct sockaddr *)&d, &l) != 0) return 0;
    if (d.sin6_family == AF_INET) return ((struct sockaddr_in *)&d)->sin_addr.s_addr;
    return d.sin6_addr.un.u32_addr[3];  // IPv4 dentro de IPv6
}
static bool ya_visito(httpd_req_t *req)
{
    uint32_t ip = ip_de(req);
    for (int i = 0; i < 8; i++) if (ip && visitantes[i] == ip) return true;
    return false;
}
static void anotar_visita(httpd_req_t *req)
{
    uint32_t ip = ip_de(req);
    if (!ip || ya_visito(req)) return;
    static int k;
    visitantes[k++ % 8] = ip;
}

static esp_err_t h_redirigir_get(httpd_req_t *req) { return h_redirigir(req, 0); }

static esp_err_t h_apple(httpd_req_t *req)
{
    if (!ya_visito(req)) return h_redirigir(req, 0);
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_sendstr(req, "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>");
}

static esp_err_t h_estado(httpd_req_t *req)
{
    ultima_visita = esp_timer_get_time() / 1000;
    anotar_visita(req);
    long desde = param_num(req, "desde", 0);
    // se devuelven los nuevos y también los últimos 30 (para que se vean los cambios de estado)
    uint32_t ultimo = almacen_ultimo();
    uint32_t base = 0;  // desde=0: los últimos 40 (el C3 no tiene PSRAM: no cabe todo de una vez)
    if (desde > 0) {
        base = (uint32_t)desde;
        if (base + 30 > ultimo) base = ultimo > 30 ? ultimo - 30 : 0;
    }
    if (ultimo > 40 && base < ultimo - 40) base = ultimo - 40;
    registro_t *lista = malloc(sizeof(registro_t) * 41);
    if (!lista) return httpd_resp_send_500(req);
    int n = almacen_desde(base, lista, 41);
    size_t cap = 6144 + n * (MAX_TEXTO * 2 + 160);
    char *o = malloc(cap);
    if (!o) {
        free(lista);
        return httpd_resp_send_500(req);
    }
    enlace_estado_t e;
    enlace_estado(&e);
    struct timeval tv;
    gettimeofday(&tv, NULL);
    int64_t t = esp_timer_get_time() / 1000;
    size_t u = snprintf(o, cap,
                        "{\"modo\":\"pc\",\"local\":true,\"remoto\":true,\"yo\":%d,\"hora\":%lld,\"lora\":%s,\"modulo\":%s,\"rssi\":%d,\"contacto\":%lld,\"radio_pendiente\":%s,"
                        "\"version\":\"%s\",\"otro\":",
                        DE_ELLA, (long long)(tv.tv_sec > 1700000000 ? tv.tv_sec : 0), e.lora_listo ? "true" : "false",
                        e.lora_conectado ? "true" : "false", e.ultimo_rssi,
                        (long long)(e.ultimo_contacto_ms ? (t - e.ultimo_contacto_ms) / 1000 : -1),
                        e.cambio_radio_pendiente ? "true" : "false", esp_app_get_description()->version);
    json_texto(o, cap, &u, ajustes.otro);
    u += snprintf(o + u, cap - u, ",\"pendientes\":%d,\"presencia\":", e.pendientes);
    enlace_presencia_json(o + u, cap - u);
    u += strlen(o + u);
    u += snprintf(o + u, cap - u, ",\"alcance\":null,\"ajustes_pc\":{\"latido_s\":%d,\"turbo\":%s,\"otro\":",
                  ajustes.latido_s, ajustes.turbo ? "true" : "false");
    json_texto(o, cap, &u, ajustes.otro);
    u += snprintf(o + u, cap - u, "},");
    enlace_extra_json(o + u, cap - u);
    u += strlen(o + u);
    u += snprintf(o + u, cap - u, ",\"mensajes\":[");
    for (int i = 0; i < n && u + MAX_TEXTO * 2 + 200 < cap; i++) {
        registro_t *r = &lista[i];
        u += snprintf(o + u, cap - u, "%s{\"n\":%lu,\"de\":%d,\"tipo\":%d,\"estado\":%d,\"rssi\":%d,\"ts\":%lld,\"llegada\":%lld,"
                      "\"prog\":%d,\"leido\":%d,\"intentos\":%d,\"texto\":",
                      i ? "," : "", (unsigned long)r->n, r->de, r->tipo, r->estado, r->rssi, (long long)r->ts,
                      (long long)r->ts_llegada, r->progreso, r->leido, r->intentos);
        json_texto(o, cap, &u, r->texto);
        o[u++] = '}';
    }
    u += snprintf(o + u, cap - u, "]}");
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t r = httpd_resp_send(req, o, u);
    free(o);
    free(lista);
    return r;
}

static esp_err_t h_enviar(httpd_req_t *req)
{
    size_t largo;
    uint8_t *b = leer_cuerpo(req, 600, &largo);
    if (!b) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "texto vacío o muy largo");
    if (largo > MAX_TEXTO) b[MAX_TEXTO] = 0;
    uint32_t n = enlace_mandar_texto((const char *)b);
    free(b);
    char r[32];
    snprintf(r, sizeof(r), "{\"n\":%lu}", (unsigned long)n);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, r);
}

static esp_err_t h_subir_foto(httpd_req_t *req)
{
    size_t largo;
    uint8_t *b = leer_cuerpo(req, 96 * 1024, &largo);
    if (!b) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "foto vacía o muy grande");
    if (largo < 4 || b[0] != 0xFF || b[1] != 0xD8) {
        free(b);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "no es JPEG");
    }
    uint32_t n = enlace_mandar_foto(b, largo);
    free(b);
    char r[32];
    snprintf(r, sizeof(r), "{\"n\":%lu}", (unsigned long)n);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, r);
}

static esp_err_t h_ver_foto(httpd_req_t *req)
{
    size_t largo;
    uint8_t *b = almacen_leer_foto(param_num(req, "n", 0), &largo);
    if (!b) return httpd_resp_send_404(req);
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "max-age=31536000");
    esp_err_t r = httpd_resp_send(req, (const char *)b, largo);
    free(b);
    return r;
}

static esp_err_t h_leido(httpd_req_t *req)
{
    if (almacen_marcar_leidos(param_num(req, "hasta", 0)) > 0) enlace_recibos_ya();
    return httpd_resp_sendstr(req, "{}");
}

static esp_err_t h_reintentar(httpd_req_t *req)
{
    enlace_reintentar(param_num(req, "n", 0));
    return httpd_resp_sendstr(req, "{}");
}

static esp_err_t h_hora(httpd_req_t *req)
{
    long t = param_num(req, "t", 0);
    struct timeval ahora;
    gettimeofday(&ahora, NULL);
    if (t > 1700000000 && (ahora.tv_sec < 1700000000 || llabs((long long)ahora.tv_sec - t) > 60)) {
        struct timeval tv = {.tv_sec = t};  // el celular sabe la hora: se la copiamos
        settimeofday(&tv, NULL);
    }
    if (t == 0) enlace_mandar_hora();  // botón "su hora = la tuya": se la manda al aparato de ella
    return httpd_resp_sendstr(req, "{}");
}

// ---------------- panel de él: el aparato de ella se controla por radio ----------------
static esp_err_t h_aparato(httpd_req_t *req)
{
    size_t largo;
    uint8_t *b = leer_cuerpo(req, 95, &largo);
    if (!b) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "comando vacío o largo");
    bool ok = enlace_mandar_comando((const char *)b);
    free(b);
    return httpd_resp_sendstr(req, ok ? "{}" : "{\"error\":\"espera la respuesta anterior\"}");
}

static esp_err_t h_ajustes_pc(httpd_req_t *req)
{
    size_t largo;
    uint8_t *b = leer_cuerpo(req, 300, &largo);
    if (!b) return httpd_resp_sendstr(req, "{}");
    char v[64];
    if (httpd_query_key_value((char *)b, "latido", v, sizeof(v)) == ESP_OK) {
        int s = atoi(v);
        if (s >= 30 && s <= 180) ajustes.latido_s = s;  // nunca más de 3 min sin darse la mano
    }
    if (httpd_query_key_value((char *)b, "turbo", v, sizeof(v)) == ESP_OK) ajustes.turbo = v[0] == '1';
    if (httpd_query_key_value((char *)b, "otro", v, sizeof(v)) == ESP_OK && v[0]) {
        // viene en formato de formulario: '+' = espacio y %XX
        char d[64];
        int k = 0;
        for (int i = 0; v[i] && k < (int)sizeof(d) - 1; i++) {
            if (v[i] == '+') d[k++] = ' ';
            else if (v[i] == '%' && v[i + 1] && v[i + 2]) {
                char h[3] = {v[i + 1], v[i + 2], 0};
                d[k++] = (char)strtol(h, NULL, 16);
                i += 2;
            } else d[k++] = v[i];
        }
        d[k] = 0;
        strlcpy(ajustes.otro, d, sizeof(ajustes.otro));
    }
    free(b);
    ajustes_guardar();
    return httpd_resp_sendstr(req, "{}");
}

static esp_err_t h_alcance(httpd_req_t *req) { return httpd_resp_sendstr(req, "{}"); }

// ---------------- administración ----------------
static esp_err_t h_ajustes_ver(httpd_req_t *req)
{
    if (!es_admin(req)) return negar(req);
    size_t total, usado;
    almacen_espacio(&total, &usado);
    const esp_partition_t *p = esp_ota_get_running_partition();
    char o[600];
    size_t u = snprintf(o, sizeof(o), "{\"ssid\":");
    json_texto(o, sizeof(o), &u, ajustes.ssid);
    u += snprintf(o + u, sizeof(o) - u, ",\"clave\":");
    json_texto(o, sizeof(o), &u, ajustes.clave);
    u += snprintf(o + u, sizeof(o) - u, ",\"otro\":");
    json_texto(o, sizeof(o), &u, ajustes.otro);
    u += snprintf(o + u, sizeof(o) - u,
                  ",\"lat\":%d,\"turbo\":%s,\"canal\":%d,\"pot\":%d,\"bajo\":%d,\"alto\":%d,\"min\":%d,\"girada\":%s,\"flash_total\":%u,\"flash_usado\":%u,"
                  "\"encendida_s\":%lld,\"particion\":\"%s\",\"ram_libre\":%u,\"psram_libre\":%u}",
                  ajustes.latido_s, ajustes.turbo ? "true" : "false", ajustes.canal, ajustes.potencia, ajustes.brillo_bajo, ajustes.brillo_alto, ajustes.minutos_alto, ajustes.girada ? "true" : "false",
                  (unsigned)total, (unsigned)usado, (long long)(esp_timer_get_time() / 1000000), p->label,
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)0);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, o, u);
}

static void reiniciar_luego(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(1500));
    almacen_cerrar();
    esp_restart();
}

// "mi%20amor" / "mi+amor" -> "mi amor" (la página manda todo urlencoded)
static void decodificar_url(char *s)
{
    char *o = s;
    for (char *p = s; *p; p++) {
        if (*p == '+') {
            *o++ = ' ';
        } else if (*p == '%' && p[1] && p[2]) {
            char h[3] = {p[1], p[2], 0};
            *o++ = (char)strtol(h, NULL, 16);
            p += 2;
        } else {
            *o++ = *p;
        }
    }
    *o = 0;
}

static esp_err_t h_ajustes_guardar(httpd_req_t *req)
{
    if (!es_admin(req)) return negar(req);
    size_t largo;
    uint8_t *b = leer_cuerpo(req, 1024, &largo);
    if (!b) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "vacío");
    char v[72];
    bool wifi_cambio = false;  // el nombre y la clave de la red ya no se cambian desde la página (quedan fijos)
    if (httpd_query_key_value((char *)b, "bajo", v, sizeof(v)) == ESP_OK) ajustes.brillo_bajo = atoi(v);
    if (httpd_query_key_value((char *)b, "alto", v, sizeof(v)) == ESP_OK) ajustes.brillo_alto = atoi(v);
    if (httpd_query_key_value((char *)b, "min", v, sizeof(v)) == ESP_OK) ajustes.minutos_alto = atoi(v);
    if (httpd_query_key_value((char *)b, "lat", v, sizeof(v)) == ESP_OK && atoi(v) >= 30 && atoi(v) <= 900) ajustes.latido_s = atoi(v);
    if (httpd_query_key_value((char *)b, "turbo", v, sizeof(v)) == ESP_OK) ajustes.turbo = v[0] == '1';
    if (httpd_query_key_value((char *)b, "otro", v, sizeof(v)) == ESP_OK && v[0]) {
        decodificar_url(v);
        if (v[0]) strlcpy(ajustes.otro, v, sizeof(ajustes.otro));
    }
    if (httpd_query_key_value((char *)b, "girada", v, sizeof(v)) == ESP_OK) {
        ajustes.girada = v[0] == '1';
        pantalla_dar_vuelta(ajustes.girada);
    }
    free(b);
    ajustes_guardar();
    httpd_resp_sendstr(req, wifi_cambio ? "{\"reinicia\":true}" : "{\"reinicia\":false}");
    if (wifi_cambio) xTaskCreate(reiniciar_luego, "reinicio", 3072, NULL, 5, NULL);
    return ESP_OK;
}

static esp_err_t h_borrar(httpd_req_t *req)
{
    if (!es_admin(req)) return negar(req);
    char r[120];
    comandos_ejecutar("borrar historial", r, sizeof(r));
    return httpd_resp_sendstr(req, "{}");
}

static esp_err_t h_reiniciar(httpd_req_t *req)
{
    if (!es_admin(req)) return negar(req);
    httpd_resp_sendstr(req, "{}");
    xTaskCreate(reiniciar_luego, "reinicio", 3072, NULL, 5, NULL);
    return ESP_OK;
}

// Actualización por Wi-Fi. Se revisa que el archivo sea de ESTE proyecto antes de aceptarlo.
// Si la versión nueva no llega a marcarse como buena (main.c, al minuto), el arranque vuelve a la anterior.
static esp_err_t h_ota(httpd_req_t *req)
{
    if (!es_admin(req)) return negar(req);
    const esp_partition_t *destino = esp_ota_get_next_update_partition(NULL);
    if (!destino || req->content_len < 64 * 1024 || req->content_len > destino->size)
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "tamaño inválido");
    char *b = heap_caps_malloc(8192, MALLOC_CAP_INTERNAL);
    esp_ota_handle_t ota = 0;
    size_t recibido = 0;
    bool revisado = false, ok = true;
    const char *motivo = "";
    while (recibido < req->content_len && ok) {
        int r = httpd_req_recv(req, b, 8192);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (r <= 0) {
            ok = false;
            motivo = "se cortó la subida";
            break;
        }
        if (!revisado) {
            size_t pos = sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t);
            if ((size_t)r < pos + sizeof(esp_app_desc_t)) {
                ok = false;
                motivo = "archivo raro";
                break;
            }
            const esp_app_desc_t *d = (const esp_app_desc_t *)(b + pos);
            if (d->magic_word != ESP_APP_DESC_MAGIC_WORD || strcmp(d->project_name, esp_app_get_description()->project_name) != 0) {
                ok = false;
                motivo = "ese archivo no es el firmware del mensajero";
                break;
            }
            if (esp_ota_begin(destino, OTA_WITH_SEQUENTIAL_WRITES, &ota) != ESP_OK) {
                ok = false;
                motivo = "no se pudo empezar";
                break;
            }
            revisado = true;
        }
        if (esp_ota_write(ota, b, r) != ESP_OK) {
            ok = false;
            motivo = "falló la escritura";
        }
        recibido += r;
    }
    free(b);
    if (revisado && ok) {
        if (esp_ota_end(ota) != ESP_OK) {
            ok = false;
            motivo = "la imagen no pasó la verificación";
        } else if (esp_ota_set_boot_partition(destino) != ESP_OK) {
            ok = false;
            motivo = "no se pudo elegir la partición";
        }
    } else if (revisado) {
        esp_ota_abort(ota);
    }
    if (!ok) {
        ESP_LOGE(TAG, "OTA rechazada: %s", motivo);
        char m[120];
        snprintf(m, sizeof(m), "{\"ok\":false,\"motivo\":\"%s\"}", motivo);
        httpd_resp_set_type(req, "application/json");
        return httpd_resp_sendstr(req, m);
    }
    ESP_LOGW(TAG, "OTA lista (%u bytes), reiniciando", (unsigned)recibido);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"ok\":true}");
    xTaskCreate(reiniciar_luego, "reinicio", 3072, NULL, 5, NULL);
    return ESP_OK;
}

// ---------------- Wi-Fi ----------------
static void al_evento_wifi(void *arg, esp_event_base_t base, int32_t id, void *datos)
{
    if (id == WIFI_EVENT_AP_STACONNECTED) clientes++;
    else if (id == WIFI_EVENT_AP_STADISCONNECTED && clientes > 0) clientes--;
}

void portal_iniciar(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *ap = esp_netif_create_default_wifi_ap();
    wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&ic));
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, al_evento_wifi, NULL);

    wifi_config_t c = {0};
    size_t l = strlen(ajustes.ssid);
    memcpy(c.ap.ssid, ajustes.ssid, l);
    c.ap.ssid_len = l;
    c.ap.channel = 6;
    c.ap.max_connection = 4;
    if (strlen(ajustes.clave) >= 8) {
        strlcpy((char *)c.ap.password, ajustes.clave, sizeof(c.ap.password));
        c.ap.authmode = WIFI_AUTH_WPA2_PSK;
    } else {
        c.ap.authmode = WIFI_AUTH_OPEN;
    }
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &c));

    // Avisa a los celulares modernos dónde está la página (DHCP opción 114)
    static char uri[] = "http://192.168.4.1/";
    esp_netif_dhcps_stop(ap);
    esp_netif_dhcps_option(ap, ESP_NETIF_OP_SET, ESP_NETIF_CAPTIVEPORTAL_URI, uri, strlen(uri));
    esp_netif_dhcps_start(ap);

    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_max_tx_power(44);  // 11 dBm: el celular de él va en el bolsillo, cerca  // 8,5 dBm: el celular de ella está al lado; menos corriente de golpe
                                    // (sumada a la radio a 22 dBm, la caída de voltaje colgaba el S3)
    xTaskCreate(tarea_dns, "dns", 3072, NULL, 4, NULL);

    httpd_handle_t s;
    httpd_config_t hc = HTTPD_DEFAULT_CONFIG();
    hc.max_uri_handlers = 28;
    hc.max_open_sockets = 7;  // el C3 tiene menos sockets (12 en total, 3 los usa el servidor)
    hc.lru_purge_enable = true;
    hc.stack_size = 8192;
    hc.recv_wait_timeout = 15;
    hc.send_wait_timeout = 15;
    ESP_ERROR_CHECK(httpd_start(&s, &hc));
    const httpd_uri_t rutas[] = {
        {.uri = "/", .method = HTTP_GET, .handler = h_inicio},
        {.uri = "/api/estado", .method = HTTP_GET, .handler = h_estado},
        {.uri = "/api/enviar", .method = HTTP_POST, .handler = h_enviar},
        {.uri = "/api/foto", .method = HTTP_POST, .handler = h_subir_foto},
        {.uri = "/foto", .method = HTTP_GET, .handler = h_ver_foto},
        {.uri = "/api/reintentar", .method = HTTP_POST, .handler = h_reintentar},
        {.uri = "/api/leido", .method = HTTP_POST, .handler = h_leido},
        {.uri = "/api/hora", .method = HTTP_POST, .handler = h_hora},
        {.uri = "/api/aparato", .method = HTTP_POST, .handler = h_aparato},
        {.uri = "/api/ajustes_pc", .method = HTTP_POST, .handler = h_ajustes_pc},
        {.uri = "/api/alcance", .method = HTTP_POST, .handler = h_alcance},
        {.uri = "/api/ajustes", .method = HTTP_GET, .handler = h_ajustes_ver},
        {.uri = "/api/ajustes", .method = HTTP_POST, .handler = h_ajustes_guardar},
        {.uri = "/api/reiniciar", .method = HTTP_POST, .handler = h_reiniciar},
        {.uri = "/api/borrar", .method = HTTP_POST, .handler = h_borrar},
        {.uri = "/api/ota", .method = HTTP_POST, .handler = h_ota},
        {.uri = "/generate_204", .method = HTTP_GET, .handler = h_redirigir_get},
        {.uri = "/hotspot-detect.html", .method = HTTP_GET, .handler = h_apple},
        {.uri = "/library/test/success.html", .method = HTTP_GET, .handler = h_apple},
    };
    for (size_t i = 0; i < sizeof(rutas) / sizeof(rutas[0]); i++) httpd_register_uri_handler(s, &rutas[i]);
    httpd_register_err_handler(s, HTTPD_404_NOT_FOUND, h_redirigir);
    listo = true;
    ESP_LOGI(TAG, "red \"%s\" lista en 192.168.4.1", ajustes.ssid);
}
