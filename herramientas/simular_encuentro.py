# ¿Cuánto tardan en encontrarse los dos aparatos al entrar al alcance? (03-oct, para decidir la búsqueda rápida)
# Los dos escuchan siempre (SLEEP2). Cada uno grita un latido cada P s con azar ±J·P (como programar_latido).
# Un latido = 13 bytes a LEVEL0 (SF11/125 kHz/CR 4/8) = 0,725 s en el aire (ms_en_aire). Semi-dúplex: mientras
# uno transmite no oye; si los dos transmiten a la vez se pierden los dos. Al oír un latido que dice "no te oigo"
# se contesta en 0,3-1,8 s, como máximo una vez cada R s. "Encontrados" = los dos oyeron al otro.
# q = probabilidad de que un paquete llegue (1 = señal buena; 0,5 y 0,2 = borde del alcance).
# Hallazgos: con ±20 % de azar los dos se "sincronizan" en los choques (cola larga); con ±50 % no. Gritar más
# seguido que ~cada 3 s empeora (choques). Lo óptimo ronda cada 4-5 s.
#   python herramientas/simular_encuentro.py
import random
import statistics as st

T = 0.725


def una(P, J, q, R, rng):
    prox = [rng.uniform(0, P[0] * (1 + J[0])), rng.uniform(0, P[1] * (1 + J[1]))]
    oyo = [None, None]
    tx = [[], []]
    resp = [-1e9, -1e9]
    while True:
        i = 0 if prox[0] <= prox[1] else 1
        t = prox[i]
        if t > 900 or (oyo[0] is not None and oyo[1] is not None):
            break
        tx[i].append(t)
        prox[i] = t + rng.uniform(P[i] * (1 - J[i]), P[i] * (1 + J[i]))
        j = 1 - i
        choca = any(abs(s - t) < T for s in tx[j][-3:]) or abs(prox[j] - t) < T
        if not choca and rng.random() < q:
            if oyo[j] is None:
                oyo[j] = t + T
            if oyo[i] is None and t - resp[j] > R:  # i todavía no oye a j: j le contesta al tiro
                resp[j] = t
                pronto = t + T + rng.uniform(0.3, 1.8)
                if pronto < prox[j]:
                    prox[j] = pronto
    if None in oyo:
        return 900
    return max(oyo)


def resumen(nombre, P, J, q, R, n=20000):
    rng = random.Random(5)
    a = sorted(una(P, J, q, R, rng) for _ in range(n))
    p = lambda x: a[int(len(a) * x)]
    print(f"{nombre:46s} prom {st.mean(a):5.1f}  mediana {p(0.5):5.1f}  p90 {p(0.9):5.1f}  p99 {p(0.99):5.1f}  "
          f"peor {a[-1]:5.1f} s")


if __name__ == "__main__":
    for q, borde in ((1.0, "señal buena"), (0.5, "borde (1 de 2 llega)"), (0.2, "muy al borde (1 de 5)")):
        print("---", borde)
        resumen("hoy: 20 s ±20%, contesta c/20 s", (20, 20), (0.2, 0.2), q, 20)
        resumen("normal: 5 s ±50%, contesta c/4 s", (5, 5), (0.5, 0.5), q, 4)
        resumen("normal: 4 s ±50%, contesta c/4 s", (4, 4), (0.5, 0.5), q, 4)
        resumen("ahorro los dos: 15 s ±50%, contesta c/4 s", (15, 15), (0.5, 0.5), q, 4)
        resumen("ella ahorro 15 s · él normal 5 s, c/4 s", (5, 15), (0.5, 0.5), q, 4)
