"""Carga de instrumentos (data/instrumentos.csv) y de mis activos (data/mis_activos.csv)."""

from __future__ import annotations

import csv
from typing import Dict, List

DEFAULT_MIN_QUANT = 1
DEFAULT_MAX_QUANT = 500


def crear_instrumento(
    ticker: str,
    tickerD: str,
    min_quant: int = DEFAULT_MIN_QUANT,
    max_quant: int = DEFAULT_MAX_QUANT,
) -> Dict:
    """Instrumento = par pesos/dólares con sus límites de cantidad. Las cotizaciones viven en el libro."""
    return {"ticker": ticker, "tickerD": tickerD, "min_quant": min_quant, "max_quant": max_quant}


def _filas(path: str) -> List[List[str]]:
    """Filas del CSV sin líneas vacías ni comentarios (#), con los campos sin espacios."""
    with open(path, newline="", encoding="utf-8") as f:
        return [
            [campo.strip() for campo in row]
            for row in csv.reader(f)
            if row and row[0].strip() and not row[0].strip().startswith("#")
        ]


def cargar_instrumentos(path: str, solo_habilitados: bool = True) -> List[Dict]:
    """
    Lee un CSV sin encabezado con el formato:
        ticker,tickerD[,habilitado(1/0),min_quant,max_quant]

    Con solo_habilitados=True (compradores) se descartan las filas cuyo flag no es 1.
    Con solo_habilitados=False (analizadores) se cargan todas y solo importan las dos primeras columnas.
    """
    instrumentos = []
    for row in _filas(path):
        if len(row) < 2:
            continue
        if solo_habilitados and (len(row) < 3 or row[2] != "1"):
            continue
        min_q = int(row[3]) if len(row) > 3 and row[3] else DEFAULT_MIN_QUANT
        max_q = int(row[4]) if len(row) > 4 and row[4] else DEFAULT_MAX_QUANT
        instrumentos.append(crear_instrumento(row[0], row[1], min_q, max_q))
    return instrumentos


def cargar_mis_activos(path: str) -> List[str]:
    """Lee los tickers de la primera columna del CSV."""
    return [row[0] for row in _filas(path)]
