// APARATO PORTÁTIL DE ÉL (ESP32-C3): la misma radio que el aparato de ella (enlace.c del S3, ya probado), con lo
// que el PC de él hace distinto: tras un choque espera un paquete más (lo de ella pasa primero), si los dos
// empiezan una foto a la vez él cede, y además manda comandos y la hora al aparato de ella.
// Todo lo que pasa por la radio. Es un aparato de supervivencia, así que las reglas son:
//  - Nada se pierde: lo de ella que no llega se reintenta PARA SIEMPRE: apenas hay contacto y, mientras
//    están cerca, cada minuto. Lejos no se gasta el aire: el latido hace de sonda.
//  - Nada se repite: cada mensaje lleva su número (seq); el que recibe reconoce los repetidos aunque
//    se haya perdido un acuse y el mensaje vuelva a llegar días después.
//  - Nada es ambiguo: ✓✓ = llegó; leído = lo abrió. Los recibos de lectura también se reintentan.
//  - El latido (cada pocos minutos) dice si el otro está cerca. Al reencontrarse se contesta al tiro
//    y se manda todo lo pendiente.
//  - Turbo: sólo para fotos y sólo con señal de sobra en los dos sentidos. Ante cualquier duda (silencio,
//    reinicio, rechazo, dos consultas sin respuesta) se vuelve al nivel 0, el de máximo alcance.
//  - Fotos que se cortan (se alejaron, se reinició algo): el que recibe guarda lo que llegó y, cuando
//    vuelve la señal, la foto sigue desde donde quedó en vez de empezar de cero.
//  - Turnos: la radio no puede hablar y escuchar a la vez. Nadie empieza a hablar si el otro está
//    hablando. Durante una foto se sigue conversando: quien la manda intercala sus propios mensajes entre
//    trozos, y cada 4 trozos le pregunta al otro "¿vas bien?" (K); el otro contesta qué le falta y si tiene
//    un mensaje esperando, y sólo entonces se le da el turno. Así no se pierde tiempo en pausas vacías.
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/time.h>
#include "enlace.h"
#include "almacen.h"
#include "lora.h"
#include "protocolo.h"
#include "portal.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_task_wdt.h"

static const char *TAG = "enlace";

#define INTENTOS_TEXTO 5
#define INTENTOS_FOTO_INICIO 5
#define RONDAS_FOTO 12
#define MAX_FOTO (96 * 1024)
#define MS_ABANDONAR_FOTO_RX (24LL * 3600 * 1000)  // lo que llegó de una foto cortada se guarda 1 día
#define MS_FOTO_EN_PAUSA 120000              // sin trozos nuevos por 2 min: la foto queda "a medias"
#define MS_FOTO_PARADA 45000                 // una foto que no avanza ya no bloquea el turno
#define MS_REVERTIR_RADIO (120 * 1000)       // cambio de canal sin contacto: se vuelve
#define MS_REINTENTO_CERCA 6000              // lo que no llegó: mientras el otro está presente, al tiro
#define MS_PRESENTE 90000                    // oído hace menos = presente (se le insiste); si no, se espera
#define MS_SILENCIO 2500                     // si el otro habló hace menos, no se le interrumpe
#define TROZOS_POR_CONSULTA 4             // cada cuántos trozos se le pregunta al otro cómo va
#define MS_VENTANA (ms_en_aire(11 + MAX_TEXTO) + 700)  // lo que se le espera cuando dice "tengo un mensaje"
#define MS_LATIDO_PERDIDO 4000               // buscándose: cada ~4 s (2 a 6 al azar). Más seguido es PEOR:
                                             // un latido dura 0,73 s en el aire y los dos chocarían
#define MS_REVISAR_RADIO_PERDIDOS (10 * 60 * 1000)  // perdidos: cada tanto se revisa que la radio esté como debe
#define MS_TURBO_SILENCIO 20000              // en turbo, 20 s sin oír nada = volver al nivel 0
#define MS_TURBO_MAXIMO (15 * 60 * 1000)
#define TROZOS_MIN_TURBO 6                   // con menos, cambiar de nivel no vale la pena
#define MAX_RECIBOS 100
#define MAX_GRUPO 20                         // mensajes que pueden ir juntos en un G

static StreamBufferHandle_t entrada;
static QueueHandle_t cola;  // números de registro por mandar
static enlace_aviso_t avisar;
static enlace_presencia_t avisar_presencia;
static volatile int ultimo_rssi = 1;
static volatile int64_t ultimo_contacto;
static int64_t revisar_radio_proximo = MS_REVISAR_RADIO_PERDIDOS;
static int rssi_bueno = -120;  // la última señal creíble (pesimista al partir: así no hay turbo por error)
static int64_t ms_volver_base = 30LL * 60 * 1000;  // canal de emergencia (la consola lo acorta para probar)
static int64_t proximo_intento_base;
static int64_t canal_libre;
static int64_t ultimo_rx;      // última vez que llegó algo
static int64_t ventana_hasta;  // ventana que abrió el otro (para hablar yo durante su foto)
static int64_t recibos_proximo;  // cuándo intentar mandar los recibos de lectura pendientes
static int64_t ceder_hasta;      // el otro dijo "tengo mensajes": hasta acá no empiezo a hablar
static int64_t turno_del_otro_hasta;  // le di acuse y quizás sigue con otro paquete (hasta ~7 s): no lo piso
static int azar_silencio;             // azar extra (0-1,5 s) sobre el silencio, nuevo con cada paquete que llega

static int64_t ahora_ms(void) { return esp_timer_get_time() / 1000; }
static uint16_t id_nuevo(void) { return (uint16_t)(esp_random() | 1); }
static int64_t ahora_unix(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec > 1700000000 ? tv.tv_sec : 0;
}

static bool turno_libre(void);
static bool turno_para_texto(void);
static void registrar_latido(bool sale, int rssi);

// ---------- bitácora de radio: lo último que se mandó y llegó (para entender cualquier rareza) ----------
typedef struct {
    int64_t ms;
    char tipo;
    bool salio;      // true = lo mandé yo · false = llegó
    bool nota;       // no es un paquete sino algo que pasó (modo AT, canal, nivel, tarea trabada, señal dudosa)
    int8_t rssi;
    uint8_t largo;
    uint16_t id;
    uint8_t fase, nivel;
    char txt[26];
    int32_t valor;
} anotacion_t;
#define LARGO_BITACORA 96
static anotacion_t bitacora[LARGO_BITACORA];
static int pos_bitacora;
static uint8_t fase_actual(void);

static void anotar(char tipo, bool salio, int rssi, int largo, uint16_t id)
{
    anotacion_t a = {.ms = esp_timer_get_time() / 1000, .tipo = tipo, .salio = salio,
                     .rssi = (int8_t)(rssi < -128 ? -128 : rssi > 127 ? 127 : rssi), .largo = (uint8_t)largo,
                     .id = id, .fase = fase_actual(), .nivel = (uint8_t)protocolo_nivel()};
    bitacora[pos_bitacora++ % LARGO_BITACORA] = a;
}

// '@' modo AT · 'U' módulo enchufado/desenchufado · '#' canal · 'N' nivel · '!' tarea trabada · '?' señal dudosa
static void anotar_nota(char tipo, const char *txt, int valor)
{
    anotacion_t a = {.ms = esp_timer_get_time() / 1000, .tipo = tipo, .nota = true, .valor = valor,
                     .fase = fase_actual(), .nivel = (uint8_t)protocolo_nivel()};
    strlcpy(a.txt, txt, sizeof(a.txt));
    bitacora[pos_bitacora++ % LARGO_BITACORA] = a;
}

// ---------- transmitir respetando el tiempo en el aire ----------
static enlace_tx_t al_transmitir;  // avisa ANTES de cada transmisión (main.c baja la pantalla)
static volatile int prueba_tx;      // transmisiones de prueba pendientes (consola: "prueba tx N")
static volatile int prueba_largo;   // largo de esos paquetes (0 = un ping corto)

static void transmitir(const uint8_t *b, int n)
{
    while (ahora_ms() < canal_libre) vTaskDelay(pdMS_TO_TICKS(10));
    // A 22 dBm el LoRa chupa mucha corriente de golpe: la pantalla baja ANTES de que empiece, para que el
    // voltaje no caiga (si cae, el S3 se cuelga). La potencia de la radio no se toca nunca.
    if (al_transmitir) al_transmitir(ms_en_aire(n) + 300);
    lora_mandar(b, n);
    canal_libre = ahora_ms() + ms_en_aire(n) + 150;
}

static void mandar(char tipo, uint16_t id, const void *cuerpo, int largo)
{
    uint8_t b[MAX_PAQUETE];
    anotar(tipo, true, 0, largo, id);
    transmitir(b, empaquetar(tipo, id, cuerpo, largo, b));
}

static void responder(char tipo, uint16_t id, const void *cuerpo, int largo)
{
    vTaskDelay(pdMS_TO_TICKS(80));  // que el otro alcance a pasar a recibir
    mandar(tipo, id, cuerpo, largo);
}

static void poner16(uint8_t *b, uint16_t v) { b[0] = v & 0xFF; b[1] = v >> 8; }
static void poner32(uint8_t *b, uint32_t v) { for (int i = 0; i < 4; i++) b[i] = (v >> (8 * i)) & 0xFF; }
static uint16_t leer16(const uint8_t *b) { return b[0] | (b[1] << 8); }
static uint32_t leer32(const uint8_t *b) { return b[0] | (b[1] << 8) | (b[2] << 16) | ((uint32_t)b[3] << 24); }

// ---------- presencia: el latido ----------
typedef struct {
    uint8_t encuentro;  // 1 = volvió la señal · 0 = se perdió
    int64_t ts, ms;
    int8_t rssi;
} evento_t;

