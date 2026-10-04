# ¿Aguanta el aparato transmitir a esta potencia sin colgarse? (la alimentación es el límite)
#   python herramientas/prueba_potencia.py 22 300          pings cada ~2,5 s durante 300 s
#   python herramientas/prueba_potencia.py 22 0 foto       el aparato manda una foto (transmisión pesada)
import io, os, queue, re, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
from PIL import Image, ImageDraw
from s3 import S3

DBM = int(sys.argv[1]) if len(sys.argv) > 1 else 22
SEG = int(sys.argv[2]) if len(sys.argv) > 2 else 300
FOTO = "foto" in sys.argv

spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(__file__), "..", "app_mensajes.pyw"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

s3 = S3()
time.sleep(0.5)
print("potencia:", s3.comando(f"potencia {DBM}"), "| módulo:", s3.at("AT+POWE"), flush=True)
s3.registro.clear()

ev = queue.Queue()
pc = app.Radio(ev)
pc.start()
time.sleep(1)


def leer_registro(segundos):
    fin = time.time() + segundos
    while time.time() < fin:
        s3._linea(0.2)


def cuelgues():
    return sum(1 for l in s3.registro if "Guru Meditation" in l or "rst:0x" in l)


t0 = time.time()
enviados = respondidos = 0
if FOTO:
    img = Image.new("RGB", (340, 250), (20, 30, 70))
    d = ImageDraw.Draw(img)
    for k in range(0, 340, 22):
        d.ellipse((k, 40 + k % 90, k + 60, 100 + k % 90), fill=(255, 150 + k // 4, 200))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=40)
    datos = b.getvalue()
    n = s3.foto(datos)
    print(f"foto de {len(datos)} B subida al aparato (registro {n}); manda {-(-len(datos) // 220)} trozos a {DBM} dBm", flush=True)
    llego = None
    while time.time() - t0 < 600 and not llego:
        leer_registro(1)
        try:
            while True:
                e = ev.get_nowait()
                if e[0] == "foto_de_ella":
                    llego = e
                elif e[0] == "foto_llegando" and e[1] % 20 == 0:
                    print(f"   {time.time() - t0:4.0f}s  PC recibió {e[1]}%  (cuelgues hasta ahora: {cuelgues()})", flush=True)
        except queue.Empty:
            pass
    igual = llego and open(llego[1], "rb").read() == datos
    print(f"RESULTADO {DBM} dBm foto: {'llegó idéntica' if igual else 'NO llegó'} en {time.time() - t0:.0f} s · cuelgues: {cuelgues()}")
    if llego:
        os.remove(llego[1])
else:
    while time.time() - t0 < SEG:
        pc.encolar(clase="ping")
        enviados += 1
        fin = time.time() + 6
        r = None
        while time.time() < fin and r is None:
            leer_registro(0.3)
            try:
                e = ev.get_nowait()
                if e[0] == "ping":
                    r = e
            except queue.Empty:
                pass
        respondidos += bool(r and r[1])
        leer_registro(max(0.0, 2.5 - 0.3))
        if enviados % 20 == 0:
            print(f"   {time.time() - t0:4.0f}s  {respondidos}/{enviados} pings · cuelgues: {cuelgues()}", flush=True)
    print(f"RESULTADO {DBM} dBm: {respondidos}/{enviados} pings respondidos · cuelgues del aparato: {cuelgues()}")
for l in s3.registro:
    if re.search(r"Guru|rst:0x|Backtrace", l):
        print("   ", l[:120])
