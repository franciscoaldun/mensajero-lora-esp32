# OJO: esta versión sólo mide al final y los tiempos salen inflados. Para medir de verdad: prueba_turnos2.py
# ¿Los dos pueden hablar a la vez? Reproduce lo que pasó el 03-oct: ella escribe muchos mensajes seguidos y los
# de él se quedan atascados minutos. Uno "inunda" (un mensaje cada `cada` s) y el otro manda unos pocos al medio;
# se mide cuánto tarda en quedar entregado (✓✓) cada mensaje de cada lado.
#   python herramientas/prueba_turnos.py                 ella inunda, él manda 3
#   python herramientas/prueba_turnos.py --inunda el     al revés
#   python herramientas/prueba_turnos.py --cada 2 --n 15 --m 3
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3 import S3

ap = argparse.ArgumentParser()
ap.add_argument("--inunda", choices=["ella", "el"], default="ella")
ap.add_argument("--cada", type=float, default=2.0)
ap.add_argument("--n", type=int, default=15)
ap.add_argument("--m", type=int, default=3)
ap.add_argument("--max", type=float, default=300)
ap.add_argument("--salida", default=None)
a = ap.parse_args()

el = S3("COM7")
ella = S3("COM8")
time.sleep(0.5)
marca = str(int(time.time()) % 10000)
inunda, otro = (ella, el) if a.inunda == "ella" else (el, ella)
nom_i, nom_o = (a.inunda, "el" if a.inunda == "ella" else "ella")

enviados = []  # (quien, dispositivo, n, texto, t_envio)
t0 = time.time()
plan = [(i * a.cada, inunda, nom_i, f"{nom_i}{marca}-{i}") for i in range(a.n)]
plan += [(3 + j * 4, otro, nom_o, f"{nom_o}{marca}-{j}") for j in range(a.m)]
plan.sort(key=lambda x: x[0])
for t, d, quien, texto in plan:
    while time.time() - t0 < t:
        time.sleep(0.05)
    n = d.ella(texto)
    enviados.append([quien, d, n, texto, time.time() - t0, None])
    print(f"{time.time() - t0:6.1f}s {quien} manda {texto} (n={n})", flush=True)

# seguimiento: cuándo queda entregado (estado 1) en quien lo mandó
pend = list(enviados)
while pend and time.time() - t0 < a.max:
    for d in (el, ella):
        mios = [e for e in pend if e[1] is d]
        if not mios:
            continue
        lista = d.lista(60) or []
        por_n = {x["n"]: x for x in lista}
        for e in mios:
            x = por_n.get(e[2])
            if x and x.get("estado") == 1:
                e[5] = time.time() - t0
                pend.remove(e)
                print(f"{e[5]:6.1f}s ✓✓ {e[0]} {e[3]} (tardó {e[5] - e[4]:.1f} s, intentos {x.get('intentos')})", flush=True)
    time.sleep(1.5)

print("\n--- resumen")
for quien in ("ella", "el"):
    tiempos = [e[5] - e[4] for e in enviados if e[0] == quien and e[5] is not None]
    faltan = [e[3] for e in enviados if e[0] == quien and e[5] is None]
    if tiempos or faltan:
        print(f"{quien}: entregados {len(tiempos)}/{len(tiempos) + len(faltan)}"
              + (f" · prom {sum(tiempos) / len(tiempos):.1f} s · peor {max(tiempos):.1f} s" if tiempos else "")
              + (f" · SIN ENTREGAR: {faltan}" if faltan else ""))
if a.salida:
    with open(a.salida, "w", encoding="utf-8") as f:
        for nombre, d in (("EL", el), ("ELLA", ella)):
            f.write(f"===== bitácora {nombre}\n")
            for l in d.bitacora():
                f.write(l + "\n")
            f.write(f"===== estado {nombre}: {json.dumps(d.estado())}\n")
