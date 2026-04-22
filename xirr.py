import configparser
from datetime import date, timedelta
from pyxirr import xirr
import requests
import simplejson
import pandas as pd
from tabulate import tabulate
from dash import dcc
import plotly.express as px
from dash import Dash, dash_table, html, Input, Output, callback
from dash.dash_table import FormatTemplate


ymcio = {
    "tickerPesos": "YMCIO",
    "tickerDolar": "YMCID",
    "dates": [
        date(2024, 6, 30),
        date(2024, 12, 30),
        date(2025, 6, 30),
        date(2025, 12, 30),
        date(2026, 6, 30),
        date(2026, 12, 30),
        date(2027, 6, 30),
        date(2027, 12, 30),
        date(2028, 6, 30),
        date(2028, 12, 30),
        date(2029, 6, 30),
    ],
    "amounts": [
        4.50,
        4.50,
        4.50,
        4.50,
        18.78,
        18.14,
        17.49,
        16.85,
        16.21,
        15.57,
        14.92,
    ],
}

ymcjo = {
    "tickerPesos": "YMCJO",
    "tickerDolar": "YMCJD",
    "dates": [
        date(2024, 9, 30),
        date(2025, 3, 30),
        date(2025, 9, 30),
        date(2026, 3, 30),
        date(2026, 9, 30),
        date(2027, 3, 30),
        date(2027, 9, 30),
        date(2028, 3, 30),
        date(2028, 9, 30),
        date(2029, 3, 30),
        date(2029, 9, 30),
        date(2030, 3, 30),
        date(2030, 9, 30),
        date(2031, 3, 30),
        date(2031, 9, 30),
        date(2032, 3, 30),
        date(2032, 9, 30),
        date(2033, 3, 30),
        date(2033, 9, 30),
    ],
    "amounts": [
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        3.50,
        28.50,
        2.63,
        27.63,
        1.75,
        26.75,
        0.88,
        25.88,
    ],
}


tlc1o = {
    "tickerPesos": "TLC1O",
    "tickerDolar": "TLC1D",
    "dates": [
        date(2024, 7, 18),
        date(2025, 1, 18),
        date(2025, 7, 18),
        date(2026, 1, 18),
        date(2026, 7, 18),
    ],
    "amounts": [4, 4, 4, 4, 104],
}

mtcgo = {
    "tickerPesos": "MTCGO",
    "tickerDolar": "MTCGD",
    "dates": [
        date(2024, 6, 30),
        date(2024, 9, 30),
        date(2024, 12, 30),
        date(2025, 3, 30),
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 30),
        date(2026, 3, 30),
        date(2026, 6, 30),
    ],
    "amounts": [
        2.74,
        2.74,
        2.74,
        2.74,
        2.74,
        2.74,
        2.74,
        2.74,
        102.74,
    ],
}

gncxo = {
    "tickerPesos": "GNCXO",
    "tickerDolar": "GNCXD",
    "dates": [
        date(2024, 9, 2),
        date(2025, 3, 2),
        date(2025, 9, 2),
        date(2026, 3, 2),
        date(2026, 9, 2),
        date(2027, 3, 2),
        date(2027, 9, 2),
    ],
    "amounts": [
        13.06,
        12.63,
        12.19,
        11.75,
        11.31,
        10.88,
        10.44,
    ],
}


pndco = {
    "tickerPesos": "PNDCO",
    "tickerDolar": "PNDCD",
    "dates": [
        date(2024, 10, 30),
        date(2025, 4, 30),
        date(2025, 10, 30),
        date(2026, 4, 30),
        date(2026, 10, 30),
        date(2027, 4, 30),
    ],
    "amounts": [
        4.56,
        24.56,
        23.65,
        22.74,
        21.83,
        20.91,
    ],
}


ircfo = {
    "tickerPesos": "IRCFO",
    "tickerDolar": "IRCFD",
    "dates": [
        date(2024, 12, 22),
        date(2025, 6, 22),
        date(2025, 12, 22),
        date(2026, 6, 22),
        date(2026, 12, 22),
        date(2027, 6, 22),
        date(2027, 12, 22),
        date(2028, 6, 22),
    ],
    "amounts": [
        3.61,
        21.11,
        2.84,
        20.34,
        2.08,
        19.58,
        1.31,
        31.31,
    ],
}


