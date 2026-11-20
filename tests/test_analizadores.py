import math

from analizadores import analizador
from comun.cotizaciones import Cotizacion, LibroCotizaciones
from comun.instrumentos import crear_instrumento

INSTRUMENTOS = [crear_instrumento("AL30", "AL30D"), crear_instrumento("GD30", "GD30D")]


def test_suscripciones_cubren_ambos_plazos():
    sus = analizador.suscripciones(INSTRUMENTOS)
    assert sus[:2] == [("AL30", "24hs"), ("GD30", "24hs")]
    assert set(sus) == {(t, p) for i in INSTRUMENTOS for t in (i["ticker"], i["tickerD"]) for p in ("24hs", "CI")}


def test_armar_dataframe_ubica_cada_cotizacion_en_su_columna():
    libro = LibroCotizaciones()
    libro.actualizar(Cotizacion("AL30", "CI", 70000, 70100))
    libro.actualizar(Cotizacion("AL30D", "24hs", 59.9, 60))
    df = analizador.armar_dataframe(INSTRUMENTOS, libro)
    fila = df.iloc[0]
    assert (fila.prCompraPesosCI, fila.prVentaPesosCI) == (70000, 70100)
    assert (fila.prCompraDolar, fila.prVentaDolar) == (59.9, 60)
    assert math.isnan(fila.prCompraPesos) and math.isnan(df.iloc[1].prVentaDolarCI)


def test_analizador_sin_datos_no_falla(capsys):
    df = analizador.armar_dataframe(INSTRUMENTOS, LibroCotizaciones())
    analizador.AnalizadorArbitraje(df, ["AL30"]).execute()
    salida = capsys.readouterr().out
    assert "NO HAY ARBITRAJE PRINCIPAL" in salida and "NO HAY ARBITRAJE EN CI" in salida


def test_analizador_detecta_arbitraje_principal(capsys):
    libro = LibroCotizaciones()
    for c in [
        Cotizacion("AL30", "24hs", 72000, 72100),
        Cotizacion("AL30D", "24hs", 59.9, 60),  # pesificar: 72000/60 = 1200
        Cotizacion("GD30", "24hs", 58000, 59000),
        Cotizacion("GD30D", "24hs", 50, 50.5),  # dolarizar: 59000/50 = 1180
    ]:
        libro.actualizar(c)
    analizador.AnalizadorArbitraje(analizador.armar_dataframe(INSTRUMENTOS, libro), []).execute()
    salida = capsys.readouterr().out
    assert "USD 24hs" in salida and "NO HAY ARBITRAJE PRINCIPAL" not in salida
