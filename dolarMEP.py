import configparser
import os
import requests
import simplejson
import pandas as pd
import time
import numpy as np
from tabulate import tabulate

from dash import Dash, dash_table, html
from dash.dash_table import DataTable, FormatTemplate

mis_activos = [
    "YMCIO",
    "YMCXO",
    "GNCXO",
    "TLC1O",
    "TLCMO",
    "MTCGO",
    "ARC1O",
    "CRCJO",
    "RUCAO",
    "MRCAO",
    "MR35O",
    "MSSEO",
    "LECGO",
    "IRCIO",
    "LOC2O",
    "LOC3O",
    "VSCRO",
    "VSCTO",
    "PECGO",
    "SNABO",
    "YFCJO",
    "YM34O",
    "NPCBO",
    "IRCPO",
]

soberanos = [
    ["AL30", "AL30D"],
    ["GD30", "GD30D"],
    ["AL35", "AL35D"],
    ["GD35", "GD35D"],
    ["BPJ25", "BPJ5D"],
    ["BPY26", "BPY6D"],
    ["BPOA7", "BPA7D"],
    ["BPOB7", "BPB7D"],
    ["BPOC7", "BPC7D"],
    ["BPOD7", "BPD7D"],
]

ons = [
    ["YMCIO", "YMCID"],
    ["YMCXO", "YMCXD"],
    ["GNCXO", "GNCXD"],
    ["TLC1O", "TLC1D"],
    ["TLCMO", "TLCMD"],
    ["MTCGO", "MTCGD"],
    ["ARC1O", "ARC1D"],
    ["BOL1O", "BOL1D"],
    ["CRCJO", "CRCJD"],
    ["DNC3O", "DNC3D"],
    ["DNC5O", "DNC5D"],
    ["RUCAO", "RUCAD"],
    ["MSSEO", "MSSED"],
    ["IRCIO", "IRCID"],
    ["LOC2O", "LOC2D"],
    ["LOC3O", "LOC3D"],
    ["AEC1O", "AEC1D"],
    ["VSCRO", "VSCRD"],
    ["PECGO", "PECGD"],
    ["SNABO", "SNABD"],
    ["RUCDO", "RUCDD"],
    ["YFCJO", "YFCJD"],
    ["YM34O", "YM34D"],
    ["NPCBO", "NPCBD"],
    ["IRCPO", "IRCPD"],
    ["CLSIO", "CLSID"],
    ["YMCHO", "YMCHD"],
    ["YMCJO", "YMCJD"],
    ["YMCQO", "YMCQD"],
    ["GN40O", "GN40D"],
    ["TLC5O", "TLC5D"],
    ["IRCFO", "IRCFD"],
    ["IRCHO", "IRCHD"],
    ["IRCJO", "IRCJD"],
    ["MGCHO", "MGCHD"],
    ["MGCJO", "MGCJD"],
    ["PNDCO", "PNDCD"],
    ["MGC9O", "MGC9D"],
    ["RCCJO", "RCCJD"],
    ["CS38O", "CS38D"],
    ["CS44O", "CS44D"],
    ["CAC5O", "CAC5D"],
    ["CAC8O", "CAC8D"],
    ["NPCAO", "NPCAD"],
    ["VSCLO", "VSCLD"],
    ["SNS9O", "SNS9D"],
    ["LMS7O", "LMS7D"],
    ["LMS8O", "LMS8D"],
    ["CP34O", "CP34D"],
    ["PNWCO", "PNWCD"],
    ["GN43O", "GN43D"],
    ["RAC6O", "RAC6D"],
    ["TTC7O", "TTC7D"],
    ["PNXCO", "PNXCD"],
    ["PECAO", "PECAD"],
    ["PECBO", "PECBD"],
    ["OTS2O", "OTS2D"],
    ["VSCPO", "VSCPD"],
    ["YMCVO", "YMCVD"],
    ["HJCBO", "HJCBD"],
    ["IRCLO", "IRCLD"],
    ["LMS9O", "LMS9D"],
    ["YFCIO", "YFCID"],
    ["RZ9BO", "RZ9BD"],
    ["LIC6O", "LIC6D"],
    ["CRCLO", "CRCLD"],
    ["PN35O", "PN35D"],
    ["SNSBO", "SNSBD"],
    ["BYCHO", "BYCHD"],
    ["MGCMO", "MGCMD"],
    ["MGCNO", "MGCND"],
    ["YMCYO", "YMCYD"],
    ["YMCZO", "YMCZD"],
    ["HJCFO", "HJCFD"],
    ["HJCGO", "HJCGD"],
    ["GN47O", "GN47D"],
    ["DNC7O", "DNC7D"],
    ["IRCNO", "IRCND"],
    ["IRCOO", "IRCOD"],
    ["PQCRO", "PQCRD"],
    ["TTC8O", "TTC8D"],
    ["TTC9O", "TTC9D"],
    ["GYC4O", "GYC4D"],
    ["OTS3O", "OTS3D"],
    ["XMC1O", "XMC1D"],
    ["CIC7O", "CIC7D"],
    ["CIC8O", "CIC8D"],
    ["PN36O", "PN36D"],
    ["PN37O", "PN37D"],
    ["CS47O", "CS47D"],
    ["YFCKO", "YFCKD"],
    ["YFCLO", "YFCLD"],
    ["TN63O", "TN63D"],
    ["RZABO", "RZABD"],
    ["OZC3O", "OZC3D"],
    ["TLCOO", "TLCOD"],
    ["BPCIO", "BPCID"],
    ["BYCKO", "BYCKD"],
    ["VSCTO", "VSCTD"],
    ["SIC1O", "SIC1D"],
    ["MGCOO", "MGCOD"],
    ["MTC1O", "MTC1D"],
    ["EAC3O", "EAC3D"],
    ["HJCHO", "HJCHD"],
    ["OT41O", "OT41D"],
    ["OT42O", "OT42D"],
    ["TTCAO", "TTCAD"],
    ["PLC1O", "PLC1D"],
    ["PLC2O", "PLC2D"],
    ["PECIO", "PECID"],
    ["SNSDO", "SNSDD"],
    ["LDCGO", "LDCGD"],
    ["PN38O", "PN38D"],
    ["PQCSO", "PQCSD"],
    ["DEC2O", "DEC2D"],
    ["ZZC1O", "ZZC1D"],
    ["YM35O", "YM35D"],
    ["PUC2O", "PUC2D"],
    ["GYC5O", "GYC5D"],
    ["GN48O", "GN48D"],
    ["CP37O", "CP37D"],
    ["MCC1O", "MCC1D"],
    ["VBC1O", "VBC1D"],
    ["MSSGO", "MSSGD"],
    ["PLC3O", "PLC3D"],
    ["YM37O", "YM37D"],
    ["RCCRO", "RCCRD"],
    ["YFCMO", "YFCMD"],
    ["MR36O", "MR36D"],
    ["LECHO", "LECHD"],
    ["MRCAO", "MRCAD"],
    ["MR35O", "MR35D"],
    ["LECBO", "LECBD"],
    ["LECGO", "LECGD"],
    ["MRCLO", "MRCLD"],
    ["MRCQO", "MRCQD"],
    ["MRCOO", "MROCD"],
    ["LECAO", "LECAD"],
    ["LECEO", "LECED"],
    ["MRCUO", "MRCUD"],
    ["MRCYO", "MRCYD"],
    ["MR39O", "MR39D"],
]


