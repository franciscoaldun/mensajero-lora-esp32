# Banco de pruebas automático: controla el aparato (consola COM8) y el PC (radio COM9) a la vez.
#   python herramientas/banco_s3.py            todas las pruebas
#   python herramientas/banco_s3.py 1 6        sólo algunas
import io, os, queue, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
from PIL import Image, ImageDraw
from s3 import S3

spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(__file__), "..", "app_mensajes.pyw"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

CAPTURAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "capturas")
os.makedirs(CAPTURAS, exist_ok=True)

ev = queue.Queue()
pc = app.Radio(ev)
pc.start()
s3 = S3()
time.sleep(2)
resultados = []
eventos_pc = []   # todo lo que le llegó al PC, con hora


def bombear(segundos=0.0):
    fin = time.time() + segundos
    while True:
        try:
            e = ev.get(timeout=0.05)
            eventos_pc.append((time.time(), e))
        except queue.Empty:
            pass
        if time.time() >= fin:
            return


def esperar_pc(cond, segundos):
    fin = time.time() + segundos
    visto = len(eventos_pc)
    while time.time() < fin:
        bombear(0.2)
        for t, e in eventos_pc[visto:]:
            if cond(e):
                return t, e
        visto = len(eventos_pc)
    return None


def esperar_s3(n, estados, segundos):
    fin = time.time() + segundos
    while time.time() < fin:
        bombear(2.0)
        r = s3.registro_n(n)
        if r and r["estado"] in estados:
            return time.time(), r
    return None


