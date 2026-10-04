# Traza de la presencia con bitácora en los dos lados: el PC se "aleja" (pausa) y vuelve.
import sys, time, queue, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radio_pc
from s3 import S3

LATIDO = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ANTES, PAUSA, DESPUES = 70, 150, 60
d = S3()
time.sleep(0.5)
d.comando("olvidar")
print(d.comando(f"latido {LATIDO}"))
ev = queue.Queue()
r = radio_pc.Radio(ev, latido_s=LATIDO)
t0 = time.time()
om, orx = r.mandar, r.recibido


def mandar(tipo, ident, cuerpo=b""):
    print(f"{time.time() - t0:6.1f}s PC SALE  {tipo.decode()} {'(en pausa: no sale)' if r.pausada else ''}", flush=True)
    om(tipo, ident, cuerpo)


def recibido(tipo, ident, cuerpo, rssi):
    print(f"{time.time() - t0:6.1f}s PC LLEGA {tipo.decode()} {rssi} dBm", flush=True)
    orx(tipo, ident, cuerpo, rssi)


r.mandar, r.recibido = mandar, recibido
r.start()
time.sleep(ANTES)
print("--- PC en pausa (fuera de alcance) ---", flush=True)
r.pausada = True
time.sleep(PAUSA)
print("--- PC vuelve ---", flush=True)
r.pausada = False
time.sleep(DESPUES)
print("PC cerca:", r.lat["cerca"], r.lat["eventos"])
print("--- bitácora del S3 (segundos atrás) ---")
for l in d.bitacora():
    print("   ", l)
print(d.comando("latido 150"))
os._exit(0)
