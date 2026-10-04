# La radio del lado PC: la misma lógica que firmware-s3/main/enlace.c, en Python.
# Reglas (aparato de supervivencia): nada se pierde (se reintenta mientras haya contacto), nada se repite
# (seq), nada es ambiguo (✓✓ llegó / leído), el latido dice si el otro está cerca, las fotos cortadas
# siguen desde donde quedaron, se puede conversar durante una foto, y el turbo sólo con señal de sobra
# (ante cualquier duda, nivel 0 = máximo alcance).
import collections, json, os, queue, random, re, struct, threading, time
import serial
from serial.tools import list_ports

import protocolo as p

CARPETA = os.path.dirname(os.path.abspath(__file__))
INTENTOS_TEXTO = 5
TROZOS_POR_CONSULTA = 4
SILENCIO = 2.5             # si el otro habló hace menos, no se le interrumpe
FOTO_PARADA = 45           # una foto que no avanza ya no bloquea el turno
FOTO_EN_PAUSA = 120        # sin trozos 2 min: la foto queda "a medias" (se guarda lo que llegó)
FOTO_GUARDADA = 24 * 3600  # lo de una foto cortada se guarda 1 día
TURBO_SILENCIO = 20
TURBO_MAXIMO = 15 * 60
TROZOS_MIN_TURBO = 6
LATIDO_PERDIDO = 20        # perdidos: se buscan cada 20 s, sin parar
MARGEN_RESPUESTA = 1.5     # lo que se espera de más por una respuesta (más un poco de azar)
AZAR_RESPUESTA = 1.2
PAUSA_RESPUESTA = 0.08     # antes de contestar: que el otro alcance a pasar a recibir
PAUSA_NIVEL = 0.2
ENTRE_PAQUETES = 0.15       # respiro entre un paquete y el siguiente
ESPERA_CAMBIO = 0.8        # turbo: lo que se le da al otro para cambiar de nivel
MARGEN_PING = 1.2          # ping de prueba en el nivel nuevo
MARGEN_VENTANA = 0.7       # lo que se espera de más por el mensaje del otro durante una foto
PERMISO_VENTANA = 1.5      # pedí la palabra en su foto: puedo empezar a hablar hasta este tiempo después
ALARGUE_ACUSE = 0.4        # me acusaron dentro de su ventana: sigue abierta este tiempo
RESPUESTA_RAPIDA = 1.5     # latido de respuesta: sale dentro de este tiempo (sólo ADELANTA el próximo)
ESPACIO_RETROCESO = 0.6    # tras un intento sin respuesta: lo que se espera de más (además del azar)
CEDER_ESPERA = 30          # si ella empezó una foto justo a la vez, la mía sale después
UMBRAL_EXTRA = 30          # perdido = 2,5 latidos + esto sin oír nada
RESPUESTA_LATIDO_MIN = 20  # latido de respuesta inmediata: como máximo uno cada tanto
LATIDO_INICIAL = 5         # el primer latido, poco después de encender
FRESCURA = 300             # la señal medida vale para decidir el turbo durante este tiempo
VOLVER_CANAL_BASE = 30 * 60  # (ya no se usa: el canal es fijo)
CHEQUEO_PERDIDOS = 10 * 60  # perdidos: cada tanto se revisa que la radio propia esté como debe
# La radio como debe estar (igual que configurar.py y el aparato). La clave no se puede leer: se repone si el
# módulo aparece reseteado (3 o más cosas fuera de lugar).
ESPERADO = [("MODE", "0"), ("LEVEL", "0"), ("POWE", "22"), ("CHANNEL", p.CANAL_BASE), ("SLEEP", "2"), ("SWITCH", "0"),
            ("OPENKEY", "1"), ("PACKET", "3"), ("DRSSI", "1"), ("LBT", "0"), ("IQ", "1"), ("CRC", "1")]
PAUSA_AT = 0.25            # el "+++" tiene que llegar solo, no pegado al último paquete


def buscar_puerto():
    for pt in list_ports.comports():
        if pt.vid == 0x1A86 and pt.pid in (0x7523, 0x7522, 0x5523):  # CH340 del adaptador DX-PJ15
            return pt.device
    return None


def i8(x):
    return struct.pack("<b", max(-128, min(127, int(x))))


