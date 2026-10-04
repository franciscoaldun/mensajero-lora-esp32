# Banco de pruebas REAL entre los dos aparatos: el C3 de él (COM7) y el S3 de ella (COM8), los dos por consola.
# Las fallas se provocan a propósito: el canal del módulo se cambia "por detrás" (= quedar fuera de alcance),
# se reinicia un aparato en plena foto, etc.
#   python herramientas/banco_dos.py            todas
#   python herramientas/banco_dos.py 1 2 7      algunas
import io, json, os, random, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
from s3 import S3

CAPTURAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "capturas")
REGISTRO = open(os.path.join(CAPTURAS, "banco_dos.txt"), "a", encoding="utf-8")
el = S3("COM7")    # C3 de él
ella = S3("COM8")  # S3 de ella
time.sleep(0.5)
resultados = []
MARCA = str(int(time.time()) % 1000)


def anotar(nombre, ok, detalle=""):
    ok = bool(ok)
    resultados.append((nombre, ok))
    linea = f"{'✅' if ok else '❌'} {nombre}: {detalle}"
    print(linea, flush=True)
    REGISTRO.write(time.strftime("%Y-%m-%d %H:%M ") + linea + "\n")
    REGISTRO.flush()


def esperar(cond, seg, cada=2.0):
    t0 = time.time()
    while time.time() - t0 < seg:
        try:
            r = cond()
        except Exception:
            r = None
        if r:
            return time.time() - t0
        time.sleep(cada)
    return None


def presencia(d):
    r = d._pedir("presencia", 4)
    m = re.match(r"@@J (\{.*\})", r or "")
    return json.loads(m.group(1)) if m else {}


def respuestas(d):
    r = d._pedir("respuestas", 4)
    m = re.match(r"@@R (\{.*\})", r or "")
    return json.loads(m.group(1)).get("respuestas", []) if m else []


def tiene(d, texto, k=30, estado=None):
    return any(x.get("texto") == texto and (estado is None or x.get("estado") == estado) for x in (d.lista(k) or []))