arc1o = {
    "tickerPesos": "ARC1O",
    "tickerDolar": "ARC1D",
    "dates": [
        date(2024, 8, 1),
        date(2024, 11, 1),
        date(2025, 2, 1),
        date(2025, 5, 1),
        date(2025, 8, 1),
        date(2025, 11, 1),
        date(2026, 2, 1),
        date(2026, 5, 1),
        date(2026, 8, 1),
        date(2026, 11, 1),
        date(2027, 2, 1),
        date(2027, 5, 1),
        date(2027, 8, 1),
        date(2027, 11, 1),
        date(2028, 2, 1),
        date(2028, 5, 1),
        date(2028, 8, 1),
        date(2028, 11, 1),
        date(2029, 2, 1),
        date(2029, 5, 1),
        date(2029, 8, 1),
        date(2029, 11, 1),
        date(2030, 2, 1),
        date(2030, 5, 1),
        date(2030, 8, 1),
        date(2030, 11, 1),
        date(2031, 2, 1),
        date(2031, 5, 1),
        date(2031, 8, 1),
    ],
    "amounts": [
        2.12,
        2.12,
        2.12,
        2.12,
        2.12,
        2.12,
        3.64,
        2.09,
        2.98,
        2.07,
        6.85,
        4.7,
        6.13,
        5.1,
        7.38,
        5.12,
        1.56,
        6.62,
        8.56,
        6.25,
        7.77,
        6.64,
        8.3,
        6.04,
        7.54,
        6.42,
        8.49,
        6.21,
        4.65,
    ],
}

rccjo = {
    "tickerPesos": "RCCJO",
    "tickerDolar": "RCCJD",
    "dates": [
        date(2024, 10, 9),
        date(2025, 4, 9),
        date(2025, 10, 9),
        date(2026, 4, 9),
        date(2026, 10, 9),
        date(2027, 4, 9),
        date(2027, 10, 9),
    ],
    "amounts": [
        18.41,
        17.82,
        17.23,
        16.64,
        16.05,
        15.46,
        14.88,
    ],
}


msseo = {
    "tickerPesos": "MSSEO",
    "tickerDolar": "MSSED",
    "dates": [
        date(2024, 7, 17),
        date(2025, 7, 17),
        date(2026, 7, 17),
    ],
    "amounts": [6.50, 6.50, 106.50],
}


cac5o = {
    "tickerPesos": "CAC5O",
    "tickerDolar": "CAC5D",
    "dates": [
        date(2024, 8, 25),
        date(2025, 2, 25),
        date(2025, 8, 25),
        date(2026, 2, 25),
        date(2026, 8, 25),
        date(2027, 2, 25),
        date(2027, 8, 25),
        date(2028, 2, 25),
        date(2028, 8, 25),
    ],
    "amounts": [
        4.63,
        17.13,
        16.55,
        15.97,
        15.39,
        14.81,
        14.23,
        13.66,
        13.08,
    ],
}

bol1o = {
    "tickerPesos": "BOL1O",
    "tickerDolar": "BOL1D",
    "dates": [
        date(2024, 9, 7),
        date(2025, 3, 7),
        date(2025, 9, 7),
        date(2026, 3, 7),
    ],
    "amounts": [
        5.00,
        5.00,
        5.00,
        105.00,
    ],
}



lms7o = {
    "tickerPesos": "LMS7O",
    "tickerDolar": "LMS7D",
    "dates": [
        date(2024, 7, 12),
        date(2024, 10, 12),
        date(2025, 1, 12),
        date(2025, 4, 12),
        date(2025, 7, 12),
        date(2025, 10, 12),
        date(2026, 1, 12),
        date(2026, 4, 12),
        date(2026, 7, 12),
        date(2026, 10, 12),
        date(2027, 1, 12),
        date(2027, 4, 12),
        date(2027, 7, 12),
        date(2027, 10, 12),
        date(2028, 1, 12),
        date(2028, 4, 12),
        date(2028, 7, 12),
        date(2028, 10, 12),
    ],
    "amounts": [
        1.75,
        1.75,
        1.75,
        1.75,
        1.75,
        1.75,
        10.08,
        9.93,
        9.79,
        9.64,
        9.50,
        9.35,
        9.21,
        9.06,
        8.91,
        8.77,
        8.62,
        8.52,
    ],
}

