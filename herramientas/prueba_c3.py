# Pruebas reales C3 (él, COM7) <-> S3 (ella, COM8): mensajes en los dos sentidos, ✓✓, leído y ráfagas.
import json, re, sys, time
import serial
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from s3 import S3

ella = S3()
time.sleep(0.5)
c3 = serial.Serial()
c3.port, c3.baudrate, c3.timeout, c3.dtr, c3.rts = "COM7", 115200, 0.2, False, False
c3.open()


def c3_cmd(cmd, seg=2.5):
    c3.reset_input_buffer()
    c3.write((cmd + "\n").encode("utf-8"))
    t, b = time.time(), b""
    while time.time() - t < seg:
        b += c3.read(4096)
    return b.decode("utf-8", "replace")


def c3_lista(k=15):
    return [json.loads(m) for m in re.findall(r"@@L (\{.*\})", c3_cmd(f"lista {k}", 3))]


def esperar(cond, seg):
    t0 = time.time()
    while time.time() - t0 < seg:
        r = cond()
        if r:
            return time.time() - t0, r
        time.sleep(1.5)
    return None, None


res = []


def anotar(nombre, ok, detalle=""):
    res.append(ok)
    print(f"{'✅' if ok else '❌'} {nombre}: {detalle}", flush=True)


marca = str(int(time.time()) % 1000)
# 1) él (C3) -> ella
txt = f"hola amor desde el C3 💙 {marca}"
c3_cmd(f"ella {txt}", 1)
t, r = esperar(lambda: [x for x in (ella.lista(5) or []) if x.get("texto") == txt], 60)
t2, r2 = esperar(lambda: [x for x in c3_lista(5) if x.get("texto") == txt and x.get("estado") == 1], 30)
anotar("1 mensaje C3 → aparato de ella", t is not None and t2 is not None, f"llegó en {t and round(t)} s · ✓✓ en el C3: {t2 is not None}")
# 2) ella -> él (C3)
txt = f"hola desde el aparato de ella 🥺 {marca}"
ella.ella(txt)
t, r = esperar(lambda: [x for x in c3_lista(5) if x.get("texto") == txt], 60)
t2, r2 = esperar(lambda: [x for x in (ella.lista(5) or []) if x.get("texto") == txt and x.get("estado") == 1], 30)
anotar("2 mensaje aparato de ella → C3", t is not None and t2 is not None, f"llegó en {t and round(t)} s · ✓✓ en el de ella: {t2 is not None}")
# 3) leído en los dos sentidos
c3_cmd("leer", 1)
ella.comando("estado")
t, r = esperar(lambda: [x for x in (ella.lista(8) or []) if x.get("texto", "").startswith("hola desde el aparato de ella") and x.get("texto", "").endswith(marca) and x.get("leido") == 1], 60)
ella._pedir("leer")
t2, r2 = esperar(lambda: [x for x in c3_lista(8) if x.get("texto", "").startswith("hola amor desde el C3") and x.get("texto", "").endswith(marca) and x.get("leido") == 1], 60)
anotar("3 recibos de leído en los dos sentidos", t is not None and t2 is not None, f"ella supo que él leyó: {t is not None} · él supo que ella leyó: {t2 is not None}")
# 4) ráfaga de 5 de cada uno al mismo tiempo
mios = [f"ráfaga C3 {marca}-{i}" for i in range(5)]
suyos = [f"ráfaga ella {marca}-{i}" for i in range(5)]
for i in range(5):
    c3_cmd(f"ella {mios[i]}", 0.3)
    ella.ella(suyos[i])
t, r = esperar(lambda: all(any(x.get("texto") == m for x in (ella.lista(25) or [])) for m in mios), 180)
t2, r2 = esperar(lambda: all(any(x.get("texto") == m for x in c3_lista(25)) for m in suyos), 180)
anotar("4 ráfaga de 5 de cada uno a la vez", t is not None and t2 is not None, f"los de él en {t and round(t)} s · los de ella en {t2 and round(t2)} s")
print("\nRESUMEN:", sum(res), "/", len(res))
print("C3:", [l for l in c3_cmd("comando estado").splitlines() if l.startswith("@@C")])
