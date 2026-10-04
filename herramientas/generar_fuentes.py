# Genera firmware/main/fuentes.c: letras con suavizado (4 bits) y emojis a color para la pantallita.
# Los emojis van ya mezclados sobre el color de fondo de la pantalla (FONDO), así se dibujan rápido.
import os
from PIL import Image, ImageDraw, ImageFont

AQUI = os.path.dirname(os.path.abspath(__file__))
import sys
# uso: generar_fuentes.py [s3]  -> el aparato S3 tiene otro fondo y otro destino
MODO = sys.argv[1] if len(sys.argv) > 1 else ""   # "s3" = aparato de ella · "c3" = aparato portátil de él
S3 = MODO in ("s3", "c3")
SALIDA = os.path.join(AQUI, "..", {"s3": "firmware-s3", "c3": "firmware-c3"}.get(MODO, "firmware"), "main", "fuentes.c")
FONDO = (14, 16, 38) if S3 else (18, 14, 26)  # igual que COLOR_FONDO en pantalla.h

# letras de los emojis japoneses (kaomoji) que se mandan desde la app: (｡♥‿♥｡) ʕ•ᴥ•ʔ (◕‿◕✿) ヽ(♡‿♡)ノ ...
KAOMOJI = "｡‿ʕᴥʔ◕✿≧◡≦♡ヽノづ˘ᵕᵔ╥﹏−っωςﾉヮ･ﾟ✧◠▿◝◜つ▽⁰⌣˶∀∩ˆ・゜♬✰❀◦∗≖◔◉⊂⊃╯╰□︵┻━┳ಥ益﹃ε"
LETRAS = ([chr(c) for c in range(32, 127)] + [chr(c) for c in range(160, 256)] + list("‘’“”…–—€•♥★☆✓✔✗✘♪☺↑↓→←»«·")
          + list(dict.fromkeys(KAOMOJI)))
EMOJIS = list(dict.fromkeys("📷💗💙🥺🌷⭐❤💜💕💖😘😍🥰😊😂😭😴👍🙏✨🌙☀🐱🐶🌸🔥🎉😢😡😮🤗💋"
                            "👀💞💓💘💝🤍🩷🩵🫶🫂🥹😅😇🤭🥲😌🙈🐰🌈🍓🦋🌹💐🏠🚗💌📡📱🔋⚡😚😙😋😜🤔😱🙌👋💪🎂🍕☕🌧🌞"))
RESPALDO = ["seguisym.ttf", "YuGothM.ttc", "msgothic.ttc", "malgun.ttf", "msjh.ttc"]  # si la principal no la tiene

FUENTES = [  # nombre, archivo, alto en px
    ("fuente_grande", "seguisb.ttf", 26),
    ("fuente_media", "seguisb.ttf", 19),
    ("fuente_chica", "segoeui.ttf", 13),
]


def ruta(f):
    return os.path.join(r"C:\Windows\Fonts", f)


def la_tiene(fuente, ch):
    """Una fuente que NO tiene la letra dibuja su "cuadradito" (o nada): eso salía en la pantallita como □□."""
    try:
        m = fuente.getmask(ch)
    except Exception:
        return False
    if m.getbbox() is None:
        return False
    nada = fuente.getmask("\U000F0000")  # privado: ninguna fuente lo tiene, sale el cuadradito
    return not (m.size == nada.size and bytes(m) == bytes(nada))


SIN_SUAVIZAR = False  # para la OLED en blanco y negro: letras de 1 bit (nítidas), no grises