lms8o = {
    "tickerPesos": "LMS8O",
    "tickerDolar": "LMS8D",
    "dates": [
        date(2024, 9, 21),
        date(2024, 12, 21),
        date(2025, 3, 21),
        date(2025, 6, 21),
        date(2025, 9, 21),
        date(2025, 12, 21),
        date(2026, 3, 21),
        date(2026, 6, 21),
        date(2026, 9, 21),
        date(2026, 12, 21),
        date(2027, 3, 21),
        date(2027, 6, 21),
        date(2027, 9, 21),
    ],
    "amounts": [
        3.13,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        26.56,
        26.17,
        25.78,
        25.39,
    ],
}


cs44o = {
    "tickerPesos": "CS44O",
    "tickerDolar": "CS44D",
    "dates": [
        date(2024, 7, 17),
        date(2025, 1, 17),
        date(2025, 7, 17),
        date(2026, 1, 17),
        date(2026, 7, 17),
        date(2027, 1, 17),
    ],
    "amounts": [
        3.00,
        3.00,
        3.00,
        3.00,
        3.00,
        103.00,
    ],
}

ymcuo = {
    "tickerPesos": "YMCUO",
    "tickerDolar": "YMCUD",
    "dates": [
        date(2024, 7, 17),
        date(2025, 1, 17),
        date(2025, 7, 17),
        date(2026, 1, 17),
        date(2026, 7, 17),
        date(2027, 1, 17),
        date(2027, 7, 17),
        date(2028, 1, 17),
        date(2028, 7, 17),
        date(2029, 1, 17),
        date(2029, 7, 17),
        date(2030, 1, 17),
        date(2030, 7, 17),
        date(2031, 1, 17),
    ],
    "amounts": [
        4.88,
        4.88,
        4.88,
        4.88,
        14.88,
        14.39,
        13.90,
        13.41,
        12.93,
        12.44,
        11.95,
        11.46,
        10.98,
        10.49,
    ],
}


ircjo = {
    "tickerPesos": "IRCJO",
    "tickerDolar": "IRCJD",
    "dates": [
        date(2024, 8, 28),
        date(2025, 2, 28),
        date(2025, 8, 28),
        date(2026, 2, 28),
        date(2026, 8, 28),
        date(2027, 2, 28),
    ],
    "amounts": [
        3.50,
        3.50,
        3.46,
        3.50,
        3.46,
        103.50,
    ],
}


dnc3o = {
    "tickerPesos": "DNC3O",
    "tickerDolar": "DNC3D",
    "dates": [
        date(2024, 5, 22),
        date(2024, 11, 22),
        date(2025, 5, 22),
        date(2025, 11, 22),
        date(2026, 5, 22),
        date(2026, 11, 22),
    ],
    "amounts": [
        2.03,
        4.88,
        4.88,
        4.88,
        4.88,
        104.88,
    ],
}

gn43o = {
    "tickerPesos": "GN43O",
    "tickerDolar": "GN43D",
    "dates": [
        date(2024, 9, 8),
        date(2024, 12, 8),
        date(2025, 3, 8),
        date(2025, 6, 8),
        date(2025, 9, 8),
        date(2025, 12, 8),
        date(2026, 3, 8),
        date(2026, 6, 8),
        date(2026, 9, 8),
        date(2026, 12, 8),
        date(2027, 3, 8),
    ],
    "amounts": [
        3.13,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        1.56,
        101.56,
    ],
}


ttc7o = {
    "tickerPesos": "TTC7O",
    "tickerDolar": "TTC7D",
    "dates": [
        date(2024, 10, 22),
        date(2025, 4, 22),
        date(2025, 10, 22),
        date(2026, 4, 22),
    ],
    "amounts": [2.99, 2.99, 2.99, 102.99],
}


ots2o = {
    "tickerPesos": "OTS2O",
    "tickerDolar": "OTS2D",
    "dates": [
        date(2025, 1, 24),
        date(2025, 4, 24),
        date(2025, 7, 24),
        date(2025, 10, 24),
        date(2026, 1, 24),
        date(2026, 4, 24),
        date(2026, 7, 24),
        date(2026, 10, 24),
        date(2027, 1, 24),
        date(2027, 4, 24),
    ],
    "amounts": [
        5.25,
        1.75,
        1.75,
        1.75,
        1.75,
        1.75,
        1.75,
        1.75,
        1.75,
        101.75,
    ],
}

