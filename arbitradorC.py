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
                "tickerC",
                "tickerD",
                "prCompraDolarC",
                "prVentaDolarC",
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
                    self.df.tickerC == ticker, ["prCompraDolarC", "prVentaDolarC"]
                ] = np.array([values[3], values[4]]).astype(float)
                self.df.loc[
                    self.df.tickerD == ticker, ["prCompraDolar", "prVentaDolar"]
                ] = np.array([values[3], values[4]]).astype(float)
        # print(self.df[["ticker","prCompraPesosCI","prVentaPesosCI","prCompraPesos","prVentaPesos","prCompraDolarCI","prVentaDolarCI","prCompraDolar","prVentaDolar"]])


def create_subscription_message(instrumentos):
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
    def __init__(self, df):
        self.df = df

    def execute(self):
        self.df["USDC/USD_ask"] = self.df.prVentaDolarC / self.df.prVentaDolar
        self.df["USDC/USD_bid"] = self.df.prCompraDolarC / self.df.prCompraDolar

        print(
            "\n##############################################################################################\n"
        )

        self.df_USD_a_p = self.df[(self.df.prVentaDolarC > 0) & (self.df.prVentaDolar > 0)].sort_values(
            by=["USDC/USD_ask"], ascending=True
        ).iloc[0:10]
        print("USDC/USD Ask")
        print(
            tabulate(
                self.df_USD_a_p[
                    ["tickerD", "prVentaDolarC", "prVentaDolar", "USDC/USD_ask"]
                ],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
                floatfmt=".2f",
            ),
        )

        self.df_USD_a_p = self.df[(self.df.prCompraDolarC > 0) & (self.df.prCompraDolar > 0)].sort_values(
            by=["USDC/USD_bid"], ascending=False
        ).iloc[0:10]
        print("USDC/USD Bid")
        print(
            tabulate(
                self.df_USD_a_p[
                    ["tickerD", "prCompraDolarC", "prCompraDolar", "USDC/USD_bid"]
                ],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
                floatfmt=".2f",
            ),
        )