static struct {
    int64_t proximo;          // cuándo toca el próximo latido
    int64_t respondido;       // último latido de respuesta inmediata (anti-tormenta)
    bool cerca, hubo;
    float rssi_suave;
    int rssi_alla;            // con qué señal me oye él (127 = no sé)
    int64_t alla_ms;
    int potencia_otro;
    int64_t visto_ms, visto_ts, desde_ms;
    int rssi_visto;
    int64_t ultimo_reintento;
    int64_t mandado_ms;       // último latido que mandé
    evento_t eventos[12];
    int n_eventos;
} lat = {.rssi_alla = 127, .potencia_otro = 22};

static int64_t umbral_perdido(void) { return (int64_t)ajustes.latido_s * 2500 + 30000; }
// falta (al menos) un latido del otro: algo pasó, a buscarse rápido aunque todavía no se den por perdidos
static int64_t umbral_sospecha(void) { return (int64_t)ajustes.latido_s * 1300 + 10000; }

static bool buscando(void) { return !lat.cerca || ahora_ms() - lat.visto_ms > umbral_sospecha(); }

static void programar_latido(void)
{
    // cerca: el latido de siempre (±20 %). Buscándose: cada ~4 s con mucho azar (±50 %): con poco azar los dos
    // quedan "sincronizados" y chocan una y otra vez (simulado en herramientas/simular_encuentro.py).
    int64_t p = (int64_t)ajustes.latido_s * 1000;
    if (buscando()) lat.proximo = ahora_ms() + MS_LATIDO_PERDIDO / 2 + esp_random() % (MS_LATIDO_PERDIDO + 1);
    else lat.proximo = ahora_ms() + p * 8 / 10 + esp_random() % (p * 4 / 10 + 1);
}

static void anotar_evento(bool encuentro, int rssi)
{
    if (lat.n_eventos == 12) {
        memmove(&lat.eventos[0], &lat.eventos[1], sizeof(evento_t) * 11);
        lat.n_eventos--;
    }
    lat.eventos[lat.n_eventos++] = (evento_t){encuentro, ahora_unix(), ahora_ms(), (int8_t)rssi};
}

static void mandar_latido(void)
{
    uint8_t c[8];
    c[0] = (uint8_t)(int8_t)(lat.hubo ? lat.rssi_visto : 127);
    int64_t desde = lat.hubo ? (ahora_ms() - lat.visto_ms) / 1000 : 0xFFFF;
    poner16(c + 1, desde > 0xFFFE ? 0xFFFE : (uint16_t)desde);
    if (!lat.hubo) poner16(c + 1, 0xFFFF);
    c[3] = (uint8_t)ajustes.potencia;
    poner32(c + 4, (uint32_t)ahora_unix());
    mandar(LATIDO, id_nuevo(), c, sizeof(c));
    lat.mandado_ms = ahora_ms();
    registrar_latido(true, 0);
    programar_latido();
}

static void reintentar_pendientes(void);

// ---------- para la página de él: latidos, comandos al aparato de ella y sus respuestas ----------
#define MAX_LATIDOS_LOG 40
static struct { uint32_t t; int8_t rssi; bool sale; } latidos_log[MAX_LATIDOS_LOG];
static int n_latidos_log, latidos_salen, latidos_llegan;
static void registrar_latido(bool sale, int rssi)
{
    uint32_t t = (uint32_t)ahora_unix();
    sale ? latidos_salen++ : latidos_llegan++;
    if (!t) return;  // sin hora todavía (la pone el celular al abrir la página)
    if (n_latidos_log == MAX_LATIDOS_LOG) {
        memmove(&latidos_log[0], &latidos_log[1], sizeof(latidos_log[0]) * (MAX_LATIDOS_LOG - 1));
        n_latidos_log--;
    }
    latidos_log[n_latidos_log].t = t;
    latidos_log[n_latidos_log].rssi = (int8_t)rssi;
    latidos_log[n_latidos_log].sale = sale;
    n_latidos_log++;
}

#define MAX_RESPUESTAS 6
static struct { uint32_t t; char texto[150]; } respuestas[MAX_RESPUESTAS];
static int n_respuestas;
static void anotar_respuesta(const char *texto)
{
    if (n_respuestas == MAX_RESPUESTAS) {
        memmove(&respuestas[0], &respuestas[1], sizeof(respuestas[0]) * (MAX_RESPUESTAS - 1));
        n_respuestas--;
    }
    respuestas[n_respuestas].t = (uint32_t)ahora_unix();
    strlcpy(respuestas[n_respuestas].texto, texto, sizeof(respuestas[0].texto));
    n_respuestas++;
}

static struct {
    bool pendiente;
    char cmd[96];
    uint16_t id;
    int intentos;
    int64_t limite;
} com;  // un comando para el aparato de ella (lo pide la página)
static int hora_pendiente;  // veces que falta mandarle la hora
static int64_t hora_limite;

// Cualquier paquete de él: está al alcance.
static void contacto(int rssi)
{
    int64_t t = ahora_ms();
    lat.visto_ms = t;
    lat.visto_ts = ahora_unix();
    lat.rssi_visto = rssi;
    lat.rssi_suave = lat.hubo && lat.cerca ? lat.rssi_suave * 0.7f + rssi * 0.3f : rssi;
    lat.hubo = true;
    if (!lat.cerca) {
        lat.cerca = true;
        lat.desde_ms = t;
        anotar_evento(true, rssi);
        ESP_LOGW(TAG, "él apareció (%d dBm)", rssi);
        if (avisar_presencia) avisar_presencia(true, rssi);
        if (t - lat.respondido > 4000) {  // que el otro también se entere al tiro (sólo adelanta el próximo)
            lat.respondido = t;
            int64_t pronto = t + 300 + esp_random() % 1500;
            if (pronto < lat.proximo) lat.proximo = pronto;
        }
        lat.ultimo_reintento = t;
        reintentar_pendientes();  // todo lo que estaba esperando, ahora
        recibos_proximo = 0;      // y los recibos de lectura que no alcanzaron a llegar
    }
}

static void revisar_presencia(void)
{
    int64_t t = ahora_ms();
    // falta un latido del otro: el próximo mío sale pronto (búsqueda rápida), sin esperar el minuto
    if (lat.cerca && t - lat.visto_ms > umbral_sospecha() && lat.proximo > t + MS_LATIDO_PERDIDO * 3 / 2) programar_latido();
    if (lat.cerca && t - lat.visto_ms > umbral_perdido()) {
        lat.cerca = false;
        lat.desde_ms = t;
        anotar_evento(false, lat.rssi_visto);
        ESP_LOGW(TAG, "se perdió la señal de él");
        if (avisar_presencia) avisar_presencia(false, lat.rssi_visto);
        programar_latido();
        revisar_radio_proximo = 0;  // perdidos: revisar la radio propia al tiro
    }
}

// ---------- turbo (sólo fotos, sólo con señal de sobra) ----------
static struct {
    bool activo, soy_emisor;
    int nivel;
    int64_t desde;
} turbo;

static int nivel_posible(void)
{
    if (!ajustes.turbo || !lat.cerca) return 0;
    int64_t t = ahora_ms();
    if (t - lat.visto_ms > 300000 || lat.rssi_alla == 127 || t - lat.alla_ms > 300000) return 0;
    int m = (int)lat.rssi_suave;
    if (lat.rssi_alla < m) m = lat.rssi_alla;
    // márgenes de unos 20 dB sobre la sensibilidad de cada nivel (122, 127 y 130 dBm)
    if (m >= -100) return 3;
    if (m >= -107) return 2;
    if (m >= -112) return 1;
    return 0;
}

static void cambiar_nivel(int n)
{
    while (ahora_ms() < canal_libre) vTaskDelay(pdMS_TO_TICKS(10));  // que termine de transmitir
    vTaskDelay(pdMS_TO_TICKS(200));
    char cmd[16], r[96];
    snprintf(cmd, sizeof(cmd), "AT+LEVEL%d", n);
    lora_comandos_at(cmd, r, sizeof(r));
    protocolo_fijar_nivel(n);
    turbo.activo = n > 0;
    turbo.nivel = n;
    turbo.desde = ahora_ms();
    xStreamBufferReset(entrada);  // lo que llegó a otro nivel no sirve
    lector_reiniciar();
    ultimo_rx = ahora_ms();
    canal_libre = ahora_ms();
    anotar_nota('N', r, n);
    ESP_LOGW(TAG, "radio en nivel %d (%s)", n, r);
}

// ---------- lo que se está mandando ahora ----------
typedef enum {
    LIBRE, TEXTO_ESPERA, FOTO_INICIO, FOTO_TROZOS, FOTO_PREGUNTA, RECIBO_ESPERA,
    TURBO_PEDIR, TURBO_PROBAR, TURBO_VOLVER,
} fase_t;
static const char *NOMBRES_FASE[] = {"libre", "texto", "foto_aviso", "foto_trozos", "foto_pregunta",
                                     "recibo", "turbo_pedir", "turbo_probar", "turbo_volver"};
static struct {
    fase_t fase;
    uint32_t n;
    uint16_t id, foto, seq;
    uint32_t ts;
    int intentos, rondas, nivel;
    int64_t limite;
    uint8_t *datos;
    size_t largo;
    int trozos, siguiente, alargues, desde_consulta, sin_respuesta;
    enum { NADA, CONSULTA, VENTANA } esperando;  // durante la foto: respuesta a "¿vas bien?" o su mensaje
    int64_t ventana_desde;
    uint8_t faltan[(MAX_FOTO / TROZO) / 8 + 1];
    char texto[MAX_TEXTO + 1];
    uint16_t recibos[MAX_RECIBOS];
    int n_recibos;
    // textos: uno va como M; varios seguidos en la cola van juntos en un G (un solo acuse)
    char tipo_msg;
    uint8_t cuerpo[MAX_CUERPO];
    int largo_cuerpo;
    uint32_t grupo[MAX_GRUPO];  // grupo[0] = tx.n
    int n_grupo;
} tx;

// Un mensaje de ella que sale en medio de su propia foto (sin esperar a que la foto termine).
static struct {
    bool activo;
    uint32_t n;
    uint16_t id, seq;
    uint32_t ts;
    int intentos;
    int64_t limite;
    char texto[MAX_TEXTO + 1];
} txt;

