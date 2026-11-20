"""
Tests del Executer contra un broker simulado. Cada test cubre una regla del enunciado.

Escenario `mercado_dolarizar_primero` (precios USD = 100 cada 100 VN → 1 nominal = USD 1):
    Pata 1: DOLARIZAR DNC3O x1000 → BUY DNC3O (pesos) / SELL DNC3D (USD)   TC 1415.5
    Pata 2: PESIFICAR YMCXO x1000 → BUY YMCXD (USD) / SELL YMCXO (pesos)   TC 1418.0
"""

import copy
import csv

import pytest

from executer_v4 import ESTADO_COMPLETO, ESTADO_INCOMPLETO, ESTADO_SIN_PATA2
from fakes_v4 import inst


def _leer_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _pnl(tmp_path):
    p = tmp_path / "pnl_diario.csv"
    return _leer_csv(p) if p.exists() else []


def _pendientes(tmp_path):
    p = tmp_path / "pendientes.csv"
    return _leer_csv(p) if p.exists() else []


# ---------------------- Confirmación ----------------------
def test_requiere_dos_ciclos_consecutivos(armar_executer, mercado_dolarizar_primero):
    ex, client, _ = armar_executer(mercado_dolarizar_primero, require_confirmation=True)
    assert ex.execute(copy.deepcopy(mercado_dolarizar_primero)) is None
    assert client.enviadas == []
    assert ex.execute(copy.deepcopy(mercado_dolarizar_primero)) is not None
    assert client.enviadas


def test_confirmacion_se_pierde_si_desaparece(armar_executer, mercado_dolarizar_primero):
    ex, client, _ = armar_executer(mercado_dolarizar_primero, require_confirmation=True)
    sin_arbitraje = copy.deepcopy(mercado_dolarizar_primero)
    sin_arbitraje[2] = inst("YMCXO", tc_dol=1421.0, tc_pes=1416.0)
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    ex.execute(sin_arbitraje)
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    assert client.enviadas == []


