"""
Comprador de dólares CI: compra la especie en dólares CI cuando su ask está suficientemente por debajo
del bid (y del cierre anterior) de la misma especie en dólares 24hs.

⚠️ Envía órdenes REALES.

    python compradores/comprador_D_CI.py [--ratio 1.002] [--instrumentos data/instrumentos.csv]
"""

from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Optional, Tuple

from comun.cotizaciones import PLAZO_24HS, PLAZO_CI, Cotizacion
from comun.rutas import ruta_data
from compradores.base import ARBITER_RATIO, CompradorBase, correr_comprador

logger = logging.getLogger(__name__)


class CompradorDolarCI(CompradorBase):
    def suscripciones(self) -> List[Tuple[str, str]]:
        return [(inst["tickerD"], PLAZO_24HS) for inst in self.instrumentos] + [
            (inst["tickerD"], PLAZO_CI) for inst in self.instrumentos
        ]

    def al_actualizar(self, cotizacion: Cotizacion) -> None:
        # Solo se evalúa ante una actualización de la especie en dólares CI
        if cotizacion.plazo != PLAZO_CI:
            return
        inst = self.por_tickerD.get(cotizacion.ticker)
        if inst is not None:
            self.evaluar(inst)

    def evaluar(self, inst: Dict) -> Optional[Dict]:
        logger.info("Ejecutando estrategia de arbitraje → comparando dólar CI contra dólar 24hs.")
        ci = self.libro.obtener(inst["tickerD"], PLAZO_CI)
        d24 = self.libro.obtener(inst["tickerD"], PLAZO_24HS)
        if (
            ci is None
            or d24 is None
            or ci.pr_venta is None
            or ci.pr_venta <= 1
            or d24.pr_compra is None
            or d24.pr_compra <= 1
            or d24.cierre_anterior is None
        ):
            return None

        if not ci.pr_venta * self.ratio < min(d24.pr_compra, d24.cierre_anterior):
            return None

        logger.info(f"{ci} | {d24}")
        clave = (inst["tickerD"], PLAZO_CI)
        cantidad = self.calcular_cantidad(inst, clave, d24.si_compra, ci.si_venta)
        if cantidad is None:
            logger.info(
                f"Oportunidad en {inst['tickerD']} descartada: cantidad insuficiente "
                f"(si 24hs={d24.si_compra}, si CI={ci.si_venta}, min={inst['min_quant']}, "
                f"cupo 10 min={self.disponible_en_ventana(inst, clave)})"
            )
            return None

        logger.info(f"Ejecutando estrategia para {inst['ticker']} → quant={cantidad}")
        logger.info(f"COMPRAR {inst['tickerD']} EN DÓLARES CI")
        return self.comprar(inst["tickerD"], PLAZO_CI, cantidad, ci.pr_venta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Comprador de dólares CI. ATENCIÓN: envía órdenes REALES.")
    parser.add_argument("--ratio", type=float, default=ARBITER_RATIO, help="ratio mínimo CI vs 24hs")
    parser.add_argument("--instrumentos", default=ruta_data("instrumentos.csv"), help="CSV de instrumentos")
    args = parser.parse_args()

    correr_comprador(
        lambda client, account, instrumentos, libro: CompradorDolarCI(
            client, account, instrumentos, libro, ratio=args.ratio
        ),
        nombre_log="comprador_D_CI.log",
        ruta_instrumentos=args.instrumentos,
    )


if __name__ == "__main__":
    main()
