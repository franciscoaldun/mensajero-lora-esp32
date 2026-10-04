# Prueba de esfuerzo: el aparato transmite seguido a la potencia de siempre (22 dBm) durante N segundos y se
# cuentan los cuelgues (Guru Meditation = reinicio). Si se reinicia, se le vuelve a pedir que siga.
#   python herramientas/esfuerzo_tx.py 60 [brillo] [largo]   brillo = por mil (0 = no tocar); largo = bytes del paquete
#                                                       (229 = como un trozo de foto, ~7 s en el aire)
import sys, time
import serial

SEG = int(sys.argv[1]) if len(sys.argv) > 1 else 60
BRILLO = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "0" else None
LARGO = int(sys.argv[3]) if len(sys.argv) > 3 else 0
s = serial.Serial()
s.port, s.baudrate, s.timeout, s.dtr, s.rts = "COM8", 115200, 0.1, False, False
s.open()


def pedir(cmd, seg=2.5):
    s.write((cmd + "\n").encode())
    t, b = time.time(), b""
    while time.time() - t < seg:
        b += s.read(4096)
    return b.decode("utf-8", "replace")


if BRILLO:
    pedir(f"comando brillo bajo {BRILLO}")
    pedir(f"comando brillo alto {BRILLO}")
pedir(f"prueba tx 500 {LARGO}")
t0, cola, caidas, esperando_arranque = time.time(), b"", 0, False
while time.time() - t0 < SEG:
    cola = (cola + s.read(4096))[-6000:]
    if b"Guru Meditation" in cola:
        caidas += 1
        print(f"  caída {caidas} a los {time.time() - t0:.0f} s", flush=True)
        cola = b""
        esperando_arranque = True
    if esperando_arranque and b"portal: red" in cola:  # arrancó de nuevo: que siga transmitiendo
        time.sleep(1)
        pedir(f"prueba tx 500 {LARGO}", 1)
        esperando_arranque = False
        cola = b""
pedir("prueba tx 0")
print(f"RESULTADO: {SEG} s transmitiendo a 22 dBm{' con brillo ' + BRILLO if BRILLO else ''}"
      f"{f' en paquetes de {LARGO} bytes' if LARGO else ''}: {caidas} caídas", flush=True)
