# ¿Hay largos de paquete que el módulo del PC se guarda sin transmitir? Manda pings (P) con relleno para
# que el paquete mida 5..N bytes y mide cuánto tarda el pong (Q) del aparato. Sin la app abierta.
#   python herramientas/prueba_largos.py 20 45      largos de paquete 20 a 45
import os, random, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import serial
import protocolo as p
from radio_pc import buscar_puerto

desde = int(sys.argv[1]) if len(sys.argv) > 1 else 5
hasta = int(sys.argv[2]) if len(sys.argv) > 2 else 70
s = serial.Serial(buscar_puerto(), 9600, timeout=0.05)
lector = p.Lector()
pendientes = {}  # id -> (largo, hora de envío)
resultados = {}


def escuchar(seg):
    fin = time.time() + seg
    while time.time() < fin:
        for tipo, ident, cuerpo, rssi in lector.alimentar(s.read(256)):
            if tipo == p.PONG and ident in pendientes:
                largo, t = pendientes.pop(ident)
                resultados.setdefault(largo, []).append(time.time() - t)
                print(f"   pong del largo {largo}: {time.time() - t:.1f} s", flush=True)


escuchar(1)
for largo in range(desde, hasta + 1):
    ident = random.randint(1, 0xFFFF)
    paquete = p.empaquetar(p.PING, ident, bytes(range(65, 65 + largo - 5)))
    assert len(paquete) == largo
    pendientes[ident] = (largo, time.time())
    s.write(paquete)
    escuchar(p.segundos_en_aire(largo) + 3.5)
    if any(v[0] == largo for v in pendientes.values()):
        print(f"❌ largo {largo}: sin pong a los {p.segundos_en_aire(largo) + 3.5:.1f} s", flush=True)
    else:
        print(f"✅ largo {largo}", flush=True)
escuchar(8)
print("\nATASCADOS (el pong llegó tarde o nunca):",
      sorted({l for l, ts in resultados.items() if max(ts) > p.segundos_en_aire(l) + 3.5} |
             {l for l, t in pendientes.values()}))