cac8o = {
    "tickerPesos": "CAC8O",
    "tickerDolar": "CAC8D",
    "dates": [
        date(2024, 7, 29),
        date(2024, 10, 29),
        date(2025, 1, 29),
        date(2025, 4, 29),
        date(2025, 7, 29),
        date(2025, 10, 29),
        date(2026, 1, 29),
        date(2026, 4, 29),
        date(2026, 6, 29),
    ],
    "amounts": [
        1.49,
        1.49,
        1.49,
        1.49,
        1.49,
        1.49,
        1.49,
        1.49,
        100.99,
    ],
}

pnxco = {
    "tickerPesos": "PNXCO",
    "tickerDolar": "PNXCD",
    "dates": [
        date(2024, 10, 25),
        date(2025, 4, 25),
        date(2025, 10, 25),
        date(2026, 4, 25),
        date(2026, 10, 25),
        date(2027, 4, 25),
        date(2027, 10, 25),
        date(2028, 4, 25),
        date(2028, 10, 25),
        date(2029, 4, 25),
        date(2029, 10, 25),
        date(2030, 4, 25),
        date(2030, 10, 25),
        date(2031, 4, 25),
        date(2031, 10, 25),
        date(2032, 4, 25),
    ],
    "amounts": [
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        4.25,
        37.58,
        2.83,
        36.16,
        1.42,
        34.76,
    ],
}

vscpo = {
    "tickerPesos": "VSCPO",
    "tickerDolar": "VSCPD",
    "dates": [
        date(2024, 11, 3),
        date(2025, 5, 3),
        date(2025, 11, 3),
        date(2026, 5, 3),
        date(2026, 11, 3),
        date(2027, 5, 3),
        date(2027, 11, 3),
        date(2028, 5, 3),
        date(2028, 11, 3),
        date(2029, 5, 3),
    ],
    "amounts": [
        4.00,
        4.00,
        4.00,
        4.00,
        4.00,
        4.00,
        29.00,
        28.00,
        27.00,
        26.00,
    ],
}


hjcbo = {
    "tickerPesos": "HJCBO",
    "tickerDolar": "HJCBD",
    "dates": [
        date(2024, 8, 30),
        date(2024, 11, 30),
        date(2025, 2, 28),
        date(2025, 5, 30),
        date(2025, 8, 30),
        date(2025, 11, 30),
        date(2026, 2, 28),
        date(2026, 5, 30),
    ],
    "amounts": [
        1.50,
        1.50,
        1.47,
        1.50,
        1.50,
        1.50,
        1.47,
        101.50,
    ],
}

irclo = {
    "tickerPesos": "IRCLO",
    "tickerDolar": "IRCLD",
    "dates": [
        date(2024, 12, 10),
        date(2025, 6, 10),
        date(2025, 12, 10),
        date(2026, 6, 10),
    ],
    "amounts": [
        3.00,
        3.00,
        3.00,
        103.00,
    ],
}

lms9o = {
    "tickerPesos": "LMS9O",
    "tickerDolar": "LMS9D",
    "dates": [
        date(2024, 12, 13),
        date(2025, 3, 13),
        date(2025, 6, 13),
        date(2025, 9, 13),
        date(2025, 12, 13),
        date(2026, 3, 13),
        date(2026, 6, 13),
    ],
    "amounts": [
        3.00,
        1.50,
        1.50,
        1.48,
        1.52,
        1.50,
        101.50,
    ],
}

yfcio = {
    "tickerPesos": "YFCIO",
    "tickerDolar": "YFCID",
    "dates": [
        date(2024, 9, 13),
        date(2024, 12, 13),
        date(2025, 3, 13),
        date(2025, 6, 13),
        date(2025, 9, 13),
        date(2025, 12, 13),
        date(2026, 3, 13),
        date(2026, 6, 13),
        date(2026, 9, 13),
        date(2026, 12, 13),
        date(2027, 3, 13),
        date(2027, 6, 13),
    ],
    "amounts": [
        1.48,
        1.48,
        1.48,
        1.48,
        1.48,
        1.48,
        1.48,
        1.48,
        1.48,
        1.48,
        51.48,
        50.74,
    ],
}

tlcmo = {
    "tickerPesos": "TLCMO",
    "tickerDolar": "TLCMD",
    "dates": [
        date(2025, 1, 18),
        date(2025, 7, 18),
        date(2026, 1, 18),
        date(2026, 7, 18),
        date(2027, 1, 18),
        date(2027, 7, 18),
        date(2028, 1, 18),
        date(2028, 7, 18),
        date(2029, 1, 18),
        date(2029, 7, 18),
        date(2030, 1, 18),
        date(2030, 7, 18),
        date(2031, 1, 18),
        date(2031, 7, 18),
    ],
    "amounts": [
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        37.75,
        3.18,
        36.18,
        1.62,
        35.62,
    ],
}


