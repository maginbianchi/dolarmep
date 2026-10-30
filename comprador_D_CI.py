import requests
import base64
import json
import time
import threading
import websocket
from datetime import datetime
from typing import Dict, List, Optional, Set
import logging
import os
import configparser
import csv

# ====================== CONSTANTES ======================
DEFAULT_API_URL = "https://api.cocos.xoms.com.ar"
REQUEST_TIMEOUT = 15
AUTH_TIMEOUT = 15
MARKET_ID_DEFAULT = "ROFX"
TIME_IN_FORCE_DEFAULT = "DAY"
WEBSOCKET_RECONNECT_DELAY = 5
ARBITER_RATIO = 1.002
UPDATE_SLEEP_INTERVAL = 0.01


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/comprador_D_CI.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class CocosMatrizClient:
    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = DEFAULT_API_URL,
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
            response = requests.post(url, headers=headers, timeout=AUTH_TIMEOUT)
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
        market_id: str = MARKET_ID_DEFAULT,  # ← Cambiado a BYMA por defecto
        account: str = "",
        time_in_force: str = TIME_IN_FORCE_DEFAULT,
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
    def __init__(self, url, executer, instrumentos):
        self.url = url
        self.executer = executer
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
        self.executer.pre_execute(data)

    def on_error(self, ws, error):
        logger.error("Error: " + str(error))

    def on_close(self, ws, close_status_code, close_msg):
        logger.info("### Closed connection ###")
        logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        time.sleep(WEBSOCKET_RECONNECT_DELAY)  # simple backoff
        self.connect()  # Reconnect automáticamente

    def on_open(self, ws):
        logger.info("### Opened connection ###")
        ws.send(self.create_subscription_message())

    def create_subscription_message(self):
        aux = [
            "md.bm_MERV_{0}_24hs".format(inst["tickerD"]) for inst in self.instrumentos
        ] + ["md.bm_MERV_{0}_CI".format(inst["tickerD"]) for inst in self.instrumentos]
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


