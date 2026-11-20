"""WebSocket de market data de Matriz (wss://matriz.cocos.xoms.com.ar/ws, con session_id y conn_id)."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Iterable, List, Optional, Tuple

from comun.cotizaciones import Cotizacion, LibroCotizaciones, parsear_registro

WEBSOCKET_RECONNECT_DELAY = 5

logger = logging.getLogger(__name__)

Suscripcion = Tuple[str, str]  # (ticker, plazo)
AlActualizar = Callable[[Cotizacion], None]


def sin_duplicados(suscripciones: Iterable[Suscripcion]) -> List[Suscripcion]:
    """Quita repetidos conservando el orden."""
    return list(dict.fromkeys(suscripciones))


def topic_matriz(ticker: str, plazo: str) -> str:
    return f"md.bm_MERV_{ticker}_{plazo}"


def mensaje_suscripcion(suscripciones: Iterable[Suscripcion]) -> str:
    topics = [topic_matriz(t, p) for t, p in sin_duplicados(suscripciones)]
    return json.dumps(
        {"_req": "S", "topicType": "md", "topics": topics, "replace": False},
        separators=(",", ":"),
    )


class FeedMatriz:
    """
    Se suscribe a las especies pedidas, guarda cada cotización en el libro y, si se indicó,
    llama a al_actualizar(cotizacion) por cada actualización (en el hilo del WebSocket).
    """

    def __init__(
        self,
        url: str,
        suscripciones: Iterable[Suscripcion],
        libro: LibroCotizaciones,
        al_actualizar: Optional[AlActualizar] = None,
    ):
        self.url = url
        self.suscripciones = sin_duplicados(suscripciones)
        self.libro = libro
        self.al_actualizar = al_actualizar
        self.ws = None
        self._detenido = False

    def conectar(self) -> threading.Thread:
        import websocket  # import diferido: los tests no necesitan la dependencia

        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        hilo = threading.Thread(target=self.ws.run_forever, daemon=True)
        hilo.start()
        return hilo

    def detener(self) -> None:
        self._detenido = True
        if self.ws:
            self.ws.close()
            logger.info("WebSocket detenido")
        self.ws = None

    # ---------------- callbacks ----------------
    def on_open(self, ws) -> None:
        logger.info("### WebSocket Matriz conectado ###")
        ws.send(mensaje_suscripcion(self.suscripciones))

    def on_message(self, ws, message: str) -> None:
        if not message or message[0] == "X":
            logger.debug("Mensaje que no es market data")
            return
        if message[0] == "M":
            registros = [message]
        else:
            try:
                registros = json.loads(message)
            except json.JSONDecodeError as e:
                logger.warning(f"❌ JSON Decode Error: {e}")
                return
            if not isinstance(registros, list):
                return
        self.procesar_registros(registros)

    def procesar_registros(self, registros: Iterable) -> None:
        for raw in registros:
            cotizacion = parsear_registro(raw)
            if cotizacion is None:
                continue
            self.libro.actualizar(cotizacion)
            if self.al_actualizar is not None:
                try:
                    self.al_actualizar(cotizacion)
                except Exception:  # noqa: BLE001 - un error de estrategia no debe tirar el feed
                    logger.exception(f"❌ Error procesando {cotizacion.ticker} {cotizacion.plazo}")

    def on_error(self, ws, error) -> None:
        logger.error(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg) -> None:
        logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        if self._detenido:
            return
        time.sleep(WEBSOCKET_RECONNECT_DELAY)  # backoff simple
        self.conectar()
