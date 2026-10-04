#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>
#include "almacen.h"
#include "esp_littlefs.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "almacen";
#define RAIZ "/datos"
#define ARCHIVO RAIZ "/historial.bin"
#define TEMPORAL RAIZ "/historial.tmp"
#define VERSION_ARCHIVO 2

static registro_t *regs;   // PSRAM, ordenado: regs[0] el más antiguo
static int cantidad;
static uint32_t siguiente_n = 1;
// Aparato de ÉL: sus mensajes se numeran desde 30000. Ella reconoce los repetidos por número, y el PC de él
// (el otro "él") usa números bajos: si chocaran, ella tomaría los del C3 por repetidos y los descartaría.
#define SEQ_DESDE 30000
static uint16_t siguiente_seq = SEQ_DESDE;
static SemaphoreHandle_t cerrojo;

void almacen_bloquear(void) { xSemaphoreTakeRecursive(cerrojo, portMAX_DELAY); }
void almacen_soltar(void) { xSemaphoreGiveRecursive(cerrojo); }

// Grabar el historial entero tarda (S3: ~72 KB, del orden de 1 s). Antes se grababa en el momento y bajo el
// cerrojo: con una ráfaga de mensajes la radio y la consola quedaban esperando la flash por segundos (03-oct).
// Ahora un cambio sólo avisa; una tarea aparte copia el historial en un instante y lo graba sin trabar a nadie.
// Los cambios que llegan juntos (un G de 10 mensajes) quedan en una sola grabación.
static SemaphoreHandle_t cerrojo_flash;
static TaskHandle_t grabador;
static registro_t *copia;
static volatile bool sucio;

static void grabar_ya(void)
{
    xSemaphoreTake(cerrojo_flash, portMAX_DELAY);
    almacen_bloquear();
    sucio = false;
    int k = cantidad;
    uint32_t cab[4] = {VERSION_ARCHIVO, (uint32_t)k, siguiente_n, siguiente_seq};
    memcpy(copia, regs, sizeof(registro_t) * k);
    almacen_soltar();
    FILE *f = fopen(TEMPORAL, "wb");
    if (!f) {
        ESP_LOGE(TAG, "no se pudo escribir el historial");
        xSemaphoreGive(cerrojo_flash);
        return;
    }
    bool ok = fwrite(cab, sizeof(cab), 1, f) == 1;
    ok &= fwrite(copia, sizeof(registro_t), k, f) == (size_t)k;
    ok &= fclose(f) == 0;
    if (!ok) {  // flash llena o error: se conserva el archivo anterior intacto
        ESP_LOGE(TAG, "el historial no se alcanzó a grabar completo; queda el anterior");
        remove(TEMPORAL);
    } else {
        rename(TEMPORAL, ARCHIVO);  // LittleFS lo reemplaza de una: un corte de luz no deja el historial roto
    }
    xSemaphoreGive(cerrojo_flash);
}

static void tarea_grabador(void *arg)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        vTaskDelay(pdMS_TO_TICKS(120));  // que se junten los cambios que vienen seguidos
        while (sucio) grabar_ya();
    }
}

static void guardar(void)
{
    sucio = true;
    if (grabador) xTaskNotifyGive(grabador);
    else grabar_ya();
}

// Formato de la versión 1 (antes de los números de mensaje): se convierte al cargar.
typedef struct {
    uint32_t n;
    uint8_t de, tipo, estado;
    int8_t rssi;
    int64_t ts;
    uint16_t progreso;
    uint8_t intentos;
    char texto[MAX_TEXTO + 1];
} registro_v1_t;

