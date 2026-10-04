# Lado del PC: la MISMA página que el aparato (firmware-s3/web/index.html), servida acá y conectada
# a la radio por el módulo USB (radio_pc.py). Así las dos apps se ven y funcionan igual.
#   python servidor_pc.py           (o el acceso directo "Mensajes LoRa")
#   python servidor_pc.py --demo    sin radio, para mirarla
import json, os, queue, re, socket, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import protocolo as p
import radio_pc

CARPETA = os.path.dirname(os.path.abspath(__file__))
PAGINA = os.path.join(CARPETA, "firmware-s3", "web", "index.html")
HISTORIAL = os.path.join(CARPETA, "historial_pc.json")
AJUSTES = os.path.join(CARPETA, "ajustes_pc.json")
FOTOS = os.path.join(CARPETA, "fotos")
PUERTO_WEB = 8790
DEMO = "--demo" in sys.argv
if DEMO:
    HISTORIAL = os.path.join(CARPETA, "historial_demo.json")
    FOTOS = os.path.join(CARPETA, "fotos_demo")
    PUERTO_WEB = 8791
os.makedirs(FOTOS, exist_ok=True)

DE_EL, DE_ELLA = 0, 1
ENVIANDO, ENTREGADO, FALLIDO, RECIBIDO = 0, 1, 2, 3
YO = DE_EL
REINTENTO_CERCA = 60   # lo que no llegó, mientras están cerca (lejos, el latido hace de sonda)


