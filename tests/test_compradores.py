import pytest

from comun.cotizaciones import LibroCotizaciones
from comun.feed_matriz import FeedMatriz
from comun.instrumentos import crear_instrumento
from compradores.comprador_D_CI import CompradorDolarCI
from compradores.comprador_pesos import CompradorPesos, pesos_a_usd
from fakes import FakeClient, registro


def armar(cls, instrumentos, **kwargs):
    client, libro = FakeClient(), LibroCotizaciones()
    comprador = cls(client, "acc", instrumentos, libro, **kwargs)
    feed = FeedMatriz("url", comprador.suscripciones(), libro, comprador.al_actualizar)
    return comprador, client, feed.procesar_registros


# ====================== comprador en pesos ======================
# Referencia AL30: 1200*60 / 60 = 1200 pesos por dólar al pesificar
REF_24 = [registro("AL30", "24hs", 72000, 72100), registro("AL30D", "24hs", 59.9, 60)]
REF_CI = [registro("AL30", "CI", 72000, 72100), registro("AL30D", "CI", 59.9, 60)]
# GD30D: bid 50 → dolarizar a 59000/50 = 1180 < 1200/1.002
DOLAR_GD30 = registro("GD30D", "24hs", 50, 50.5, 300, 300, minimo="", cierre=50.2)


def test_pesos_a_usd_usa_el_minimo_entre_bid_cierre_y_minimo():
    from comun.cotizaciones import Cotizacion

    pesos = Cotizacion("X", "24hs", pr_venta=1000)
    assert pesos_a_usd(pesos, Cotizacion("XD", "24hs", pr_compra=10, cierre_anterior=8)) == 125
    assert pesos_a_usd(pesos, Cotizacion("XD", "24hs", pr_compra=10, cierre_anterior=8, minimo=5)) == 200
    assert pesos_a_usd(pesos, Cotizacion("XD", "24hs", pr_compra=10, cierre_anterior=8, minimo=0)) == 125
    assert pesos_a_usd(pesos, Cotizacion("XD", "24hs", pr_compra=10)) is None


@pytest.mark.parametrize("plazo, ref", [("24hs", REF_24), ("CI", REF_CI)])
def test_compra_en_pesos_en_el_plazo_elegido(plazo, ref):
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)], plazo=plazo)
    procesar(ref + [DOLAR_GD30, registro("GD30", plazo, 58900, 59000, 500, 200)])
    assert len(client.ordenes) == 1
    orden = client.ordenes[0]
    assert orden["symbol"] == f"MERV - XMEV - GD30 - {plazo}"
    assert (orden["side"], orden["quantity"], orden["price"]) == ("BUY", 200, 59000.0)


def test_no_evalua_ante_actualizacion_del_dolar():
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D")])
    procesar(REF_24 + [registro("GD30", "24hs", 58900, 59000, 500, 200), DOLAR_GD30])
    assert client.ordenes == []  # el pesos llegó antes que el dólar: no había datos para evaluar


def test_no_compra_sin_ventaja_suficiente():
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D")])
    procesar(REF_24 + [DOLAR_GD30, registro("GD30", "24hs", 59900, 59990, 500, 200)])  # 1199.8 * 1.002 > 1200
    assert client.ordenes == []


def test_no_compra_sin_referencia_al30():
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D")])
    procesar([DOLAR_GD30, registro("GD30", "24hs", 58900, 59000, 500, 200)])
    assert client.ordenes == []


def test_referencia_ci_no_se_mezcla_con_24hs():
    """AL30D en 24hs no debe pisar la referencia CI (bug del comprador_CI original)."""
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D")], plazo="CI")
    procesar(REF_CI + [registro("AL30D", "24hs", 80, 80.1), DOLAR_GD30, registro("GD30", "CI", 58900, 59000)])
    assert len(client.ordenes) == 1