static int contar_faltan(const uint8_t *mapa, int trozos)
{
    int k = 0;
    for (int i = 0; i < trozos; i++) k += (mapa[i / 8] >> (i % 8)) & 1;
    return k;
}

static void marcar_entregado(registro_t *r, void *arg)
{
    r->estado = ENTREGADO;
    r->progreso = 1000;
    r->ts_llegada = ahora_unix();
}

static void terminar_tx(uint8_t estado)
{
    for (int i = 1; i < tx.n_grupo; i++) {  // los que iban juntos en el mismo G
        if (estado == ENTREGADO) almacen_modificar(tx.grupo[i], marcar_entregado, NULL, true);
        else almacen_actualizar(tx.grupo[i], estado, 0);
    }
    if (estado == ENTREGADO) almacen_modificar(tx.n, marcar_entregado, NULL, true);
    else almacen_actualizar(tx.n, estado, 0);
    free(tx.datos);
    tx.datos = NULL;
    ESP_LOGI(TAG, "registro %lu: %s", (unsigned long)tx.n, estado == ENTREGADO ? "entregado" : "no llegó (se reintentará)");
    if (turbo.activo && turbo.soy_emisor) {  // la foto terminó: los dos vuelven al nivel 0
        tx.fase = TURBO_VOLVER;
        tx.intentos = 0;
        tx.limite = 0;
    } else {
        tx.fase = LIBRE;
    }
}

static int64_t espera_respuesta(int bytes_ida)
{
    return ahora_ms() + ms_en_aire(bytes_ida) + ms_en_aire(16) + 1500 + (esp_random() % 1200);
}

// Después de un intento sin respuesta (quizás chocamos): espera extra al azar, más larga a cada intento
// y mayor que lo que dura el paquete en el aire, para no volver a chocar con el otro.
static int64_t retroceso(int intento, int bytes)
{
    // Igual en los dos aparatos. Antes el de él esperaba siempre un paquete más que ella: con ella escribiendo
    // seguido, él perdía SIEMPRE el turno (medido el 03-oct: sus mensajes tardaban minutos).
    if (intento < 1) return 0;
    int k = intento > 4 ? 4 : intento;
    uint32_t tope = (uint32_t)k * (ms_en_aire(bytes) + 400);
    return 300 + esp_random() % (tope + 1);
}

static void empezar_foto_normal(int64_t esperar_ms)
{
    tx.fase = FOTO_INICIO;
    tx.intentos = 0;
    tx.limite = esperar_ms ? ahora_ms() + esperar_ms : 0;
}

static bool empezar_recibos(void)
{
    if (ahora_ms() < recibos_proximo || !lat.cerca) return false;
    int k = almacen_recibos_pendientes(tx.recibos, MAX_RECIBOS);
    if (!k) {
        recibos_proximo = ahora_ms() + 30000;
        return false;
    }
    tx.n_recibos = k;
    tx.id = id_nuevo();
    tx.intentos = 0;
    tx.limite = 0;
    tx.fase = RECIBO_ESPERA;
    return true;
}

// Varios mensajes seguidos en la cola van JUNTOS en un G: una ráfaga de 10 "te amo" sale en un solo paquete
// (~7 s) en vez de uno por uno con su acuse y su pausa cada uno (más de un minuto, y él sin poder hablar).
static void armar_textos(void)
{
    tx.grupo[0] = tx.n;
    tx.n_grupo = 1;
    uint8_t *c = tx.cuerpo;
    int largo = strlen(tx.texto), usado = 1;
    poner16(c + usado, tx.seq);
    poner32(c + usado + 2, tx.ts);
    c[usado + 6] = (uint8_t)largo;
    memcpy(c + usado + 7, tx.texto, largo);
    usado += 7 + largo;
    uint32_t m;
    registro_t r;
    while (tx.n_grupo < MAX_GRUPO && xQueuePeek(cola, &m, 0) == pdTRUE && almacen_obtener(m, &r) && r.tipo == ES_TEXTO) {
        int l = strlen(r.texto);
        if (usado + 7 + l > MAX_CUERPO) break;
        xQueueReceive(cola, &m, 0);
        if (r.estado == ENTREGADO) continue;
        uint16_t seq = r.seq ? r.seq : almacen_dar_seq(m);
        poner16(c + usado, seq);
        poner32(c + usado + 2, (uint32_t)r.ts);
        c[usado + 6] = (uint8_t)l;
        memcpy(c + usado + 7, r.texto, l);
        usado += 7 + l;
        almacen_actualizar(m, ENVIANDO, 0);
        tx.grupo[tx.n_grupo++] = m;
    }
    if (tx.n_grupo > 1) {
        c[0] = (uint8_t)tx.n_grupo;
        tx.tipo_msg = GRUPO;
        tx.largo_cuerpo = usado;
    } else {  // uno solo: M de siempre (seq, hora, texto)
        poner16(c, tx.seq);
        poner32(c + 2, tx.ts);
        memcpy(c + 6, tx.texto, largo);
        tx.tipo_msg = MENSAJE;
        tx.largo_cuerpo = 6 + largo;
    }
}

static void empezar_siguiente(void)
{
    uint32_t n;
    if (tx.fase != LIBRE || !lora_listo() || !turno_para_texto()) return;
    if (xQueuePeek(cola, &n, 0) != pdTRUE) {
        if (turno_libre()) empezar_recibos();
        return;
    }
    registro_t r;
    bool hay = almacen_obtener(n, &r);
    if (hay && r.tipo == ES_FOTO && !turno_libre()) return;  // las fotos esperan a que el canal esté libre
    xQueueReceive(cola, &n, 0);
    if (!hay || r.estado == ENTREGADO) return;
    free(tx.datos);
    memset(&tx, 0, sizeof(tx));
    tx.n = n;
    tx.id = id_nuevo();
    tx.seq = r.seq ? r.seq : almacen_dar_seq(n);
    tx.ts = (uint32_t)r.ts;
    almacen_actualizar(n, ENVIANDO, 0);
    if (r.tipo == ES_TEXTO) {
        strlcpy(tx.texto, r.texto, sizeof(tx.texto));
        armar_textos();
        tx.intentos = 0;
        tx.limite = 0;
        tx.fase = TEXTO_ESPERA;
        return;
    }
    tx.datos = almacen_leer_foto(n, &tx.largo);
    if (!tx.datos || tx.largo > MAX_FOTO) {
        tx.fase = LIBRE;
        almacen_actualizar(n, FALLIDO, 0);
        return;
    }
    tx.foto = id_nuevo();
    tx.trozos = (tx.largo + TROZO - 1) / TROZO;
    memset(tx.faltan, 0, sizeof(tx.faltan));
    for (int i = 0; i < tx.trozos; i++) tx.faltan[i / 8] |= 1 << (i % 8);
    int nv = tx.trozos >= TROZOS_MIN_TURBO ? nivel_posible() : 0;
    if (nv > 0) {
        tx.nivel = nv;
        tx.fase = TURBO_PEDIR;
        tx.intentos = 0;
        tx.limite = 0;
    } else {
        empezar_foto_normal(0);
    }
}

static void mandar_texto(uint16_t id, uint16_t seq, uint32_t ts, const char *texto)
{
    uint8_t c[6 + MAX_TEXTO];
    int largo = strlen(texto);
    poner16(c, seq);
    poner32(c + 2, ts);
    memcpy(c + 6, texto, largo);
    mandar(MENSAJE, id, c, 6 + largo);
}

// ¿Lo primero de la cola es un mensaje de texto? (para intercalarlo en la foto o avisar que hay uno)
static bool texto_en_cola(uint32_t *n_out)
{
    uint32_t n;
    registro_t r;
    if (xQueuePeek(cola, &n, 0) != pdTRUE || !almacen_obtener(n, &r) || r.tipo != ES_TEXTO) return false;
    if (n_out) *n_out = n;
    return true;
}

static bool empezar_txt(void)
{
    uint32_t n;
    registro_t r;
    if (txt.activo || !texto_en_cola(&n)) return false;
    xQueueReceive(cola, &n, 0);
    if (!almacen_obtener(n, &r) || r.estado == ENTREGADO) return true;
    txt.activo = true;
    txt.n = n;
    txt.id = id_nuevo();
    txt.seq = r.seq ? r.seq : almacen_dar_seq(n);
    txt.ts = (uint32_t)r.ts;
    txt.intentos = 0;
    txt.limite = 0;
    strlcpy(txt.texto, r.texto, sizeof(txt.texto));
    almacen_actualizar(n, ENVIANDO, 0);
    return true;
}

static void gestionar_txt(void)
{
    if (!txt.activo || ahora_ms() < txt.limite) return;
    if (txt.intentos >= INTENTOS_TEXTO) {
        almacen_actualizar(txt.n, FALLIDO, 0);
        txt.activo = false;
        return;
    }
    mandar_texto(txt.id, txt.seq, txt.ts, txt.texto);
    txt.intentos++;
    txt.limite = espera_respuesta(11 + strlen(txt.texto)) + retroceso(txt.intentos, 11 + strlen(txt.texto));
}

