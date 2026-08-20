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
import copy

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
        aux = (
            ["md.bm_MERV_{0}_24hs".format(inst["tickerD"]) for inst in self.instrumentos]
            + ["md.bm_MERV_{0}_CI".format(inst["tickerD"]) for inst in self.instrumentos]
        )
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
        self.instrumentos_by_ticker = {
            inst["ticker"]: inst for inst in instrumentos
        }
        self.instrumentos_by_tickerD = {
            inst["tickerD"]: inst for inst in instrumentos
        }

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
                ticker = (
                    values[0].removeprefix("M:bm_MERV_").removesuffix("_CI")
                )
                if ticker in self.instrumentos_by_tickerD:
                    inst = self.instrumentos_by_tickerD[ticker]
                    inst["prCompraDolarCI"] = float(values[3])
                    inst["prVentaDolarCI"] = float(values[4])
                    inst["siCompraDolarCI"] = (
                        float(values[2]) if values[2] else None
                    )
                    inst["siVentaDolarCI"] = float(values[5]) if values[5] else None
                    inst["cierreAnteriorDolarCI"] = float(values[15]) if len(values) > 15 and values[15] else None
                    inst["minDolarCI"] = float(values[11]) if values[11] else None
                    self.execute(inst.copy())  # Pasar una copia para evitar modificaciones durante la ejecución

            elif "_24hs" in values[0]:
                ticker = (
                    values[0].removeprefix("M:bm_MERV_").removesuffix("_24hs")
                )
                if ticker in self.instrumentos_by_tickerD:
                    inst = self.instrumentos_by_tickerD[ticker]
                    inst["prCompraDolar"] = float(values[3])
                    inst["prVentaDolar"] = float(values[4])
                    inst["siCompraDolar"] = (
                        float(values[2]) if values[2] else None
                    )
                    inst["siVentaDolar"] = float(values[5]) if values[5] else None
                    inst["cierreAnteriorDolar"] = float(values[15]) if len(values) > 15 and values[15] else None
                    inst["minDolar"] = float(values[11]) if values[11] else None

    def execute(
        self, ticker: Dict,
    ):
        ratio = ARBITER_RATIO

        logger.info("Ejecutando estrategia de arbitraje → comparando ratios USD/pesos.")
        #logger.info(json.dumps(ticker, indent=2, ensure_ascii=False))
        if ticker["prVentaDolarCI"] is None or ticker["prVentaDolarCI"] <= 1 or ticker["prCompraDolar"] is None or ticker["prCompraDolar"] <= 1:
            return
        
        if ticker["prVentaDolarCI"] * ratio < min(ticker["prCompraDolar"], ticker["cierreAnteriorDolar"]):
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
            logger.error("Error enviando orden de compra en dolares.")
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


