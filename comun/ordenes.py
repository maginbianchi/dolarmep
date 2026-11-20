"""Envío de una orden LIMIT con verificación inmediata y cancelación si no se completó."""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)

ESTADO_FILLED = "FILLED"


def simbolo_orden(ticker: str, plazo: str) -> str:
    return f"MERV - XMEV - {ticker} - {plazo}"


def enviar_limit_y_verificar(
    client, account: str, simbolo: str, lado: str, cantidad: int, precio: float
) -> Dict:
    """
    Envía la orden, consulta su estado y, si no quedó FILLED, la cancela.

    Devuelve {"estado": <status o "ERROR">, "enviada": bool, "cl_ord_id": ..., "proprietary": ...,
    "cancelada": bool}. "enviada" es False solo si el broker rechazó el envío (la orden no existe).
    """
    resultado = {
        "estado": "ERROR",
        "enviada": False,
        "cl_ord_id": None,
        "proprietary": None,
        "cancelada": False,
    }

    respuesta = client.send_order(
        symbol=simbolo,
        side=lado,
        quantity=cantidad,
        price=precio,
        ord_type="LIMIT",
        market_id="ROFX",
        account=account,
        time_in_force="DAY",
    )
    if "error" in respuesta:
        logger.error(f"❌ Error enviando orden {lado} {simbolo}: {respuesta['error']}")
        return resultado

    resultado["enviada"] = True
    orden = respuesta.get("order", {})
    cl_ord_id, prop = orden.get("clientId"), orden.get("proprietary")
    resultado.update(cl_ord_id=cl_ord_id, proprietary=prop)
    if not cl_ord_id or not prop:
        logger.error(f"❌ No se obtuvo clOrdId o proprietary. Respuesta: {respuesta}")
        return resultado

    consulta = client.get_orders_by_clor_id(cl_ord_id, prop)
    estado = consulta.get("order", {}).get("status") or "ERROR"
    resultado["estado"] = estado

    if estado == ESTADO_FILLED:
        logger.info(f"✅ Orden {lado} {simbolo} x{cantidad} @ {precio} FILLED")
        return resultado

    logger.warning(f"Orden {lado} {simbolo} no filled. Status: {estado}")
    cancelacion = client.cancel_order(cl_ord_id, prop)
    resultado["cancelada"] = "error" not in cancelacion
    logger.info(f"Cancelación de la orden: {cancelacion}")
    return resultado