# ---------------------- Camino feliz ----------------------
def test_dolarizar_primero_completo(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(mercado_dolarizar_primero)
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert client.secuencia() == [
        ("DNC3O", "BUY", 1000),
        ("DNC3D", "SELL", 1000),
        ("YMCXD", "BUY", 1000),
        ("YMCXO", "SELL", 1000),
    ]
    fila = _pnl(tmp_path)[0]
    assert fila["estado"] == ESTADO_COMPLETO
    assert fila["modo_pata1"] == "DOLARIZAR"
    assert float(fila["tc_pata1"]) == pytest.approx(1415.5)
    assert float(fila["tc_pata2"]) == pytest.approx(1418.0)
    # Compré USD 1000 a 1415.5 y los vendí a 1418 → +2500 ARS
    assert float(fila["pnl_ars"]) == pytest.approx(2500.0)
    assert client.activas() == []
    assert _pendientes(tmp_path) == []


def test_pesificar_primero_completo(armar_executer, mercado_pesificar_primero, tmp_path):
    ex, client, _ = armar_executer(mercado_pesificar_primero)
    ex.execute(copy.deepcopy(mercado_pesificar_primero))

    sec = client.secuencia()
    # Primero se compra en dólares y luego se vende en pesos el instrumento más desviado
    assert sec[0][:2] == ("YMCXD", "BUY")
    assert sec[1][:2] == ("YMCXO", "SELL")
    # Con los pesos obtenidos se dolariza DNC3O
    assert sec[2][:2] == ("DNC3O", "BUY")
    assert sec[3][:2] == ("DNC3D", "SELL")
    fila = _pnl(tmp_path)[0]
    assert fila["estado"] == ESTADO_COMPLETO
    tc1, tc2 = float(fila["tc_pata1"]), float(fila["tc_pata2"])
    assert tc1 == pytest.approx(1419.5) and tc2 == pytest.approx(1416.2)
    assert float(fila["pnl_ars"]) == pytest.approx(float(fila["usd_cerrados"]) * (tc1 - tc2), abs=0.01)
    assert float(fila["pnl_ars"]) > 0
    assert client.activas() == []


def test_pnl_se_acumula_en_el_dia(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, _, _ = armar_executer(mercado_dolarizar_primero)
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    filas = _pnl(tmp_path)
    assert [int(f["operaciones_dia"]) for f in filas] == [1, 2]
    assert float(filas[1]["pnl_acumulado_dia"]) == pytest.approx(5000.0)


# ---------------------- Pata 1: primera operación ----------------------
def test_compra_inicial_no_ejecutada_solo_loguea(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(mercado_dolarizar_primero, {("DNC3O", "BUY"): 0.0})
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert client.secuencia() == [("DNC3O", "BUY", 1000)]
    assert client.cancelaciones == ["CL1"]      # se cancela la orden que quedó sin ejecutar
    assert client.activas() == []
    assert _pnl(tmp_path) == []                 # no hubo arbitraje
    assert _pendientes(tmp_path) == []


def test_compra_inicial_rechazada_o_con_error(armar_executer, mercado_dolarizar_primero, tmp_path):
    for spec in ("reject", "error"):
        ex, client, _ = armar_executer(mercado_dolarizar_primero, {("DNC3O", "BUY"): spec})
        ex.execute(copy.deepcopy(mercado_dolarizar_primero))
        assert len(client.enviadas) <= 1
        assert client.activas() == []
    assert _pnl(tmp_path) == []


def test_compra_inicial_parcial_continua_con_lo_ejecutado(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(mercado_dolarizar_primero, {("DNC3O", "BUY"): 400})
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert client.secuencia() == [
        ("DNC3O", "BUY", 1000),
        ("DNC3D", "SELL", 400),       # se vende solo lo ejecutado
        ("YMCXD", "BUY", 400),        # y se cierra solo ese monto
        ("YMCXO", "SELL", 400),
    ]
    assert client.orders["CL1"]["status"] == "CANCELLED"   # remanente cancelado
    assert client.activas() == []
    assert _pnl(tmp_path)[0]["estado"] == ESTADO_COMPLETO


def test_fill_tardio_durante_la_cancelacion_se_respeta(armar_executer, mercado_dolarizar_primero):
    ex, client, _ = armar_executer(mercado_dolarizar_primero, {("DNC3O", "BUY"): 400})
    client.fills_tardios[("DNC3O", "BUY")] = 100      # entran 100 más mientras se cancela
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    assert client.secuencia()[1] == ("DNC3D", "SELL", 500)


# ---------------------- Pata 1: segunda operación ----------------------
def test_venta_parcial_crea_deshacer_y_continua(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(
        mercado_dolarizar_primero,
        {("DNC3D", "SELL"): 700, ("DNC3O", "SELL"): 0.0},   # el deshacer queda vivo
    )
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    sec = client.secuencia()
    assert sec[:2] == [("DNC3O", "BUY", 1000), ("DNC3D", "SELL", 1000)]
    # Diferencia en cartera: 300 DNC3O, venta en PESOS (moneda de compra) a precio de compra
    deshacer = client.enviadas[2]
    assert (deshacer["ticker"], deshacer["side"], deshacer["orderQty"]) == ("DNC3O", "SELL", 300)
    assert deshacer["price"] == pytest.approx(client.enviadas[0]["avgPx"])
    # El arbitraje continúa con lo vendido (USD 700)
    assert sec[3:] == [("YMCXD", "BUY", 700), ("YMCXO", "SELL", 700)]
    # Solo la orden de deshacer queda activa, y está registrada para el operador
    assert [o["clOrdId"] for o in client.activas()] == [deshacer["clOrdId"]]
    pend = _pendientes(tmp_path)
    assert [p["tipo"] for p in pend] == ["DESHACER_ACTIVA"]
    assert pend[0]["cl_ord_id"] == deshacer["clOrdId"]
    assert _pnl(tmp_path)[0]["ordenes_deshacer"] == deshacer["clOrdId"]


def test_venta_no_ejecutada_cancela_y_deshace_todo(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(
        mercado_dolarizar_primero, {("DNC3D", "SELL"): 0.0, ("DNC3O", "SELL"): 0.0}
    )
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert client.secuencia() == [
        ("DNC3O", "BUY", 1000),
        ("DNC3D", "SELL", 1000),
        ("DNC3O", "SELL", 1000),      # deshacer todo, en pesos, a precio de compra
    ]
    assert client.orders["CL2"]["status"] == "CANCELLED"
    assert [o["ticker"] for o in client.activas()] == ["DNC3O"]
    assert _pnl(tmp_path) == []       # nada se convirtió: no hay pata 2
    assert _pendientes(tmp_path)[0]["tipo"] == "DESHACER_ACTIVA"


def test_deshacer_rechazado_queda_para_accion_manual(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(
        mercado_dolarizar_primero, {("DNC3D", "SELL"): 0.0, ("DNC3O", "SELL"): "reject"}
    )
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    assert [p["tipo"] for p in _pendientes(tmp_path)] == ["DESHACER_ERROR"]


def test_pesificar_venta_parcial_deshace_en_dolares(armar_executer, mercado_pesificar_primero, tmp_path):
    ex, client, _ = armar_executer(
        mercado_pesificar_primero, {("YMCXO", "SELL"): 500, ("YMCXD", "SELL"): 0.0}
    )
    ex.execute(copy.deepcopy(mercado_pesificar_primero))
    deshacer = client.enviadas[2]
    comprado = client.enviadas[0]["orderQty"]
    assert (deshacer["ticker"], deshacer["side"]) == ("YMCXD", "SELL")     # misma moneda: USD
    assert deshacer["orderQty"] == comprado - 500
    assert deshacer["price"] == pytest.approx(client.enviadas[0]["avgPx"])


# ---------------------- Pata 2 ----------------------
def test_pata2_refresca_market_data(armar_executer, mercado_dolarizar_primero):
    ex, _, dm = armar_executer(mercado_dolarizar_primero)
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    assert dm.llamadas >= 1


def test_pata2_puntas_desaparecen_usa_alternativa(armar_executer, mercado_dolarizar_primero, tmp_path):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2] = inst("YMCXO", tc_dol=1421.0, tc_pes=1416.0)             # la punta original se fue
    fresco.append(inst("PN43O", tc_dol=1422.0, tc_pes=1415.5 * 1.0007))  # cumple 1.0006
    fresco.append(inst("LOC5O", tc_dol=1422.0, tc_pes=1415.5 * 1.0004))  # no cumple
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert client.secuencia()[2:] == [("PN43D", "BUY", 1000), ("PN43O", "SELL", 1000)]
    fila = _pnl(tmp_path)[0]
    assert fila["instrumentos_pata2"] == "PN43O"
    assert fila["estado"] == ESTADO_COMPLETO


def test_pata2_puntas_mejores_sigue_con_el_original(armar_executer, mercado_dolarizar_primero):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2]["prCompraPesos"] += 50                     # bid en pesos mejor que el detectado
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    assert client.secuencia()[2][0] == "YMCXD"


def test_pata2_sin_alternativas_loguea_y_registra(armar_executer, mercado_dolarizar_primero, tmp_path, caplog):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2] = inst("YMCXO", tc_dol=1421.0, tc_pes=1416.0)
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    with caplog.at_level("WARNING", logger="arbitrador_v4"):
        ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert len(client.enviadas) == 2                     # solo la pata 1
    assert "No se pudo ejecutar la segunda parte" in caplog.text
    assert _pnl(tmp_path)[0]["estado"] == ESTADO_SIN_PATA2
    pend = _pendientes(tmp_path)
    assert pend[0]["tipo"] == "CIERRE_INCOMPLETO" and pend[0]["moneda"] == "USD"
    assert float(pend[0]["monto"]) == pytest.approx(1000.0)


def test_pata2_completa_con_varios_instrumentos(armar_executer, mercado_dolarizar_primero, tmp_path):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2]["siVentaDolar"] = 600                      # la original solo cubre 600
    fresco.append(inst("PN43O", tc_dol=1422.0, tc_pes=1415.5 * 1.0010, size=250))
    fresco.append(inst("TSC4O", tc_dol=1422.0, tc_pes=1415.5 * 1.0007))
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    compras_pata2 = [(t, q) for t, s, q in client.secuencia()[2:] if s == "BUY"]
    assert compras_pata2 == [("YMCXD", 600), ("PN43D", 250), ("TSC4D", 150)]
    fila = _pnl(tmp_path)[0]
    assert fila["instrumentos_pata2"] == "YMCXO+PN43O+TSC4O"
    assert fila["estado"] == ESTADO_COMPLETO
    assert float(fila["usd_cerrados"]) == pytest.approx(1000.0)


def test_pata2_no_completa_el_monto(armar_executer, mercado_dolarizar_primero, tmp_path, caplog):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2]["siVentaDolar"] = 600
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    with caplog.at_level("WARNING", logger="arbitrador_v4"):
        ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    assert "no cubrió el monto" in caplog.text
    fila = _pnl(tmp_path)[0]
    assert fila["estado"] == ESTADO_INCOMPLETO
    assert float(fila["usd_sin_cerrar"]) == pytest.approx(400.0)
    assert _pendientes(tmp_path)[0]["tipo"] == "CIERRE_INCOMPLETO"


def test_pata2_compra_parcial_sigue_con_alternativa(armar_executer, mercado_dolarizar_primero, tmp_path):
    mercado = copy.deepcopy(mercado_dolarizar_primero)
    mercado.append(inst("PN43O", tc_dol=1422.0, tc_pes=1415.5 * 1.0007))
    ex, client, _ = armar_executer(mercado, {("YMCXD", "BUY"): 700})
    ex.execute(copy.deepcopy(mercado))

    compras_pata2 = [(t, q) for t, s, q in client.secuencia()[2:] if s == "BUY"]
    assert compras_pata2 == [("YMCXD", 1000), ("PN43D", 300)]
    assert client.activas() == []
    assert _pnl(tmp_path)[0]["estado"] == ESTADO_COMPLETO


def test_pata2_venta_parcial_crea_deshacer(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(
        mercado_dolarizar_primero, {("YMCXO", "SELL"): 800, ("YMCXD", "SELL"): 0.0}
    )
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    deshacer = client.enviadas[-1]
    assert (deshacer["ticker"], deshacer["side"], deshacer["orderQty"]) == ("YMCXD", "SELL", 200)
    assert deshacer["price"] == pytest.approx(100.0)            # precio de compra en USD
    fila = _pnl(tmp_path)[0]
    assert fila["estado"] == ESTADO_INCOMPLETO
    assert float(fila["usd_cerrados"]) == pytest.approx(800.0)
    assert [o["clOrdId"] for o in client.activas()] == [deshacer["clOrdId"]]


def test_al30_se_opera_en_pata2_con_limit(armar_executer, tmp_path):
    from fakes_v4 import al30

    mercado = [al30(), inst("YMCXO", tc_dol=1425.0, tc_pes=1421.0)]
    ex, client, _ = armar_executer(mercado)
    ex.execute(copy.deepcopy(mercado))
    sec = client.secuencia()
    assert sec[0][0] == "YMCXD" and sec[2][0] == "AL30"
    assert all(o["ordType"] == "LIMIT" for o in client.enviadas)


# ---------------------- Órdenes huérfanas ----------------------
def test_ninguna_orden_de_arbitraje_queda_activa(armar_executer, mercado_dolarizar_primero):
    escenarios = [
        {("DNC3O", "BUY"): 0.0},
        {("DNC3O", "BUY"): 0.5},
        {("DNC3D", "SELL"): 0.3, ("DNC3O", "SELL"): 0.0},
        {("YMCXD", "BUY"): 0.2},
        {("YMCXO", "SELL"): 0.0, ("YMCXD", "SELL"): 0.0},
    ]
    for comportamiento in escenarios:
        ex, client, _ = armar_executer(mercado_dolarizar_primero, comportamiento)
        ex.execute(copy.deepcopy(mercado_dolarizar_primero))
        activas_no_deshacer = [
            o for o in client.activas()
            if o["clOrdId"] not in ex.ordenes_deshacer
        ]
        assert activas_no_deshacer == [], comportamiento
        assert ex.ordenes == {}


def test_cancelacion_no_confirmada_se_reporta(armar_executer, mercado_dolarizar_primero, tmp_path):
    ex, client, _ = armar_executer(mercado_dolarizar_primero, {("DNC3O", "BUY"): 0.0})
    client.no_cancelables.add("DNC3O")
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))

    pend = _pendientes(tmp_path)
    assert [p["tipo"] for p in pend] == ["ORDEN_NO_CONFIRMADA"]
    assert "CL1" in ex.ordenes                   # se sigue intentando
    # En el siguiente intento (p.ej. al cerrar el bot) ya no se duplica el reporte
    ex.cerrar()
    assert len(_pendientes(tmp_path)) == 1


def test_excepcion_en_medio_verifica_huerfanas(armar_executer, mercado_dolarizar_primero):
    # La venta de la pata 1 queda viva y la primera cancelación lanza una excepción inesperada
    ex, client, _ = armar_executer(mercado_dolarizar_primero, {("DNC3D", "SELL"): 0.0})
    cancel_original = client.cancel_order
    llamadas = []

    def cancel_que_falla_una_vez(cl_ord_id, proprietary):
        llamadas.append(cl_ord_id)
        if len(llamadas) == 1:
            raise RuntimeError("caída de red")
        return cancel_original(cl_ord_id, proprietary)

    client.cancel_order = cancel_que_falla_una_vez
    with pytest.raises(RuntimeError):
        ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    # El bloque finally verificó huérfanas y canceló la venta que había quedado activa
    assert llamadas == ["CL2", "CL2"]
    assert client.activas() == []
    assert ex.ordenes == {}


# ---------------------- Mínimos y máximos por instrumento ----------------------
def _con_minimo(mercado, indice, minimo):
    mercado = copy.deepcopy(mercado)
    mercado[indice]["min_quant"] = minimo
    return mercado


def test_no_opera_si_el_primer_instrumento_no_llega_al_minimo(armar_executer, mercado_dolarizar_primero, tmp_path):
    mercado = _con_minimo(mercado_dolarizar_primero, 1, 1500)   # DNC3O pide 1500 y hay 1000
    ex, client, _ = armar_executer(mercado)
    ex.execute(copy.deepcopy(mercado))
    assert client.enviadas == []
    assert _pnl(tmp_path) == []


def test_no_opera_si_el_cierre_no_llega_al_minimo(armar_executer, mercado_dolarizar_primero):
    mercado = _con_minimo(mercado_dolarizar_primero, 2, 1500)   # YMCXO no se cubre con USD 1000
    ex, client, _ = armar_executer(mercado)
    ex.execute(copy.deepcopy(mercado))
    assert client.enviadas == []


def test_respeta_max_quant_del_primer_instrumento(armar_executer, mercado_dolarizar_primero):
    mercado = copy.deepcopy(mercado_dolarizar_primero)
    mercado[1]["max_quant"] = 400
    ex, client, _ = armar_executer(mercado)
    ex.execute(copy.deepcopy(mercado))
    assert client.secuencia()[0] == ("DNC3O", "BUY", 400)
    assert client.secuencia()[2] == ("YMCXD", "BUY", 400)


def test_pata2_saltea_alternativa_cuyo_minimo_supera_el_restante(armar_executer, mercado_dolarizar_primero, tmp_path):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2] = inst("YMCXO", tc_dol=1421.0, tc_pes=1416.0)
    fresco.append(inst("PN43O", tc_dol=1422.0, tc_pes=1415.5 * 1.0015, min_q=2000))
    fresco.append(inst("TSC4O", tc_dol=1422.0, tc_pes=1415.5 * 1.0007))
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    assert client.secuencia()[2:] == [("TSC4D", "BUY", 1000), ("TSC4O", "SELL", 1000)]
    assert _pnl(tmp_path)[0]["instrumentos_pata2"] == "TSC4O"


