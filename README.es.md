# Mensajero LoRa: chat y fotos sin internet con ESP32-S3, ESP32-C3 y un PC

[English](README.md) · **Español**

Por [Francisco Aldunate](https://franciscoaldunate.cl) · Talca, Chile · octubre de 2026

Lo hice para poder hablar con mi pareja a ~10 km **sin internet ni señal de celular**, pensado para emergencias: si se cae la red, igual nos podemos escribir, mandar fotos y saber si el otro está cerca. Su aparato va sellado contra el agua; se conecta al celular por Wi-Fi y cualquier navegador abre el chat. Del otro lado hay un ESP32-C3 de bolsillo o un PC con un módulo LoRa por USB.

Todo el código está aquí para que nadie que quiera algo parecido tenga que partir de cero.

| | | |
|---|---|---|
| <img src="docs/s3_mensaje_texto.png" alt="Pantalla del S3 mostrando un mensaje recibido"> | <img src="docs/s3_foto_recibida.png" alt="Pantalla del S3 avisando que llegó una foto, con miniatura"> | <img src="docs/s3_estado_senal.png" alt="Pantalla del S3 con la última foto, cuánto hace que se oyó al otro y la potencia"> |
| Mensaje recibido en la pantalla del S3 | Llegó una foto (con miniatura) | Estado: hace cuánto se oyó al otro, distancia y potencia |
| <img src="docs/s3_emoji.png" alt="Pantalla del S3 mostrando un emoji"> | <img src="docs/s3_desde_pc.png" alt="Pantalla del S3 con un mensaje enviado desde el PC"> | <img src="docs/c3_oled.png" alt="OLED de 128x32 del ESP32-C3 con señal, mensaje y estado de entrega"> |
| Emojis | Mensaje que llegó desde el PC | El C3 de bolsillo (OLED 128×32): señal 0-100, último mensaje y "todo entregado" |

## Qué hace

- **Chat y fotos en los dos sentidos** por LoRa, sin internet. Fotos en 3 calidades; se achican y se mandan en trozos.
- **La misma página en los tres aparatos**: el S3 y el C3 la sirven por su propia red Wi-Fi (portal cautivo) y el PC la abre en una ventana propia. Cualquier celular sirve, sin instalar nada.
- **"Está cerca"**: arriba se ve "💙 está cerca · ~1,5 km · señal hace 20 s" o "sin señal desde las 14:32". Avisa y vibra cuando el otro vuelve a estar al alcance.
- **Nada se pierde**: lo que no llega se reintenta para siempre, pero solo cuando hay contacto. Lo escrito sin señal sale solo apenas vuelve.
- **Nada se repite**: cada mensaje lleva su número y la hora en que se escribió. Si un acuse se pierde y el mensaje vuelve a llegar días después, se reconoce.
- **Nada es ambiguo**: ✓✓ = llegó · 👀 = lo leyó. Los recibos de lectura también se reintentan.
- **Las fotos cortadas siguen solas**: lo recibido se guarda 24 h y al volver la señal se manda solo lo que falta.
- **Se puede hablar durante una foto**: los mensajes se intercalan entre los trozos.
- **Ráfagas**: varios mensajes en cola salen juntos en un solo paquete con un solo acuse.
- **Control a distancia**: desde un aparato se cambian los ajustes del otro y se le manda la hora por radio.
- **Pensado para ir sellado**: actualización OTA por Wi-Fi con vuelta atrás, vigilante que reinicia si la radio se traba 20 s, e historial atómico en LittleFS.

## Hardware

| Pieza | Aparato fijo (va sellado) | Aparato de bolsillo | Estación en el PC |
|---|---|---|---|
| Microcontrolador | ESP32-S3 N16R8 (16 MB flash, 8 MB PSRAM) | ESP32-C3 SuperMini | — (Python) |
| Radio | DX-LR32-900T22D (SX1262, 22 dBm) por **USB host** (CH340) | DX-LR32 soldado al UART (TX→GPIO7, RX←GPIO6, AUX→5, M0→3, M1→4, VCC→5V) | DX-LR32 + adaptador USB DX-PJ15 |
| Pantalla | ST7789 en color | OLED SSD1306 128×32 (SDA→8, SCK→9) | ventana con la misma página |
| Detalles | Puente OTG soldado; **condensador de 470 µF entre 5V y GND** (sin él, a 22 dBm el S3 se colgaba en ~20 % de las transmisiones) | Pines 2, 20 y 21 libres a propósito | Arranca solo con Windows |

⚠️ Con el puente OTG soldado, el puerto "USB" del S3 **entrega** 5 V: no lo enchufes a un PC ni a un cargador.

## Lo que se aprendió midiendo (para que no te pase)

- **El DX-LR32 retiene los paquetes de 31, 63, 95… bytes** (largo % 32 == 31) y no los transmite hasta que le llega otro. Un mensaje de 20 letras medía justo 31. Arreglo: un byte 0x00 al principio (`lora.c`, `radio_pc.py`).
- **Choques**: si los dos hablan a la vez ninguno oye. Con la misma espera vuelven a chocar; hace falta una espera al azar creciente, mayor que el tiempo en el aire. Turnos por reloj, nunca: con los relojes desfasados chocarían siempre.
- **Un latido de 13 bytes tarda 0,725 s en el aire** en el nivel de más alcance (SF11/125 kHz). Buscar al otro más seguido que cada ~3 s empeora el encuentro. Lo óptimo medido en simulación es cada ~4 s con azar de ±50 % (`herramientas/simular_encuentro.py`).
- **Antes de escribirle al módulo hay que esperar AUX en bajo**: escribiéndole mientras recibía se perdía un byte.
- **Grabar el historial completo traba la radio** por segundos: se graba en una tarea aparte. Con eso, dos personas mandando a la vez entregan en ~6-7 s promedio, antes minutos (`herramientas/prueba_turnos2.py`).
- **La página manda los campos urlencoded**: hay que decodificarlos antes de guardar, o la red pasa a llamarse `Mensajes%20%F0…`.
- **"Interrupt WDT" al transmitir a 22 dBm** con la pantalla soldada: `CONFIG_ESP_INT_WDT_TIMEOUT_MS=3000`, Wi-Fi a 8,5 dBm y pantalla atenuada al transmitir.
- **Barrido de 18.431 comandos AT**: no hay nada oculto; LEVEL0 + 22 dBm es el techo del LR32. `AT+KEY` es un revoltijo fijo, no cifrado.

## Cómo usarlo

1. Instala [ESP-IDF](https://docs.espressif.com/projects/esp-idf/) v6.x.
2. Copia `secreto.example.json` como `secreto.json` y pon tus claves (red Wi-Fi de cada aparato y clave del módulo).
3. Genera los `secreto.h` (nunca se suben al repo):
   ```
   python herramientas/generar_secreto_s3.py
   python herramientas/generar_secreto_c3.py
   ```
4. Compila y graba:
   ```
   cd firmware-s3 && idf.py build && idf.py -p COMx flash
   cd firmware-c3 && idf.py build && idf.py -p COMy flash
   ```
5. En el PC (opcional): `python servidor_pc.py` abre la misma página y usa el módulo LoRa por USB. No uses el C3 y el PC a la vez: los dos serían la misma persona.

## Herramientas de prueba (`herramientas/`)

- `simular_v2.py`: dos radios en un aire virtual (pérdidas, choques semidúplex, cortes, niveles), con el tiempo ×40 y 12 escenarios.
- `banco_v2.py`: 20 pruebas reales con fallas provocadas: fuera de alcance, acuses descartados, reinicio en mitad de una foto.
- `prueba_turnos2.py`: mide la entrega mientras los dos mandan a la vez.
- `simular_encuentro.py`: cuánto tardan en reencontrarse según cada cuánto se buscan.
- `esfuerzo_tx.py` y `prueba_potencia.py`: cuelgues y consumo transmitiendo a 22 dBm.
- `s3.py`: consola del S3 por USB (estado, lista, bitácora de los últimos 48 paquetes).

## Estado

En uso diario entre los dos aparatos y el PC. Faltan medir la batería completa de los dos aparatos y probar el reencuentro alejándose de verdad. Mientras eso no esté medido, no lo vendas como aparato de emergencias certificado.

## Autor

**Francisco Aldunate Rodríguez** · Talca, Chile · Firmware ESP32 (P4, S3, C3)
Portafolio: **[franciscoaldunate.cl](https://franciscoaldunate.cl)** · GitHub: [@franciscoaldun](https://github.com/franciscoaldun)

## Licencia

[MIT](LICENSE). Revisa la normativa de tu país para la banda de 900 MHz y la potencia (en Chile y en América se usa 915 MHz).
