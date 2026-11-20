"""
Analizador de arbitraje dólar MEP en 24hs y CI. Solo imprime tablas: no envía órdenes.

Reemplaza a analizador_v1.py (--feed matriz, por defecto) y analizador_v2.py (--feed primary).
Los parámetros son los de v1.

    python analizadores/analizador.py [--feed matriz|primary]
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from typing import Dict, List, Tuple

import pandas as pd

from comun.cliente_rest import CocosMatrizClient
from comun.cotizaciones import PLAZO_24HS, PLAZO_CI, LibroCotizaciones
from comun.feed_matriz import FeedMatriz
from comun.feed_primary import FeedPrimary
from comun.instrumentos import cargar_instrumentos, cargar_mis_activos
from comun.rutas import configurar_logging, leer_credenciales, ruta_data, url_websocket_matriz
from comun.tablas import imprimir_tabla, libro_a_dataframe

RATIO = 1.0008
RATIO_CI = 1.0012
INTERVALO = 1.5  # segundos entre análisis

SEPARADOR = "\n##############################################################################################\n"
LINEA = "---------------------------------------------------------------------------------------------"

# columna → (0 = ticker en pesos / 1 = ticker en dólares, plazo, campo)
COLUMNAS = {
    "prCompraPesosCI": (0, PLAZO_CI, "pr_compra"),
    "prVentaPesosCI": (0, PLAZO_CI, "pr_venta"),
    "prCompraPesos": (0, PLAZO_24HS, "pr_compra"),
    "prVentaPesos": (0, PLAZO_24HS, "pr_venta"),
    "prCompraDolarCI": (1, PLAZO_CI, "pr_compra"),
    "prVentaDolarCI": (1, PLAZO_CI, "pr_venta"),
    "prCompraDolar": (1, PLAZO_24HS, "pr_compra"),
    "prVentaDolar": (1, PLAZO_24HS, "pr_venta"),
}

logger = logging.getLogger(__name__)


class AnalizadorArbitraje:
    def __init__(self, df: pd.DataFrame, mis_activos: List[str]):
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

    def _mis_activos(self) -> pd.Series:
        return self.df["ticker"].isin(self.mis_activos)

    def detect_main_arbitrage(self) -> None:
        print(SEPARADOR)
        window = 3

        USD_a_pesos_MAX = self.df.USD_a_pesos.max()
        USDCI_a_pesos_MAX = (
            self.df.USDCI_a_pesos.max() if not math.isnan(self.df.USDCI_a_pesos.max()) else 0
        )
        pesos_a_USD_Min = self.df[self.df.pesos_a_USD > 1].pesos_a_USD.min()

        # Verifico que el máximo entre "USD a pesos" o "USDCI a pesos" sea mayor a "pesos a USD" (multiplicado por el ratio)
        if pesos_a_USD_Min * RATIO < max(USD_a_pesos_MAX, USDCI_a_pesos_MAX / RATIO):
            # Si la condición es verdadera, verifico cuál de los dos es mayor e imprimo la tabla
            if USD_a_pesos_MAX * RATIO >= USDCI_a_pesos_MAX:
                df_USD_a_p = self.df.sort_values(by=["USD_a_pesos"], ascending=False).iloc[0:window]
                imprimir_tabla(
                    df_USD_a_p[["ticker", "prCompraPesos", "prVentaDolar", "USD_a_pesos"]],
                    "USD 24hs",
                )
            else:
                df_USDCI_a_p = self.df.sort_values(by=["USDCI_a_pesos"], ascending=False).iloc[0:window]
                imprimir_tabla(
                    df_USDCI_a_p[["ticker", "prCompraPesos", "prVentaDolarCI", "USDCI_a_pesos"]],
                    "USD CI",
                )

            # Imprimo la tabla de "pesos a USD" ya que será utilizada en cualquiera de los dos casos
            df_p_a_USD = (
                self.df[self.df.pesos_a_USD > 1]
                .sort_values(by=["pesos_a_USD"], ascending=True)
                .iloc[0:window]
            )
            imprimir_tabla(df_p_a_USD[["ticker", "prVentaPesos", "prCompraDolar", "pesos_a_USD"]])
        else:
            print("NO HAY ARBITRAJE PRINCIPAL")

        # Muestro la tabla de "pesos a USDCI" solo si es menor a "pesos a USD" (arbitraje poco recurrente)
        df_p_a_USDCI = (
            self.df[(self.df.pesos_a_USDCI > 1) & (self.df.pesos_a_USDCI < pesos_a_USD_Min)]
            .loc[self._mis_activos(), :]
            .sort_values(by=["pesos_a_USDCI"], ascending=True)
        )
        if not df_p_a_USDCI.empty:
            imprimir_tabla(
                df_p_a_USDCI[["ticker", "prVentaPesos", "prCompraDolarCI", "pesos_a_USDCI"]].iloc[0:window],
                "\nPesos a USDCI (Mis activos)",
            )
        else:
            print("No hay arbitraje Pesos a DolarCI.")

    def detect_ci_arbitrage(self) -> None:
        print("-----------------------------------------CI--------------------------------------------------")

        USDCI_a_pesosCI_MAX = self.df.USDCI_a_pesosCI.max()
        USD_a_pesosCI_MAX = self.df.loc[self._mis_activos(), :].USD_a_pesosCI.max()
        pesosCI_a_USDCI_Min = self.df[self.df.pesosCI_a_USDCI > 1].pesosCI_a_USDCI.min()
        pesosCI_a_USD_Min = self.df[self.df.pesosCI_a_USD > 1].pesosCI_a_USD.min()

        # Verifico que el máximo entre "USDCI a pesosCI" o "USD a pesosCI" sea mayor que el mínimo entre
        # "pesosCI a USDCI" y "pesosCI a USD" (multiplicado por el ratio)
        if min(pesosCI_a_USDCI_Min, pesosCI_a_USD_Min) * RATIO_CI < max(USDCI_a_pesosCI_MAX, USD_a_pesosCI_MAX):
            # Verifico cuál de los dos USD a pesos es mayor e imprimo la tabla
            if USDCI_a_pesosCI_MAX >= USD_a_pesosCI_MAX:
                df_USDCI_a_pCI = self.df.sort_values(by=["USDCI_a_pesosCI"], ascending=False).iloc[0:2]
                imprimir_tabla(
                    df_USDCI_a_pCI[["ticker", "prCompraPesosCI", "prVentaDolarCI", "USDCI_a_pesosCI"]],
                    "\nUSD CI a Pesos CI",
                )
            else:
                df_USD_a_pCI = (
                    self.df[self.df.USD_a_pesosCI > USDCI_a_pesosCI_MAX]
                    .loc[self._mis_activos(), :]
                    .sort_values(by=["USD_a_pesosCI"], ascending=False)
                )
                imprimir_tabla(
                    df_USD_a_pCI[["ticker", "prCompraPesosCI", "prVentaDolar", "USD_a_pesosCI"]],
                    "\nUSD 24hs a Pesos CI (Mis activos)",
                )

            # Verifico cuál de los dos pesos a USD es menor e imprimo la tabla
            if pesosCI_a_USDCI_Min <= pesosCI_a_USD_Min:
                df_pCI_a_USDCI = (
                    self.df[self.df.pesosCI_a_USDCI > 1]
                    .sort_values(by=["pesosCI_a_USDCI"], ascending=True)
                    .iloc[0:2]
                )
                imprimir_tabla(
                    df_pCI_a_USDCI[["ticker", "prVentaPesosCI", "prCompraDolarCI", "pesosCI_a_USDCI"]],
                    "\nPesos CI a USD CI",
                )
            else:
                df_pCI_a_USD = self.df[self.df.pesosCI_a_USD > 1].sort_values(
                    by=["pesosCI_a_USD"], ascending=True
                )
                imprimir_tabla(
                    df_pCI_a_USD[["ticker", "prVentaPesosCI", "prCompraDolar", "pesosCI_a_USD"]].iloc[0:3],
                    "\nPesos CI a USD 24hs",
                )
        else:
            print("NO HAY ARBITRAJE EN CI")

    def detect_ci_to_24hs(self) -> None:
        print(LINEA)

        df_dolares = self.df.copy()[
            (self.df.prVentaDolarCI < self.df.prCompraDolar) & (self.df.prVentaDolarCI > 1)
        ]
        df_dolares["%"] = ((df_dolares.prCompraDolar / df_dolares.prVentaDolarCI) - 1) * 100
        if not df_dolares[df_dolares["%"] > 0.10].empty:
            imprimir_tabla(df_dolares[["ticker", "prVentaDolarCI", "prCompraDolar", "%"]], floatfmt="g")
        else:
            print("No hay arbitraje DolarCI por Dolar.")

        df_pesos = self.df.copy()[
            (self.df.prVentaPesosCI < self.df.prCompraPesos) & (self.df.prVentaPesosCI > 1)
        ]
        df_pesos["%"] = ((df_pesos.prCompraPesos / df_pesos.prVentaPesosCI) - 1) * 36500
        if not df_pesos.empty:
            imprimir_tabla(
                df_pesos[["ticker", "prVentaPesosCI", "prCompraPesos", "%"]]
                .sort_values(by=["%"], ascending=False)
                .iloc[0:2]
            )
        else:
            print("No hay arbitraje PesosCI por Pesos.")

    def detect_24hs_to_ci(self) -> None:
        print(LINEA)
        df_dolares = self.df.copy()[
            (self.df.prVentaDolar < self.df.prCompraDolarCI) & (self.df.prVentaDolar > 1)
        ].loc[self._mis_activos(), :]
        df_dolares["%"] = ((df_dolares.prCompraDolarCI / df_dolares.prVentaDolar) - 1) * 100
        if not df_dolares[df_dolares["%"] > 0.20].empty:
            imprimir_tabla(df_dolares[["ticker", "prVentaDolar", "prCompraDolarCI", "%"]], floatfmt="g")
        else:
            print("No hay arbitraje Dolar a DolarCI.")

        df_pesos = self.df.copy()[
            (self.df.prVentaPesos <= self.df.prCompraPesosCI) & (self.df.prVentaPesos > 1)
        ].loc[self._mis_activos(), :]
        df_pesos["%"] = ((df_pesos.prCompraPesosCI / df_pesos.prVentaPesos) - 1) * 100
        if not df_pesos.empty:
            imprimir_tabla(df_pesos[["ticker", "prVentaPesos", "prCompraPesosCI", "%"]], floatfmt="g")
        else:
            print("No hay arbitraje Pesos a PesosCI.")

        print(SEPARADOR)

    def execute(self) -> None:
        self.calculate_ratios()
        self.detect_main_arbitrage()
        self.detect_ci_arbitrage()
        self.detect_ci_to_24hs()
        self.detect_24hs_to_ci()


def pares(instrumentos: List[Dict]) -> List[Tuple[str, str]]:
    return [(inst["ticker"], inst["tickerD"]) for inst in instrumentos]


def suscripciones(instrumentos: List[Dict]) -> List[Tuple[str, str]]:
    return (
        [(i["ticker"], PLAZO_24HS) for i in instrumentos]
        + [(i["tickerD"], PLAZO_24HS) for i in instrumentos]
        + [(i["ticker"], PLAZO_CI) for i in instrumentos]
        + [(i["tickerD"], PLAZO_CI) for i in instrumentos]
    )


def armar_dataframe(instrumentos: List[Dict], libro: LibroCotizaciones) -> pd.DataFrame:
    return libro_a_dataframe(pares(instrumentos), libro.snapshot(), ("ticker", "tickerD"), COLUMNAS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analizador de arbitraje (solo lectura).")
    parser.add_argument(
        "--feed",
        choices=("matriz", "primary"),
        default="matriz",
        help="matriz: WebSocket de Matriz (session_id/conn_id). primary: WebSocket de Primary (login con usuario).",
    )
    args = parser.parse_args()

    configurar_logging("analizador.log")
    # Los analizadores cargan todos los instrumentos del CSV, habilitados o no
    instrumentos = cargar_instrumentos(ruta_data("instrumentos.csv"), solo_habilitados=False)
    mis_activos = cargar_mis_activos(ruta_data("mis_activos.csv"))
    credenciales = leer_credenciales()
    libro = LibroCotizaciones()

    if args.feed == "matriz":
        feed = FeedMatriz(url_websocket_matriz(credenciales), suscripciones(instrumentos), libro)
    else:
        client = CocosMatrizClient(credenciales["matriz_username"], credenciales["matriz_password"])
        if not client.token:
            raise SystemExit("❌ No se pudo hacer login: no se puede abrir el feed de Primary.")
        feed = FeedPrimary(client.token, suscripciones(instrumentos), libro)
    feed.conectar()

    try:
        while True:
            time.sleep(INTERVALO)
            AnalizadorArbitraje(armar_dataframe(instrumentos, libro), mis_activos).execute()
    except KeyboardInterrupt:
        print("Saliendo...")
        feed.detener()


if __name__ == "__main__":
    main()