static void avanzar_tx(void)
{
    int64_t t = ahora_ms();
    if (txt.activo) {
        gestionar_txt();
        if (tx.fase == LIBRE || txt.activo) return;
    }
    switch (tx.fase) {
    case LIBRE:
        empezar_siguiente();
        break;
    case TEXTO_ESPERA:
        if (t < tx.limite || !turno_para_texto()) break;
        if (tx.intentos >= INTENTOS_TEXTO) {
            // Si el otro está presente, vuelven a la cola de inmediato (adelante, en su orden) y salen juntos con
            // lo que se escribió mientras tanto. Si no está, quedan esperando el reencuentro.
            bool presente = lat.hubo && t - lat.visto_ms < MS_PRESENTE;
            uint32_t grupo[MAX_GRUPO];
            int k = tx.n_grupo;
            memcpy(grupo, tx.grupo, sizeof(uint32_t) * k);
            terminar_tx(FALLIDO);
            if (presente)
                for (int i = k - 1; i >= 0; i--) {
                    almacen_actualizar(grupo[i], ENVIANDO, 0);
                    if (xQueueSendToFront(cola, &grupo[i], 0) != pdTRUE) almacen_actualizar(grupo[i], FALLIDO, 0);
                }
            break;
        }
        mandar(tx.tipo_msg, tx.id, tx.cuerpo, tx.largo_cuerpo);
        tx.intentos++;
        tx.limite = espera_respuesta(5 + tx.largo_cuerpo) + retroceso(tx.intentos, 5 + tx.largo_cuerpo);
        break;
    case RECIBO_ESPERA:
        if (t < tx.limite || !turno_libre()) break;
        if (tx.intentos >= 3) {
            tx.fase = LIBRE;
            recibos_proximo = t + 60000;
            break;
        }
        {
            uint8_t c[2 * MAX_RECIBOS];
            for (int i = 0; i < tx.n_recibos; i++) poner16(c + 2 * i, tx.recibos[i]);
            mandar(LEIDO, tx.id, c, 2 * tx.n_recibos);
        }
        tx.intentos++;
        tx.limite = espera_respuesta(5 + 2 * tx.n_recibos) + retroceso(tx.intentos, 5 + 2 * tx.n_recibos);
        break;
    case TURBO_PEDIR:
        if (t < tx.limite || !turno_libre()) break;
        if (tx.intentos >= 3) {  // no contestó: por si alcanzó a cambiar, se le da tiempo a volver solo
            empezar_foto_normal(MS_TURBO_SILENCIO + 1000);
            break;
        }
        {
            uint8_t c[1] = {(uint8_t)tx.nivel};
            mandar(VELOCIDAD, tx.id, c, 1);
        }
        tx.intentos++;
        tx.limite = espera_respuesta(6) + retroceso(tx.intentos, 6);
        break;
    case TURBO_PROBAR:
        if (t < tx.limite) break;
        if (tx.intentos >= 6) {  // no nos encontramos en el nivel nuevo: los dos vuelven al 0
            cambiar_nivel(0);
            empezar_foto_normal(MS_TURBO_SILENCIO + 1000);
            break;
        }
        mandar(PING, tx.id, NULL, 0);
        tx.intentos++;
        tx.limite = ahora_ms() + ms_en_aire(5) + ms_en_aire(6) + 1200;
        break;
    case TURBO_VOLVER:
        if (t < tx.limite) break;
        if (tx.intentos >= 2) {  // él vuelve solo por silencio; yo vuelvo ya
            cambiar_nivel(0);
            tx.fase = LIBRE;
            break;
        }
        {
            uint8_t c[1] = {0};
            mandar(VELOCIDAD, tx.id, c, 1);
        }
        tx.intentos++;
        tx.limite = espera_respuesta(6);
        break;
    case FOTO_INICIO:
        if (t < tx.limite || !turno_libre()) break;
        if (tx.intentos >= INTENTOS_FOTO_INICIO) {
            terminar_tx(FALLIDO);
            break;
        }
        {
            uint8_t c[14];
            poner16(c, tx.foto);
            poner32(c + 2, tx.largo);
            poner16(c + 6, tx.trozos);
            poner16(c + 8, tx.seq);
            poner32(c + 10, tx.ts);
            mandar(FOTO, tx.id, c, sizeof(c));
        }
        tx.intentos++;
        tx.limite = espera_respuesta(19) + retroceso(tx.intentos, 19);
        break;
    case FOTO_TROZOS: {
        if (t < tx.limite) break;  // esperando la respuesta a "¿vas bien?" o el mensaje del otro
        if (tx.esperando == VENTANA && ultimo_rx > tx.ventana_desde && tx.alargues < 4) {
            tx.alargues++;  // el otro habló: quizás tiene más que decir
            tx.ventana_desde = t;
            tx.limite = t + MS_VENTANA;
            break;
        }
        if (tx.esperando == CONSULTA) {  // "¿vas bien?" quedó sin respuesta
            tx.sin_respuesta++;
            if (turbo.activo && turbo.soy_emisor && tx.sin_respuesta >= 2) {
                ESP_LOGW(TAG, "turbo: no me contesta, bajo a nivel 0 y sigo desde donde iba");
                cambiar_nivel(0);
                tx.fase = FOTO_PREGUNTA;  // al nivel 0 primero se pregunta qué le falta
                tx.intentos = 0;
                tx.limite = ahora_ms() + MS_TURBO_SILENCIO + 1000;  // que él también alcance a bajar
                tx.esperando = NADA;
                break;
            }
            if (!turbo.activo && tx.sin_respuesta >= 3) {  // se fue de alcance: no tirar trozos al vacío
                ESP_LOGW(TAG, "foto: no me contesta; espero el reencuentro y sigo desde donde quedó");
                tx.fase = FOTO_PREGUNTA;
                tx.intentos = 0;
                tx.limite = 0;
                tx.esperando = NADA;
                break;
            }
        }
        tx.esperando = NADA;
        tx.limite = 0;
        if (empezar_txt()) break;  // un mensaje de ella: sale ahora, entre dos trozos
        while (tx.siguiente < tx.trozos && !((tx.faltan[tx.siguiente / 8] >> (tx.siguiente % 8)) & 1)) tx.siguiente++;
        if (tx.siguiente >= tx.trozos) {
            tx.fase = FOTO_PREGUNTA;
            tx.intentos = 0;
            tx.limite = 0;
            break;
        }
        int i = tx.siguiente++;
        int largo = (i == tx.trozos - 1) ? tx.largo - i * TROZO : TROZO;
        uint8_t c[4 + TROZO];
        poner16(c, tx.foto);
        poner16(c + 2, i);
        memcpy(c + 4, tx.datos + i * TROZO, largo);
        mandar(TROZO_FOTO, id_nuevo(), c, 4 + largo);
        if (++tx.desde_consulta >= TROZOS_POR_CONSULTA && tx.siguiente < tx.trozos) {
            tx.desde_consulta = 0;
            uint8_t k[2];
            poner16(k, tx.foto);
            mandar(PREGUNTA_FOTO, tx.id, k, 2);  // "¿vas bien?": contesta lo que le falta y si quiere hablar
            tx.esperando = CONSULTA;
            tx.limite = espera_respuesta(7 + (tx.trozos + 7) / 8 + 1);
        }
        break;
    }
    case FOTO_PREGUNTA:
        if (t < tx.limite || !turno_libre()) break;
        if (tx.intentos >= 6) {
            terminar_tx(FALLIDO);
            break;
        }
        {
            uint8_t c[2];
            poner16(c, tx.foto);
            mandar(PREGUNTA_FOTO, tx.id, c, 2);
        }
        tx.intentos++;
        tx.limite = espera_respuesta(7 + (tx.trozos + 7) / 8) + retroceso(tx.intentos, 7);
        break;
    }
}

// ---------- foto que está llegando ----------
static struct {
    bool activa, en_pausa;
    uint16_t foto, seq;
    uint32_t n;
    uint8_t *datos;
    size_t largo;
    int trozos;
    uint8_t tengo[(MAX_FOTO / TROZO) / 8 + 1];
    int64_t ultimo;
} rx;
static uint16_t ultima_foto_completa;

static int progreso_rx(void)
{
    int tengo = 0;
    for (int k = 0; k < rx.trozos; k++) tengo += (rx.tengo[k / 8] >> (k % 8)) & 1;
    return rx.trozos ? tengo * 1000 / rx.trozos : 0;
}

static void marcar_recibida(registro_t *r, void *arg)
{
    r->estado = RECIBIDO;
    r->progreso = 1000;
    r->ts_llegada = ahora_unix();
}

static void foto_rx_terminar(bool completa)
{
    if (completa) {
        if (almacen_guardar_foto(rx.n, rx.datos, rx.largo)) {
            almacen_modificar(rx.n, marcar_recibida, NULL, true);
            ultima_foto_completa = rx.foto;
            if (avisar) avisar(rx.n);
        } else {
            ESP_LOGE(TAG, "no se pudo guardar la foto (¿flash llena?)");
            almacen_actualizar(rx.n, FALLIDO, 0);
        }
    } else {
        almacen_actualizar(rx.n, FALLIDO, 0);
    }
    free(rx.datos);
    memset(&rx, 0, sizeof(rx));
}

// Respuesta a "¿te falta algo?": ACUSE = llegó completa; R con mapa = faltan esos trozos;
// R sin mapa = no conozco esa foto (me reinicié): empieza de nuevo.
// Devuelve true si la foto en curso quedó completa.
static bool contestar_pregunta(uint16_t id, uint16_t foto)
{
    int8_t nada = 0;
    if (rx.activa && rx.foto == foto) {
        uint8_t c[2 + sizeof(rx.tengo) + 1] = {0};
        poner16(c, foto);
        int faltan = 0;
        for (int i = 0; i < rx.trozos; i++)
            if (!((rx.tengo[i / 8] >> (i % 8)) & 1)) {
                c[2 + i / 8] |= 1 << (i % 8);
                faltan++;
            }
        if (faltan) {
            int largo = 2 + (rx.trozos + 7) / 8;
            bool quiero_hablar = texto_en_cola(NULL) || tx.fase == TEXTO_ESPERA;
            c[largo++] = quiero_hablar ? 1 : 0;  // "tengo un mensaje para ti"
            responder(FALTAN, id, c, largo);
            if (quiero_hablar) ventana_hasta = canal_libre + 1500;  // me toca justo después
            return false;
        }
        responder(ACUSE, id, &nada, 1);
        return true;
    }
    if (foto == ultima_foto_completa) {
        responder(ACUSE, id, &nada, 1);
    } else {
        uint8_t c[2];
        poner16(c, foto);
        responder(FALTAN, id, c, 2);
    }
    return false;
}