def test_pata2_respeta_max_quant_y_completa_con_otra(armar_executer, mercado_dolarizar_primero):
    fresco = copy.deepcopy(mercado_dolarizar_primero)
    fresco[2]["max_quant"] = 700
    fresco.append(inst("TSC4O", tc_dol=1422.0, tc_pes=1415.5 * 1.0007))
    ex, client, dm = armar_executer(mercado_dolarizar_primero)
    dm.cola = [fresco]
    ex.execute(copy.deepcopy(mercado_dolarizar_primero))
    compras = [(t, q) for t, s, q in client.secuencia()[2:] if s == "BUY"]
    assert compras == [("YMCXD", 700), ("TSC4D", 300)]


def test_compra_parcial_bajo_minimo_no_se_opera(armar_executer, mercado_dolarizar_primero, tmp_path, caplog):
    mercado = _con_minimo(mercado_dolarizar_primero, 1, 500)
    ex, client, _ = armar_executer(mercado, {("DNC3O", "BUY"): 300})
    with caplog.at_level("ERROR", logger="arbitrador_v4"):
        ex.execute(copy.deepcopy(mercado))
    assert client.secuencia() == [("DNC3O", "BUY", 1000)]
    assert client.orders["CL1"]["status"] == "CANCELLED"
    assert "por debajo del mínimo" in caplog.text
    assert _pnl(tmp_path) == [] and _pendientes(tmp_path) == []
    assert client.activas() == []