# ====================== MAIN ======================
if __name__ == "__main__":
    instrumentos = [
        create_instrument("AL30", "AL30D"),
        create_instrument("ARC1O", "ARC1D", 1000),
        create_instrument("BACGO", "BACGD", 1000),
        create_instrument("BF37O", "BF37D", 1000),
        create_instrument("BGC4O", "BGC4D", 1000),
        create_instrument("BYCHO", "BYCHD", 2000),
        create_instrument("BYCVO", "BYCVD", 2000),
        create_instrument("BYCXO", "BYCXD", 1000),
        create_instrument("CACBO", "CACBD"),
        create_instrument("CACDO", "CACDD"),
        create_instrument("CP36O", "CP36D"),
        create_instrument("CP37O", "CP37D"),
        create_instrument("CP38O", "CP38D"),
        create_instrument("CS44O", "CS44D"),
        create_instrument("CS47O", "CS47D"),
        create_instrument("CS48O", "CS48D"),
        create_instrument("CS49O", "CS49D"),
        create_instrument("CS50O", "CS50D"),
        create_instrument("CS53O", "CS53D"),
        create_instrument("CIC7O", "CIC7D"),
        create_instrument("CIC9O", "CIC9D"),
        create_instrument("CICAO", "CICAD"),
        create_instrument("CICBO", "CICBD"),
        create_instrument("DEC2O", "DEC2D"),
        create_instrument("DNC3O", "DNC3D"),
        create_instrument("DNC5O", "DNC5D"),
        create_instrument("DNC7O", "DNC7D"),
        create_instrument("DNCAO", "DNCAD"),
        create_instrument("DNCBO", "DNCBD"),
        create_instrument("FO4AO", "FO4AD"),
        create_instrument("FYC1O", "FYC1D"),
        create_instrument("GN47O", "GN47D"),
        create_instrument("GN49O", "GN49D"),
        create_instrument("HJCHO", "HJCHD", 1200),
        create_instrument("HJCFO", "HJCFD", 1200),
        create_instrument("HJCGO", "HJCGD", 1200),
        create_instrument("HJCJO", "HJCJD", 1000),
        create_instrument("HJCKO", "HJCKD", 1000),
        create_instrument("HJCLO", "HJCLD", 1000),
        create_instrument("IRCFO", "IRCFD"),
        create_instrument("IRCJO", "IRCJD"),
        create_instrument("IRCLO", "IRCLD"),
        create_instrument("IRCOO", "IRCOD"),
        create_instrument("IRCPO", "IRCPD"),
        create_instrument("IRCQO", "IRCQD"),
        create_instrument("LDCGO", "LDCGD"),
        create_instrument("LOC5O", "LOC5D"),
        create_instrument("LOC6O", "LOC6D"),
        create_instrument("MCC1O", "MCC1D"),
        create_instrument("MCC2O", "MCC2D"),
        create_instrument("MCC3O", "MCC3D"),
        create_instrument("MGCQO", "MGCQD"),
        create_instrument("MGCTO", "MGCTD", 1000),
        create_instrument("MIC3O", "MIC3D"),
        create_instrument("MIC4O", "MIC4D"),
        create_instrument("MTC2O", "MTC2D"),
        create_instrument("NDT25", "NDT5D"),
        create_instrument("NPCCO", "NPCCD"),
        create_instrument("NPCDO", "NPCDD"),
        create_instrument("OLC5O", "OLC5D"),
        create_instrument("OLC6O", "OLC6D"),
        create_instrument("OLC7O", "OLC7D"),
        create_instrument("OTS2O", "OTS2D"),
        create_instrument("OTS3O", "OTS3D"),
        create_instrument("OTS5O", "OTS5D"),
        create_instrument("OTS6O", "OTS6D"),
        create_instrument("OZC3O", "OZC3D"),
        create_instrument("PFC2O", "PFC2D"),
        create_instrument("PFC3O", "PFC3D"),
        create_instrument("PLC1O", "PLC1D"),
        create_instrument("PLC2O", "PLC2D"),
        create_instrument("PLC3O", "PLC3D"),
        create_instrument("PLC4O", "PLC4D", 1000),
        create_instrument("PLC5O", "PLC5D", 1000),
        create_instrument("PLC6O", "PLC6D"),
        create_instrument("PLC7O", "PLC7D"),
        create_instrument("PMM29", "PM29D"),
        create_instrument("PN35O", "PN35D"),
        create_instrument("PN36O", "PN36D"),
        create_instrument("PN37O", "PN37D"),
        create_instrument("PN38O", "PN38D"),
        create_instrument("PN41O", "PN41D"),
        create_instrument("PN42O", "PN42D"),
        create_instrument("PN43O", "PN43D"),
        create_instrument("PNDCO", "PNDCD"),
        create_instrument("PNWCO", "PNWCD"),
        create_instrument("PNXCO", "PNXCD", 1000),
        create_instrument("PQCRO", "PQCRD"),
        create_instrument("PQCSO", "PQCSD"),
        create_instrument("RC1CO", "RC1CD", 1000),
        create_instrument("RC2CO", "RC2CD"),
        create_instrument("RC5CO", "RC5CD"),
        create_instrument("RCCRO", "RCCRD"),
        create_instrument("RUCDO", "RUCDD"),
        create_instrument("RUCEO", "RUCED"),
        create_instrument("SBC2O", "SBC2D"),
        create_instrument("SBC3O", "SBC3D"),
        create_instrument("T641O", "T641D"),
        create_instrument("T652O", "T652D"),
        create_instrument("T662O", "T662D"),
        create_instrument("T672O", "T672D"),
        create_instrument("TLCMO", "TLCMD", 1000),
        create_instrument("TLCOO", "TLCOD"),
        create_instrument("TLCPO", "TLCPD"),
        create_instrument("TLCQO", "TLCQD"),
        create_instrument("TLCTO", "TLCTD"),
        create_instrument("TLCWO", "TLCWD"),
        create_instrument("TSC4O", "TSC4D", 1000),
        create_instrument("TTC9O", "TTC9D"),
        create_instrument("TTCAO", "TTCAD", 1000),
        create_instrument("TTCDO", "TTCDD", 1000),
        create_instrument("TTCEO", "TTCED"),
        create_instrument("VBC1O", "VBC1D", 1000),
        create_instrument("VBC2O", "VBC2D", 1000),
        create_instrument("VSCOO", "VSCOD"),
        create_instrument("VSCPO", "VSCPD"),
        create_instrument("VSCRO", "VSCRD"),
        create_instrument("VSCVO", "VSCVD", 1000),
        create_instrument("VSCXO", "VSCXD", 1000),
        create_instrument("XMC1O", "XMC1D"),
        create_instrument("YM34O", "YM34D"),
        create_instrument("YM35O", "YM35D"),
        create_instrument("YM37O", "YM37D"),
        create_instrument("YM38O", "YM38D"),
        create_instrument("YM39O", "YM39D"),
        create_instrument("YM40O", "YM40D"),
        create_instrument("YM41O", "YM41D"),
        create_instrument("YM42O", "YM42D"),
        create_instrument("YM43O", "YM43D"),
        create_instrument("YMCIO", "YMCID"),
        create_instrument("YMCJO", "YMCJD"),
        create_instrument("YMCXO", "YMCXD"),
        create_instrument("YMCYO", "YMCYD"),
        create_instrument("YFCJO", "YFCJD", 1000),
        create_instrument("ZZC1O", "ZZC1D"),
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