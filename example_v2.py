import requests
import base64
import json
import time
import threading
import websocket  # pip install websocket-client
from datetime import datetime
from typing import Dict, List, Optional, Callable
import logging
import os
import configparser
import copy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/example2.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class CocosMatrizClient:
    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = "https://api.cocos.xoms.com.ar",
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: Optional[str] = None
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.basic_auth = base64.b64encode(
            f"{username}:{password}".encode()
        ).decode()  # Para endpoints de riesgo
        self.login()

    def login(self) -> bool:
        """Login a la Primary API"""
        url = f"{self.base_url}/auth/getToken"
        headers = {"X-Username": self.username, "X-Password": self.password}
        try:
            response = requests.post(url, headers=headers, timeout=15)
            response.raise_for_status()
            self.token = response.headers.get("X-Auth-Token")
            if self.token:
                self.headers["X-Auth-Token"] = self.token
                logger.info(
                    f"✅ Login exitoso - Token válido por 24h: {datetime.now()}"
                )
                return True
            else:
                logger.error("❌ Login falló: no se recibió token")
                return False
        except Exception as e:
            logger.error(f"❌ Error en login: {e}")
            return False

    # ====================== DATOS DE MERCADO ======================
    def get_snapshot(
        self,
        symbol: str = "MERV - XMEV - GGAL - 24hs",
        market_id: str = "ROFX",
        entries: str = "LA,BI",  # LA=last, BI=bid, OF=offer, VO=volume, etc.
        depth: int = 1,
    ) -> dict:
        """
        Snapshot usando el formato completo que devuelve la API de instrumentos.
        Ejemplos válidos:
        - Acciones T+2:    "MERV - XMEV - GGAL - 24hs"
        - Acciones CI:     "MERV - XMEV - GGAL - CI"
        - Bonos AL30:      "MERV - XMEV - AL30 - 48hs" o similar
        """
        url = f"{self.base_url}/rest/marketdata/get"
        params = {
            "marketId": market_id,
            "symbol": symbol,  # ← Aquí va el string completo
            "entries": entries,
            "depth": depth,
        }

        try:
            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ERROR":
                logger.error("Error API:", data.get("description", "Sin descripción"))
                return data

            # Extracción amigable de datos útiles (opcional)
            if "marketData" in data and data["marketData"]:
                md = data["marketData"]
                logger.info(
                    f"Último precio (LA): {md.get('LA', {}).get('price', 'N/A')}"
                )

            return data

        except Exception as e:
            logger.error(f"❌ Error en get_snapshot: {e}")
            return {"error": str(e)}

    def get_instruments(self, market_id: str = "BYMA"):
        url = f"{self.base_url}/rest/instruments/details"
        params = {"marketId": market_id}
        r = requests.get(url, headers=self.headers, params=params)
        return r.json()

    # ====================== GESTIÓN DE ÓRDENES ======================
    def send_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: Optional[float] = None,
        ord_type: str = "LIMIT",
        market_id: str = "ROFX",  # ← Cambiado a BYMA por defecto
        account: str = "",
        time_in_force: str = "DAY",
        **kwargs,
    ) -> Dict:

        url = f"{self.base_url}/rest/order/newSingleOrder"
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

        r = requests.get(
            url, headers=self.headers, params=params
        )  # La mayoría de endpoints de Primary usan GET + query params
        return r.json() if r.ok else {"error": r.text}

    def get_orders(
        self, account: str, status: str = "actives"
    ) -> List:  # actives, filleds, all
        """Consultar órdenes por cuenta"""
        endpoint = f"/rest/order/{status}"
        url = f"{self.base_url}{endpoint}"
        params = {"accountId": account}  # En doc es accountId, pero asumimos string
        r = requests.get(url, headers=self.headers, params=params)
        return r.json() if r.ok else []

    def get_orders_by_clor_id(
        self, clorId: str, proprietary: str
    ) -> Dict:  # actives, filleds, all
        """Consultar órdenes por cuenta"""
        endpoint = "/rest/order/id"
        url = f"{self.base_url}{endpoint}"
        params = {
            "clOrdId": clorId,
            "proprietary": proprietary,
        }  # En doc es accountId, pero asumimos string
        r = requests.get(url, headers=self.headers, params=params)
        return r.json() if r.ok else {"error": r.text}

    def cancel_order(self, cl_ord_id: str, proprietary: str) -> Dict:
        """Cancelar orden por clOrdId"""
        url = f"{self.base_url}/rest/order/cancelById"
        params = {"clOrdId": cl_ord_id, "proprietary": proprietary}
        r = requests.get(url, headers=self.headers, params=params)
        return r.json() if r.ok else {"error": r.text}

    # ====================== PORTFOLIO Y CUENTA (RISK API con Basic Auth) ======================
    def get_positions(self, account_name: str) -> Dict:
        """Posiciones abiertas"""
        url = f"{self.base_url}/rest/risk/position/getPositions/{account_name}"
        headers = {"Authorization": f"Basic {self.basic_auth}"}
        r = requests.get(url, headers=headers)
        return r.json() if r.ok else {"error": r.text}

    def get_detailed_positions(self, account_name: str) -> Dict:
        """Posiciones detalladas"""
        url = f"{self.base_url}/rest/risk/detailedPosition/{account_name}"
        headers = {"Authorization": f"Basic {self.basic_auth}"}
        r = requests.get(url, headers=headers)
        return r.json() if r.ok else {"error": r.text}

    def get_portfolio(self, account_name: str) -> Dict:
        """Reporte completo de cuenta (portfolio, cash, P&L, etc.)"""
        url = f"{self.base_url}/rest/risk/accountReport/{account_name}"
        headers = {"Authorization": f"Basic {self.basic_auth}"}
        r = requests.get(url, headers=headers)
        return r.json() if r.ok else {"error": r.text}

    # ====================== WEBSOCKET - MARKET DATA EN TIEMPO REAL ======================


class WebSocketClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token

    def start_market_data_websocket(
        self,
        symbols: List[str],
        on_data_callback: Callable[[Dict], None],
        depth: int = 1,
        entries: str = "LA,BI,OF",
    ):  # agregué OI como común
        """
        Inicia WebSocket para market data en tiempo real (compatible Primary / Cocos Matriz).
        """
        if not self.token:
            logger.warning("❌ Token no disponible. Realiza login primero.")
            return

        self.on_market_data = on_data_callback
        ws_url = "wss://api.cocos.xoms.com.ar/"

        def on_open(ws):
            logger.info("WebSocket conectado → Suscribiendo a market data...")

            subscribe_msg = {
                "type": "smd",
                "level": 1,
                "entries": entries.split(","),
                "products": [
                    {"symbol": symbol, "marketId": "ROFX"} for symbol in symbols
                ],
                "depth": depth,
            }

            ws.send(json.dumps(subscribe_msg))
            # logger.info(f" → Suscripción enviada: {json.dumps(subscribe_msg, indent=2)}")

        def on_message(ws, message):
            # logger.info(f"WS Msg (raw): {message}")
            try:
                data = json.loads(message)
                msg_type = data.get("type")

                # Mensajes de market data update (oficial en doc: "Md")
                if msg_type == "Md":
                    if self.on_market_data:
                        self.on_market_data(data)
                elif msg_type in ["smd_update", "md", "update"]:
                    # Variantes vistas en algunos brokers
                    if self.on_market_data:
                        self.on_market_data(data)
                else:
                    # Mensajes de control / error / heartbeat
                    logger.warning(f"WS Msg (control/error): {data}")

            except json.JSONDecodeError:
                logger.error("WS Msg raw (no JSON):", message)
            except Exception as e:
                logger.error(f"Error parseando WS msg: {e} → raw: {message}")

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
            time.sleep(5)  # simple backoff
            # Opcional: exponential backoff + max retries
            self.start_market_data_websocket(symbols, on_data_callback, depth, entries)

        # Header oficial según Primary API y pyRofex / jsRofex
        ws_headers = {
            "x-auth-token": self.token
            # Si falla, prueba también: "Authorization": f"Bearer {self.token}"
        }

        self.ws = websocket.WebSocketApp(
            ws_url,
            header=ws_headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()
        logger.info(f"WebSocket iniciado (URL: {ws_url})")

    def stop_websocket(self):
        if self.ws:
            self.ws.close()
            logger.info("WebSocket detenido")
        self.ws = None


class DataManager:
    def __init__(self, instrumentos: List[Dict]):
        self.instrumentos = instrumentos

    def market_data_callback(self, data: Dict):
        self.update_instrument_data(data)
        # self.execute()

    def update_instrument_data(self, data: Dict):
        if "marketData" in data and data["marketData"]:
            md = data["marketData"]
            symbol = data["instrumentId"]["symbol"]
            bid = (
                md.get("BI", [{}])[0].get("price")
                if md.get("BI") is not None and md.get("BI") != []
                else None
            )
            offer = (
                md.get("OF", [{}])[0].get("price")
                if md.get("OF") is not None and md.get("OF") != []
                else None
            )
            size_bid = (
                md.get("BI", [{}])[0].get("size", "N/A")
                if md.get("BI") is not None and md.get("BI") != []
                else None
            )
            size_offer = (
                md.get("OF", [{}])[0].get("size", "N/A")
                if md.get("OF") is not None and md.get("OF") != []
                else None
            )

            ticker = symbol.removeprefix("MERV - XMEV -").removesuffix("- 24hs").strip()
            for item in self.instrumentos:
                if item["ticker"] == ticker:
                    item["prCompraPesos"] = bid
                    item["prVentaPesos"] = offer
                    item["siCompraPesos"] = size_bid
                    item["siVentaPesos"] = size_offer
                    return
                if item["tickerD"] == ticker:
                    item["prCompraDolar"] = bid
                    item["prVentaDolar"] = offer
                    item["siCompraDolar"] = size_bid
                    item["siVentaDolar"] = size_offer
                    return


class Executer:
    def __init__(self, account, client: CocosMatrizClient):
        self.account = account
        self.client = client

    def monitor_order(
        self,
        cl_ord_id: str,
        proprietary: str,
        timeout: int = 60,
        poll_interval: int = 1,
    ) -> Dict:
        """
        Monitorea el status de una orden hasta que sea FILLED o timeout.
        Retorna el order report final.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            orders = self.client.get_orders_by_clor_id(cl_ord_id, proprietary)
            if orders and isinstance(orders, list) and len(orders) > 0:
                order = orders[0].get("order", {})
                status = order.get("status")
                cum_qty = order.get("cumQty", 0)
                leaves_qty = order.get("leavesQty", order.get("orderQty", 0) - cum_qty)
                logger.info(
                    f"Order {cl_ord_id}: status={status}, cumQty={cum_qty}, leavesQty={leaves_qty}"
                )
                if status in ["FILLED", "PARTIALLY_FILLED"] and leaves_qty == 0:
                    return order
                elif status in ["REJECTED", "CANCELLED"]:
                    logger.error(f"Order {cl_ord_id} rechazada o cancelada.")
                    return order
            time.sleep(poll_interval)
        logger.warning(f"Timeout monitoreando order {cl_ord_id}")
        return {}

    def execute(self, instrumentos: List[Dict]):
        for item in instrumentos:
            item["USD_a_pesos"] = (
                float(item["prCompraPesos"] / item["prVentaDolar"])
                if item["prCompraPesos"] and item["prVentaDolar"]
                else None
            )
            item["pesos_a_USD"] = (
                float(item["prVentaPesos"] / item["prCompraDolar"])
                if item["prVentaPesos"] and item["prCompraDolar"]
                else None
            )
        # logger.info(f"Instrumentos actualizados: {instrumentos}")

        logger.info("Ejecutando estrategia de arbitraje → comparando ratios USD/pesos.")

        ratio = 1.0007
        al30 = {}
        USD_a_pesos_AL30 = None
        pesos_a_USD_AL30 = None

        for item in instrumentos:
            if item["ticker"] == "AL30":
                if item["USD_a_pesos"] is None or item["pesos_a_USD"] is None:
                    logger.warning(
                        "AL30 no tiene datos completos para calcular ratios."
                    )
                    return
                al30 = item
                USD_a_pesos_AL30 = item["USD_a_pesos"]
                pesos_a_USD_AL30 = item["pesos_a_USD"]

        if not USD_a_pesos_AL30 or not pesos_a_USD_AL30:
            logger.warning("No se pudo calcular ratios para AL30. Verifica datos.")
            return

        max_quant_al30 = al30.get(
            "max_quant", 2000
        )  # Ejemplo: máximo 2000 contratos de AL30 por operación

        for item in instrumentos:
            if item["pesos_a_USD"] is None:
                continue
            else:
                if item["pesos_a_USD"] * ratio < USD_a_pesos_AL30:
                    logger.info(json.dumps(item, indent=2, ensure_ascii=False))
                    quant = min(
                        item["siCompraPesos"],
                        item["siVentaDolar"],
                        item["max_quant"],
                    )

                    amount = quant * item["prVentaDolar"]
                    quant_al30 = min(
                        int(amount / al30["prCompraDolar"]), max_quant_al30
                    )

                    logger.info(
                        f"Ejecutando estrategia para {item['ticker']} → quant={quant}, amount={amount}, quant_al30={quant_al30}"
                    )

                    logger.info(f"COMPRAR {item['ticker']} EN PESOS")
                    logger.info(f"VENDER {item['ticker']} EN DOLARES")
                    logger.info(
                        f"Se dolarizaria a un precio de {item['pesos_a_USD']:.2f} pesos por dólar"
                    )
                    success, price_ratio = self.dolarizar(item, quant)
                    logger.warning(
                        f"Se dolarizó a: {price_ratio:.4f}"
                        if price_ratio
                        else "No se obtuvo price ratio en dolarización."
                    )

                    logger.info("COMPRAR AL30 EN DOLARES")
                    logger.info("VENDER AL30 EN PESOS")
                    if success:
                        success2, price_ratio2 = self.pesificar(
                            al30, quant_al30, order_type="MARKET"
                        )
                        logger.warning(
                            f"Se pesificó a: {price_ratio2:.4f}"
                            if price_ratio2
                            else "No se obtuvo price ratio en pesificación."
                        )
                    else:
                        logger.warning(
                            "No se ejecutó la parte de AL30 porque la dolarización no se ejecutó."
                        )
            if item["USD_a_pesos"] is None:
                continue
            else:
                if pesos_a_USD_AL30 * ratio < item["USD_a_pesos"]:
                    logger.info(json.dumps(item, indent=2, ensure_ascii=False))
                    quant = min(
                        item["siVentaDolar"],
                        item["siCompraPesos"],
                        item["max_quant"],
                    )

                    amount = quant * item["prVentaDolar"]
                    quant_al30 = min(
                        int(amount / al30["prCompraDolar"]), max_quant_al30
                    )

                    logger.info(
                        f"Ejecutando estrategia para {item['ticker']} → quant={quant}, amount={amount}, quant_al30={quant_al30}"
                    )

                    logger.info(f"COMPRAR {item['ticker']} EN DOLARES")
                    logger.info(f"VENDER {item['ticker']} EN PESOS")
                    logger.info(
                        f"Se pesificaria a un precio de {item['USD_a_pesos']:.2f} pesos por dólar"
                    )
                    success, price_ratio = self.pesificar(item, quant)
                    logger.warning(
                        f"Se pesificó a: {price_ratio:.4f}"
                        if price_ratio
                        else "No se obtuvo price ratio en pesificación."
                    )

                    logger.info("COMPRAR AL30 EN PESOS")
                    logger.info("VENDER AL30 EN DOLARES")
                    if success:
                        success2, price_ratio2 = self.dolarizar(
                            al30, quant_al30, order_type="MARKET"
                        )
                        logger.warning(
                            f"Se dolarizó a: {price_ratio2:.4f}"
                            if price_ratio2
                            else "No se obtuvo price ratio en dolarización."
                        )
                    else:
                        logger.warning(
                            "No se ejecutó la parte de AL30 porque la pesificación no se ejecutó."
                        )

    def dolarizar(self, dolarizador: Dict, quant: int, order_type: str = "LIMIT"):
        price_ratio = None
        first_price, second_price = None, None
        # print(f"Enviando orden de compra en pesos: {dolarizador['prVentaPesos']}")
        # print(f"Tipo de orden: {order_type}")
        # Enviar orden de compra en pesos
        orden_response = self.client.send_order(
            symbol=f"MERV - XMEV - {dolarizador['ticker']} - 24hs",  # Ticker D para dólares
            side="BUY",
            quantity=quant,
            price=dolarizador["prVentaPesos"] if order_type == "LIMIT" else None,
            ord_type=order_type,
            market_id="ROFX",
            account=self.account,
            time_in_force="DAY",
        )

        logger.info("Respuesta de la orden de compra en pesos:")
        logger.info(orden_response)

        if "error" in orden_response:
            logger.error("Error enviando orden de compra en pesos.")
            return False, price_ratio

        cl_ord_id = orden_response.get("order", {}).get("clientId")
        prop = orden_response.get("order", {}).get("proprietary")

        if not cl_ord_id or not prop:
            logger.error("No se obtuvo clOrdId o proprietary.")
            return False, price_ratio

        orden_encontrada = self.client.get_orders_by_clor_id(cl_ord_id, prop)

        status = orden_encontrada.get("order", {}).get("status")
        cum_qty = orden_encontrada.get("order", {}).get("cumQty", 0)

        if status != "FILLED":
            logger.warning(f"Orden de compra no filled. Status: {status}")

            cancel_response = self.client.cancel_order(cl_ord_id, prop)
            logger.info("Cancelación de la orden: ", cancel_response)
            if status != "PARTIALLY_FILLED":
                return False, price_ratio

        if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_qty > 0):
            logger.info(f"Orden de compra filled con cumQty={cum_qty}")
            first_price = orden_encontrada.get("order", {}).get("avgPx")
            # Enviar orden complementaria de venta en dolares por la cantidad filled
            comp_response = self.client.send_order(
                symbol=f"MERV - XMEV - {dolarizador['tickerD']} - 24hs",
                side="SELL",
                quantity=cum_qty,
                price=dolarizador["prCompraDolar"] if order_type == "LIMIT" else None,
                ord_type=order_type,
                market_id="ROFX",
                account=self.account,
                time_in_force="DAY",
            )

            logger.info("Respuesta de la orden complementaria de venta en dólares:")
            logger.info(comp_response)
            # logger.info(json.dumps(comp_response, indent=2, ensure_ascii=False))

            comp_cl_ord_id = comp_response.get("order", {}).get("clientId")
            comp_prop = comp_response.get("order", {}).get("proprietary")

            if comp_cl_ord_id and comp_prop:
                logger.info(
                    f"Orden de venta en dólares enviada → clOrdId={comp_cl_ord_id}, proprietary={comp_prop}"
                )
                comp_orden_encontrada = self.client.get_orders_by_clor_id(
                    comp_cl_ord_id, comp_prop
                )
                second_price = comp_orden_encontrada.get("order", {}).get("avgPx")
                if (
                    first_price is not None
                    and second_price is not None
                    and second_price != 0
                ):
                    price_ratio = first_price / second_price
                return True, price_ratio
            else:
                logger.error(
                    "No se pudo obtener clOrdId/proprietary de la venta en dólares"
                )

        return False, price_ratio

    def pesificar(self, pesificador: Dict, quant: int, order_type: str = "LIMIT"):
        price_ratio = None
        first_price, second_price = None, None
        # print(f"Enviando orden de compra en dolares: {pesificador['prVentaDolar']}")
        # print(f"Tipo de orden: {order_type}")
        # Enviar orden de compra en dólares
        orden_response = self.client.send_order(
            symbol=f"MERV - XMEV - {pesificador['tickerD']} - 24hs",  # Ticker D para dólares
            side="BUY",
            quantity=quant,
            price=pesificador["prVentaDolar"] if order_type == "LIMIT" else None,
            ord_type=order_type,
            market_id="ROFX",
            account=self.account,
            time_in_force="DAY",
        )

        logger.info("Respuesta de la orden de compra en dólares:")
        logger.info(orden_response)

        if "error" in orden_response:
            logger.error("Error enviando orden de compra en dolares.")
            return False, price_ratio

        cl_ord_id = orden_response.get("order", {}).get("clientId")
        prop = orden_response.get("order", {}).get("proprietary")

        if not cl_ord_id or not prop:
            logger.error("No se obtuvo clOrdId o proprietary.")
            return False, price_ratio

        orden_encontrada = self.client.get_orders_by_clor_id(cl_ord_id, prop)

        status = orden_encontrada.get("order", {}).get("status")
        cum_qty = orden_encontrada.get("order", {}).get("cumQty", 0)

        if status != "FILLED":
            logger.warning(f"Orden de compra no filled. Status: {status}")

            cancel_response = self.client.cancel_order(cl_ord_id, prop)
            logger.info("Cancelación de la orden: ", cancel_response)
            if status != "PARTIALLY_FILLED":
                return False, price_ratio

        if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_qty > 0):
            logger.info(f"Orden de compra filled con cumQty={cum_qty}")
            first_price = orden_encontrada.get("order", {}).get("avgPx")
            # Enviar orden complementaria de venta en pesos por la cantidad filled
            comp_response = self.client.send_order(
                symbol=f"MERV - XMEV - {pesificador['ticker']} - 24hs",
                side="SELL",
                quantity=cum_qty,
                price=pesificador["prCompraPesos"] if order_type == "LIMIT" else None,
                ord_type=order_type,
                market_id="ROFX",
                account=self.account,
                time_in_force="DAY",
            )

            logger.info("Respuesta de la orden complementaria de venta en pesos:")
            logger.info(comp_response)
            # logger.info(json.dumps(comp_response, indent=2, ensure_ascii=False))

            comp_cl_ord_id = comp_response.get("order", {}).get("clientId")
            comp_prop = comp_response.get("order", {}).get("proprietary")

            if comp_cl_ord_id and comp_prop:
                logger.info(
                    f"Orden de venta en pesos enviada → clOrdId={comp_cl_ord_id}, proprietary={comp_prop}"
                )
                comp_orden_encontrada = self.client.get_orders_by_clor_id(
                    comp_cl_ord_id, comp_prop
                )
                second_price = comp_orden_encontrada.get("order", {}).get("avgPx")
                if (
                    first_price is not None
                    and second_price is not None
                    and second_price != 0
                ):
                    price_ratio = second_price / first_price
                return True, price_ratio
            else:
                logger.error(
                    "No se pudo obtener clOrdId/proprietary de la venta en pesos"
                )

        return False, price_ratio


# ====================== EJEMPLO DE USO ======================
if __name__ == "__main__":
    instrumentos = [
        {
            "ticker": "AL30",
            "tickerD": "AL30D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 1800,
        },
        {
            "ticker": "YM34O",
            "tickerD": "YM34D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "YMCXO",
            "tickerD": "YMCXD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "RUCDO",
            "tickerD": "RUCDD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 400,
        },
        {
            "ticker": "TLCTO",
            "tickerD": "TLCTD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "PQCSO",
            "tickerD": "PQCSD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "TLCPO",
            "tickerD": "TLCPD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "IRCPO",
            "tickerD": "IRCPD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "DNC7O",
            "tickerD": "DNC7D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "DNC8O",
            "tickerD": "DNC8D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "VSCVO",
            "tickerD": "VSCVD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 1000,
        },
        {
            "ticker": "TSC4O",
            "tickerD": "TSC4D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 1000,
        },
        {
            "ticker": "TTCDO",
            "tickerD": "TTCDD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 1000,
        },
        {
            "ticker": "TLCMO",
            "tickerD": "TLCMD",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 1000,
        },
        {
            "ticker": "PLC5O",
            "tickerD": "PLC5D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 1000,
        },
        {
            "ticker": "YM37O",
            "tickerD": "YM37D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
        {
            "ticker": "YM42O",
            "tickerD": "YM42D",
            "prCompraPesos": None,
            "prVentaPesos": None,
            "prCompraDolar": None,
            "prVentaDolar": None,
            "siCompraPesos": None,
            "siVentaPesos": None,
            "siCompraDolar": None,
            "siVentaDolar": None,
            "max_quant": 200,
        },
    ]

    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    CONFIG_FILE_PATH = os.path.join(PROJECT_ROOT, "config.ini")
    if not os.path.exists(CONFIG_FILE_PATH):
        raise FileNotFoundError(
            f"Credentials file not found at {CONFIG_FILE_PATH}. Please create it."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE_PATH)
    usuario = config["credentials"]["matriz_username"]
    password = config["credentials"]["matriz_password"]
    account = config["credentials"]["matriz_account"]

    client = CocosMatrizClient(username=usuario, password=password)

    # print(client.get_instruments("ROFX"))
    # Datos de mercado
    # print("Snapshot GGAL:")
    # print(client.get_snapshot("MERV - XMEV - GGAL - 24hs"))

    # # Portfolio (usa Basic Auth)
    # print("Portfolio para cuenta 'TU_CUENTA':")
    # print(client.get_portfolio("35315"))  # Reemplaza con tu accountName

    # # Órdenes activas
    # print("Órdenes activas para cuenta 'TU_CUENTA':")
    # print(client.get_orders("35315", "actives"))

    # # # orden_response = client.send_order(
    # # #     symbol="MERV - XMEV - YM34D - 24hs",
    # # #     side="BUY",  # o "SELL"
    # # #     quantity=1,
    # # #     price=100.00,  # precio límite
    # # #     ord_type="LIMIT",
    # # #     market_id="ROFX",  # o "BYMA" si tu broker lo usa así
    # # #     account="35315",  # ← muy importante: tu número de cuenta
    # # #     time_in_force="DAY",  # DAY = válida solo el día, GTC = good till cancel, etc.
    # # # )

    # # # print("Respuesta de la orden:")
    # # # print(orden_response)
    # # # clord_id = orden_response["order"].get("clientId")
    # # # prop = orden_response["order"].get("proprietary")
    # # # print(clord_id, prop)

    # # # orden_encontrada = client.get_orders_by_clor_id(
    # # #     clorId=clord_id,
    # # #     proprietary=prop
    # # # )

    # # # if orden_encontrada:
    # # #     print("\nEstado de la orden:")
    # # #     print(json.dumps(orden_encontrada, indent=2, ensure_ascii=False))

    # # # if clord_id and prop:
    # # #     cancel_response = client.cancel_order(
    # # #         cl_ord_id=clord_id, proprietary=prop
    # # #     )  # Reemplaza con el valor correcto si es necesario
    # # #     print("Respuesta de cancelación:", cancel_response)

    # response = client.send_order(
    #     symbol="MERV - XMEV - YPFD - 24hs",
    #     side="SELL",
    #     quantity=50,
    #     price=None,               # sin precio → es MARKET
    #     ord_type="MARKET",
    #     market_id="ROFX",
    #     account="35315",
    #     time_in_force="DAY"
    # )

    # print(response)

    # Ejemplo: suscribirse a GGAL y YPFD

    symbols_to_subscribe = [
        "MERV - XMEV - {0} - 24hs".format(inst["ticker"]) for inst in instrumentos
    ] + ["MERV - XMEV - {0} - 24hs".format(inst["tickerD"]) for inst in instrumentos]

    websocket_client = WebSocketClient(token=client.token)
    data_manager = DataManager(instrumentos)

    executer = Executer(account=account, client=client)

    websocket_client.start_market_data_websocket(
        symbols=symbols_to_subscribe,
        on_data_callback=data_manager.market_data_callback,
        depth=1,  # 1 = solo top of book
        entries="LA,BI,OF",  # Last, Bid, Offer, Volume
    )

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(3)
            snapshot = copy.deepcopy(instrumentos)
            executer.execute(
                snapshot
            )  # pasar una copia para evitar modificaciones concurrentes
    except KeyboardInterrupt:
        print("Exiting...")
        websocket_client.stop_websocket()
