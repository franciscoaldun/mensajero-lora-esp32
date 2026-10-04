# Sirve la página con un aparato de mentira para revisarla en el PC antes de grabarla.
#   python web/simulador.py   ->  http://localhost:8765
import io, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageDraw

INICIO = time.time()
img = Image.new("RGB", (340, 250), (60, 30, 90))
d = ImageDraw.Draw(img)
for k in range(0, 340, 30):
    d.ellipse((k, 60 + k % 70, k + 70, 130 + k % 70), fill=(255, 130, 190))
b = io.BytesIO()
img.save(b, "JPEG", quality=50)
FOTO = b.getvalue()


def mensajes():
    t0 = int(time.time()) - 3600
    prog = min(1000, int((time.time() - INICIO) * 40))
    m = [
        {"n": 1, "de": 0, "tipo": 0, "estado": 3, "rssi": -97, "ts": t0, "prog": 0, "texto": "Hola amor 💜 ¿cómo te fue hoy?"},
        {"n": 2, "de": 1, "tipo": 0, "estado": 1, "rssi": 0, "ts": t0 + 60, "prog": 0, "texto": "hola mi vidaaaa, bien y tú?"},
        {"n": 3, "de": 0, "tipo": 1, "estado": 3, "rssi": -99, "ts": t0 + 120, "prog": 1000, "texto": "foto"},
        {"n": 4, "de": 1, "tipo": 0, "estado": 2, "rssi": 0, "ts": t0 + 200, "prog": 0, "texto": "este no llegó"},
    ]
    for i in range(5, 25):
        m.append({"n": i, "de": i % 2, "tipo": 0, "estado": 1 if i % 2 else 3, "rssi": -100, "ts": t0 + 300 + i * 30,
                  "prog": 0, "texto": f"mensaje de relleno número {i} para ver el scroll"})
    m.append({"n": 25, "de": 0, "tipo": 1, "estado": 0 if prog < 1000 else 3, "rssi": -101, "ts": t0 + 2000,
              "prog": prog, "texto": "foto"})
    return m


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def enviar(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.enviar(200, "text/html; charset=utf-8", open(__file__.replace("simulador.py", "_debug.html" if "debug" in self.path else "index.html"), "rb").read())
        elif self.path.startswith("/api/estado"):
            j = {"hora": int(time.time()), "lora": True, "modulo": True, "rssi": -101, "contacto": 12,
                 "radio_pendiente": False, "version": "1", "mensajes": mensajes()}
            self.enviar(200, "application/json", json.dumps(j).encode())
        elif self.path.startswith("/foto"):
            self.enviar(200, "image/jpeg", FOTO)
        else:
            self.enviar(404, "text/plain", b"no")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        self.enviar(200, "application/json", b"{}")


ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
