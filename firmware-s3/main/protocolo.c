#include <string.h>
#include <math.h>
#include "protocolo.h"

static bool tipo_valido(char t)
{
    return t != 0 && strchr("MAPQTFDKRCSLVEG", t) != NULL;
}

int empaquetar(char tipo, uint16_t id, const void *cuerpo, int largo, uint8_t *out)
{
    if (largo > MAX_CUERPO) largo = MAX_CUERPO;
    out[0] = MAGICO;
    out[1] = (uint8_t)(3 + largo);
    out[2] = (uint8_t)tipo;
    out[3] = id & 0xFF;
    out[4] = id >> 8;
    if (largo) memcpy(out + 5, cuerpo, largo);
    return 5 + largo;
}

static uint8_t buf[2 + 255 + 1];
static int lleno, esperado;

void lector_reiniciar(void) { lleno = 0; esperado = 0; }

static bool abrir(paquete_t *p)
{
    int largo = buf[1];
    if (largo < 3 || !tipo_valido((char)buf[2])) return false;
    p->tipo = (char)buf[2];
    p->id = buf[3] | (buf[4] << 8);
    p->largo = largo - 3 > MAX_CUERPO ? MAX_CUERPO : largo - 3;
    memcpy(p->cuerpo, buf + 5, p->largo);
    p->cuerpo[p->largo] = 0;
    p->rssi = -(255 - buf[2 + largo]);
    p->rssi_ok = p->rssi >= -130 && p->rssi <= -1;  // fuera de eso el SX1262 no mide: era otro byte
    return true;
}

bool lector_byte(uint8_t b, paquete_t *p)
{
    if (lleno == 0 && b != MAGICO) return false;
    buf[lleno++] = b;
    if (lleno == 2) {
        if (b < 3 || b > MAX_PAQUETE - 2) {  // largo imposible: no era un comienzo de paquete
            lector_reiniciar();
            return false;
        }
        esperado = 2 + b + 1;  // + byte de señal
    }
    if (lleno < 2 || lleno < esperado) return false;
    if (abrir(p)) {
        lector_reiniciar();
        return true;
    }
    // no era nuestro: buscar otro 0xD5 dentro de lo recibido
    int desde = 1;
    while (desde < lleno && buf[desde] != MAGICO) desde++;
    int resto = lleno - desde;
    uint8_t copia[sizeof(buf)];
    memcpy(copia, buf + desde, resto);
    lector_reiniciar();
    for (int i = 0; i < resto; i++)
        if (lector_byte(copia[i], p)) return true;
    return false;
}

// Niveles del módulo (manual, AT+LEVEL): SF, ancho de banda y tasa de código (1 = 4/5 ... 4 = 4/8).
// El 0 es el de máximo alcance; los otros sólo se usan un rato para fotos cuando la señal sobra.
static const struct { int sf, bw_hz, cr; } NIVELES[] = {
    {11, 125000, 4}, {11, 250000, 1}, {11, 500000, 1}, {8, 250000, 1},
};
static int nivel;

void protocolo_fijar_nivel(int n) { nivel = (n >= 0 && n <= NIVEL_MAX_TURBO) ? n : 0; }
int protocolo_nivel(void) { return nivel; }

// Fórmula de Semtech (preámbulo 8, cabecera explícita, CRC). En los niveles rápidos se suma lo que
// demora pasar el paquete por el cable serie a 9600 baudios, que ahí ya pesa.
int ms_en_aire(int bytes)
{
    int sf = NIVELES[nivel].sf, cr = NIVELES[nivel].cr;
    double tsym = (double)(1 << sf) / NIVELES[nivel].bw_hz * 1000.0;  // ms
    int de = tsym >= 16.0 ? 1 : 0;
    int num = 8 * bytes - 4 * sf + 28 + 16;
    int den = 4 * (sf - 2 * de);
    int sim = 8 + (num > 0 ? (num + den - 1) / den * (cr + 4) : 0);
    int ms = (int)((8 + 4.25 + sim) * tsym);
    if (nivel > 0) ms += bytes * 10 * 1000 / 9600 + 60;
    return ms;
}
