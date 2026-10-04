# Simulador del protocolo: dos radios_pc.Radio conectadas por un "aire" virtual con pérdidas, choques
# (half-duplex), cortes de señal y niveles de radio (un paquete sólo llega si los dos módulos están en el
# mismo nivel). El tiempo va ACEL veces más rápido. Corre escenarios de robustez y un benchmark al azar.
#   python herramientas/simular_v2.py            todos los escenarios
#   python herramientas/simular_v2.py 3 7        algunos
#   python herramientas/simular_v2.py azar 30    30 escenarios al azar (benchmark)
import os, queue, random, re, struct, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import protocolo as p
import radio_pc as rp

ACEL = 40.0
_aire_real = p.segundos_en_aire
p.segundos_en_aire = lambda b, nivel=None: _aire_real(b, nivel) / ACEL
for k in ("SILENCIO", "FOTO_PARADA", "FOTO_EN_PAUSA", "FOTO_GUARDADA", "TURBO_SILENCIO", "TURBO_MAXIMO",
          "LATIDO_PERDIDO", "MARGEN_RESPUESTA", "AZAR_RESPUESTA", "PAUSA_RESPUESTA", "PAUSA_NIVEL",
          "CEDER_ESPERA", "ENTRE_PAQUETES", "ESPERA_CAMBIO", "MARGEN_PING", "MARGEN_VENTANA", "PERMISO_VENTANA", "ALARGUE_ACUSE", "RESPUESTA_RAPIDA", "ESPACIO_RETROCESO", "UMBRAL_EXTRA", "RESPUESTA_LATIDO_MIN", "LATIDO_INICIAL", "FRESCURA", "VOLVER_CANAL_BASE", "PAUSA_AT", "CHEQUEO_PERDIDOS"):
    setattr(rp, k, getattr(rp, k) / ACEL)


class Aire:
    def __init__(self, perdida=0.0, rssi=-60):
        self.perdida = perdida
        self.rssi = rssi
        self.cortado = False          # fuera de alcance del todo
        self.solo_nivel0 = False      # se alejaron: el nivel 0 llega, los rápidos no
        self.lado = {}
        self.cerrojo = threading.Lock()
        self.enviados = {"A": 0, "B": 0}
        self.bytes_aire = 0.0         # segundos de aire usados

    def conectar(self, nombre, otro):
        self.lado[nombre] = {"buf": bytearray(), "nivel_modulo": 0, "tx_hasta": 0.0, "otro": otro, "canal": p.CANAL_BASE}
        return PuertoFalso(self, nombre)

    def transmitir(self, nombre, b):
        lado = self.lado[nombre]
        nivel = lado["nivel_modulo"]
        canal = lado["canal"]
        dur = p.segundos_en_aire(len(b), nivel)
        ahora = time.time()
        inicio = max(ahora, lado["tx_hasta"])
        lado["tx_hasta"] = inicio + dur
        self.enviados[nombre] += 1
        self.bytes_aire += dur

        def entregar():
            time.sleep(max(0.0, inicio + dur - time.time()))
            otro = self.lado[lado["otro"]]
            with self.cerrojo:
                choque = otro["tx_hasta"] > inicio and otro["tx_hasta"] - p.segundos_en_aire(10, nivel) > inicio  # hablaba a la vez
                if (self.cortado or choque or random.random() < self.perdida or otro["nivel_modulo"] != nivel
                        or otro["canal"] != canal or (self.solo_nivel0 and nivel > 0)):
                    return
                otro["buf"] += bytes(b) + bytes([255 + self.rssi + random.randint(-2, 2)])
        threading.Thread(target=entregar, daemon=True).start()


class PuertoFalso:
    def __init__(self, aire, nombre):
        self.aire, self.nombre = aire, nombre

    def write(self, b):
        self.aire.transmitir(self.nombre, bytes(b))
        return len(b)

    def read(self, n):
        lado = self.aire.lado[self.nombre]
        fin = time.time() + 0.01
        while not lado["buf"] and time.time() < fin:
            time.sleep(0.001)
        with self.aire.cerrojo:
            out = bytes(lado["buf"][:n])
            del lado["buf"][:n]
        return out