cedears = [
    ["SPY", "SPYD"],
    ["IWM", "IWMD"],
    ["EEM", "EEMD"],
    ["AAPL", "AAPLD"],
    ["GOOGL", "GOGLD"],
    ["AMZN", "AMZND"],
    ["JNJ", "JNJD"],
    ["TSLA", "TSLAD"],
    ["DISN", "DISND"],
    ["PYPL", "PYPLD"],
    ["PFE", "PFED"],
    ["NVDA", "NVDAD"],
    ["BRKB", "BRKBD"],
    ["KO", "KOD"],
    ["BABA", "BABAD"],
    ["WMT", "WMTD"],
    ["BA.C", "BA.CD"],
]

provinciales = [
    ["BA37D", "BA7DD"],
    ["NDT25", "NDT5D"],
    ["CO26", "CO26D"],
    ["PMM29", "PM29D"],
    ["SA24D", "S24DD"],
]

to_get_data = [
    [
        "https://clientes.balanz.com/api/v1/cotizaciones/panel/27?token=0&tokenindice=0",
        ons,
    ],
    # [
    #     "https://clientes.balanz.com/api/v1/cotizaciones/cedears?token=0&tokenindice=0",
    #     cedears,
    # ],
    [
        "https://clientes.balanz.com/api/v1/cotizaciones/panel/23?token=0&tokenindice=0",
        soberanos,
    ],
    [
        "https://clientes.balanz.com/api/v1/cotizaciones/panel/24?token=0&tokenindice=0",
        provinciales,
    ],
]


