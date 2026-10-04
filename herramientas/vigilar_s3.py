# Graba todo lo que dice el aparato por el cable COM y, en paralelo, hace pings por radio.
#   python herramientas/vigilar_s3.py 420
import os, queue, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import importlib.util
import serial

SEG = int(sys.argv[1]) if len(sys.argv) > 1 else 420
SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "capturas", "registro_s3.txt")
spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(__file__), "..", "app_mensajes.pyw"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

s = serial.Serial()
s.port, s.baudrate, s.timeout, s.dtr, s.rts = "COM8", 115200, 0.2, False, False
s.open()
ev = queue.Queue()
pc = app.Radio(ev)
pc.start()
t0 = time.time()
f = open(SALIDA, "w", encoding="utf-8")


def pings():
    while time.time() - t0 < SEG:
        pc.encolar(clase="ping")
        time.sleep(20)


threading.Thread(target=pings, daemon=True).start()
buf = b""
while time.time() - t0 < SEG:
    buf += s.read(4096)
    while b"\n" in buf:
        linea, buf = buf.split(b"\n", 1)
        f.write(f"[{time.time() - t0:6.1f}] {linea.decode('utf-8', 'replace').rstrip()}\n")
    try:
        while True:
            e = ev.get_nowait()
            if e[0] in ("ping", "de_ella", "foto_de_ella", "respuesta"):
                f.write(f"[{time.time() - t0:6.1f}] PC> {e}\n")
    except queue.Empty:
        pass
    f.flush()
print("listo:", SALIDA)