static void cargar(void)
{
    FILE *f = fopen(ARCHIVO, "rb");
    if (!f) f = fopen(TEMPORAL, "rb");
    if (!f) return;
    uint32_t cab[3];
    if (fread(cab, sizeof(cab), 1, f) == 1 && cab[1] <= MAX_REGISTROS) {
        if (cab[0] == VERSION_ARCHIVO) {
            uint32_t seq;
            if (fread(&seq, sizeof(seq), 1, f) == 1) {
                siguiente_seq = seq >= SEQ_DESDE ? seq : SEQ_DESDE;
                cantidad = fread(regs, sizeof(registro_t), cab[1], f);
                siguiente_n = cab[2];
            }
        } else if (cab[0] == 1) {
            registro_v1_t v;
            for (uint32_t i = 0; i < cab[1] && fread(&v, sizeof(v), 1, f) == 1; i++) {
                registro_t *r = &regs[cantidad++];
                memset(r, 0, sizeof(*r));
                r->n = v.n;
                r->de = v.de;
                r->tipo = v.tipo;
                r->estado = v.estado;
                r->rssi = v.rssi;
                r->ts = v.ts;
                r->progreso = v.progreso;
                r->intentos = v.intentos;
                r->recibo = 1;               // lo viejo no pide recibos de lectura
                r->leido = r->de == DE_EL;   // lo que mandó ella NO se da por leído (no se sabe)
                memcpy(r->texto, v.texto, sizeof(r->texto));
            }
            siguiente_n = cab[2];
            ESP_LOGW(TAG, "historial convertido al formato nuevo (%d mensajes)", cantidad);
        }
    }
    fclose(f);
    for (int i = 0; i < cantidad; i++) {
        if (regs[i].estado == ENVIANDO) regs[i].estado = FALLIDO;  // lo que quedó a medio mandar al apagarse
        if (regs[i].de == DE_ELLA && regs[i].seq == 0) regs[i].leido = 0;  // sin número no hay recibo: no se sabe
    }
    ESP_LOGI(TAG, "%d mensajes guardados", cantidad);
}

void almacen_iniciar(void)
{
    cerrojo = xSemaphoreCreateRecursiveMutex();
    regs = calloc(MAX_REGISTROS, sizeof(registro_t));
    assert(regs);
    copia = calloc(MAX_REGISTROS, sizeof(registro_t));
    assert(copia);
    cerrojo_flash = xSemaphoreCreateMutex();
    esp_vfs_littlefs_conf_t c = {
        .base_path = RAIZ, .partition_label = "datos", .format_if_mount_failed = true, .dont_mount = false,
    };
    esp_err_t e = esp_vfs_littlefs_register(&c);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "no se pudo montar la flash de datos (%s)", esp_err_to_name(e));
        return;
    }
    mkdir(RAIZ "/fotos", 0755);
    cargar();
    xTaskCreate(tarea_grabador, "grabador", 4096, NULL, 2, &grabador);
}

void almacen_cerrar(void)
{
    grabar_ya();  // antes de reiniciar: grabado en el momento
}

void almacen_ruta_foto(uint32_t n, char *ruta, size_t largo)
{
    snprintf(ruta, largo, RAIZ "/fotos/%lu.jpg", (unsigned long)n);
}

uint32_t almacen_agregar(const registro_t *r)
{
    almacen_bloquear();
    if (cantidad == MAX_REGISTROS) {  // se va el más antiguo (y su foto)
        if (regs[0].tipo == ES_FOTO) {
            char ruta[48];
            almacen_ruta_foto(regs[0].n, ruta, sizeof(ruta));
            remove(ruta);
        }
        memmove(&regs[0], &regs[1], sizeof(registro_t) * (MAX_REGISTROS - 1));
        cantidad--;
    }
    regs[cantidad] = *r;
    regs[cantidad].n = siguiente_n++;
    if (r->de == DE_ELLA && r->seq == 0) {  // los mensajes de ella llevan su número propio
        regs[cantidad].seq = siguiente_seq++;
        if (siguiente_seq == 0) siguiente_seq = SEQ_DESDE;
    }
    uint32_t n = regs[cantidad].n;
    cantidad++;
    guardar();
    almacen_soltar();
    return n;
}

static registro_t *buscar(uint32_t n)
{
    for (int i = cantidad - 1; i >= 0; i--)
        if (regs[i].n == n) return &regs[i];
    return NULL;
}

bool almacen_obtener(uint32_t n, registro_t *r)
{
    almacen_bloquear();
    registro_t *x = buscar(n);
    if (x) *r = *x;
    almacen_soltar();
    return x != NULL;
}