// ---------- reglas al recibir ----------
static uint16_t vistos[32];
static int pos_vistos;
static bool ya_visto(char tipo, uint16_t id)
{
    uint16_t k = id ^ ((uint16_t)tipo << 8);
    for (int i = 0; i < 32; i++) if (vistos[i] == k) return true;
    vistos[pos_vistos++ % 32] = k;
    return false;
}

// Cambio de canal con vuelta atrás: el canal nuevo se aplica pero NO se graba hasta que llega algo
// por él. Si en 2 min no llega nada, vuelve al anterior. Si se reinicia a la mitad, arranca con el
// anterior (el grabado). Así el aparato sellado nunca queda perdido en un canal.
static struct {
    bool pendiente;
    int64_t limite;
    int nuevo, viejo;
} radio;

static bool turno_libre(void)
{
    bool foto_viva = rx.activa && !rx.en_pausa && ahora_ms() - rx.ultimo < MS_FOTO_PARADA;
    int64_t t = ahora_ms();
    return !foto_viva && t - ultimo_rx > MS_SILENCIO + azar_silencio && t >= ceder_hasta && t >= turno_del_otro_hasta;
}

// un texto (corto) además cabe en la ventana que abre el que manda una foto
static bool turno_para_texto(void)
{
    return turno_libre() || ahora_ms() < ventana_hasta;
}

typedef struct {
    uint16_t seq;
    int64_t ts;
    int rssi;
} datos_llegada_t;

static void marcar_reintento_foto(registro_t *r, void *arg)
{
    datos_llegada_t *d = arg;
    r->estado = ENVIANDO;
    r->progreso = 0;
    r->rssi = (int8_t)d->rssi;
}

static uint8_t fase_actual(void) { return (uint8_t)tx.fase; }

// Un mensaje de él (suelto o dentro de un G). Los repetidos (acuse perdido) se reconocen por el número.
static void guardar_de_el(uint16_t seq, uint32_t ts, const char *texto, int largo, int8_t r8)
{
    registro_t previo;
    if (seq && almacen_buscar_seq(DE_EL, seq, &previo)) return;  // ya lo tenía: el acuse se había perdido
    registro_t r = {.de = DE_EL, .tipo = ES_TEXTO, .estado = RECIBIDO, .rssi = r8, .seq = seq};
    r.ts_llegada = ahora_unix();
    r.ts = ts > 1700000000 ? ts : r.ts_llegada;
    if (largo > (int)sizeof(r.texto) - 1) largo = sizeof(r.texto) - 1;
    memcpy(r.texto, texto, largo);
    r.texto[largo] = 0;
    uint32_t n = almacen_agregar(&r);
    if (avisar) avisar(n);
}

