# Traza del cambio de canal: todo lo que el PC manda y recibe (y los bytes crudos), más la bitácora del S3.
# Uso: python traza_canal.py 38   (cambia los dos al canal 38 y prueba hablar ahí)
import sys, time, queue, os, struct, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radio_pc
import protocolo as p
from s3 import S3

NUEVO = (sys.argv[1] if len(sys.argv) > 1 else "38").upper()
d = S3()
time.sleep(0.5)
print("aparato:", d.comando("estado"))
ev = queue.Queue()
r = radio_pc.Radio(ev, latido_s=150)
t0 = time.time()
om, orx, ol = r.mandar, r.recibido, r.lector


def ts():
    return f"{time.time() - t0:7.2f}s"


def mandar(tipo, ident, cuerpo=b""):
    print(f"{ts()} PC SALE  {tipo.decode()} id={ident:04x} {len(cuerpo)} B nivel={r.nivel}", flush=True)
    om(tipo, ident, cuerpo)


def recibido(tipo, ident, cuerpo, rssi):
    print(f"{ts()} PC LLEGA {tipo.decode()} id={ident:04x} {len(cuerpo)} B {rssi} dBm", flush=True)
    orx(tipo, ident, cuerpo, rssi)


r.mandar, r.recibido = mandar, recibido

# bytes crudos que entrega el puerto (para ver si el módulo dice algo que el lector no entiende)
leer_original = None


def envolver_puerto():
    global leer_original
    s = r.puerto
    if s is None or getattr(s, "_trazado", False):
        return
    leer_original = s.read

    def leer(n):
        b = leer_original(n)
        if b:
            print(f"{ts()}   crudo {b[:60].hex(' ')}{' …' if len(b) > 60 else ''}", flush=True)
        return b

    s.read = leer
    s._trazado = True


r.start()
for _ in range(50):
    if r.puerto is not None:
        break
    time.sleep(0.2)
time.sleep(4)
envolver_puerto()


def esperar(cond, seg):
    fin = time.time() + seg
    while time.time() < fin:
        envolver_puerto()
        try:
            e = ev.get(timeout=0.5)
        except queue.Empty:
            continue
        if e[0] not in ("rssi", "progreso"):
            print(f"{ts()} EVENTO {e}", flush=True)
        if cond(e):
            return e
    return None


print("--- ping antes ---", flush=True)
r.encolar(clase="ping", ref="p0")
esperar(lambda e: e[0] == "ping", 60)
print(f"--- canal {NUEVO} ---", flush=True)
r.encolar(clase="canal", canal=NUEVO)
esperar(lambda e: e[0] == "canal", 180)
envolver_puerto()
print("--- texto en el canal nuevo ---", flush=True)
r.encolar(clase="texto", texto=f"por el canal {NUEVO} (traza)", seq=random.randint(40000, 60000), ts=int(time.time()), ref="t1")
esperar(lambda e: e[0] == "resultado" and e[1] == "t1", 120)
print("--- ping después ---", flush=True)
r.encolar(clase="ping", ref="p1")
esperar(lambda e: e[0] == "ping", 60)
time.sleep(2)
print("--- bitácora del S3 (segundos atrás) ---")
for l in d.bitacora()[-30:]:
    print("   ", l)
print("aparato:", d.comando("estado"))
os._exit(0)
