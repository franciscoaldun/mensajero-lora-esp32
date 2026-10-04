#pragma once
#define BRILLO_BAJO_MIN 60  // por debajo de esto la pantallita no se ve (antes venía en 25 y era casi negra)
// Red Wi-Fi propia + página que se abre sola (portal cautivo) para que ella lea, escriba y mande fotos.
// También: ajustes y actualización del firmware por Wi-Fi (con clave de administrador).
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    char ssid[33];
    char clave[65];      // < 8 caracteres = red abierta
    int brillo_bajo;     // 0..1000
    int brillo_alto;
    int minutos_alto;
    bool girada;
    bool colores_al_reves;  // inversión de color del panel
    int potencia;        // dBm de la radio (0..22)
    int canal;           // canal del módulo (0x34..0x4D = 902-927 MHz, banda legal en Chile)
    int latido_s;        // cada cuántos segundos se dan la mano (para saber si el otro está cerca)
    bool turbo;          // fotos más rápidas cuando la señal sobra (siempre vuelve al máximo alcance)
    char otro[24];       // cómo se llama él en la pantallita y la página
} ajustes_t;

extern ajustes_t ajustes;
void ajustes_cargar(void);
void ajustes_guardar(void);

void portal_iniciar(void);
int portal_clientes(void);              // celulares conectados ahora
int64_t portal_ultima_visita_ms(void);  // última vez que la página preguntó por mensajes
bool portal_listo(void);
