import math
import time
import numpy as np
from tabulate import tabulate
import websocket
import pandas as pd
import json
import threading
import logging
from typing import Dict, List, Optional, Callable
import requests
from datetime import datetime
import base64
import os
import configparser
import csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/arbitrador_v2.log", encoding="utf-8"),
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


class DataFrameHandler:
    def __init__(self, instrumentos):
        self.instrumentos = instrumentos
        self.df = self.create_df()

    def create_df(self):
        return pd.DataFrame(
            data=self.instrumentos,
            columns=[
                "ticker",
                "tickerD",
                "prCompraPesosCI",
                "prVentaPesosCI",
                "prCompraPesos",
                "prVentaPesos",
                "prCompraDolarCI",
                "prVentaDolarCI",
                "prCompraDolar",
                "prVentaDolar",
            ],
        )

    def update_df(self, data):
        if "marketData" in data and data["marketData"]:
            md = data["marketData"]
            symbol = data["instrumentId"]["symbol"]
            bid = md.get("BI", [{}])[0].get("price") if md.get('BI') is not None and md.get('BI') != [] else None
            offer = md.get("OF", [{}])[0].get("price") if md.get('OF') is not None and md.get('OF') != [] else None


            if symbol.__contains__("- 24hs"):
                ticker = symbol.removeprefix("MERV - XMEV - ").removesuffix("- 24hs").strip()
                if bid:
                    self.df.loc[
                        self.df.ticker == ticker, ["prCompraPesos"]
                    ] = np.array([bid]).astype(float)
                    self.df.loc[
                        self.df.tickerD == ticker, ["prCompraDolar"]
                    ] = np.array([bid]).astype(float)

                if offer:
                    self.df.loc[
                        self.df.ticker == ticker, ["prVentaPesos"]
                    ] = np.array([offer]).astype(float)
                    self.df.loc[
                        self.df.tickerD == ticker, ["prVentaDolar"]
                    ] = np.array([offer]).astype(float)

            elif symbol.__contains__("- CI"):
                ticker = symbol.removeprefix("MERV - XMEV - ").removesuffix("- CI").strip()
                if bid:
                    self.df.loc[
                        self.df.ticker == ticker, ["prCompraPesosCI"]
                    ] = np.array([bid]).astype(float)
                    self.df.loc[
                        self.df.tickerD == ticker, ["prCompraDolarCI"]
                    ] = np.array([bid]).astype(float)

                if offer:
                    self.df.loc[
                        self.df.ticker == ticker, ["prVentaPesosCI"]
                    ] = np.array([offer]).astype(float)
                    self.df.loc[
                        self.df.tickerD == ticker, ["prVentaDolarCI"]
                    ] = np.array([offer]).astype(float)

        #print(self.df[["ticker","prCompraPesosCI","prVentaPesosCI","prCompraPesos","prVentaPesos","prCompraDolarCI","prVentaDolarCI","prCompraDolar","prVentaDolar"]])


class WebSocketClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.retries = 0
        self.max_retries = 5

    def start_market_data_websocket(
        self,
        symbols: List[str],
        on_data_callback: Callable[[Dict], None],
        depth: int = 1,
        entries: str = "LA,BI,OF",
    ):  
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
            if self.retries < self.max_retries:
                self.retries += 1
                time.sleep(2**self.retries)  # Exponential backoff
                self.start_market_data_websocket(
                    symbols, on_data_callback, depth, entries
                )
            else:
                logger.error("Max retries reached. Stopping WebSocket.")

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