def foto_prueba(texto, kb):
    img = Image.new("RGB", (340, 250), (20, 30, 70))
    d = ImageDraw.Draw(img)
    for k in range(0, 340, 22):
        d.ellipse((k, 40 + k % 90, k + 60, 100 + k % 90), fill=(255, 150 + k // 4, 200))
    d.text((90, 12), texto, fill=(255, 255, 255))
    q = 70
    while True:
        b = io.BytesIO()
        img.save(b, "JPEG", quality=q)
        if b.tell() <= kb * 1024 or q < 10:
            return b.getvalue()
        q -= 5


def anotar(nombre, ok, detalle):
    resultados.append((nombre, ok, detalle))
    print(f"{'✅' if ok else '❌'} {nombre}: {detalle}", flush=True)


def ultimo_s3_de_el():
    for r in reversed(s3.lista(5)):
        if r["de"] == 0:
            return r
    return None


def prueba_1():
    t0 = time.time()
    pc.encolar(clase="texto", texto="Hola amor 💗 prueba 1, ñandú", ref="p1")
    r = esperar_pc(lambda e: e[0] == "resultado" and e[1] == "p1", 60)
    u = ultimo_s3_de_el()
    ok = bool(r and r[1][2] and u and u["texto"] == "Hola amor 💗 prueba 1, ñandú")
    anotar("1 texto PC→aparato", ok, f"{(r[0] - t0) if r else 0:.1f} s, en el aparato: {u['texto'] if u else None!r}")
    s3.captura(os.path.join(CAPTURAS, "1_texto.png"))


def prueba_2():
    t0 = time.time()
    n = s3.ella("te amo mi vida 🥺 prueba 2")
    r = esperar_pc(lambda e: e[0] == "de_ella" and "prueba 2" in e[1], 60)
    f = esperar_s3(n, (1,), 30)
    anotar("2 texto aparato→PC", bool(r and f), f"llegó al PC en {(r[0] - t0) if r else 0:.1f} s, ✓✓ en el aparato: {bool(f)}")


def prueba_3():
    ok = 0
    for _ in range(5):
        pc.encolar(clase="ping")
        r = esperar_pc(lambda e: e[0] == "ping", 10)
        ok += bool(r and r[1][1])
    anotar("3 pings", ok >= 4, f"{ok}/5")


def prueba_4():
    pc.encolar(clase="comando", texto="estado")
    r = esperar_pc(lambda e: e[0] == "respuesta", 40)
    anotar("4 comando estado por radio", bool(r and "encendida" in r[1][1]), r[1][1] if r else "sin respuesta")


def prueba_5():
    # los dos hablan al mismo tiempo: chocan, pero los dos tienen que llegar
    t0 = time.time()
    n = s3.ella("cruzado desde el aparato (prueba 5)")
    pc.encolar(clase="texto", texto="cruzado desde el PC (prueba 5)", ref="p5")
    a = b = f = None
    while time.time() - t0 < 180 and not (a and b and f):
        bombear(3)
        a = a or next(((t, e) for t, e in eventos_pc if t > t0 and e[0] == "resultado" and e[1] == "p5"), None)
        b = b or next(((t, e) for t, e in eventos_pc if t > t0 and e[0] == "de_ella" and "prueba 5" in e[1]), None)
        r = s3.registro_n(n)
        f = f or (r and r["estado"] == 1 and (time.time(), r))
        if "-v" in sys.argv:
            e = s3.estado()
            print(f"   {time.time() - t0:4.0f}s ap: estado={r and r['estado']} fase={e and e['fase']} turno={e and e['turno']}"
                  f" | PC: resultado={bool(a)} de_ella={bool(b)}", flush=True)
    anotar("5 mensajes cruzados al mismo tiempo", bool(a and a[1][2] and b and f),
           f"PC→ap {(a[0] - t0) if a else 0:.0f} s · ap→PC {(b[0] - t0) if b else 0:.0f} s")


def prueba_6():
    # foto PC→aparato y, en medio, ella escribe dos veces: tienen que llegar ANTES de que termine la foto
    datos = foto_prueba("prueba 6 PC -> aparato", 5)
    t0 = time.time()
    pc.encolar(clase="foto", datos=datos, ref="p6")
    bombear(25)
    n1 = s3.ella("escribo mientras llega la foto (6a)")
    bombear(40)
    n2 = s3.ella("otro durante la foto (6b)")
    r = esperar_pc(lambda e: e[0] == "resultado" and e[1] == "p6", 600)
    fin_foto = r[0] if r else time.time()
    bombear(30)
    llegadas = [t for t, e in eventos_pc if e[0] == "de_ella" and "(6" in e[1]]
    durante = sum(1 for t in llegadas if t < fin_foto)
    u = ultimo_s3_de_el()
    anotar("6 foto PC→aparato con textos de ella en medio", bool(r and r[1][2] and len(llegadas) == 2),
           f"foto {len(datos)} B en {fin_foto - t0:.0f} s (estimado {app.p.segundos_foto(len(datos)):.0f}); "
           f"textos llegados {len(llegadas)}/2, durante la foto {durante}; en el aparato: {u['tipo'] if u else '-'}/{u['estado'] if u else '-'}")
    s3.captura(os.path.join(CAPTURAS, "6_foto.png"))


def prueba_7():
    # foto aparato→PC y el PC escribe en medio
    datos = foto_prueba("prueba 7 aparato -> PC", 5)
    t0 = time.time()
    n = s3.foto(datos)
    print(f"   foto subida al aparato como registro {n} ({len(datos)} B)", flush=True)
    bombear(30)
    pc.encolar(clase="texto", texto="escribo mientras me llega tu foto (7)", ref="p7")
    r = None
    while time.time() - t0 < 600 and not r:
        r = esperar_pc(lambda e: e[0] == "foto_de_ella", 15)
        if "-v" in sys.argv and not r:
            e = s3.estado()
            pct = [x[1] for _, x in eventos_pc if x[0] == "foto_llegando"]
            print(f"   {time.time() - t0:4.0f}s ap: fase={e and e['fase']} rondas={e and e['rondas']} int={e and e['intentos']}"
                  f" | PC recibió {pct[-1] if pct else 0}%", flush=True)
    t = esperar_pc(lambda e: e[0] == "resultado" and e[1] == "p7", 5) or \
        next(((tt, e) for tt, e in eventos_pc if e[0] == "resultado" and e[1] == "p7"), None)
    igual = r and open(r[1][1], "rb").read() == datos
    f = esperar_s3(n, (1,), 60)
    anotar("7 foto aparato→PC con texto del PC en medio", bool(igual and t and t[1][2] and f),
           f"foto en {(r[0] - t0) if r else 0:.0f} s, idéntica={bool(igual)}, texto {'llegó' if t and t[1][2] else 'NO'}"
           f" a los {(t[0] - t0) if t else 0:.0f} s, ✓✓ en el aparato={bool(f)}")
    if r:
        os.remove(r[1][1])


def prueba_8():
    # el aparato se reinicia en medio de una foto: tiene que terminar llegando igual
    datos = foto_prueba("prueba 8 reinicio", 4)
    t0 = time.time()
    pc.encolar(clase="foto", datos=datos, ref="p8")
    bombear(30)
    s3.reiniciar()
    r = esperar_pc(lambda e: e[0] == "resultado" and e[1] == "p8", 600)
    u = ultimo_s3_de_el()
    anotar("8 foto con reinicio del aparato a la mitad", bool(r and r[1][2] and u and u["tipo"] == 1 and u["estado"] == 3),
           f"{(r[0] - t0) if r else 0:.0f} s; en el aparato: {u}")


def canal_s3():
    r = s3.at("AT+CHANNEL")
    import re as _re
    m = _re.search(r"\+CHANNEL=([0-9A-Fa-f]{2})", r or "")
    return m.group(1).upper() if m else None


def prueba_9():
    # cambio de canal bien hecho en los dos lados, mensaje por el canal nuevo y vuelta al de siempre
    t0 = time.time()
    pc.encolar(clase="canal", canal="3A")
    r = esperar_pc(lambda e: e[0] == "canal", 150)
    en_s3 = canal_s3()
    pc.encolar(clase="texto", texto="hola por el canal 3A (prueba 9)", ref="p9")
    m = esperar_pc(lambda e: e[0] == "resultado" and e[1] == "p9", 60)
    est = s3.comando("estado") or ""
    pc.encolar(clase="canal", canal="38")
    v = esperar_pc(lambda e: e[0] == "canal", 150)
    anotar("9 cambio de canal en los dos y vuelta", bool(r and r[1][1] and en_s3 == "3A" and m and m[1][2] and v and v[1][1] and canal_s3() == "38"),
           f"ida: {r[1][2] if r else None} | aparato en {en_s3}, guardado: {'canal 3A' in est} | mensaje {'llegó' if m and m[1][2] else 'NO'}"
           f" | vuelta: {v[1][2] if v else None} ({time.time() - t0:.0f} s)")


def prueba_10():
    # sólo el aparato cambia (el PC se queda): tiene que darse cuenta y volver solo a los 2 min
    pc.encolar(clase="comando", texto="canal 3A")
    r = esperar_pc(lambda e: e[0] == "respuesta", 40)
    bombear(8)
    durante = canal_s3()
    print("   esperando que vuelva solo (2 min)…", flush=True)
    bombear(130)
    despues = canal_s3()
    ok = 0
    for _ in range(3):
        pc.encolar(clase="ping")
        x = esperar_pc(lambda e: e[0] == "ping", 10)
        ok += bool(x and x[1] and x[1][1] is not None)
    est = s3.comando("estado") or ""
    anotar("10 cambio a medias: el aparato vuelve solo", durante == "3A" and despues == "38" and ok >= 2 and "canal 38" in est,
           f"respuesta: {r[1][1] if r else None} | durante: {durante} | después: {despues} | pings {ok}/3 | guardado 38: {'canal 38' in est}")


PRUEBAS = {1: prueba_1, 2: prueba_2, 3: prueba_3, 4: prueba_4, 5: prueba_5, 6: prueba_6, 7: prueba_7, 8: prueba_8, 9: prueba_9, 10: prueba_10}
elegidas = [int(x) for x in sys.argv[1:] if x.isdigit()] or list(PRUEBAS)
for k in elegidas:
    try:
        PRUEBAS[k]()
    except Exception as e:
        anotar(f"{k} (excepción)", False, repr(e))
    bombear(3)
print("\nRESUMEN:", sum(ok for _, ok, _ in resultados), "/", len(resultados), "OK")
err = s3.errores()
print("errores en el registro del aparato:", err[-10:] if err else "ninguno")
