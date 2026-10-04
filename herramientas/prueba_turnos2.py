# Igual que prueba_turnos.py, pero mide la entrega MIENTRAS se mandan (la otra sólo miraba al final y los
# tiempos salían inflados). Uno inunda (un mensaje cada `cada` s) y el otro manda unos pocos al medio.
#   python herramientas/prueba_turnos2.py --inunda ella|el --cada 2 --n 12 --m 3
import argparse, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3 import S3

ap = argparse.ArgumentParser()
ap.add_argument("--inunda", choices=["ella", "el"], default="ella")
ap.add_argument("--cada", type=float, default=2.0)
ap.add_argument("--n", type=int, default=12)
ap.add_argument("--m", type=int, default=3)
ap.add_argument("--max", type=float, default=150)
a = ap.parse_args()

el, ella = S3("COM7"), S3("COM8")
time.sleep(0.5)
cerrojo = {id(el): threading.Lock(), id(ella): threading.Lock()}
marca = str(int(time.time()) % 10000)
inunda, otro = (ella, el) if a.inunda == "ella" else (el, ella)
nom_i, nom_o = a.inunda, ("el" if a.inunda == "ella" else "ella")
plan = sorted([(i * a.cada, inunda, nom_i, f"{nom_i}{marca}-{i}") for i in range(a.n)] +
              [(3 + j * 4, otro, nom_o, f"{nom_o}{marca}-{j}") for j in range(a.m)], key=lambda x: x[0])
enviados, listo = [], threading.Event()
t0 = time.time()


def mandar_todo():
    for t, d, quien, texto in plan:
        while time.time() - t0 < t:
            time.sleep(0.05)
        with cerrojo[id(d)]:
            n = d.ella(texto)
        enviados.append([quien, d, n, texto, time.time() - t0, None])
    listo.set()


hilo = threading.Thread(target=mandar_todo)
hilo.start()
while time.time() - t0 < a.max:
    pend = [e for e in list(enviados) if e[5] is None]
    if listo.is_set() and not pend:
        break
    for d in (el, ella):
        mios = [e for e in pend if e[1] is d]
        if not mios:
            continue
        with cerrojo[id(d)]:
            por_n = {x["n"]: x for x in (d.lista(40) or [])}
        for e in mios:
            x = por_n.get(e[2])
            if x and x.get("estado") == 1 and e[5] is None:
                e[5] = time.time() - t0
                print(f"{e[5]:6.1f}s ✓✓ {e[0]} {e[3]} (tardó {e[5] - e[4]:.1f} s)", flush=True)
    time.sleep(0.2)
hilo.join()
print("--- resumen (la medición tiene ~1-2 s de retraso por la consulta)")
for quien in ("ella", "el"):
    tt = [e[5] - e[4] for e in enviados if e[0] == quien and e[5] is not None]
    faltan = [e[3] for e in enviados if e[0] == quien and e[5] is None]
    print(f"{quien}: entregados {len(tt)}/{len(tt) + len(faltan)}" +
          (f" · prom {sum(tt) / len(tt):.1f} s · peor {max(tt):.1f} s" if tt else "") +
          (f" · SIN ENTREGAR {faltan}" if faltan else ""))
