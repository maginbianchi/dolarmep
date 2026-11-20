"""Cliente REST de la Primary API (Cocos/Matriz). Basado en arbitrador_v4/mercado.py."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

import requests

DEFAULT_API_URL = "https://api.cocos.xoms.com.ar"
REQUEST_TIMEOUT = 15
AUTH_TIMEOUT = 15
MARKET_ID_DEFAULT = "ROFX"
TIME_IN_FORCE_DEFAULT = "DAY"

logger = logging.getLogger(__name__)


class CocosMatrizClient:
    """Login, envío, consulta y cancelación de órdenes. Nunca lanza: ante fallas devuelve {"error": ...}."""

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
