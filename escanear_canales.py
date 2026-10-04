# Mide el ruido de fondo de cada canal legal en Chile (902-928 MHz) para elegir el más limpio.
# Deja el módulo en el canal donde estaba al empezar.
import serial, sys, time

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "COM9"
CANALES = range(0x34, 0x4F)  # 902,15 ... 928,15 MHz -> se excluye 4E por ser el borde
MUESTRAS = 6


def hablar(s, datos, espera=0.5):
    s.reset_input_buffer()
    s.write(datos)
    time.sleep(espera)
    return s.read(s.in_waiting or 1).decode("latin-1", "replace")


def entrar_at(s):
    if "OK" in hablar(s, b"AT\r\n", 0.4):
        return
    for _ in range(4):
        if "Entry" in hablar(s, b"+++\r\n", 1.5):
            return
        time.sleep(0.5)
    raise SystemExit("no entra al modo AT")


resultados = {}
with serial.Serial(PUERTO, 9600, timeout=0.3) as s:
    entrar_at(s)
    original = hablar(s, b"AT+CHANNEL\r\n").split("=")[1].split()[0]
    for c in CANALES:
        if c == 0x4E:
            continue
        hablar(s, f"AT+CHANNEL{c:02X}\r\n".encode())
        hablar(s, b"+++\r\n", 2.0)  # salir reinicia con el canal nuevo
        entrar_at(s)
        lect = []
        for _ in range(MUESTRAS):
            r = hablar(s, b"AT+ERSSI\r\n", 0.35)
            try:
                lect.append(int(r.split("=")[1].split()[0]))
            except (IndexError, ValueError):
                pass
            time.sleep(0.2)
        mhz = 850.15 + c
        resultados[c] = lect
        print(f"canal {c:02X} ({mhz:.2f} MHz): {lect}", flush=True)
    hablar(s, f"AT+CHANNEL{original}\r\n".encode())
    hablar(s, b"+++\r\n", 2.0)

print("\nMás silenciosos (peor lectura de cada canal):")
orden = sorted((max(v), sum(v) / len(v), c) for c, v in resultados.items() if v)
for peor, prom, c in orden[:8]:
    print(f"  {c:02X} = {850.15 + c:.2f} MHz  peor {peor} dBm, promedio {prom:.1f}")
