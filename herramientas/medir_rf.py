# Mediciones de RF con los dos módulos sobre la mesa (sin moverlos durante la prueba).
#   python herramientas/medir_rf.py COM9 COM10 potencia|canales
# potencia: barre AT+POWE 0..22 en el que transmite y mide la señal que llega.
# canales:  barre los canales legales en Chile (902-928 MHz) con potencia baja para no saturar,
#           y dice en cuál rinden mejor las antenas.
import statistics, sys, time
import serial

TX, RX, QUE = sys.argv[1], sys.argv[2], sys.argv[3]
REPETIR = 6


def at(s, cmds):
    s.reset_input_buffer()
    s.write(b"+++\r\n"); time.sleep(1.5); r = s.read(200)
    if b"Entry" not in r:
        s.write(b"+++\r\n"); time.sleep(1.5); s.read(200)
    for c in cmds:
        s.write((c + "\r\n").encode()); time.sleep(0.5); s.read(200)
    s.write(b"+++\r\n"); time.sleep(2.0); s.read(200)


def medir(tx, rx):
    vals = []
    for i in range(REPETIR):
        rx.reset_input_buffer()
        tx.write(b"\xd5\x03P\x00\x00")  # 5 bytes, como un ping
        fin, d = time.time() + 2.5, b""
        while time.time() < fin and len(d) < 6:
            d += rx.read(16)
        if len(d) >= 6:
            vals.append(-(255 - d[5]))
        time.sleep(0.3)
    return vals


tx = serial.Serial(TX, 9600, timeout=0.05)
rx = serial.Serial(RX, 9600, timeout=0.05)
try:
    if QUE == "potencia":
        for p in range(0, 23, 2):
            at(tx, [f"AT+POWE{p}"])
            v = medir(tx, rx)
            print(f"potencia {p:2d} dBm -> llega {statistics.mean(v):6.1f} dBm  ({len(v)}/{REPETIR})" if v else f"potencia {p:2d} -> nada", flush=True)
    else:
        at(tx, ["AT+POWE0"])
        res = []
        for c in range(0x34, 0x4E):
            at(tx, [f"AT+CHANNEL{c:02X}"])
            at(rx, [f"AT+CHANNEL{c:02X}"])
            v = medir(tx, rx)
            m = statistics.mean(v) if v else None
            res.append((m, c))
            print(f"canal {c:02X} {850.15 + c:7.2f} MHz -> {m if m is None else round(m, 1)} dBm ({len(v)}/{REPETIR})", flush=True)
        buenos = sorted((r for r in res if r[0] is not None), reverse=True)[:5]
        print("mejores:", ", ".join(f"{c:02X}={850.15 + c:.2f} MHz ({m:.1f})" for m, c in buenos))
finally:
    # dejar ambos como estaban
    at(tx, ["AT+POWE22", "AT+CHANNEL38"])
    at(rx, ["AT+POWE22", "AT+CHANNEL38"])