def test_remanente_de_venta_bajo_minimo_no_se_deshace(armar_executer, mercado_dolarizar_primero, tmp_path, caplog):
    mercado = _con_minimo(mercado_dolarizar_primero, 1, 500)
    ex, client, _ = armar_executer(mercado, {("DNC3D", "SELL"): 800})
    with caplog.at_level("ERROR", logger="arbitrador_v4"):
        ex.execute(copy.deepcopy(mercado))
    assert client.secuencia() == [
        ("DNC3O", "BUY", 1000),
        ("DNC3D", "SELL", 1000),
        ("YMCXD", "BUY", 800),
        ("YMCXO", "SELL", 800),
    ]
    assert "No se envía la orden de deshacer" in caplog.text
    assert _pendientes(tmp_path) == []
    assert client.activas() == []


def test_remanente_igual_al_minimo_si_se_deshace(armar_executer, mercado_dolarizar_primero):
    mercado = _con_minimo(mercado_dolarizar_primero, 1, 200)
    ex, client, _ = armar_executer(mercado, {("DNC3D", "SELL"): 800, ("DNC3O", "SELL"): 0.0})
    ex.execute(copy.deepcopy(mercado))
    assert client.secuencia()[2] == ("DNC3O", "SELL", 200)


def test_pata2_compra_parcial_bajo_minimo_sigue_con_alternativa(armar_executer, mercado_dolarizar_primero):
    mercado = _con_minimo(mercado_dolarizar_primero, 2, 500)
    mercado.append(inst("PN43O", tc_dol=1422.0, tc_pes=1415.5 * 1.0007))
    ex, client, _ = armar_executer(mercado, {("YMCXD", "BUY"): 300})
    ex.execute(copy.deepcopy(mercado))
    sec = client.secuencia()[2:]
    assert sec == [("YMCXD", "BUY", 1000), ("PN43D", "BUY", 700), ("PN43O", "SELL", 700)]
    assert client.activas() == []


