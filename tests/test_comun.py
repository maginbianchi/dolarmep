import json

import pytest
import requests

from comun import cliente_rest
from comun.cotizaciones import Cotizacion, LibroCotizaciones, parsear_registro, parsear_topic
from comun.feed_matriz import FeedMatriz, mensaje_suscripcion
from comun.feed_primary import FeedPrimary, parsear_mensaje_primary
from comun.instrumentos import cargar_instrumentos, cargar_mis_activos
from comun.ordenes import enviar_limit_y_verificar
from comun.rutas import leer_credenciales, url_websocket_matriz
from fakes import FakeClient, registro


# ====================== cotizaciones ======================
@pytest.mark.parametrize(
    "topic, esperado",
    [
        ("M:bm_MERV_AL30_24hs", ("AL30", "24hs")),
        ("M:bm_MERV_AL30D_CI", ("AL30D", "CI")),
        # Tickers que contienen "CI" no deben confundirse con el plazo CI
        ("M:bm_MERV_YMCID_24hs", ("YMCID", "24hs")),
        ("M:bm_MERV_CICAD_CI", ("CICAD", "CI")),
        ("M:bm_MERV_AL30_48hs", None),
        ("X:algo", None),
    ],
)
def test_parsear_topic(topic, esperado):
    assert parsear_topic(topic) == esperado


def test_parsear_registro_toma_todos_los_campos():
    cot = parsear_registro(registro("AL30", "24hs", 100.5, 101, 20, 30, minimo=99, cierre=98))
    assert cot == Cotizacion("AL30", "24hs", 100.5, 101.0, 20.0, 30.0, 99.0, 98.0)


def test_parsear_registro_puntas_vacias_son_none():
    cot = parsear_registro(registro("AL30", "CI", "", "", "", ""))
    assert cot is not None
    assert (cot.pr_compra, cot.pr_venta, cot.si_compra, cot.si_venta) == (None, None, None, None)


def test_parsear_registro_corto_o_desconocido():
    assert parsear_registro("M:bm_MERV_AL30_24hs|x|1") is None
    assert parsear_registro("M:bm_MERV_AL30_24hs|x|1|2|3|4") == Cotizacion("AL30", "24hs", 2, 3, 1, 4)
    assert parsear_registro(registro("AL30", "48hs", 1, 2)) is None


def test_libro_actualizar_campos_conserva_lo_no_recibido():
    libro = LibroCotizaciones()
    libro.actualizar_campos("AL30", "24hs", pr_compra=10, si_compra=5)
    nueva = libro.actualizar_campos("AL30", "24hs", pr_venta=11)
    assert (nueva.pr_compra, nueva.si_compra, nueva.pr_venta) == (10, 5, 11)


def test_libro_separa_plazos_y_snapshot_es_copia():
    libro = LibroCotizaciones()
    libro.actualizar(Cotizacion("AL30D", "24hs", 60))
    libro.actualizar(Cotizacion("AL30D", "CI", 59))
    snap = libro.snapshot()
    libro.actualizar(Cotizacion("AL30D", "24hs", 61))
    assert snap[("AL30D", "24hs")].pr_compra == 60
    assert libro.obtener("AL30D", "CI").pr_compra == 59


# ====================== instrumentos y rutas ======================
def test_cargar_instrumentos(tmp_path):
    csv = tmp_path / "inst.csv"
    csv.write_text("# comentario\nAL30,AL30D,1,10,400\nGD30,GD30D,0,1,1\n\nTX26,TX26D,1\nX\n", encoding="utf-8")
    habilitados = cargar_instrumentos(str(csv))
    assert [(i["ticker"], i["min_quant"], i["max_quant"]) for i in habilitados] == [
        ("AL30", 10, 400),
        ("TX26", 1, 500),
    ]
    todos = cargar_instrumentos(str(csv), solo_habilitados=False)
    assert [i["ticker"] for i in todos] == ["AL30", "GD30", "TX26"]


def test_cargar_mis_activos(tmp_path):
    csv = tmp_path / "mis.csv"
    csv.write_text("AL30\n# GD30\n TX26 \n", encoding="utf-8")
    assert cargar_mis_activos(str(csv)) == ["AL30", "TX26"]