def get_token():
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    CONFIG_FILE_PATH = os.path.join(PROJECT_ROOT, "config.ini")
    if not os.path.exists(CONFIG_FILE_PATH):
        raise FileNotFoundError(
            f"Credentials file not found at {CONFIG_FILE_PATH}. Please create it."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE_PATH)
    usuario = config['credentials']['balanz_username']
    password = config['credentials']['balanz_password']

    r = requests.post(
        "https://clientes.balanz.com/api/v1/auth/init?avoidAuthRedirect=true",
        headers={"Accept": "application/json"},
        json={"user": usuario, "source": "WebV2"},
    )
    data = simplejson.loads(r.text)
    nonce = data["nonce"]

    r = requests.post(
        "https://clientes.balanz.com/api/v1/auth/login?avoidAuthRedirect=true",
        headers={"Accept": "application/json"},
        json={
            "user": usuario,
            "pass": password,
            "nonce": nonce,
            "source": "WebV2",
            "idDispositivo": "399b693e-b626-4daa-819d-7986ccad8c96",
            "TipoDispositivo": "Web",
            "sc": 1,
            "Nombre": "Linux x86_64 Chrome 123.0.0.0",
            "SistemaOperativo": "Linux",
            "VersionSO": "x86_64",
            "VersionAPP": "2.15.1",
        },
    )
    data = simplejson.loads(r.text)
    return data["AccessToken"]


def get_data(token):
    for_df = []
    for index in to_get_data:
        r = requests.get(
            index[0],
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": token,
            },
        )
        data = simplejson.loads(r.text)
        data = data["cotizaciones"]

        for instrumento in index[1]:
            prCompraPesosCI = prVentaPesosCI = prCompraDolarCI = prVentaDolarCI = (
                prCompraPesos
            ) = prVentaPesos = prCompraDolar = prVentaDolar = None

            # CI
            result = list(
                filter(
                    lambda x: (x["ticker"] == instrumento[0] and x["plazo"] == "CI"),
                    data,
                )
            )
            if result != []:
                prCompraPesosCI = result[0]["pc"] * 100
                prVentaPesosCI = result[0]["pv"] * 100
            result = list(
                filter(
                    lambda x: (x["ticker"] == instrumento[1] and x["plazo"] == "CI"),
                    data,
                )
            )
            if result != []:
                prCompraDolarCI = result[0]["pc"] * 100
                prVentaDolarCI = result[0]["pv"] * 100

            # 24hs
            result = list(
                filter(
                    lambda x: (x["ticker"] == instrumento[0] and x["plazo"] == "24hs"),
                    data,
                )
            )
            if result != []:
                prCompraPesos = result[0]["pc"] * 100
                prVentaPesos = result[0]["pv"] * 100
            result = list(
                filter(
                    lambda x: (x["ticker"] == instrumento[1] and x["plazo"] == "24hs"),
                    data,
                )
            )
            if result != []:
                prCompraDolar = result[0]["pc"] * 100
                prVentaDolar = result[0]["pv"] * 100
            for_df.append(
                [
                    instrumento[0],
                    prCompraPesosCI,
                    prVentaPesosCI,
                    prCompraPesos,
                    prVentaPesos,
                    prCompraDolarCI,
                    prVentaDolarCI,
                    prCompraDolar,
                    prVentaDolar,
                ]
            )
    return for_df


