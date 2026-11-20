"""Dobles de prueba compartidos por los tests de comun/, compradores/ y analizadores/."""

from __future__ import annotations

from typing import Dict, List, Optional


class FakeClient:
    """Broker simulado: registra las órdenes y responde con el estado configurado."""

    def __init__(self, estado: str = "FILLED", error_envio: Optional[str] = None):
        self.estado = estado
        self.error_envio = error_envio
        self.ordenes: List[Dict] = []
        self.cancelaciones: List[str] = []
        self.consultas: List[str] = []

    def send_order(self, **kwargs) -> Dict:
        self.ordenes.append(kwargs)
        if self.error_envio:
            return {"error": self.error_envio}
        return {"order": {"clientId": f"id{len(self.ordenes)}", "proprietary": "PBCP"}}

    def get_orders_by_clor_id(self, cl_ord_id: str, proprietary: str) -> Dict:
        self.consultas.append(cl_ord_id)
        return {"order": {"status": self.estado}}

    def cancel_order(self, cl_ord_id: str, proprietary: str) -> Dict:
        self.cancelaciones.append(cl_ord_id)
        return {"status": "OK"}


def registro(
    ticker: str,
    plazo: str,
    bid,
    ask,
    si_bid=1000,
    si_ask=1000,
    minimo="",
    cierre="",
) -> str:
    """Arma un registro de market data de Matriz con los campos en las posiciones reales."""
    valores = [f"M:bm_MERV_{ticker}_{plazo}", "x", str(si_bid), str(bid), str(ask), str(si_ask)]
    valores += [""] * 10
    valores[11] = str(minimo)
    valores[15] = str(cierre)
    return "|".join(valores)