def test_leer_credenciales_y_url(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[credentials]\nmatriz_session_id = S\nmatriz_conn_id = C\n", encoding="utf-8")
    cred = leer_credenciales(str(ini))
    assert url_websocket_matriz(cred) == "wss://matriz.cocos.xoms.com.ar/ws?session_id=S&conn_id=C"
    with pytest.raises(FileNotFoundError):
        leer_credenciales(str(tmp_path / "no_existe.ini"))


# ====================== feed Matriz ======================
def test_mensaje_suscripcion_sin_duplicados():
    msg = json.loads(mensaje_suscripcion([("AL30", "24hs"), ("AL30", "CI"), ("AL30", "24hs")]))
    assert msg == {
        "_req": "S",
        "topicType": "md",
        "topics": ["md.bm_MERV_AL30_24hs", "md.bm_MERV_AL30_CI"],
        "replace": False,
    }


def test_feed_matriz_procesa_string_y_lista_y_llama_callback():
    libro, recibidas = LibroCotizaciones(), []
    feed = FeedMatriz("url", [], libro, recibidas.append)
    feed.on_message(None, registro("AL30", "24hs", 1, 2))
    feed.on_message(None, json.dumps([registro("AL30D", "CI", 3, 4), "basura"]))
    feed.on_message(None, "X|no es market data")
    assert [(c.ticker, c.plazo) for c in recibidas] == [("AL30", "24hs"), ("AL30D", "CI")]
    assert libro.obtener("AL30D", "CI").pr_venta == 4


def test_feed_matriz_error_en_callback_no_corta_el_feed():
    libro = LibroCotizaciones()

    def falla(_):
        raise RuntimeError("boom")

    feed = FeedMatriz("url", [], libro, falla)
    feed.procesar_registros([registro("AL30", "24hs", 1, 2), registro("GD30", "24hs", 3, 4)])
    assert libro.obtener("GD30", "24hs") is not None


# ====================== feed Primary ======================
def _md(symbol, **md):
    return {"type": "Md", "instrumentId": {"symbol": symbol}, "marketData": md}


def test_parsear_mensaje_primary():
    data = _md("MERV - XMEV - AL30 - 24hs", BI=[{"price": 100, "size": 5}], OF=[])
    assert parsear_mensaje_primary(data) == (
        "AL30",
        "24hs",
        {"pr_compra": 100, "si_compra": 5, "pr_venta": None, "si_venta": None},
    )
    assert parsear_mensaje_primary(_md("MERV - XMEV - AL30 - 48hs", BI=[])) is None


def test_feed_primary_actualiza_solo_las_puntas_recibidas():
    libro = LibroCotizaciones()
    feed = FeedPrimary("token", [("AL30", "CI")], libro)
    feed.on_message(None, json.dumps(_md("MERV - XMEV - AL30 - CI", BI=[{"price": 1, "size": 2}], OF=[{"price": 3, "size": 4}])))
    feed.on_message(None, json.dumps(_md("MERV - XMEV - AL30 - CI", LA={"price": 2})))
    feed.on_message(None, json.dumps({"type": "error", "description": "x"}))
    assert libro.obtener("AL30", "CI") == Cotizacion("AL30", "CI", 1, 3, 2, 4)
    sus = json.loads(feed.mensaje_suscripcion())
    assert sus["products"] == [{"symbol": "MERV - XMEV - AL30 - CI", "marketId": "ROFX"}]


# ====================== cliente REST y órdenes ======================
def test_cliente_rest_usa_timeout_y_no_lanza(monkeypatch):
    llamadas = []

    def get_que_falla(*args, **kwargs):
        llamadas.append(kwargs)
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(cliente_rest.CocosMatrizClient, "login", lambda self: True)
    monkeypatch.setattr(cliente_rest.requests, "get", get_que_falla)
    client = cliente_rest.CocosMatrizClient("u", "p")
    assert "error" in client.send_order("S", "BUY", 1, 10.0)
    assert "error" in client.cancel_order("id", "p")
    assert all(k["timeout"] == cliente_rest.REQUEST_TIMEOUT for k in llamadas)


def test_orden_filled_no_se_cancela():
    client = FakeClient("FILLED")
    res = enviar_limit_y_verificar(client, "acc", "MERV - XMEV - AL30 - 24hs", "BUY", 10, 100.0)
    assert res["estado"] == "FILLED" and client.cancelaciones == []
    assert client.ordenes[0]["price"] == 100.0 and client.ordenes[0]["ord_type"] == "LIMIT"


def test_orden_no_filled_se_cancela():
    client = FakeClient("PENDING_NEW")
    res = enviar_limit_y_verificar(client, "acc", "S", "BUY", 10, 100.0)
    assert res["estado"] == "PENDING_NEW" and res["enviada"] and res["cancelada"]
    assert client.cancelaciones == ["id1"] and client.consultas == ["id1"]


def test_orden_con_error_no_se_consulta():
    client = FakeClient(error_envio="rechazada")
    res = enviar_limit_y_verificar(client, "acc", "S", "BUY", 10, 100.0)
    assert res["estado"] == "ERROR" and not res["enviada"] and client.consultas == []