dnc5o = {
    "tickerPesos": "DNC5O",
    "tickerDolar": "DNC5D",
    "dates": [
        date(2025, 2, 5),
        date(2025, 8, 5),
        date(2026, 2, 5),
        date(2026, 6, 5),
        date(2027, 2, 5),
        date(2027, 8, 5),
        date(2028, 2, 5),
        date(2028, 8, 5),
    ],
    "amounts": [
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        4.75,
        104.75,
    ],
}

ymcxo = {
    "tickerPesos": "YMCXO",
    "tickerDolar": "YMCXD",
    "dates": [
        date(2025, 3, 11),
        date(2025, 9, 11),
        date(2026, 3, 11),
        date(2026, 9, 11),
        date(2027, 3, 11),
        date(2027, 9, 11),
        date(2028, 3, 11),
        date(2028, 9, 11),
        date(2029, 3, 11),
        date(2029, 9, 11),
        date(2030, 3, 11),
        date(2030, 9, 11),
        date(2031, 3, 11),
        date(2031, 9, 11),
    ],
    "amounts": [
        4.38,
        4.38,
        4.38,
        4.38,
        4.38,
        4.38,
        4.38,
        4.38,
        4.38,
        24.38,
        3.50,
        23.50,
        2.63,
        62.63,
    ],
}


vscro = {
    "tickerPesos": "VSCRO",
    "tickerDolar": "VSCRD",
    "dates": [
        date(2025, 4, 10),
        date(2025, 10, 10),
        date(2026, 4, 10),
        date(2026, 10, 10),
        date(2027, 4, 10),
        date(2027, 10, 10),
        date(2028, 4, 10),
        date(2028, 10, 10),
        date(2029, 4, 10),
        date(2029, 10, 10),
        date(2030, 4, 10),
        date(2030, 10, 10),
        date(2031, 4, 10),
        date(2031, 10, 10),
    ],
    "amounts": [
        3.83,
        3.83,
        3.83,
        3.83,
        3.83,
        3.83,
        3.83,
        3.83,
        3.83,
        36.83,
        2.56,
        35.56,
        1.30,
        35.30,
    ],
}


rucdo = {
    "tickerPesos": "RUCDO",
    "tickerDolar": "RUCDD",
    "dates": [
        date(2025, 6, 5),
        date(2025, 12, 5),
        date(2026, 6, 5),
        date(2026, 12, 5),
        date(2027, 6, 5),
        date(2027, 12, 5),
        date(2028, 6, 5),
        date(2028, 12, 5),
        date(2029, 6, 5),
        date(2029, 12, 5),
        date(2030, 6, 5),
        date(2030, 12, 5),
    ],
    "amounts": [
        4.88,
        4.88,
        4.88,
        4.88,
        4.88,
        4.88,
        4.88,
        22.38,
        4.02,
        21.52,
        3.17,
        68.17,
    ],
}


pmm29 = {
    "tickerPesos": "PMM29",
    "tickerDolar": "PM29D",
    "dates": [
        date(2024, 9, 19),
        date(2025, 3, 19),
        date(2025, 9, 19),
        date(2026, 3, 19),
        date(2026, 9, 19),
        date(2027, 3, 19),
        date(2027, 9, 19),
        date(2028, 3, 19),
        date(2028, 9, 19),
        date(2029, 3, 19),
    ],
    "amounts": [
        9.91,
        9.69,
        9.47,
        9.25,
        9.03,
        8.8,
        8.58,
        8.36,
        8.14,
        7.92,
    ],
}

ndt25 = {
    "tickerPesos": "NDT25",
    "tickerDolar": "NDT5D",
    "dates": [
        date(2024, 10, 27),
        date(2025, 4, 27),
        date(2025, 10, 27),
        date(2026, 4, 27),
        date(2026, 10, 27),
        date(2027, 4, 27),
        date(2027, 10, 27),
        date(2028, 4, 27),
        date(2028, 10, 27),
        date(2029, 4, 27),
        date(2029, 10, 27),
        date(2030, 4, 27),
    ],
    "amounts": [
        10.57,
        10.11,
        9.69,
        9.31,
        8.97,
        8.67,
        8.41,
        8.19,
        8.01,
        7.87,
        7.77,
        7.71,
    ],
}

