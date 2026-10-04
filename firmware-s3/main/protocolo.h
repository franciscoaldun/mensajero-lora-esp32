#pragma once
// Mismo formato que protocolo.py en el PC:
//   0xD5 | LEN | tipo | id(2, little endian) | cuerpo
// Al recibir, el módulo agrega 1 byte de señal al final (dBm = -(255 - byte)).
//
// Tipos y cuerpos:
//   M mensaje   texto UTF-8                         -> se contesta A
//   A acuse     rssi(int8) con que llegó (en fotos/velocidad: ver abajo)
//   P ping      -                                    -> se contesta Q
//   Q pong      rssi(int8)
//   T hora      unix(uint32)
//   F foto      foto(2) bytes(4) trozos(2)           -> se contesta A
//   D trozo     foto(2) indice(2) datos(<=220)       (sin acuse: se piden los que falten)
//   K ¿falta?   foto(2)                              -> se contesta R
//   R faltan    foto(2) mapa de bits (1 = falta)     todo en 0 = la foto llegó completa
//   C comando   texto ("estado", "reiniciar", "canal NN", "brillo N", ...) -> se contesta S
//   S respuesta texto
//   L latido    rssi_te_oigo(int8, 127 = nunca) seg_desde(uint16, 0xFFFF = nunca) mi_potencia(uint8)
//               (cada pocos minutos, sin acuse: para saber si el otro está cerca)
//   V velocidad nivel(uint8)  -> A con el nivel aceptado (0 = no). Los dos pasan a ese nivel de radio
//               mientras dura una foto; V 0 = volver al nivel 0 (máximo alcance).
//   E leído     seq(uint16) x k   -> A. "Leí tus mensajes con estos números" (recibo de lectura)
//   G grupo     cantidad(1) + [seq(2) hora(4) largo(1) texto] x cantidad -> A (varios mensajes de una vez)
//
// M lleva al comienzo del cuerpo seq(uint16) y la hora en que se escribió (uint32, 0 = no se sabía);
// F lleva al final seq(uint16) y hora(uint32). Con el seq el que recibe nunca muestra algo repetido.
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#define MAGICO 0xD5
#define MAX_PAQUETE 230
#define MAX_CUERPO (MAX_PAQUETE - 5)
#define TROZO 220
#define NIVEL_MAX_TURBO 3  // sobre el 3 el cable serie a 9600 baudios ya no da abasto

enum {
    MENSAJE = 'M', ACUSE = 'A', PING = 'P', PONG = 'Q', HORA = 'T',
    FOTO = 'F', TROZO_FOTO = 'D', PREGUNTA_FOTO = 'K', FALTAN = 'R', COMANDO = 'C', RESPUESTA = 'S',
    LATIDO = 'L', VELOCIDAD = 'V', LEIDO = 'E', GRUPO = 'G',
};

typedef struct {
    char tipo;
    uint16_t id;
    uint8_t cuerpo[MAX_CUERPO + 1];  // siempre termina en 0, para usarlo como texto
    int largo;
    int rssi;
    bool rssi_ok;  // el byte de señal es creíble (si llega basura pegada al paquete, ese byte no es la señal)
} paquete_t;

// Canal de fábrica (38 = 906,15 MHz). Con otro canal, 30 min sin contacto = los dos vuelven a éste.
#define CANAL_BASE 0x38

int empaquetar(char tipo, uint16_t id, const void *cuerpo, int largo, uint8_t *salida);  // bytes a mandar
void lector_reiniciar(void);
bool lector_byte(uint8_t b, paquete_t *p);

// Tiempo que ocupa un paquete en el aire con el nivel de radio actual (AT+LEVEL del módulo)
int ms_en_aire(int bytes);
void protocolo_fijar_nivel(int nivel);
int protocolo_nivel(void);