def cargar_ajustes():
    a = {"latido_s": 150, "turbo": True, "otro": "mi amor"}
    try:
        a.update(json.load(open(AJUSTES, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return a


ajustes = cargar_ajustes()


def guardar_ajustes():
    tmp = AJUSTES + ".tmp"
    json.dump(ajustes, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, AJUSTES)


class Almacen:
    """Historial del PC. Cada mensaje: n, de, tipo, estado, rssi, ts (escrito), llegada, prog, texto,
    intentos, seq (número de quien lo mandó), leido, recibo (el recibo de lectura ya le llegó a ella)."""

    def __init__(self):
        self.cerrojo = threading.RLock()
        try:
            d = json.load(open(HISTORIAL, encoding="utf-8"))
            self.regs, self.siguiente, self.seq = d["regs"], d["siguiente"], d.get("seq", 1)
        except (OSError, ValueError, KeyError):
            self.regs, self.siguiente, self.seq = [], 1, 1
        for r in self.regs:
            # lo de antes: no pide recibos de lectura, y lo que mandó él NO se da por leído (no se sabe)
            for k, v in (("seq", 0), ("leido", 1 if r["de"] == DE_ELLA else 0), ("recibo", 1), ("llegada", 0), ("intentos", 0)):
                r.setdefault(k, v)
            if r["de"] == DE_EL and not r["seq"]:
                r["leido"] = 0  # sin número no hay recibo posible: no se sabe si lo leyó
            if r["estado"] == ENVIANDO:
                r["estado"] = FALLIDO

    def guardar(self):
        with self.cerrojo:
            tmp = HISTORIAL + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"regs": self.regs[-3000:], "siguiente": self.siguiente, "seq": self.seq}, f, ensure_ascii=False)
            os.replace(tmp, HISTORIAL)

    def agregar(self, **r):
        with self.cerrojo:
            r = {"n": self.siguiente, "de": DE_EL, "tipo": 0, "estado": ENVIANDO, "rssi": 0, "ts": int(time.time()),
                 "llegada": 0, "prog": 0, "texto": "", "intentos": 0, "seq": 0, "leido": 0, "recibo": 0, **r}
            if r["de"] == DE_EL and not r["seq"]:
                r["seq"] = self.seq
                self.seq = self.seq % 0xFFFF + 1
            self.siguiente += 1
            self.regs.append(r)
            self.guardar()
            return dict(r)

    def buscar(self, n):
        with self.cerrojo:
            for r in reversed(self.regs):
                if r["n"] == n:
                    return r
        return None

    def buscar_seq(self, de, seq):
        if not seq:
            return None
        with self.cerrojo:
            for r in reversed(self.regs):
                if r["de"] == de and r["seq"] == seq:
                    return r
        return None

    def cambiar(self, n, guardar=True, **cambios):
        with self.cerrojo:
            r = self.buscar(n)
            if r:
                r.update(cambios)
                if guardar:
                    self.guardar()
            return r

    def desde(self, n):
        with self.cerrojo:
            return [dict(r) for r in self.regs if r["n"] > n]


almacen = Almacen()
eventos = queue.Queue()
estado = {"puerto": None, "rssi": 1, "contacto": 0.0, "respuestas": [], "alcance": None,
          "presencia": {}, "nivel": 0, "recibos_en_vuelo": False, "ultimo_reintento": 0.0,
          "latidos": [], "latidos_salen": 0, "latidos_llegan": 0, "inicio": time.time()}


def ruta_foto(n):
    return os.path.join(FOTOS, f"{n}.jpg")


class RadioDemo(threading.Thread):
    """Para mirar la app sin módulo (servidor_pc.py --demo): contesta como si ella estuviera."""

    def __init__(self, ev):
        super().__init__(daemon=True)
        self.ev = ev
        self.latido_s, self.turbo_permitido = 150, True

    def presencia(self):
        return {"cerca": True, "hubo": True, "visto_hace": 20, "desde_hace": 600, "rssi": -101, "alla": -103,
                "potencia_otro": 22, "rssi_visto": -101, "visto_ts": int(time.time()) - 20, "periodo": 150,
                "nivel": 0, "turbo_posible": 1, "eventos": []}

    def encolar(self, **t):
        def hacer():
            c = t["clase"]
            if c == "texto":
                time.sleep(2)
                self.ev.put(("resultado", t["ref"], True, -97))
            elif c == "foto":
                for k in range(0, 101, 10):
                    self.ev.put(("progreso", t["ref"], k))
                    time.sleep(0.6)
                self.ev.put(("resultado", t["ref"], True, None))
            elif c == "comando":
                time.sleep(2)
                self.ev.put(("respuesta", f"(demo) recibido: {t['texto']}"))
            elif c == "ping":
                time.sleep(2)
                self.ev.put(("ping", (-99, -101, 2.1)))
            elif c == "recibos":
                self.ev.put(("recibos_ok", t["seqs"]))
        threading.Thread(target=hacer, daemon=True).start()

    def run(self):
        self.ev.put(("conexion", "DEMO"))
        time.sleep(4)
        self.ev.put(("rssi", -101))
        self.ev.put(("presencia", "encuentro", -101))
        self.ev.put(("de_ella", {"seq": 1, "ts": int(time.time()), "texto": "holiii 💗 ¿cómo va tu día?", "rssi": -101}))


completas = {r["seq"] for r in almacen.regs if r["de"] == DE_ELLA and r["tipo"] == 1 and r["estado"] == RECIBIDO and r["seq"]}
radio = RadioDemo(eventos) if DEMO else radio_pc.Radio(eventos, latido_s=ajustes["latido_s"], turbo=ajustes["turbo"],
                                                         fotos_completas=completas)


def mandar(r):
    if r["tipo"] == 0:
        radio.encolar(clase="texto", texto=r["texto"], seq=r["seq"], ts=r["ts"], ref=str(r["n"]))
    else:
        try:
            datos = open(ruta_foto(r["n"]), "rb").read()
        except OSError:
            almacen.cambiar(r["n"], estado=FALLIDO, intentos=255)  # sin el archivo no hay qué mandar
            return
        radio.encolar(clase="foto", datos=datos, seq=r["seq"], ts=r["ts"], ref=str(r["n"]))


def reintentar_pendientes():
    """Lo de él que no llegó: se manda otra vez. Para siempre, pero sólo cuando hay contacto."""
    estado["ultimo_reintento"] = time.time()
    for r in almacen.desde(0)[-200:]:
        if r["de"] == DE_EL and r["estado"] == FALLIDO and r["intentos"] < 255:
            almacen.cambiar(r["n"], estado=ENVIANDO, intentos=min(254, r["intentos"] + 1))
            mandar(r)


def recibos_pendientes():
    return [r["seq"] for r in almacen.desde(0) if r["de"] == DE_ELLA and r["leido"] and not r["recibo"] and r["seq"]]


def mandar_recibos():
    seqs = recibos_pendientes()
    if seqs and not estado["recibos_en_vuelo"]:
        estado["recibos_en_vuelo"] = True
        radio.encolar(clase="recibos", seqs=seqs[:100])


def procesar_eventos():
    while True:
        ev = eventos.get()
        tipo = ev[0]
        if tipo == "conexion":
            estado["puerto"] = ev[1]
        elif tipo == "rssi":
            estado["rssi"] = ev[1]
            estado["contacto"] = time.time()
        elif tipo == "presencia":
            estado["presencia"] = radio.presencia()
            if ev[1] == "encuentro":  # volvió la señal: todo lo pendiente, ahora
                reintentar_pendientes()
                mandar_recibos()
        elif tipo == "nivel":
            estado["nivel"] = ev[1]
        elif tipo == "latido":  # para la app: cuándo se dieron la mano, con qué señal
            estado["latidos"] = (estado["latidos"] + [ev[1]])[-80:]
            estado["latidos_salen" if ev[1]["dir"] == "sale" else "latidos_llegan"] += 1
        elif tipo == "hora":
            estado["respuestas"] = (estado["respuestas"] + [{"t": time.time(), "texto": "✓ su hora quedó igual a la del PC ("
                                                              + time.strftime("%H:%M:%S") + ")"}])[-20:]
        elif tipo == "de_ella":
            d = ev[1]
            if not almacen.buscar_seq(DE_ELLA, d["seq"]):  # repetido (se había perdido el acuse): se ignora
                ahora = int(time.time())
                almacen.agregar(de=DE_ELLA, tipo=0, estado=RECIBIDO, rssi=d["rssi"], texto=d["texto"], seq=d["seq"],
                                ts=d["ts"] if d["ts"] > 1700000000 else ahora, llegada=ahora)
        elif tipo == "foto_llegando":
            d = ev[1]
            r = almacen.buscar_seq(DE_ELLA, d["seq"]) if d["seq"] else None
            if r is None:
                ahora = int(time.time())
                r = almacen.agregar(de=DE_ELLA, tipo=1, estado=ENVIANDO, texto="foto", seq=d["seq"], rssi=d.get("rssi", 0),
                                    ts=d["ts"] if d.get("ts", 0) > 1700000000 else ahora, llegada=ahora)
            if r["estado"] != RECIBIDO:
                almacen.cambiar(r["n"], guardar=r["estado"] != ENVIANDO, estado=ENVIANDO, prog=d["pct"] * 10)
        elif tipo == "foto_pausa":
            d = ev[1]
            r = almacen.buscar_seq(DE_ELLA, d["seq"])
            if r and r["estado"] == ENVIANDO:
                almacen.cambiar(r["n"], estado=FALLIDO, prog=d["pct"] * 10)  # "a medias: sigue sola"
        elif tipo == "foto_de_ella":
            d = ev[1]
            r = almacen.buscar_seq(DE_ELLA, d["seq"])
            if r is None:
                r = almacen.agregar(de=DE_ELLA, tipo=1, estado=ENVIANDO, texto="foto", seq=d["seq"], ts=d["ts"] or int(time.time()))
            os.replace(d["ruta"], ruta_foto(r["n"]))
            almacen.cambiar(r["n"], estado=RECIBIDO, prog=1000, rssi=d["rssi"], llegada=int(time.time()))
        elif tipo == "progreso":
            almacen.cambiar(int(ev[1]), guardar=False, prog=ev[2] * 10)
        elif tipo == "resultado":
            _, ref, ok, alla = ev
            r = almacen.buscar(int(ref))
            if r and ok:
                almacen.cambiar(r["n"], estado=ENTREGADO, prog=1000, llegada=int(time.time()),
                                **({"rssi": alla} if alla is not None else {}))
            elif r and r["estado"] != ENTREGADO:
                almacen.cambiar(r["n"], estado=FALLIDO, prog=0)
        elif tipo == "leidos_por_ella":
            for s in ev[1]:
                r = almacen.buscar_seq(DE_EL, s)
                if r and not r["leido"]:
                    cambios = {"leido": 1}
                    if r["estado"] != ENTREGADO:  # si lo leyó, le llegó (aunque el acuse se haya perdido)
                        cambios.update(estado=ENTREGADO, prog=1000)
                    almacen.cambiar(r["n"], **cambios)
        elif tipo == "recibos_ok":
            estado["recibos_en_vuelo"] = False
            for s in ev[1]:
                r = almacen.buscar_seq(DE_ELLA, s)
                if r:
                    almacen.cambiar(r["n"], recibo=1)
            mandar_recibos()  # por si quedaron más
        elif tipo == "recibos_fallidos":
            estado["recibos_en_vuelo"] = False
        elif tipo == "respuesta":
            estado["respuestas"] = (estado["respuestas"] + [{"t": time.time(), "texto": ev[1]}])[-20:]
        elif tipo == "canal":
            estado["respuestas"] = (estado["respuestas"] + [{"t": time.time(), "texto": ev[2]}])[-20:]
            m = re.search(r"canal ([0-9A-F]{2})", ev[2])
            if ev[1] and m:  # se recuerda para que configurar.py no lo desarme
                ruta = os.path.join(CARPETA, "secreto.json")
                s = json.load(open(ruta, encoding="utf-8"))
                s["canal"] = m.group(1)
                json.dump(s, open(ruta, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        elif tipo == "ping":
            a = estado["alcance"]
            if a is not None:
                a["enviados"] += 1
                if ev[1]:
                    vuelta, alla, rtt = ev[1]
                    a["ok"] += 1
                    a["aca"].append(vuelta)
                    if alla is not None:
                        a["alla"].append(alla)
                    a["rtt"].append(rtt)
                threading.Timer(3.0, lambda: estado["alcance"] is not None and radio.encolar(clase="ping")).start()


def vigilante():
    """Cada pocos segundos: presencia al día, reintentos mientras están cerca, recibos de lectura."""
    while True:
        time.sleep(5)
        try:
            estado["presencia"] = radio.presencia()
            if estado["presencia"].get("cerca"):
                if time.time() - estado["ultimo_reintento"] > REINTENTO_CERCA:
                    reintentar_pendientes()
                mandar_recibos()
        except Exception:
            pass


def resumen_alcance():
    a = estado["alcance"]
    if a is None:
        return None

    def prom(v):
        return round(sum(v) / len(v)) if v else None
    return {"enviados": a["enviados"], "ok": a["ok"], "alla": prom(a["alla"]), "aca": prom(a["aca"]),
            "rtt": round(sum(a["rtt"]) / len(a["rtt"]), 2) if a["rtt"] else None,
            "ultimo_alla": a["alla"][-1] if a["alla"] else None, "ultimo_aca": a["aca"][-1] if a["aca"] else None}


class Web(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, cuerpo, tipo="application/json", codigo=200, cache="no-store"):
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        elif isinstance(cuerpo, str):
            cuerpo = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(cuerpo)

    def cuerpo(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self.responder(open(PAGINA, "rb").read(), "text/html; charset=utf-8")
        elif u.path == "/api/estado":
            desde = int(q.get("desde", ["0"])[0])
            ultimo = almacen.regs[-1]["n"] if almacen.regs else 0
            base = 0 if desde <= 0 else min(desde, max(0, ultimo - 30))
            pend = sum(1 for r in almacen.regs[-300:] if r["de"] == DE_EL and r["estado"] in (ENVIANDO, FALLIDO))
            self.responder({
                "modo": "pc", "yo": YO, "hora": int(time.time()), "lora": bool(estado["puerto"]),
                "modulo": bool(estado["puerto"]), "rssi": estado["rssi"],
                "contacto": int(time.time() - estado["contacto"]) if estado["contacto"] else -1,
                "radio_pendiente": False, "version": "pc", "alcance": resumen_alcance(), "otro": ajustes["otro"],
                "respuestas": estado["respuestas"][-5:], "presencia": radio.presencia(), "pendientes": pend,
                "ajustes_pc": ajustes,
                "latidos": estado["latidos"][-40:], "radio_pc": {
                    "potencia": radio.potencia, "nivel": radio.nivel, "latido_s": radio.latido_s, "canal": radio.canal,
                    "salen": estado["latidos_salen"], "llegan": estado["latidos_llegan"], "desde": int(estado["inicio"])},
                "mensajes": [{"n": r["n"], "de": r["de"], "tipo": r["tipo"], "estado": r["estado"], "rssi": r["rssi"],
                              "ts": r["ts"], "llegada": r["llegada"], "prog": r["prog"], "leido": r["leido"],
                              "intentos": r["intentos"], "texto": r["texto"]} for r in almacen.desde(base)]})
        elif u.path == "/foto":
            try:
                self.responder(open(ruta_foto(int(q["n"][0])), "rb").read(), "image/jpeg", cache="max-age=31536000")
            except (OSError, KeyError, ValueError):
                self.responder({"error": "no"}, codigo=404)
        else:
            self.responder({"error": "no"}, codigo=404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/enviar":
            texto = self.cuerpo().decode("utf-8", "replace").strip()[:p.MAX_TEXTO * 2]
            while len(texto.encode("utf-8")) > p.MAX_TEXTO:
                texto = texto[:-1]
            if not texto:
                return self.responder({"error": "vacío"}, codigo=400)
            r = almacen.agregar(de=DE_EL, tipo=0, texto=texto)
            mandar(r)
            self.responder({"n": r["n"]})
        elif u.path == "/api/foto":
            datos = self.cuerpo()
            if not datos.startswith(b"\xff\xd8") or len(datos) > p.MAX_FOTO:
                return self.responder({"error": "foto"}, codigo=400)
            r = almacen.agregar(de=DE_EL, tipo=1, texto="foto")
            with open(ruta_foto(r["n"]), "wb") as f:
                f.write(datos)
            mandar(r)
            self.responder({"n": r["n"]})
        elif u.path == "/api/reintentar":
            r = almacen.buscar(int(q.get("n", ["0"])[0]))
            if r and r["de"] == DE_EL and r["estado"] == FALLIDO:
                almacen.cambiar(r["n"], estado=ENVIANDO, intentos=min(254, r["intentos"] + 1))
                mandar(r)
            self.responder({})
        elif u.path == "/api/leido":  # él tenía la app a la vista: leyó lo de ella hasta n
            hasta = int(q.get("hasta", ["0"])[0])
            cambio = False
            with almacen.cerrojo:
                for r in almacen.regs:
                    if r["n"] <= hasta and r["de"] == DE_ELLA and r["estado"] == RECIBIDO and not r["leido"]:
                        r["leido"], r["recibo"] = 1, 0 if r["seq"] else 1
                        cambio = True
                if cambio:
                    almacen.guardar()
            if cambio:
                mandar_recibos()
            self.responder({})
        elif u.path == "/api/hora":  # su reloj igual al del PC
            radio.encolar(clase="hora")
            estado["respuestas"] = (estado["respuestas"] + [{"t": time.time(), "texto": "→ poniendo su hora igual a la del PC…"}])[-20:]
            self.responder({})
        elif u.path == "/api/aparato":  # comando por radio al aparato de ella
            cmd = self.cuerpo().decode("utf-8", "replace").strip()
            if cmd:
                radio.encolar(clase="comando", texto=cmd)
            self.responder({})
        elif u.path == "/api/ajustes_pc":
            cambios = parse_qs(self.cuerpo().decode("utf-8", "replace"))
            if "latido" in cambios and 30 <= int(cambios["latido"][0]) <= 180:  # nunca más de 3 min sin darse la mano
                ajustes["latido_s"] = int(cambios["latido"][0])
                radio.latido_s = ajustes["latido_s"]
            if "turbo" in cambios:
                ajustes["turbo"] = cambios["turbo"][0] == "1"
                radio.turbo_permitido = ajustes["turbo"]
            if "otro" in cambios and cambios["otro"][0].strip():
                ajustes["otro"] = cambios["otro"][0].strip()[:23]
            guardar_ajustes()
            self.responder(ajustes)
        elif u.path == "/api/canal":
            c = q.get("c", [""])[0].upper()
            if len(c) == 2 and 0x34 <= int(c, 16) <= 0x4D:
                estado["respuestas"] = (estado["respuestas"] + [{"t": time.time(), "texto": f"→ cambiando los dos al canal {c}… (hasta 1 min)"}])[-20:]
                radio.encolar(clase="canal", canal=c)
                self.responder({})
            else:
                self.responder({"error": "canal 34..4D"}, codigo=400)
        elif u.path == "/api/alcance":
            if q.get("accion", [""])[0] == "empezar":
                estado["alcance"] = {"enviados": 0, "ok": 0, "alla": [], "aca": [], "rtt": []}
                radio.encolar(clase="ping")
            else:
                estado["alcance"] = None
            self.responder({})
        else:
            self.responder({"error": "no"}, codigo=404)


class ServidorUnico(ThreadingHTTPServer):
    """Una sola copia de la app: en Windows, SO_REUSEADDR (lo que trae HTTPServer) deja que OTRA copia escuche
    el mismo puerto, y las peticiones de la ventana caían al azar en copias sin radio (pasó: 3 copias abiertas)."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def abrir_ventana():
    url = f"http://127.0.0.1:{PUERTO_WEB}/"
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(exe):
            import subprocess
            subprocess.Popen([exe, f"--app={url}", "--window-size=460,820"])
            return
    webbrowser.open(url)


if __name__ == "__main__":
    try:
        servidor = ServidorUnico(("127.0.0.1", PUERTO_WEB), Web)
    except OSError:  # ya estaba abierto: sólo se muestra la ventana
        abrir_ventana()
        sys.exit(0)
    threading.Thread(target=procesar_eventos, daemon=True).start()
    threading.Thread(target=vigilante, daemon=True).start()
    radio.start()
    if "--sin-ventana" not in sys.argv:
        threading.Timer(0.8, abrir_ventana).start()
    servidor.serve_forever()