ba37d = {
    "tickerPesos": "BA37D",
    "tickerDolar": "BA7DD",
    "dates": [
        date(2024, 9, 1),
        date(2025, 3, 1),
        date(2025, 9, 1),
        date(2026, 3, 1),
        date(2026, 9, 1),
        date(2027, 3, 1),
        date(2027, 9, 1),
        date(2028, 3, 1),
        date(2028, 9, 1),
        date(2029, 3, 1),
        date(2029, 9, 1),
        date(2030, 3, 1),
        date(2030, 9, 1),
        date(2031, 3, 1),
        date(2031, 9, 1),
        date(2032, 3, 1),
        date(2032, 9, 1),
        date(2033, 3, 1),
        date(2033, 9, 1),
        date(2034, 3, 1),
        date(2034, 9, 1),
        date(2035, 3, 1),
        date(2035, 9, 1),
        date(2036, 3, 1),
        date(2036, 9, 1),
        date(2037, 3, 1),
        date(2037, 9, 1),
    ],
    "amounts": [
        4.96,
        5.44,
        5.37,
        5.94,
        5.85,
        6.13,
        6.02,
        6.1,
        5.99,
        6.19,
        6.07,
        5.23,
        5.13,
        5.54,
        5.42,
        5.48,
        5.36,
        5.4,
        5.27,
        5.4,
        5.26,
        5.32,
        5.18,
        5.3,
        5.14,
        5.15,
        4.99,
    ],
}

calendar = [
    # crceo,
    # yca6o,
    ymcio,
    ymcjo,
    # cp17o,
    tlc1o,
    mtcgo,
    gncxo,
    pndco,
    ircfo,
    # cs37o,
    arc1o,
    rccjo,
    # mrcao,
    # mrclo,
    # aec1o,
    # mrcoo,
    cac5o,
    # bol1o,
    # mrcqo,
    lms7o,
    lms8o,
    # pncuo,
    # loc4o,
    # vscno,
    cs44o,
    # ymcuo,
    # leceo,
    ircjo,
    # pecao,
    # pecbo,
    # dnc3o,
    gn43o,
    # mrcuo,
    # rac5o,
    # rac6o,
    ttc7o,
    # cs45o,
    # ots2o,
    cac8o,
    pnxco,
    vscpo,
    # crcjo,
    # mrcxo,
    # mrcyo,
    hjcbo,
    irclo,
    lms9o,
    yfcio,
    tlcmo,
    # mssfo,
    # snabo,
    # dnc5o,
    # crclo,
    # mr35o,
    # lecgo,
    ymcxo,
    # snsbo,
    vscro,
    # pecgo,
    # gyc4o,
    # rucdo,
    pmm29,
    ndt25,
    ba37d,
]


def modified_duration(dates, amounts, xirr):
    nav_total = 0
    dur_total = 0
    for i, d in enumerate(dates):
        dias = (d - date.today()).days
        nav = amounts[i] / pow(pow(1 + xirr / 2, 2), dias / 365) if dias > 0 else 0
        dur = nav * (dias / 365) / 100
        nav_total += nav
        dur_total += dur

    duration = dur_total * 100 / nav_total
    modified_duration = duration / (1 + xirr / 2)
    return modified_duration


def get_dolar():
    r = requests.post(
        "https://www.bullmarketbrokers.com/Information/StockPrice/GetDollarPrice",
        headers={"Accept": "application/json"},
    )
    data = simplejson.loads(r.text)
    return (data["bidPrice"] + data["bidPrice"]) / 2


def get_token():
    config = configparser.ConfigParser()
    config.read("config.ini")
    usuario = config["credentials"]["balanz_username"]
    password = config["credentials"]["balanz_password"]

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


def get_data_ons(token):
    r = requests.get(
        "https://clientes.balanz.com/api/v1/cotizaciones/panel/27?token=0&tokenindice=0",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": token,
        },
    )
    data = simplejson.loads(r.text)
    return data["cotizaciones"]


def get_data_provs(token):
    r = requests.get(
        "https://clientes.balanz.com/api/v1/cotizaciones/panel/24?token=0&tokenindice=0",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": token,
        },
    )
    data = simplejson.loads(r.text)
    return data["cotizaciones"]


