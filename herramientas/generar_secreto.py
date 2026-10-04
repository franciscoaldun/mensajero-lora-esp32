# Escribe firmware/main/secreto.h a partir de secreto.json (la clave nunca se escribe a mano en el código).
import json, os

AQUI = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(AQUI, "..", "secreto.json")))
clave = bytes.fromhex(s["clave_mensajes"])
open(os.path.join(AQUI, "..", "firmware", "main", "secreto.h"), "w").write(
    "// Generado por herramientas/generar_secreto.py desde secreto.json. No compartir.\n#pragma once\n#include <stdint.h>\n"
    "static const uint8_t CLAVE_MENSAJES[16] = {" + ", ".join(f"0x{b:02X}" for b in clave) + "};\n")
print("secreto.h listo")