static void procesar(const paquete_t *p)
{
    int8_t r8 = (int8_t)p->rssi;
    anotar(p->tipo, false, p->rssi, p->largo, p->id);
    ultimo_rx = ahora_ms();
    azar_silencio = esp_random() % 1500;
    ultimo_rssi = p->rssi;
    ultimo_contacto = ahora_ms();
    contacto(p->rssi);
    if (radio.pendiente) {  // el canal nuevo funciona: ahora sí se graba
        radio.pendiente = false;
        ajustes.canal = radio.nuevo;
        ajustes_guardar();
        anotar_nota('#', "confirmado", radio.nuevo);
        ESP_LOGW(TAG, "canal %02X confirmado y guardado", radio.nuevo);
    }
    switch (p->tipo) {
    case MENSAJE:
    case GRUPO: {
        if (p->tipo == GRUPO) {  // tiene que cuadrar exacto; si no, llegó roto: sin acuse, que lo repita entero
            int cuantos = p->largo >= 1 ? p->cuerpo[0] : 0, pos = 1, k = 0;
            while (k < cuantos && pos + 7 <= p->largo && pos + 7 + p->cuerpo[pos + 6] <= p->largo) {
                pos += 7 + p->cuerpo[pos + 6];
                k++;
            }
            if (cuantos == 0 || k != cuantos || pos != p->largo) {
                anotar_nota('?', "grupo roto: sin acuse", p->largo);
                break;
            }
        }
        {
            // El acuse dice si yo también tengo mensajes: el otro me cede el turno y los míos salen justo
            // después de este acuse, sin esperar silencio (así nadie le estorba al otro).
            if (lat.hubo && ahora_ms() - lat.ultimo_reintento > 1000) {  // lo que no llegó, a la cola antes del acuse
                lat.ultimo_reintento = ahora_ms();
                reintentar_pendientes();
            }
            bool quiero = texto_en_cola(NULL) || tx.fase == TEXTO_ESPERA || txt.activo;
            uint8_t ac[2] = {(uint8_t)r8, quiero ? 1 : 0};
            responder(ACUSE, p->id, ac, 2);
            ceder_hasta = 0;
            if (quiero) {
                ventana_hasta = canal_libre + 1500;  // hablo yo, justo después de mi acuse
                turno_del_otro_hasta = 0;
                if (tx.fase == TEXTO_ESPERA) tx.limite = 0;
            } else {
                turno_del_otro_hasta = canal_libre + ms_en_aire(MAX_PAQUETE) + 1000;  // el otro puede seguir
            }
        }
        if (ya_visto(p->tipo, p->id)) break;
        if (p->tipo == MENSAJE) {
            if (p->largo < 6) break;
            guardar_de_el(leer16(p->cuerpo), leer32(p->cuerpo + 2), (const char *)p->cuerpo + 6, p->largo - 6, r8);
        } else {
            int cuantos = p->largo >= 1 ? p->cuerpo[0] : 0, pos = 1;
            for (int i = 0; i < cuantos && pos + 7 <= p->largo; i++) {
                int l = p->cuerpo[pos + 6];
                if (pos + 7 + l > p->largo) break;
                guardar_de_el(leer16(p->cuerpo + pos), leer32(p->cuerpo + pos + 2), (const char *)p->cuerpo + pos + 7, l, r8);
                pos += 7 + l;
            }
        }
        if (ahora_ms() - lat.ultimo_reintento > 3000) {  // él está activo: lo de ella que no llegó, va ahora
            lat.ultimo_reintento = ahora_ms();
            reintentar_pendientes();
        }
        break;
    }
    case ACUSE:
        if (p->largo >= 1 && (int8_t)p->cuerpo[0] < 0 && ((txt.activo && p->id == txt.id) ||
                                 ((tx.fase == TEXTO_ESPERA || tx.fase == RECIBO_ESPERA) && p->id == tx.id))) {
            lat.rssi_alla = (int8_t)p->cuerpo[0];
            lat.alla_ms = ahora_ms();
        }
        if (txt.activo && p->id == txt.id) {  // el mensaje intercalado en mi foto llegó
            almacen_modificar(txt.n, marcar_entregado, NULL, true);
            txt.activo = false;
            break;
        }
        if (p->id != tx.id) break;
        if (tx.fase == FOTO_TROZOS && tx.esperando == CONSULTA) {  // ya tenía todo (p.ej. segunda pasada)
            terminar_tx(ENTREGADO);
            break;
        }
        if (tx.fase == TEXTO_ESPERA) {
            if (rx.activa) ventana_hasta = ahora_ms() + 400;  // la ventana de su foto sigue abierta
            // el otro dijo "yo también tengo": le cedo el turno (termina antes si me llega lo suyo)
            if (p->largo >= 2 && (p->cuerpo[1] & 1)) ceder_hasta = ahora_ms() + ms_en_aire(MAX_PAQUETE) + 2000;
            else ventana_hasta = ahora_ms() + 1500;  // el turno sigue siendo mío: lo que tenga en cola sale ya
            terminar_tx(ENTREGADO);
        } else if (tx.fase == FOTO_PREGUNTA) {
            terminar_tx(ENTREGADO);
        } else if (tx.fase == RECIBO_ESPERA) {
            almacen_recibos_entregados(tx.recibos, tx.n_recibos);
            tx.fase = LIBRE;
            recibos_proximo = ahora_ms() + 2000;
        } else if (tx.fase == FOTO_INICIO) {
            tx.rondas = 1;
            tx.limite = 0;
            tx.alargues = 0;
            tx.sin_respuesta = 0;
            tx.desde_consulta = 0;
            tx.esperando = NADA;
            if (p->largo >= 2 && p->cuerpo[1] == 1) {  // ya tiene una parte (se había cortado): que diga qué falta
                tx.fase = FOTO_PREGUNTA;
                tx.intentos = 0;
            } else {
                tx.fase = FOTO_TROZOS;
                tx.siguiente = 0;
            }
        } else if (tx.fase == TURBO_PEDIR) {
            if (p->largo >= 1 && p->cuerpo[0] == tx.nivel) {
                turbo.soy_emisor = true;
                cambiar_nivel(tx.nivel);
                tx.fase = TURBO_PROBAR;
                tx.intentos = 0;
                tx.limite = ahora_ms() + 800;  // que él alcance a cambiar
            } else {
                empezar_foto_normal(0);  // no quiso: normal
            }
        } else if (tx.fase == TURBO_VOLVER) {
            cambiar_nivel(0);
            tx.fase = LIBRE;
        }
        break;
    case PING:
        responder(PONG, p->id, &r8, 1);
        break;
    case PONG:
        if (p->largo >= 1 && (int8_t)p->cuerpo[0] < 0) {
            lat.rssi_alla = (int8_t)p->cuerpo[0];
            lat.alla_ms = ahora_ms();
        }
        if (tx.fase == TURBO_PROBAR && p->id == tx.id) empezar_foto_normal(0);  // turbo confirmado
        break;
    case HORA:
        if (p->largo >= 4) {
            struct timeval tv = {.tv_sec = leer32(p->cuerpo)};
            settimeofday(&tv, NULL);
            ESP_LOGI(TAG, "hora puesta desde el PC");
        }
        break;
    case RESPUESTA:  // lo que contestó el aparato de ella a un comando de él
        if (com.pendiente && p->id == com.id) {
            com.pendiente = false;
            anotar_respuesta((const char *)p->cuerpo);
        }
        break;
    case LATIDO:
        if (p->largo >= 8) {
            int8_t me_oye = (int8_t)p->cuerpo[0];
            uint16_t desde = leer16(p->cuerpo + 1);
            if (me_oye != 127) {
                lat.rssi_alla = me_oye;
                lat.alla_ms = ahora_ms();
            }
            lat.potencia_otro = p->cuerpo[3];
            registrar_latido(false, p->rssi);
            uint32_t hora = leer32(p->cuerpo + 4);
            int64_t mia = ahora_unix();
            if (hora > 1700000000 && (!mia || llabs(mia - (int64_t)hora) > 120)) {  // el PC sabe la hora
                struct timeval tv = {.tv_sec = hora};
                settimeofday(&tv, NULL);
                ESP_LOGI(TAG, "hora puesta por el latido");
            }
            // él no me oye hace rato: le contesto al tiro (una vez cada 20 s como máximo)
            if ((desde == 0xFFFF || (int64_t)desde * 1000 > umbral_sospecha()) && ahora_ms() - lat.respondido > 4000) {
                lat.respondido = ahora_ms();
                int64_t pronto = ahora_ms() + 300 + esp_random() % 1500;
                if (pronto < lat.proximo) lat.proximo = pronto;  // sólo adelanta: nunca posterga un latido
            }
        }
        break;
    case LEIDO: {
        responder(ACUSE, p->id, &r8, 1);
        uint16_t seqs[MAX_RECIBOS];
        int k = p->largo / 2 > MAX_RECIBOS ? MAX_RECIBOS : p->largo / 2;
        for (int i = 0; i < k; i++) seqs[i] = leer16(p->cuerpo + 2 * i);
        if (almacen_leidos_por_el(seqs, k)) ESP_LOGI(TAG, "él leyó %d mensajes", k);
        break;
    }
    case VELOCIDAD: {
        if (p->largo < 1) break;
        int nv = p->cuerpo[0];
        if (nv == 0) {
            uint8_t c[1] = {0};
            responder(ACUSE, p->id, c, 1);
            if (turbo.activo) cambiar_nivel(0);
            break;
        }
        bool rx_libre = !rx.activa || rx.en_pausa;  // una foto a medias guardada no impide el turbo
        bool puedo = ajustes.turbo && nv <= NIVEL_MAX_TURBO && rx_libre && tx.fase == LIBRE && !radio.pendiente;
        uint8_t c[1] = {(uint8_t)(puedo ? nv : 0)};
        responder(ACUSE, p->id, c, 1);
        if (puedo) {
            turbo.soy_emisor = false;
            cambiar_nivel(nv);
        }
        break;
    }
    case FOTO: {
        if (p->largo < 14) break;
        uint16_t foto = leer16(p->cuerpo);
        size_t largo = leer32(p->cuerpo + 2);
        int trozos = leer16(p->cuerpo + 6);
        uint16_t seq = leer16(p->cuerpo + 8);
        uint32_t ts = leer32(p->cuerpo + 10);
        // los dos empezaron una foto a la vez: ÉL cede (la suya vuelve a la cola y sale después)
        if (tx.fase == FOTO_INICIO || tx.fase == TURBO_PEDIR) {
            ESP_LOGW(TAG, "ella empezó una foto a la vez que yo: cedo, la mía sale después");
            terminar_tx(FALLIDO);
        } else if (tx.fase == FOTO_TROZOS || tx.fase == FOTO_PREGUNTA || tx.fase == TURBO_PROBAR) {
            break;  // la mía ya va en camino: ella espera (no debería pasar: no empieza si le llega una foto)
        }
        uint8_t ac[2] = {(uint8_t)r8, 0};  // acuse: señal + "ya tengo una parte" (para seguir desde ahí)
        if (rx.activa && rx.foto == foto) {  // el mismo aviso repetido (se perdió mi acuse)
            ac[1] = 1;
            responder(ACUSE, p->id, ac, 2);
            break;
        }
        if (largo == 0 || largo > MAX_FOTO || trozos != (int)((largo + TROZO - 1) / TROZO)) break;
        registro_t previo;
        bool hay_previo = seq && almacen_buscar_seq(DE_EL, seq, &previo);
        if (foto == ultima_foto_completa || (hay_previo && previo.estado == RECIBIDO)) {
            ultima_foto_completa = foto;  // ya la tenía entera: el acuse final se había perdido
            responder(ACUSE, p->id, ac, 2);
            break;
        }
        if (rx.activa && seq && rx.seq == seq && rx.largo == largo) {  // la misma foto que quedó cortada: se retoma
            rx.foto = foto;
            rx.ultimo = ahora_ms();
            rx.en_pausa = false;
            almacen_actualizar(rx.n, ENVIANDO, progreso_rx());
            ac[1] = 1;
            responder(ACUSE, p->id, ac, 2);
            ESP_LOGI(TAG, "se retoma una foto cortada desde donde quedó");
            break;
        }
        responder(ACUSE, p->id, ac, 2);
        if (rx.activa) foto_rx_terminar(false);  // llegó otra: la anterior se da por perdida
        rx.datos = calloc(1, largo);
        if (!rx.datos) break;
        rx.activa = true;
        rx.en_pausa = false;
        rx.foto = foto;
        rx.seq = seq;
        rx.largo = largo;
        rx.trozos = trozos;
        rx.ultimo = ahora_ms();
        if (hay_previo) {  // la misma foto que había quedado cortada: se usa el mismo registro
            datos_llegada_t d = {.rssi = p->rssi};
            almacen_modificar(previo.n, marcar_reintento_foto, &d, true);
            rx.n = previo.n;
        } else {
            registro_t r = {.de = DE_EL, .tipo = ES_FOTO, .estado = ENVIANDO, .rssi = r8, .seq = seq};
            r.ts_llegada = ahora_unix();
            r.ts = ts > 1700000000 ? ts : r.ts_llegada;
            snprintf(r.texto, sizeof(r.texto), "foto");
            rx.n = almacen_agregar(&r);
        }
        ESP_LOGI(TAG, "llega una foto: %u bytes en %d trozos (nivel %d)", (unsigned)largo, trozos, protocolo_nivel());
        break;
    }
    case TROZO_FOTO: {
        if (!rx.activa || p->largo < 5) break;
        uint16_t foto = leer16(p->cuerpo);
        int i = leer16(p->cuerpo + 2);
        if (foto != rx.foto || i >= rx.trozos) break;
        int largo = p->largo - 4;
        if ((size_t)(i * TROZO + largo) > rx.largo) break;
        memcpy(rx.datos + i * TROZO, p->cuerpo + 4, largo);
        rx.tengo[i / 8] |= 1 << (i % 8);
        rx.ultimo = ahora_ms();
        rx.en_pausa = false;
        almacen_actualizar(rx.n, ENVIANDO, progreso_rx());
        break;
    }
    case PREGUNTA_FOTO: {
        if (p->largo < 2) break;
        uint16_t foto = leer16(p->cuerpo);
        if (contestar_pregunta(p->id, foto)) foto_rx_terminar(true);
        break;
    }
    case FALTAN: {
        if (p->id != tx.id || p->largo < 2) break;
        if (tx.fase == FOTO_TROZOS && tx.esperando == CONSULTA) {  // respuesta a "¿vas bien?" en medio de la foto
            tx.sin_respuesta = 0;
            int mapa = (tx.trozos + 7) / 8;
            if (p->largo - 2 >= mapa) {  // lo que ya mandé y no le llegó, se repite en la próxima pasada
                for (int i = 0; i < tx.siguiente; i++)
                    if ((p->cuerpo[2 + i / 8] >> (i % 8)) & 1) tx.faltan[i / 8] |= 1 << (i % 8);
            }
            bool quiere_hablar = p->largo - 2 > mapa && p->cuerpo[2 + mapa];
            tx.esperando = quiere_hablar ? VENTANA : NADA;
            tx.ventana_desde = ahora_ms();
            tx.alargues = 0;
            tx.limite = quiere_hablar ? ahora_ms() + MS_VENTANA : 0;
            break;
        }
        if (tx.fase != FOTO_PREGUNTA) break;
        int bytes = p->largo - 2;
        if (bytes == 0) {  // no la conoce (se reinició): de nuevo desde el comienzo
            if (++tx.rondas > RONDAS_FOTO) {
                terminar_tx(FALLIDO);
                break;
            }
            for (int i = 0; i < tx.trozos; i++) tx.faltan[i / 8] |= 1 << (i % 8);
            empezar_foto_normal(0);
            break;
        }
        int mapa = (tx.trozos + 7) / 8;
        memset(tx.faltan, 0, sizeof(tx.faltan));
        memcpy(tx.faltan, p->cuerpo + 2, bytes < mapa ? bytes : mapa);
        int faltan = contar_faltan(tx.faltan, tx.trozos);
        almacen_actualizar(tx.n, ENVIANDO, (tx.trozos - faltan) * 1000 / tx.trozos);
        if (faltan == 0) {
            terminar_tx(ENTREGADO);
        } else if (++tx.rondas > RONDAS_FOTO) {
            terminar_tx(FALLIDO);
        } else {
            tx.fase = FOTO_TROZOS;
            tx.siguiente = 0;
            tx.limite = 0;
            tx.alargues = 0;
            tx.esperando = NADA;
            tx.desde_consulta = 0;
            tx.sin_respuesta = 0;
            if (p->largo - 2 > mapa && p->cuerpo[2 + mapa]) {  // quiere hablar: primero su mensaje
                tx.esperando = VENTANA;
                tx.ventana_desde = ahora_ms();
                tx.limite = ahora_ms() + MS_VENTANA;
            }
        }
        break;
    }
    case COMANDO: {
        if (ya_visto(COMANDO, p->id)) break;
        char resp[MAX_CUERPO];
        const char *c = (const char *)p->cuerpo;
        unsigned nuevo;
        if (sscanf(c, "canal %x", &nuevo) == 1) {
            // El canal es FIJO: un aparato para encontrarse en emergencias no puede arriesgarse a quedar
            // en un canal distinto del otro. (Lo de abajo ya no se usa.)
            snprintf(resp, sizeof(resp), "canal: fijo en el %02X (906,15 MHz), así nunca quedan en canales distintos", CANAL_BASE);
            responder(RESPUESTA, p->id, resp, strlen(resp));
            break;
            if (nuevo < 0x34 || nuevo > 0x4D) {
                snprintf(resp, sizeof(resp), "canal: sólo 34..4D (902-927 MHz, banda legal en Chile)");
                responder(RESPUESTA, p->id, resp, strlen(resp));
                break;
            }
            if ((int)nuevo == ajustes.canal && !radio.pendiente) {
                snprintf(resp, sizeof(resp), "canal: ya estoy en %02X", nuevo);
                responder(RESPUESTA, p->id, resp, strlen(resp));
                break;
            }
            snprintf(resp, sizeof(resp), "canal: paso a %02X en 3 s; si en 2 min no hay contacto vuelvo a %02X", nuevo, ajustes.canal);
            responder(RESPUESTA, p->id, resp, strlen(resp));
            vTaskDelay(pdMS_TO_TICKS(3000));
            radio.viejo = ajustes.canal;
            radio.nuevo = nuevo;
            bool ok = lora_fijar_canal(nuevo, true);
            anotar_nota('#', ok ? "a prueba" : "a prueba SIN OK", nuevo);
            xStreamBufferReset(entrada);  // lo que llegó por el canal viejo no confirma el nuevo
            lector_reiniciar();
            radio.pendiente = true;
            radio.limite = ahora_ms() + MS_REVERTIR_RADIO;
            ESP_LOGW(TAG, "canal %02X a prueba", nuevo);
            break;
        }
        comandos_ejecutar(c, resp, sizeof(resp));
        responder(RESPUESTA, p->id, resp, strlen(resp));
        break;
    }
    default:
        break;
    }
}

