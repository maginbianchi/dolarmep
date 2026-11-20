"""
Acceso a mercado: cliente REST de la Primary API (Cocos/Matriz), WebSocket de market data,
DataManager thread-safe y carga de instrumentos.

Basado en deprecated/example_v1.py, con estas mejoras:
    - Todas las requests tienen timeout y capturan excepciones (devuelven {"error": ...}).
    - El DataManager protege los datos con un lock y entrega snapshots consistentes.
    - Las puntas vacías se guardan como None (en v1 se usaba el centinela -100).
    - El WebSocket no se reconecta cuando se lo detiene a propósito.
"""

from __future__ import annotations

import copy
import csv
import json
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

# ====================== CONSTANTES ======================
DEFAULT_API_URL = "https://api.cocos.xoms.com.ar"
REQUEST_TIMEOUT = 15
AUTH_TIMEOUT = 15
MARKET_ID_DEFAULT = "ROFX"
TIME_IN_FORCE_DEFAULT = "DAY"
WEBSOCKET_RECONNECT_DELAY = 5
DEFAULT_MIN_QUANT = 1
DEFAULT_MAX_QUANT = 500

logger = logging.getLogger("arbitrador_v4")


# ====================== CLIENTE REST ======================
class CocosMatrizClient:
    """Cliente mínimo de la Primary API: login, envío, consulta y cancelación de órdenes."""

    def __init__(self, username: str, password: str, base_url: str = DEFAULT_API_URL):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: Optional[str] = None
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self.login()

    def login(self) -> bool:
        """Obtiene el token de autenticación (válido por 24 hs)."""
        url = f"{self.base_url}/auth/getToken"
        headers = {"X-Username": self.username, "X-Password": self.password}
        try:
            response = requests.post(url, headers=headers, timeout=AUTH_TIMEOUT)
            response.raise_for_status()
            self.token = response.headers.get("X-Auth-Token")
            if self.token:
                self.headers["X-Auth-Token"] = self.token
                logger.info(f"✅ Login exitoso - Token válido por 24h: {datetime.now()}")
                return True
            logger.error("❌ Login falló: no se recibió token")
            return False
        except Exception as e:  # noqa: BLE001 - se loguea cualquier falla de red/HTTP
            logger.error(f"❌ Error en login: {e}")
            return False

    def _get(self, endpoint: str, params: Dict) -> Dict:
        """GET con timeout. Ante cualquier falla devuelve {"error": <detalle>} en vez de lanzar."""
        try:
            r = requests.get(
                f"{self.base_url}{endpoint}",
                headers=self.headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            return r.json() if r.ok else {"error": r.text}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def send_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: Optional[float] = None,
        ord_type: str = "LIMIT",
        market_id: str = MARKET_ID_DEFAULT,
        account: str = "",
        time_in_force: str = TIME_IN_FORCE_DEFAULT,
        **kwargs,
    ) -> Dict:
        """Envía una orden nueva (la Primary API usa GET + query params)."""
        params = {
            "marketId": market_id,
            "symbol": symbol,
            "side": side.upper(),
            "orderQty": quantity,
            "ordType": ord_type.upper(),
            "account": account,
            "timeInForce": time_in_force.upper(),
            **kwargs,
        }
        if price is not None:
            params["price"] = str(price)
        return self._get("/rest/order/newSingleOrder", params)

    def get_orders_by_clor_id(self, clorId: str, proprietary: str) -> Dict:
        """Consulta el estado de una orden por clOrdId."""
        return self._get("/rest/order/id", {"clOrdId": clorId, "proprietary": proprietary})

    def cancel_order(self, cl_ord_id: str, proprietary: str) -> Dict:
        """Cancela una orden por clOrdId."""
        return self._get(
            "/rest/order/cancelById", {"clOrdId": cl_ord_id, "proprietary": proprietary}
        )


