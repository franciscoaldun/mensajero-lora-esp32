# Escribe firmware-c3/main/secreto.h desde secreto.json (las claves viven SOLO ahí).
# El aparato portátil de él usa su propia red Wi-Fi ("Mensajes 💙", clave fácil pedida por él) y la MISMA clave
# de módulo que la pareja (si no, los LoRa no se entienden).
import json, os

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.join(AQUI, "..")
ruta = os.path.join(RAIZ, "secreto.json")
d = json.load(open(ruta, encoding="utf-8"))
cambio = False
if "wifi_nombre_el" not in d:
    d["wifi_nombre_el"] = "Mensajes 💙"
    cambio = True
if d.get("wifi_clave_el") != "12345678r":
    d["wifi_clave_el"] = "12345678r"
    cambio = True
if cambio:
    json.dump(d, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def c(s):
    """Texto como literal C, con los caracteres no ASCII escapados en UTF-8."""
    out = []
    for ch in s:
        if 32 <= ord(ch) < 127 and ch not in '"\\':
            out.append(ch)
        else:
            out.append("".join(f"\\x{b:02X}" for b in ch.encode("utf-8")))
            out.append('" "')  # corta el literal: así un \x no se come la letra que sigue
    return '"' + "".join(out) + '"'


h = ("// Generado por herramientas/generar_secreto_c3.py desde secreto.json. No editar ni compartir.\n"
     "#pragma once\n"
     f"#define CLAVE_MODULO {int(d['clave_modulo'])}\n"
     f"#define WIFI_NOMBRE {c(d['wifi_nombre_el'])}\n"
     f"#define WIFI_CLAVE {c(d['wifi_clave_el'])}\n"
     f"#define ADMIN_CLAVE {c(d['admin_clave'])}\n")
open(os.path.join(RAIZ, "firmware-c3", "main", "secreto.h"), "w", encoding="utf-8").write(h)
print("secreto.h del C3 listo")