class Executer:
    def __init__(self, account, client: CocosMatrizClient, instrumentos: List[Dict]):

        self.account = account
        self.client = client
        self.instrumentos = instrumentos
        self.al30 = create_instrument("AL30", "AL30D", 1800)

        # Crear índices para acceso rápido O(1)
        self.instrumentos_by_ticker = {inst["ticker"]: inst for inst in instrumentos}
        self.instrumentos_by_tickerD = {inst["tickerD"]: inst for inst in instrumentos}

    def pre_execute(self, data: List) -> None:
        for record in data:
            values = str(record).split("|")

            # Validar que el mensaje tenga al menos los campos necesarios
            if len(values) < 6:
                logger.warning(f"Mensaje incompleto: {record}")
                continue

            # Normalizar valores vacíos
            values[3] = values[3] if values[3] else "-100"
            values[4] = values[4] if values[4] else "-100"

            if "CI" in values[0]:
                ticker = values[0].removeprefix("M:bm_MERV_").removesuffix("_CI")
                if ticker in self.instrumentos_by_tickerD:
                    inst = self.instrumentos_by_tickerD[ticker]
                    inst["prCompraDolarCI"] = float(values[3])
                    inst["prVentaDolarCI"] = float(values[4])
                    inst["siCompraDolarCI"] = float(values[2]) if values[2] else None
                    inst["siVentaDolarCI"] = float(values[5]) if values[5] else None
                    inst["cierreAnteriorDolarCI"] = (
                        float(values[15]) if len(values) > 15 and values[15] else None
                    )
                    inst["minDolarCI"] = float(values[11]) if values[11] else None
                    self.execute(
                        inst.copy()
                    )  # Pasar una copia para evitar modificaciones durante la ejecución

            elif "_24hs" in values[0]:
                ticker = values[0].removeprefix("M:bm_MERV_").removesuffix("_24hs")
                if ticker in self.instrumentos_by_tickerD:
                    inst = self.instrumentos_by_tickerD[ticker]
                    inst["prCompraDolar"] = float(values[3])
                    inst["prVentaDolar"] = float(values[4])
                    inst["siCompraDolar"] = float(values[2]) if values[2] else None
                    inst["siVentaDolar"] = float(values[5]) if values[5] else None
                    inst["cierreAnteriorDolar"] = (
                        float(values[15]) if len(values) > 15 and values[15] else None
                    )
                    inst["minDolar"] = float(values[11]) if values[11] else None

    def execute(
        self,
        ticker: Dict,
    ):
        ratio = ARBITER_RATIO

        logger.info("Ejecutando estrategia de arbitraje → comparando ratios USD/pesos.")
        # logger.info(json.dumps(ticker, indent=2, ensure_ascii=False))
        if (
            ticker["prVentaDolarCI"] is None
            or ticker["prVentaDolarCI"] <= 1
            or ticker["prCompraDolar"] is None
            or ticker["prCompraDolar"] <= 1
        ):
            return

        if ticker["prVentaDolarCI"] * ratio < min(
            ticker["prCompraDolar"], ticker["cierreAnteriorDolar"]
        ):
            logger.info(json.dumps(ticker, indent=2, ensure_ascii=False))
            quant = min(
                ticker["siCompraDolar"],
                ticker["siVentaDolarCI"],
                ticker["max_quant"],
            )

            logger.info(
                f"Ejecutando estrategia para {ticker['ticker']} → quant={quant}"
            )

            logger.info(f"COMPRAR {ticker['ticker']} EN DÓLARES")
            self.comprar_en_dolares(ticker, quant)

            return  # Ejecutar solo una operación por ciclo.

    def comprar_en_dolares(
        self, pesificador: Dict, quant: int, order_type: str = "LIMIT"
    ):
        # Enviar orden de compra en dólares
        orden_response = self.client.send_order(
            symbol=f"MERV - XMEV - {pesificador['tickerD']} - CI",  # Ticker D para dólares
            side="BUY",
            quantity=quant,
            price=pesificador["prVentaDolarCI"] if order_type == "LIMIT" else None,
            ord_type=order_type,
            market_id="ROFX",
            account=self.account,
            time_in_force="DAY",
        )

        if "error" in orden_response:
            logger.error("Error enviando orden de compra en dolares: %s", orden_response["error"])
            return

        cl_ord_id = orden_response.get("order", {}).get("clientId")
        prop = orden_response.get("order", {}).get("proprietary")

        if not cl_ord_id or not prop:
            logger.error("No se obtuvo clOrdId o proprietary.")
            return

        orden_encontrada = self.client.get_orders_by_clor_id(cl_ord_id, prop)

        status = orden_encontrada.get("order", {}).get("status")

        if status != "FILLED":
            logger.warning(f"Orden de compra no filled. Status: {status}")

            cancel_response = self.client.cancel_order(cl_ord_id, prop)
            logger.info("Cancelación de la orden: ", cancel_response)


# ====================== FUNCIÓN AUXILIAR ======================
def create_instrument(ticker: str, tickerD: str, max_quant: int = 500) -> Dict:
    """
    Crea un diccionario de instrumento con valores inicializados.

    Args:
        ticker: Ticker en pesos
        tickerD: Ticker en dólares
        max_quant: Cantidad máxima permitida

    Returns:
        Dict: Diccionario de instrumento
    """
    return {
        "ticker": ticker,
        "tickerD": tickerD,
        "prCompraDolarCI": None,
        "prVentaDolarCI": None,
        "prCompraDolar": None,
        "prVentaDolar": None,
        "siCompraDolarCI": None,
        "siVentaDolarCI": None,
        "siCompraDolar": None,
        "siVentaDolar": None,
        "cierreAnteriorDolarCI": None,
        "cierreAnteriorDolar": None,
        "minDolarCI": None,
        "minDolar": None,
        "max_quant": max_quant,
    }


def load_instruments(path: str) -> List[Dict]:
    instrumentos = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 4 or row[2].strip() != "1":
                continue  # sin flag o flag distinto de 1 → no operar
            instrumentos.append(
                create_instrument(row[0].strip(), row[1].strip(), int(row[3].strip()))
            )
    return instrumentos


# ====================== MAIN ======================
if __name__ == "__main__":
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

    instrumentos = load_instruments(os.path.join(PROJECT_ROOT, "data/instrumentos.csv"))

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
    executer = Executer(account=account, client=client, instrumentos=instrumentos)
    websocket_client = WebSocketClient(websocket_url, executer, instrumentos)
    wst = websocket_client.connect()

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(UPDATE_SLEEP_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Exiting...")
        websocket_client.stop_websocket()
