# Barre todos los nombres de comando AT de 1 a 3 letras (más un diccionario de nombres largos)
# y anota todo lo que NO responda "ERROR=104" (= comando desconocido).
#   python herramientas/barrer_comandos.py COM10
import itertools, json, os, string, sys, time
import serial

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "COM10"
SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "barrido_comandos.json")
CONOCIDOS = {"AT", "RESET", "DEFAULT", "BAUD", "PARI", "HELP", "LEVEL", "MODE", "SLEEP", "SWITCH", "CHANNEL",
             "MAC", "OPENKEY", "KEY", "PACKET", "DRSSI", "POWE", "LBT", "LRSSI", "ERSSI", "IQ", "CRC", "WAKEUP"}
# acciones que no queremos disparar sin querer (reinician o borran la configuración)
EVITAR = ("RESET", "DEFAULT", "SLEEP", "BAUD", "PARI", "SWITCH")
LARGOS = """FACTORY FACTORYTEST FTEST TESTMODE RFTEST TXTEST RXTEST CW TXCW TONE CARRIER PRBS SWEEP
SF SPREAD BW BAND BANDW BWD CR CODING FEC PREAM PREAMBLE PRE PLEN SYNC SYNCWORD NETID NID
LDRO LOWDR HEADER IMPLICIT EXPLICIT PAYLOAD LEN TXPWR TXPOWER PWR PA PAMAX OCP RAMP TCXO XTAL TRIM
FREQ FRE FRQ FREQUENCY RFFREQ CH CHAN HOP FHSS RXBOOST BOOST LNA GAIN AGC RXGAIN SENS
VER VERSION FW FWVER SN SERIAL ID UID CHIPID NAME MODEL INFO STATUS STAT CFG CONFIG PARAM PARAMS
SAVE LOAD WRITE READ REG REGS PEEK POKE MEM DUMP FLASH EEPROM ERASE BOOT ISP DFU UPDATE OTA UPGRADE
DEBUG LOG ECHO ATE ATI HELP1 HELP2 MENU ALL LIST CMD CMDS
RSSI SNR NOISE CAD CADT CADTIME WOR WAKE PERIOD TIME TIMEOUT DELAY INTERVAL RETRY ACK RELAY REPEAT
TRANS TRANSPARENT FIXED BROADCAST ADDR ADDRESS DEST TARGET GROUP NET NETWORK PAIR BIND
POWER POW TXP DBM LEVELS RATE AIR AIRRATE SPEED DR ADR
""".split()


def preguntar(s, nombre):
    s.reset_input_buffer()
    s.write(f"AT+{nombre}\r\n".encode())
    fin, r = time.time() + 0.35, b""
    while time.time() < fin:
        r += s.read(64)
        if r.endswith(b"\r\n") and (b"ERROR" in r or b"OK" in r):
            break
    return r.decode("latin-1").strip().replace("\r\n", " | ")


def main():
    hallados = {}
    with serial.Serial(PUERTO, 9600, timeout=0.02) as s:
        s.write(b"AT\r\n"); time.sleep(0.4)
        if b"OK" not in s.read(64):
            s.write(b"+++\r\n"); time.sleep(1.5); s.read(64)
        nombres = [w for w in LARGOS]
        for n in (1, 2, 3):
            nombres += ["".join(t) for t in itertools.product(string.ascii_uppercase, repeat=n)]
        t0 = time.time()
        for i, nom in enumerate(nombres):
            if any(nom.startswith(e) for e in EVITAR) or nom in CONOCIDOS:
                continue
            r = preguntar(s, nom)
            if "ERROR=104" not in r:
                hallados[nom] = r
                print(f"  ¡{nom}! -> {r}", flush=True)
            if i % 2000 == 0:
                print(f"{i}/{len(nombres)} ({time.time() - t0:.0f} s)", flush=True)
                json.dump(hallados, open(SALIDA, "w"), indent=1)
        s.write(b"+++\r\n"); time.sleep(2)
    json.dump(hallados, open(SALIDA, "w"), indent=1)
    print("listo:", len(hallados), "respuestas distintas de 104")


if __name__ == "__main__":
    main()
