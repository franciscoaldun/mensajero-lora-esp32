# App de escritorio para hablar por LoRa con el aparato de mi pareja: textos, fotos y control del aparato.
# Doble clic para abrir (.pyw = sin ventana negra).
import io, json, os, queue, random, re, struct, threading, time, tkinter as tk
from datetime import datetime
from tkinter import filedialog, font as tkfont, simpledialog

import serial
from serial.tools import list_ports
from PIL import Image, ImageOps, ImageTk

import protocolo as p

CARPETA = os.path.dirname(os.path.abspath(__file__))
HISTORIAL = os.path.join(CARPETA, "historial.json")
FOTOS = os.path.join(CARPETA, "fotos")
os.makedirs(FOTOS, exist_ok=True)
INTENTOS_TEXTO = 5
CADA_PING = 3.0
REINTENTO_AUTOMATICO = 5 * 60   # segundos
MAX_REINTENTOS_AUTO = 24
TROZOS_POR_VENTANA = 8   # igual que en el aparato
CALIDADES = [("Rápida", 220, 42, 5000), ("Normal", 340, 50, 12000), ("Buena", 480, 60, 26000)]

FONDO, PANEL, YO, ELLA, TEXTO, TENUE, BIEN, MAL, ACENTO = (
    "#1e1b26", "#2a2634", "#7c4dff", "#3a3546", "#f2eefa", "#9a93ab", "#5fd38d", "#ff6b81", "#ff8fb1")


def buscar_puerto():
    for pt in list_ports.comports():
        if pt.vid == 0x1A86 and pt.pid in (0x7523, 0x7522, 0x5523):  # CH340 del adaptador DX-PJ15
            return pt.device
    return None


def barras(rssi):
    if rssi is None:
        return "sin datos"
    n = 4 if rssi > -90 else 3 if rssi > -105 else 2 if rssi > -115 else 1 if rssi > -125 else 0
    return "▂▄▆█"[:n].ljust(4, "·") + f"  {rssi} dBm"


def achicar_foto(ruta, lado, calidad, maximo):
    img = ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")
    img.thumbnail((lado, lado), Image.LANCZOS)
    q = calidad
    while True:
        b = io.BytesIO()
        img.save(b, "JPEG", quality=q, optimize=True)
        if b.tell() <= maximo or q <= 12:
            return b.getvalue()
        q -= 7


