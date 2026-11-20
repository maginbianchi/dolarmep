"""
Modelo único de cotización y libro de cotizaciones compartido por todos los scripts.

Cada cotización se identifica por (ticker, plazo). Así una especie en CI y la misma en 24hs
nunca se pisan entre sí. Una punta sin datos es None (los scripts viejos usaban -100).
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

PLAZO_24HS = "24hs"
PLAZO_CI = "CI"
PLAZOS = (PLAZO_24HS, PLAZO_CI)

PREFIJO_MATRIZ = "M:bm_MERV_"

# Posiciones de los campos en un mensaje de Matriz:
#   M:bm_MERV_<TICKER>_<PLAZO>|?|size bid|precio bid|precio ask|size ask|...|mínimo(11)|...|cierre anterior(15)
IDX_SI_COMPRA = 2
IDX_PR_COMPRA = 3
IDX_PR_VENTA = 4
IDX_SI_VENTA = 5
IDX_MINIMO = 11
IDX_CIERRE_ANTERIOR = 15

Clave = Tuple[str, str]


@dataclass(frozen=True)
class Cotizacion:
    """Puntas de una especie en un plazo. Compra = bid, Venta = ask."""

    ticker: str
    plazo: str
    pr_compra: Optional[float] = None
    pr_venta: Optional[float] = None
    si_compra: Optional[float] = None
    si_venta: Optional[float] = None
    minimo: Optional[float] = None
    cierre_anterior: Optional[float] = None


def a_float(valor) -> Optional[float]:
    """Convierte un campo a float; vacío, None o inválido → None."""
    if valor in ("", None):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def parsear_topic(topic: str) -> Optional[Clave]:
    """'M:bm_MERV_AL30_24hs' → ('AL30', '24hs'). Devuelve None si no es un topic de Matriz conocido."""
    if not topic.startswith(PREFIJO_MATRIZ):
        return None
    ticker, sep, plazo = topic.removeprefix(PREFIJO_MATRIZ).rpartition("_")
    if not sep or not ticker or plazo not in PLAZOS:
        return None
    return ticker, plazo


def parsear_registro(raw: str) -> Optional[Cotizacion]:
    """Convierte un registro de market data de Matriz en una Cotizacion (None si no corresponde)."""
    valores = str(raw).split("|")
    if len(valores) <= IDX_SI_VENTA:
        return None
    clave = parsear_topic(valores[0])
    if clave is None:
        return None

    def campo(idx: int) -> Optional[float]:
        return a_float(valores[idx]) if len(valores) > idx else None

    return Cotizacion(
        ticker=clave[0],
        plazo=clave[1],
        pr_compra=campo(IDX_PR_COMPRA),
        pr_venta=campo(IDX_PR_VENTA),
        si_compra=campo(IDX_SI_COMPRA),
        si_venta=campo(IDX_SI_VENTA),
        minimo=campo(IDX_MINIMO),
        cierre_anterior=campo(IDX_CIERRE_ANTERIOR),
    )


class LibroCotizaciones:
    """Última cotización de cada (ticker, plazo). Thread-safe: el WebSocket escribe y el main lee."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cotizaciones: Dict[Clave, Cotizacion] = {}

    def actualizar(self, cotizacion: Cotizacion) -> None:
        """Reemplaza la cotización completa (los mensajes de Matriz traen todas las puntas)."""
        with self._lock:
            self._cotizaciones[(cotizacion.ticker, cotizacion.plazo)] = cotizacion

    def actualizar_campos(self, ticker: str, plazo: str, **campos) -> Cotizacion:
        """Actualiza solo los campos recibidos y conserva el resto (los mensajes de Primary son parciales)."""
        with self._lock:
            actual = self._cotizaciones.get((ticker, plazo)) or Cotizacion(ticker, plazo)
            nueva = dataclasses.replace(actual, **campos)
            self._cotizaciones[(ticker, plazo)] = nueva
            return nueva

    def obtener(self, ticker: str, plazo: str) -> Optional[Cotizacion]:
        with self._lock:
            return self._cotizaciones.get((ticker, plazo))

    def snapshot(self) -> Dict[Clave, Cotizacion]:
        """Copia consistente del libro (las Cotizacion son inmutables, alcanza con copiar el dict)."""
        with self._lock:
            return dict(self._cotizaciones)