class Executer:
    def __init__(
        self,
        df,
        mis_activos: List[str],
        ratio: float = 1.0008,
        ratio_ci: float = 1.0015,
    ):
        self.mis_activos = mis_activos
        self.df = df
        self.ratio = ratio
        self.ratio_ci = ratio_ci

    def calculate_ratios(self) -> None:
        self.df["USD_a_pesos"] = self.df.prCompraPesos / self.df.prVentaDolar
        self.df["USDCI_a_pesos"] = self.df.prCompraPesos / self.df.prVentaDolarCI
        self.df["pesos_a_USD"] = self.df.prVentaPesos / self.df.prCompraDolar
        self.df["pesos_a_USDCI"] = self.df.prVentaPesos / self.df.prCompraDolarCI

        self.df["USD_a_pesosCI"] = self.df.prCompraPesosCI / self.df.prVentaDolar
        self.df["USDCI_a_pesosCI"] = self.df.prCompraPesosCI / self.df.prVentaDolarCI
        self.df["pesosCI_a_USD"] = self.df.prVentaPesosCI / self.df.prCompraDolar
        self.df["pesosCI_a_USDCI"] = self.df.prVentaPesosCI / self.df.prCompraDolarCI


    def detect_main_arbitrage(self) -> None:
        print(
            "\n##############################################################################################\n"
        )

        USD_a_pesos_MAX = self.df.USD_a_pesos.max()
        USDCI_a_pesos_MAX = (
            self.df.USDCI_a_pesos.max()
            if not math.isnan(self.df.USDCI_a_pesos.max())
            else 0
        )
        pesos_a_USD_Min = self.df[self.df.pesos_a_USD > 1].pesos_a_USD.min()

        # Verifico que el maximo entre "USD a pesos" o "USDCI a pesos" sea mayor a "pesos a USD" (multiplicado por el ratio)
        if pesos_a_USD_Min * self.ratio < max(USD_a_pesos_MAX, USDCI_a_pesos_MAX / self.ratio):
            # Si la condición es verdadera, verifico cual de los dos es mayor e imprimo la tabla
            if USD_a_pesos_MAX * self.ratio >= USDCI_a_pesos_MAX:
                self.df_USD_a_p = self.df.sort_values(
                    by=["USD_a_pesos"], ascending=False
                ).iloc[0:2]
                print("USD 24hs")
                print(
                    tabulate(
                        self.df_USD_a_p[
                            ["ticker", "prCompraPesos", "prVentaDolar", "USD_a_pesos"]
                        ],  # type: ignore
                        headers="keys",
                        tablefmt="mixed_outline",
                        floatfmt=".2f",
                    ),
                )
            else:
                self.df_USDCI_a_p = self.df.sort_values(
                    by=["USDCI_a_pesos"], ascending=False
                ).iloc[0:2]
                print("USD CI")
                print(
                    tabulate(
                        self.df_USDCI_a_p[
                            [
                                "ticker",
                                "prCompraPesos",
                                "prVentaDolarCI",
                                "USDCI_a_pesos",
                            ]
                        ],  # type: ignore
                        headers="keys",
                        tablefmt="mixed_outline",
                        floatfmt=".2f",
                    ),
                )

            # Imprimo la tabla de "pesos a USD" ya que será utilizada en cualquiera de los dos casos
            self.df_p_a_USD = (
                self.df[self.df.pesos_a_USD > 1]
                .sort_values(by=["pesos_a_USD"], ascending=True)
                .iloc[0:2]
            )
            print(
                tabulate(
                    self.df_p_a_USD[
                        ["ticker", "prVentaPesos", "prCompraDolar", "pesos_a_USD"]
                    ],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                    floatfmt=".2f",
                )
            )
        else:
            print("NO HAY ARBITRAJE PRINCIPAL")

        # Muestro la tabla de "pesos a USDCI" solo en caso de que sea menor a "pesos a USD", ya que no es un arbitraje tan recurrente
        self.df_p_a_USDCI = (
            self.df[
                (self.df.pesos_a_USDCI > 1) & (self.df.pesos_a_USDCI < pesos_a_USD_Min)
            ]
            .loc[self.df["ticker"].isin(self.mis_activos), :]
            .sort_values(by=["pesos_a_USDCI"], ascending=True)
        )
        if not self.df_p_a_USDCI.empty:
            print("\nPesos a USDCI (Mis activos)")
            print(
                tabulate(
                    self.df_p_a_USDCI[
                        ["ticker", "prVentaPesos", "prCompraDolarCI", "pesos_a_USDCI"]
                    ].iloc[0:2],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                    floatfmt=".2f",
                ),
            )
        else:
            print("No hay arbitraje Pesos a DolarCI.")


    def detect_ci_arbitrage(self) -> None:
        print(
            "-----------------------------------------CI--------------------------------------------------"
        )

        USDCI_a_pesosCI_MAX = self.df.USDCI_a_pesosCI.max()
        USD_a_pesosCI_MAX = self.df.loc[
            self.df["ticker"].isin(self.mis_activos), :
        ].USD_a_pesosCI.max()
        pesosCI_a_USDCI_Min = self.df[self.df.pesosCI_a_USDCI > 1].pesosCI_a_USDCI.min()
        pesosCI_a_USD_Min = self.df[self.df.pesosCI_a_USD > 1].pesosCI_a_USD.min()

        # Verifico que el maximo entre "USDCI a pesosCI" o "USD a pesosCI" sea mayor que el minimo entre "pesosCI a USDCI" y "pesosCI a USD" (multiplicado por el ratio)
        if min(pesosCI_a_USDCI_Min, pesosCI_a_USD_Min) * self.ratio_ci < max(
            USDCI_a_pesosCI_MAX, USD_a_pesosCI_MAX
        ):
            # Si la condición es verdadera, verifico cual de los dos USD a pesos es mayor, e imprimo la tabla
            if USDCI_a_pesosCI_MAX >= USD_a_pesosCI_MAX:
                self.df_USDCI_a_pCI = self.df.sort_values(
                    by=["USDCI_a_pesosCI"], ascending=False
                ).iloc[0:2]
                print("\nUSD CI a Pesos CI")
                print(
                    tabulate(
                        self.df_USDCI_a_pCI[
                            [
                                "ticker",
                                "prCompraPesosCI",
                                "prVentaDolarCI",
                                "USDCI_a_pesosCI",
                            ]
                        ],  # type: ignore
                        headers="keys",
                        tablefmt="mixed_outline",
                        floatfmt=".2f",
                    ),
                )
            else:
                self.df_USD_a_pCI = (
                    self.df[self.df.USD_a_pesosCI > USDCI_a_pesosCI_MAX]
                    .loc[self.df["ticker"].isin(self.mis_activos), :]
                    .sort_values(by=["USD_a_pesosCI"], ascending=False)
                )
                print("\nUSD 24hs a Pesos CI (Mis activos)")
                print(
                    tabulate(
                        self.df_USD_a_pCI[
                            [
                                "ticker",
                                "prCompraPesosCI",
                                "prVentaDolar",
                                "USD_a_pesosCI",
                            ]
                        ],  # type: ignore
                        headers="keys",
                        tablefmt="mixed_outline",
                        floatfmt=".2f",
                    ),
                )

            # Si la condición es verdadera, verifico cual de los dos pesos a USD es menor e imprimo la tabla
            if pesosCI_a_USDCI_Min <= pesosCI_a_USD_Min:
                self.df_pCI_a_USDCI = (
                    self.df[self.df.pesosCI_a_USDCI > 1]
                    .sort_values(by=["pesosCI_a_USDCI"], ascending=True)
                    .iloc[0:2]
                )
                print("\nPesos CI a USD CI")
                print(
                    tabulate(
                        self.df_pCI_a_USDCI[
                            [
                                "ticker",
                                "prVentaPesosCI",
                                "prCompraDolarCI",
                                "pesosCI_a_USDCI",
                            ]
                        ],  # type: ignore
                        headers="keys",
                        tablefmt="mixed_outline",
                        floatfmt=".2f",
                    ),
                )
            else:
                self.df_pCI_a_USD = self.df[self.df.pesosCI_a_USD > 1].sort_values(
                    by=["pesosCI_a_USD"], ascending=True
                )
                print("\nPesos CI a USD 24hs")
                print(
                    tabulate(
                        self.df_pCI_a_USD[
                            [
                                "ticker",
                                "prVentaPesosCI",
                                "prCompraDolar",
                                "pesosCI_a_USD",
                            ]
                        ].iloc[0:3],  # type: ignore
                        headers="keys",
                        tablefmt="mixed_outline",
                        floatfmt=".2f",
                    ),
                )

        else:
            print("NO HAY ARBITRAJE EN CI")


    def detect_ci_to_24hs(self) -> None:
        print(
            "---------------------------------------------------------------------------------------------"
        )

        self.df_dolares = self.df.copy()[
            (self.df.prVentaDolarCI < self.df.prCompraDolar)
            & (self.df.prVentaDolarCI > 1)
        ]
        self.df_dolares["%"] = (
            (self.df_dolares.prCompraDolar / self.df_dolares.prVentaDolarCI) - 1
        ) * 100
        if not self.df_dolares[self.df_dolares["%"] > 0.20].empty:
            print(
                tabulate(
                    self.df_dolares[["ticker", "prVentaDolarCI", "prCompraDolar", "%"]],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                )
            )
        else:
            print("No hay arbitraje DolarCI por Dolar.")

        self.df_pesos = self.df.copy()[
            (self.df.prVentaPesosCI < self.df.prCompraPesos)
            & (self.df.prVentaPesosCI > 1)
        ]
        self.df_pesos["%"] = (
            (self.df_pesos.prCompraPesos / self.df_pesos.prVentaPesosCI) - 1
        ) * 36500
        if not self.df_pesos.empty:
            print(
                tabulate(
                    self.df_pesos[["ticker", "prVentaPesosCI", "prCompraPesos", "%"]]
                    .sort_values(by=["%"], ascending=False)
                    .iloc[0:2],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                    floatfmt=".2f",
                )
            )
        else:
            print("No hay arbitraje PesosCI por Pesos.")

    def detect_24hs_to_ci(self) -> None:
        print(
            "---------------------------------------------------------------------------------------------"
        )
        self.df_dolares = self.df.copy()[
            (self.df.prVentaDolar < self.df.prCompraDolarCI)
            & (self.df.prVentaDolar > 1)
        ].loc[self.df["ticker"].isin(self.mis_activos), :]
        self.df_dolares["%"] = (
            (self.df_dolares.prCompraDolarCI / self.df_dolares.prVentaDolar) - 1
        ) * 100
        if not self.df_dolares[self.df_dolares["%"] > 0.20].empty:
            print(
                tabulate(
                    self.df_dolares[["ticker", "prVentaDolar", "prCompraDolarCI", "%"]],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                )
            )
        else:
            print("No hay arbitraje Dolar a DolarCI.")

        self.df_pesos = self.df.copy()[
            (self.df.prVentaPesos <= self.df.prCompraPesosCI)
            & (self.df.prVentaPesos > 1)
        ].loc[self.df["ticker"].isin(self.mis_activos), :]
        self.df_pesos["%"] = (
            (self.df_pesos.prCompraPesosCI / self.df_pesos.prVentaPesos) - 1
        ) * 100
        if not self.df_pesos.empty:
            print(
                tabulate(
                    self.df_pesos[["ticker", "prVentaPesos", "prCompraPesosCI", "%"]],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                )
            )
        else:
            print("No hay arbitraje Pesos a PesosCI.")

        print(
            "\n##############################################################################################\n"
        )

    def execute(self):
        self.calculate_ratios()
        self.detect_main_arbitrage()
        self.detect_ci_arbitrage()
        self.detect_ci_to_24hs()
        self.detect_24hs_to_ci()

