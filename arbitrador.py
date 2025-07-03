from datetime import datetime
import math
import time
import numpy as np
from tabulate import tabulate
import websocket
import pandas as pd
import json
import threading


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
        for r in data:
            values = str(r).split("|")
            if values[3] == "":
                values[3] = "-100"
            if values[4] == "":
                values[4] = "-100"
            if values[0].__contains__("_24hs"):
                ticker = values[0].removeprefix("M:bm_MERV_").removesuffix("_24hs")
                self.df.loc[
                    self.df.ticker == ticker, ["prCompraPesos", "prVentaPesos"]
                ] = np.array([values[3], values[4]]).astype(float)
                self.df.loc[
                    self.df.tickerD == ticker, ["prCompraDolar", "prVentaDolar"]
                ] = np.array([values[3], values[4]]).astype(float)
            elif values[0].__contains__("_CI"):
                ticker = values[0].removeprefix("M:bm_MERV_").removesuffix("_CI")
                self.df.loc[
                    self.df.ticker == ticker, ["prCompraPesosCI", "prVentaPesosCI"]
                ] = np.array([values[3], values[4]]).astype(float)
                self.df.loc[
                    self.df.tickerD == ticker, ["prCompraDolarCI", "prVentaDolarCI"]
                ] = np.array([values[3], values[4]]).astype(float)

        # print(self.df[["ticker","prCompraPesosCI","prVentaPesosCI","prCompraPesos","prVentaPesos","prCompraDolarCI","prVentaDolarCI","prCompraDolar","prVentaDolar"]])


def create_subscription_message(instrumentos):
    now = datetime.now()
    closeTimeCI = datetime.now().replace(hour=16, minute=25, second=0, microsecond=0)

    if now < closeTimeCI:
        aux = (
            ["md.bm_MERV_{0}_24hs".format(inst[0]) for inst in instrumentos]
            + ["md.bm_MERV_{0}_24hs".format(inst[1]) for inst in instrumentos]
            + ["md.bm_MERV_{0}_CI".format(inst[0]) for inst in instrumentos]
            + ["md.bm_MERV_{0}_CI".format(inst[1]) for inst in instrumentos]
        )
        return (
            '{"_req":"S","topicType":"md","topics":'
            + str(aux).replace(" ", "").replace("'", '"')
            + ',"replace":false}'
        )
    else:
        aux = ["md.bm_MERV_{0}_24hs".format(inst[0]) for inst in instrumentos] + [
            "md.bm_MERV_{0}_24hs".format(inst[1]) for inst in instrumentos
        ]
        return (
            '{"_req":"S","topicType":"md","topics":'
            + str(aux).replace(" ", "").replace("'", '"')
            + ',"replace":false}'
        )


class WebSocketClient:
    def __init__(self, url, DataFrameHandler):
        self.url = url
        self.DataFrameHandler = DataFrameHandler

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
        self.ws.keep_running = False
        self.ws.close()

    def on_open(self, ws):
        print("\n### Opened connection ###")
        ws.send(create_subscription_message(instrumentos))


