# Control del aparato (ESP32-S3) por su consola en el cable COM, sin Wi-Fi.
import base64, json, re, time
import serial
from serial.tools import list_ports
from PIL import Image


def buscar_puerto_s3():
    for p in list_ports.comports():
        if p.vid == 0x1A86 and p.pid == 0x55D3:  # CH343 de la placa S3
            return p.device
    return None


class S3:
    def __init__(self, puerto=None):
        s = serial.Serial()
        s.port = puerto or buscar_puerto_s3()
        s.baudrate = 115200
        s.timeout = 0.1
        s.dtr = False  # que abrir el puerto no reinicie la placa
        s.rts = False
        s.open()
        self.s = s
        self.registro = []  # líneas de log del aparato

    def _linea(self, segundos):
        fin = time.time() + segundos
        buf = b""
        while time.time() < fin:
            c = self.s.readline()
            if c:
                buf += c
                if buf.endswith(b"\n"):
                    t = buf.decode("utf-8", "replace").rstrip("\r\n")
                    buf = b""
                    if t.startswith("@@"):
                        return t
                    self.registro.append(t)
        return None

    def _pedir(self, cmd, segundos=5):
        self.s.reset_input_buffer()
        self.s.write((cmd + "\n").encode("utf-8"))
        return self._linea(segundos)

    def ella(self, texto):
        r = self._pedir("ella " + texto)
        return int(r.split()[1]) if r and r.startswith("@@OK") else None

    def foto(self, datos):
        r = self._pedir(f"foto {len(datos)}")
        if r != "@@LISTO":
            return None
        for i in range(0, len(datos), 1024):
            self.s.write(datos[i:i + 1024])
            time.sleep(0.09)
        r = self._linea(10)
        return int(r.split()[1]) if r and r.startswith("@@OK") else None

    def lista(self, k=10):
        for _ in range(3):  # si una línea llega rota, se pregunta de nuevo
            self.s.reset_input_buffer()
            self.s.write(f"lista {k}\n".encode())
            out, rota = [], False
            while True:
                r = self._linea(5)
                if not r or r == "@@FIN":
                    break
                if r.startswith("@@L "):
                    try:
                        out.append(json.loads(r[4:]))
                    except ValueError:
                        rota = True
            if not rota:
                return out
        return out

    def registro_n(self, n):
        for r in self.lista(30):
            if r["n"] == n:
                return r
        return None

    def estado(self):
        r = self._pedir("estado")
        return json.loads(r[4:]) if r and r.startswith("@@E ") else None

    def comando(self, texto):
        r = self._pedir("comando " + texto)
        return r[4:] if r else None

    def at(self, comandos):
        r = self._pedir("at " + comandos, 30)
        return r[4:] if r else None

    def bitacora(self):
        self.s.reset_input_buffer()
        self.s.write(b"bitacora\n")
        out = []
        while True:
            r = self._linea(5)
            if not r or r == "@@FIN":
                return out
            if r.startswith("@@B "):
                out.append(r[4:])

    def reintentar(self, n):
        return self._pedir(f"reintentar {n}")

    def captura(self, ruta=None):
        r = self._pedir("captura", 15)
        pedazos = {}
        while r and r.startswith("@@P "):
            _, i, total, datos = r.split(" ", 3)
            pedazos[int(i)] = base64.b64decode(datos)
            if len(pedazos) == int(total):
                break
            r = self._linea(5)
        if not pedazos or len(pedazos) != int(total):
            return None
        crudo = b"".join(pedazos[i] for i in range(int(total)))
        img = Image.new("RGB", (284, 76))
        px = img.load()
        for i in range(284 * 76):
            v = crudo[2 * i] << 8 | crudo[2 * i + 1]  # en el lienzo va con los bytes dados vuelta
            px[i % 284, i // 284] = ((v >> 11) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3)
        if ruta:
            img.resize((284 * 3, 76 * 3), Image.NEAREST).save(ruta)
        return img

    def reiniciar(self):
        self._pedir("reiniciar")
        time.sleep(4)

    def errores(self):
        return [l for l in self.registro if re.search(r"^E \(|panic|Guru|abort", l)]
