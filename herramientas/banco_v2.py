# Banco de pruebas REAL (protocolo v2): el S3 por su consola (COM8) y el PC por su radio (COM9, radio_pc).
# Cada prueba usa la radio de verdad; las fallas se provocan a propósito (pausar = fuera de alcance, etc.).
#   python herramientas/banco_v2.py 1 2 3          algunas
#   python herramientas/banco_v2.py rapidas        las que duran poco
import io, os, queue, random, re, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
import protocolo as p
import radio_pc
from s3 import S3

CAPTURAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "capturas")
os.makedirs(CAPTURAS, exist_ok=True)
REGISTRO = open(os.path.join(CAPTURAS, "banco_v2.txt"), "a", encoding="utf-8")

ev = queue.Queue()
pc = radio_pc.Radio(ev, latido_s=150)
pc.start()
s3 = S3()
_t = time.time()
while pc.puerto is None and time.time() - _t < 15:  # sin el módulo no hay prueba que valga
    time.sleep(0.2)
if pc.puerto is None:
    _e = []
    while not ev.empty():
        _e.append(ev.get())
    print("NO PUEDO USAR EL MÓDULO DEL PC:", [x[2] for x in _e if x[0] == "conexion" and len(x) > 2][-1:],
          "-> ¿está abierta la app Mensajes LoRa? Ciérrala para probar.")
    os._exit(1)
time.sleep(1)
eventos = []  # (t, evento)
resultados = []
seq_pc = [int(time.time()) % 20000 + 1000]


def nuevo_seq():
    seq_pc[0] += 1
    return seq_pc[0]


def bombear(seg=0.0):
    fin = time.time() + seg
    while True:
        try:
            e = ev.get(timeout=0.05)
            eventos.append((time.time(), e))
        except queue.Empty:
            pass
        if time.time() >= fin:
            return


def hay(cond, desde=0.0):
    return next(((t, e) for t, e in eventos if t >= desde and cond(e)), None)


def esperar(cond, seg, desde=0.0):
    fin = time.time() + seg
    while time.time() < fin:
        bombear(0.2)
        x = hay(cond, desde)
        if x:
            return x
    return None


def esperar_s3(n, cond, seg):
    fin = time.time() + seg
    while time.time() < fin:
        bombear(2.0)
        r = s3.registro_n(n)
        if r and cond(r):
            return r
    return None


def anotar(nombre, ok, detalle=""):
    ok = bool(ok)
    resultados.append((nombre, ok, detalle))
    linea = f"{'✅' if ok else '❌'} {nombre}: {detalle}"
    print(linea, flush=True)
    REGISTRO.write(time.strftime("%Y-%m-%d %H:%M ") + linea + "\n")
    REGISTRO.flush()
    if not ok:
        diagnostico(nombre)


def diagnostico(nombre):
    """Si una prueba falla: lo que vieron los dos lados, para entender qué pasó sin repetirla."""
    ruta = os.path.join(CAPTURAS, f"diag_{nombre.split()[0]}_{time.strftime('%H%M%S')}.txt")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(f"{nombre}\n\n--- PC (segundos atrás) ---\n")
        f.write("\n".join(pc.bitacora_texto(300)) + "\n")
        try:
            f.write("\n--- aparato (segundos atrás) ---\n" + "\n".join(s3.bitacora()) + "\n")
            f.write("\n--- registro del aparato ---\n" + "\n".join(s3.registro[-200:]) + "\n")
        except Exception as e:
            f.write(f"\n(no pude leer el aparato: {e!r})\n")
    print("   diagnóstico en", ruta, flush=True)