def test_punta_bajo_minimo_se_ignora_y_loguea_una_vez_con_motivo(armar_executer, mercado_dolarizar_primero, caplog):
    mercado = copy.deepcopy(mercado_dolarizar_primero)
    mercado[1]["min_quant"] = 1000
    mercado[1]["siVentaPesos"] = 300
    ex, client, _ = armar_executer(mercado)
    with caplog.at_level("INFO", logger="arbitrador_v4"):
        ex.execute(copy.deepcopy(mercado))
        ex.execute(copy.deepcopy(mercado))
    assert client.enviadas == []
    assert caplog.text.count("no alcanzan el mínimo de mercado") == 1
    assert "min_quant=1000" in caplog.text and "capacidad 300 VN" in caplog.text


def test_pata1_minimo_1000_se_cierra_con_varios_instrumentos(armar_executer, tmp_path):
    from fakes_v4 import al30

    # VSCVO (min/max 1000) dolariza; RC7CO (max 400) y TSC4O (alternativa) completan el cierre
    mercado = [
        al30(),
        inst("VSCVO", tc_dol=1415.0, tc_pes=1411.0, min_q=1000, max_q=1000, size=5000),
        inst("RC7CO", tc_dol=1422.0, tc_pes=1418.5, max_q=400, size=5000),
        inst("TSC4O", tc_dol=1422.0, tc_pes=1415.0 * 1.0007, max_q=1000, size=5000),
    ]
    ex, client, _ = armar_executer(mercado)
    ex.execute(copy.deepcopy(mercado))
    compras = [(t, q) for t, s, q in client.secuencia() if s == "BUY"]
    assert compras == [("VSCVO", 1000), ("RC7CD", 400), ("TSC4D", 600)]
    fila = _pnl(tmp_path)[0]
    assert fila["estado"] == ESTADO_COMPLETO and fila["instrumentos_pata2"] == "RC7CO+TSC4O"
