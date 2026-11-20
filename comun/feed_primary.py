"""WebSocket de market data de Primary (wss://api.cocos.xoms.com.ar/, autenticado con el token del login)."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict, Iterable, Optional, Tuple

from comun.cotizaciones import PLAZOS, LibroCotizaciones
from comun.feed_matriz import AlActualizar, Suscripcion, sin_duplicados

URL_WS_PRIMARY = "wss://api.cocos.xoms.com.ar/"
PREFIJO_SIMBOLO = "MERV - XMEV - "
TIPOS_MARKET_DATA = ("Md", "smd_update", "md", "update")

logger = logging.getLogger(__name__)


def simbolo_primary(ticker: str, plazo: str) -> str:
    return f"{PREFIJO_SIMBOLO}{ticker} - {plazo}"


def _punta(md: Dict, entrada: str) -> Tuple[Optional[float], Optional[float]]:
    """(precio, size) del mejor nivel de BI u OF; (None, None) si el libro está vacío."""
    niveles = md.get(entrada) or []
    if not niveles:
        return None, None
    return niveles[0].get("price"), niveles[0].get("size")


def parsear_mensaje_primary(data: Dict) -> Optional[Tuple[str, str, Dict]]:
    """
    Mensaje 'Md' de Primary → (ticker, plazo, campos a actualizar).
    Solo se actualizan las puntas presentes en el mensaje; si BI u OF vienen vacíos, la punta queda en None.
    """
    md = data.get("marketData")
    simbolo = (data.get("instrumentId") or {}).get("symbol", "")
    if not md or not simbolo.startswith(PREFIJO_SIMBOLO):
        return None
    ticker, sep, plazo = simbolo.removeprefix(PREFIJO_SIMBOLO).rpartition(" - ")
    if not sep or plazo.strip() not in PLAZOS:
        return None

    campos: Dict = {}
    if "BI" in md:
        campos["pr_compra"], campos["si_compra"] = _punta(md, "BI")
    if "OF" in md:
        campos["pr_venta"], campos["si_venta"] = _punta(md, "OF")
    return ticker.strip(), plazo.strip(), campos


class FeedPrimary:
    """Misma interfaz que FeedMatriz, sobre el WebSocket de Primary."""

    def __init__(
        self,
        token: str,
        suscripciones: Iterable[Suscripcion],
        libro: LibroCotizaciones,
        al_actualizar: Optional[AlActualizar] = None,
        max_reintentos: int = 5,
        url: str = URL_WS_PRIMARY,
    ):
        self.token = token
        self.suscripciones = sin_duplicados(suscripciones)
        self.libro = libro
        self.al_actualizar = al_actualizar
        self.max_reintentos = max_reintentos
        self.url = url
        self.reintentos = 0
        self.ws = None
        self._detenido = False

    def mensaje_suscripcion(self) -> str:
        return json.dumps(
            {
                "type": "smd",
                "level": 1,
                "entries": ["LA", "BI", "OF"],
                "products": [
                    {"symbol": simbolo_primary(t, p), "marketId": "ROFX"}
                    for t, p in self.suscripciones
                ],
                "depth": 1,
            }
        )

    def conectar(self) -> threading.Thread:
        import websocket  # import diferido: los tests no necesitan la dependencia

        self.ws = websocket.WebSocketApp(
            self.url,
            header={"x-auth-token": self.token},
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        hilo = threading.Thread(target=self.ws.run_forever, daemon=True)
        hilo.start()
        logger.info(f"WebSocket iniciado (URL: {self.url})")
        return hilo

    def detener(self) -> None:
        self._detenido = True
        if self.ws:
            self.ws.close()
            logger.info("WebSocket detenido")
        self.ws = None

    # ---------------- callbacks ----------------
    def on_open(self, ws) -> None:
        logger.info("WebSocket Primary conectado → suscribiendo a market data...")
        self.reintentos = 0
        ws.send(self.mensaje_suscripcion())

    def on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.error(f"WS Msg raw (no JSON): {message}")
            return
        if data.get("type") not in TIPOS_MARKET_DATA:
            logger.warning(f"WS Msg (control/error): {data}")
            return
        parseado = parsear_mensaje_primary(data)
        if parseado is None:
            return
        ticker, plazo, campos = parseado
        cotizacion = self.libro.actualizar_campos(ticker, plazo, **campos)
        if self.al_actualizar is not None:
            try:
                self.al_actualizar(cotizacion)
            except Exception:  # noqa: BLE001
                logger.exception(f"❌ Error procesando {ticker} {plazo}")

    def on_error(self, ws, error) -> None:
        logger.error(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg) -> None:
        logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        if self._detenido:
            return
        if self.reintentos >= self.max_reintentos:
            logger.error("Máximo de reintentos alcanzado. WebSocket detenido.")
            return
        self.reintentos += 1
        time.sleep(2**self.reintentos)  # backoff exponencial
        self.conectar()