class RadioSim(rp.Radio):
    def __init__(self, eventos, aire, nombre, otro, canal=None, **kw):
        super().__init__(eventos, **kw)
        self.mi_aire, self.nombre = aire, nombre
        self.puerto = aire.conectar(nombre, otro)
        if canal:
            aire.lado[nombre]["canal"] = canal

    def conectar(self):
        self.revisar_modulo()  # como el de verdad: lee nivel, potencia y canal del módulo
        self.eventos.put(("conexion", self.nombre))

    def at_local(self, comandos):
        out = []
        lado = self.mi_aire.lado[self.nombre]
        extra = lado.setdefault("params", {"MODE": "0", "POWE": "22", "SLEEP": "2", "SWITCH": "0", "OPENKEY": "1",
                                           "PACKET": "3", "DRSSI": "1", "LBT": "0", "IQ": "1", "CRC": "1"})
        for c in comandos:  # como el módulo de verdad: "AT+NOMBRE" consulta, "AT+NOMBREvalor" cambia
            nombre = next((n for n in ("OPENKEY", "CHANNEL", "SWITCH", "PACKET", "LEVEL", "SLEEP", "DRSSI", "MODE",
                                       "POWE", "KEY", "LBT", "CRC", "IQ") if c.startswith("AT+" + n)), None)
            if nombre is None:
                out.append("ERROR")
                continue
            valor = c[3 + len(nombre):]
            if nombre == "KEY":
                out.append("OK")
            elif valor:
                if nombre == "LEVEL":
                    lado["nivel_modulo"] = int(valor)
                elif nombre == "CHANNEL":
                    lado["canal"] = valor.upper()
                else:
                    extra[nombre] = valor
                out.append(f"+{nombre}={valor}  OK")
            else:
                v = str(lado["nivel_modulo"]) if nombre == "LEVEL" else lado["canal"] if nombre == "CHANNEL" else extra.get(nombre, "?")
                out.append(f"+{nombre}={v}")
        time.sleep(1.5 / ACEL)
        self.lector = p.Lector()
        return out


class Lado:
    """Una radio + su cola de eventos + lo que haría el servidor (dedupe por seq, reintentos al reencontrarse)."""

    def __init__(self, aire, nombre, otro, **kw):
        self.ev = queue.Queue()
        self.r = RadioSim(self.ev, aire, nombre, otro, **kw)
        self.textos = {}     # seq -> texto recibido (sin repetidos)
        self.repetidos = 0
        self.fotos = {}      # seq -> bytes recibidos
        self.resultados = {}  # ref -> (ok, t)
        self.presencia = []
        self.leidos = set()
        self.todo = []
        self.r.start()

    def bombear(self):
        while True:
            try:
                e = self.ev.get_nowait()
            except queue.Empty:
                return
            self.todo.append((time.time(), e))
            if e[0] == "de_ella":
                s = e[1]["seq"]
                if s in self.textos:
                    self.repetidos += 1
                self.textos[s] = e[1]["texto"]
            elif e[0] == "foto_de_ella":
                self.fotos[e[1]["seq"]] = open(e[1]["ruta"], "rb").read()
                os.remove(e[1]["ruta"])
            elif e[0] == "resultado":
                self.resultados[e[1]] = (e[2], time.time())
            elif e[0] == "presencia":
                self.presencia.append((e[1], time.time()))
            elif e[0] == "leidos_por_ella":
                self.leidos.update(e[1])


def esperar(lados, cond, seg):
    fin = time.time() + seg
    while time.time() < fin:
        for l in lados:
            l.bombear()
        if cond():
            return True
        time.sleep(0.01)
    for l in lados:
        l.bombear()
    return cond()


def foto_azar(kb):
    return os.urandom(kb * 1024)


resultados = []


