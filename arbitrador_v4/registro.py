"""
Registro de resultados en CSV (carpeta results/):

    pnl_diario.csv   Una fila por arbitraje cuya pata 1 convirtió algo. Incluye el P&L en pesos
                     de la operación y el acumulado del día (se reinicia al cambiar la fecha y se
                     recupera del archivo si el bot se reinicia en el mismo día).

    pendientes.csv   Todo lo que requiere atención del operador:
                       - DESHACER_ACTIVA:     orden de venta del remanente que quedó viva en mercado.
                       - DESHACER_ERROR:      no se pudo enviar la orden de deshacer (acción manual).
                       - CIERRE_INCOMPLETO:   la pata 2 no cubrió todo el monto de la pata 1.
                       - ORDEN_NO_CONFIRMADA: una orden de arbitraje no llegó a un estado final
                                              después de cancelarla (posible orden huérfana).

Cálculo del P&L (en ARS, bruto, sin comisiones ni derechos de mercado):
    - Pata 1 DOLARIZAR: compré USD a tc1 y los vendí en la pata 2 a tc2 → USD_cerrados × (tc2 − tc1)
    - Pata 1 PESIFICAR: vendí USD a tc1 y los recompré en la pata 2 a tc2 → USD_cerrados × (tc1 − tc2)
    Es equivalente a valuar el remanente sin cerrar al TC de la pata 1 (no genera P&L).
    Las órdenes de deshacer se envían a precio de compra, por lo que no suman P&L.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from typing import Callable, Optional

from estrategia import DOLARIZAR

logger = logging.getLogger("arbitrador_v4")

PNL_COLUMNS = [
    "fecha",
    "hora",
    "arbitraje_id",
    "estado",
    "modo_pata1",
    "instrumento_pata1",
    "instrumentos_pata2",
    "tc_pata1",
    "tc_pata2",
    "usd_pata1",
    "usd_cerrados",
    "usd_sin_cerrar",
    "pnl_ars",
    "pnl_acumulado_dia",
    "operaciones_dia",
    "ordenes_deshacer",
]

PENDIENTES_COLUMNS = [
    "timestamp",
    "arbitraje_id",
    "tipo",
    "pata",
    "ticker",
    "side",
    "cantidad",
    "precio",
    "moneda",
    "monto",
    "cl_ord_id",
    "detalle",
]


def calcular_pnl(modo_pata1: str, tc1: float, tc2: Optional[float], usd_cerrados: float) -> float:
    """P&L en pesos de la porción cerrada del arbitraje (ver docstring del módulo)."""
    if not tc2 or usd_cerrados <= 0:
        return 0.0
    if modo_pata1 == DOLARIZAR:
        return usd_cerrados * (tc2 - tc1)
    return usd_cerrados * (tc1 - tc2)


def _append_csv(path: str, columnas: list, fila: dict) -> None:
    """Agrega una fila al CSV, escribiendo el encabezado si el archivo es nuevo."""
    nuevo = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        if nuevo:
            writer.writeheader()
        writer.writerow(fila)


class RegistroPnL:
    """Acumula el P&L del día y lo persiste en pnl_diario.csv."""

    def __init__(self, path: str, reloj: Callable[[], datetime] = datetime.now):
        self.path = path
        self._reloj = reloj
        self.fecha = reloj().strftime("%Y-%m-%d")
        self.acumulado, self.operaciones = self._cargar_del_dia(self.fecha)

    def _cargar_del_dia(self, fecha: str):
        """Recupera el acumulado del día desde el CSV (si el bot se reinició en el mismo día)."""
        if not os.path.exists(self.path):
            return 0.0, 0
        acumulado, operaciones = 0.0, 0
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("fecha") == fecha:
                    try:
                        acumulado += float(row.get("pnl_ars") or 0)
                    except ValueError:
                        continue
                    operaciones += 1
        if operaciones:
            logger.info(f"P&L recuperado del día {fecha}: {acumulado:+,.2f} ARS en {operaciones} ops")
        return acumulado, operaciones

    def registrar(
        self,
        arbitraje_id: str,
        estado: str,
        modo_pata1: str,
        instrumento_pata1: str,
        instrumentos_pata2: str,
        tc1: float,
        tc2: Optional[float],
        usd_pata1: float,
        usd_cerrados: float,
        usd_sin_cerrar: float,
        ordenes_deshacer: str = "",
    ) -> float:
        """
        Calcula el P&L de un arbitraje, lo suma al acumulado del día y lo escribe en el CSV.
            usd_pata1:      USD convertidos en la pata 1.
            usd_cerrados:   USD convertidos en la pata 2 (base del P&L).
            usd_sin_cerrar: monto libre de la pata 1 que la pata 2 no pudo cerrar, en USD.
            ordenes_deshacer: clOrdIds de las ventas de remanentes que quedaron vivas.
        """
        ahora = self._reloj()
        hoy = ahora.strftime("%Y-%m-%d")
        if hoy != self.fecha:  # cambio de día con el bot corriendo → reinicio del acumulado
            self.fecha, self.acumulado, self.operaciones = hoy, 0.0, 0

        pnl = calcular_pnl(modo_pata1, tc1, tc2, usd_cerrados)
        self.acumulado += pnl
        self.operaciones += 1

        _append_csv(
            self.path,
            PNL_COLUMNS,
            {
                "fecha": hoy,
                "hora": ahora.strftime("%H:%M:%S"),
                "arbitraje_id": arbitraje_id,
                "estado": estado,
                "modo_pata1": modo_pata1,
                "instrumento_pata1": instrumento_pata1,
                "instrumentos_pata2": instrumentos_pata2,
                "tc_pata1": f"{tc1:.4f}",
                "tc_pata2": f"{tc2:.4f}" if tc2 else "",
                "usd_pata1": f"{usd_pata1:.2f}",
                "usd_cerrados": f"{usd_cerrados:.2f}",
                "usd_sin_cerrar": f"{usd_sin_cerrar:.2f}",
                "pnl_ars": f"{pnl:.2f}",
                "pnl_acumulado_dia": f"{self.acumulado:.2f}",
                "operaciones_dia": self.operaciones,
                "ordenes_deshacer": ordenes_deshacer,
            },
        )
        logger.info(
            f"💰 P&L {arbitraje_id} [{estado}]: {pnl:+,.2f} ARS sobre USD {usd_cerrados:.2f} | "
            f"Acumulado del día ({self.operaciones} ops): {self.acumulado:+,.2f} ARS"
        )
        return pnl


class RegistroPendientes:
    """Escribe en pendientes.csv todo lo que el operador debe revisar o gestionar a mano."""

    def __init__(self, path: str, reloj: Callable[[], datetime] = datetime.now):
        self.path = path
        self._reloj = reloj

    def registrar(
        self,
        arbitraje_id: str,
        tipo: str,
        pata: int,
        ticker: str = "",
        side: str = "",
        cantidad: Optional[float] = None,
        precio: Optional[float] = None,
        moneda: str = "",
        monto: Optional[float] = None,
        cl_ord_id: str = "",
        detalle: str = "",
    ) -> None:
        _append_csv(
            self.path,
            PENDIENTES_COLUMNS,
            {
                "timestamp": self._reloj().isoformat(timespec="seconds"),
                "arbitraje_id": arbitraje_id,
                "tipo": tipo,
                "pata": pata,
                "ticker": ticker,
                "side": side,
                "cantidad": "" if cantidad is None else cantidad,
                "precio": "" if precio is None else precio,
                "moneda": moneda,
                "monto": "" if monto is None else f"{monto:.2f}",
                "cl_ord_id": cl_ord_id,
                "detalle": detalle,
            },
        )
