#pragma once
// Historial de mensajes y fotos en la flash (partición "datos", LittleFS). Sobrevive a quedarse sin batería.
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#define MAX_REGISTROS 120  // el C3 no tiene PSRAM: menos mensajes en memoria (las fotos siguen en la flash)
#define MAX_TEXTO 200

// APARATO DE ÉL (C3): los valores van AL REVÉS que en el de ella, para no reescribir la lógica (ya probada):
// en el código "DE_ELLA" significa "lo mío" y "DE_EL" "lo del otro". Así lo mío queda con de=0 (él) y lo de
// ella con de=1, igual que en el PC y en la página.
enum { DE_EL = 1, DE_ELLA = 0 };                      // él = el PC; ella = la del celular (este aparato)
enum { ES_TEXTO = 0, ES_FOTO = 1 };
enum { ENVIANDO = 0, ENTREGADO = 1, FALLIDO = 2, RECIBIDO = 3 };

typedef struct {
    uint32_t n;          // número correlativo local (también nombra el archivo de la foto)
    uint8_t de, tipo, estado;
    int8_t rssi;
    int64_t ts;          // hora en que se escribió (la de quien lo mandó; 0 = no se sabía)
    int64_t ts_llegada;  // de él: cuándo llegó acá · de ella: cuándo se confirmó la entrega (0 = no se sabe)
    uint16_t progreso;   // fotos: 0..1000
    uint8_t intentos;    // reintentos hechos (se satura en 255)
    uint8_t leido;       // de él: ella lo leyó · de ella: él lo leyó (llegó su recibo)
    uint8_t recibo;      // de él: el recibo de lectura ya le llegó a él
    uint16_t seq;        // número del mensaje según quien lo mandó: con esto nunca se repite (0 = sin número)
    char texto[MAX_TEXTO + 1];
} registro_t;

void almacen_iniciar(void);
void almacen_cerrar(void);                       // antes de reiniciar
uint32_t almacen_agregar(const registro_t *r);   // devuelve n; a lo que manda ella le pone su seq
bool almacen_obtener(uint32_t n, registro_t *r);
void almacen_actualizar(uint32_t n, uint8_t estado, uint16_t progreso);

// Cambia lo que haga falta de un registro bajo el cerrojo. grabar = guardarlo ya en la flash.
typedef void (*almacen_cambio_t)(registro_t *r, void *arg);
bool almacen_modificar(uint32_t n, almacen_cambio_t f, void *arg, bool grabar);

bool almacen_buscar_seq(uint8_t de, uint16_t seq, registro_t *r);  // el más reciente con ese número
int almacen_desde(uint32_t desde_n, registro_t *salida, int max);  // los registros con n > desde_n
uint32_t almacen_ultimo(void);
bool almacen_ultimo_de(uint8_t de, registro_t *r);  // false si no hay
bool almacen_ultimo_mostrable(uint8_t de, registro_t *r);  // igual, pero sin fotos que quedaron cortadas
int almacen_contar(uint8_t de, uint8_t estado);     // cuántos de alguien están en ese estado
int almacen_pendientes(void);                       // de ella, todavía sin entregar (sin contar los "olvidados")
uint16_t almacen_dar_seq(uint32_t n);               // le pone número si no tenía; devuelve el número
int almacen_fallidos(uint32_t *ns, int max);        // de ella, "no llegó" y por reintentar (del más antiguo al más nuevo)

// Lectura: ella vio en el celular todo lo de él hasta n. Devuelve cuántos cambiaron.
int almacen_marcar_leidos(uint32_t hasta_n);
// Recibos de lectura que falta mandarle a él: llena seqs (máx. max) y devuelve cuántos.
int almacen_recibos_pendientes(uint16_t *seqs, int max);
void almacen_recibos_entregados(const uint16_t *seqs, int k);
// Él leyó los mensajes de ella con estos números.
int almacen_leidos_por_el(const uint16_t *seqs, int k);

void almacen_ruta_foto(uint32_t n, char *ruta, size_t largo);
bool almacen_guardar_foto(uint32_t n, const uint8_t *datos, size_t largo);
uint8_t *almacen_leer_foto(uint32_t n, size_t *largo);  // en PSRAM, liberar con free()
void almacen_contar_intento(uint32_t n);
void almacen_borrar_todo(void);  // historial vacío; la numeración sigue
void almacen_espacio(size_t *total, size_t *usado);
void almacen_bloquear(void);
void almacen_soltar(void);