class Radio(threading.Thread):
    def __init__(self, eventos, latido_s=150, turbo=True, potencia=22, fotos_completas=()):
        super().__init__(daemon=True)
        self.eventos = eventos
        self.trabajos = queue.Queue()
        self.lector = p.Lector()
        self.puerto = None
        self.canal_libre = 0.0
        self.ultimo_rx = 0.0
        self.ventana_hasta = 0.0          # ventana que abrió el otro para que yo hable durante su foto
        self.esperando = None             # (tipos, id) -> la respuesta queda en self.respuesta
        self.respuesta = None
        self.vistos = {}
        self.trabajo = None               # lo que se está haciendo ahora (para decidir qué aceptar)
        self.fase_foto = None             # "inicio" | "trozos" | "pregunta" mientras mando una foto
        self.ceder = False                # ella empezó a mandarme una foto justo cuando yo iba a mandar la mía
        self.texto_esperando = False      # tengo un mensaje esperando turno (para pedírselo en su foto)
        self.latido_s = latido_s
        self.turbo_permitido = turbo
        self.potencia = potencia
        self.seq = int(time.time()) & 0x7FFF or 1  # para trabajos que no traen seq (pruebas)
        self.nivel = 0                    # nivel de radio ahora (0 = máximo alcance)
        # foto que me está llegando (se guarda aunque se corte, para seguir después)
        self.rx = None
        self.completas = set(fotos_completas)   # seqs de fotos de ella que ya tengo enteras
        self.ultima_foto_completa = None
        # presencia
        ahora = time.time()
        self.lat = {"cerca": False, "hubo": False, "suave": None, "alla": 127, "alla_t": 0.0, "pot_otro": 22,
                    "visto_t": 0.0, "visto_ts": 0, "desde_t": ahora, "rssi_visto": 0, "respondido": 0.0,
                    "proximo": ahora + LATIDO_INICIAL * (1 + random.random()), "eventos": []}
        # turbo
        self.turbo = False
        self.turbo_emisor = False
        self.turbo_desde = 0.0
        # para pruebas (inyección de fallas)
        self.pausada = False              # como si estuvieran fuera de alcance: no oye ni habla
        self.descartar_acuses = 0         # no manda los próximos N acuses de mensajes (se "pierden")
        self.no_cambiar_nivel = False     # finge que su módulo no alcanzó a cambiar de nivel
        # diagnóstico y canal
        self.bitacora = collections.deque(maxlen=500)  # (hora, texto): lo que salió, llegó y pasó
        self.rssi_bueno = -120            # la última señal creíble (pesimista al partir: nada de turbo por error)
        self.canal = None                 # canal del módulo (se lee al conectar)
        self.inicio = time.time()
        self.proximo_intento_base = 0.0
        self.proximo_chequeo = time.time() + CHEQUEO_PERDIDOS

    def anotar(self, texto):
        self.bitacora.append((time.time(), texto))

    def bitacora_texto(self, ultimos=500):
        ahora = time.time()
        return [f"{t - ahora:8.1f}s {x}" for t, x in list(self.bitacora)[-ultimos:]]

    # ------------------------------------------------------------------ puerto
    def conectar(self):
        while True:
            nombre = buscar_puerto()
            if nombre:
                try:
                    self.puerto = serial.Serial(nombre, 9600, timeout=0.05)
                    self.revisar_modulo()
                    self.anotar(f"## módulo conectado en {nombre} (canal {self.canal}, {self.potencia} dBm)")
                    self.eventos.put(("conexion", nombre))
                    return
                except serial.SerialException as e:
                    self.puerto = None
                    # típico: la app "Mensajes LoRa" ya está abierta y tiene el módulo
                    self.eventos.put(("conexion", None, f"{nombre} ocupado: {e}"))
                    time.sleep(2)
                    continue
            self.eventos.put(("conexion", None, "no encuentro el módulo LoRa enchufado"))
            time.sleep(2)

    def at_local(self, comandos):
        """Modo AT del módulo propio (sordo un par de segundos). Devuelve las respuestas o None."""
        s = self.puerto
        espera = self.canal_libre - time.time()
        if espera > 0:  # que el módulo termine de transmitir: si no, el "+++" sale por el aire pegado al paquete
            time.sleep(espera)
        time.sleep(PAUSA_AT)
        t0 = time.time()

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
                self.anotar(f"@@ AT {';'.join(comandos)}: NO ENTRÓ al modo AT ({time.time() - t0:.1f} s)")
                return None
        # consulta ("AT+CHANNEL") = basta la línea; cambio ("AT+CHANNEL3A") = hasta el OK
        salida = [hablar((c + "\r\n").encode(), 1.2, b"\r\n" if re.fullmatch(r"AT\+[A-Z]+", c) else b"OK")
                  for c in comandos]
        hablar(b"+++\r\n", 2.5, b"Power on")
        time.sleep(0.3)  # que termine de arrancar antes de darle un paquete
        self.lector = p.Lector()
        self.canal_libre = time.time()
        self.anotar(f"@@ AT {';'.join(comandos)} -> {' | '.join(x.strip() for x in salida)} ({time.time() - t0:.1f} s)")
        return salida

    def clave_modulo(self):
        try:
            return int(json.load(open(os.path.join(CARPETA, "secreto.json"), encoding="utf-8"))["clave_modulo"])
        except (OSError, ValueError, KeyError):
            return None

    def revisar_configuracion(self, motivo):
        """La radio propia como debe estar; se corrige lo que no calce. Devuelve cuántas cosas estaban mal."""
        r = self.at_local([f"AT+{n}" for n, _ in ESPERADO])
        if r is None:
            self.anotar(f"## revisar radio ({motivo}): no entró al modo AT")
            return -1
        malos = []
        for (n, v), resp in zip(ESPERADO, r):
            m = re.search(rf"\+{n}=([0-9A-Fa-f]+)", resp)
            if not m or m.group(1).upper() != v.upper():
                malos.append((n, v))
        if malos:
            cmds = [f"AT+{n}{v}" for n, v in malos]
            clave = self.clave_modulo() if len(malos) >= 3 else None  # parece reseteado: la clave de la pareja también
            if clave:
                cmds.append(f"AT+KEY{clave}")
            self.at_local(cmds)
            self.eventos.put(("radio_corregida", motivo, [n for n, _ in malos]))
        self.anotar(f"## revisar radio ({motivo}): {len(malos)} corregidos {[n for n, _ in malos] if malos else ''}")
        self.potencia, self.canal, self.nivel, self.turbo = 22, p.CANAL_BASE, 0, False
        return len(malos)

    def revisar_modulo(self):
        """Al conectar: la radio entera como debe estar (y en nivel 0: si la app se cerró en turbo, quedó cambiado)."""
        if self.revisar_configuracion("al conectar") >= 0:
            self.lector = p.Lector()
            return
        r = self.at_local(["AT+LEVEL", "AT+POWE", "AT+CHANNEL"]) or ["", "", ""]
        m = re.search(r"\+LEVEL=(\d)", r[0])
        if m and m.group(1) != "0":
            self.at_local(["AT+LEVEL0"])
        m = re.search(r"\+POWE=(\d+)", r[1] if len(r) > 1 else "")
        if m:
            self.potencia = int(m.group(1))
        m = re.search(r"\+CHANNEL=([0-9A-Fa-f]{2})", r[2] if len(r) > 2 else "")
        if m:
            self.canal = m.group(1).upper()
        self.nivel = 0
        self.turbo = False

    def transmitir(self, paquete):
        if len(paquete) % 32 == 31:
            # el DX-LR32 se GUARDA sin transmitir lo que mide 31, 63, 95... bytes hasta que le llega otra
            # cosa (medido: siempre). Un 0x00 adelante lo evita; el que recibe lo salta (busca el 0xD5)
            paquete = b"\x00" + paquete
        espera = self.canal_libre - time.time()
        if espera > 0:
            self.escuchar(espera)
        if not self.pausada:
            for _ in range(2):
                try:
                    t0 = time.time()
                    self.puerto.write(paquete)
                    if time.time() - t0 > 1.0:  # el cable USB está fallando (o Windows trabó el puerto)
                        self.anotar(f"!! escribir al módulo tardó {time.time() - t0:.1f} s")
                    break
                except (serial.SerialException, AttributeError):
                    self.anotar("!! el puerto falló al escribir: reconecto")
                    self.conectar()
        self.canal_libre = time.time() + self.aire(len(paquete)) + ENTRE_PAQUETES

    def mandar(self, tipo, ident, cuerpo=b""):
        self.anotar(f"SALE  {tipo.decode()} id={ident:04x} {len(cuerpo)} B nivel={self.nivel}"
                    f"{' (en pausa: no sale)' if self.pausada else ''}")
        self.transmitir(p.empaquetar(tipo, ident, cuerpo))

    def responder(self, tipo, ident, cuerpo=b""):
        time.sleep(PAUSA_RESPUESTA)
        self.mandar(tipo, ident, cuerpo)

    def escuchar(self, segundos, hasta_respuesta=False):
        fin = time.time() + segundos
        while time.time() < fin:
            try:
                t0 = time.time()
                datos = self.puerto.read(256)
                if time.time() - t0 > 2.0:  # leer dura 0,05 s: el cable USB está fallando
                    self.anotar(f"!! leer del módulo tardó {time.time() - t0:.1f} s")
            except (serial.SerialException, AttributeError):
                self.anotar("!! el puerto falló al leer: reconecto")
                self.puerto = None
                self.eventos.put(("conexion", None))
                self.conectar()
                continue
            if self.pausada:
                continue
            for tipo, ident, cuerpo, rssi in self.lector.alimentar(datos):
                if p.rssi_creible(rssi):
                    self.rssi_bueno = rssi
                    self.anotar(f"LLEGA {tipo.decode()} id={ident:04x} {len(cuerpo)} B {rssi} dBm")
                else:  # llegó algo pegado al paquete: ese byte no es la señal
                    self.anotar(f"LLEGA {tipo.decode()} id={ident:04x} {len(cuerpo)} B señal dudosa ({rssi}): uso {self.rssi_bueno}")
                    rssi = self.rssi_bueno
                self.recibido(tipo, ident, cuerpo, rssi)
            if hasta_respuesta and self.respuesta is not None:
                return

    def esperar(self, tipos, ident, segundos):
        self.esperando, self.respuesta = (tipos, ident), None
        self.escuchar(segundos, hasta_respuesta=True)
        self.esperando = None
        return self.respuesta

    def aire(self, bytes_paquete):
        return p.segundos_en_aire(bytes_paquete, self.nivel)

    def retroceso(self, intento, bytes_ida):
        """Tras un intento sin respuesta (quizás chocamos): el PC siempre espera un paquete más que ella
        (lo de ella pasa primero) y un azar que crece con cada intento."""
        if intento < 1:
            return 0.0
        largo = self.aire(bytes_ida) + ESPACIO_RETROCESO
        return largo + random.random() * intento * largo

    def espera_respuesta(self, bytes_ida):
        return self.aire(bytes_ida) + self.aire(16) + MARGEN_RESPUESTA + random.random() * AZAR_RESPUESTA

    # ------------------------------------------------------------------ turnos
    def foto_viva(self):
        r = self.rx
        return r is not None and not r["en_pausa"] and time.time() - r["t"] < FOTO_PARADA

    def turno_libre(self):
        return not self.foto_viva() and time.time() - self.ultimo_rx > SILENCIO

    def turno_para_texto(self):
        return self.turno_libre() or time.time() < self.ventana_hasta

    def esperar_turno(self, maximo=600, texto=False):
        fin = time.time() + maximo
        self.texto_esperando = texto
        while not (self.turno_para_texto() if texto else self.turno_libre()) and time.time() < fin:
            self.escuchar(0.02)
        self.texto_esperando = False

    def hay_texto_en_cola(self):
        with self.trabajos.mutex:
            return any(t["clase"] == "texto" for t in self.trabajos.queue)

    def sacar_textos(self):
        with self.trabajos.mutex:
            textos = [t for t in self.trabajos.queue if t["clase"] == "texto"]
            for t in textos:
                self.trabajos.queue.remove(t)
        return textos

    def ya_visto(self, tipo, ident):
        ahora = time.time()
        self.vistos = {k: v for k, v in self.vistos.items() if ahora - v < 600}
        if (tipo, ident) in self.vistos:
            return True
        self.vistos[(tipo, ident)] = ahora
        return False

    # ------------------------------------------------------------------ presencia (latido)
    def umbral_perdido(self):
        return self.latido_s * 2.5 + UMBRAL_EXTRA

    def periodo_latido(self):
        return self.latido_s if self.lat["cerca"] else min(self.latido_s, LATIDO_PERDIDO)

    def programar_latido(self):
        per = self.periodo_latido()
        self.lat["proximo"] = time.time() + per * (0.8 + 0.4 * random.random())

    def presencia(self):
        l = self.lat
        ahora = time.time()
        return {"cerca": l["cerca"], "hubo": l["hubo"], "visto_hace": int(ahora - l["visto_t"]) if l["hubo"] else -1,
                "desde_hace": int(ahora - l["desde_t"]), "rssi": round(l["suave"]) if l["suave"] is not None else 1,
                "alla": l["alla"], "potencia_otro": l["pot_otro"], "rssi_visto": l["rssi_visto"],
                "visto_ts": l["visto_ts"], "periodo": self.latido_s, "nivel": self.nivel,
                "turbo_posible": self.nivel_posible(), "eventos": l["eventos"][-12:]}

    def contacto(self, rssi):
        l = self.lat
        ahora = time.time()
        l["visto_t"], l["visto_ts"], l["rssi_visto"] = ahora, int(ahora), rssi
        l["suave"] = rssi if (l["suave"] is None or not l["cerca"]) else l["suave"] * 0.7 + rssi * 0.3
        l["hubo"] = True
        if not l["cerca"]:
            l["cerca"] = True
            l["desde_t"] = ahora
            l["eventos"].append({"encuentro": True, "ts": int(ahora), "rssi": rssi})
            if ahora - l["respondido"] > RESPUESTA_LATIDO_MIN:  # que ella también se entere al tiro
                l["respondido"] = ahora
                l["proximo"] = min(l["proximo"], ahora + RESPUESTA_RAPIDA * (0.2 + random.random()))
            self.eventos.put(("presencia", "encuentro", rssi))

    def revisar_presencia(self):
        l = self.lat
        if l["cerca"] and time.time() - l["visto_t"] > self.umbral_perdido():
            l["cerca"] = False
            l["desde_t"] = time.time()
            l["eventos"].append({"encuentro": False, "ts": int(time.time()), "rssi": l["rssi_visto"]})
            self.programar_latido()
            self.proximo_chequeo = 0  # perdidos: revisar la radio propia al tiro
            self.eventos.put(("presencia", "perdida", l["rssi_visto"]))

    def mandar_latido(self):
        l = self.lat
        desde = min(int(time.time() - l["visto_t"]), 0xFFFE) if l["hubo"] else 0xFFFF
        cuerpo = i8(l["rssi_visto"] if l["hubo"] else 127) + struct.pack("<HBI", desde, self.potencia, int(time.time()))
        self.mandar(p.LATIDO, random.randint(1, 0xFFFF), cuerpo)
        self.eventos.put(("latido", {"dir": "sale", "t": time.time(), "cerca": l["cerca"]}))
        self.programar_latido()

    # ------------------------------------------------------------------ turbo
    def nivel_posible(self):
        l = self.lat
        ahora = time.time()
        if (not self.turbo_permitido or not l["cerca"] or l["suave"] is None or ahora - l["visto_t"] > FRESCURA
                or l["alla"] == 127 or ahora - l["alla_t"] > FRESCURA):
            return 0
        m = min(l["suave"], l["alla"])
        return 3 if m >= -100 else 2 if m >= -107 else 1 if m >= -112 else 0

    def cambiar_nivel(self, n):
        espera = self.canal_libre - time.time()
        if espera > 0:
            time.sleep(espera)
        time.sleep(PAUSA_NIVEL)
        if not self.no_cambiar_nivel:
            self.at_local([f"AT+LEVEL{n}"])
        self.anotar(f"## nivel {self.nivel} -> {n}")
        self.nivel = n
        self.turbo = n > 0
        self.turbo_desde = time.time()
        self.lector = p.Lector()
        self.ultimo_rx = time.time()
        self.canal_libre = time.time()
        self.eventos.put(("nivel", n))

    def pedir_turbo(self, nv):
        ident = random.randint(1, 0xFFFF)
        for _ in range(3):
            self.esperar_turno()
            self.mandar(p.VELOCIDAD, ident, bytes([nv]))
            r = self.esperar((p.ACUSE,), ident, self.espera_respuesta(6))
            if r:
                if r[1][:1] != bytes([nv]):
                    return False  # no quiso: normal
                self.turbo_emisor = True
                self.cambiar_nivel(nv)
                self.escuchar(ESPERA_CAMBIO)  # que ella alcance a cambiar
                for _ in range(6):
                    pid = random.randint(1, 0xFFFF)
                    self.mandar(p.PING, pid)
                    if self.esperar((p.PONG,), pid, self.aire(5) + self.aire(6) + MARGEN_PING):
                        return True
                self.cambiar_nivel(0)  # no nos encontramos en el nivel nuevo
                self.escuchar(TURBO_SILENCIO * 1.05)
                return False
        self.escuchar(TURBO_SILENCIO * 1.05)  # no contestó: por si alcanzó a cambiar, que vuelva sola
        return False

    def volver_turbo(self):
        ident = random.randint(1, 0xFFFF)
        for _ in range(2):
            self.mandar(p.VELOCIDAD, ident, b"\x00")
            if self.esperar((p.ACUSE,), ident, self.espera_respuesta(6)):
                break
        self.cambiar_nivel(0)
        self.turbo_emisor = False

    # ------------------------------------------------------------------ lo que llega
    def recibido(self, tipo, ident, cuerpo, rssi):
        self.ultimo_rx = time.time()
        self.contacto(rssi)
        self.eventos.put(("rssi", rssi))
        r8 = i8(rssi)
        if tipo in (p.ACUSE, p.PONG) and cuerpo[:1] and struct.unpack("<b", cuerpo[:1])[0] < 0:
            self.lat["alla"], self.lat["alla_t"] = struct.unpack("<b", cuerpo[:1])[0], time.time()
        if self.esperando and ident == self.esperando[1] and tipo in self.esperando[0]:
            self.respuesta = (tipo, cuerpo, rssi)
            if tipo == p.ACUSE and self.rx is not None and self.trabajo and self.trabajo["clase"] == "texto":
                self.ventana_hasta = time.time() + ALARGUE_ACUSE  # la ventana de su foto sigue abierta
            return
        if tipo in (p.MENSAJE, p.GRUPO):
            if tipo == p.GRUPO and not p.grupo_cuadra(cuerpo):  # llegó roto: sin acuse, que lo repita entero
                self.anotar(f"?? grupo roto ({len(cuerpo)} B): sin acuse")
                return
            if self.descartar_acuses > 0:
                self.descartar_acuses -= 1
            else:
                self.responder(p.ACUSE, ident, r8)
            if self.ya_visto(tipo, ident):
                return
            if tipo == p.MENSAJE and len(cuerpo) >= 6:
                items = [(*struct.unpack("<HI", cuerpo[:6]), cuerpo[6:])]
            else:
                items = p.abrir_grupo(cuerpo) if tipo == p.GRUPO else []
            for seq, ts, tb in items:  # el servidor descarta los repetidos por seq
                self.eventos.put(("de_ella", {"seq": seq, "ts": ts, "texto": tb.decode("utf-8", "replace"),
                                              "rssi": rssi}))
        elif tipo == p.PING:
            self.responder(p.PONG, ident, r8)
        elif tipo == p.LATIDO and len(cuerpo) >= 8:
            me_oye, desde, pot, _hora = struct.unpack("<bHBI", cuerpo[:8])
            self.eventos.put(("latido", {"dir": "llega", "t": time.time(), "rssi": rssi,
                                         "me_oye": None if me_oye == 127 else me_oye, "pot": pot}))
            if me_oye != 127:
                self.lat["alla"], self.lat["alla_t"] = me_oye, time.time()
            self.lat["pot_otro"] = pot
            if (desde == 0xFFFF or desde > self.umbral_perdido()) and time.time() - self.lat["respondido"] > RESPUESTA_LATIDO_MIN:
                self.lat["respondido"] = time.time()
                self.lat["proximo"] = min(self.lat["proximo"], time.time() + RESPUESTA_RAPIDA * (0.2 + random.random()))
        elif tipo == p.LEIDO:
            self.responder(p.ACUSE, ident, r8)
            seqs = [struct.unpack("<H", cuerpo[i:i + 2])[0] for i in range(0, len(cuerpo) - 1, 2)]
            self.eventos.put(("leidos_por_ella", seqs))
        elif tipo == p.VELOCIDAD and cuerpo:
            nv = cuerpo[0]
            if nv == 0:
                self.responder(p.ACUSE, ident, b"\x00")
                if self.turbo:
                    self.cambiar_nivel(0)
                return
            puedo = self.turbo_permitido and nv <= p.NIVEL_MAX_TURBO and self.rx_libre() and self.trabajo is None
            self.responder(p.ACUSE, ident, bytes([nv if puedo else 0]))
            if puedo:
                self.turbo_emisor = False
                self.cambiar_nivel(nv)
        elif tipo == p.FOTO and len(cuerpo) >= 14:
            self.llega_aviso_foto(ident, cuerpo, rssi, r8)
        elif tipo == p.TROZO_FOTO and len(cuerpo) >= 5:
            self.llega_trozo(cuerpo)
        elif tipo == p.PREGUNTA_FOTO and len(cuerpo) >= 2:
            self.contestar_pregunta(ident, struct.unpack("<H", cuerpo[:2])[0], rssi)

    def rx_libre(self):
        return self.rx is None or self.rx["en_pausa"]

    def llega_aviso_foto(self, ident, cuerpo, rssi, r8):
        foto, largo, trozos, seq, ts = struct.unpack("<HIHHI", cuerpo[:14])
        if self.fase_foto in ("trozos", "pregunta"):
            return  # yo estoy mandando la mía: ella espera
        if self.fase_foto == "inicio":
            self.ceder = True  # los dos empezamos a la vez: cedo y mando la mía después
        rx = self.rx
        if rx is not None and rx["foto"] == foto:
            self.responder(p.ACUSE, ident, r8 + b"\x01")
            return
        if not (0 < largo <= p.MAX_FOTO and trozos == -(-largo // p.TROZO)):
            return
        if foto == self.ultima_foto_completa or (seq and seq in self.completas):
            self.ultima_foto_completa = foto
            self.responder(p.ACUSE, ident, r8 + b"\x00")
            return
        if rx is not None and seq and rx["seq"] == seq and rx["largo"] == largo:  # la misma que quedó cortada
            rx.update(foto=foto, t=time.time(), en_pausa=False)
            self.responder(p.ACUSE, ident, r8 + b"\x01")
            self.eventos.put(("foto_llegando", {"seq": seq, "pct": self.pct_rx(), "ts": rx["ts"], "rssi": rssi}))
            return
        self.responder(p.ACUSE, ident, r8 + b"\x00")
        if rx is not None:
            self.eventos.put(("foto_pausa", {"seq": rx["seq"], "pct": self.pct_rx(), "perdida": True}))
        self.rx = {"foto": foto, "seq": seq, "ts": ts, "largo": largo, "trozos": trozos, "datos": bytearray(largo),
                   "tengo": set(), "t": time.time(), "creada": time.time(), "en_pausa": False}
        self.eventos.put(("foto_llegando", {"seq": seq, "pct": 0, "ts": ts, "rssi": rssi}))

    def pct_rx(self):
        rx = self.rx
        return 100 * len(rx["tengo"]) // rx["trozos"] if rx else 0

    def llega_trozo(self, cuerpo):
        rx = self.rx
        foto, i = struct.unpack("<HH", cuerpo[:4])
        if rx is None or foto != rx["foto"] or i >= rx["trozos"]:
            return
        d = cuerpo[4:]
        if i * p.TROZO + len(d) > rx["largo"]:
            return
        rx["datos"][i * p.TROZO:i * p.TROZO + len(d)] = d
        rx["tengo"].add(i)
        rx["t"] = time.time()
        rx["en_pausa"] = False
        self.eventos.put(("foto_llegando", {"seq": rx["seq"], "pct": self.pct_rx(), "ts": rx["ts"]}))

    def contestar_pregunta(self, ident, foto, rssi):
        rx = self.rx
        if rx is not None and rx["foto"] == foto:
            faltan = [i for i in range(rx["trozos"]) if i not in rx["tengo"]]
            if faltan:
                mapa = bytearray((rx["trozos"] + 7) // 8)
                for i in faltan:
                    mapa[i // 8] |= 1 << (i % 8)
                quiero = self.hay_texto_en_cola() or self.texto_esperando
                self.responder(p.FALTAN, ident, struct.pack("<H", foto) + bytes(mapa) + (b"\x01" if quiero else b"\x00"))
                if quiero:
                    self.ventana_hasta = self.canal_libre + PERMISO_VENTANA  # me toca justo después
                return
            self.responder(p.ACUSE, ident, b"\x00")
            ruta = os.path.join(CARPETA, "fotos", f"llegando_{rx['seq']}_{int(time.time())}.jpg")
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            with open(ruta, "wb") as f:
                f.write(rx["datos"])
            self.ultima_foto_completa = foto
            if rx["seq"]:
                self.completas.add(rx["seq"])
            self.rx = None
            self.eventos.put(("foto_de_ella", {"seq": rx["seq"], "ts": rx["ts"], "ruta": ruta, "rssi": rssi}))
        elif foto == self.ultima_foto_completa:
            self.responder(p.ACUSE, ident, b"\x00")
        else:
            self.responder(p.FALTAN, ident, struct.pack("<H", foto))  # no la conozco: que empiece de nuevo

    # ------------------------------------------------------------------ trabajos
    def nuevo_seq(self):
        self.seq = (self.seq % 0xFFFF) + 1
        return self.seq

    def encolar(self, **trabajo):
        self.trabajos.put(trabajo)

    def hacer_texto(self, t):
        self.enviar_textos([t])

    def hacer_hora(self, t):
        """Su reloj igual al del PC (también lo pone cada latido; esto es para hacerlo ya)."""
        for _ in range(2):
            self.esperar_turno()
            self.mandar(p.HORA, random.randint(1, 0xFFFF), struct.pack("<I", int(time.time())))
            self.escuchar(2.5)
        self.eventos.put(("hora", time.time()))

    def hacer_grupo(self, t):
        self.enviar_textos(t["lista"])

    def enviar_textos(self, lista):
        """Uno = M. Varios = un G con todos (un solo acuse): una ráfaga de 10 mensajes sale de una vez en vez
        de uno por uno, cada uno con su acuse y su pausa."""
        anterior = self.trabajo
        self.trabajo = lista[0]
        try:
            ident = random.randint(1, 0xFFFF)
            items = [(t.get("seq") or self.nuevo_seq(), int(t.get("ts") or time.time()), t["texto"].encode("utf-8"))
                     for t in lista]
            if len(items) == 1:
                tipo, cuerpo = p.MENSAJE, struct.pack("<HI", items[0][0], items[0][1]) + items[0][2]
            else:
                tipo, cuerpo = p.GRUPO, p.armar_grupo(items)
            for n in range(1, INTENTOS_TEXTO + 1):
                if n > 1:
                    for t in lista:
                        self.eventos.put(("estado", t.get("ref"), f"reintentando ({n}/{INTENTOS_TEXTO})…", None))
                if not self.fase_foto:  # en mi propia foto el turno es mío
                    self.esperar_turno(maximo=600, texto=True)
                self.mandar(tipo, ident, cuerpo)
                r = self.esperar((p.ACUSE,), ident, self.espera_respuesta(len(cuerpo) + 5))
                if not r and n < INTENTOS_TEXTO:
                    self.escuchar(self.retroceso(n, len(cuerpo) + 5))
                if r:
                    alla = struct.unpack("<b", r[1][:1])[0] if r[1] else None
                    for t in lista:
                        self.eventos.put(("resultado", t.get("ref"), True, alla))
                    return
            for t in lista:
                self.eventos.put(("resultado", t.get("ref"), False, None))
        finally:
            self.trabajo = anterior

    @staticmethod
    def agrupar(textos):
        """Reparte los textos en grupos que quepan en un paquete (en orden)."""
        grupos, actual, usado = [], [], 1
        for t in textos:
            b = 7 + len(t["texto"].encode("utf-8"))
            if actual and (usado + b > p.MAX_CUERPO or len(actual) >= p.MAX_GRUPO):
                grupos.append(actual)
                actual, usado = [], 1
            actual.append(t)
            usado += b
        if actual:
            grupos.append(actual)
        return grupos

    def intercalar_textos(self):
        for g in self.agrupar(self.sacar_textos()):  # mis mensajes no esperan a que termine mi foto
            self.enviar_textos(g)

    def ventana_para_ella(self):
        """Ella dijo "tengo un mensaje": se le espera y, si habla, se alarga por si tiene más."""
        for _ in range(5):
            inicio = time.time()
            self.escuchar(self.aire(11 + p.MAX_TEXTO) + MARGEN_VENTANA)
            if self.ultimo_rx < inicio:
                return

    def hacer_foto(self, t):
        self.trabajo = t
        try:
            ok = self._foto(t)
        finally:
            self.fase_foto = None
            self.trabajo = None
            if self.turbo and self.turbo_emisor:
                self.volver_turbo()
        if ok is None:  # ella empezó a mandarme una foto justo a la vez: la mía va después
            self.ceder = False
            threading.Timer(CEDER_ESPERA, lambda: self.encolar(**t)).start()
            return
        self.eventos.put(("resultado", t["ref"], ok, None))

    def _foto(self, t):
        datos = t["datos"]
        seq = t.get("seq") or self.nuevo_seq()
        ts = int(t.get("ts") or time.time())
        trozos = -(-len(datos) // p.TROZO)
        faltan = set(range(trozos))
        if trozos >= TROZOS_MIN_TURBO and self.nivel_posible() > 0:
            self.pedir_turbo(self.nivel_posible())
        rondas = 0
        while rondas <= 12:
            # 1) avisar que viene (o que sigue) una foto
            foto, ident = random.randint(1, 0xFFFF), random.randint(1, 0xFFFF)
            cabecera = struct.pack("<HIHHI", foto, len(datos), trozos, seq, ts)
            self.fase_foto = "inicio"
            r = None
            for intento in range(1, 6):
                self.esperar_turno()
                if self.ceder:
                    return None
                self.mandar(p.FOTO, ident, cabecera)
                r = self.esperar((p.ACUSE,), ident, self.espera_respuesta(19))
                if self.ceder:
                    return None
                if r:
                    break
                self.escuchar(self.retroceso(intento, 19))
            if not r:
                return False
            preguntar_primero = len(r[1]) >= 2 and r[1][1] == 1  # ya tiene una parte: que diga qué falta
            sin_respuesta = 0
            while True:
                # 2) los trozos que falten, preguntando cada 4 si va bien (y si quiere hablar)
                if not preguntar_primero:
                    self.fase_foto = "trozos"
                    rondas += 1
                    pendientes = sorted(faltan)
                    desde_consulta = 0
                    for k, i in enumerate(pendientes):
                        self.intercalar_textos()
                        d = datos[i * p.TROZO:(i + 1) * p.TROZO]
                        self.mandar(p.TROZO_FOTO, random.randint(1, 0xFFFF), struct.pack("<HH", foto, i) + d)
                        self.eventos.put(("progreso", t["ref"], 100 * (trozos - len(faltan) + k + 1) // trozos))
                        desde_consulta += 1
                        if desde_consulta < TROZOS_POR_CONSULTA or k == len(pendientes) - 1:
                            continue
                        desde_consulta = 0
                        self.mandar(p.PREGUNTA_FOTO, ident, struct.pack("<H", foto))
                        rr = self.esperar((p.ACUSE, p.FALTAN), ident, self.espera_respuesta(8 + trozos // 8))
                        if rr is None:
                            sin_respuesta += 1
                            if self.turbo and self.turbo_emisor and sin_respuesta >= 2:  # bajar y seguir
                                self.cambiar_nivel(0)
                                self.turbo_emisor = False
                                self.escuchar(TURBO_SILENCIO * 1.05)  # que ella también baje
                                break
                            if not self.turbo and sin_respuesta >= 3:  # se fue de alcance
                                break
                            continue
                        sin_respuesta = 0
                        if rr[0] == p.ACUSE:
                            return True
                        mapa = rr[1][2:]
                        nb = (trozos + 7) // 8
                        if len(mapa) >= nb:
                            for j in pendientes[:k + 1]:
                                if (mapa[j // 8] >> (j % 8)) & 1:
                                    faltan.add(j)
                                else:
                                    faltan.discard(j)
                        if len(mapa) > nb and mapa[nb]:
                            self.ventana_para_ella()
                    else:
                        faltan = {j for j in faltan if j not in pendientes}  # mandados: se confirma al preguntar
                preguntar_primero = False
                # 3) ¿qué te falta?
                self.fase_foto = "pregunta"
                rr = None
                for intento in range(1, 7):
                    self.esperar_turno()
                    self.mandar(p.PREGUNTA_FOTO, ident, struct.pack("<H", foto))
                    rr = self.esperar((p.ACUSE, p.FALTAN), ident, self.espera_respuesta(8 + trozos // 8))
                    if rr:
                        break
                    self.escuchar(self.retroceso(intento, 7))
                if not rr:
                    return False  # se fue: se reintenta cuando vuelva (y sigue desde donde quedó)
                if rr[0] == p.ACUSE:
                    return True
                mapa = rr[1][2:]
                if not mapa:  # no la conoce (se reinició): de nuevo desde el aviso
                    faltan = set(range(trozos))
                    break
                nb = (trozos + 7) // 8
                faltan = {j for j in range(trozos) if j // 8 < len(mapa) and (mapa[j // 8] >> (j % 8)) & 1}
                if len(mapa) > nb and mapa[nb]:
                    self.ventana_para_ella()
                if not faltan:
                    return True
                if rondas > 12:
                    return False
        return False

    def hacer_recibos(self, t):
        ident = random.randint(1, 0xFFFF)
        seqs = t["seqs"][:100]
        cuerpo = b"".join(struct.pack("<H", s) for s in seqs)
        for intento in range(1, 4):
            self.esperar_turno()
            self.mandar(p.LEIDO, ident, cuerpo)
            if self.esperar((p.ACUSE,), ident, self.espera_respuesta(5 + len(cuerpo))):
                self.eventos.put(("recibos_ok", seqs))
                return
            self.escuchar(self.retroceso(intento, 5 + len(cuerpo)))
        self.eventos.put(("recibos_fallidos", seqs))

    def hacer_comando(self, t):
        ident = random.randint(1, 0xFFFF)
        crudo = t["texto"].encode("utf-8")
        for intento in range(1, 4):
            self.esperar_turno()
            self.mandar(p.COMANDO, ident, crudo)
            r = self.esperar((p.RESPUESTA,), ident, self.aire(len(crudo) + 5) + self.aire(200) + 6)
            if r:
                self.eventos.put(("respuesta", r[1].decode("utf-8", "replace")))
                return
            self.escuchar(self.retroceso(intento, len(crudo) + 5))
        self.eventos.put(("respuesta", "el aparato no contestó"))

    def hacer_ping(self, t):
        ident = random.randint(1, 0xFFFF)
        t0 = time.time()
        self.esperar_turno()
        self.mandar(p.PING, ident)
        r = self.esperar((p.PONG,), ident, 5)
        if r:
            alla = struct.unpack("<b", r[1][:1])[0] if r[1] else None
            self.eventos.put(("ping", (r[2], alla, time.time() - t0)))
        else:
            self.eventos.put(("ping", None))

    def hacer_canal(self, t):
        """Cambia el canal en los DOS aparatos. Si no se encuentran en el nuevo, los dos vuelven solos."""
        # el canal es FIJO: un aparato para encontrarse en emergencias no puede arriesgarse a quedar en un
        # canal distinto del otro (lo de abajo ya no se usa)
        self.eventos.put(("canal", False, f"el canal es fijo ({p.CANAL_BASE}): así nunca quedan en canales distintos"))
        return
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
            r = self.esperar((p.RESPUESTA,), ident, self.aire(15) + self.aire(90) + 3)
            if r:
                resp = r[1].decode("utf-8", "replace")
                break
        if not resp or not resp.startswith("canal: paso"):
            self.eventos.put(("canal", False, f"el aparato no aceptó el cambio ({resp or 'no contestó'}); no cambié nada"))
            return
        self.eventos.put(("respuesta", resp))
        time.sleep(3.5)
        self.at_local([f"AT+CHANNEL{nuevo}"])
        self.canal = nuevo
        self.anotar(f"## canal {viejo} -> {nuevo} (a prueba)")
        for _ in range(8):  # el primer ping que llegue confirma (y graba) el canal en el aparato
            pid = random.randint(1, 0xFFFF)
            self.mandar(p.PING, pid)
            if self.esperar((p.PONG,), pid, 5):
                self.anotar(f"## canal {nuevo} confirmado")
                self.eventos.put(("canal", True, f"listo: los dos en el canal {nuevo} ({850.15 + int(nuevo, 16):.2f} MHz)"))
                return
            self.escuchar(2)
        self.at_local([f"AT+CHANNEL{viejo}"])
        self.canal = viejo
        self.anotar(f"## sin contacto en el canal {nuevo}: vuelvo al {viejo}")
        self.eventos.put(("canal", False, f"sin contacto en el canal {nuevo}: volví al {viejo}; el aparato vuelve solo en 2 min"))

    def hacer_at(self, t):
        """Comandos AT al módulo propio (diagnóstico y pruebas)."""
        salida = self.at_local(t["comandos"]) or []
        for c, r in zip(t["comandos"], salida):
            m = re.fullmatch(r"AT\+CHANNEL([0-9A-Fa-f]{2})", c)
            if m and "OK" in r:
                self.canal = m.group(1).upper()
        self.eventos.put(("at", salida))

    def siguiente_trabajo(self):
        """Un texto puede salir en la ventana de su foto; lo demás espera el canal libre. Los textos que estén
        esperando salen juntos (un G)."""
        with self.trabajos.mutex:
            q = self.trabajos.queue
            if not q:
                return None
            if self.turno_libre():
                t = q.popleft()
            else:
                t = next((x for x in q if x["clase"] == "texto"), None)
                if t is None:
                    return None
                q.remove(t)
            if t["clase"] != "texto":
                return t
            textos = [t] + [x for x in q if x["clase"] == "texto"]
            grupo = self.agrupar(textos)[0]
            for x in grupo[1:]:
                q.remove(x)
            return t if len(grupo) == 1 else {"clase": "grupo", "lista": grupo}

    def mantenimiento(self):
        ahora = time.time()
        self.revisar_presencia()
        rx = self.rx
        if rx is not None:
            if not rx["en_pausa"] and ahora - rx["t"] > FOTO_EN_PAUSA:
                rx["en_pausa"] = True
                self.eventos.put(("foto_pausa", {"seq": rx["seq"], "pct": self.pct_rx(), "perdida": False}))
            if ahora - rx["creada"] > FOTO_GUARDADA:
                self.rx = None
        if self.turbo and (ahora - self.ultimo_rx > TURBO_SILENCIO or ahora - self.turbo_desde > TURBO_MAXIMO):
            self.cambiar_nivel(0)  # silencio o demasiado tiempo: de vuelta al máximo alcance
            self.turbo_emisor = False
        if ahora >= self.lat["proximo"] and not self.pausada and self.turno_libre():
            self.mandar_latido()
        if not self.lat["cerca"] and ahora >= self.proximo_chequeo and self.trabajo is None:
            self.proximo_chequeo = ahora + CHEQUEO_PERDIDOS
            self.revisar_configuracion("perdidos")

    def revisar_canal_base(self, ahora):
        """Canal de emergencia: con un canal que no es el de fábrica, 30 min sin oír nada = volver al de
        fábrica. El aparato hace lo mismo, así que nunca pueden quedar para siempre en canales distintos."""
        if not self.canal or self.canal == p.CANAL_BASE or ahora < self.proximo_intento_base:
            return
        sin = ahora - max(self.ultimo_rx, self.inicio)
        if sin <= VOLVER_CANAL_BASE:
            return
        self.proximo_intento_base = ahora + VOLVER_CANAL_BASE / 30
        r = self.at_local([f"AT+CHANNEL{p.CANAL_BASE}"])
        if r and "OK" in r[0]:
            viejo, self.canal = self.canal, p.CANAL_BASE
            self.anotar(f"## {sin / 60:.0f} min sin contacto en el canal {viejo}: vuelvo al de fábrica {p.CANAL_BASE}")
            self.eventos.put(("canal", True, f"{sin / 60:.0f} min sin contacto en el canal {viejo}: volví al canal de "
                                             f"fábrica {p.CANAL_BASE} (el aparato hace lo mismo)"))

    def run(self):
        self.conectar()
        while True:
            self.mantenimiento()
            if not self.turno_para_texto():
                self.escuchar(0.02)
                continue
            t = self.siguiente_trabajo()
            if t is None:
                self.escuchar(0.05)
                continue
            getattr(self, "hacer_" + t["clase"])(t)
