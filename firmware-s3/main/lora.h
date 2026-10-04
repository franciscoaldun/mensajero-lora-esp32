#pragma once
// El módulo LoRa va enchufado por USB (adaptador DX-PJ15, chip CH340) al puerto "USB" del S3.
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

typedef void (*lora_al_recibir_t)(const uint8_t *datos, size_t n);

void lora_iniciar(lora_al_recibir_t al_recibir);
bool lora_conectado(void);
bool lora_listo(void);              // conectado y con la configuración revisada
bool lora_mandar(const uint8_t *datos, size_t n);

// Modo AT (el envío normal queda pausado mientras tanto). Devuelve lo que respondió el módulo.
// comandos separados por ';'. La respuesta queda en `salida` (texto).
bool lora_comandos_at(const char *comandos, char *salida, size_t largo_salida);

// Revisa que el módulo tenga la configuración de siempre y corrige lo que no calce.
void lora_revisar_configuracion(void);

// Potencia que se exige al módulo (se revisa en cada conexión). aplicar = mandarla ya por AT.
void lora_fijar_potencia(int dbm, bool aplicar);
bool lora_fijar_canal(int canal, bool aplicar);  // false = el módulo no dijo OK

// Sucesos para la bitácora de radio: '@' sesión AT (qué y cuántos ms duró) · 'U' enchufado/desenchufado
typedef void (*lora_suceso_t)(char tipo, const char *texto, int ms);
void lora_al_suceso(lora_suceso_t cb);
