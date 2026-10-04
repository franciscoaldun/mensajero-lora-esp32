# Prueba real: PC (módulo en COM9) <-> radio <-> S3. Usa la misma lógica que la app.
#   python herramientas/prueba_punta_a_punta.py
import io, os, queue, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import importlib.util
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(__file__), "..", "app_mensajes.pyw"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

ev = queue.Queue()
r = app.Radio(ev)
r.start()


def esperar(tipos, segundos):
    fin = time.time() + segundos
    otros = []
    while time.time() < fin:
        try:
            e = ev.get(timeout=0.3)
        except queue.Empty:
            continue
        if e[0] in tipos:
            for o in otros:
                ev.put(o)
            return e
        if e[0] in ("de_ella", "foto_de_ella"):
            print("   (llegó de ella:", e[1], ")")
        otros.append(e)
    return None


print("1) PING x5")
for i in range(5):
    t0 = time.time()
    r.encolar(clase="ping")
    e = esperar(("ping",), 10)
    print("   ", f"OK señal acá {e[1][0]} dBm, allá {e[1][1]} dBm, {e[1][2]:.2f} s" if e and e[1] else "sin respuesta")

print("2) COMANDO estado")
r.encolar(clase="comando", texto="estado")
e = esperar(("respuesta",), 30)
print("   ", e[1] if e else "sin respuesta")

print("3) MENSAJE")
t0 = time.time()
r.encolar(clase="texto", texto="Hola amor 💜 esto es una prueba desde el PC, ¿se ve bien la ñ y las tildes?", ref="t")
e = esperar(("resultado",), 60)
print("   ", f"{'✓✓ le llegó' if e and e[2] else 'NO llegó'} ({time.time() - t0:.1f} s, señal allá {e[3] if e else '-'})")

print("4) FOTO chica (Rápida)")
img = Image.new("RGB", (220, 160), (40, 20, 70))
d = ImageDraw.Draw(img)
for k in range(0, 220, 20):
    d.ellipse((k, 40 + (k % 60), k + 50, 90 + (k % 60)), fill=(255, 120 + k // 3, 180))
d.text((60, 10), "prueba LoRa", fill=(255, 255, 255))
b = io.BytesIO()
img.save(b, "JPEG", quality=45)
datos = b.getvalue()
print(f"   {len(datos)} bytes, estimado {app.p.segundos_foto(len(datos)):.0f} s")
t0 = time.time()
r.encolar(clase="foto", datos=datos, ref="f")
while True:
    e = esperar(("resultado", "progreso"), 600)
    if not e:
        print("   sin noticias")
        break
    if e[0] == "progreso":
        print(f"   {e[2]}%", end="", flush=True)
        continue
    print(f"\n   {'✓✓ foto entregada' if e[2] else 'NO llegó'} en {time.time() - t0:.0f} s")
    break

print("5) ¿llegó algo de ella? (escuchando 20 s)")
e = esperar(("de_ella", "foto_de_ella"), 20)
print("   ", e[1] if e else "nada en ese rato")
