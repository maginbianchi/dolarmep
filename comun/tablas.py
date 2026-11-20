"""DataFrames armados desde el libro de cotizaciones e impresión de tablas para los analizadores."""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from tabulate import tabulate

from comun.cotizaciones import Clave, Cotizacion

FORMATO_TABLA = "mixed_outline"

# columna → (posición del ticker en el par, plazo, campo de Cotizacion)
DefinicionColumnas = Mapping[str, Tuple[int, str, str]]


def libro_a_dataframe(
    pares: Sequence[Tuple[str, str]],
    snapshot: Mapping[Clave, Cotizacion],
    nombres_tickers: Tuple[str, str],
    columnas: DefinicionColumnas,
) -> pd.DataFrame:
    """
    Una fila por par de tickers, con las columnas de precios pedidas (float; sin dato = NaN).

    Ej.: columnas={"prCompraPesos": (0, "24hs", "pr_compra")} toma el bid 24hs del primer ticker del par.
    """
    filas: List[Dict] = []
    for par in pares:
        fila: Dict = {nombres_tickers[0]: par[0], nombres_tickers[1]: par[1]}
        for columna, (posicion, plazo, campo) in columnas.items():
            cotizacion = snapshot.get((par[posicion], plazo))
            valor = getattr(cotizacion, campo) if cotizacion is not None else None
            fila[columna] = np.nan if valor is None else float(valor)
        filas.append(fila)
    return pd.DataFrame(filas, columns=[*nombres_tickers, *columnas])


def imprimir_tabla(df: pd.DataFrame, titulo: Optional[str] = None, floatfmt: str = ".2f") -> None:
    """Imprime el DataFrame con el formato de tabla de los analizadores."""
    if titulo is not None:
        print(titulo)
    print(tabulate(df, headers="keys", tablefmt=FORMATO_TABLA, floatfmt=floatfmt))  # type: ignore[arg-type]
