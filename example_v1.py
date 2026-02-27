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


class WebSocketClient:
    def __init__(self, url, dataManager, instrumentos):
        self.url = url
        self.dataManager = dataManager
        self.instrumentos = instrumentos

    def connect(self):
        # websocket.enableTrace(True)
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws.keep_running = True
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()

        return wst

    def on_message(self, ws, message):
        data = []
        if message[0] != "X":
            if message[0] == "M":
                data.append(message)
            else:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError as e:
                    logger.warning(f"❌ JSON Decode Error: {e}")
                    return
        else:
            logger.warning("Message is not market data")
            return
        self.dataManager.update_instrument_data(data)

    def on_error(self, ws, error):
        logger.error("Error: " + str(error))

    def on_close(self, ws, close_status_code, close_msg):
        logger.info("### Closed connection ###")
        logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        time.sleep(5)  # simple backoff
        self.connect()  # Reconnect automáticamente

    def on_open(self, ws):
        logger.info("### Opened connection ###")
        ws.send(self.create_subscription_message())

    def create_subscription_message(self):
        aux = [
            "md.bm_MERV_{0}_24hs".format(inst["ticker"]) for inst in self.instrumentos
        ] + [
            "md.bm_MERV_{0}_24hs".format(inst["tickerD"]) for inst in self.instrumentos
        ]
        return (
            '{"_req":"S","topicType":"md","topics":'
            + str(aux).replace(" ", "").replace("'", '"')
            + ',"replace":false}'
        )

    def stop_websocket(self):
        if self.ws:
            self.ws.close()
            logger.info("WebSocket detenido")
        self.ws = None


class DataManager:
    def __init__(self, instrumentos: List[Dict]):
        self.instrumentos = instrumentos

    def update_instrument_data(self, data: Dict):
        for r in data:
            values = str(r).split("|")
            if values[3] == "":
                values[3] = "-100"
            if values[4] == "":
                values[4] = "-100"
            if values[0].__contains__("_24hs"):
                ticker = values[0].removeprefix("M:bm_MERV_").removesuffix("_24hs")
                for item in self.instrumentos:
                    if item["ticker"] == ticker:
                        item["prCompraPesos"] = float(values[3])
                        item["prVentaPesos"] = float(values[4])
                        item["siCompraPesos"] = (
                            float(values[2]) if values[2] != "" else None
                        )
                        item["siVentaPesos"] = (
                            float(values[5]) if values[5] != "" else None
                        )
                        continue
                    if item["tickerD"] == ticker:
                        item["prCompraDolar"] = float(values[3])
                        item["prVentaDolar"] = float(values[4])
                        item["siCompraDolar"] = (
                            float(values[2]) if values[2] != "" else None
                        )
                        item["siVentaDolar"] = (
                            float(values[5]) if values[5] != "" else None
                        )
                        continue


class Executer:
    def __init__(self, account, client: CocosMatrizClient):
        self.account = account
        self.client = client

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

        ratio = 1.0005
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
            if item["pesos_a_USD"] is None or item["pesos_a_USD"] <= 1:
                continue
            else:
                if item["pesos_a_USD"] * ratio < USD_a_pesos_AL30:
                    logger.info(json.dumps(item, indent=2, ensure_ascii=False))
                    quant = min(
                        item["siCompraDolar"],
                        item["siVentaPesos"],
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
            if item["USD_a_pesos"] is None or item["USD_a_pesos"] <= 1:
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
            "max_quant": 200,
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
        {
            "ticker": "LOC6O",
            "tickerD": "LOC6D",
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
            "ticker": "OLC5O",
            "tickerD": "OLC5D",
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
            "ticker": "PN43O",
            "tickerD": "PN43D",
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
    session_id = config["credentials"]["matriz_session_id"]
    conn_id = config["credentials"]["matriz_conn_id"]

    websocket_url = (
        f"wss://matriz.cocos.xoms.com.ar/ws?session_id={session_id}&conn_id={conn_id}"
    )

    client = CocosMatrizClient(username=usuario, password=password)

    data_manager = DataManager(instrumentos)
    websocket_client = WebSocketClient(websocket_url, data_manager, instrumentos)
    wst = websocket_client.connect()

    executer = Executer(account=account, client=client)

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(3)
            snapshot = copy.deepcopy(instrumentos)
            executer.execute(
                snapshot
            )  # pasar una copia para evitar modificaciones concurrentes
    except KeyboardInterrupt:
        logger.info("Exiting...")
        websocket_client.stop_websocket()