def foto(texto, kb):
    img = Image.new("RGB", (340, 250), (20, 30, 70))
    dib = ImageDraw.Draw(img)
    for k in range(0, 340, 22):
        dib.ellipse((k, 40 + k % 90, k + 60, 100 + k % 90), fill=(255, 150 + k // 4, 200))
    dib.text((80, 12), texto, fill=(255, 255, 255))
    q = 90
    while True:
        b = io.BytesIO()
        img.save(b, "JPEG", quality=q)
        if b.tell() <= kb * 1024 or q < 8:
            return b.getvalue()
        q -= 4


def ultima_foto(d, de):
    for x in reversed(d.lista(10) or []):
        if x.get("tipo") == 1 and x.get("de") == de:
            return x
    return None


def juntos(seg=120):
    """Que los dos se estén oyendo antes de seguir."""
    return esperar(lambda: presencia(el).get("cerca") and presencia(ella).get("cerca"), seg, 3)


def cortar(d, segundos=0):
    """Como si quedara fuera de alcance: su módulo se pasa a otro canal (el aparato no se entera)."""
    d.at("AT+CHANNEL3A")


def volver(d):
    d.at("AT+CHANNEL38")


# --------------------------------------------------------------------------------------------- pruebas
def p1():
    juntos()
    t1, t2 = f"de él {MARCA} 💙", f"de ella {MARCA} 🥺"
    el.ella(t1)
    ella.ella(t2)
    a = esperar(lambda: tiene(ella, t1), 60)
    b = esperar(lambda: tiene(el, t2), 60)
    c = esperar(lambda: tiene(el, t1, estado=1) and tiene(ella, t2, estado=1), 40)
    anotar("1 textos en los dos sentidos con ✓✓", a and b and c, f"él→ella {a and round(a)} s · ella→él {b and round(b)} s")


def p2():
    # los largos que el módulo se guardaba (31 y 63 bytes: 20 y 52 de texto)
    ok = []
    for n in (20, 52):
        t = ("x" * n)[:n - 4] + MARCA[:3] + "!"
        t = t[:n]
        el.ella(t)
        ok.append(esperar(lambda: tiene(ella, t), 60))
        t2 = ("y" * n)[:n - 4] + MARCA[:3] + "!"
        t2 = t2[:n]
        ella.ella(t2)
        ok.append(esperar(lambda: tiene(el, t2), 60))
    anotar("2 mensajes de los largos que el módulo retenía", all(x is not None for x in ok), " · ".join(f"{x and round(x)} s" for x in ok))


def p3():
    el._pedir("leer")
    ella._pedir("leer")
    t1 = f"leeme {MARCA} a"
    t2 = f"leeme {MARCA} b"
    el.ella(t1)
    ella.ella(t2)
    esperar(lambda: tiene(ella, t1) and tiene(el, t2), 60)
    ella._pedir("leer")
    el._pedir("leer")
    a = esperar(lambda: any(x.get("texto") == t1 and x.get("leido") for x in el.lista(15)), 90)
    b = esperar(lambda: any(x.get("texto") == t2 and x.get("leido") for x in ella.lista(15)), 90)
    anotar("3 recibos de leído en los dos sentidos", a and b, f"él supo {a and round(a)} s · ella supo {b and round(b)} s")


def p4():
    mios = [f"ráfaga él {MARCA}-{i}" for i in range(10)]
    suyos = [f"ráfaga ella {MARCA}-{i}" for i in range(10)]
    for i in range(10):
        el.ella(mios[i])
        ella.ella(suyos[i])
    a = esperar(lambda: all(tiene(ella, m, 40) for m in mios), 300, 4)
    b = esperar(lambda: all(tiene(el, m, 40) for m in suyos), 300, 4)
    lista_ella = [x.get("texto") for x in ella.lista(60)]
    rep = sum(lista_ella.count(m) > 1 for m in mios)
    anotar("4 ráfaga de 10 de cada uno a la vez", a and b and rep == 0, f"los de él en {a and round(a)} s · los de ella en {b and round(b)} s · repetidos {rep}")


def p5():
    c0, c1 = len(respuestas(el)), len(respuestas(ella))
    el._pedir("aparato estado")
    a = esperar(lambda: len(respuestas(el)) > c0, 60)
    ella._pedir("aparato minutos 10")
    b = esperar(lambda: len(respuestas(ella)) > c1, 60)
    ra, rb = respuestas(el), respuestas(ella)
    anotar("5 comandos por radio en los dos sentidos", a and b and "minutos" in rb[-1]["texto"],
           f"él→ella {a and round(a)} s ({ra[-1]['texto'][:40] if ra else ''}) · ella→él {b and round(b)} s ({rb[-1]['texto'][:30] if rb else ''})")


def p6():
    d = foto(f"foto de ella {MARCA}", 5)
    n = ella.foto(d)
    a = esperar(lambda: (ultima_foto(el, 1) or {}).get("estado") == 3 and ultima_foto(el, 1)["n"] > 0, 400, 4)
    b = esperar(lambda: (ella.registro_n(n) or {}).get("estado") == 1, 60)
    anotar("6 foto de ella → él (5 KB)", a and b, f"llegó en {a and round(a)} s · ✓✓ en el de ella {b is not None}")


def p7():
    d = foto(f"foto de él {MARCA}", 5)
    n = el.foto(d)
    a = esperar(lambda: (ultima_foto(ella, 0) or {}).get("estado") == 3, 400, 4)
    b = esperar(lambda: (el.registro_n(n) or {}).get("estado") == 1, 60)
    anotar("7 foto de él → ella (5 KB)", a and b, f"llegó en {a and round(a)} s · ✓✓ en el C3 {b is not None}")


def p8():
    # foto grande: con la señal fuerte va en turbo
    d = foto(f"foto turbo {MARCA} " + "x" * 30, 12)
    n = ella.foto(d)
    a = esperar(lambda: (ella.registro_n(n) or {}).get("estado") == 1, 500, 4)
    anotar("8 foto de 12 KB de ella → él (turbo si la señal sobra)", a, f"{a and round(a)} s")


def p9():
    # se pierden (el módulo de él cambia de canal por detrás) y se reencuentran solos: el C3 revisa su radio al
    # perderse y corrige el canal; lo escrito sin señal sale solo al reencontrarse
    el.comando("latido 30")
    ella.comando("latido 30")
    juntos()
    cortar(el)
    t0 = time.time()
    perdida = esperar(lambda: not presencia(ella).get("cerca"), 240, 5)
    t_sin = f"escrito sin señal {MARCA}"
    ella.ella(t_sin)
    vuelta = esperar(lambda: presencia(el).get("cerca") and presencia(ella).get("cerca"), 400, 5)
    llego = esperar(lambda: tiene(el, t_sin), 120)
    est = el.comando("estado") or ""
    el.comando("latido 150")
    ella.comando("latido 150")
    anotar("9 se pierden, el C3 corrige su radio solo y lo escrito sin señal llega", perdida and vuelta and llego and "canal 38" in est,
           f"ella notó la pérdida a los {perdida and round(perdida)} s · se reencontraron {vuelta and round(vuelta)} s después · "
           f"el mensaje llegó {llego and round(llego)} s después")


def p10():
    # foto que se corta a mitad (ella → él) y sigue desde donde quedó
    juntos()
    d = foto(f"foto cortada {MARCA} " + "y" * 20, 7)
    n = ella.foto(d)
    esperar(lambda: (ultima_foto(el, 1) or {}).get("prog", 0) >= 250 and ultima_foto(el, 1).get("estado") == 0, 240, 3)
    tenia = (ultima_foto(el, 1) or {}).get("prog", 0)
    cortar(el)
    time.sleep(40)
    volver(el)
    a = esperar(lambda: (ella.registro_n(n) or {}).get("estado") == 1, 600, 5)
    anotar("10 foto cortada a mitad (ella → él) sigue desde donde quedó", a, f"él tenía {tenia // 10}% · terminó {a and round(a)} s después de volver")


def p11():
    # el C3 se reinicia en plena foto de ella
    juntos()
    d = foto(f"foto con reinicio {MARCA} " + "z" * 20, 7)
    n = ella.foto(d)
    esperar(lambda: (ultima_foto(el, 1) or {}).get("prog", 0) >= 200, 240, 3)
    el.reiniciar()
    a = esperar(lambda: (ella.registro_n(n) or {}).get("estado") == 1, 700, 5)
    anotar("11 el C3 se reinicia en plena foto y la foto llega igual", a, f"{a and round(a)} s")


PRUEBAS = {i: globals()[f"p{i}"] for i in range(1, 12)}
if __name__ == "__main__":
    elegidas = [int(x) for x in sys.argv[1:] if x.isdigit()] or list(PRUEBAS)
    for k in elegidas:
        try:
            PRUEBAS[k]()
        except Exception as e:
            import traceback
            traceback.print_exc()
            anotar(f"{k} (excepción)", False, repr(e))
    volver(el)
    print("\nRESUMEN:", sum(ok for _, ok in resultados), "/", len(resultados), flush=True)
    os._exit(0)
