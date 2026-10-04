#pragma once
// Todo lo que pasa por la radio: mensajes con acuse, fotos por trozos, latido (¿está cerca?),
// recibos de lectura, hora, comandos a distancia y turbo para fotos (sólo con señal de sobra).
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

typedef struct {
    bool lora_conectado, lora_listo;
    int ultimo_rssi;             // 1 = todavía nada
    int64_t ultimo_contacto_ms;  // 0 = nunca (desde que se encendió)
    bool cambio_radio_pendiente;
    // presencia (latido)
    bool cerca, hubo_contacto;
    int rssi_suave, rssi_alla;   // lo que oigo de él / con qué señal me oye él (127 = no sé)
    int potencia_otro;           // dBm con que transmite él
    int64_t visto_ms;            // última vez que lo oí (reloj desde el encendido)
    int64_t latido_mandado_ms;   // último latido que mandé (0 = ninguno)
    int nivel;                   // nivel de radio ahora (0 = máximo alcance)
    int pendientes;              // mensajes de ella que todavía no le llegan
} enlace_estado_t;

typedef void (*enlace_aviso_t)(uint32_t n);                // llegó algo nuevo de él (mensaje o foto completa)
typedef void (*enlace_presencia_t)(bool cerca, int rssi);  // él apareció (cerca) o se perdió la señal

void enlace_iniciar(enlace_aviso_t al_llegar, enlace_presencia_t al_cambiar_presencia);
uint32_t enlace_mandar_texto(const char *texto);
uint32_t enlace_mandar_foto(const uint8_t *jpg, size_t largo);
void enlace_reintentar(uint32_t n);
void enlace_recibos_ya(void);  // ella acaba de leer algo: mandarle los recibos sin esperar
void enlace_estado(enlace_estado_t *e);
void enlace_depurar(char *salida, size_t largo);         // JSON sin cerrar, para la consola
void enlace_presencia_json(char *salida, size_t largo);  // JSON completo, para la página
void enlace_olvidar_pendientes(void);  // vacía la cola y deja de reintentar lo que no llegó
void enlace_bitacora(void (*escribir)(const char *));  // lo último que se mandó y llegó (consola)
void enlace_prueba_canal_base(int segundos);  // acorta el canal de emergencia para probarlo (0 = 30 min)
typedef void (*enlace_tx_t)(int ms);    // va a transmitir durante ~ms
void enlace_al_transmitir(enlace_tx_t cb);
void enlace_prueba_tx(int n, int largo);
// Aparato de él: comandos y hora para el aparato de ella, y datos para su página
bool enlace_mandar_comando(const char *cmd);  // false = ya hay uno esperando respuesta
void enlace_mandar_hora(void);
void enlace_extra_json(char *salida, size_t largo);  // n transmisiones seguidas a la potencia de siempre (prueba de esfuerzo)

// Lo implementa main.c: comandos que llegan desde el PC ("estado", "brillo ...", "girar", "wifi ...")
void comandos_ejecutar(const char *cmd, char *respuesta, size_t largo);
