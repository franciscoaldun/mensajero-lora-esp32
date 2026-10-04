# Formato de los paquetes por el aire. El firmware (firmware-s3/main/protocolo.c) hace exactamente lo mismo.
#
#   0xD5 | LEN | tipo | id(2, little endian) | cuerpo
#
# Al recibir, el módulo agrega 1 byte de señal (DRSSI): dBm = -(255 - byte)
#
#   M mensaje   seq(2) hora(4) texto UTF-8               -> A
#   A acuse     rssi(int8) con que llegó  (F: + "ya tengo una parte"(1) · V: nivel aceptado)
#   P ping      -                                        -> Q
#   Q pong      rssi(int8)
#   T hora      unix(uint32)
#   F foto      foto(2) bytes(4) trozos(2) seq(2) hora(4) -> A
#   D trozo     foto(2) indice(2) datos(<=220)           (sin acuse)
#   K ¿falta?   foto(2)  -> A (completa) · R mapa(1 = falta) + quiero_hablar(1) · R vacío = "no la conozco"
#   C comando   texto                                    -> S
#   S respuesta texto
#   L latido    rssi_te_oigo(int8, 127 = nunca) seg_desde(2, 0xFFFF = nunca) potencia(1) hora(4)
#   V velocidad nivel(1)  -> A nivel aceptado (0 = no). V 0 = volver al nivel 0
#   E leído     seq(2) x k                               -> A
import struct

MAGICO = 0xD5
SOBRECARGA = 11       # cabecera + seq + hora de un mensaje
MAX_TEXTO = 180
TROZO = 220
MAX_FOTO = 96 * 1024

MENSAJE, ACUSE, PING, PONG, HORA = b"M", b"A", b"P", b"Q", b"T"
FOTO, TROZO_FOTO, PREGUNTA_FOTO, FALTAN, COMANDO, RESPUESTA = b"F", b"D", b"K", b"R", b"C", b"S"
LATIDO, VELOCIDAD, LEIDO = b"L", b"V", b"E"
GRUPO = b"G"  # varios mensajes juntos: cantidad(1) + [seq(2) hora(4) largo(1) texto] x cantidad -> A
TIPOS = (MENSAJE, ACUSE, PING, PONG, HORA, FOTO, TROZO_FOTO, PREGUNTA_FOTO, FALTAN, COMANDO, RESPUESTA,
         LATIDO, VELOCIDAD, LEIDO, GRUPO)
MAX_CUERPO = 225  # el paquete entero no pasa de 230 bytes (largo de subpaquete del módulo)
MAX_GRUPO = 20


def armar_grupo(items):
    """items = [(seq, hora, texto_utf8)] -> cuerpo de un G"""
    c = bytes([len(items)])
    for seq, ts, tb in items:
        c += struct.pack("<HIB", seq, ts, len(tb)) + tb
    return c


def grupo_cuadra(cuerpo):
    """Un G llegó entero sólo si sus mensajes ocupan EXACTO el cuerpo (si se perdió un byte en el cable, no)."""
    if not cuerpo or cuerpo[0] == 0:
        return False
    pos = 1
    for _ in range(cuerpo[0]):
        if pos + 7 > len(cuerpo) or pos + 7 + cuerpo[pos + 6] > len(cuerpo):
            return False
        pos += 7 + cuerpo[pos + 6]
    return pos == len(cuerpo)


def abrir_grupo(cuerpo):
    """cuerpo de un G -> [(seq, hora, texto_utf8)] (lo que venga cortado se descarta)"""
    salida, pos = [], 1
    for _ in range(cuerpo[0] if cuerpo else 0):
        if pos + 7 > len(cuerpo):
            break
        seq, ts, n = struct.unpack("<HIB", cuerpo[pos:pos + 7])
        pos += 7
        if pos + n > len(cuerpo):
            break
        salida.append((seq, ts, cuerpo[pos:pos + n]))
        pos += n
    return salida

# Niveles del módulo (AT+LEVEL): SF, ancho de banda, tasa de código (1 = 4/5 ... 4 = 4/8).
# El 0 es el de máximo alcance; los otros sólo un rato, para fotos, con señal de sobra.
NIVELES = [(11, 125_000, 4), (11, 250_000, 1), (11, 500_000, 1), (8, 250_000, 1)]
NIVEL_MAX_TURBO = 3
NIVEL = 0  # el nivel en que está la radio ahora (lo cambia radio_pc)


def empaquetar(tipo: bytes, ident: int, cuerpo: bytes = b"") -> bytes:
    datos = tipo + struct.pack("<H", ident & 0xFFFF) + cuerpo
    return bytes([MAGICO, len(datos)]) + datos


def abrir(datos: bytes):
    """datos = lo que viene después de LEN. Devuelve (tipo, id, cuerpo) o None si no es nuestro."""
    if len(datos) < 3 or datos[:1] not in TIPOS:
        return None
    return datos[:1], struct.unpack("<H", datos[1:3])[0], datos[3:]


def segundos_en_aire(bytes_paquete: int, nivel=None) -> float:
    """Tiempo en el aire (fórmula de Semtech, preámbulo 8). En los niveles rápidos se suma el paso por
    el cable serie a 9600 baudios, que ahí ya pesa."""
    nivel = NIVEL if nivel is None else nivel
    sf, bw, cr = NIVELES[nivel]
    tsym = (2 ** sf) / bw
    de = 1 if tsym >= 0.016 else 0
    simbolos = 8 + max(-(-(8 * bytes_paquete - 4 * sf + 28 + 16) // (4 * (sf - 2 * de))) * (cr + 4), 0)
    t = (8 + 4.25 + simbolos) * tsym
    if nivel > 0:
        t += bytes_paquete * 10 / 9600 + 0.06
    return t


def segundos_foto(largo: int, nivel=0) -> float:
    trozos = -(-largo // TROZO)
    consultas = trozos // 4
    return (trozos * (segundos_en_aire(229, nivel) + 0.25) + consultas * (2 * segundos_en_aire(12, nivel) + 0.6)
            + 2 * segundos_en_aire(19, nivel) + 6)


CANAL_BASE = "38"  # canal de fábrica (906,15 MHz): con otro, 30 min sin contacto = los dos vuelven a éste


def rssi_creible(rssi):
    """El SX1262 no mide fuera de esto: si sale otra cosa, el byte que va después del paquete no era la
    señal (llegó algo pegado, p. ej. un "+++" que se escapó por el aire)."""
    return -130 <= rssi <= -1


class Lector:
    """Arma paquetes a partir del chorro de bytes del puerto serie."""

    def __init__(self):
        self.buf = bytearray()

    def alimentar(self, nuevos: bytes):
        """Devuelve lista de (tipo, id, cuerpo, rssi_dbm)."""
        self.buf += nuevos
        salida = []
        while True:
            i = self.buf.find(bytes([MAGICO]))
            if i < 0:
                self.buf.clear()
                return salida
            del self.buf[:i]
            if len(self.buf) < 2:
                return salida
            largo = self.buf[1]
            if largo < 3 or largo > 228:
                del self.buf[:1]
                continue
            if len(self.buf) < 2 + largo + 1:   # +1 por el byte de señal
                return salida
            r = abrir(bytes(self.buf[2:2 + largo]))
            if r is None:                       # basura o no es nuestro: saltar este 0xD5
                del self.buf[:1]
                continue
            rssi = -(255 - self.buf[2 + largo])
            del self.buf[:2 + largo + 1]
            salida.append((*r, rssi))
