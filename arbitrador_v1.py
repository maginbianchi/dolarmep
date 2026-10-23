import configparser
import math
import os
import time
from tabulate import tabulate
import websocket
import pandas as pd
import json
import threading
import csv
from typing import List, Tuple, Optional

RATIO = 1.0007
RATIO_CI = 1.001


class DataFrameHandler:
    def __init__(self, instrumentos: List[List]) -> None:
        self.instrumentos = instrumentos
        self.df = self.create_df()
        self._idx_t = {t: i for i, t in enumerate(self.df["ticker"])}
        self._idx_tD = {t: i for i, t in enumerate(self.df["tickerD"])}

    def create_df(self) -> pd.DataFrame:
        columns = [
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
        ]
        return pd.DataFrame(data=self.instrumentos, columns=columns)

    @staticmethod
    def _parse_message(record: str) -> Optional[Tuple[str, float, float, bool]]:
        """Extract ticker, bid/ask and CI flag from a market message string.

        Returns a tuple (ticker, bid, ask, is_ci) or None if the message does not
        contain a recognised topic.
        """
        vals = record.split("|")
        # normalise empty bid/ask
        bid = float(vals[3] or "-100")
        ask = float(vals[4] or "-100")
        topic = vals[0]
        if topic.endswith("_24hs"):
            ticker = topic.removeprefix("M:bm_MERV_").removesuffix("_24hs")
            return ticker, bid, ask, False
        if topic.endswith("_CI"):
            ticker = topic.removeprefix("M:bm_MERV_").removesuffix("_CI")
            return ticker, bid, ask, True
        return None

    def update_df(self, data: List) -> None:
        """Update the stored DataFrame using a list of raw websocket records.

        This method uses prebuilt index maps to avoid repeated filtering of the
        frame (see item 1)."""
        for r in data:
            parsed = self._parse_message(str(r))
            if not parsed:
                continue
            ticker, bid, ask, is_ci = parsed

            idx = self._idx_t.get(ticker)
            idxD = self._idx_tD.get(ticker)
            if idx is not None:
                if not is_ci:
                    self.df.at[idx, "prCompraPesos"] = bid
                    self.df.at[idx, "prVentaPesos"] = ask
                else:
                    self.df.at[idx, "prCompraPesosCI"] = bid
                    self.df.at[idx, "prVentaPesosCI"] = ask
            elif idxD is not None:
                if not is_ci:
                    self.df.at[idxD, "prCompraDolar"] = bid
                    self.df.at[idxD, "prVentaDolar"] = ask
                else:
                    self.df.at[idxD, "prCompraDolarCI"] = bid
                    self.df.at[idxD, "prVentaDolarCI"] = ask


class WebSocketClient:
    def __init__(self, url, DataFrameHandler, instrumentos):
        self.url = url
        self.DataFrameHandler = DataFrameHandler
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
        # print("\nMessage: " + message)
        data = []
        if message[0] != "X":
            if message[0] == "M":
                data.append(message)
                # print("\nReceived String")
            else:
                try:
                    data = json.loads(message)
                    # print("\nReceived JSON array")
                except json.JSONDecodeError as e:
                    print(f"\nJSON Decode Error: {e}")
                    return
        else:
            print("\nMessage is not market data")
            return
        self.DataFrameHandler.update_df(data)

    def on_error(self, ws, error):
        print("\nError: " + str(error))

    def on_close(self, ws, close_status_code, close_msg):
        print("\n### Closed connection ###")
        print(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        time.sleep(5)  # simple backoff
        self.connect()  # Reconnect automáticamente

    def on_open(self, ws):
        print("\n### Opened connection ###")
        ws.send(self.create_subscription_message())

    def stop_websocket(self):
        if self.ws:
            self.ws.close()
            print("WebSocket detenido")
        self.ws = None

    def create_subscription_message(self):
        aux = (
            ["md.bm_MERV_{0}_24hs".format(inst[0]) for inst in self.instrumentos]
            + ["md.bm_MERV_{0}_24hs".format(inst[1]) for inst in self.instrumentos]
            + ["md.bm_MERV_{0}_CI".format(inst[0]) for inst in self.instrumentos]
            + ["md.bm_MERV_{0}_CI".format(inst[1]) for inst in self.instrumentos]
        )
        return (
            '{"_req":"S","topicType":"md","topics":'
            + str(aux).replace(" ", "").replace("'", '"')
            + ',"replace":false}'
        )


class Executer:
    def __init__(self, df, mis_activos):
        self.mis_activos = mis_activos
        self.df = df

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
        window = 3

        USD_a_pesos_MAX = self.df.USD_a_pesos.max()
        USDCI_a_pesos_MAX = (
            self.df.USDCI_a_pesos.max()
            if not math.isnan(self.df.USDCI_a_pesos.max())
            else 0
        )
        pesos_a_USD_Min = self.df[self.df.pesos_a_USD > 1].pesos_a_USD.min()

        # Verifico que el maximo entre "USD a pesos" o "USDCI a pesos" sea mayor a "pesos a USD" (multiplicado por el ratio)
        if pesos_a_USD_Min * RATIO < max(USD_a_pesos_MAX, USDCI_a_pesos_MAX / RATIO):
            # Si la condición es verdadera, verifico cual de los dos es mayor e imprimo la tabla
            if USD_a_pesos_MAX * RATIO >= USDCI_a_pesos_MAX:
                self.df_USD_a_p = self.df.sort_values(
                    by=["USD_a_pesos"], ascending=False
                ).iloc[0:window]
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
                ).iloc[0:window]
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
                .iloc[0:window]
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
                    ].iloc[0:window],  # type: ignore
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
        if min(pesosCI_a_USDCI_Min, pesosCI_a_USD_Min) * RATIO_CI < max(
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
        if not self.df_dolares[self.df_dolares["%"] > 0.10].empty:
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

    CONFIG_FILE_PATH = os.path.join(PROJECT_ROOT, "config.ini")
    if not os.path.exists(CONFIG_FILE_PATH):
        raise FileNotFoundError(
            f"Credentials file not found at {CONFIG_FILE_PATH}. Please create it."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE_PATH)

    session_id = config["credentials"]["matriz_session_id"]
    conn_id = config["credentials"]["matriz_conn_id"]

    websocket_url = (
        f"wss://matriz.cocos.xoms.com.ar/ws?session_id={session_id}&conn_id={conn_id}"
    )

    dataframehandler = DataFrameHandler(instrumentos)
    websocketclient = WebSocketClient(websocket_url, dataframehandler, instrumentos)
    wst = websocketclient.connect()

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(1.5)
            executer = Executer(dataframehandler.df.copy(), mis_activos)
            executer.execute()
    except KeyboardInterrupt:
        print("Exiting...")
        websocketclient.stop_websocket()
