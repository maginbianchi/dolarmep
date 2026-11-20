"""Tests del registro de P&L/pendientes y del parseo de market data / instrumentos."""

import csv
from datetime import datetime

import pytest

from estrategia import DOLARIZAR, PESIFICAR
from mercado import DataManager, WebSocketClient, create_instrument, load_instruments
from registro import RegistroPendientes, RegistroPnL, calcular_pnl


# ---------------------- P&L ----------------------
def test_calcular_pnl_por_modo():
    # Compré USD a 1415 y los vendí a 1418
    assert calcular_pnl(DOLARIZAR, 1415.0, 1418.0, 100.0) == pytest.approx(300.0)
    # Vendí USD a 1418 y los recompré a 1415
    assert calcular_pnl(PESIFICAR, 1418.0, 1415.0, 100.0) == pytest.approx(300.0)
    # Pérdida
    assert calcular_pnl(DOLARIZAR, 1418.0, 1415.0, 100.0) == pytest.approx(-300.0)
    # Sin pata 2
    assert calcular_pnl(DOLARIZAR, 1418.0, None, 0.0) == 0.0


def _registrar(reg, pnl_usd=100.0, tc2=1418.0):
    return reg.registrar(
        arbitraje_id="X",
        estado="COMPLETO",
        modo_pata1=DOLARIZAR,
        instrumento_pata1="A",
        instrumentos_pata2="B",
        tc1=1415.0,
        tc2=tc2,
        usd_pata1=pnl_usd,
        usd_cerrados=pnl_usd,
        usd_sin_cerrar=0.0,
    )


def test_pnl_acumula_y_se_recupera_al_reiniciar(tmp_path):
    path = str(tmp_path / "pnl.csv")
    reloj = lambda: datetime(2026, 11, 19, 12, 0, 0)  # noqa: E731
    reg = RegistroPnL(path, reloj=reloj)
    _registrar(reg)
    _registrar(reg)
    assert reg.acumulado == pytest.approx(600.0) and reg.operaciones == 2

    # Reinicio del bot el mismo día: retoma el acumulado
    reg2 = RegistroPnL(path, reloj=reloj)
    assert reg2.acumulado == pytest.approx(600.0) and reg2.operaciones == 2
    _registrar(reg2)
    with open(path, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    assert filas[-1]["pnl_acumulado_dia"] == "900.00"
    assert filas[-1]["operaciones_dia"] == "3"


def test_pnl_se_reinicia_al_cambiar_el_dia(tmp_path):
    path = str(tmp_path / "pnl.csv")
    fechas = iter([datetime(2026, 11, 19, 17), datetime(2026, 11, 19, 17), datetime(2026, 11, 20, 11)])
    reg = RegistroPnL(path, reloj=lambda: next(fechas))
    _registrar(reg)
    _registrar(reg)
    assert reg.acumulado == pytest.approx(300.0) and reg.operaciones == 1
    assert reg.fecha == "2026-11-20"


def test_pendientes_escribe_encabezado_una_vez(tmp_path):
    path = tmp_path / "pend.csv"
    reg = RegistroPendientes(str(path))
    reg.registrar("X", "DESHACER_ACTIVA", 1, ticker="A", cantidad=10, monto=1.5)
    reg.registrar("Y", "CIERRE_INCOMPLETO", 2, ticker="B", monto=2.0)
    lineas = path.read_text(encoding="utf-8").splitlines()
    assert lineas[0].startswith("timestamp,arbitraje_id,tipo")
    assert len(lineas) == 3


# ---------------------- Market data ----------------------
def test_data_manager_parsea_pesos_y_dolares():
    inst = create_instrument("AL30", "AL30D")
    dm = DataManager([inst])
    dm.update_instrument_data(
        [
            "M:bm_MERV_AL30_24hs|x|1500|85000|85100|2000|resto",
            "M:bm_MERV_AL30D_24hs|x|300|60.1|60.2|||",
        ]
    )
    snap = dm.get_snapshot()[0]
    assert (snap["siCompraPesos"], snap["prCompraPesos"]) == (1500, 85000)
    assert (snap["prVentaPesos"], snap["siVentaPesos"]) == (85100, 2000)
    assert (snap["prCompraDolar"], snap["prVentaDolar"]) == (60.1, 60.2)
    assert snap["siVentaDolar"] is None          # punta vacía → None
    # El snapshot es una copia: modificarlo no altera el DataManager
    snap["prCompraPesos"] = 1
    assert dm.get_snapshot()[0]["prCompraPesos"] == 85000


def test_data_manager_ignora_mensajes_invalidos():
    dm = DataManager([create_instrument("AL30", "AL30D")])
    dm.update_instrument_data(["M:bm_MERV_AL30_48hs|x|1|2|3|4", "basura", "M:bm_MERV_OTRO_24hs|x|1|2|3|4"])
    assert dm.get_snapshot()[0]["prCompraPesos"] is None


def test_mensaje_de_suscripcion_incluye_pesos_y_dolares():
    ws = WebSocketClient("wss://x", None, [create_instrument("AL30", "AL30D")])
    msg = ws.create_subscription_message()
    assert '"md.bm_MERV_AL30_24hs"' in msg and '"md.bm_MERV_AL30D_24hs"' in msg


def test_load_instruments_respeta_flag_y_limites(tmp_path):
    path = tmp_path / "inst.csv"
    path.write_text(
        "# comentario\nAL30,AL30D,1,1,2000\nGD30,GD30D,0,0,0\nYMCXO,YMCXD,1,,\n\n",
        encoding="utf-8",
    )
    instrumentos = load_instruments(str(path))
    assert [i["ticker"] for i in instrumentos] == ["AL30", "YMCXO"]
    assert (instrumentos[0]["min_quant"], instrumentos[0]["max_quant"]) == (1, 2000)
    assert (instrumentos[1]["min_quant"], instrumentos[1]["max_quant"]) == (1, 500)