# ====================== DATA MANAGER ======================
class DataManager:
    """Mantiene las puntas de cada instrumento, actualizadas desde el WebSocket."""

    def __init__(self, instrumentos: List[Dict]) -> None:
        self._lock = threading.Lock()
        self.instrumentos = instrumentos
        # Índices para acceso O(1) por ticker en pesos y en dólares
        self.instrumentos_by_ticker = {inst["ticker"]: inst for inst in instrumentos}
        self.instrumentos_by_tickerD = {inst["tickerD"]: inst for inst in instrumentos}

    @staticmethod
    def _to_float(valor: str) -> Optional[float]:
        """Convierte un campo del mensaje a float; vacío o inválido → None."""
        try:
            return float(valor) if valor not in ("", None) else None
        except ValueError:
            return None

    def update_instrument_data(self, data: List) -> None:
        """
        Actualiza las puntas a partir de mensajes del WebSocket con el formato
            M:bm_MERV_<TICKER>_24hs|<?>|<size bid>|<precio bid>|<precio ask>|<size ask>|...
        """
        for record in data:
            values = str(record).split("|")
            if len(values) < 6 or "_24hs" not in values[0]:
                continue
            ticker = values[0].removeprefix("M:bm_MERV_").removesuffix("_24hs")
            si_bid, pr_bid = self._to_float(values[2]), self._to_float(values[3])
            pr_ask, si_ask = self._to_float(values[4]), self._to_float(values[5])

            with self._lock:
                if ticker in self.instrumentos_by_ticker:
                    inst = self.instrumentos_by_ticker[ticker]
                    inst["prCompraPesos"], inst["prVentaPesos"] = pr_bid, pr_ask
                    inst["siCompraPesos"], inst["siVentaPesos"] = si_bid, si_ask
                elif ticker in self.instrumentos_by_tickerD:
                    inst = self.instrumentos_by_tickerD[ticker]
                    inst["prCompraDolar"], inst["prVentaDolar"] = pr_bid, pr_ask
                    inst["siCompraDolar"], inst["siVentaDolar"] = si_bid, si_ask

    def get_snapshot(self) -> List[Dict]:
        """Copia profunda y consistente de los instrumentos (evita lecturas a medio actualizar)."""
        with self._lock:
            return copy.deepcopy(self.instrumentos)


# ====================== WEBSOCKET ======================
class WebSocketClient:
    """Suscripción a market data de todos los instrumentos (pesos y dólares) en 24hs."""

    def __init__(self, url: str, data_manager: DataManager, instrumentos: List[Dict]):
        self.url = url
        self.data_manager = data_manager
        self.instrumentos = instrumentos
        self.ws = None
        self._detenido = False

    def connect(self) -> threading.Thread:
        import websocket  # import diferido: los tests no necesitan la dependencia

        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        wst = threading.Thread(target=self.ws.run_forever, daemon=True)
        wst.start()
        return wst

    def on_message(self, ws, message: str) -> None:
        if not message or message[0] == "X":
            logger.debug("Mensaje que no es market data")
            return
        if message[0] == "M":
            data = [message]
        else:
            try:
                data = json.loads(message)
            except json.JSONDecodeError as e:
                logger.warning(f"❌ JSON Decode Error: {e}")
                return
        if isinstance(data, list):
            self.data_manager.update_instrument_data(data)

    def on_error(self, ws, error) -> None:
        logger.error(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg) -> None:
        logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        if self._detenido:
            return
        time.sleep(WEBSOCKET_RECONNECT_DELAY)  # backoff simple
        self.connect()

    def on_open(self, ws) -> None:
        logger.info("### WebSocket conectado ###")
        ws.send(self.create_subscription_message())

    def create_subscription_message(self) -> str:
        topics = [f"md.bm_MERV_{i['ticker']}_24hs" for i in self.instrumentos] + [
            f"md.bm_MERV_{i['tickerD']}_24hs" for i in self.instrumentos
        ]
        return json.dumps(
            {"_req": "S", "topicType": "md", "topics": topics, "replace": False},
            separators=(",", ":"),
        )

    def stop_websocket(self) -> None:
        self._detenido = True
        if self.ws:
            self.ws.close()
            logger.info("WebSocket detenido")
        self.ws = None


# ====================== INSTRUMENTOS ======================
def create_instrument(
    ticker: str,
    tickerD: str,
    min_quant: int = DEFAULT_MIN_QUANT,
    max_quant: int = DEFAULT_MAX_QUANT,
) -> Dict:
    """Crea el diccionario de un instrumento con las puntas sin datos."""
    return {
        "ticker": ticker,
        "tickerD": tickerD,
        "prCompraPesos": None,
        "prVentaPesos": None,
        "prCompraDolar": None,
        "prVentaDolar": None,
        "siCompraPesos": None,
        "siVentaPesos": None,
        "siCompraDolar": None,
        "siVentaDolar": None,
        "min_quant": min_quant,
        "max_quant": max_quant,
    }


def load_instruments(path: str) -> List[Dict]:
    """
    Carga instrumentos desde un CSV sin encabezado con el formato de data/instrumentos.csv:
        ticker,tickerD,habilitado(1/0),min_quant,max_quant
    Se ignoran líneas vacías, comentarios (#) y filas no habilitadas.
    """
    instrumentos = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 3 or row[2].strip() != "1":
                continue
            min_q = int(row[3]) if len(row) > 3 and row[3].strip() else DEFAULT_MIN_QUANT
            max_q = int(row[4]) if len(row) > 4 and row[4].strip() else DEFAULT_MAX_QUANT
            instrumentos.append(
                create_instrument(row[0].strip(), row[1].strip(), min_q, max_q)
            )
    return instrumentos