def get_24hs_date():
    _24hs = date.today() + timedelta(days=1)
    if (
        _24hs.isoweekday() in set((6, 7))
    ):  # Si la fecha _24hs es sabado o domingo, le agrego los dias necesarios para que sea lunes.
        _24hs += timedelta(days=_24hs.isoweekday() % 5)
    return _24hs


def get_dates_amounts(instrumento):
    dates = instrumento["dates"][:]
    amounts = instrumento["amounts"][:]
    _24hs = get_24hs_date()
    ban = True
    while ban:
        if dates[0] <= _24hs:
            dates.pop(0)
            amounts.pop(0)
        else:
            ban = False
    return dates, amounts


def create_df():
    dolar = get_dolar()

    try:
        token = open("token.csv", "r").read()
        data = get_data_ons(token)
        data += get_data_provs(token)
    except:
        token = get_token()
        open("token.csv", "w").write(token)
        data = get_data_ons(token)
        data += get_data_provs(token)

    for_df = []
    for instrumento in calendar:
        tir_ask_pesos = 0
        tir_bid_pesos = 0
        tir_ask_dolar = 0
        tir_bid_dolar = 0

        try:
            result = list(
                filter(
                    lambda x: (
                        x["ticker"] == instrumento["tickerPesos"]
                        and x["plazo"] == "24hs"
                    ),
                    data,
                )
            )
            prCompraPesos = result[0]["pc"] * 100
            prVentaPesos = result[0]["pv"] * 100

            if prCompraPesos > 0:
                dates, amounts = get_dates_amounts(instrumento)
                dates.insert(0, get_24hs_date())
                amounts.insert(0, -1 * prCompraPesos / dolar)
                tir_ask_pesos = xirr(dates, amounts)  # type: ignore

            if prVentaPesos > 0:
                dates, amounts = get_dates_amounts(instrumento)
                dates.insert(0, get_24hs_date())
                amounts.insert(0, -1 * prVentaPesos / dolar)
                tir_bid_pesos = xirr(dates, amounts)  # type: ignore

        except:
            print("Error con intrumento: ", instrumento["tickerPesos"])

        try:
            result = list(
                filter(
                    lambda x: (
                        x["ticker"] == instrumento["tickerDolar"]
                        and x["plazo"] == "24hs"
                    ),
                    data,
                )
            )
            prCompraDolar = result[0]["pc"] * 100
            prVentaDolar = result[0]["pv"] * 100

            if prCompraDolar > 0:
                dates, amounts = get_dates_amounts(instrumento)
                dates.insert(0, get_24hs_date())
                amounts.insert(0, -1 * prCompraDolar)
                tir_ask_dolar = xirr(dates, amounts)  # type: ignore

            if prVentaDolar > 0:
                dates, amounts = get_dates_amounts(instrumento)
                dates.insert(0, get_24hs_date())
                amounts.insert(0, -1 * prVentaDolar)
                tir_bid_dolar = xirr(dates, amounts)  # type: ignore

        except:
            print("Error con intrumento: ", instrumento["tickerDolar"])

        md = modified_duration(
            instrumento["dates"][:], instrumento["amounts"][:], tir_bid_dolar
        )
        for_df.append(
            [
                instrumento["tickerPesos"],
                instrumento["tickerDolar"],
                prCompraPesos,
                prVentaPesos,
                prCompraDolar,
                prVentaDolar,
                tir_ask_pesos,  # type: ignore
                tir_bid_pesos,  # type: ignore
                tir_ask_dolar,  # type: ignore
                tir_bid_dolar,  # type: ignore
                md,
            ]
        )

    df = pd.DataFrame(
        data=for_df,
        columns=[
            "tickerPesos",
            "tickerDolar",
            "Pr Compra Pesos",
            "Pr Venta Pesos",
            "Pr Compra Dolar",
            "Pr Venta Dolar",
            "TIR Ask Pesos",
            "TIR Bid Pesos",
            "TIR Ask Dolar",
            "TIR Bid Dolar",
            "Mod. Dur.",
        ],
    )
    # print(
    #     tabulate(
    #         df,  # type: ignore
    #         headers="keys",
    #         tablefmt="mixed_outline",
    #     )
    # )

    dff = df[
        (
            ((df["TIR Ask Pesos"] < 0.04) & (df["Pr Compra Pesos"] > 0))
            | (df["TIR Ask Dolar"] < 0.04) & (df["Pr Compra Dolar"] > 0)
        )
    ].sort_values(by=["TIR Ask Dolar"], ascending=False)[
        [
            "tickerPesos",
            "Pr Compra Pesos",
            "TIR Ask Pesos",
            "Pr Compra Dolar",
            "TIR Ask Dolar",
        ]
    ]
    dff["TIR Ask Pesos"] = dff["TIR Ask Pesos"].map("{:.2%}".format)
    dff["TIR Ask Dolar"] = dff["TIR Ask Dolar"].map("{:.2%}".format)
    print(
        tabulate(
            dff,  # type: ignore
            headers="keys",
            tablefmt="mixed_outline",
        )
    )
    df = df[(df["TIR Bid Pesos"] > 0.08) | (df["TIR Bid Dolar"] > 0.08)]

    return df