@pytest.mark.parametrize("si_pesos, min_quant", [("", 1), (5, 10)])
def test_respeta_tamanio_y_min_quant(si_pesos, min_quant):
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D", min_quant, 400)])
    procesar(REF_24 + [DOLAR_GD30, registro("GD30", "24hs", 58900, 59000, 500, si_pesos)])
    assert client.ordenes == []


def test_respeta_max_quant():
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 50)])
    procesar(REF_24 + [DOLAR_GD30, registro("GD30", "24hs", 58900, 59000, 500, 200)])
    assert client.ordenes[0]["quantity"] == 50


def test_al30_no_se_compra_contra_si_mismo():
    _, client, procesar = armar(CompradorPesos, [crear_instrumento("AL30", "AL30D")])
    procesar(REF_24 + [registro("AL30", "24hs", 1, 2)])
    assert client.ordenes == []


# ====================== comprador de dólares CI ======================
def test_compra_dolar_ci_y_tickers_con_ci_en_el_nombre():
    """YMCID contiene 'CI': su cotización 24hs debe guardarse como 24hs (bug del original)."""
    _, client, procesar = armar(CompradorDolarCI, [crear_instrumento("YMCIO", "YMCID", 1, 400)])
    procesar([
        registro("YMCID", "24hs", 100, 100.5, 300, 300, cierre=100.2),
        registro("YMCID", "CI", 99, 99.5, 300, 250),
    ])
    assert len(client.ordenes) == 1
    orden = client.ordenes[0]
    assert orden["symbol"] == "MERV - XMEV - YMCID - CI"
    assert (orden["quantity"], orden["price"]) == (250, 99.5)


def test_dolar_ci_sin_cierre_anterior_no_compra():
    _, client, procesar = armar(CompradorDolarCI, [crear_instrumento("GD30", "GD30D")])
    procesar([registro("GD30D", "24hs", 100, 100.5, 300, 300), registro("GD30D", "CI", 99, 99.5, 300, 250)])
    assert client.ordenes == []


def test_dolar_ci_sin_ventaja_no_compra():
    _, client, procesar = armar(CompradorDolarCI, [crear_instrumento("GD30", "GD30D")])
    procesar([
        registro("GD30D", "24hs", 100, 100.5, 300, 300, cierre=100.2),
        registro("GD30D", "CI", 99, 99.9, 300, 250),  # 99.9 * 1.002 > 100
    ])
    assert client.ordenes == []


# ====================== reglas de plazos (especificación) ======================
# Referencia AL30 desfavorable: 59000/60 = 983 → nunca conviene dolarizar contra ella
REF_24_MALA = [registro("AL30", "24hs", 59000, 59100), registro("AL30D", "24hs", 59.9, 60)]
REF_CI_MALA = [registro("AL30", "CI", 59000, 59100), registro("AL30D", "CI", 59.9, 60)]
# Dólar del instrumento en CI favorable, que NO debe usarse en el comprador en pesos
DOLAR_GD30_CI = registro("GD30D", "CI", 50, 50.5, 300, 300, cierre=50.2)


def test_plazo_24hs_compara_solo_contra_al30_24hs():
    inst = [crear_instrumento("GD30", "GD30D", 1, 400)]
    # AL30 CI favorable pero AL30 24hs desfavorable → no compra
    _, client, procesar = armar(CompradorPesos, inst, plazo="24hs")
    procesar(REF_CI + REF_24_MALA + [DOLAR_GD30, registro("GD30", "24hs", 58900, 59000)])
    assert client.ordenes == []
    # AL30 24hs favorable aunque AL30 CI sea desfavorable → compra
    _, client, procesar = armar(CompradorPesos, inst, plazo="24hs")
    procesar(REF_CI_MALA + REF_24 + [DOLAR_GD30, registro("GD30", "24hs", 58900, 59000)])
    assert [o["symbol"] for o in client.ordenes] == ["MERV - XMEV - GD30 - 24hs"]


