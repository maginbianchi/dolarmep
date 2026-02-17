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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("arbitrador.log", encoding="utf-8"),
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
            bid = md.get("BI", [{}])[0].get("price", "N/A") if "BI" in md else "N/A"
            offer = md.get("OF", [{}])[0].get("price", "N/A") if "OF" in md else "N/A"

            if symbol.__contains__("_24hs"):
                ticker = symbol.removeprefix("MERV - XMEV -").removesuffix("- 24hs")
                self.df.loc[
                    self.df.ticker == ticker, ["prCompraPesos", "prVentaPesos"]
                ] = np.array([bid, offer]).astype(float)
                self.df.loc[
                    self.df.tickerD == ticker, ["prCompraDolar", "prVentaDolar"]
                ] = np.array([bid, offer]).astype(float)
            elif symbol.__contains__("_CI"):
                ticker = symbol.removeprefix("M:bm_MERV_").removesuffix("_CI")
                self.df.loc[
                    self.df.ticker == ticker, ["prCompraPesosCI", "prVentaPesosCI"]
                ] = np.array([bid, offer]).astype(float)
                self.df.loc[
                    self.df.tickerD == ticker, ["prCompraDolarCI", "prVentaDolarCI"]
                ] = np.array([bid, offer]).astype(float)

        # print(self.df[["ticker","prCompraPesosCI","prVentaPesosCI","prCompraPesos","prVentaPesos","prCompraDolarCI","prVentaDolarCI","prCompraDolar","prVentaDolar"]])


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
        df: pd.DataFrame,
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
        if pesos_a_USD_Min * self.ratio < max(
            USD_a_pesos_MAX, USDCI_a_pesos_MAX / self.ratio
        ):
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

