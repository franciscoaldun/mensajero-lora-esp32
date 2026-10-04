# Prueba la lógica de la app (textos, fotos con pérdidas, comandos) con dos radios virtuales.
#   python herramientas/simular_enlace.py
import os, queue, random, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import importlib.util
import protocolo as p

spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(__file__), "..", "app_mensajes.pyw"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

PERDIDA = 0.2
ACELERAR = 40  # el aire virtual va 40 veces más rápido


class Aire:
    """Lleva los paquetes de un lado al otro, pierde algunos y agrega el byte de señal."""

    def __init__(self):
        self.buzones = {}

    def puerto(self, nombre, otro):
        return PuertoFalso(self, nombre, otro)


class PuertoFalso:
    def __init__(self, aire, nombre, otro):
        self.aire, self.nombre, self.otro = aire, nombre, otro
        aire.buzones[nombre] = queue.Queue()
        self.pend = bytearray()

    def write(self, b):
        lector = p.Lector()
        b = bytes(b)
        largo = b[1]
        if random.random() >= PERDIDA:
            def entregar():
                time.sleep(p.segundos_en_aire(len(b)) / ACELERAR)
                self.aire.buzones[self.otro].put(b + bytes([255 - 95]))
            threading.Thread(target=entregar, daemon=True).start()
        return len(b)

    def read(self, n):
        try:
            self.pend += self.aire.buzones[self.nombre].get(timeout=0.01)
        except queue.Empty:
            pass
        out, self.pend = bytes(self.pend[:n]), self.pend[n:]
        return out


# el aire virtual también acelera las esperas de la app
_real = p.segundos_en_aire
p.segundos_en_aire = lambda n: _real(n) / ACELERAR

aire = Aire()
ev_a, ev_b = queue.Queue(), queue.Queue()
a, b = app.Radio(ev_a), app.Radio(ev_b)
a.puerto, b.puerto = aire.puerto("A", "B"), aire.puerto("B", "A")
a.conectar = b.conectar = lambda: None
a.ultima_hora = b.ultima_hora = time.time()
a.start(); b.start()


def esperar_evento(ev, tipo, segundos=120):
    fin = time.time() + segundos
    while time.time() < fin:
        try:
            e = ev.get(timeout=0.2)
        except queue.Empty:
            continue
        if e[0] == tipo:
            return e
    return None


ok = True
a.encolar(clase="texto", texto="hola amor ñ ❤", ref="t1")
r = esperar_evento(ev_a, "resultado")
llego = esperar_evento(ev_b, "de_ella", 5)
print("texto:", "OK" if r and r[2] and llego else "FALLÓ", llego[1] if llego else "")
ok &= bool(r and r[2] and llego)

foto = os.urandom(9000)  # 41 trozos
t0 = time.time()
a.encolar(clase="foto", datos=foto, ref="f1")
r = esperar_evento(ev_a, "resultado", 300)
llego = esperar_evento(ev_b, "foto_de_ella", 5)
igual = llego and open(llego[1], "rb").read() == foto
print(f"foto de 9 KB con {PERDIDA:.0%} de pérdida:", "OK, idéntica" if r and r[2] and igual else "FALLÓ",
      f"({time.time() - t0:.1f} s simulados x{ACELERAR})")
ok &= bool(r and r[2] and igual)
if llego:
    os.remove(llego[1])

# el otro lado "se reinicia" a mitad de una foto: tiene que empezar de nuevo y llegar igual
b.rx = None
foto2 = os.urandom(3000)
viejo_recibido = b.recibido
contador = {"n": 0}
def recibido_con_reinicio(tipo, ident, cuerpo, rssi):
    if tipo == p.TROZO_FOTO:
        contador["n"] += 1
        if contador["n"] == 5:
            b.rx = None  # pierde todo lo que llevaba
    return viejo_recibido(tipo, ident, cuerpo, rssi)
b.recibido = recibido_con_reinicio
a.encolar(clase="foto", datos=foto2, ref="f2")
r = esperar_evento(ev_a, "resultado", 300)
llego = esperar_evento(ev_b, "foto_de_ella", 5)
igual = llego and open(llego[1], "rb").read() == foto2
print("foto con reinicio del otro lado a la mitad:", "OK, idéntica" if r and r[2] and igual else "FALLÓ")
ok &= bool(r and r[2] and igual)
if llego:
    os.remove(llego[1])
print("TODO OK" if ok else "HAY FALLAS")