def create_df(data):
    df = pd.DataFrame(
        data=data,
        columns=[
            "ticker",
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
    df["USD_a_pesos"] = df.prCompraPesos / df.prVentaDolar
    df["USDCI_a_pesos"] = df.prCompraPesos / df.prVentaDolarCI
    df["pesos_a_USD"] = df.prVentaPesos / df.prCompraDolar
    df["pesos_a_USDCI"] = df.prVentaPesos / df.prCompraDolarCI

    df["USDCI_a_pesosCI"] = df.prCompraPesosCI / df.prVentaDolarCI
    df["USD_a_pesosCI"] = df.prCompraPesosCI / df.prVentaDolar
    df["pesosCI_a_USD"] = df.prVentaPesosCI / df.prCompraDolar
    df["pesosCI_a_USDCI"] = df.prVentaPesosCI / df.prCompraDolarCI
    # print(tabulate(df, headers="keys", tablefmt="mixed_outline"))  # type: ignore
    return df


def run():
    token = open("token.csv", "r").read()
    r = requests.get(
        "https://clientes.balanz.com/api/v1/notificaciones?avoidAuthRedirect=true",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": token,
        },
    )
    if r.status_code != 200:
        token = get_token()
        open("token.csv", "w").write(token)

    data = get_data(token)

    df = create_df(data)

    print(
        "\n##############################################################################################\n"
    )

    ##############################################################################################

    ratio = 1.002
    USD_a_pesos_MAX = df.USD_a_pesos.max()
    USDCI_a_pesos_MAX = df.USDCI_a_pesos.max()
    pesos_a_USD_Min = df[df.pesos_a_USD > 1].pesos_a_USD.min()

    # Verifico que el maximo entre "USD a pesos" o "USDCI a pesos" sea mayor a "pesos a USD" (multiplicado por el ratio)
    if pesos_a_USD_Min * ratio < max(USD_a_pesos_MAX, USDCI_a_pesos_MAX):
        # Si la condición es verdadera, verifico cual de los dos es mayor e imprimo la tabla
        if USD_a_pesos_MAX >= USDCI_a_pesos_MAX:
            df_USD_a_p = df.sort_values(by=["USD_a_pesos"], ascending=False).iloc[0:2]
            print("USD 24hs")
            print(
                tabulate(
                    df_USD_a_p[
                        ["ticker", "prCompraPesos", "prVentaDolar", "USD_a_pesos"]
                    ],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                    floatfmt=".2f",
                ),
            )
        else:
            df_USDCI_a_p = df.sort_values(by=["USDCI_a_pesos"], ascending=False).iloc[
                0:2
            ]
            print("USD CI")
            print(
                tabulate(
                    df_USDCI_a_p[
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
        df_p_a_USD = (
            df[df.pesos_a_USD > 1]
            .sort_values(by=["pesos_a_USD"], ascending=True)
            .iloc[0:2]
        )
        print(
            tabulate(
                df_p_a_USD[["ticker", "prVentaPesos", "prCompraDolar", "pesos_a_USD"]],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
                floatfmt=".2f",
            )
        )
    else:
        print("NO HAY ARBITRAJE PRINCIPAL")

    # Muestro la tabla de "pesos a USDCI" solo en caso de que sea menor a "pesos a USD", ya que no es un arbitraje tan recurrente
    df_p_a_USDCI = (
        df[(df.pesos_a_USDCI > 1) & (df.pesos_a_USDCI < pesos_a_USD_Min)]
        .loc[df["ticker"].isin(mis_activos), :]
        .sort_values(by=["pesos_a_USDCI"], ascending=True)
    )
    if not df_p_a_USDCI.empty:
        print("\nPesos a USDCI (Mis activos)")
        print(
            tabulate(
                df_p_a_USDCI[
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

    USDCI_a_pesosCI_MAX = df.USDCI_a_pesosCI.max()
    USD_a_pesosCI_MAX = df.loc[df["ticker"].isin(mis_activos), :].USD_a_pesosCI.max()
    pesosCI_a_USDCI_Min = df[df.pesosCI_a_USDCI > 1].pesosCI_a_USDCI.min()
    pesosCI_a_USD_Min = df[df.pesosCI_a_USD > 1].pesosCI_a_USD.min()

    # Verifico que el maximo entre "USDCI a pesosCI" o "USD a pesosCI" sea mayor que el minimo entre "pesosCI a USDCI" y "pesosCI a USD" (multiplicado por el ratio)
    if min(pesosCI_a_USDCI_Min, pesosCI_a_USD_Min) * ratio < max(
        USDCI_a_pesosCI_MAX, USD_a_pesosCI_MAX
    ):
        # Si la condición es verdadera, verifico cual de los dos USD a pesos es mayor, e imprimo la tabla
        if USDCI_a_pesosCI_MAX >= USD_a_pesosCI_MAX:
            df_USDCI_a_pCI = df.sort_values(
                by=["USDCI_a_pesosCI"], ascending=False
            ).iloc[0:2]
            print("\nUSD CI a Pesos CI")
            print(
                tabulate(
                    df_USDCI_a_pCI[
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
            df_USD_a_pCI = (
                df[df.USD_a_pesosCI > USDCI_a_pesosCI_MAX]
                .loc[df["ticker"].isin(mis_activos), :]
                .sort_values(by=["USD_a_pesosCI"], ascending=False)
            )
            print("\nUSD 24hs a Pesos CI (Mis activos)")
            print(
                tabulate(
                    df_USD_a_pCI[
                        ["ticker", "prCompraPesosCI", "prVentaDolar", "USD_a_pesosCI"]
                    ],  # type: ignore
                    headers="keys",
                    tablefmt="mixed_outline",
                    floatfmt=".2f",
                ),
            )

        # Si la condición es verdadera, verifico cual de los dos pesos a USD es menor e imprimo la tabla
        if pesosCI_a_USDCI_Min <= pesosCI_a_USD_Min:
            df_pCI_a_USDCI = (
                df[df.pesosCI_a_USDCI > 1]
                .sort_values(by=["pesosCI_a_USDCI"], ascending=True)
                .iloc[0:2]
            )
            print("\nPesos CI a USD CI")
            print(
                tabulate(
                    df_pCI_a_USDCI[
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
            df_pCI_a_USD = df[df.pesosCI_a_USD > 1].sort_values(
                by=["pesosCI_a_USD"], ascending=True
            )
            print("\nPesos CI a USD 24hs")
            print(
                tabulate(
                    df_pCI_a_USD[
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

    df_dolares = df.copy()[
        (df.prVentaDolarCI < df.prCompraDolar) & (df.prVentaDolarCI > 1)
    ]
    df_dolares["%"] = ((df_dolares.prCompraDolar / df_dolares.prVentaDolarCI) - 1) * 100
    if not df_dolares.empty:
        print(
            tabulate(
                df_dolares[["ticker", "prVentaDolarCI", "prCompraDolar", "%"]],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
            )
        )
    else:
        print("No hay arbitraje DolarCI por Dolar.")

    df_pesos = df.copy()[
        (df.prVentaPesosCI < df.prCompraPesos) & (df.prVentaPesosCI > 1)
    ]
    df_pesos["%"] = ((df_pesos.prCompraPesos / df_pesos.prVentaPesosCI) - 1) * 36500
    if not df_pesos.empty:
        print(
            tabulate(
                df_pesos[["ticker", "prVentaPesosCI", "prCompraPesos", "%"]]
                .sort_values(by=["%"], ascending=False)
                .iloc[0:2],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
                floatfmt=".5f",
            )
        )
    else:
        print("No hay arbitraje PesosCI por Pesos.")

    print(
        "---------------------------------------------------------------------------------------------"
    )

    df_dolares = df.copy()[
        (df.prVentaDolar < df.prCompraDolarCI) & (df.prVentaDolar > 1)
    ].loc[df["ticker"].isin(mis_activos), :]
    df_dolares["%"] = ((df_dolares.prCompraDolarCI / df_dolares.prVentaDolar) - 1) * 100
    if not df_dolares.empty:
        print(
            tabulate(
                df_dolares[["ticker", "prVentaDolar", "prCompraDolarCI", "%"]],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
            )
        )
    else:
        print("No hay arbitraje Dolar a DolarCI.")

    df_pesos = df.copy()[
        (df.prVentaPesos < df.prCompraPesosCI) & (df.prVentaPesos > 1)
    ].loc[df["ticker"].isin(mis_activos), :]
    df_pesos["%"] = ((df_pesos.prCompraPesosCI / df_pesos.prVentaPesos) - 1) * 100
    if not df_pesos.empty:
        print(
            tabulate(
                df_pesos[["ticker", "prVentaPesos", "prCompraPesosCI", "%"]],  # type: ignore
                headers="keys",
                tablefmt="mixed_outline",
            )
        )
    else:
        print("No hay arbitraje Pesos a PesosCI.")

    print(
        "\n##############################################################################################\n"
    )


while True:
    run()
    time.sleep(15)

##############################################################################################

# app = Dash(__name__)

# app.layout = html.Div([
#     dash_table.DataTable(
#         id='datatable-interactivity',
#         columns=[
#             {"name": "ticker", "id": "ticker", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)},
#             {"name": "prCompraPesos", "id": "prCompraPesos", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)},
#             {"name": "prVentaPesos", "id": "prVentaPesos", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)},
#             {"name": "prCompraDolar", "id": "prCompraDolar", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)},
#             {"name": "prVentaDolar", "id": "prVentaDolar", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)},
#             {"name": "USD_a_pesos", "id": "USD_a_pesos", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)},
#             {"name": "pesos_a_USD", "id": "pesos_a_USD", "deletable": False, "selectable": True, "type": "numeric",
#              "format": FormatTemplate.money(2)}
#         ],
#         data=df.to_dict('records'),
#         editable=True,
#         sort_action="native",
#         sort_mode="multi",
#         row_deletable=True,
#         selected_columns=[],
#         selected_rows=[],
#         page_action="native",
#         page_current=0,
#         page_size=100,
#         style_header={
#             'backgroundColor': 'rgb(30, 30, 30)',
#             'color': 'white'
#         },
#         style_data={
#             'backgroundColor': 'rgb(50, 50, 50)',
#             'color': 'white'
#         },
#         style_data_conditional=[
#             {
#                 'if': {
#                     'column_id': 'USD_a_pesos',
#                     'filter_query': '{{USD_a_pesos}} > {}'.format(df.USD_a_pesos.nlargest(5).iloc[-1])
#                 },
#                 'backgroundColor': '#006400',
#                 'color': 'white'
#             },
#             {
#                 'if': {
#                     'column_id': 'pesos_a_USD',
#                     'filter_query': '{{pesos_a_USD}} < {}'.format(df.pesos_a_USD.nsmallest(5).iloc[-1])
#                 },
#                 'backgroundColor': '#B90E0A',
#                 'color': 'white'
#             }
#         ]
#     ),
#     html.Div(id='datatable-interactivity-container')
# ])

# if __name__ == "__main__":
#     app.run()