void almacen_actualizar(uint32_t n, uint8_t estado, uint16_t progreso)
{
    almacen_bloquear();
    registro_t *x = buscar(n);
    if (x) {
        bool cambio_estado = x->estado != estado;
        x->estado = estado;
        x->progreso = progreso;
        if (cambio_estado) guardar();  // el avance de una foto no se graba a cada rato
    }
    almacen_soltar();
}

bool almacen_modificar(uint32_t n, almacen_cambio_t f, void *arg, bool grabar)
{
    almacen_bloquear();
    registro_t *x = buscar(n);
    if (x) {
        f(x, arg);
        if (grabar) guardar();
    }
    almacen_soltar();
    return x != NULL;
}

bool almacen_buscar_seq(uint8_t de, uint16_t seq, registro_t *r)
{
    if (!seq) return false;
    almacen_bloquear();
    bool hay = false;
    for (int i = cantidad - 1; i >= 0 && !hay; i--)
        if (regs[i].de == de && regs[i].seq == seq) {
            *r = regs[i];
            hay = true;
        }
    almacen_soltar();
    return hay;
}

int almacen_desde(uint32_t desde_n, registro_t *salida, int max)
{
    almacen_bloquear();
    int k = 0;
    for (int i = 0; i < cantidad && k < max; i++)
        if (regs[i].n > desde_n) salida[k++] = regs[i];
    almacen_soltar();
    return k;
}

uint32_t almacen_ultimo(void)
{
    almacen_bloquear();
    uint32_t n = cantidad ? regs[cantidad - 1].n : 0;
    almacen_soltar();
    return n;
}

// Un mensaje de ella sin número (de antes de que existieran) recibe uno al reintentarse: así nunca se repite.
uint16_t almacen_dar_seq(uint32_t n)
{
    almacen_bloquear();
    registro_t *x = buscar(n);
    uint16_t s = 0;
    if (x) {
        if (!x->seq && x->de == DE_ELLA) {
            x->seq = siguiente_seq++;
            if (siguiente_seq == 0) siguiente_seq = SEQ_DESDE;
            guardar();
        }
        s = x->seq;
    }
    almacen_soltar();
    return s;
}

int almacen_pendientes(void)
{
    almacen_bloquear();
    int k = 0;
    for (int i = 0; i < cantidad; i++)
        k += regs[i].de == DE_ELLA && (regs[i].estado == ENVIANDO || (regs[i].estado == FALLIDO && regs[i].intentos < 255));
    almacen_soltar();
    return k;
}

int almacen_fallidos(uint32_t *ns, int max)
{
    almacen_bloquear();
    int k = 0;
    for (int i = 0; i < cantidad && k < max; i++)
        if (regs[i].de == DE_ELLA && regs[i].estado == FALLIDO && regs[i].intentos < 255) ns[k++] = regs[i].n;
    almacen_soltar();
    return k;
}

int almacen_contar(uint8_t de, uint8_t estado)
{
    almacen_bloquear();
    int k = 0;
    for (int i = 0; i < cantidad; i++) k += regs[i].de == de && regs[i].estado == estado;
    almacen_soltar();
    return k;
}

int almacen_marcar_leidos(uint32_t hasta_n)
{
    almacen_bloquear();
    int k = 0;
    for (int i = 0; i < cantidad; i++) {
        registro_t *r = &regs[i];
        if (r->n <= hasta_n && r->de == DE_EL && r->estado == RECIBIDO && !r->leido) {
            r->leido = 1;
            r->recibo = r->seq ? 0 : 1;  // sin número no hay cómo avisarle (mensajes viejos)
            k++;
        }
    }
    if (k) guardar();
    almacen_soltar();
    return k;
}

int almacen_recibos_pendientes(uint16_t *seqs, int max)
{
    almacen_bloquear();
    int k = 0;
    for (int i = 0; i < cantidad && k < max; i++)
        if (regs[i].de == DE_EL && regs[i].leido && !regs[i].recibo && regs[i].seq) seqs[k++] = regs[i].seq;
    almacen_soltar();
    return k;
}

