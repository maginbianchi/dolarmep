"""Tests de la lógica pura: TC, consenso, detección de pares, prioridad, cierre."""

import pytest

from estrategia import (
    CLOSE_RATIO,
    DOLARIZAR,
    PESIFICAR,
    buscar_oportunidades,
    calcular_consenso,
    calcular_ratios,
    candidatos_cierre,
    capacidad_nominales,
    determinar_primera_pata,
    dimensionar_oportunidad,
    nominales_para_monto,
    puntas_vigentes,
    ratio_cierre,
)
from fakes_v4 import al30, inst


def _preparar(mercado):
    calcular_ratios(mercado)
    return calcular_consenso(mercado)


# ---------------------- TC y consenso ----------------------
def test_calcula_tc_como_example_v1():
    i = inst("X", tc_dol=1420.0, tc_pes=1410.0)
    calcular_ratios([i])
    assert i["pesos_a_USD"] == pytest.approx(i["prVentaPesos"] / i["prCompraDolar"])
    assert i["USD_a_pesos"] == pytest.approx(i["prCompraPesos"] / i["prVentaDolar"])


def test_tc_invalido_o_sin_puntas_es_none():
    i = inst("X", 1420.0, 1410.0)
    i["prCompraDolar"] = None
    i["prVentaDolar"] = 0
    calcular_ratios([i])
    assert i["pesos_a_USD"] is None and i["USD_a_pesos"] is None


def test_consenso_es_promedio_de_los_dos_mep_de_al30():
    mercado = [al30(), inst("X", 1420.0, 1410.0)]
    assert _preparar(mercado) == pytest.approx((1418.0 + 1415.8) / 2)


def test_consenso_none_sin_al30():
    assert _preparar([inst("X", 1420.0, 1410.0)]) is None


# ---------------------- Detección de oportunidades ----------------------
def test_detecta_pares_entre_todos_los_instrumentos_no_solo_al30(mercado_dolarizar_primero):
    consenso = _preparar(mercado_dolarizar_primero)
    ops = buscar_oportunidades(mercado_dolarizar_primero, consenso)
    # Ningún instrumento arbitra contra AL30: el único par es DNC3O → YMCXO
    assert [op.clave for op in ops] == [("DNC3O", "YMCXO")]


def test_respeta_ratio_minimo_1_0008():
    base = 1416.0
    mercado = [al30(), inst("A", base, base - 3)]
    # Justo por debajo del umbral: no hay arbitraje
    mercado.append(inst("B", base * 1.0008 + 5, base * 1.0007))
    consenso = _preparar(mercado)
    assert [op.clave for op in buscar_oportunidades(mercado, consenso)] == []
    # Por encima del umbral: hay arbitraje
    mercado[2] = inst("B", base * 1.0008 + 5, base * 1.0009)
    consenso = _preparar(mercado)
    assert ("A", "B") in [op.clave for op in buscar_oportunidades(mercado, consenso)]


def test_comienza_por_el_mas_desviado(mercado_dolarizar_primero, mercado_pesificar_primero):
    op = buscar_oportunidades(mercado_dolarizar_primero, _preparar(mercado_dolarizar_primero))[0]
    assert op.modo_primera_pata == DOLARIZAR and op.primer_instrumento["ticker"] == "DNC3O"

    ops = buscar_oportunidades(mercado_pesificar_primero, _preparar(mercado_pesificar_primero))
    assert ops[0].clave == ("DNC3O", "YMCXO")
    assert ops[0].modo_primera_pata == PESIFICAR
    assert ops[0].primer_instrumento["ticker"] == "YMCXO"


def test_si_uno_es_al30_comienza_por_el_otro():
    a = {"ticker": "AL30"}
    b = {"ticker": "YMCXO"}
    # AL30 más desviado igual se comienza por el otro instrumento
    assert determinar_primera_pata(a, b, desv_dol=0.01, desv_pes=0.0001) == PESIFICAR
    assert determinar_primera_pata(b, a, desv_dol=0.0001, desv_pes=0.01) == DOLARIZAR


def test_al30_nunca_es_la_primera_pata():
    mercado = [al30(), inst("YMCXO", 1425.0, 1421.0), inst("DNC3O", 1413.0, 1410.0)]
    ops = buscar_oportunidades(mercado, _preparar(mercado))
    assert ops, "debería haber oportunidades contra AL30"
    for op in ops:
        assert op.primer_instrumento["ticker"] != "AL30"