if __name__ == "__main__":

    instrumentos = [
        ["YMCIC", "YMCID", None, None, None, None],
        ["YMCXC", "YMCXD", None, None, None, None],
        ["TLC1C", "TLC1D", None, None, None, None],
        ["TLCMC", "TLCMD", None, None, None, None],
        ["MTCGC", "MTCGD", None, None, None, None],
        ["ARC1C", "ARC1D", None, None, None, None],
        ["BOL1C", "BOL1D", None, None, None, None],
        ["DNC3C", "DNC3D", None, None, None, None],
        ["DNC5C", "DNC5D", None, None, None, None],
        ["RUCAC", "RUCAD", None, None, None, None],
        ["MSSEC", "MSSED", None, None, None, None],
        ["LOC3C", "LOC3D", None, None, None, None],
        ["VSCRC", "VSCRD", None, None, None, None],
        ["SNABC", "SNABD", None, None, None, None],
        ["RUCDC", "RUCDD", None, None, None, None],
        ["YFCJC", "YFCJD", None, None, None, None],
        ["YM34C", "YM34D", None, None, None, None],
        ["IRCPC", "IRCPD", None, None, None, None],
        ["YMCJC", "YMCJD", None, None, None, None],
        ["YMCQC", "YMCQD", None, None, None, None],
        ["IRCFC", "IRCFD", None, None, None, None],
        ["IRCJC", "IRCJD", None, None, None, None],
        ["PNDCC", "PNDCD", None, None, None, None],
        ["CS38C", "CS38D", None, None, None, None],
        ["CS44C", "CS44D", None, None, None, None],
        ["CAC5C", "CAC5D", None, None, None, None],
        ["LMS7C", "LMS7D", None, None, None, None],
        ["LMS8C", "LMS8D", None, None, None, None],
        ["CP34C", "CP34D", None, None, None, None],
        ["PNWCC", "PNWCD", None, None, None, None],
        ["GN43C", "GN43D", None, None, None, None],
        ["RAC6C", "RAC6D", None, None, None, None],
        ["PNXCC", "PNXCD", None, None, None, None],
        ["OTS2C", "OTS2D", None, None, None, None],
        ["TSC3C", "TSC3D", None, None, None, None],
        ["VSCPC", "VSCPD", None, None, None, None],
        ["YMCVC", "YMCVD", None, None, None, None],
        ["HJCBC", "HJCBD", None, None, None, None],
        ["IRCLC", "IRCLD", None, None, None, None],
        ["LMS9C", "LMS9D", None, None, None, None],
        ["YFCIC", "YFCID", None, None, None, None],
        ["RZ9BC", "RZ9BD", None, None, None, None],
        ["LIC6C", "LIC6D", None, None, None, None],
        ["PN35C", "PN35D", None, None, None, None],
        ["BYCHC", "BYCHD", None, None, None, None],
        ["MGCMC", "MGCMD", None, None, None, None],
        ["MGCNC", "MGCND", None, None, None, None],
        ["YMCYC", "YMCYD", None, None, None, None],
        ["YMCZC", "YMCZD", None, None, None, None],
        ["HJCFC", "HJCFD", None, None, None, None],
        ["HJCGC", "HJCGD", None, None, None, None],
        ["GN47C", "GN47D", None, None, None, None],
        ["DNC7C", "DNC7D", None, None, None, None],
        ["IRCNC", "IRCND", None, None, None, None],
        ["IRCOC", "IRCOD", None, None, None, None],
        ["PQCRC", "PQCRD", None, None, None, None],
        ["TTC9C", "TTC9D", None, None, None, None],
        ["GYC4C", "GYC4D", None, None, None, None],
        ["OTS3C", "OTS3D", None, None, None, None],
        ["XMC1C", "XMC1D", None, None, None, None],
        ["CIC7C", "CIC7D", None, None, None, None],
        ["PN36C", "PN36D", None, None, None, None],
        ["PN37C", "PN37D", None, None, None, None],
        ["CS47C", "CS47D", None, None, None, None],
        ["YFCKC", "YFCKD", None, None, None, None],
        ["YFCLC", "YFCLD", None, None, None, None],
        ["RZABC", "RZABD", None, None, None, None],
        ["OZC3C", "OZC3D", None, None, None, None],
        ["TLCOC", "TLCOD", None, None, None, None],
        ["VSCTC", "VSCTD", None, None, None, None],
        ["SIC1C", "SIC1D", None, None, None, None],
        ["MGCOC", "MGCOD", None, None, None, None],
        ["HJCHC", "HJCHD", None, None, None, None],
        ["OT41C", "OT41D", None, None, None, None],
        ["OT42C", "OT42D", None, None, None, None],
        ["TTCAC", "TTCAD", None, None, None, None],
        ["PLC1C", "PLC1D", None, None, None, None],
        ["PLC2C", "PLC2D", None, None, None, None],
        ["SNSDC", "SNSDD", None, None, None, None],
        ["LDCGC", "LDCGD", None, None, None, None],
        ["PN38C", "PN38D", None, None, None, None],
        ["PQCSC", "PQCSD", None, None, None, None],
        ["DEC2C", "DEC2D", None, None, None, None],
        ["ZZC1C", "ZZC1D", None, None, None, None],
        ["YM35C", "YM35D", None, None, None, None],
        ["PUC2C", "PUC2D", None, None, None, None],
        ["GYC5C", "GYC5D", None, None, None, None],
        ["GN48C", "GN48D", None, None, None, None],
        ["CP37C", "CP37D", None, None, None, None],
        ["MCC1C", "MCC1D", None, None, None, None],
        ["MCC2C", "MCC2D", None, None, None, None],
        ["VBC1C", "VBC1D", None, None, None, None],
        ["MSSGC", "MSSGD", None, None, None, None],
        ["PLC3C", "PLC3D", None, None, None, None],
        ["YM37C", "YM37D", None, None, None, None],
        ["RCCRC", "RCCRD", None, None, None, None],
        ["YFCMC", "YFCMD", None, None, None, None],
        ["HJCIC", "HJCID", None, None, None, None],
        ["TLCPC", "TLCPD", None, None, None, None],
        ["CIC9C", "CIC9D", None, None, None, None],
        ["PLC4C", "PLC4D", None, None, None, None],
        ["BF35C", "BF35D", None, None, None, None],
        ["ZPC2C", "ZPC2D", None, None, None, None],
        ["VSCVC", "VSCVD", None, None, None, None],
        ["OLC5C", "OLC5D", None, None, None, None],
        ["CACBC", "CACBD", None, None, None, None],
        ["TLCQC", "TLCQD", None, None, None, None],
        ["EMC1C", "EMC1D", None, None, None, None],
        ["CS48C", "CS48D", None, None, None, None],
        ["HVS1C", "HVS1D", None, None, None, None],
        ["BACGC", "BACGD", None, None, None, None],
        ["PFC2C", "PFC2D", None, None, None, None],
        ["YM38C", "YM38D", None, None, None, None],
        ["YM39C", "YM39D", None, None, None, None],
        ["LOC5C", "LOC5D", None, None, None, None],
        ["HJCJC", "HJCJD", None, None, None, None],
        ["MGCQC", "MGCQD", None, None, None, None],
        ["RC1CC", "RC1CD", None, None, None, None],
        ["DNC8C", "DNC8D", None, None, None, None],
        ["BYCVC", "BYCVD", None, None, None, None],
        ["BF37C", "BF37D", None, None, None, None],
        ["YM40C", "YM40D", None, None, None, None],
        ["NPCCC", "NPCCD", None, None, None, None],
        ["PN41C", "PN41D", None, None, None, None],
        ["CS49C", "CS49D", None, None, None, None],
        ["T652C", "T652D", None, None, None, None],
        ["AERBC", "AERBD", None, None, None, None],
        ["BPCPC", "BPCPD", None, None, None, None],
        ["NBS1C", "NBS1D", None, None, None, None],
        ["VSCOC", "VSCOD", None, None, None, None],
        ["VSCUC", "VSCUD", None, None, None, None],
        ["YMCMC", "YMCMD", None, None, None, None],
        ["ZPC3C", "ZPC3D", None, None, None, None],
        ["SBC1C", "SBC1D", None, None, None, None],
        ["RC2CC", "RC2CD", None, None, None, None],
        ["JNC6C", "JNC6D", None, None, None, None],
        ["YM41C", "YM41D", None, None, None, None],
        ["PN42C", "PN42D", None, None, None, None],
        ["VAC3C", "VAC3D", None, None, None, None],
        ["OTS5C", "OTS5D", None, None, None, None],
        ["VSCWC", "VSCWD", None, None, None, None],
        ["TTCDC", "TTCDD", None, None, None, None],
        ["MIC3C", "MIC3D", None, None, None, None],
        ["AFCIC", "AFCID", None, None, None, None],
        ["BGC4C", "BGC4D", None, None, None, None],
        ["PLC5C", "PLC5D", None, None, None, None],
        ["MGCRC", "MGCRD", None, None, None, None],
        ["TSC4C", "TSC4D", None, None, None, None],
        ["GN49C", "GN49D", None, None, None, None],
        ["CICAC", "CICAD", None, None, None, None],
        ["YM42C", "YM42D", None, None, None, None],
        ["BF39C", "BF39D", None, None, None, None],
        ["BPCUC", "BPCUD", None, None, None, None],
        ["CS50C", "CS50D", None, None, None, None],
        ["OLC6C", "OLC6D", None, None, None, None],
        ["YFCOC", "YFCOD", None, None, None, None],
        ["CACDC", "CACDD", None, None, None, None],
        ["AL30C", "AL30D", None, None, None, None],
        ["GD30C", "GD30D", None, None, None, None],
        ["AL35C", "AL35D", None, None, None, None],
        ["GD35C", "GD35D", None, None, None, None],
        ["BPY6C", "BPY6D", None, None, None, None],
        ["BPD7C", "BPD7D", None, None, None, None],
        ["SPYC", "SPYD", None, None, None, None],
        ["EWZC", "EWZD", None, None, None, None],
        ["IBITC", "IBITD", None, None, None, None],
        ["BRKBC", "BRKBD", None, None, None, None],
    ]
    websocket_url = "wss://matriz.cocos.xoms.com.ar/ws?session_id=gqKxOszDYQQ7rKXTo3ypHhA%2FnaS%2BvkIeZGVFew7mxGElbIUxZv1DT4dpZo%2Fm8eny&conn_id=Vj2HkM3nQqa9VqD5N2NzJF2sdbX5UZ7%2B1OpC6CxnoNi4c2TuzJ4Tdg7GX%2FWDF0%2Bp"

    dataframehandler = DataFrameHandler(instrumentos)
    websocketclient = WebSocketClient(websocket_url, dataframehandler)
    wst = websocketclient.connect()

    try:
        # Keep the main thread alive while the WebSocket listens
        while True:
            time.sleep(7)
            executer = Executer(dataframehandler.df.copy())
            executer.execute()
    except KeyboardInterrupt:
        print("Exiting...")