if __name__ == "__main__":
    mis_activos = [
        "ARC1O",
        "BACGO",
        "CS39O",
        "GN49O",
        "IRCPO",
        "LOC5O",
        "MGCOO",
        "MGCRO",
        "MSSEO",
        "NPCCO",
        "RUCAO",
        "TLCMO",
        "TLCPO",
        "VBC1O",
        "VSCTO",
        "YFCJO",
        "YM34O",
        "YMCXO",
    ]

    instrumentos = [
        ["YMCIO", "YMCID", None, None, None, None, None, None, None, None],
        ["YMCXO", "YMCXD", None, None, None, None, None, None, None, None],
        ["TLC1O", "TLC1D", None, None, None, None, None, None, None, None],
        ["TLCMO", "TLCMD", None, None, None, None, None, None, None, None],
        ["MTCGO", "MTCGD", None, None, None, None, None, None, None, None],
        ["ARC1O", "ARC1D", None, None, None, None, None, None, None, None],
        ["BOL1O", "BOL1D", None, None, None, None, None, None, None, None],
        ["DNC3O", "DNC3D", None, None, None, None, None, None, None, None],
        ["DNC5O", "DNC5D", None, None, None, None, None, None, None, None],
        ["RUCAO", "RUCAD", None, None, None, None, None, None, None, None],
        ["MSSEO", "MSSED", None, None, None, None, None, None, None, None],
        ["LOC3O", "LOC3D", None, None, None, None, None, None, None, None],
        ["VSCRO", "VSCRD", None, None, None, None, None, None, None, None],
        ["SNABO", "SNABD", None, None, None, None, None, None, None, None],
        ["RUCDO", "RUCDD", None, None, None, None, None, None, None, None],
        ["YFCJO", "YFCJD", None, None, None, None, None, None, None, None],
        ["YM34O", "YM34D", None, None, None, None, None, None, None, None],
        ["IRCPO", "IRCPD", None, None, None, None, None, None, None, None],
        ["YMCJO", "YMCJD", None, None, None, None, None, None, None, None],
        ["IRCFO", "IRCFD", None, None, None, None, None, None, None, None],
        ["IRCJO", "IRCJD", None, None, None, None, None, None, None, None],
        ["PNDCO", "PNDCD", None, None, None, None, None, None, None, None],
        ["CS38O", "CS38D", None, None, None, None, None, None, None, None],
        ["CS44O", "CS44D", None, None, None, None, None, None, None, None],
        ["CAC5O", "CAC5D", None, None, None, None, None, None, None, None],
        ["LMS7O", "LMS7D", None, None, None, None, None, None, None, None],
        ["LMS8O", "LMS8D", None, None, None, None, None, None, None, None],
        ["CP34O", "CP34D", None, None, None, None, None, None, None, None],
        ["PNWCO", "PNWCD", None, None, None, None, None, None, None, None],
        ["GN43O", "GN43D", None, None, None, None, None, None, None, None],
        ["RAC6O", "RAC6D", None, None, None, None, None, None, None, None],
        ["PNXCO", "PNXCD", None, None, None, None, None, None, None, None],
        ["OTS2O", "OTS2D", None, None, None, None, None, None, None, None],
        ["TSC3O", "TSC3D", None, None, None, None, None, None, None, None],
        ["VSCPO", "VSCPD", None, None, None, None, None, None, None, None],
        ["YMCVO", "YMCVD", None, None, None, None, None, None, None, None],
        ["HJCBO", "HJCBD", None, None, None, None, None, None, None, None],
        ["IRCLO", "IRCLD", None, None, None, None, None, None, None, None],
        ["LMS9O", "LMS9D", None, None, None, None, None, None, None, None],
        ["YFCIO", "YFCID", None, None, None, None, None, None, None, None],
        ["LIC6O", "LIC6D", None, None, None, None, None, None, None, None],
        ["PN35O", "PN35D", None, None, None, None, None, None, None, None],
        ["BYCHO", "BYCHD", None, None, None, None, None, None, None, None],
        ["MGCMO", "MGCMD", None, None, None, None, None, None, None, None],
        ["MGCNO", "MGCND", None, None, None, None, None, None, None, None],
        ["YMCYO", "YMCYD", None, None, None, None, None, None, None, None],
        ["YMCZO", "YMCZD", None, None, None, None, None, None, None, None],
        ["HJCFO", "HJCFD", None, None, None, None, None, None, None, None],
        ["HJCGO", "HJCGD", None, None, None, None, None, None, None, None],
        ["GN47O", "GN47D", None, None, None, None, None, None, None, None],
        ["DNC7O", "DNC7D", None, None, None, None, None, None, None, None],
        ["IRCNO", "IRCND", None, None, None, None, None, None, None, None],
        ["IRCOO", "IRCOD", None, None, None, None, None, None, None, None],
        ["PQCRO", "PQCRD", None, None, None, None, None, None, None, None],
        ["TTC9O", "TTC9D", None, None, None, None, None, None, None, None],
        ["GYC4O", "GYC4D", None, None, None, None, None, None, None, None],
        ["OTS3O", "OTS3D", None, None, None, None, None, None, None, None],
        ["XMC1O", "XMC1D", None, None, None, None, None, None, None, None],
        ["CIC7O", "CIC7D", None, None, None, None, None, None, None, None],
        ["PN36O", "PN36D", None, None, None, None, None, None, None, None],
        ["PN37O", "PN37D", None, None, None, None, None, None, None, None],
        ["CS47O", "CS47D", None, None, None, None, None, None, None, None],
        ["YFCKO", "YFCKD", None, None, None, None, None, None, None, None],
        ["YFCLO", "YFCLD", None, None, None, None, None, None, None, None],
        ["OZC3O", "OZC3D", None, None, None, None, None, None, None, None],
        ["TLCOO", "TLCOD", None, None, None, None, None, None, None, None],
        ["VSCTO", "VSCTD", None, None, None, None, None, None, None, None],
        ["SIC1O", "SIC1D", None, None, None, None, None, None, None, None],
        ["MGCOO", "MGCOD", None, None, None, None, None, None, None, None],
        ["HJCHO", "HJCHD", None, None, None, None, None, None, None, None],
        ["OT41O", "OT41D", None, None, None, None, None, None, None, None],
        ["OT42O", "OT42D", None, None, None, None, None, None, None, None],
        ["TTCAO", "TTCAD", None, None, None, None, None, None, None, None],
        ["PLC1O", "PLC1D", None, None, None, None, None, None, None, None],
        ["PLC2O", "PLC2D", None, None, None, None, None, None, None, None],
        ["SNSDO", "SNSDD", None, None, None, None, None, None, None, None],
        ["LDCGO", "LDCGD", None, None, None, None, None, None, None, None],
        ["PN38O", "PN38D", None, None, None, None, None, None, None, None],
        ["PQCSO", "PQCSD", None, None, None, None, None, None, None, None],
        ["DEC2O", "DEC2D", None, None, None, None, None, None, None, None],
        ["ZZC1O", "ZZC1D", None, None, None, None, None, None, None, None],
        ["YM35O", "YM35D", None, None, None, None, None, None, None, None],
        ["PUC2O", "PUC2D", None, None, None, None, None, None, None, None],
        ["GYC5O", "GYC5D", None, None, None, None, None, None, None, None],
        ["GN48O", "GN48D", None, None, None, None, None, None, None, None],
        ["CP37O", "CP37D", None, None, None, None, None, None, None, None],
        ["MCC1O", "MCC1D", None, None, None, None, None, None, None, None],
        ["MCC2O", "MCC2D", None, None, None, None, None, None, None, None],
        ["VBC1O", "VBC1D", None, None, None, None, None, None, None, None],
        ["MSSGO", "MSSGD", None, None, None, None, None, None, None, None],
        ["PLC3O", "PLC3D", None, None, None, None, None, None, None, None],
        ["YM37O", "YM37D", None, None, None, None, None, None, None, None],
        ["RCCRO", "RCCRD", None, None, None, None, None, None, None, None],
        ["YFCMO", "YFCMD", None, None, None, None, None, None, None, None],
        ["HJCIO", "HJCID", None, None, None, None, None, None, None, None],
        ["TLCPO", "TLCPD", None, None, None, None, None, None, None, None],
        ["CIC9O", "CIC9D", None, None, None, None, None, None, None, None],
        ["PLC4O", "PLC4D", None, None, None, None, None, None, None, None],
        ["BF35O", "BF35D", None, None, None, None, None, None, None, None],
        ["ZPC2O", "ZPC2D", None, None, None, None, None, None, None, None],
        ["VSCVO", "VSCVD", None, None, None, None, None, None, None, None],
        ["OLC5O", "OLC5D", None, None, None, None, None, None, None, None],
        ["CACBO", "CACBD", None, None, None, None, None, None, None, None],
        ["TLCQO", "TLCQD", None, None, None, None, None, None, None, None],
        ["EMC1O", "EMC1D", None, None, None, None, None, None, None, None],
        ["CS48O", "CS48D", None, None, None, None, None, None, None, None],
        ["HVS1O", "HVS1D", None, None, None, None, None, None, None, None],
        ["BACGO", "BACGD", None, None, None, None, None, None, None, None],
        ["PFC2O", "PFC2D", None, None, None, None, None, None, None, None],
        ["YM38O", "YM38D", None, None, None, None, None, None, None, None],
        ["YM39O", "YM39D", None, None, None, None, None, None, None, None],
        ["LOC5O", "LOC5D", None, None, None, None, None, None, None, None],
        ["HJCJO", "HJCJD", None, None, None, None, None, None, None, None],
        ["MGCQO", "MGCQD", None, None, None, None, None, None, None, None],
        ["RC1CO", "RC1CD", None, None, None, None, None, None, None, None],
        ["DNC8O", "DNC8D", None, None, None, None, None, None, None, None],
        ["BYCVO", "BYCVD", None, None, None, None, None, None, None, None],
        ["BF37O", "BF37D", None, None, None, None, None, None, None, None],
        ["YM40O", "YM40D", None, None, None, None, None, None, None, None],
        ["NPCCO", "NPCCD", None, None, None, None, None, None, None, None],
        ["PN41O", "PN41D", None, None, None, None, None, None, None, None],
        ["CS49O", "CS49D", None, None, None, None, None, None, None, None],
        ["T652O", "T652D", None, None, None, None, None, None, None, None],
        ["AERBO", "AERBD", None, None, None, None, None, None, None, None],
        ["NBS1O", "NBS1D", None, None, None, None, None, None, None, None],
        ["VSCOO", "VSCOD", None, None, None, None, None, None, None, None],
        ["VSCUO", "VSCUD", None, None, None, None, None, None, None, None],
        ["ZPC3O", "ZPC3D", None, None, None, None, None, None, None, None],
        ["SBC1O", "SBC1D", None, None, None, None, None, None, None, None],
        ["RC2CO", "RC2CD", None, None, None, None, None, None, None, None],
        ["JNC6O", "JNC6D", None, None, None, None, None, None, None, None],
        ["YM41O", "YM41D", None, None, None, None, None, None, None, None],
        ["PN42O", "PN42D", None, None, None, None, None, None, None, None],
        ["OTS5O", "OTS5D", None, None, None, None, None, None, None, None],
        ["VSCWO", "VSCWD", None, None, None, None, None, None, None, None],
        ["TTCDO", "TTCDD", None, None, None, None, None, None, None, None],
        ["MIC3O", "MIC3D", None, None, None, None, None, None, None, None],
        ["AFCIO", "AFCID", None, None, None, None, None, None, None, None],
        ["BGC4O", "BGC4D", None, None, None, None, None, None, None, None],
        ["PLC5O", "PLC5D", None, None, None, None, None, None, None, None],
        ["MGCRO", "MGCRD", None, None, None, None, None, None, None, None],
        ["TSC4O", "TSC4D", None, None, None, None, None, None, None, None],
        ["GN49O", "GN49D", None, None, None, None, None, None, None, None],
        ["CICAO", "CICAD", None, None, None, None, None, None, None, None],
        ["YM42O", "YM42D", None, None, None, None, None, None, None, None],
        ["BF39O", "BF39D", None, None, None, None, None, None, None, None],
        ["BPCUO", "BPCUD", None, None, None, None, None, None, None, None],
        ["CS50O", "CS50D", None, None, None, None, None, None, None, None],
        ["OLC6O", "OLC6D", None, None, None, None, None, None, None, None],
        ["YFCOO", "YFCOD", None, None, None, None, None, None, None, None],
        ["PN43O", "PN43D", None, None, None, None, None, None, None, None],
        ["CS51O", "CS51D", None, None, None, None, None, None, None, None],
        ["TLCTO", "TLCTD", None, None, None, None, None, None, None, None],
        ["BACHO", "BACHD", None, None, None, None, None, None, None, None],
        ["LOC6O", "LOC6D", None, None, None, None, None, None, None, None],
        ["FO4AO", "FO4AD", None, None, None, None, None, None, None, None],
        ["SNEBO", "SNEBD", None, None, None, None, None, None, None, None],
        ["MIC4O", "MIC4D", None, None, None, None, None, None, None, None],
        ["CACDO", "CACDD", None, None, None, None, None, None, None, None],
        # ["RUCEO", "RUCED", None, None, None, None, None, None, None, None],
        ["AFCJO", "AFCJD", None, None, None, None, None, None, None, None],
        ["AFCKO", "AFCKD", None, None, None, None, None, None, None, None],
        ["AFCLO", "AFCLD", None, None, None, None, None, None, None, None],
        ["BA37D", "BA7DD", None, None, None, None, None, None, None, None],
        ["NDT25", "NDT5D", None, None, None, None, None, None, None, None],
        ["CO26", "CO26D", None, None, None, None, None, None, None, None],
        ["PMM29", "PM29D", None, None, None, None, None, None, None, None],
        ["SA24D", "S24DD", None, None, None, None, None, None, None, None],
        ["AL30", "AL30D", None, None, None, None, None, None, None, None],
        ["GD30", "GD30D", None, None, None, None, None, None, None, None],
        ["AL35", "AL35D", None, None, None, None, None, None, None, None],
        ["GD35", "GD35D", None, None, None, None, None, None, None, None],
        ["BPY26", "BPY6D", None, None, None, None, None, None, None, None],
        ["BPOD7", "BPD7D", None, None, None, None, None, None, None, None],
    ]
    symbols_to_subscribe = (
        ["MERV - XMEV - {0} - 24hs".format(inst[0]) for inst in instrumentos]
        + ["MERV - XMEV - {0} - 24hs".format(inst[1]) for inst in instrumentos]
        + ["MERV - XMEV - {0} - CI".format(inst[0]) for inst in instrumentos]
        + ["MERV - XMEV - {0} - CI".format(inst[1]) for inst in instrumentos]
    )

    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
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