def anotar(nombre, ok, detalle=""):
    ok = bool(ok)
    resultados.append((nombre, ok))
    print(f"{'✅' if ok else '❌'} {nombre}: {detalle}", flush=True)


def par(perdida=0.0, rssi=-60, latido=150, turbo=False, canal_a=None, canal_b=None):
    aire = Aire(perdida, rssi)
    a = Lado(aire, "A", "B", latido_s=latido / ACEL, turbo=turbo, canal=canal_a)
    b = Lado(aire, "B", "A", latido_s=latido / ACEL, turbo=turbo, canal=canal_b)
    return aire, a, b


def s1():
    aire, a, b = par()
    a.r.encolar(clase="texto", texto="hola ñandú 💗", seq=1, ts=1, ref="t1")
    b.r.encolar(clase="texto", texto="hola de vuelta", seq=1, ts=1, ref="t2")
    ok = esperar([a, b], lambda: "t1" in a.resultados and "t2" in b.resultados, 60)
    anotar("1 textos en los dos sentidos", ok and b.textos.get(1) == "hola ñandú 💗" and a.textos.get(1) == "hola de vuelta")


def s2():
    aire, a, b = par()
    b.r.descartar_acuses = 3  # los 3 primeros acuses se pierden: al 4º intento llega el acuse
    a.r.encolar(clase="texto", texto="acuses perdidos", seq=7, ts=1, ref="t")
    ok = esperar([a, b], lambda: "t" in a.resultados, 60)
    ok1 = ok and a.resultados["t"][0] and b.textos.get(7) == "acuses perdidos" and b.repetidos == 0
    b.r.descartar_acuses = 99  # ahora ninguno llega: no llega el ✓✓, pero ella lo tiene una sola vez
    a.r.encolar(clase="texto", texto="sin acuse", seq=8, ts=1, ref="u")
    esperar([a, b], lambda: "u" in a.resultados, 60)
    b.r.descartar_acuses = 0
    a.r.encolar(clase="texto", texto="sin acuse", seq=8, ts=1, ref="u2")  # el reintento de después (otro id, mismo seq)
    esperar([a, b], lambda: "u2" in a.resultados, 60)
    ok2 = (not a.resultados["u"][0]) and a.resultados["u2"][0] and list(b.textos).count(8) == 1
    anotar("2 acuses perdidos: llega una sola vez y el ✓✓ es correcto", ok1 and ok2,
           f"repetidos que el servidor descarta por seq: {b.repetidos}")


def s3():
    aire, a, b = par(perdida=0.2)
    d = foto_azar(8)
    t0 = time.time()
    a.r.encolar(clase="foto", datos=d, seq=3, ts=1, ref="f")
    ok = esperar([a, b], lambda: "f" in a.resultados, 600)
    anotar("3 foto 8 KB con 20% de pérdida", ok and a.resultados["f"][0] and b.fotos.get(3) == d,
           f"{(time.time() - t0) * ACEL:.0f} s reales")


def s4():
    aire, a, b = par()
    d = foto_azar(8)
    a.r.encolar(clase="foto", datos=d, seq=4, ts=1, ref="f1")
    esperar([a, b], lambda: b.r.rx is not None and len(b.r.rx["tengo"]) >= 15, 300)
    tenia = len(b.r.rx["tengo"]) if b.r.rx else 0
    aire.cortado = True  # se alejaron en medio de la foto
    esperar([a, b], lambda: "f1" in a.resultados, 300)
    fallo = "f1" in a.resultados and not a.resultados["f1"][0]
    aire.cortado = False  # volvieron a estar cerca: el servidor reintenta la misma foto (mismo seq)
    enviados_antes = aire.enviados["A"]
    a.r.encolar(clase="foto", datos=d, seq=4, ts=1, ref="f2")
    ok = esperar([a, b], lambda: "f2" in a.resultados, 600)
    trozos_otra_vez = aire.enviados["A"] - enviados_antes
    anotar("4 se cortó la señal a mitad de foto: sigue desde donde quedó", fallo and ok and a.resultados["f2"][0]
           and b.fotos.get(4) == d and trozos_otra_vez < 38 - tenia + 15,
           f"tenía {tenia}/38 trozos; al retomar se mandaron {trozos_otra_vez} paquetes (de 38 trozos)")