// ---------- lo de ella que no llegó: se reintenta mientras haya contacto, para siempre ----------
static void reintentar_pendientes(void)
{
    static uint32_t ns[MAX_REGISTROS];
    int k = almacen_fallidos(ns, MAX_REGISTROS);
    for (int i = 0; i < k; i++) {
        almacen_contar_intento(ns[i]);
        enlace_reintentar(ns[i]);
    }
}

static void tarea_enlace(void *arg)
{
    uint8_t b[128];
    paquete_t p;
    int64_t ultimo_byte = 0;
    programar_latido();
    lat.proximo = ahora_ms() + 5000 + esp_random() % 5000;  // el primero, poco después de encender
    esp_task_wdt_add(NULL);  // vigilante: si la radio se traba 20 s, el aparato se reinicia solo
    for (;;) {
        esp_task_wdt_reset();
        int64_t vuelta = ahora_ms();
        uint8_t fase0 = tx.fase;
        size_t n = xStreamBufferReceive(entrada, b, sizeof(b), pdMS_TO_TICKS(15));
        if (n) ultimo_byte = ahora_ms();
        else if (ultimo_byte && ahora_ms() - ultimo_byte > 800) {
            lector_reiniciar();
            ultimo_byte = 0;
        }
        for (size_t i = 0; i < n; i++) {
            if (!lector_byte(b[i], &p)) continue;
            if (p.rssi_ok) {
                rssi_bueno = p.rssi;
            } else {  // llegó algo pegado al paquete (p. ej. un "+++" que se escapó): ese byte no es la señal
                anotar_nota('?', "senal dudosa", p.rssi);
                p.rssi = rssi_bueno;
            }
            procesar(&p);
        }
        avanzar_tx();
        int64_t ahora = ahora_ms();
        if (com.pendiente && tx.fase == LIBRE && !txt.activo && turno_libre() && ahora >= com.limite) {
            if (com.intentos >= 3) {
                com.pendiente = false;
                char r[140];
                snprintf(r, sizeof(r), "\xE2\x9C\x97 su aparato no contest\xC3\xB3 \xE2\x80\x9C%.80s\xE2\x80\x9D (\xC2\xBF" "est\xC3\xA1 lejos?)", com.cmd);
                anotar_respuesta(r);
            } else {
                mandar(COMANDO, com.id, com.cmd, strlen(com.cmd));
                com.intentos++;
                com.limite = espera_respuesta(5 + strlen(com.cmd)) + ms_en_aire(5 + 150) + retroceso(com.intentos, 5 + strlen(com.cmd));
            }
        }
        if (hora_pendiente > 0 && tx.fase == LIBRE && !txt.activo && turno_libre() && ahora >= hora_limite &&
            ahora_unix()) {
            uint8_t c[4];
            poner32(c, (uint32_t)ahora_unix());
            mandar(HORA, id_nuevo(), c, 4);
            hora_limite = ahora + 3000;
            if (--hora_pendiente == 0) anotar_respuesta("\xE2\x9C\x93 su hora qued\xC3\xB3 igual a la tuya");
        }
        if (prueba_tx > 0 && tx.fase == LIBRE && !txt.activo && ahora_ms() > canal_libre + 400) {
            prueba_tx--;
            static uint8_t relleno[MAX_CUERPO];  // de paso, paquetes largos como un trozo de foto (~7 s en el aire)
            mandar(PING, id_nuevo(), relleno, prueba_largo > 5 ? prueba_largo - 5 : 0);
        }
        int64_t t = ahora_ms();
        if (rx.activa && t - rx.ultimo > MS_ABANDONAR_FOTO_RX) foto_rx_terminar(false);
        if (rx.activa && !rx.en_pausa && t - rx.ultimo > MS_FOTO_EN_PAUSA) {
            registro_t r;
            rx.en_pausa = true;
            if (almacen_obtener(rx.n, &r)) almacen_actualizar(rx.n, FALLIDO, r.progreso);  // "a medias: sigue sola"
        }
        if (radio.pendiente && t > radio.limite) {
            ESP_LOGW(TAG, "sin contacto en el canal %02X: vuelvo al %02X", radio.nuevo, radio.viejo);
            radio.pendiente = false;
            lora_fijar_canal(radio.viejo, true);
            xStreamBufferReset(entrada);
            lector_reiniciar();
            anotar_nota('#', "sin contacto: vuelvo", radio.viejo);
        }
        // Canal de emergencia: con un canal que no es el de fábrica, 30 min sin oír nada = volver al de
        // fábrica. El PC hace lo mismo, así que nunca pueden quedar para siempre en canales distintos.
        if (!radio.pendiente && ajustes.canal != CANAL_BASE && t - ultimo_contacto > ms_volver_base &&
            t > proximo_intento_base) {
            proximo_intento_base = t + 60000;
            ESP_LOGW(TAG, "%lld s sin contacto en el canal %02X: vuelvo al de fábrica %02X",
                     (long long)((t - ultimo_contacto) / 1000), ajustes.canal, CANAL_BASE);
            if (lora_fijar_canal(CANAL_BASE, true)) {
                anotar_nota('#', "canal de fabrica", ajustes.canal);
                ajustes.canal = CANAL_BASE;
                ajustes_guardar();
                xStreamBufferReset(entrada);
                lector_reiniciar();
            }
        }
        // turbo: ante silencio o demasiado tiempo, de vuelta al nivel 0 (el de máximo alcance)
        if (turbo.activo) {
            bool emitiendo = turbo.soy_emisor && tx.fase == FOTO_TROZOS;
            if ((!emitiendo && t - ultimo_rx > MS_TURBO_SILENCIO) || t - turbo.desde > MS_TURBO_MAXIMO) {
                ESP_LOGW(TAG, "turbo: silencio o tiempo máximo, vuelvo al nivel 0");
                cambiar_nivel(0);
                if (tx.fase == TURBO_PROBAR || tx.fase == TURBO_VOLVER) tx.fase = tx.fase == TURBO_VOLVER ? LIBRE : FOTO_INICIO;
            }
        }
        revisar_presencia();
        // Perdidos: ¿la radio propia sigue como debe? (canal, potencia, nivel...). Si algo se movió, se corrige y
        // se pueden volver a encontrar. Queda anotado en la bitácora ('@ revisar: N corregidos').
        if (!lat.cerca && t >= revisar_radio_proximo && tx.fase == LIBRE && !txt.activo && lora_listo()) {
            revisar_radio_proximo = t + MS_REVISAR_RADIO_PERDIDOS;
            lora_revisar_configuracion();
            if (protocolo_nivel() != 0) {
                protocolo_fijar_nivel(0);
                turbo.activo = false;
            }
            xStreamBufferReset(entrada);
            lector_reiniciar();
        }
        if (t >= lat.proximo && tx.fase == LIBRE && !txt.activo && lora_listo() && turno_libre()) mandar_latido();
        if (lat.hubo && t - lat.visto_ms < MS_PRESENTE && t - lat.ultimo_reintento > MS_REINTENTO_CERCA) {
            lat.ultimo_reintento = t;
            reintentar_pendientes();
        }
        int64_t dur = ahora_ms() - vuelta;
        if (dur > 10000) {  // nada normal la tiene ocupada tanto (lo más largo: esperar que salga un trozo, ~7 s)
            anotar_nota('!', NOMBRES_FASE[fase0], (int)dur);
            ESP_LOGW(TAG, "la tarea de radio estuvo %lld ms ocupada (fase %s)", (long long)dur, NOMBRES_FASE[fase0]);
        }
    }
}

static void al_recibir(const uint8_t *d, size_t n)
{
    xStreamBufferSend(entrada, d, n, 0);
}

void enlace_iniciar(enlace_aviso_t al_llegar, enlace_presencia_t al_cambiar_presencia)
{
    avisar = al_llegar;
    avisar_presencia = al_cambiar_presencia;
    entrada = xStreamBufferCreate(4096, 1);
    cola = xQueueCreate(128, sizeof(uint32_t));
    protocolo_fijar_nivel(0);
    lora_al_suceso(anotar_nota);
    lora_iniciar(al_recibir);
    xTaskCreate(tarea_enlace, "enlace", 8192, NULL, 6, NULL);
}

