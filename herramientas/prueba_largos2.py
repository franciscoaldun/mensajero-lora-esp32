# ¿El atasco depende del largo del paquete o de cuántos bytes lleva el módulo desde que arrancó?
# Reinicia el módulo (entra y sale del modo AT) y manda pings de los largos pedidos, varias veces.
#   python herramientas/prueba_largos2.py 31 31 31 30 32 29 54 63 --veces 3
import os, random, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import serial
import protocolo as p
from radio_pc import buscar_puerto

args = sys.argv[1:]
relleno = "--relleno" in args  # con el arreglo: un 0x00 adelante si el largo cae en 31 (mod 32)
args = [a for a in args if a != "--relleno"]
veces = 1
if "--veces" in args:
    i = args.index("--veces")
    veces = int(args[i + 1])
    args = args[:i] + args[i + 2:]
largos = [int(x) for x in args] * veces
s = serial.Serial(buscar_puerto(), 9600, timeout=0.05)
lector = p.Lector()
pendientes = {}
llegados = {}


def escuchar(seg):
    fin = time.time() + seg
    while time.time() < fin:
        for tipo, ident, cuerpo, rssi in lector.alimentar(s.read(256)):
            if tipo == p.PONG and ident in pendientes:
                n, largo, t = pendientes.pop(ident)
                llegados[n] = time.time() - t


def hablar(d, espera, fin):
    s.reset_input_buffer()
    s.write(d)
    t0, r = time.time(), b""
    while time.time() - t0 < espera:
        r += s.read(256)
        if fin in r:
            break
    return r


print("reinicio del módulo:", hablar(b"+++\r\n", 2, b"Entry")[-20:], hablar(b"+++\r\n", 2.5, b"Power on")[-20:])
time.sleep(0.5)
lector = p.Lector()
total = 0
for n, largo in enumerate(largos):
    ident = random.randint(1, 0xFFFF)
    paq = p.empaquetar(p.PING, ident, bytes(65 + i % 26 for i in range(largo - 5)))
    if relleno and len(paq) % 32 == 31:
        paq = b"\x00" + paq
    pendientes[ident] = (n, largo, time.time())
    s.write(paq)
    total += largo
    limite = p.segundos_en_aire(largo) + 3.5
    escuchar(limite)
    print(f"{n:3d} largo {largo:3d} · bytes desde el arranque {total:5d} (mod 32 = {total % 32:2d}, mod 64 = {total % 64:2d}) · "
          f"{'✅ %.1f s' % llegados[n] if n in llegados else '❌ atascado'}", flush=True)
escuchar(8)
print("tarde (lo soltó el siguiente):", {n: round(t, 1) for n, t in llegados.items() if t > p.segundos_en_aire(largos[n]) + 3.5})
print("nunca llegaron:", sorted(v[0] for v in pendientes.values()))