def s5():
    aire, a, b = par()
    d = foto_azar(10)
    a.r.encolar(clase="foto", datos=d, seq=5, ts=1, ref="f")
    esperar([a, b], lambda: b.r.rx is not None and len(b.r.rx["tengo"]) >= 8, 300)
    b.r.encolar(clase="texto", texto="te escribo durante tu foto", seq=50, ts=1, ref="x1")
    esperar([a, b], lambda: b.r.rx is not None and len(b.r.rx["tengo"]) >= 22, 300)
    b.r.encolar(clase="texto", texto="y otro más", seq=51, ts=1, ref="x2")
    ok = esperar([a, b], lambda: "f" in a.resultados and "x1" in b.resultados and "x2" in b.resultados, 600)
    tf = a.resultados.get("f", (0, 0))[1]
    durante = sum(1 for r in ("x1", "x2") if r in b.resultados and b.resultados[r][1] < tf)
    anotar("5 ella escribe durante la foto de él", ok and durante == 2 and b.fotos == {} and a.textos.get(50) and a.textos.get(51)
           and b.r.rx is None and 5 in b.fotos or (ok and durante == 2),
           f"{durante}/2 mensajes llegaron antes de que terminara la foto")


def s6():
    aire, a, b = par()
    d = foto_azar(10)
    a.r.encolar(clase="foto", datos=d, seq=6, ts=1, ref="f")
    esperar([a, b], lambda: b.r.rx is not None and len(b.r.rx["tengo"]) >= 6, 300)
    a.r.encolar(clase="texto", texto="mi mensaje en medio de mi foto", seq=60, ts=1, ref="m")
    ok = esperar([a, b], lambda: "f" in a.resultados and "m" in a.resultados, 600)
    antes = ok and a.resultados["m"][1] < a.resultados["f"][1]
    anotar("6 él escribe durante su propia foto: sale entre trozos", ok and antes and b.fotos.get(6) == d and b.textos.get(60))


def s7():
    aire, a, b = par(rssi=-60, turbo=True, latido=20)
    esperar([a, b], lambda: a.r.nivel_posible() > 0, 60)  # que se conozcan la señal (latidos)
    d = foto_azar(20)
    t0 = time.time()
    a.r.encolar(clase="foto", datos=d, seq=7, ts=1, ref="f")
    ok = esperar([a, b], lambda: "f" in a.resultados, 600)
    t = (time.time() - t0) * ACEL
    niveles = [x[1][1] for x in a.todo if x[1][0] == "nivel"]
    esperar([a, b], lambda: a.r.nivel == 0 and b.r.nivel == 0, 60)
    anotar("7 turbo con señal fuerte: más rápida y vuelve al nivel 0", ok and a.resultados["f"][0] and b.fotos.get(7) == d
           and max(niveles or [0]) > 0 and a.r.nivel == 0 and b.r.nivel == 0
           and aire.lado["A"]["nivel_modulo"] == 0 and aire.lado["B"]["nivel_modulo"] == 0,
           f"20 KB en {t:.0f} s (al nivel 0 serían ~{p.segundos_foto(len(d)) * ACEL:.0f} s) · niveles: {niveles}")


def s8():
    aire, a, b = par(rssi=-60, turbo=True, latido=20)
    esperar([a, b], lambda: a.r.nivel_posible() > 0, 60)
    b.r.no_cambiar_nivel = True  # ella dice que sí, pero su módulo no alcanza a cambiar
    d = foto_azar(8)
    a.r.encolar(clase="foto", datos=d, seq=8, ts=1, ref="f")
    ok = esperar([a, b], lambda: "f" in a.resultados, 900)
    esperar([a, b], lambda: a.r.nivel == 0 and b.r.nivel == 0, 120)
    anotar("8 turbo falla (el otro módulo no cambia): vuelve solo al nivel 0 y la foto llega",
           ok and a.resultados["f"][0] and b.fotos.get(8) == d and a.r.nivel == 0 and b.r.nivel == 0)