void almacen_recibos_entregados(const uint16_t *seqs, int k)
{
    almacen_bloquear();
    bool cambio = false;
    for (int i = 0; i < cantidad; i++)
        if (regs[i].de == DE_EL && !regs[i].recibo)
            for (int j = 0; j < k; j++)
                if (regs[i].seq == seqs[j]) {
                    regs[i].recibo = 1;
                    cambio = true;
                }
    if (cambio) guardar();
    almacen_soltar();
}

int almacen_leidos_por_el(const uint16_t *seqs, int k)
{
    almacen_bloquear();
    int cambio = 0;
    for (int i = 0; i < cantidad; i++)
        if (regs[i].de == DE_ELLA && !regs[i].leido)
            for (int j = 0; j < k; j++)
                if (regs[i].seq == seqs[j]) {
                    regs[i].leido = 1;
                    if (regs[i].estado != ENTREGADO) {  // si lo leyó, le llegó (aunque el acuse se haya perdido)
                        regs[i].estado = ENTREGADO;
                        regs[i].progreso = 1000;
                    }
                    cambio++;
                }
    if (cambio) guardar();
    almacen_soltar();
    return cambio;
}

// Lo último de alguien que vale la pena mostrar: se saltan las fotos que quedaron cortadas.
bool almacen_ultimo_mostrable(uint8_t de, registro_t *r)
{
    almacen_bloquear();
    bool hay = false;
    for (int i = cantidad - 1; i >= 0 && !hay; i--)
        if (regs[i].de == de && !(regs[i].tipo == ES_FOTO && regs[i].estado == FALLIDO)) {
            *r = regs[i];
            hay = true;
        }
    almacen_soltar();
    return hay;
}

bool almacen_ultimo_de(uint8_t de, registro_t *r)
{
    almacen_bloquear();
    bool hay = false;
    for (int i = cantidad - 1; i >= 0 && !hay; i--)
        if (regs[i].de == de) {
            *r = regs[i];
            hay = true;
        }
    almacen_soltar();
    return hay;
}

bool almacen_guardar_foto(uint32_t n, const uint8_t *datos, size_t largo)
{
    char ruta[48];
    almacen_ruta_foto(n, ruta, sizeof(ruta));
    FILE *f = fopen(ruta, "wb");
    if (!f) return false;
    bool ok = fwrite(datos, 1, largo, f) == largo;
    ok &= fclose(f) == 0;
    if (!ok) remove(ruta);
    return ok;
}

uint8_t *almacen_leer_foto(uint32_t n, size_t *largo)
{
    char ruta[48];
    almacen_ruta_foto(n, ruta, sizeof(ruta));
    struct stat st;
    if (stat(ruta, &st) != 0 || st.st_size <= 0 || st.st_size > 512 * 1024) return NULL;
    uint8_t *b = malloc(st.st_size);
    if (!b) return NULL;
    FILE *f = fopen(ruta, "rb");
    size_t k = f ? fread(b, 1, st.st_size, f) : 0;
    if (f) fclose(f);
    if (k != (size_t)st.st_size) {
        free(b);
        return NULL;
    }
    *largo = k;
    return b;
}

void almacen_espacio(size_t *total, size_t *usado)
{
    *total = *usado = 0;
    esp_littlefs_info("datos", total, usado);
}

// Deja el historial vacío (p.ej. antes de entregarlo, para que no se vean los mensajes de prueba).
// La numeración NO vuelve a cero: el celular guarda las fotos en su memoria según el número.
void almacen_borrar_todo(void)
{
    almacen_bloquear();
    for (int i = 0; i < cantidad; i++)
        if (regs[i].tipo == ES_FOTO) {
            char ruta[48];
            almacen_ruta_foto(regs[i].n, ruta, sizeof(ruta));
            remove(ruta);
        }
    cantidad = 0;
    guardar();
    almacen_soltar();
}

void almacen_contar_intento(uint32_t n)
{
    almacen_bloquear();
    registro_t *x = buscar(n);
    if (x && x->intentos < 254) x->intentos++;  // 255 = "olvidado"
    almacen_soltar();
}
