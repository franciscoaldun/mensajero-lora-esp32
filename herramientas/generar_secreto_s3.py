# Escribe firmware-s3/main/secreto.h desde secreto.json (las claves nunca se escriben a mano en el código).
import json, os

AQUI = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(AQUI, "..", "secreto.json"), encoding="utf-8"))


def c(texto):
    return '"' + "".join(f"\\x{b:02x}" for b in texto.encode("utf-8")) + '"'


open(os.path.join(AQUI, "..", "firmware-s3", "main", "secreto.h"), "w", encoding="utf-8").write(
    "// Generado por herramientas/generar_secreto_s3.py desde secreto.json. No compartir.\n#pragma once\n"
    f"#define CLAVE_MODULO {int(s['clave_modulo'])}u\n"
    f"#define WIFI_NOMBRE {c(s['wifi_nombre'])}\n"
    f"#define WIFI_CLAVE {c(s['wifi_clave'])}\n"
    f"#define ADMIN_CLAVE {c(s['admin_clave'])}\n")
print("secreto.h del S3 listo")