class Radio(threading.Thread):
    """Dueña del puerto serie: hace un trabajo a la vez y contesta todo lo que llega."""

    def __init__(self, eventos):
        super().__init__(daemon=True)
        self.eventos = eventos
        self.trabajos = queue.Queue()
        self.lector = p.Lector()
        self.puerto = None
        self.canal_libre = 0.0
        self.esperando = None      # (tipo esperado, id) -> respuesta en self.respuesta
        self.respuesta = None
        self.vistos = {}
        self.rx = None             # foto que está llegando
        self.ultima_foto_completa = None
        self.ultima_hora = 0
        self.ultimo_rx = 0.0       # si el otro está hablando, no se le interrumpe
        self.ventana_hasta = 0.0   # mientras llega una foto, el otro abre ventanas para que yo hable

    def encolar(self, **trabajo):
        self.trabajos.put(trabajo)

    # ---- puerto ----
    def conectar(self):
        while True:
            nombre = buscar_puerto()
            if nombre:
                try:
                    self.puerto = serial.Serial(nombre, 9600, timeout=0.05)
                    self.eventos.put(("conexion", nombre))
                    self.ultima_hora = 0
                    return
                except serial.SerialException:
                    pass
            self.eventos.put(("conexion", None))
            time.sleep(2)

    def transmitir(self, paquete):
        espera = self.canal_libre - time.time()
        if espera > 0:
            self.escuchar(espera)
        for _ in range(2):
            try:
                self.puerto.write(paquete)
                break
            except (serial.SerialException, AttributeError):
                self.conectar()
        self.canal_libre = time.time() + p.segundos_en_aire(len(paquete)) + 0.15

    def mandar(self, tipo, ident, cuerpo=b""):
        self.transmitir(p.empaquetar(tipo, ident, cuerpo))

    def responder(self, tipo, ident, cuerpo=b""):
        time.sleep(0.08)
        self.mandar(tipo, ident, cuerpo)

    def escuchar(self, segundos, hasta_respuesta=False):
        fin = time.time() + segundos
        while time.time() < fin:
            try:
                datos = self.puerto.read(256)
            except (serial.SerialException, AttributeError):
                self.puerto = None
                self.eventos.put(("conexion", None))
                self.conectar()
                continue
            for paq in self.lector.alimentar(datos):
                self.recibido(*paq)
            if hasta_respuesta and self.respuesta is not None:
                return

    def esperar(self, tipos, ident, segundos):
        self.esperando, self.respuesta = (tipos, ident), None
        self.escuchar(segundos, hasta_respuesta=True)
        self.esperando = None
        return self.respuesta

    def ya_visto(self, tipo, ident):
        ahora = time.time()
        self.vistos = {k: v for k, v in self.vistos.items() if ahora - v < 600}
        k = (tipo, ident)
        if k in self.vistos:
            return True
        self.vistos[k] = ahora
        return False

    # ---- lo que llega ----
    def turno_libre(self):
        # una foto que dejó de avanzar (el otro se reinició o se fue de alcance) ya no bloquea el turno
        foto_viva = self.rx is not None and time.time() - self.rx["t"] < 45
        return not foto_viva and time.time() - self.ultimo_rx > 2.5

    def turno_para_texto(self):
        return self.turno_libre() or time.time() < self.ventana_hasta

    def esperar_turno(self, maximo=30, texto=False):
        fin = time.time() + maximo
        while not (self.turno_para_texto() if texto else self.turno_libre()) and time.time() < fin:
            self.escuchar(0.02)

    def recibido(self, tipo, ident, cuerpo, rssi):
        self.ultimo_rx = time.time()
        self.eventos.put(("rssi", rssi))
        r8 = struct.pack("<b", max(-128, min(127, rssi)))
        if self.esperando and ident == self.esperando[1] and tipo in self.esperando[0]:
            self.respuesta = (tipo, cuerpo, rssi)
            return
        if tipo == p.ACUSE and self.rx is not None:
            self.ventana_hasta = time.time() + 0.4  # me acusaron dentro de una ventana: sigue abierta
        if tipo == p.MENSAJE:
            self.responder(p.ACUSE, ident, r8)
            if not self.ya_visto(tipo, ident):
                self.eventos.put(("de_ella", cuerpo.decode("utf-8", "replace"), rssi))
        elif tipo == p.PING:
            self.responder(p.PONG, ident, r8)
        elif tipo == p.FOTO and len(cuerpo) >= 8:
            foto, largo, trozos = struct.unpack("<HIH", cuerpo[:8])
            self.responder(p.ACUSE, ident, r8)
            if (self.rx and self.rx["foto"] == foto) or foto == self.ultima_foto_completa:
                return
            if 0 < largo <= p.MAX_FOTO and trozos == -(-largo // p.TROZO):
                self.rx = {"foto": foto, "datos": bytearray(largo), "tengo": set(), "trozos": trozos, "t": time.time()}
                self.eventos.put(("foto_llegando", 0))
        elif tipo == p.TROZO_FOTO and self.rx and len(cuerpo) >= 5:
            foto, i = struct.unpack("<HH", cuerpo[:4])
            if foto == self.rx["foto"] and i < self.rx["trozos"]:
                d = cuerpo[4:]
                self.rx["datos"][i * p.TROZO:i * p.TROZO + len(d)] = d
                self.rx["tengo"].add(i)
                if i % TROZOS_POR_VENTANA == TROZOS_POR_VENTANA - 1:
                    self.ventana_hasta = time.time() + 0.4
                self.rx["t"] = time.time()
                self.eventos.put(("foto_llegando", 100 * len(self.rx["tengo"]) // self.rx["trozos"]))
        elif tipo == p.PREGUNTA_FOTO and len(cuerpo) >= 2:
            foto = struct.unpack("<H", cuerpo[:2])[0]
            if self.rx and self.rx["foto"] == foto:
                faltan = [i for i in range(self.rx["trozos"]) if i not in self.rx["tengo"]]
                if faltan:
                    mapa = bytearray((self.rx["trozos"] + 7) // 8)
                    for i in faltan:
                        mapa[i // 8] |= 1 << (i % 8)
                    self.responder(p.FALTAN, ident, cuerpo[:2] + bytes(mapa))
                else:
                    self.responder(p.ACUSE, ident, b"\x00")
                    ruta = os.path.join(FOTOS, datetime.now().strftime("ella_%Y%m%d_%H%M%S.jpg"))
                    open(ruta, "wb").write(self.rx["datos"])
                    self.ultima_foto_completa = foto
                    self.rx = None
                    self.eventos.put(("foto_de_ella", ruta, rssi))
            elif foto == self.ultima_foto_completa:
                self.responder(p.ACUSE, ident, b"\x00")
            else:
                self.responder(p.FALTAN, ident, cuerpo[:2])  # no la conozco: que empiece de nuevo

    # ---- trabajos ----
    def hacer_texto(self, t):
        ident = random.randint(1, 0xFFFF)
        crudo = t["texto"].encode("utf-8")
        for n in range(1, INTENTOS_TEXTO + 1):
            if n > 1:
                self.eventos.put(("estado", t["ref"], f"reintentando ({n}/{INTENTOS_TEXTO})…", None))
            self.esperar_turno(maximo=600, texto=True)
            self.mandar(p.MENSAJE, ident, crudo)
            r = self.esperar((p.ACUSE,), ident, p.segundos_en_aire(len(crudo) + 5) + p.segundos_en_aire(6)
                             + 1.5 + random.random() * 1.2)
            if r:
                alla = struct.unpack("<b", r[1][:1])[0] if r[1] else None
                self.eventos.put(("resultado", t["ref"], True, alla))
                return
        self.eventos.put(("resultado", t["ref"], False, None))

    def hacer_foto(self, t):
        datos = t["datos"]
        foto, ident = random.randint(1, 0xFFFF), random.randint(1, 0xFFFF)
        trozos = -(-len(datos) // p.TROZO)
        faltan = set(range(trozos))
        cabecera = struct.pack("<HIH", foto, len(datos), trozos)
        rondas = 0
        while rondas <= 12:
            # 1) avisar que viene una foto
            ok = False
            for _ in range(5):
                self.esperar_turno()
                self.mandar(p.FOTO, ident, cabecera)
                if self.esperar((p.ACUSE,), ident, p.segundos_en_aire(13) + p.segundos_en_aire(6) + 1.5 + random.random()):
                    ok = True
                    break
            if not ok:
                break
            # 2) mandar los trozos que falten y preguntar, hasta que no falte nada
            reiniciar = False
            while faltan and rondas <= 12:
                rondas += 1
                for i in sorted(faltan):
                    d = datos[i * p.TROZO:(i + 1) * p.TROZO]
                    self.mandar(p.TROZO_FOTO, random.randint(1, 0xFFFF), struct.pack("<HH", foto, i) + d)
                    if i % TROZOS_POR_VENTANA == TROZOS_POR_VENTANA - 1:  # ventana para que el otro hable
                        for _ in range(5):  # si habló, se alarga: quizás tiene más que decir
                            inicio = time.time()
                            self.escuchar(max(0.0, self.canal_libre - time.time()) + p.segundos_en_aire(5 + p.MAX_TEXTO) + 0.7)
                            if self.ultimo_rx < inicio:
                                break
                    hechos = trozos - len(faltan) + sorted(faltan).index(i) + 1
                    self.eventos.put(("progreso", t["ref"], 100 * hechos // trozos))
                r = None
                for _ in range(6):
                    self.esperar_turno()
                    self.mandar(p.PREGUNTA_FOTO, ident, struct.pack("<H", foto))
                    r = self.esperar((p.ACUSE, p.FALTAN), ident, p.segundos_en_aire(7) + p.segundos_en_aire(7 + trozos // 8 + 1) + 1.5)
                    if r:
                        break
                if not r:
                    self.eventos.put(("resultado", t["ref"], False, None))
                    return
                if r[0] == p.ACUSE:
                    self.eventos.put(("resultado", t["ref"], True, None))
                    return
                mapa = r[1][2:]
                if not mapa:  # se reinició y no la conoce: de nuevo desde el aviso
                    faltan = set(range(trozos))
                    reiniciar = True
                    break
                faltan = {i for i in range(trozos) if i // 8 < len(mapa) and (mapa[i // 8] >> (i % 8)) & 1}
                if not faltan:
                    self.eventos.put(("resultado", t["ref"], True, None))
                    return
            if not reiniciar:
                break
        self.eventos.put(("resultado", t["ref"], False, None))

    def at_local(self, comandos):
        """Modo AT del módulo propio (la radio queda sorda un par de segundos). Devuelve las respuestas."""
        s = self.puerto

        def hablar(d, espera, fin=None):
            s.reset_input_buffer()
            s.write(d)
            t0, r = time.time(), b""
            while time.time() - t0 < espera:
                r += s.read(256)
                if fin and fin in r:
                    break
            return r.decode("latin-1", "replace")

        if "Entry" not in hablar(b"+++\r\n", 2.0, b"Entry"):
            if "Entry" not in hablar(b"+++\r\n", 2.0, b"Entry"):  # quizás estaba en AT y salió
                return None
        salida = [hablar((c + "\r\n").encode(), 1.2, b"OK") for c in comandos]
        hablar(b"+++\r\n", 2.5, b"Power on")
        self.lector = p.Lector()
        return salida

    def hacer_canal(self, t):
        """Cambia el canal en los DOS aparatos. Si no hay contacto en el nuevo, los dos vuelven solos."""
        nuevo = t["canal"].upper()
        r0 = self.at_local(["AT+CHANNEL"])
        m = re.search(r"\+CHANNEL=([0-9A-Fa-f]{2})", r0[0]) if r0 else None
        if not m:
            self.eventos.put(("canal", False, "no pude leer el canal de mi módulo; no cambié nada"))
            return
        viejo = m.group(1).upper()
        if viejo == nuevo:
            self.eventos.put(("canal", True, f"ya estamos en el canal {nuevo}"))
            return
        ident = random.randint(1, 0xFFFF)
        resp = None
        for _ in range(3):
            self.esperar_turno()
            self.mandar(p.COMANDO, ident, f"canal {nuevo}".encode())
            r = self.esperar((p.RESPUESTA,), ident, p.segundos_en_aire(15) + p.segundos_en_aire(90) + 3)
            if r:
                resp = r[1].decode("utf-8", "replace")
                break
        if not resp or not resp.startswith("canal: paso"):
            self.eventos.put(("canal", False, f"el aparato no aceptó el cambio ({resp or 'no contestó'}); no cambié nada"))
            return
        self.eventos.put(("respuesta", resp))
        time.sleep(3.5)  # el aparato cambia a los 3 s
        self.at_local([f"AT+CHANNEL{nuevo}"])
        for _ in range(8):  # el primer ping que llegue confirma (y graba) el canal en el aparato
            ident = random.randint(1, 0xFFFF)
            self.mandar(p.PING, ident)
            if self.esperar((p.PONG,), ident, 5):
                self.eventos.put(("canal", True, f"listo: los dos en el canal {nuevo} ({850.15 + int(nuevo, 16):.2f} MHz)"))
                return
            self.escuchar(2)
        self.at_local([f"AT+CHANNEL{viejo}"])
        self.eventos.put(("canal", False, f"sin contacto en el canal {nuevo}: volví al {viejo}; el aparato vuelve solo en 2 min"))

    def hacer_comando(self, t):
        ident = random.randint(1, 0xFFFF)
        crudo = t["texto"].encode("utf-8")
        for _ in range(3):
            self.mandar(p.COMANDO, ident, crudo)
            r = self.esperar((p.RESPUESTA,), ident, p.segundos_en_aire(len(crudo) + 5) + p.segundos_en_aire(200) + 6)
            if r:
                self.eventos.put(("respuesta", r[1].decode("utf-8", "replace")))
                return
        self.eventos.put(("respuesta", "el aparato no contestó"))

    def hacer_ping(self, t):
        ident = random.randint(1, 0xFFFF)
        t0 = time.time()
        self.mandar(p.PING, ident)
        r = self.esperar((p.PONG,), ident, 5)
        if r:
            alla = struct.unpack("<b", r[1][:1])[0] if r[1] else None
            self.eventos.put(("ping", (r[2], alla, time.time() - t0)))
        else:
            self.eventos.put(("ping", None))

    def run(self):
        self.conectar()
        while True:
            if time.time() - self.ultima_hora > 6 * 3600:  # el aparato no tiene reloj propio
                self.ultima_hora = time.time()
                self.mandar(p.HORA, random.randint(1, 0xFFFF), struct.pack("<I", int(time.time())))
            if self.rx and time.time() - self.rx["t"] > 15 * 60:
                self.rx = None
            if not self.turno_para_texto():
                self.escuchar(0.02)
                continue
            if not self.turno_libre():
                # sólo un texto cabe en la ventana: se busca uno en la cola sin desordenar el resto
                with self.trabajos.mutex:
                    textos = [x for x in self.trabajos.queue if x["clase"] == "texto"]
                    if textos:
                        self.trabajos.queue.remove(textos[0])
                if not textos:
                    self.escuchar(0.02)
                    continue
                self.hacer_texto(textos[0])
                continue
            try:
                t = self.trabajos.get_nowait()
            except queue.Empty:
                self.escuchar(0.05)
                continue
            getattr(self, "hacer_" + t["clase"])(t)


class App:
    def __init__(self, raiz):
        self.raiz = raiz
        raiz.title("Mensajes LoRa 💌")
        raiz.configure(bg=FONDO)
        raiz.geometry("480x680")
        raiz.minsize(380, 440)
        self.f_txt = tkfont.Font(family="Segoe UI", size=12)
        self.f_peq = tkfont.Font(family="Segoe UI", size=9)
        self.f_tit = tkfont.Font(family="Segoe UI Semibold", size=14)
        self.imagenes = []  # referencias para que Tk no borre las fotos

        arriba = tk.Frame(raiz, bg=PANEL, padx=14, pady=10)
        arriba.pack(fill="x")
        tk.Label(arriba, text="💌 Mensajes", font=self.f_tit, bg=PANEL, fg=TEXTO).pack(side="left")
        tk.Button(arriba, text="⚙ Aparato", font=self.f_peq, bg=PANEL, fg=TENUE, bd=0, activebackground=PANEL,
                  command=self.menu_aparato).pack(side="right", padx=(8, 0))
        self.l_senal = tk.Label(arriba, text="señal: sin datos", font=self.f_peq, bg=PANEL, fg=TENUE)
        self.l_senal.pack(side="right")
        self.l_con = tk.Label(raiz, text="buscando el módulo…", font=self.f_peq, bg=FONDO, fg=TENUE, anchor="w", padx=14)
        self.l_con.pack(fill="x")

        self.chat = tk.Text(raiz, bg=FONDO, fg=TEXTO, font=self.f_txt, wrap="word", bd=0, padx=12, pady=8,
                            highlightthickness=0, state="disabled", cursor="arrow")
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("yo", justify="right", background=YO, lmargin1=90, lmargin2=90, rmargin=4, spacing1=8)
        self.chat.tag_configure("ella", justify="left", background=ELLA, lmargin1=4, lmargin2=4, rmargin=90, spacing1=8)
        self.chat.tag_configure("foto_yo", justify="right", spacing1=8)
        self.chat.tag_configure("foto_ella", justify="left", spacing1=8)
        self.chat.tag_configure("estado_yo", justify="right", foreground=TENUE, font=self.f_peq, spacing3=4)
        self.chat.tag_configure("estado_ella", justify="left", foreground=TENUE, font=self.f_peq, spacing3=4)
        self.chat.tag_configure("bien", foreground=BIEN)
        self.chat.tag_configure("mal", foreground=MAL)
        self.chat.tag_configure("aviso", justify="center", foreground=ACENTO, font=self.f_peq, spacing1=6, spacing3=6)

        abajo = tk.Frame(raiz, bg=PANEL, padx=10, pady=10)
        abajo.pack(fill="x")
        tk.Button(abajo, text="📷", font=self.f_txt, bg=ELLA, fg="white", bd=0, padx=10,
                  activebackground=ELLA, command=self.elegir_foto).pack(side="left", padx=(0, 8), fill="y")
        self.entrada = tk.Text(abajo, height=2, font=self.f_txt, bg=FONDO, fg=TEXTO, insertbackground=TEXTO,
                               bd=0, wrap="word", padx=8, pady=6, highlightthickness=1,
                               highlightbackground="#444", highlightcolor=YO)
        self.entrada.pack(side="left", fill="x", expand=True)
        self.entrada.bind("<Return>", self.enviar)
        self.entrada.bind("<KeyRelease>", self.contar)
        tk.Button(abajo, text="Enviar", font=self.f_txt, bg=YO, fg="white", bd=0, padx=16,
                  activebackground="#6a3df0", activeforeground="white", command=self.enviar).pack(side="left", padx=(8, 0), fill="y")

        pie = tk.Frame(raiz, bg=PANEL, padx=10)
        pie.pack(fill="x")
        self.l_cuenta = tk.Label(pie, text=f"0 / {p.MAX_TEXTO}", font=self.f_peq, bg=PANEL, fg=TENUE)
        self.l_cuenta.pack(side="left", pady=(0, 8))
        self.b_alc = tk.Button(pie, text="📡 Prueba de alcance", font=self.f_peq, bg=PANEL, fg=ACENTO, bd=0,
                               activebackground=PANEL, command=self.alternar_alcance)
        self.b_alc.pack(side="right", pady=(0, 8))

        self.eventos = queue.Queue()
        self.radio = Radio(self.eventos)
        self.marcas = {}
        self.alcance = None
        self.historial = self.cargar()
        for h in self.historial[-150:]:
            if h.get("foto"):
                self.pintar_foto(h["quien"], h["foto"], h.get("hora", ""), h.get("estado", ""), h.get("ref"))
            else:
                self.pintar(h["quien"], h["texto"], h.get("hora", ""), h.get("estado", ""), h.get("ref"))
        self.radio.start()
        self.raiz.after(50, self.procesar)
        self.raiz.after(REINTENTO_AUTOMATICO * 1000, self.reintentar_fallidos)
        self.entrada.focus_set()

    # ---- historial ----
    def cargar(self):
        try:
            return json.load(open(HISTORIAL, encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def guardar(self):
        try:
            json.dump(self.historial[-2000:], open(HISTORIAL, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass

    def buscar(self, ref):
        for h in reversed(self.historial):
            if h.get("ref") == ref:
                return h
        return None

    # ---- dibujo ----
    def _estado(self, quien, estado, ref):
        c = self.chat
        marca = f"m{ref or time.time()}"
        c.insert("end", "\n", "estado_" + quien)
        c.mark_set(marca, "end-1c")
        c.mark_gravity(marca, "left")
        c.insert("end", estado + "\n", ("estado_" + quien,))
        if ref:
            self.marcas[ref] = marca

    def pintar(self, quien, texto, hora, estado, ref=None):
        c = self.chat
        c.configure(state="normal")
        c.insert("end", f" {texto} ", quien)
        self._estado(quien, f"{hora}  {estado}".strip(), ref)
        c.configure(state="disabled")
        c.see("end")

    def pintar_foto(self, quien, ruta, hora, estado, ref=None):
        c = self.chat
        c.configure(state="normal")
        try:
            img = Image.open(ruta)
            img.thumbnail((260, 260))
            foto = ImageTk.PhotoImage(img)
            self.imagenes.append(foto)
            c.insert("end", " ", "foto_" + quien)
            c.image_create("end", image=foto, padx=2, pady=2)
        except OSError:
            c.insert("end", " 📷 (foto no encontrada) ", quien)
        self._estado(quien, f"{hora}  {estado}".strip(), ref)
        c.configure(state="disabled")
        c.see("end")

    def cambiar_estado(self, ref, estado, tag=None):
        marca = self.marcas.get(ref)
        h = self.buscar(ref)
        if h:
            h["estado"] = estado
            self.guardar()
        if not marca:
            return
        c = self.chat
        c.configure(state="normal")
        c.delete(marca, f"{marca} lineend")
        hora = h.get("hora", "") if h else ""
        c.insert(marca, f"{hora}  {estado}", ("estado_yo",) + ((tag,) if tag else ()))
        c.configure(state="disabled")

    def aviso(self, texto):
        self.chat.configure(state="normal")
        self.chat.insert("end", texto + "\n", "aviso")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    # ---- acciones ----
    def contar(self, _=None):
        n = len(self.entrada.get("1.0", "end-1c").encode("utf-8"))
        self.l_cuenta.configure(text=f"{n} / {p.MAX_TEXTO}", fg=MAL if n > p.MAX_TEXTO else TENUE)

    def enviar(self, ev=None):
        if ev is not None and ev.state & 0x1:  # Shift+Enter = salto de línea
            return None
        texto = self.entrada.get("1.0", "end-1c").strip()
        if not texto:
            return "break"
        if len(texto.encode("utf-8")) > p.MAX_TEXTO:
            self.aviso(f"muy largo ({len(texto.encode())} de {p.MAX_TEXTO}); córtalo en dos")
            return "break"
        self.entrada.delete("1.0", "end")
        self.contar()
        ref = f"r{time.time()}"
        hora = datetime.now().strftime("%H:%M")
        self.historial.append({"quien": "yo", "texto": texto, "hora": hora, "estado": "enviando…", "ref": ref,
                               "t": time.time(), "intentos": 0})
        self.guardar()
        self.pintar("yo", texto, hora, "enviando…", ref)
        self.radio.encolar(clase="texto", texto=texto, ref=ref)
        return "break"

    def elegir_foto(self):
        ruta = filedialog.askopenfilename(title="Foto para mandar",
                                          filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.heic *.webp *.bmp"), ("Todo", "*.*")])
        if not ruta:
            return
        try:
            versiones = [achicar_foto(ruta, lado, q, mx) for _, lado, q, mx in CALIDADES]
        except Exception as e:
            self.aviso(f"no pude abrir la foto: {e}")
            return
        v = tk.Toplevel(self.raiz, bg=PANEL, padx=14, pady=12)
        v.title("Mandar foto")
        v.transient(self.raiz)
        img = Image.open(io.BytesIO(versiones[1]))
        img.thumbnail((300, 300))
        vista = ImageTk.PhotoImage(img)
        lbl = tk.Label(v, image=vista, bg=PANEL)
        lbl.image = vista
        lbl.pack(pady=(0, 8))
        for (nombre, *_), datos in zip(CALIDADES, versiones):
            seg = p.segundos_foto(len(datos))
            t = f"{seg:.0f} s" if seg < 90 else f"~{seg / 60:.0f} min"
            tk.Button(v, text=f"{nombre}  ·  {len(datos) / 1024:.1f} KB  ·  {t}", font=self.f_txt, bg=FONDO, fg=TEXTO,
                      bd=0, pady=6, anchor="w", padx=10, activebackground=ELLA,
                      command=lambda d=datos: (v.destroy(), self.mandar_foto(d))).pack(fill="x", pady=3)
        tk.Button(v, text="Cancelar", font=self.f_peq, bg=PANEL, fg=TENUE, bd=0, command=v.destroy).pack(pady=(6, 0))

    def mandar_foto(self, datos):
        ruta = os.path.join(FOTOS, datetime.now().strftime("yo_%Y%m%d_%H%M%S.jpg"))
        open(ruta, "wb").write(datos)
        ref = f"f{time.time()}"
        hora = datetime.now().strftime("%H:%M")
        self.historial.append({"quien": "yo", "foto": ruta, "hora": hora, "estado": "enviando foto…", "ref": ref})
        self.guardar()
        self.pintar_foto("yo", ruta, hora, "enviando foto…", ref)
        self.radio.encolar(clase="foto", datos=datos, ref=ref)

    def reintentar_fallidos(self):
        ahora = time.time()
        for h in self.historial[-50:]:
            if (h.get("quien") == "yo" and "texto" in h and h.get("estado", "").startswith("✗")
                    and ahora - h.get("t", 0) < 24 * 3600 and h.get("intentos", 0) < MAX_REINTENTOS_AUTO):
                h["intentos"] = h.get("intentos", 0) + 1
                self.cambiar_estado(h["ref"], "reintentando solo…")
                self.radio.encolar(clase="texto", texto=h["texto"], ref=h["ref"])
        self.raiz.after(REINTENTO_AUTOMATICO * 1000, self.reintentar_fallidos)

    def menu_aparato(self):
        m = tk.Menu(self.raiz, tearoff=0)
        m.add_command(label="Ver estado del aparato", command=lambda: self.comando("estado"))
        m.add_command(label="Girar la pantalla 180°", command=lambda: self.comando("girar"))
        m.add_command(label="Brillo normal…", command=lambda: self.pedir_num("brillo bajo", "Brillo normal (0-300, hoy 25)"))
        m.add_command(label="Brillo al llegar mensaje…", command=lambda: self.pedir_num("brillo alto", "Brillo alto (50-1000)"))
        m.add_command(label="Minutos con brillo alto…", command=lambda: self.pedir_num("minutos", "Minutos (1-60)"))
        m.add_separator()
        m.add_command(label="Reiniciar el aparato", command=lambda: self.comando("reiniciar"))
        m.add_command(label="Comando a mano…", command=self.comando_a_mano)
        m.tk_popup(self.raiz.winfo_pointerx(), self.raiz.winfo_pointery())

    def pedir_num(self, cmd, titulo):
        v = simpledialog.askinteger("Aparato", titulo, parent=self.raiz)
        if v is not None:
            self.comando(f"{cmd} {v}")

    def comando_a_mano(self):
        v = simpledialog.askstring("Aparato", "Comando (escribe 'ayuda' para ver la lista):", parent=self.raiz)
        if v:
            self.comando(v.strip())

    def comando(self, texto):
        self.aviso(f"→ aparato: {texto}")
        self.radio.encolar(clase="comando", texto=texto)

    def alternar_alcance(self):
        if self.alcance:
            a = self.alcance
            self.alcance = None
            self.b_alc.configure(text="📡 Prueba de alcance")
            self.aviso(self.resumen_alcance(a, final=True))
            return
        self.alcance = {"enviados": 0, "ok": 0, "ida": [], "vuelta": [], "rtt": []}
        self.b_alc.configure(text="⏹ Detener prueba")
        self.aviso("prueba de alcance: un ping cada pocos segundos; aléjate o que ella se aleje")
        self.ping()

    def ping(self):
        if self.alcance:
            self.alcance["enviados"] += 1
            self.radio.encolar(clase="ping")

    def resumen_alcance(self, a, final=False):
        def prom(v):
            return f"{sum(v) / len(v):.0f}" if v else "–"
        pct = 100 * a["ok"] / a["enviados"] if a["enviados"] else 0
        return (("RESULTADO  " if final else "") + f"{a['ok']}/{a['enviados']} respondidos ({pct:.0f}%) · "
                f"señal allá {prom(a['ida'])} dBm · acá {prom(a['vuelta'])} dBm · ida y vuelta {prom(a['rtt'])} s")

    # ---- eventos de la radio ----
    def procesar(self):
        while True:
            try:
                ev = self.eventos.get_nowait()
            except queue.Empty:
                break
            tipo = ev[0]
            if tipo == "conexion":
                self.l_con.configure(text=f"● módulo conectado en {ev[1]}" if ev[1] else
                                     "○ módulo desconectado: enchufa el adaptador USB", fg=BIEN if ev[1] else MAL)
            elif tipo == "rssi":
                self.l_senal.configure(text="señal " + barras(ev[1]))
            elif tipo == "de_ella":
                hora = datetime.now().strftime("%H:%M")
                self.historial.append({"quien": "ella", "texto": ev[1], "hora": hora, "estado": barras(ev[2])})
                self.guardar()
                self.pintar("ella", ev[1], hora, barras(ev[2]))
                self.avisar_llegada()
            elif tipo == "foto_de_ella":
                hora = datetime.now().strftime("%H:%M")
                self.historial.append({"quien": "ella", "foto": ev[1], "hora": hora, "estado": barras(ev[2])})
                self.guardar()
                self.pintar_foto("ella", ev[1], hora, barras(ev[2]))
                self.l_con.configure(text="● foto recibida", fg=BIEN)
                self.avisar_llegada()
            elif tipo == "foto_llegando":
                self.l_con.configure(text=f"📷 llegando una foto de ella… {ev[1]}%", fg=ACENTO)
            elif tipo == "estado":
                self.cambiar_estado(ev[1], ev[2])
            elif tipo == "progreso":
                self.cambiar_estado(ev[1], f"enviando foto… {ev[2]}%")
            elif tipo == "resultado":
                _, ref, ok, alla = ev
                if ok:
                    extra = f" · {alla} dBm allá" if alla is not None else ""
                    self.cambiar_estado(ref, f"✓✓ le llegó{extra}", "bien")
                else:
                    self.cambiar_estado(ref, "✗ no llegó (se reintenta solo cada 5 min)", "mal")
            elif tipo == "respuesta":
                self.aviso(f"← aparato: {ev[1]}")
            elif tipo == "ping":
                a = self.alcance
                if a:
                    if ev[1]:
                        vuelta, alla, rtt = ev[1]
                        a["ok"] += 1
                        a["vuelta"].append(vuelta)
                        if alla is not None:
                            a["ida"].append(alla)
                        a["rtt"].append(rtt)
                    self.l_con.configure(text=self.resumen_alcance(a), fg=ACENTO)
                    self.raiz.after(int(CADA_PING * 1000), self.ping)
        self.raiz.after(50, self.procesar)

    def avisar_llegada(self):
        self.raiz.bell()
        self.raiz.deiconify()
        try:
            self.raiz.attributes("-topmost", True)
            self.raiz.after(400, lambda: self.raiz.attributes("-topmost", False))
        except tk.TclError:
            pass


if __name__ == "__main__":
    raiz = tk.Tk()
    App(raiz)
    raiz.mainloop()