def glifo_letra(fuente, respaldos, ch):
    if ch != " " and not la_tiene(fuente, ch):
        fuente = next((f for f in respaldos if la_tiene(f, ch)), None)
        if fuente is None:
            return None  # ninguna la tiene: mejor nada que un cuadradito
    try:
        caja = fuente.getbbox(ch)
    except Exception:
        return None
    adv = round(fuente.getlength(ch))
    if caja[2] <= caja[0] or caja[3] <= caja[1]:
        return dict(w=0, h=0, x=0, y=0, adv=adv, color=0, datos=b"")
    w, h = caja[2] - caja[0], caja[3] - caja[1]
    img = Image.new("L", (w, h), 0)
    dib = ImageDraw.Draw(img)
    if SIN_SUAVIZAR:
        dib.fontmode = "1"
    dib.text((-caja[0], -caja[1]), ch, font=fuente, fill=255)
    px = list(img.getdata())
    if len(px) % 2:
        px.append(0)
    datos = bytes(((px[i] >> 4) << 4) | (px[i + 1] >> 4) for i in range(0, len(px), 2))
    return dict(w=w, h=h, x=caja[0], y=caja[1], adv=adv, color=0, datos=datos)


def glifo_emoji(alto, ascenso, ch):
    # Segoe UI Emoji a color se dibuja bien a 109 px; se achica con buen filtro
    grande = ImageFont.truetype(ruta("seguiemj.ttf"), 109)
    if not la_tiene(grande, ch):  # emoji que Windows no tiene: no se guarda un cuadradito
        return None
    # lienzo con margen de sobra: en uno chico se cortaba la parte de arriba de las caritas (😘🥰💗)
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((60, 60), ch, font=grande, embedded_color=True)
    caja = img.getbbox()
    if caja is None:
        return None
    img = img.crop(caja)
    lado = round(alto * 0.95)
    img = img.resize((lado, max(1, round(img.height * lado / img.width))), Image.LANCZOS)
    fondo = Image.new("RGBA", img.size, FONDO + (255,))
    fondo.alpha_composite(img)
    datos = bytearray()
    for r, g, b, _ in fondo.getdata():
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        datos += bytes([v >> 8, v & 0xFF])  # big endian, como lo quiere el ST7789
    y = ascenso - img.height + round(alto * 0.12)
    return dict(w=img.width, h=img.height, x=1, y=y, adv=img.width + 2, color=1, datos=bytes(datos))


def main():
    out = ['// Generado por herramientas/generar_fuentes.py. No editar a mano.',
           '#include "fuentes.h"', ""]
    global SIN_SUAVIZAR
    fuentes = FUENTES + ([("fuente_mini", "tahoma.ttf", 10)] if MODO == "c3" else [])
    for nombre, archivo, alto in fuentes:
        SIN_SUAVIZAR = nombre == "fuente_mini"
        fuente = ImageFont.truetype(ruta(archivo), alto)
        respaldos = [ImageFont.truetype(ruta(f), alto) for f in RESPALDO if os.path.exists(ruta(f))]
        ascenso, descenso = fuente.getmetrics()
        glifos = []
        for ch in LETRAS:
            g = glifo_letra(fuente, respaldos, ch)
            if g:
                glifos.append((ord(ch), g))
        for ch in EMOJIS:
            g = glifo_emoji(alto, ascenso, ch)
            if g:
                glifos.append((ord(ch), g))
        glifos.sort()
        datos, filas, pos = bytearray(), [], 0
        for cp, g in glifos:
            filas.append(f"    {{0x{cp:X}, {pos}, {g['w']}, {g['h']}, {g['x']}, {g['y']}, {g['adv']}, {g['color']}}},")
            datos += g["datos"]
            pos += len(g["datos"])
        out.append(f"static const uint8_t {nombre}_datos[{max(len(datos), 1)}] = {{")
        for i in range(0, len(datos), 24):
            out.append("    " + ",".join(str(b) for b in datos[i:i + 24]) + ",")
        out.append("};")
        out.append(f"static const glifo_t {nombre}_glifos[] = {{")
        out += filas
        out.append("};")
        out.append(f"const fuente_t {nombre} = {{{nombre}_glifos, {len(glifos)}, {nombre}_datos, "
                   f"{ascenso + descenso}, {ascenso}}};")
        out.append("")
        print(f"{nombre}: {len(glifos)} glifos, {len(datos) // 1024} KB")
    open(SALIDA, "w", encoding="utf-8").write("\n".join(out))


if __name__ == "__main__":
    main()
