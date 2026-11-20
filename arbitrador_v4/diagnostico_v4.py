"""
Diagnóstico en producción, SOLO LECTURA (no envía órdenes: no crea el cliente REST).

Se conecta al WebSocket real, evalúa la estrategia del executer_v4 cada UPDATE_SLEEP_INTERVAL
segundos durante `--segundos` y, para cada oportunidad, registra:
    - cuántos ciclos consecutivos se mantuvo (confirmación de 2 ciclos),
    - si el dimensionamiento la habilita y, si no, por qué (puntas, min_quant, max_quant).
Al final escribe un resumen en results/diagnostico_v4_<fecha>.log.

Uso (desde arbitrador_v4/):  ..\\.venv\\Scripts\\python.exe diagnostico_v4.py --segundos 120
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import datetime

from estrategia import (
    ARBITER_RATIO,
    buscar_oportunidades,
    calcular_consenso,
    calcular_ratios,
    dimensionar_oportunidad,
    motivo_sin_tamano,
)
from executer_v4 import CONFIG_PATH, INSTRUMENTOS_PATH, RESULTS_DIR, UPDATE_SLEEP_INTERVAL
from mercado import DataManager, WebSocketClient, load_instruments

logger = logging.getLogger("arbitrador_v4")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segundos", type=int, default=120)
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    log_path = os.path.join(RESULTS_DIR, f"diagnostico_v4_{datetime.now():%Y%m%d_%H%M%S}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )

    instrumentos = load_instruments(INSTRUMENTOS_PATH)
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    cred = config["credentials"]
    url = (
        f"wss://matriz.cocos.xoms.com.ar/ws?session_id={cred['matriz_session_id']}"
        f"&conn_id={cred['matriz_conn_id']}"
    )
    dm = DataManager(instrumentos)
    ws = WebSocketClient(url, dm, instrumentos)
    ws.connect()
    logger.info(f"Diagnóstico SOLO LECTURA por {args.segundos}s sobre {len(instrumentos)} instrumentos")

    racha = defaultdict(int)          # ciclos consecutivos de cada par
    ciclos = 0
    confirmadas_total = 0
    ejecutables = Counter()
    motivos = {}
    max_racha = Counter()
    fin = time.time() + args.segundos
    try:
        while time.time() < fin:
            time.sleep(UPDATE_SLEEP_INTERVAL)
            snap = dm.get_snapshot()
            calcular_ratios(snap)
            consenso = calcular_consenso(snap)
            ciclos += 1
            con_puntas = sum(1 for i in snap if i.get("pesos_a_USD") or i.get("USD_a_pesos"))
            if consenso is None:
                logger.warning(f"Ciclo {ciclos}: sin consenso (AL30 sin puntas). Con puntas: {con_puntas}")
                continue
            ops = buscar_oportunidades(snap, consenso, ARBITER_RATIO)
            actuales = {op.clave for op in ops}
            for clave in list(racha):
                if clave not in actuales:
                    del racha[clave]
            for op in ops:
                racha[op.clave] += 1
                max_racha[op.clave] = max(max_racha[op.clave], racha[op.clave])
                if racha[op.clave] < 2:
                    continue
                confirmadas_total += 1
                cantidad = dimensionar_oportunidad(op)
                if cantidad > 0:
                    ejecutables[op.clave] += 1
                    logger.info(
                        f"✅ EJECUTABLE {op.clave} ratio {op.ratio:.5f}: comienza "
                        f"{op.modo_primera_pata} {op.primer_instrumento['ticker']} x {cantidad}"
                    )
                elif op.clave not in motivos:
                    motivos[op.clave] = motivo_sin_tamano(op)
                    logger.info(f"⛔ SIN TAMAÑO {op.clave} ratio {op.ratio:.5f}: {motivos[op.clave]}")
            logger.info(
                f"Ciclo {ciclos}: consenso {consenso:.2f} | instrumentos con puntas {con_puntas} | "
                f"oportunidades {len(ops)} | confirmadas {sum(1 for c in actuales if racha[c] >= 2)}"
            )
    finally:
        ws.stop_websocket()

    logger.info("=" * 80)
    logger.info(f"RESUMEN: {ciclos} ciclos | {len(max_racha)} pares distintos con ratio >= {ARBITER_RATIO}")
    logger.info(f"Pares que duraron 1 solo ciclo (no confirman): {sum(1 for v in max_racha.values() if v == 1)}")
    logger.info(f"Pares confirmados (>=2 ciclos): {sum(1 for v in max_racha.values() if v >= 2)}")
    logger.info(f"Pares confirmados y ejecutables: {len(ejecutables)}")
    logger.info(f"Pares confirmados sin tamaño: {len(motivos)}")
    for clave, motivo in motivos.items():
        logger.info(f"   {clave}: {motivo}")
    logger.info(f"Log: {log_path}")


if __name__ == "__main__":
    main()