def foto(texto, kb):
    img = Image.new("RGB", (340, 250), (20, 30, 70))
    d = ImageDraw.Draw(img)
    for k in range(0, 340, 22):
        d.ellipse((k, 40 + k % 90, k + 60, 100 + k % 90), fill=(255, 150 + k // 4, 200))
    d.text((80, 12), texto, fill=(255, 255, 255))
    q = 90
    while True:
        b = io.BytesIO()
        img.save(b, "JPEG", quality=q)
        if b.tell() <= kb * 1024 or q < 8:
            return b.getvalue()
        q -= 4


def presencia_s3():
    r = s3._pedir("presencia")
    import json
    return json.loads(r[4:]) if r and r.startswith("@@J ") else {}


def nivel_s3():
    r = s3.at("AT+LEVEL") or ""
    m = re.search(r"\+LEVEL=(\d)", r)
    return int(m.group(1)) if m else None


def ultimo_de_el():
    for r in reversed(s3.lista(6)):
        if r["de"] == 0:
            return r
    return None


def limpiar():
    s3.comando("olvidar")
    pc.pausada = False
    pc.descartar_acuses = 0
    pc.no_cambiar_nivel = False
    bombear(1)


# ------------------------------------------------------------------------------------------ pruebas
def h1():
    limpiar()
    t0 = time.time()
    seq = nuevo_seq()
    pc.encolar(clase="texto", texto="Hola amor 💗 prueba real, ñandú", seq=seq, ts=int(time.time()), ref="h1")
    r = esperar(lambda e: e[0] == "resultado" and e[1] == "h1", 60, t0)
    u = ultimo_de_el()
    anotar("1 texto PC→aparato", r and r[1][2] and u and u["seq"] == seq and u["texto"].startswith("Hola amor"),
           f"{(r[0] - t0) if r else 0:.1f} s · en el aparato con número {u and u['seq']}")


def h2():
    limpiar()
    t0 = time.time()
    n = s3.ella("te amo mi vida 🥺 prueba real 2")
    r = esperar(lambda e: e[0] == "de_ella" and "prueba real 2" in e[1]["texto"], 60, t0)
    f = esperar_s3(n, lambda x: x["estado"] == 1, 40)
    anotar("2 texto aparato→PC", r and f, f"{(r[0] - t0) if r else 0:.1f} s · número {r and r[1][1]['seq']} · ✓✓ en el aparato: {bool(f)}")


def h3():
    ok = 0
    for _ in range(5):
        t0 = time.time()
        pc.encolar(clase="ping")
        r = esperar(lambda e: e[0] == "ping", 15, t0)
        ok += bool(r and r[1][1])
    anotar("3 pings", ok >= 4, f"{ok}/5")


def h4():
    t0 = time.time()
    pc.encolar(clase="comando", texto="estado")
    r = esperar(lambda e: e[0] == "respuesta", 40, t0)
    anotar("4 comando estado por radio", r and "encendida" in r[1][1], r[1][1] if r else "sin respuesta")


def h5():
    limpiar()
    t0 = time.time()
    n = s3.ella("cruzado desde el aparato (5)")
    pc.encolar(clase="texto", texto="cruzado desde el PC (5)", seq=nuevo_seq(), ts=int(time.time()), ref="h5")
    a = esperar(lambda e: e[0] == "resultado" and e[1] == "h5", 180, t0)
    b = esperar(lambda e: e[0] == "de_ella" and "(5)" in e[1]["texto"], 180, t0)
    f = esperar_s3(n, lambda x: x["estado"] == 1, 120)
    anotar("5 los dos escriben al mismo tiempo", a and a[1][2] and b and f,
           f"PC→ap {(a[0] - t0) if a else 0:.0f} s · ap→PC {(b[0] - t0) if b else 0:.0f} s")


def h6():
    limpiar()
    t0 = time.time()
    seq = nuevo_seq()
    pc.encolar(clase="texto", texto="¿leíste esto? (6)", seq=seq, ts=int(time.time()), ref="h6")
    esperar(lambda e: e[0] == "resultado" and e[1] == "h6", 60, t0)
    s3._pedir("leer")  # ella abre la página
    r = esperar(lambda e: e[0] == "leidos_por_ella" and seq in e[1], 120, t0)
    n = s3.ella("y tú ¿leíste? (6b)")
    m = esperar(lambda e: e[0] == "de_ella" and "(6b)" in e[1]["texto"], 60, t0)
    pc.encolar(clase="recibos", seqs=[m[1][1]["seq"]] if m else [])
    ok2 = esperar_s3(n, lambda x: x["leido"] == 1, 120)
    anotar("6 recibos de lectura en los dos sentidos", r and ok2,
           f"él supo que ella leyó: {bool(r)} ({(r[0] - t0) if r else 0:.0f} s) · ella supo que él leyó: {bool(ok2)}")


def h7():
    limpiar()
    t0 = time.time()
    pc.descartar_acuses = 5  # todos los acuses del primer intento se pierden
    n = s3.ella("acuses perdidos (7)")
    # el aparato reintenta solo apenas falla (puede ser en 2 s: no se exige ver el "no llegó" en medio)
    ok = esperar_s3(n, lambda x: x["estado"] == 1, 300)
    pc.descartar_acuses = 0
    llegadas = sum(1 for t, e in eventos if t > t0 and e[0] == "de_ella" and "(7)" in e[1]["texto"])
    seqs = {e[1]["seq"] for t, e in eventos if t > t0 and e[0] == "de_ella" and "(7)" in e[1]["texto"]}
    anotar("7 acuses perdidos: el aparato reintenta solo y llega con el mismo número", ok and len(seqs) == 1 and llegadas >= 2,
           f"le llegó {llegadas} veces (acuses perdidos + reintento) con {len(seqs)} número: el servidor guarda 1 · ✓✓ al final: {bool(ok)}")


def h8():
    limpiar()
    s3.comando("latido 30")
    pc.latido_s = 30
    t0 = time.time()
    esperar(lambda e: e[0] == "presencia" and e[1] == "encuentro", 90, 0)
    pc.pausada = True  # fuera de alcance
    t1 = time.time()
    ok1 = None
    while time.time() - t1 < 200:
        bombear(5)
        if not presencia_s3().get("cerca", True) and hay(lambda e: e[0] == "presencia" and e[1] == "perdida", t1):
            ok1 = time.time() - t1
            break
    pc.pausada = False  # vuelven a estar cerca
    t2 = time.time()
    r = esperar(lambda e: e[0] == "presencia" and e[1] == "encuentro", 120, t2)
    ok2 = None
    while time.time() - t2 < 120:
        bombear(2)
        if presencia_s3().get("cerca"):
            ok2 = time.time() - t2
            break
    s3.comando("latido 150")
    pc.latido_s = 150
    anotar("8 latido: se pierden y se reencuentran solos", ok1 and r and ok2,
           f"perdida detectada a los {ok1 or 0:.0f} s · reencuentro: PC {(r[0] - t2) if r else 0:.0f} s, aparato {ok2 or 0:.0f} s")


def h9():
    limpiar()
    s3.comando("latido 30")
    pc.latido_s = 30
    pc.pausada = True
    n = s3.ella("escrito sin señal (9)")
    f = esperar_s3(n, lambda x: x["estado"] == 2, 120)
    bombear(20)
    pc.pausada = False
    t2 = time.time()
    r = esperar(lambda e: e[0] == "de_ella" and "(9)" in e[1]["texto"], 240, t2)
    ok = esperar_s3(n, lambda x: x["estado"] == 1, 120)
    s3.comando("latido 150")
    pc.latido_s = 150
    anotar("9 lo escrito sin señal sale solo al reencontrarse", f and r and ok,
           f"llegó {(r[0] - t2) if r else 0:.0f} s después de volver la señal")


def h10():
    limpiar()
    d = foto("prueba 10 PC -> aparato", 5)
    t0 = time.time()
    pc.encolar(clase="foto", datos=d, seq=nuevo_seq(), ts=int(time.time()), ref="h10")
    bombear(30)
    s3.ella("escribo durante tu foto (10a)")
    bombear(45)
    s3.ella("otro durante tu foto (10b)")
    r = esperar(lambda e: e[0] == "resultado" and e[1] == "h10", 900, t0)
    fin = r[0] if r else time.time()
    bombear(20)
    llegadas = [t for t, e in eventos if t > t0 and e[0] == "de_ella" and "(10" in e[1]["texto"]]
    u = ultimo_de_el()
    anotar("10 foto PC→aparato con mensajes de ella en medio", r and r[1][2] and u and u["tipo"] == 1 and u["estado"] == 3,
           f"{len(d)} B en {fin - t0:.0f} s · mensajes {len(llegadas)}/2, durante la foto {sum(t < fin for t in llegadas)}")
    s3.captura(os.path.join(CAPTURAS, "h10_pantalla.png"))


def h11():
    limpiar()
    d = foto("prueba 11 aparato -> PC", 5)
    t0 = time.time()
    n = s3.foto(d)
    bombear(40)
    pc.encolar(clase="texto", texto="escribo mientras me llega tu foto (11)", seq=nuevo_seq(), ts=int(time.time()), ref="h11t")
    r = esperar(lambda e: e[0] == "foto_de_ella", 900, t0)
    t = esperar(lambda e: e[0] == "resultado" and e[1] == "h11t", 60, t0)
    igual = r and open(r[1][1]["ruta"], "rb").read() == d
    f = esperar_s3(n, lambda x: x["estado"] == 1, 60)
    anotar("11 foto aparato→PC con mensaje de él en medio", igual and t and t[1][2] and f,
           f"foto {(r[0] - t0) if r else 0:.0f} s, idéntica={bool(igual)} · texto llegó a los {(t[0] - t0) if t else 0:.0f} s"
           f" {'(durante)' if t and r and t[0] < r[0] else ''}")
    if r:
        os.remove(r[1][1]["ruta"])


def h12():
    limpiar()
    d = foto("prueba 12 corte", 6)
    seq = nuevo_seq()
    t0 = time.time()
    pc.encolar(clase="foto", datos=d, seq=seq, ts=int(time.time()), ref="h12a")
    esperar(lambda e: e[0] == "progreso" and e[1] == "h12a" and e[2] >= 40, 600, t0)
    pc.pausada = True  # se alejan
    r1 = esperar(lambda e: e[0] == "resultado" and e[1] == "h12a", 400, t0)
    u = ultimo_de_el()
    tenia = u["prog"] if u else 0
    pc.pausada = False
    t1 = time.time()
    pc.encolar(clase="foto", datos=d, seq=seq, ts=int(time.time()), ref="h12b")  # como el servidor al reencontrarse
    r2 = esperar(lambda e: e[0] == "resultado" and e[1] == "h12b", 900, t1)
    u2 = ultimo_de_el()
    anotar("12 se corta la señal a mitad de foto (PC→aparato): sigue desde donde quedó",
           r1 and not r1[1][2] and r2 and r2[1][2] and u2 and u2["tipo"] == 1 and u2["estado"] == 3,
           f"el aparato tenía {tenia / 10:.0f}% · lo que faltó tardó {(r2[0] - t1) if r2 else 0:.0f} s (foto entera ~{p.segundos_foto(len(d)):.0f} s)")


def h13():
    limpiar()
    s3.comando("latido 30")
    pc.latido_s = 30
    d = foto("prueba 13 corte", 6)
    t0 = time.time()
    n = s3.foto(d)
    esperar(lambda e: e[0] == "foto_llegando" and e[1]["pct"] >= 40, 600, t0)
    pc.pausada = True
    f = esperar_s3(n, lambda x: x["estado"] == 2, 400)
    tenia = pc.pct_rx()
    bombear(15)
    pc.pausada = False
    t1 = time.time()
    r = esperar(lambda e: e[0] == "foto_de_ella", 900, t1)
    igual = r and open(r[1][1]["ruta"], "rb").read() == d
    ok = esperar_s3(n, lambda x: x["estado"] == 1, 120)
    s3.comando("latido 150")
    pc.latido_s = 150
    anotar("13 se corta la señal a mitad de foto (aparato→PC): sigue sola al reencontrarse", f and igual and ok,
           f"el PC tenía {tenia}% · lo que faltó tardó {(r[0] - t1) if r else 0:.0f} s")
    if r:
        os.remove(r[1][1]["ruta"])


def preparar_turbo():
    """Que los dos conozcan la señal en ambos sentidos (pings) para que el turbo sea posible."""
    for _ in range(3):
        pc.encolar(clase="ping")
        esperar(lambda e: e[0] == "ping", 15, time.time())
    s3.comando("turbo si")
    pc.turbo_permitido = True


def h14():
    limpiar()
    preparar_turbo()
    d = foto("prueba 14 turbo PC -> aparato", 12)
    t0 = time.time()
    pc.encolar(clase="foto", datos=d, seq=nuevo_seq(), ts=int(time.time()), ref="h14")
    r = esperar(lambda e: e[0] == "resultado" and e[1] == "h14", 900, t0)
    niveles = [e[1] for t, e in eventos if t > t0 and e[0] == "nivel"]
    bombear(5)
    u = ultimo_de_el()
    anotar("14 turbo PC→aparato con señal fuerte", r and r[1][2] and u and u["estado"] == 3 and max(niveles or [0]) > 0
           and pc.nivel == 0 and nivel_s3() == 0,
           f"{len(d)} B en {(r[0] - t0) if r else 0:.0f} s (a nivel 0 serían ~{p.segundos_foto(len(d)):.0f} s) · niveles {niveles}")


def h15():
    limpiar()
    preparar_turbo()
    bombear(5)
    d = foto("prueba 15 turbo aparato -> PC", 12)
    t0 = time.time()
    n = s3.foto(d)
    r = esperar(lambda e: e[0] == "foto_de_ella", 900, t0)
    igual = r and open(r[1][1]["ruta"], "rb").read() == d
    niveles = [e[1] for t, e in eventos if t > t0 and e[0] == "nivel"]
    esperar_s3(n, lambda x: x["estado"] == 1, 60)
    bombear(8)
    anotar("15 turbo aparato→PC con señal fuerte", igual and max(niveles or [0]) > 0 and pc.nivel == 0 and nivel_s3() == 0,
           f"{len(d)} B en {(r[0] - t0) if r else 0:.0f} s · niveles {niveles}")
    if r:
        os.remove(r[1][1]["ruta"])


def h16():
    limpiar()
    preparar_turbo()
    pc.no_cambiar_nivel = True  # el PC dice que sí, pero su módulo "no alcanza" a cambiar
    d = foto("prueba 16 turbo falla", 8)
    t0 = time.time()
    n = s3.foto(d)
    r = esperar(lambda e: e[0] == "foto_de_ella", 1200, t0)
    pc.no_cambiar_nivel = False
    igual = r and open(r[1][1]["ruta"], "rb").read() == d
    bombear(25)
    anotar("16 turbo que falla: vuelve solo al nivel 0 y la foto llega", igual and pc.nivel == 0 and nivel_s3() == 0,
           f"{(r[0] - t0) if r else 0:.0f} s")
    if r:
        os.remove(r[1][1]["ruta"])


def h17():
    limpiar()
    preparar_turbo()
    d = foto("prueba 17 se alejan en turbo", 14)
    t0 = time.time()
    pc.encolar(clase="foto", datos=d, seq=nuevo_seq(), ts=int(time.time()), ref="h17")
    esperar(lambda e: e[0] == "progreso" and e[1] == "h17" and e[2] >= 30, 600, t0)
    en_turbo = pc.nivel
    pc.pausada = True  # se alejan en pleno turbo (por 40 s)
    bombear(40)
    pc.pausada = False
    r = esperar(lambda e: e[0] == "resultado" and e[1] == "h17", 1500, t0)
    bombear(25)
    u = ultimo_de_el()
    anotar("17 se alejan en pleno turbo: bajan al nivel 0 y la foto sigue", r and r[1][2] and u and u["estado"] == 3
           and pc.nivel == 0 and nivel_s3() == 0,
           f"iba en nivel {en_turbo} · terminó en {(r[0] - t0) if r else 0:.0f} s")


def h18():
    limpiar()
    preparar_turbo()
    d = foto("prueba 18 reinicio en turbo", 14)
    t0 = time.time()
    pc.encolar(clase="foto", datos=d, seq=nuevo_seq(), ts=int(time.time()), ref="h18")
    esperar(lambda e: e[0] == "progreso" and e[1] == "h18" and e[2] >= 30, 600, t0)
    en_turbo = pc.nivel
    s3.reiniciar()  # el aparato se reinicia en pleno turbo
    r = esperar(lambda e: e[0] == "resultado" and e[1] == "h18", 1500, t0)
    bombear(25)
    u = ultimo_de_el()
    anotar("18 el aparato se reinicia en pleno turbo: todo vuelve a nivel 0 y la foto llega",
           r and r[1][2] and u and u["estado"] == 3 and pc.nivel == 0 and nivel_s3() == 0,
           f"iba en nivel {en_turbo} · terminó en {(r[0] - t0) if r else 0:.0f} s")


def h19():
    limpiar()
    s3.comando("turbo no")
    pc.turbo_permitido = False
    da, db = foto("19 del PC", 4), foto("19 del aparato", 4)
    t0 = time.time()
    pc.encolar(clase="foto", datos=da, seq=nuevo_seq(), ts=int(time.time()), ref="h19")
    n = s3.foto(db)
    r1 = esperar(lambda e: e[0] == "resultado" and e[1] == "h19" and e[2], 1800, t0)
    r2 = esperar(lambda e: e[0] == "foto_de_ella", 1800, t0)
    igual = r2 and open(r2[1][1]["ruta"], "rb").read() == db
    s3.comando("turbo si")
    pc.turbo_permitido = True
    anotar("19 los dos mandan una foto a la vez: llegan las dos", r1 and igual,
           f"la del PC {(r1[0] - t0) if r1 else 0:.0f} s · la del aparato {(r2[0] - t0) if r2 else 0:.0f} s")
    if r2:
        os.remove(r2[1][1]["ruta"])


def h20():
    # el canal es fijo: el PC no lo cambia y el aparato rechaza el pedido por radio; siguen hablando en el 38
    limpiar()
    t0 = time.time()
    pc.encolar(clase="canal", canal="3A")
    r = esperar(lambda e: e[0] == "canal", 30, t0)
    pc.encolar(clase="comando", texto="canal 3A")
    s = esperar(lambda e: e[0] == "respuesta" and "canal" in str(e[1]), 60, t0)
    pc.encolar(clase="ping")
    q = esperar(lambda e: e[0] == "ping", 20, time.time())
    est = s3.comando("estado") or ""
    anotar("20 el canal es fijo: se rechaza el cambio y siguen hablando",
           r and not r[1][1] and s and "fijo" in str(s[1][1]) and q and q[1][1] and "canal 38" in est,
           f"PC: {r and r[1][2]} | aparato: {s and s[1][1]}")


def texto_de_bytes(etiqueta, n):
    """Un texto que en UTF-8 mide exactamente n bytes."""
    t = etiqueta + " "
    return t + "a" * (n - len(t.encode("utf-8")))


def h21():
    # el módulo se guardaba sin transmitir los paquetes de 31, 63, 95... bytes: un mensaje de 20 o 52 bytes
    # caía justo ahí (5 de cabecera + 6 de número y hora + el texto). Ahora van con un byte de relleno.
    limpiar()
    tiempos = []
    for n in (20, 52):
        t0 = time.time()
        ref = f"h21-{n}"
        pc.encolar(clase="texto", texto=texto_de_bytes(f"pc{n}", n), seq=nuevo_seq(), ts=int(time.time()), ref=ref)
        r = esperar(lambda e: e[0] == "resultado" and e[1] == ref, 40, t0)
        tiempos.append(("PC→aparato", n, round(r[0] - t0, 1) if r and r[1][2] else None))
        bombear(3)
        t0 = time.time()
        texto = texto_de_bytes(f"ap{n}", n)
        s3.ella(texto)
        r = esperar(lambda e: e[0] == "de_ella" and e[1]["texto"] == texto, 40, t0)
        tiempos.append(("aparato→PC", n, round(r[0] - t0, 1) if r else None))
        bombear(3)
    anotar("21 mensajes del largo que el módulo se guardaba (31 y 63 bytes)",
           all(t is not None and t < 15 for _, _, t in tiempos), " · ".join(f"{d} {n} B: {t} s" for d, n, t in tiempos))


def contacto_hasta(seg):
    """Pings cada 15 s hasta que el aparato conteste. Devuelve los segundos que tardó, o None."""
    t0 = time.time()
    while time.time() - t0 < seg:
        t1 = time.time()
        pc.encolar(clase="ping")
        r = esperar(lambda e: e[0] == "ping", 20, t1)
        if r and r[1][1]:
            return time.time() - t0
        bombear(max(0.0, 15 - (time.time() - t1)))
    return None


def h22():
    # el módulo del aparato se movió solo a otro canal: al perderse, el aparato revisa su radio y lo corrige
    limpiar()
    s3.comando("latido 30")  # así se da por perdido a los ~105 s (de verdad: ~7 min)
    bombear(3)
    s3.at("AT+CHANNEL3A")
    t0 = time.time()
    seg = contacto_hasta(400)
    b = s3.bitacora()
    est = s3.comando("estado") or ""
    s3.comando("latido 150")
    corr = [x for x in b if "revisar:" in x]
    anotar("22 el aparato se pierde con la radio movida: la revisa sola y vuelven a hablar",
           seg is not None and "canal 38" in est and any("1 corregidos" in x for x in corr),
           f"contacto {seg and round(seg)} s después · {corr[-1].split('NOTA')[-1].strip() if corr else 'sin revisión'}")


def h23():
    # el módulo del PC se movió solo a otro canal: al perderse, el PC revisa su radio y lo corrige
    limpiar()
    pc.latido_s = 30
    pc.encolar(clase="at", comandos=["AT+CHANNEL3A"])
    esperar(lambda e: e[0] == "at", 30, time.time())
    t0 = time.time()
    v = esperar(lambda e: e[0] == "radio_corregida", 400, t0)
    seg = contacto_hasta(120)
    pc.latido_s = 150
    anotar("23 el PC se pierde con la radio movida: la revisa sola y vuelven a hablar",
           v and "CHANNEL" in v[1][2] and seg is not None,
           f"corregido a los {v and round(v[0] - t0)} s ({v and v[1][2]}) · contacto {seg and round(seg)} s después")


def h24():
    # lo que pasó de verdad: ella manda 10 seguidos y él no podía hablar (cada uno tardaba ~1 min). Ahora los
    # que se juntan en la cola van en un solo paquete (G). Los DOS mandan 10 a la vez.
    limpiar()
    t0 = time.time()
    marca = random.randint(100, 999)
    textos_ella = [f"te extraño 🥺 {marca}-{i}" for i in range(10)]
    textos_el = [f"te amo 💙 {marca}-{i}" for i in range(10)]
    for i in range(10):
        s3.ella(textos_ella[i])
        pc.encolar(clase="texto", texto=textos_el[i], seq=nuevo_seq(), ts=int(time.time()), ref=f"h24-{i}")
    fin = time.time() + 180
    llegados, t_ella, t_el = set(), None, None
    while time.time() < fin and (t_ella is None or t_el is None):
        bombear(0.5)
        llegados = {e[1]["texto"] for t, e in eventos if t >= t0 and e[0] == "de_ella"}
        if t_ella is None and all(x in llegados for x in textos_ella):
            t_ella = time.time() - t0
        oks = [hay(lambda e, i=i: e[0] == "resultado" and e[1] == f"h24-{i}" and e[2], t0) for i in range(10)]
        if t_el is None and all(oks):
            t_el = time.time() - t0
    en_aparato = s3.lista(40) or []
    recibidos_aparato = {r["texto"] for r in en_aparato if r.get("de") == 0}
    ok_aparato = all(x in recibidos_aparato for x in textos_el)
    repetidos = len([1 for t, e in eventos if t >= t0 and e[0] == "de_ella" and e[1]["texto"] in textos_ella]) - len(
        {e[1]["texto"] for t, e in eventos if t >= t0 and e[0] == "de_ella" and e[1]["texto"] in textos_ella})
    anotar("24 ráfaga de 10 mensajes de cada uno, al mismo tiempo", t_ella and t_el and ok_aparato,
           f"los 10 de ella en {t_ella and round(t_ella)} s · los 10 de él en {t_el and round(t_el)} s · "
           f"en el aparato {sum(x in recibidos_aparato for x in textos_el)}/10 · repetidos al PC {repetidos}")


PRUEBAS = {i: globals()[f"h{i}"] for i in range(1, 25)}
RAPIDAS = [1, 2, 3, 4, 5, 6, 7]
if __name__ == "__main__":
    args = sys.argv[1:]
    elegidas = RAPIDAS if args == ["rapidas"] else [int(x) for x in args if x.isdigit()] or list(PRUEBAS)
    for k in elegidas:
        try:
            PRUEBAS[k]()
        except Exception as e:
            import traceback
            traceback.print_exc()
            anotar(f"{k} (excepción)", False, repr(e))
        bombear(3)
    print("\nRESUMEN:", sum(ok for _, ok, _ in resultados), "/", len(resultados), flush=True)
    err = s3.errores()
    print("errores en el registro del aparato:", err[-10:] if err else "ninguno")
    os._exit(0)