def test_prioriza_la_oportunidad_de_mayor_desviacion():
    mercado = [
        al30(),
        inst("POCO", 1415.9, 1412.0),     # dolariza levemente barato
        inst("MUCHO", 1410.0, 1406.0),    # dolariza muy barato
        inst("CARO", 1424.0, 1419.0),     # pesifica caro
    ]
    ops = buscar_oportunidades(mercado, _preparar(mercado))
    assert ops[0].primer_instrumento["ticker"] == "MUCHO"
    desvios = [op.desviacion_primera_pata for op in ops]
    assert desvios == sorted(desvios, reverse=True)


# ---------------------- Dimensionamiento ----------------------
def test_capacidad_limitada_por_puntas_y_max_quant():
    i = inst("X", 1420.0, 1410.0, size=800, max_q=500)
    assert capacidad_nominales(i, DOLARIZAR) == 500
    i["siCompraDolar"] = 120
    assert capacidad_nominales(i, DOLARIZAR) == 120
    i["siVentaPesos"] = None
    assert capacidad_nominales(i, DOLARIZAR) == 0


def test_nominales_para_monto_cotizacion_cada_100():
    assert nominales_para_monto(1000.0, 100.0) == 1000   # USD 1000 a 100 c/100 VN
    assert nominales_para_monto(999.99, 100.0) == 999
    assert nominales_para_monto(100.0, None) == 0


def test_no_limita_por_la_capacidad_del_segundo_instrumento(mercado_dolarizar_primero):
    consenso = _preparar(mercado_dolarizar_primero)
    # YMCXO solo absorbe 300 nominales: la pata 1 igual abre su capacidad completa (1000) y
    # la pata 2 se completa con alternativas
    mercado_dolarizar_primero[2]["siVentaDolar"] = 300
    op = buscar_oportunidades(mercado_dolarizar_primero, consenso)[0]
    assert dimensionar_oportunidad(op) == 1000


def test_minimo_1000_contra_cierre_con_maximo_400():
    # Caso real: VSCVO (min 1000) dolariza, RC7CO (max 400) pesifica
    mercado = [
        al30(),
        inst("VSCVO", tc_dol=1415.0, tc_pes=1411.0, min_q=1000, max_q=1000, size=5000),
        inst("RC7CO", tc_dol=1422.0, tc_pes=1418.5, max_q=400, size=5000),
    ]
    op = buscar_oportunidades(mercado, _preparar(mercado))[0]
    assert dimensionar_oportunidad(op) == 1000


def test_dimensiona_cero_si_no_alcanza_min_quant(mercado_dolarizar_primero):
    consenso = _preparar(mercado_dolarizar_primero)
    mercado_dolarizar_primero[1]["min_quant"] = 2000
    op = buscar_oportunidades(mercado_dolarizar_primero, consenso)[0]
    assert dimensionar_oportunidad(op) == 0


# ---------------------- Cierre ----------------------
def test_puntas_vigentes_precio_igual_o_mejor():
    orig = inst("X", 1420.0, 1410.0)
    mejor = dict(orig, prVentaPesos=orig["prVentaPesos"] - 10, prCompraDolar=orig["prCompraDolar"] + 0.1)
    peor = dict(orig, prVentaPesos=orig["prVentaPesos"] + 10)
    sin_size = dict(orig, siCompraDolar=0)
    assert puntas_vigentes(orig, dict(orig), DOLARIZAR)
    assert puntas_vigentes(orig, mejor, DOLARIZAR)
    assert not puntas_vigentes(orig, peor, DOLARIZAR)
    assert not puntas_vigentes(orig, sin_size, DOLARIZAR)
    assert not puntas_vigentes(orig, None, DOLARIZAR)


def test_ratio_cierre_contra_tc_real_de_pata1():
    i = inst("X", tc_dol=1410.0, tc_pes=1420.0)
    calcular_ratios([i])
    # Pata 1 dolarizó a 1415 → cierro pesificando a 1420
    assert ratio_cierre(PESIFICAR, 1415.0, i) == pytest.approx(1420.0 / 1415.0)
    # Pata 1 pesificó a 1415 → cierro dolarizando a 1410
    assert ratio_cierre(DOLARIZAR, 1415.0, i) == pytest.approx(1415.0 / 1410.0)


def test_candidatos_cierre_filtran_1_0006_y_ordenan():
    tc1 = 1415.0
    mercado = [
        inst("OK1", 1430.0, tc1 * 1.0007),
        inst("OK2", 1430.0, tc1 * 1.0010),
        inst("NO", 1430.0, tc1 * 1.0005),
        inst("EXCL", 1430.0, tc1 * 1.0020),
    ]
    calcular_ratios(mercado)
    cands = candidatos_cierre(mercado, PESIFICAR, tc1, excluir={"EXCL"}, ratio_minimo=CLOSE_RATIO)
    assert [c[0]["ticker"] for c in cands] == ["OK2", "OK1"]