class Executer:
    def __init__(self, df, mis_activos):
        self.mis_activos = mis_activos
        self.df = df

    def execute(self):
        self.df["USD_a_pesos"] = self.df.prCompraPesos / self.df.prVentaDolar
        self.df["USDCI_a_pesos"] = self.df.prCompraPesos / self.df.prVentaDolarCI
        self.df["pesos_a_USD"] = self.df.prVentaPesos / self.df.prCompraDolar
        self.df["pesos_a_USDCI"] = self.df.prVentaPesos / self.df.prCompraDolarCI

        self.df["USD_a_pesosCI"] = self.df.prCompraPesosCI / self.df.prVentaDolar
        self.df["USDCI_a_pesosCI"] = self.df.prCompraPesosCI / self.df.prVentaDolarCI
        self.df["pesosCI_a_USD"] = self.df.prVentaPesosCI / self.df.prCompraDolar
        self.df["pesosCI_a_USDCI"] = self.df.prVentaPesosCI / self.df.prCompraDolarCI

        print(
            "\n##############################################################################################\n"
        )

        ratio = 1.0015
        ratio_CI = 1.002
        USD_a_pesos_MAX = self.df.USD_a_pesos.max()
        USDCI_a_pesos_MAX = (
            self.df.USDCI_a_pesos.max()
            if not math.isnan(self.df.USDCI_a_pesos.max())
            else 0
        )
        pesos_a_USD_Min = self.df[self.df.pesos_a_USD > 1].pesos_a_USD.min()

        # Verifico que el maximo entre "USD a pesos" o "USDCI a pesos" sea mayor a "pesos a USD" (multiplicado por el ratio)
        if pesos_a_USD_Min * ratio < max(USD_a_pesos_MAX, USDCI_a_pesos_MAX):
            # Si la condición es verdadera, verifico cual de los dos es mayor e imprimo la tabla
            if USD_a_pesos_MAX >= USDCI_a_pesos_MAX:
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
        if min(pesosCI_a_USDCI_Min, pesosCI_a_USD_Min) * ratio_CI < max(
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


if __name__ == "__main__":
    mis_activos = [
        "YMCIO",
        "YMCXO",
        "GNCXO",
        "TLC1O",
        "TLCMO",
        "ARC1O",
        "CRCJO",
        "RUCAO",
        "MRCAO",
        "MR35O",
        "MSSEO",
        "LECGO",
        "LOC2O",
        "LOC3O",
        "VSCRO",
        "VSCTO",
        "YFCJO",
        "YM34O",
        "NPCBO",
        "IRCPO",
        "MGCOO",
    ]

    instrumentos = [
        ["YMCIO", "YMCID", None, None, None, None, None, None, None, None],
        ["YMCXO", "YMCXD", None, None, None, None, None, None, None, None],
        ["GNCXO", "GNCXD", None, None, None, None, None, None, None, None],
        ["TLC1O", "TLC1D", None, None, None, None, None, None, None, None],
        ["TLCMO", "TLCMD", None, None, None, None, None, None, None, None],
        ["MTCGO", "MTCGD", None, None, None, None, None, None, None, None],
        ["ARC1O", "ARC1D", None, None, None, None, None, None, None, None],
        ["BOL1O", "BOL1D", None, None, None, None, None, None, None, None],
        ["CRCJO", "CRCJD", None, None, None, None, None, None, None, None],
        ["DNC3O", "DNC3D", None, None, None, None, None, None, None, None],
        ["DNC5O", "DNC5D", None, None, None, None, None, None, None, None],
        ["RUCAO", "RUCAD", None, None, None, None, None, None, None, None],
        ["MSSEO", "MSSED", None, None, None, None, None, None, None, None],
        ["IRCIO", "IRCID", None, None, None, None, None, None, None, None],
        ["LOC2O", "LOC2D", None, None, None, None, None, None, None, None],
        ["LOC3O", "LOC3D", None, None, None, None, None, None, None, None],
        ["AEC1O", "AEC1D", None, None, None, None, None, None, None, None],
        ["VSCRO", "VSCRD", None, None, None, None, None, None, None, None],
        ["PECGO", "PECGD", None, None, None, None, None, None, None, None],
        ["SNABO", "SNABD", None, None, None, None, None, None, None, None],
        ["RUCDO", "RUCDD", None, None, None, None, None, None, None, None],
        ["YFCJO", "YFCJD", None, None, None, None, None, None, None, None],
        ["YM34O", "YM34D", None, None, None, None, None, None, None, None],
        ["NPCBO", "NPCBD", None, None, None, None, None, None, None, None],
        ["IRCPO", "IRCPD", None, None, None, None, None, None, None, None],
        ["YMCHO", "YMCHD", None, None, None, None, None, None, None, None],
        ["YMCJO", "YMCJD", None, None, None, None, None, None, None, None],
        ["YMCQO", "YMCQD", None, None, None, None, None, None, None, None],
        ["GN40O", "GN40D", None, None, None, None, None, None, None, None],
        ["TLC5O", "TLC5D", None, None, None, None, None, None, None, None],
        ["IRCFO", "IRCFD", None, None, None, None, None, None, None, None],
        ["IRCHO", "IRCHD", None, None, None, None, None, None, None, None],
        ["IRCJO", "IRCJD", None, None, None, None, None, None, None, None],
        ["MGCHO", "MGCHD", None, None, None, None, None, None, None, None],
        ["MGCJO", "MGCJD", None, None, None, None, None, None, None, None],
        ["PNDCO", "PNDCD", None, None, None, None, None, None, None, None],
        ["MGC9O", "MGC9D", None, None, None, None, None, None, None, None],
        ["RCCJO", "RCCJD", None, None, None, None, None, None, None, None],
        ["CS38O", "CS38D", None, None, None, None, None, None, None, None],
        ["CS44O", "CS44D", None, None, None, None, None, None, None, None],
        ["CAC5O", "CAC5D", None, None, None, None, None, None, None, None],
        ["CAC8O", "CAC8D", None, None, None, None, None, None, None, None],
        ["NPCAO", "NPCAD", None, None, None, None, None, None, None, None],
        ["VSCLO", "VSCLD", None, None, None, None, None, None, None, None],
        ["LMS7O", "LMS7D", None, None, None, None, None, None, None, None],
        ["LMS8O", "LMS8D", None, None, None, None, None, None, None, None],
        ["CP34O", "CP34D", None, None, None, None, None, None, None, None],
        ["PNWCO", "PNWCD", None, None, None, None, None, None, None, None],
        ["GN43O", "GN43D", None, None, None, None, None, None, None, None],
        ["RAC6O", "RAC6D", None, None, None, None, None, None, None, None],
        ["TTC7O", "TTC7D", None, None, None, None, None, None, None, None],
        ["PNXCO", "PNXCD", None, None, None, None, None, None, None, None],
        ["PECAO", "PECAD", None, None, None, None, None, None, None, None],
        ["PECBO", "PECBD", None, None, None, None, None, None, None, None],
        ["OTS2O", "OTS2D", None, None, None, None, None, None, None, None],
        ["TSC3O", "TSC3D", None, None, None, None, None, None, None, None],
        ["VSCPO", "VSCPD", None, None, None, None, None, None, None, None],
        ["YMCVO", "YMCVD", None, None, None, None, None, None, None, None],
        ["HJCBO", "HJCBD", None, None, None, None, None, None, None, None],
        ["IRCLO", "IRCLD", None, None, None, None, None, None, None, None],
        ["LMS9O", "LMS9D", None, None, None, None, None, None, None, None],
        ["YFCIO", "YFCID", None, None, None, None, None, None, None, None],
        ["RZ9BO", "RZ9BD", None, None, None, None, None, None, None, None],
        ["LIC6O", "LIC6D", None, None, None, None, None, None, None, None],
        ["CRCLO", "CRCLD", None, None, None, None, None, None, None, None],
        ["PN35O", "PN35D", None, None, None, None, None, None, None, None],
        ["SNSBO", "SNSBD", None, None, None, None, None, None, None, None],
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
        ["CIC8O", "CIC8D", None, None, None, None, None, None, None, None],
        ["PN36O", "PN36D", None, None, None, None, None, None, None, None],
        ["PN37O", "PN37D", None, None, None, None, None, None, None, None],
        ["CS47O", "CS47D", None, None, None, None, None, None, None, None],
        ["YFCKO", "YFCKD", None, None, None, None, None, None, None, None],
        ["YFCLO", "YFCLD", None, None, None, None, None, None, None, None],
        ["TN63O", "TN63D", None, None, None, None, None, None, None, None],
        ["RZABO", "RZABD", None, None, None, None, None, None, None, None],
        ["OZC3O", "OZC3D", None, None, None, None, None, None, None, None],
        ["TLCOO", "TLCOD", None, None, None, None, None, None, None, None],
        ["VSCTO", "VSCTD", None, None, None, None, None, None, None, None],
        ["SIC1O", "SIC1D", None, None, None, None, None, None, None, None],
        ["MGCOO", "MGCOD", None, None, None, None, None, None, None, None],
        ["MTC1O", "MTC1D", None, None, None, None, None, None, None, None],
        ["EAC3O", "EAC3D", None, None, None, None, None, None, None, None],
        ["HJCHO", "HJCHD", None, None, None, None, None, None, None, None],
        ["OT41O", "OT41D", None, None, None, None, None, None, None, None],
        ["OT42O", "OT42D", None, None, None, None, None, None, None, None],
        ["TTCAO", "TTCAD", None, None, None, None, None, None, None, None],
        ["PLC1O", "PLC1D", None, None, None, None, None, None, None, None],
        ["PLC2O", "PLC2D", None, None, None, None, None, None, None, None],
        ["PECIO", "PECID", None, None, None, None, None, None, None, None],
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
        ["MR36O", "MR36D", None, None, None, None, None, None, None, None],
        ["LECHO", "LECHD", None, None, None, None, None, None, None, None],
        ["MRCAO", "MRCAD", None, None, None, None, None, None, None, None],
        ["MR35O", "MR35D", None, None, None, None, None, None, None, None],
        ["LECBO", "LECBD", None, None, None, None, None, None, None, None],
        ["LECGO", "LECGD", None, None, None, None, None, None, None, None],
        ["MRCLO", "MRCLD", None, None, None, None, None, None, None, None],
        ["MRCQO", "MRCQD", None, None, None, None, None, None, None, None],
        ["MRCOO", "MROCD", None, None, None, None, None, None, None, None],
        ["LECAO", "LECAD", None, None, None, None, None, None, None, None],
        ["LECEO", "LECED", None, None, None, None, None, None, None, None],
        ["MRCUO", "MRCUD", None, None, None, None, None, None, None, None],
        ["MRCYO", "MRCYD", None, None, None, None, None, None, None, None],
        ["MR39O", "MR39D", None, None, None, None, None, None, None, None],
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
        ["BPOA7", "BPA7D", None, None, None, None, None, None, None, None],
        ["BPOB7", "BPB7D", None, None, None, None, None, None, None, None],
        ["BPOC7", "BPC7D", None, None, None, None, None, None, None, None],
        ["BPOD7", "BPD7D", None, None, None, None, None, None, None, None],
        ["SPY", "SPYD", None, None, None, None, None, None, None, None],
        ["EWZ", "EWZD", None, None, None, None, None, None, None, None],
    ]
    websocket_url = "wss://matriz.cocos.xoms.com.ar/ws?session_id=gqKxOszDYQQ7rKXTo3ypHhA%2FnaS%2BvkIeZGVFew7mxGElbIUxZv1DT4dpZo%2Fm8eny&conn_id=Vj2HkM3nQqa9VqD5N2NzJF2sdbX5UZ7%2B1OpC6CxnoNi4c2TuzJ4Tdg7GX%2FWDF0%2Bp"

    dataframehandler = DataFrameHandler(instrumentos)
    websocketclient = WebSocketClient(websocket_url, dataframehandler)
    wst = websocketclient.connect()

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(5)
            executer = Executer(dataframehandler.df.copy(), mis_activos)
            executer.execute()
    except KeyboardInterrupt:
        print("Exiting...")
