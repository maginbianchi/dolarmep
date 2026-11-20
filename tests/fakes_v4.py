"""
Dobles de prueba: un broker simulado (FakeClient) y un DataManager con snapshots programables.

FakeClient simula la Primary API:
    - `comportamiento[(ticker, side)]` define cuánto se ejecuta cada orden al enviarla:
        float 0..1 → fracción de la cantidad; int → nominales; "reject" → REJECTED;
        "error" → falla el envío. Puede ser una lista para consumir un valor por orden.
      Por defecto toda orden se ejecuta completa al precio límite.
    - `fills_tardios[(ticker, side)] = n`: nominales extra que se ejecutan al cancelar
      (simula la carrera fill vs. cancel).
    - `no_cancelables`: tickers cuyas cancelaciones nunca se confirman.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

from mercado import create_instrument


def inst(
    ticker: str,
    tc_dol: float,
    tc_pes: float,
    px_usd: float = 100.0,
    size: float = 1000,
    min_q: int = 1,
    max_q: int = 1000,
    tickerD: Optional[str] = None,
) -> Dict:
    """
    Construye un instrumento con las puntas necesarias para obtener los TC pedidos:
        prVentaPesos / prCompraDolar = tc_dol     prCompraPesos / prVentaDolar = tc_pes
    El precio en USD es `px_usd` (bid y ask iguales, para que las cuentas sean simples).
    """
    # Convención BYMA: ONs "XXXXO" → "XXXXD"; bonos "AL30" → "AL30D"
    if tickerD is None:
        tickerD = ticker[:-1] + "D" if ticker.endswith("O") else ticker + "D"
    i = create_instrument(ticker, tickerD, min_q, max_q)
    i.update(
        prCompraDolar=px_usd,
        prVentaDolar=px_usd,
        prVentaPesos=round(tc_dol * px_usd, 6),
        prCompraPesos=round(tc_pes * px_usd, 6),
        siCompraPesos=size,
        siVentaPesos=size,
        siCompraDolar=size,
        siVentaDolar=size,
    )
    return i


def al30(size: float = 5000, max_q: int = 5000) -> Dict:
    """AL30 con un MEP ~1416.9 de consenso (dolarización 1418, pesificación 1415.8)."""
    return inst("AL30", 1418.0, 1415.8, px_usd=60.0, size=size, max_q=max_q)


class FakeClient:
    FINALES = ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")

    def __init__(self, comportamiento: Optional[Dict] = None):
        self.comportamiento = dict(comportamiento or {})
        self.fills_tardios: Dict = {}
        self.no_cancelables: set = set()
        self.orders: Dict[str, Dict] = {}
        self.enviadas: List[Dict] = []
        self.cancelaciones: List[str] = []
        self._n = 0

    @staticmethod
    def _ticker(symbol: str) -> str:
        return symbol.split(" - ")[2]

    def _spec(self, ticker: str, side: str):
        spec = self.comportamiento.get((ticker, side), 1.0)
        if isinstance(spec, list):
            return spec.pop(0) if spec else 1.0
        return spec

    # ---- API simulada ----
    def send_order(self, symbol, side, quantity, price=None, ord_type="LIMIT", **kwargs):
        ticker = self._ticker(symbol)
        spec = self._spec(ticker, side)
        if spec == "error":
            return {"error": "fallo simulado"}
        self._n += 1
        cl = f"CL{self._n}"
        if spec == "reject":
            cum, status = 0, "REJECTED"
        else:
            cum = int(quantity * spec) if isinstance(spec, float) else min(int(spec), quantity)
            status = "FILLED" if cum >= quantity else ("PARTIALLY_FILLED" if cum else "NEW")
        orden = {
            "clOrdId": cl,
            "ticker": ticker,
            "side": side,
            "orderQty": quantity,
            "price": price,
            "ordType": ord_type,
            "cumQty": cum,
            "avgPx": price if cum else None,
            "status": status,
            "text": "rechazo simulado" if status == "REJECTED" else "",
        }
        self.orders[cl] = orden
        self.enviadas.append(orden)
        return {"status": "OK", "order": {"clientId": cl, "proprietary": "PBCP"}}

    def get_orders_by_clor_id(self, clorId, proprietary):
        if clorId not in self.orders:
            return {"error": "no existe"}
        return {"order": copy.deepcopy(self.orders[clorId])}

    def cancel_order(self, cl_ord_id, proprietary):
        self.cancelaciones.append(cl_ord_id)
        o = self.orders[cl_ord_id]
        if o["ticker"] in self.no_cancelables or o["status"] in self.FINALES:
            return {"status": "OK"}
        extra = self.fills_tardios.pop((o["ticker"], o["side"]), 0)
        if extra:
            o["cumQty"] = min(o["orderQty"], o["cumQty"] + extra)
            o["avgPx"] = o["price"]
        o["status"] = "FILLED" if o["cumQty"] >= o["orderQty"] else "CANCELLED"
        return {"status": "OK"}

    # ---- helpers de aserción ----
    def activas(self) -> List[Dict]:
        return [o for o in self.orders.values() if o["status"] not in self.FINALES]

    def secuencia(self) -> List[tuple]:
        """(ticker, side, cantidad) de cada orden enviada, en orden."""
        return [(o["ticker"], o["side"], o["orderQty"]) for o in self.enviadas]


class FakeDataManager:
    """Devuelve el `base` actual; `cola` permite inyectar snapshots distintos para cada refresco."""

    def __init__(self, base: List[Dict]):
        self.base = base
        self.cola: List[List[Dict]] = []
        self.llamadas = 0

    def get_snapshot(self) -> List[Dict]:
        self.llamadas += 1
        if self.cola:
            self.base = self.cola.pop(0)
        return copy.deepcopy(self.base)