##############################################################################################

app = Dash(__name__)

df = create_df()

app.layout = html.Div(
    id="container",
    children=[
        html.H1(
            children="Curva ONs",
            style={
                "font-family": "Franziska, Georgia, Cambria",
                "color": "#4B6082",
                "text-align": "center",
            },
        ),
        html.Div(
            children=[
                html.Div(
                    children=[
                        dcc.RadioItems(
                            ["Pesos", "Dolares"], "Pesos", id="moneda", inline=True
                        ),
                    ]
                ),
                html.Div(
                    html.Button(
                        "Actualizar",
                        id="actualizar",
                        n_clicks=0,
                        style={
                            "align-items": "center",
                            "padding": "6px 14px",
                            "font-family": "-apple-system, BlinkMacSystemFont, 'Roboto', sans-serif",
                            "border-radius": "6px",
                            "border-color": "grey",
                            "color": "#fff",
                            "background": "linear-gradient(180deg, #4B91F7 0%, #367AF6 100%)",
                            "background-origin": "border-box",
                        },
                    ),
                ),
            ]
        ),
        dcc.Graph(id="graph-1"),
        dash_table.DataTable(
            id="datatable-interactivity",
            columns=[
                {
                    "name": "tickerPesos",
                    "id": "tickerPesos",
                    "deletable": False,
                    "selectable": True,
                },
                {
                    "name": "tickerDolar",
                    "id": "tickerDolar",
                    "deletable": False,
                    "selectable": True,
                },
                {
                    "name": "TIR Bid Pesos",
                    "id": "TIR Bid Pesos",
                    "deletable": False,
                    "selectable": True,
                    "type": "numeric",
                    "format": FormatTemplate.percentage(2),
                },
                {
                    "name": "TIR Bid Dolar",
                    "id": "TIR Bid Dolar",
                    "deletable": False,
                    "selectable": True,
                    "type": "numeric",
                    "format": FormatTemplate.percentage(2),
                },
                {
                    "name": "Mod. Dur.",
                    "id": "Mod. Dur.",
                    "deletable": False,
                    "selectable": True,
                    "type": "numeric",
                    "format": FormatTemplate.Format(
                        precision=2, scheme=FormatTemplate.Scheme.fixed
                    ),
                },
            ],
            data=df.to_dict("records"),
            editable=True,
            sort_action="native",
            sort_mode="multi",
            row_deletable=True,
            selected_columns=[],
            selected_rows=[],
            page_action="native",
            page_current=0,
            page_size=100,
            style_header={"backgroundColor": "rgb(30, 30, 30)", "color": "white"},
            style_data={"backgroundColor": "#e2f0fb", "color": "black"},
        ),
        html.Div(id="datatable-interactivity-container"),
    ],
)


@callback(Output("graph-1", "figure"), Input("moneda", "value"))
def update_graph(moneda):
    fig = px.scatter(
        df,
        x="Mod. Dur.",
        y="TIR Bid Pesos" if moneda == "Pesos" else "TIR Bid Dolar",
        trendline="ols",
        trendline_options=dict(log_x=True),
        hover_name="tickerPesos" if moneda == "Pesos" else "tickerDolar",
        text="tickerPesos" if moneda == "Pesos" else "tickerDolar",
        width=1400,
        height=700,
    )

    return fig


@callback(Output("datatable-interactivity", "data"), [Input("actualizar", "n_clicks")])
def update_output(n_clicks):
    global df
    df = create_df()
    return df.to_dict("records")


if __name__ == "__main__":
    app.run(port="8051")