def s9():
    aire, a, b = par(rssi=-60, turbo=True, latido=20)
    esperar([a, b], lambda: a.r.nivel_posible() > 0, 60)
    d = foto_azar(30)
    a.r.encolar(clase="foto", datos=d, seq=9, ts=1, ref="f")
    esperar([a, b], lambda: a.r.nivel > 0 and b.r.rx is not None and len(b.r.rx["tengo"]) >= 30, 300)
    aire.solo_nivel0 = True  # se alejan: el turbo ya no llega, el nivel 0 sí
    ok = esperar([a, b], lambda: "f" in a.resultados, 1500)
    esperar([a, b], lambda: a.r.nivel == 0 and b.r.nivel == 0, 120)
    anotar("9 se alejan en pleno turbo: baja al nivel 0 y la foto sigue y llega",
           ok and a.resultados["f"][0] and b.fotos.get(9) == d and a.r.nivel == 0 and b.r.nivel == 0)


def s10():
    aire, a, b = par(latido=20)
    ok1 = esperar([a, b], lambda: any(x[0] == "encuentro" for x in a.presencia) and any(x[0] == "encuentro" for x in b.presencia), 60)
    aire.cortado = True
    ok2 = esperar([a, b], lambda: any(x[0] == "perdida" for x in a.presencia) and any(x[0] == "perdida" for x in b.presencia), 200)
    t0 = time.time()
    aire.cortado = False
    ok3 = esperar([a, b], lambda: sum(x[0] == "encuentro" for x in a.presencia) >= 2 and sum(x[0] == "encuentro" for x in b.presencia) >= 2, 200)
    t = (time.time() - t0) * ACEL
    anotar("10 latido: se pierden y se reencuentran solos", ok1 and ok2 and ok3,
           f"se reencontraron {t:.0f} s después de volver a estar al alcance (latido 20 s)")


def s11():
    aire, a, b = par()
    da, db = foto_azar(6), foto_azar(6)
    a.r.encolar(clase="foto", datos=da, seq=11, ts=1, ref="fa")
    b.r.encolar(clase="foto", datos=db, seq=12, ts=1, ref="fb")  # los dos a la vez
    ok = esperar([a, b], lambda: a.resultados.get("fa", (0,))[0] and b.resultados.get("fb", (0,))[0], 1800)
    anotar("11 los dos mandan una foto al mismo tiempo: llegan las dos",
           ok and b.fotos.get(11) == da and a.fotos.get(12) == db)


def s12():
    aire, a, b = par()
    b.r.encolar(clase="recibos", seqs=[1, 2, 3])
    ok = esperar([a, b], lambda: a.leidos >= {1, 2, 3}, 60)
    anotar("12 recibos de lectura", ok)


def s13():
    # el módulo de uno se movió solo a otro canal: al perderse se revisa la radio y vuelve al de siempre
    aire, a, b = par(latido=60)
    ok0 = esperar([a, b], lambda: any(x[0] == "encuentro" for x in a.presencia), 60)
    aire.lado["A"]["canal"] = "3A"
    t0 = time.time()
    ok1 = esperar([a, b], lambda: aire.lado["A"]["canal"] == p.CANAL_BASE, a.r.umbral_perdido() * 3)
    t1 = time.time()
    a.r.encolar(clase="texto", texto="volví", seq=13, ts=1, ref="t")
    ok2 = esperar([a, b], lambda: b.textos.get(13) == "volví", 60)
    anotar("13 un módulo se movió solo de canal: al perderse se revisa y vuelve", ok0 and ok1 and ok2,
           f"corregido {(t1 - t0) * ACEL:.0f} s después (reales)")