# ====================== FUNCIÓN AUXILIAR ======================
def create_instrument(ticker: str, tickerD: str) -> List:
    """
    Crea un array de instrumento con valores inicializados.

    Args:
        ticker: Ticker en pesos
        tickerD: Ticker en dólares

    Returns:
        List: Lista de valores inicializados para el instrumento
    """
    return [ticker, tickerD, None, None, None, None, None, None, None, None]


def load_instruments(path: str) -> List[List]:
    """Lee instrumentos desde un CSV (ticker,tickerD). Ignora líneas vacías y las que empiezan con #."""
    instrumentos = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue
            instrumentos.append(create_instrument(row[0].strip(), row[1].strip()))
    return instrumentos


def load_mis_activos(path: str) -> List[str]:
    """Lee tickers desde un CSV (columna ticker). Ignora líneas vacías y las que empiezan con #."""
    with open(path, newline="", encoding="utf-8") as f:
        return [
            row[0].strip()
            for row in csv.reader(f)
            if row and not row[0].strip().startswith("#")
        ]
    
if __name__ == "__main__":

    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    
    instrumentos = load_instruments(os.path.join(PROJECT_ROOT, "data/instrumentos.csv"))
    mis_activos = load_mis_activos(os.path.join(PROJECT_ROOT, "data/mis_activos.csv"))

    symbols_to_subscribe = (
        ["MERV - XMEV - {0} - 24hs".format(inst[0]) for inst in instrumentos]
        + ["MERV - XMEV - {0} - 24hs".format(inst[1]) for inst in instrumentos]
        + ["MERV - XMEV - {0} - CI".format(inst[0]) for inst in instrumentos]
        + ["MERV - XMEV - {0} - CI".format(inst[1]) for inst in instrumentos]
    )

    CONFIG_FILE_PATH = os.path.join(PROJECT_ROOT, "config.ini")
    if not os.path.exists(CONFIG_FILE_PATH):
        raise FileNotFoundError(
            f"Credentials file not found at {CONFIG_FILE_PATH}. Please create it."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE_PATH)
    usuario = config['credentials']['matriz_username']
    password = config['credentials']['matriz_password']

    client = CocosMatrizClient(username=usuario, password=password)

    dataframehandler = DataFrameHandler(instrumentos)
    websocket_client = WebSocketClient(token=client.token)
    websocket_client.start_market_data_websocket(
        symbols=symbols_to_subscribe,
        on_data_callback=dataframehandler.update_df,
        depth=1,  # 1 = solo top of book
        entries="LA,BI,OF",  # Last, Bid, Offer
    )

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(3.5)
            executer = Executer(dataframehandler.df.copy(), mis_activos)
            executer.execute()
    except KeyboardInterrupt:
        print("Exiting...")
        websocket_client.ws.close()