uint32_t enlace_mandar_texto(const char *texto)
{
    registro_t r = {.de = DE_ELLA, .tipo = ES_TEXTO, .estado = ENVIANDO};
    r.ts = ahora_unix();
    strlcpy(r.texto, texto, sizeof(r.texto));
    uint32_t n = almacen_agregar(&r);
    xQueueSend(cola, &n, 0);
    return n;
}

uint32_t enlace_mandar_foto(const uint8_t *jpg, size_t largo)
{
    if (largo == 0 || largo > MAX_FOTO) return 0;
    registro_t r = {.de = DE_ELLA, .tipo = ES_FOTO, .estado = ENVIANDO};
    r.ts = ahora_unix();
    snprintf(r.texto, sizeof(r.texto), "foto");
    uint32_t n = almacen_agregar(&r);
    if (!almacen_guardar_foto(n, jpg, largo)) {
        almacen_actualizar(n, FALLIDO, 0);
        return n;
    }
    xQueueSend(cola, &n, 0);
    return n;
}

void enlace_reintentar(uint32_t n)
{
    registro_t r;
    if (!almacen_obtener(n, &r) || r.de != DE_ELLA || r.estado != FALLIDO) return;
    almacen_actualizar(n, ENVIANDO, 0);  // así no entra dos veces a la cola
    if (xQueueSend(cola, &n, 0) != pdTRUE) almacen_actualizar(n, FALLIDO, 0);  // cola llena: la próxima vuelta
}

void enlace_recibos_ya(void) { recibos_proximo = 0; }

static void marcar_olvidado(registro_t *r, void *arg)
{
    r->estado = FALLIDO;
    r->intentos = 255;  // ya no se reintenta solo
}

void enlace_olvidar_pendientes(void)
{
    xQueueReset(cola);
    if (txt.activo) {
        almacen_actualizar(txt.n, FALLIDO, 0);
        txt.activo = false;
    }
    if (tx.fase != LIBRE && tx.fase != RECIBO_ESPERA) terminar_tx(FALLIDO);
    static registro_t l[80];
    uint32_t ult = almacen_ultimo();
    int k = almacen_desde(ult > 80 ? ult - 80 : 0, l, 80);
    for (int i = 0; i < k; i++)
        if (l[i].de == DE_ELLA && (l[i].estado == FALLIDO || l[i].estado == ENVIANDO))
            almacen_modificar(l[i].n, marcar_olvidado, NULL, i == k - 1);
    almacen_cerrar();  // graba todo junto
}

void enlace_bitacora(void (*escribir)(const char *))
{
    int64_t t = esp_timer_get_time() / 1000;
    char l[160];
    for (int k = 0; k < LARGO_BITACORA; k++) {
        anotacion_t *a = &bitacora[(pos_bitacora + k) % LARGO_BITACORA];
        if (!a->ms) continue;
        if (a->nota)
            snprintf(l, sizeof(l), "@@B %7.1fs NOTA  %c %s (%ld) fase=%s nivel=%u\n", (t - a->ms) / -1000.0, a->tipo,
                     a->txt, (long)a->valor, NOMBRES_FASE[a->fase], a->nivel);
        else
            snprintf(l, sizeof(l), "@@B %7.1fs %s %c id=%04x largo=%u rssi=%d fase=%s nivel=%u\n", (t - a->ms) / -1000.0,
                     a->salio ? "SALE " : "LLEGA", a->tipo, a->id, a->largo, a->rssi, NOMBRES_FASE[a->fase], a->nivel);
        escribir(l);
    }
    escribir("@@FIN\n");
}

void enlace_al_transmitir(enlace_tx_t cb) { al_transmitir = cb; }

bool enlace_mandar_comando(const char *cmd)
{
    if (com.pendiente || !cmd[0]) return false;
    strlcpy(com.cmd, cmd, sizeof(com.cmd));
    com.id = id_nuevo();
    com.intentos = 0;
    com.limite = 0;
    com.pendiente = true;
    return true;
}

void enlace_mandar_hora(void)
{
    hora_pendiente = 2;
    hora_limite = 0;
}

// Para la página: "latidos":[...], "respuestas":[...], "radio_pc":{...}
void enlace_extra_json(char *o, size_t cap)
{
    size_t u = snprintf(o, cap, "\"latidos\":[");  // los que llegan llevan la señal
    for (int i = 0; i < n_latidos_log && u + 64 < cap; i++) {
        if (latidos_log[i].sale)
            u += snprintf(o + u, cap - u, "%s{\"dir\":\"sale\",\"t\":%lu}", i ? "," : "", (unsigned long)latidos_log[i].t);
        else
            u += snprintf(o + u, cap - u, "%s{\"dir\":\"llega\",\"t\":%lu,\"rssi\":%d}", i ? "," : "",
                          (unsigned long)latidos_log[i].t, latidos_log[i].rssi);
    }
    u += snprintf(o + u, cap > u ? cap - u : 0, "],\"respuestas\":[");
    for (int i = 0; i < n_respuestas && u + 200 < cap; i++) {
        u += snprintf(o + u, cap - u, "%s{\"t\":%lu,\"texto\":\"", i ? "," : "", (unsigned long)respuestas[i].t);
        for (const char *s = respuestas[i].texto; *s && u + 8 < cap; s++) {
            if (*s == '"' || *s == '\\') o[u++] = '\\';
            if ((unsigned char)*s >= 32) o[u++] = *s;
        }
        u += snprintf(o + u, cap - u, "\"}");
    }
    snprintf(o + u, cap > u ? cap - u : 0, "],\"radio_pc\":{\"potencia\":%d,\"nivel\":%d,\"latido_s\":%d,\"canal\":\"%02X\","
             "\"salen\":%d,\"llegan\":%d,\"desde\":0}", ajustes.potencia, protocolo_nivel(), ajustes.latido_s, ajustes.canal,
             latidos_salen, latidos_llegan);
}
void enlace_prueba_tx(int n, int largo)
{
    prueba_largo = largo > MAX_PAQUETE ? MAX_PAQUETE : largo;
    prueba_tx = n;
}

void enlace_prueba_canal_base(int segundos)
{
    ms_volver_base = (int64_t)(segundos > 0 ? segundos : 1800) * 1000;
    proximo_intento_base = 0;
}

void enlace_depurar(char *o, size_t n)
{
    int64_t t = ahora_ms();
    snprintf(o, n, "{\"fase\":\"%s\",\"tx_n\":%lu,\"intentos\":%d,\"rondas\":%d,\"rx_foto\":%s,\"cola\":%d,"
                   "\"turno\":%s,\"ventana_ms\":%lld,\"desde_rx_ms\":%lld,\"lora\":%s,\"rssi\":%d,\"radio_pendiente\":%s,"
                   "\"nivel\":%d,\"cerca\":%s,\"rssi_suave\":%d,\"alla\":%d,\"latido_en_s\":%lld",
             NOMBRES_FASE[tx.fase], (unsigned long)tx.n, tx.intentos, tx.rondas, rx.activa ? "true" : "false",
             (int)uxQueueMessagesWaiting(cola), turno_libre() ? "true" : "false",
             (long long)(ventana_hasta > t ? ventana_hasta - t : 0), (long long)(t - ultimo_rx),
             lora_listo() ? "true" : "false", ultimo_rssi, radio.pendiente ? "true" : "false",
             protocolo_nivel(), lat.cerca ? "true" : "false", (int)lat.rssi_suave, lat.rssi_alla,
             (long long)((lat.proximo - t) / 1000));
}

void enlace_presencia_json(char *o, size_t cap)
{
    int64_t t = ahora_ms();
    size_t u = snprintf(o, cap,
                        "{\"cerca\":%s,\"hubo\":%s,\"visto_hace\":%lld,\"desde_hace\":%lld,\"rssi\":%d,\"alla\":%d,"
                        "\"potencia_otro\":%d,\"rssi_visto\":%d,\"visto_ts\":%lld,\"periodo\":%d,\"nivel\":%d,"
                        "\"turbo_posible\":%d,\"eventos\":[",
                        lat.cerca ? "true" : "false", lat.hubo ? "true" : "false",
                        (long long)(lat.hubo ? (t - lat.visto_ms) / 1000 : -1),
                        (long long)(lat.desde_ms ? (t - lat.desde_ms) / 1000 : -1), (int)lat.rssi_suave, lat.rssi_alla,
                        lat.potencia_otro, lat.rssi_visto, (long long)lat.visto_ts, ajustes.latido_s, protocolo_nivel(),
                        nivel_posible());
    for (int i = 0; i < lat.n_eventos && u + 80 < cap; i++)
        u += snprintf(o + u, cap - u, "%s{\"encuentro\":%s,\"ts\":%lld,\"hace\":%lld,\"rssi\":%d}", i ? "," : "",
                      lat.eventos[i].encuentro ? "true" : "false", (long long)lat.eventos[i].ts,
                      (long long)((t - lat.eventos[i].ms) / 1000), lat.eventos[i].rssi);
    snprintf(o + u, cap > u ? cap - u : 0, "]}");
}

void enlace_estado(enlace_estado_t *e)
{
    e->lora_conectado = lora_conectado();
    e->lora_listo = lora_listo();
    e->ultimo_rssi = ultimo_rssi;
    e->ultimo_contacto_ms = ultimo_contacto;
    e->cambio_radio_pendiente = radio.pendiente;
    e->cerca = lat.cerca;
    e->hubo_contacto = lat.hubo;
    e->rssi_suave = (int)lat.rssi_suave;
    e->rssi_alla = lat.rssi_alla;
    e->potencia_otro = lat.potencia_otro;
    e->visto_ms = lat.visto_ms;
    e->latido_mandado_ms = lat.mandado_ms;
    e->nivel = protocolo_nivel();
    e->pendientes = almacen_pendientes();
}
