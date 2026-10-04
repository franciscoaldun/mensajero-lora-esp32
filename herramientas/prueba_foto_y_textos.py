# Prueba real: el PC manda una foto al aparato mientras ella escribe desde el celular.
# Los mensajes de ella tienen que llegar DURANTE la foto (por las ventanas), no después.
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
t0 = time.time()


def ts():
    return f"[{time.time() - t0:5.0f} s]"


img = Image.new("RGB", (340, 250), (30, 20, 60))
d = ImageDraw.Draw(img)
for k in range(0, 340, 25):
    d.ellipse((k, 50 + k % 80, k + 60, 110 + k % 80), fill=(255, 120 + k // 3, 190))
d.text((110, 15), "prueba con ventanas", fill=(255, 255, 255))
b = io.BytesIO()
img.save(b, "JPEG", quality=40)
datos = b.getvalue()
print(f"{ts()} foto de {len(datos)} bytes ({-(-len(datos) // 220)} trozos), estimado {app.p.segundos_foto(len(datos)):.0f} s", flush=True)
r.encolar(clase="foto", datos=datos, ref="f")
foto_lista = None
fin = time.time() + 15 * 60
while time.time() < fin:
    try:
        e = ev.get(timeout=0.5)
    except queue.Empty:
        continue
    if e[0] == "progreso":
        print(f"{ts()} foto {e[2]}%", flush=True)
    elif e[0] == "resultado":
        foto_lista = time.time()
        print(f"{ts()} FOTO {'ENTREGADA ✓✓' if e[2] else 'NO llegó'}", flush=True)
    elif e[0] == "de_ella":
        print(f"{ts()} MENSAJE DE ELLA: {e[1]!r} ({e[2]} dBm){'  <- durante la foto' if foto_lista is None else ''}", flush=True)
    elif e[0] == "foto_de_ella":
        print(f"{ts()} FOTO DE ELLA guardada en {e[1]}", flush=True)
    if foto_lista and time.time() - foto_lista > 120:
        break
print(f"{ts()} fin de la prueba", flush=True)
