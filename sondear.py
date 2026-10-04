# Lee la configuración actual del módulo DX-LR32 sin cambiar nada.
import serial, sys, time

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "COM9"
BAUDIOS = [9600, 115200, 57600, 38400, 19200, 4800, 2400]


def hablar(s, datos, espera=0.6):
    s.reset_input_buffer()
    s.write(datos)
    time.sleep(espera)
    return s.read(s.in_waiting or 1).decode("latin-1", "replace")


for b in BAUDIOS:
    with serial.Serial(PUERTO, b, timeout=0.3) as s:
        time.sleep(0.2)
        r = hablar(s, b"AT\r\n")
        en_at = "OK" in r
        if not en_at:
            for variante in (b"+++", b"+++\r\n"):
                r = hablar(s, variante, 1.0)
                if "Entry" in r or "OK" in r:
                    en_at = True
                    break
                if "Exit" in r:  # estaba en AT y lo sacamos: volver a entrar
                    r = hablar(s, variante, 1.0)
                    en_at = "Entry" in r
                    break
        print(f"{b} baudios -> {r.strip()!r}")
        if not en_at:
            continue
        print(hablar(s, b"AT+HELP\r\n", 1.0))
        for cmd in ("SWITCH", "CHANNEL", "OPENKEY", "PACKET", "DRSSI", "LBT", "BAUD", "PARI"):
            print(hablar(s, f"AT+{cmd}\r\n".encode(), 0.4).strip())
        print(hablar(s, b"+++", 1.0).strip())  # salir del modo AT
        break
else:
    print("No respondió en ningún baudio. ¿M0/M1 sueltos con SWITCH=1? Probar M0 y M1 a GND.")