def test_plazo_ci_compara_contra_al30_ci():
    inst = [crear_instrumento("GD30", "GD30D", 1, 400)]
    # AL30 24hs favorable pero AL30 CI desfavorable → no compra
    _, client, procesar = armar(CompradorPesos, inst, plazo="CI")
    procesar(REF_24 + REF_CI_MALA + [DOLAR_GD30, registro("GD30", "CI", 58900, 59000)])
    assert client.ordenes == []
    # AL30 CI favorable aunque AL30 24hs sea desfavorable → compra
    _, client, procesar = armar(CompradorPesos, inst, plazo="CI")
    procesar(REF_24_MALA + REF_CI + [DOLAR_GD30, registro("GD30", "CI", 58900, 59000)])
    assert [o["symbol"] for o in client.ordenes] == ["MERV - XMEV - GD30 - CI"]


def test_plazo_ci_ratio_usa_pesos_ci_y_dolar_24hs():
    inst = [crear_instrumento("GD30", "GD30D", 1, 400)]
    # Sin dólar 24hs (solo CI) → no hay ratio, no compra
    _, client, procesar = armar(CompradorPesos, inst, plazo="CI")
    procesar(REF_CI + [DOLAR_GD30_CI, registro("GD30", "CI", 58900, 59000)])
    assert client.ordenes == []
    # Pesos 24hs favorable no dispara nada en modo CI
    _, client, procesar = armar(CompradorPesos, inst, plazo="CI")
    procesar(REF_CI + [DOLAR_GD30, registro("GD30", "24hs", 58900, 59000)])
    assert client.ordenes == []


def test_dolar_ci_compara_contra_su_propio_24hs():
    inst = [crear_instrumento("GD30", "GD30D", 1, 400), crear_instrumento("AL30", "AL30D", 1, 400)]
    _, client, procesar = armar(CompradorDolarCI, inst)
    procesar([
        registro("AL30D", "24hs", 100, 100.5, 300, 300, cierre=100.2),  # 24hs de OTRA especie, favorable
        registro("GD30D", "24hs", 99, 99.5, 300, 300, cierre=99.2),
        registro("GD30D", "CI", 99, 99.5, 300, 250),  # 99.5 * 1.002 > 99 → sin ventaja contra su 24hs
    ])
    assert client.ordenes == []


def test_dolar_ci_solo_evalua_ante_actualizacion_ci():
    _, client, procesar = armar(CompradorDolarCI, [crear_instrumento("GD30", "GD30D", 1, 400)])
    procesar([
        registro("GD30D", "CI", 99, 99.5, 300, 250),
        registro("GD30D", "24hs", 100, 100.5, 300, 300, cierre=100.2),  # llega después: no dispara
    ])
    assert client.ordenes == []


# ====================== tope de 5 × max_quant cada 10 minutos ======================
class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


PESOS_GD30 = registro("GD30", "24hs", 58900, 59000, 5000, 5000)
DOLAR_GD30_GRANDE = registro("GD30D", "24hs", 50, 50.5, 5000, 5000, cierre=50.2)


def armar_con_reloj(cls, instrumentos, **kwargs):
    reloj = Reloj()
    comprador, client, procesar = armar(cls, instrumentos, reloj=reloj, **kwargs)
    return comprador, client, procesar, reloj


def test_tope_cinco_veces_max_quant_por_ventana():
    _, client, procesar, _ = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)])
    procesar(REF_24 + [DOLAR_GD30_GRANDE] + [PESOS_GD30] * 8)
    assert [o["quantity"] for o in client.ordenes] == [400] * 5  # 2000 = 5 × 400; el resto se bloquea


def test_tope_recorta_la_ultima_orden_al_cupo_restante():
    _, client, procesar, _ = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)])
    pesos_300 = registro("GD30", "24hs", 58900, 59000, 5000, 300)
    procesar(REF_24 + [DOLAR_GD30_GRANDE] + [pesos_300] * 8)
    assert [o["quantity"] for o in client.ordenes] == [300] * 6 + [200]  # 1800 + 200 = 2000


