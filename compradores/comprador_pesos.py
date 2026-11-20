"""
Comprador en pesos (dolarizar): compra la especie en pesos cuando su tipo de cambio para dolarizar
es mejor que el de AL30 para pesificar.

Reemplaza a comprador_v2.py (--plazo 24hs) y comprador_CI.py (--plazo CI).
    - La especie en pesos y la referencia AL30/AL30D se toman en el plazo elegido.
    - La especie en dólares se toma siempre en 24hs.

⚠️ Envía órdenes REALES.

    python compradores/comprador_pesos.py [--plazo 24hs|CI] [--ratio 1.002] [--instrumentos data/instrumentos.csv]
"""

from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Optional, Tuple

from comun.cotizaciones import PLAZO_24HS, PLAZOS, Cotizacion
from comun.rutas import ruta_data
from compradores.base import ARBITER_RATIO, CompradorBase, correr_comprador

TICKER_REFERENCIA = "AL30"
TICKER_REFERENCIA_D = "AL30D"

logger = logging.getLogger(__name__)


def pesos_a_usd(pesos: Optional[Cotizacion], dolar: Optional[Cotizacion]) -> Optional[float]:
    """
    Tipo de cambio para dolarizar: ask en pesos / min(bid en dólares, cierre anterior, mínimo del día).
    None si falta alguno de los datos obligatorios.
    """
    if pesos is None or dolar is None:
        return None
    if pesos.pr_venta is None or dolar.pr_compra is None or dolar.cierre_anterior is None:
        return None
    candidatos = [dolar.pr_compra, dolar.cierre_anterior]
    if dolar.minimo is not None and dolar.minimo > 0:
        candidatos.append(dolar.minimo)
    denominador = min(candidatos)
    if denominador == 0:
        return None
    return pesos.pr_venta / denominador


class CompradorPesos(CompradorBase):
    def __init__(self, *args, plazo: str = PLAZO_24HS, **kwargs):
        super().__init__(*args, **kwargs)
        self.plazo = plazo

    def suscripciones(self) -> List[Tuple[str, str]]:
        return (
            [(TICKER_REFERENCIA, self.plazo), (TICKER_REFERENCIA_D, self.plazo)]
            + [(inst["ticker"], self.plazo) for inst in self.instrumentos]
            + [(inst["tickerD"], PLAZO_24HS) for inst in self.instrumentos]
        )

    def al_actualizar(self, cotizacion: Cotizacion) -> None:
        # Solo se evalúa ante una actualización de la especie en pesos (como los scripts originales)
        if cotizacion.plazo != self.plazo or cotizacion.ticker == TICKER_REFERENCIA:
            return
        inst = self.por_ticker.get(cotizacion.ticker)
        if inst is not None:
            self.evaluar(inst)

    def usd_a_pesos_referencia(self) -> Optional[float]:
        """Tipo de cambio para pesificar con AL30: bid AL30 / ask AL30D."""
        al30 = self.libro.obtener(TICKER_REFERENCIA, self.plazo)
        al30d = self.libro.obtener(TICKER_REFERENCIA_D, self.plazo)
        if (
            al30 is None
            or al30d is None
            or not al30.pr_compra
            or not al30.pr_venta
            or not al30d.pr_compra
            or not al30d.pr_venta
        ):
            return None
        return al30.pr_compra / al30d.pr_venta

    def evaluar(self, inst: Dict) -> Optional[Dict]:
        logger.info("Ejecutando estrategia de arbitraje → comparando ratios USD/pesos.")
        referencia = self.usd_a_pesos_referencia()
        if referencia is None:
            logger.warning("No se pudo calcular ratios para AL30. Verifica datos.")
            return None

        pesos = self.libro.obtener(inst["ticker"], self.plazo)
        dolar = self.libro.obtener(inst["tickerD"], PLAZO_24HS)
        tc = pesos_a_usd(pesos, dolar)
        if tc is None or tc <= 1 or not tc * self.ratio < referencia:
            return None
        assert pesos is not None and dolar is not None and pesos.pr_venta is not None

        logger.info(f"USD A PESOS {TICKER_REFERENCIA}: {referencia:.2f}")
        logger.info(f"PESOS A USD {inst['ticker'].upper()}: {tc:.2f}")
        logger.info(f"{pesos} | {dolar}")

        clave = (inst["ticker"], self.plazo)
        cantidad = self.calcular_cantidad(inst, clave, dolar.si_compra, pesos.si_venta)
        if cantidad is None:
            logger.info(
                f"Oportunidad en {inst['ticker']} descartada: cantidad insuficiente "
                f"(si dólar={dolar.si_compra}, si pesos={pesos.si_venta}, min={inst['min_quant']}, "
                f"cupo 10 min={self.disponible_en_ventana(inst, clave)})"
            )
            return None

        logger.info(f"Ejecutando estrategia para {inst['ticker']} → quant={cantidad}")
        logger.info(f"COMPRAR {inst['ticker']} EN PESOS ({self.plazo})")
        logger.info(f"Se dolarizaría a un precio de {tc:.2f} pesos por dólar")
        return self.comprar(inst["ticker"], self.plazo, cantidad, pesos.pr_venta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Comprador en pesos. ATENCIÓN: envía órdenes REALES.")
    parser.add_argument("--plazo", choices=PLAZOS, default=PLAZO_24HS, help="plazo de la compra en pesos y de AL30")
    parser.add_argument("--ratio", type=float, default=ARBITER_RATIO, help="ratio mínimo contra AL30")
    parser.add_argument("--instrumentos", default=ruta_data("instrumentos.csv"), help="CSV de instrumentos")
    args = parser.parse_args()

    correr_comprador(
        lambda client, account, instrumentos, libro: CompradorPesos(
            client, account, instrumentos, libro, ratio=args.ratio, plazo=args.plazo
        ),
        nombre_log=f"comprador_pesos_{args.plazo}.log",
        ruta_instrumentos=args.instrumentos,
    )


if __name__ == "__main__":
    main()
