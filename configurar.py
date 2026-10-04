# Deja un módulo DX-LR32-900T22D con la configuración de máximo alcance.
# Correr igual en los DOS módulos: deben quedar idénticos o no se hablan.
#   python configurar.py COM9
import json, os, serial, sys, time

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "COM9"
# La clave vive en secreto.json (solo en este PC), nunca escrita en el código ni en notas.
SECRETO = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "secreto.json")))

CONFIG = [
    "AT+MODE0",       # transparente: lo que entra por serie sale por radio
    "AT+LEVEL0",      # SF11 / 125 kHz / 4/8 -> 336 bps, el de más alcance
    "AT+POWE22",      # potencia máxima (22 dBm, con VCC a 5 V)
    f"AT+CHANNEL{SECRETO.get('canal', '38')}",  # 906,15 MHz por defecto: dentro de 902-928 (Chile), lejos de LoRaWAN AU915
    "AT+SLEEP2",      # siempre escuchando (4 mA en recepción)
    "AT+SWITCH0",     # M0/M1 no se usan: se pueden dejar sin conectar
    "AT+OPENKEY1",
    f"AT+KEY{SECRETO['clave_modulo']}",  # la "clave" de la pareja (1-65535), igual en ambos
    "AT+PACKET3",     # paquetes de 230 bytes
    "AT+DRSSI1",      # agrega 1 byte con la intensidad de señal a cada mensaje recibido
    "AT+LBT0",        # transmitir al tiro, sin esperar a que el canal esté libre
    "AT+IQ1",
    "AT+CRC1",
]


def hablar(s, datos, espera=0.6):
    s.reset_input_buffer()
    s.write(datos)
    time.sleep(espera)
    return s.read(s.in_waiting or 1).decode("latin-1", "replace").strip()


with serial.Serial(PUERTO, 9600, timeout=0.3) as s:
    time.sleep(0.2)
    if "OK" not in hablar(s, b"AT\r\n"):
        for _ in range(3):  # "+++" sin \r\n no hace nada
            time.sleep(1.0)
            r = hablar(s, b"+++\r\n", 1.5)
            if "Entry" in r:
                break
        else:
            sys.exit(f"No entró al modo AT (respondió {r!r})")
    malos = []
    for cmd in CONFIG:
        r = hablar(s, (cmd + "\r\n").encode(), 0.5)
        visible = f"{cmd} -> {r.replace(chr(13), '').replace(chr(10), ' ')}"
        if cmd.startswith("AT+KEY"):
            visible = "AT+KEY***** -> " + ("OK" if "OK" in r else r)
        print(visible)
        if "OK" not in r:
            malos.append(cmd)
    print(hablar(s, b"AT+HELP\r\n", 1.0))
    print(hablar(s, b"+++\r\n", 2.0))  # salir: el módulo se reinicia con lo nuevo
    print("LISTO" if not malos else f"FALLARON: {malos}")
