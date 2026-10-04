# BORRADOR, NO APLICAR TAL CUAL (04-oct): el plan final para M0/M1 es dejarlos SUELTOS (sin resistencia del C3) como
# sensor de vida + rescate si el módulo quedara en SWITCH1 dormido. Esto usaba resistencia hacia arriba.

# C3: M0/M1 como "sensor de vida" del LoRa. Medido el 03-oct: con AT+SWITCH0 el módulo NO maneja M0/M1 (sólo
# tienen una resistencia débil hacia arriba adentro: sin nada en el C3 se leen en 1; con una hacia abajo en el C3,
# en 0). Con la del C3 hacia ARRIBA: LoRa con energía -> 1; LoRa sin energía -> el pin cae (lo tira su protección
# interna) -> 0. Si los dos caen más de 3 s, se revisa el LoRa al instante (si no responde, "⚠ sin radio").
# Una lectura equivocada sólo provoca una revisión de más (inofensiva).
p = r"C:\Users\USUARIO\lora-mensajes\firmware-c3\main\lora.c"
s = open(p, encoding="utf-8").read()
cambios = [
('''    // M0, M1 y AUX sólo se leen (M0/M1 los maneja el propio LoRa mientras esté en AT+SWITCH0)
    const gpio_config_t g = {.pin_bit_mask = (1ULL << PIN_M0) | (1ULL << PIN_M1) | (1ULL << PIN_AUX),
                             .mode = GPIO_MODE_INPUT, .pull_up_en = 0, .pull_down_en = 1, .intr_type = GPIO_INTR_DISABLE};
    gpio_config(&g);''',
 '''    // M0 y M1 sólo se LEEN (con AT+SWITCH0 el LoRa no los usa): con resistencia hacia arriba en el C3 quedan en 1
    // mientras el LoRa tenga energía. AUX con resistencia hacia abajo: sin LoRa, "libre" (no traba los envíos).
    const gpio_config_t g = {.pin_bit_mask = (1ULL << PIN_M0) | (1ULL << PIN_M1), .mode = GPIO_MODE_INPUT,
                             .pull_up_en = 1, .pull_down_en = 0, .intr_type = GPIO_INTR_DISABLE};
    gpio_config(&g);
    const gpio_config_t ga = {.pin_bit_mask = 1ULL << PIN_AUX, .mode = GPIO_MODE_INPUT, .pull_up_en = 0,
                              .pull_down_en = 1, .intr_type = GPIO_INTR_DISABLE};
    gpio_config(&ga);'''),
('''    xTaskCreate(tarea_arranque, "lora_ini", 4096, NULL, 5, NULL);''',
 '''    xTaskCreate(tarea_arranque, "lora_ini", 4096, NULL, 5, NULL);
    xTaskCreate(tarea_vida, "lora_vida", 3072, NULL, 4, NULL);'''),
('''static void tarea_arranque(void *arg)''',
 '''// M0/M1 en 0 los dos por más de 3 s = el LoRa probablemente se quedó sin energía: revisarlo al tiro
// (como máximo una vez cada 30 s). Mientras está en modo AT (o recién salió) no se mira: el módulo se reinicia.
static int64_t fin_at_ms;
static void tarea_vida(void *arg)
{
    int bajos_ms = 0;
    int64_t ultima_revision = 0;
    bool avisado = false;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(500));
        if (!listo || en_modo_at || ms_ahora() - fin_at_ms < 3000) {
            bajos_ms = 0;
            continue;
        }
        bool caidos = !gpio_get_level(PIN_M0) && !gpio_get_level(PIN_M1);
        bajos_ms = caidos ? bajos_ms + 500 : 0;
        if (bajos_ms >= 3000 && ms_ahora() - ultima_revision > 30000) {
            ultima_revision = ms_ahora();
            if (!avisado && suceso) suceso('!', "M0/M1 caidos: reviso", 0);
            avisado = true;
            ESP_LOGW(TAG, "M0 y M1 en 0: ¿el LoRa se quedó sin energía? lo reviso");
            lora_revisar_configuracion();
        }
        if (!caidos) avisado = false;
    }
}

static void tarea_arranque(void *arg)'''),
('''static void salir_at(void)
{
    char r[96];
    at_preguntar("+++\\r\\n", r, sizeof(r), 2500, "Power on");  // el módulo se reinicia con lo nuevo
    vTaskDelay(pdMS_TO_TICKS(300));  // que termine de arrancar antes de darle un paquete
}''',
 '''static int64_t fin_at_ms;
static void salir_at(void)
{
    char r[96];
    at_preguntar("+++\\r\\n", r, sizeof(r), 2500, "Power on");  // el módulo se reinicia con lo nuevo
    vTaskDelay(pdMS_TO_TICKS(300));  // que termine de arrancar antes de darle un paquete
    fin_at_ms = ms_ahora();
}'''),
]
for viejo, nuevo in cambios:
    assert s.count(viejo) == 1, (s.count(viejo), viejo[:80])
    s = s.replace(viejo, nuevo)
# la segunda declaración de fin_at_ms (dentro del bloque de la tarea) sobra: queda la de salir_at
s = s.replace('''// (como máximo una vez cada 30 s). Mientras está en modo AT (o recién salió) no se mira: el módulo se reinicia.
static int64_t fin_at_ms;
''', '''// (como máximo una vez cada 30 s). Mientras está en modo AT (o recién salió) no se mira: el módulo se reinicia.
''')
open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