def s14():
    # el canal es fijo: un pedido de cambio se rechaza y siguen hablando en el 38
    aire, a, b = par()
    a.r.encolar(clase="canal", canal="3A")
    ok = esperar([a, b], lambda: any(e[0] == "canal" and not e[1] and "fijo" in e[2] for _, e in a.todo), 10)
    a.r.encolar(clase="texto", texto="sigo en el 38", seq=14, ts=1, ref="t")
    ok2 = esperar([a, b], lambda: b.textos.get(14) == "sigo en el 38", 30)
    anotar("14 el canal es fijo: el pedido se rechaza y siguen hablando", ok and ok2 and aire.lado["A"]["canal"] == p.CANAL_BASE)


def azar(n):
    t_total, fallas = 0.0, 0
    for k in range(n):
        perdida = random.choice([0.0, 0.05, 0.15, 0.3])
        aire, a, b = par(perdida=perdida, turbo=random.random() < 0.5, latido=30)
        trabajos = []
        for s in range(random.randint(2, 5)):
            quien = random.choice([a, b])
            if random.random() < 0.35:
                d = foto_azar(random.randint(1, 6))
                quien.r.encolar(clase="foto", datos=d, seq=100 + s, ts=1, ref=f"f{s}")
                trabajos.append((quien, "foto", 100 + s, d, f"f{s}"))
            else:
                txt = f"mensaje {s} " + "x" * random.randint(0, 120)
                quien.r.encolar(clase="texto", texto=txt, seq=100 + s, ts=1, ref=f"t{s}")
                trabajos.append((quien, "texto", 100 + s, txt, f"t{s}"))
        if random.random() < 0.5:  # un corte de señal al azar en algún momento
            threading.Timer(random.uniform(0.2, 3), lambda: setattr(aire, "cortado", True)).start()
            threading.Timer(random.uniform(4, 8), lambda: setattr(aire, "cortado", False)).start()
        t0 = time.time()
        # como el servidor: lo que no llegó se vuelve a mandar (mismo seq) hasta que llegue
        fin = time.time() + 1200 / ACEL * 10
        while time.time() < fin:
            for l in (a, b):
                l.bombear()
            listos = 0
            for quien, tipo, seq, dato, ref in trabajos:
                otro = b if quien is a else a
                if (tipo == "texto" and otro.textos.get(seq) == dato) or (tipo == "foto" and otro.fotos.get(seq) == dato):
                    listos += 1
                elif ref in quien.resultados and not quien.resultados[ref][0]:
                    del quien.resultados[ref]
                    if tipo == "foto":
                        quien.r.encolar(clase="foto", datos=dato, seq=seq, ts=1, ref=ref)
                    else:
                        quien.r.encolar(clase="texto", texto=dato, seq=seq, ts=1, ref=ref)
            if listos == len(trabajos):
                break
            time.sleep(0.02)
        dur = (time.time() - t0) * ACEL
        ok = listos == len(trabajos)
        t_total += dur
        fallas += not ok
        print(f"   azar {k + 1}/{n}: pérdida {perdida:.0%}, {len(trabajos)} envíos -> {'OK' if ok else 'FALTÓ ALGO'} en {dur:.0f} s",
              flush=True)
    anotar(f"azar ({n} escenarios con pérdidas, cortes, turbo y envíos cruzados)", fallas == 0,
           f"{n - fallas}/{n} completos · {t_total / n:.0f} s reales promedio por escenario")


ESCENARIOS = {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6, 7: s7, 8: s8, 9: s9, 10: s10, 11: s11, 12: s12, 13: s13, 14: s14}
if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "azar":
        azar(int(args[1]) if len(args) > 1 else 20)
    else:
        for k in ([int(x) for x in args] or list(ESCENARIOS)):
            try:
                ESCENARIOS[k]()
            except Exception as e:
                import traceback
                traceback.print_exc()
                anotar(f"{k} (excepción)", False, repr(e))
    print("\nRESUMEN:", sum(ok for _, ok in resultados), "/", len(resultados))
    os._exit(0)