def test_tope_es_ventana_movil_de_10_minutos():
    _, client, procesar, reloj = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)])
    procesar(REF_24 + [DOLAR_GD30_GRANDE])
    for _ in range(3):
        procesar([PESOS_GD30])
    reloj.t += 300  # 5 min después: 2 compras más completan el tope
    procesar([PESOS_GD30] * 3)
    assert len(client.ordenes) == 5
    reloj.t += 300  # 10 min después de las 3 primeras: se libera su cupo (1200), no el de las 2 últimas
    procesar([PESOS_GD30] * 5)
    assert len(client.ordenes) == 8
    reloj.t += 300  # vencen también las 2 compras del minuto 5 y las 3 nuevas siguen en la ventana
    procesar([PESOS_GD30] * 5)
    assert len(client.ordenes) == 10


def test_tope_bloquea_si_el_cupo_no_llega_a_min_quant():
    _, client, procesar, _ = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 350, 400)])
    procesar(REF_24 + [DOLAR_GD30_GRANDE] + [PESOS_GD30] * 6)
    assert [o["quantity"] for o in client.ordenes] == [400] * 5  # quedan 0 → no hay orden de 0


def test_envio_rechazado_no_consume_cupo():
    comprador, client, procesar, _ = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)])
    client.error_envio = "rechazada"
    procesar(REF_24 + [DOLAR_GD30_GRANDE] + [PESOS_GD30] * 3)
    assert comprador.comprado_en_ventana(("GD30", "24hs")) == 0
    client.error_envio = None
    procesar([PESOS_GD30] * 6)
    assert len(client.ordenes) == 3 + 5


def test_orden_no_filled_si_consume_cupo():
    comprador, client, procesar, _ = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)])
    client.estado = "CANCELLED"
    procesar(REF_24 + [DOLAR_GD30_GRANDE] + [PESOS_GD30] * 6)
    assert len(client.ordenes) == 5 and comprador.comprado_en_ventana(("GD30", "24hs")) == 2000


def test_tope_es_por_instrumento():
    inst = [crear_instrumento("GD30", "GD30D", 1, 400), crear_instrumento("TX26", "TX26D", 1, 100)]
    _, client, procesar, _ = armar_con_reloj(CompradorPesos, inst)
    tx_dolar = registro("TX26D", "24hs", 50, 50.5, 5000, 5000, cierre=50.2)
    tx_pesos = registro("TX26", "24hs", 58900, 59000, 5000, 5000)
    procesar(REF_24 + [DOLAR_GD30_GRANDE, tx_dolar] + [PESOS_GD30, tx_pesos] * 7)
    cantidades = {}
    for o in client.ordenes:
        cantidades[o["symbol"]] = cantidades.get(o["symbol"], 0) + o["quantity"]
    assert cantidades == {"MERV - XMEV - GD30 - 24hs": 2000, "MERV - XMEV - TX26 - 24hs": 500}


def test_tope_dolar_ci():
    _, client, procesar, _ = armar_con_reloj(CompradorDolarCI, [crear_instrumento("GD30", "GD30D", 1, 100)])
    ci = registro("GD30D", "CI", 99, 99.5, 5000, 5000)
    procesar([registro("GD30D", "24hs", 100, 100.5, 5000, 5000, cierre=100.2)] + [ci] * 7)
    assert [o["quantity"] for o in client.ordenes] == [100] * 5


def test_orden_trabada_consume_cupo_y_se_reintenta_hasta_el_tope():
    # Orden que queda en PENDING_NEW y no se cancela: el cupo se consume igual (cuenta lo enviado)
    comprador, client, procesar, _ = armar_con_reloj(CompradorPesos, [crear_instrumento("GD30", "GD30D", 1, 400)])
    client.estado = "PENDING_NEW"
    procesar(REF_24 + [DOLAR_GD30_GRANDE] + [PESOS_GD30] * 6)
    assert len(client.ordenes) == 5 and len(client.cancelaciones) == 5
    assert comprador.comprado_en_ventana(("GD30", "24hs")) == 2000
