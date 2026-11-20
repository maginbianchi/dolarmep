"""
Lógica pura de la estrategia de arbitraje MEP (sin I/O, sin red).

Conceptos:
    - DOLARIZAR un instrumento: comprarlo en pesos (punta vendedora en pesos) y venderlo en
      dólares (punta compradora en USD). Entran pesos, salen dólares.
        TC de dolarización = prVentaPesos / prCompraDolar   (ARS pagados por cada USD obtenido)
    - PESIFICAR un instrumento: comprarlo en dólares (punta vendedora en USD) y venderlo en
      pesos (punta compradora en pesos). Entran dólares, salen pesos.
        TC de pesificación = prCompraPesos / prVentaDolar   (ARS recibidos por cada USD entregado)

    Existe un arbitraje entre A (dolariza) y B (pesifica) cuando:
        TC_pesificacion(B) > TC_dolarizacion(A) * ARBITER_RATIO

    Se consideran TODOS los pares de instrumentos entre sí, no solo contra AL30.

    El consenso de mercado es el promedio de los dos MEP del AL30 (dolarización y pesificación).
    La pata que se ejecuta primero es la del instrumento con mayor desviación (en valor absoluto)
    respecto del consenso: es la punta "fuera de mercado" con más probabilidad de que otro
    participante la tome. Si uno de los instrumentos es AL30, siempre se comienza por el otro.

Unidades:
    Los bonos y ONs cotizan cada 100 nominales (PRICE_FACTOR). Una cantidad `q` de nominales a
    precio `p` equivale a un monto de q * p / 100 en la moneda de la especie.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# ====================== CONSTANTES DE ESTRATEGIA ======================
ARBITER_RATIO = 1.0008      # Ratio mínimo para disparar un arbitraje
CLOSE_RATIO = 1.0006        # Ratio mínimo para cerrar la pata 2 con un instrumento alternativo
CONSENSO_TICKER = "AL30"    # Instrumento que define el consenso de mercado
PRICE_FACTOR = 100          # Las cotizaciones son por cada 100 nominales

DOLARIZAR = "DOLARIZAR"
PESIFICAR = "PESIFICAR"


# ====================== PRECIOS Y PUNTAS POR MODO ======================
def opuesto(modo: str) -> str:
    """Devuelve el modo contrario (la pata 2 siempre es la operación inversa a la pata 1)."""
    return PESIFICAR if modo == DOLARIZAR else DOLARIZAR


def ticker_compra(inst: Dict, modo: str) -> str:
    """Especie que se COMPRA en el modo dado (pesos al dolarizar, USD al pesificar)."""
    return inst["ticker"] if modo == DOLARIZAR else inst["tickerD"]


def ticker_venta(inst: Dict, modo: str) -> str:
    """Especie que se VENDE en el modo dado (USD al dolarizar, pesos al pesificar)."""
    return inst["tickerD"] if modo == DOLARIZAR else inst["ticker"]


def moneda_compra(modo: str) -> str:
    """Moneda que se entrega en el modo dado."""
    return "ARS" if modo == DOLARIZAR else "USD"


def moneda_venta(modo: str) -> str:
    """Moneda que se recibe en el modo dado."""
    return "USD" if modo == DOLARIZAR else "ARS"


def precio_compra(inst: Dict, modo: str) -> Optional[float]:
    """Precio al que se compra: punta vendedora (ask) de la especie de compra."""
    return inst.get("prVentaPesos") if modo == DOLARIZAR else inst.get("prVentaDolar")


def precio_venta(inst: Dict, modo: str) -> Optional[float]:
    """Precio al que se vende: punta compradora (bid) de la especie de venta."""
    return inst.get("prCompraDolar") if modo == DOLARIZAR else inst.get("prCompraPesos")


def size_compra(inst: Dict, modo: str) -> Optional[float]:
    """Nominales disponibles en la punta en la que se compra."""
    return inst.get("siVentaPesos") if modo == DOLARIZAR else inst.get("siVentaDolar")


def size_venta(inst: Dict, modo: str) -> Optional[float]:
    """Nominales disponibles en la punta en la que se vende."""
    return inst.get("siCompraDolar") if modo == DOLARIZAR else inst.get("siCompraPesos")


def _positivo(valor: Optional[float]) -> bool:
    return valor is not None and valor > 0


# ====================== TIPOS DE CAMBIO ======================
def tc_dolarizacion(inst: Dict) -> Optional[float]:
    """ARS pagados por cada USD al dolarizar (comprar en pesos, vender en USD)."""
    pv_pesos, pc_usd = inst.get("prVentaPesos"), inst.get("prCompraDolar")
    if _positivo(pv_pesos) and _positivo(pc_usd):
        return pv_pesos / pc_usd
    return None


def tc_pesificacion(inst: Dict) -> Optional[float]:
    """ARS recibidos por cada USD al pesificar (comprar en USD, vender en pesos)."""
    pc_pesos, pv_usd = inst.get("prCompraPesos"), inst.get("prVentaDolar")
    if _positivo(pc_pesos) and _positivo(pv_usd):
        return pc_pesos / pv_usd
    return None


def tc_por_modo(inst: Dict, modo: str) -> Optional[float]:
    """TC de mercado del instrumento para el modo dado."""
    return tc_dolarizacion(inst) if modo == DOLARIZAR else tc_pesificacion(inst)


def calcular_ratios(instrumentos: Iterable[Dict]) -> None:
    """
    Calcula y guarda en cada instrumento los TC (se mantienen los nombres de example_v1):
        - pesos_a_USD: TC de dolarización
        - USD_a_pesos: TC de pesificación
    Un TC <= 1 se considera dato inválido (puntas vacías o mal cargadas) y se descarta.
    """
    for inst in instrumentos:
        dol = tc_dolarizacion(inst)
        pes = tc_pesificacion(inst)
        inst["pesos_a_USD"] = dol if dol is not None and dol > 1 else None
        inst["USD_a_pesos"] = pes if pes is not None and pes > 1 else None


def calcular_consenso(instrumentos: Iterable[Dict]) -> Optional[float]:
    """
    Consenso de mercado: promedio de los dos MEP del AL30 (dolarización y pesificación).
    Requiere que `calcular_ratios` se haya ejecutado. Devuelve None si AL30 no tiene datos.
    """
    al30 = next((i for i in instrumentos if i["ticker"] == CONSENSO_TICKER), None)
    if not al30 or not al30.get("pesos_a_USD") or not al30.get("USD_a_pesos"):
        return None
    return (al30["pesos_a_USD"] + al30["USD_a_pesos"]) / 2


# ====================== OPORTUNIDADES ======================
@dataclass
class Oportunidad:
    """Un arbitraje detectado entre un instrumento que dolariza y otro que pesifica."""

    dolarizador: Dict          # Instrumento A: se compra en pesos y se vende en USD
    pesificador: Dict          # Instrumento B: se compra en USD y se vende en pesos
    ratio: float               # TC_pesificacion(B) / TC_dolarizacion(A)
    desv_dolarizador: float    # (consenso - TC_dol(A)) / consenso
    desv_pesificador: float    # (TC_pes(B) - consenso) / consenso
    modo_primera_pata: str     # DOLARIZAR (comienza por A) o PESIFICAR (comienza por B)

    @property
    def clave(self) -> Tuple[str, str]:
        """Identificador del par, usado para confirmar la oportunidad en ciclos consecutivos."""
        return (self.dolarizador["ticker"], self.pesificador["ticker"])

    @property
    def primer_instrumento(self) -> Dict:
        return self.dolarizador if self.modo_primera_pata == DOLARIZAR else self.pesificador

    @property
    def segundo_instrumento(self) -> Dict:
        return self.pesificador if self.modo_primera_pata == DOLARIZAR else self.dolarizador

    @property
    def modo_segunda_pata(self) -> str:
        return opuesto(self.modo_primera_pata)

    @property
    def desviacion_primera_pata(self) -> float:
        """Desviación absoluta del instrumento por el que se comienza (criterio de prioridad)."""
        if self.modo_primera_pata == DOLARIZAR:
            return abs(self.desv_dolarizador)
        return abs(self.desv_pesificador)


def determinar_primera_pata(
    dolarizador: Dict, pesificador: Dict, desv_dol: float, desv_pes: float
) -> str:
    """
    Decide por qué pata se comienza:
        1. Si uno de los dos es AL30, se comienza por el otro.
        2. Si no, por el que tenga mayor desviación absoluta respecto del consenso.
           En empate se comienza por el dolarizador (criterio determinístico).
    """
    if dolarizador["ticker"] == CONSENSO_TICKER:
        return PESIFICAR
    if pesificador["ticker"] == CONSENSO_TICKER:
        return DOLARIZAR
    return DOLARIZAR if abs(desv_dol) >= abs(desv_pes) else PESIFICAR


def buscar_oportunidades(
    instrumentos: List[Dict], consenso: float, ratio_minimo: float = ARBITER_RATIO
) -> List[Oportunidad]:
    """
    Recorre todos los pares (A, B), A != B, y devuelve los que cumplen
        TC_pes(B) > TC_dol(A) * ratio_minimo
    ordenados por prioridad: mayor desviación de la primera pata primero y, en empate,
    mayor ratio. Requiere que `calcular_ratios` se haya ejecutado.
    """
    oportunidades: List[Oportunidad] = []
    for a in instrumentos:
        tc_dol = a.get("pesos_a_USD")
        if not tc_dol:
            continue
        for b in instrumentos:
            if b is a or b["ticker"] == a["ticker"]:
                continue
            tc_pes = b.get("USD_a_pesos")
            if not tc_pes or not tc_pes > tc_dol * ratio_minimo:
                continue
            desv_dol = (consenso - tc_dol) / consenso
            desv_pes = (tc_pes - consenso) / consenso
            oportunidades.append(
                Oportunidad(
                    dolarizador=a,
                    pesificador=b,
                    ratio=tc_pes / tc_dol,
                    desv_dolarizador=desv_dol,
                    desv_pesificador=desv_pes,
                    modo_primera_pata=determinar_primera_pata(a, b, desv_dol, desv_pes),
                )
            )
    oportunidades.sort(key=lambda o: (o.desviacion_primera_pata, o.ratio), reverse=True)
    return oportunidades


# ====================== DIMENSIONAMIENTO ======================
def capacidad_nominales(inst: Dict, modo: str) -> int:
    """
    Nominales operables en un instrumento para un modo: el mínimo entre el size de la punta
    de compra, el size de la punta de venta y el max_quant configurado.
    """
    sizes = [size_compra(inst, modo), size_venta(inst, modo)]
    if not all(_positivo(s) for s in sizes):
        return 0
    return int(min(sizes[0], sizes[1], inst.get("max_quant") or 0))


def nominales_para_monto(monto: float, precio: Optional[float]) -> int:
    """Máxima cantidad entera de nominales que se pueden comprar con `monto` a `precio`."""
    if not _positivo(precio) or monto <= 0:
        return 0
    # Pequeña tolerancia para evitar que errores de punto flotante resten un nominal
    return int(math.floor(monto * PRICE_FACTOR / precio + 1e-9))


def monto(nominales: float, precio: Optional[float]) -> float:
    """Monto en la moneda de la especie de `nominales` a `precio` (cotización cada 100 VN)."""
    if not precio or not nominales:
        return 0.0
    return nominales * precio / PRICE_FACTOR


def dimensionar_oportunidad(op: Oportunidad) -> int:
    """
    Cantidad de nominales de la primera pata: la capacidad del primer instrumento (puntas y
    max_quant). NO se limita por la capacidad del instrumento de cierre: si la punta original
    no alcanza, la pata 2 se completa con alternativas con ratio >= CLOSE_RATIO.
    Devuelve 0 si la oportunidad no alcanza los mínimos (min_quant) de alguno de los dos.
    """
    primero, segundo = op.primer_instrumento, op.segundo_instrumento
    modo1, modo2 = op.modo_primera_pata, op.modo_segunda_pata

    cantidad = capacidad_nominales(primero, modo1)
    cap2 = capacidad_nominales(segundo, modo2)
    if cantidad < (primero.get("min_quant") or 1) or cap2 < (segundo.get("min_quant") or 1):
        return 0

    # El monto que produce la pata 1 debe alcanzar para el mínimo del segundo instrumento
    monto_producido = monto(cantidad, precio_venta(primero, modo1))
    if nominales_para_monto(monto_producido, precio_compra(segundo, modo2)) < (
        segundo.get("min_quant") or 1
    ):
        return 0
    return cantidad


def motivo_sin_tamano(op: Oportunidad) -> str:
    """Explica (para el log) por qué `dimensionar_oportunidad` devolvió 0."""
    partes = []
    for inst, modo in (
        (op.primer_instrumento, op.modo_primera_pata),
        (op.segundo_instrumento, op.modo_segunda_pata),
    ):
        partes.append(
            f"{inst['ticker']} {modo}: puntas compra={size_compra(inst, modo)} "
            f"venta={size_venta(inst, modo)} VN, min_quant={inst.get('min_quant')}, "
            f"max_quant={inst.get('max_quant')} → capacidad {capacidad_nominales(inst, modo)} VN"
        )
    return " | ".join(partes)


# ====================== CIERRE (PATA 2) ======================
def puntas_vigentes(original: Dict, fresco: Optional[Dict], modo: str) -> bool:
    """
    Las puntas originales "aún existen" si, con market data actualizada, las dos puntas que
    se necesitan para el modo tienen precio igual o mejor que el detectado y size > 0:
        - comprar: ask actual <= ask detectado
        - vender:  bid actual >= bid detectado
    """
    if not fresco:
        return False
    pc_orig, pv_orig = precio_compra(original, modo), precio_venta(original, modo)
    pc_new, pv_new = precio_compra(fresco, modo), precio_venta(fresco, modo)
    if not all(_positivo(p) for p in (pc_orig, pv_orig, pc_new, pv_new)):
        return False
    if not (_positivo(size_compra(fresco, modo)) and _positivo(size_venta(fresco, modo))):
        return False
    return pc_new <= pc_orig and pv_new >= pv_orig


def ratio_cierre(modo_cierre: str, tc_pata1: float, inst: Dict) -> Optional[float]:
    """
    Ratio que obtendría el arbitraje cerrando la pata 2 con `inst` contra el TC real de la pata 1:
        - Pata 1 DOLARIZAR (compré USD a tc_pata1) → cierre PESIFICAR: TC_pes(inst) / tc_pata1
        - Pata 1 PESIFICAR (vendí USD a tc_pata1)  → cierre DOLARIZAR: tc_pata1 / TC_dol(inst)
    """
    tc = tc_por_modo(inst, modo_cierre)
    if not tc or not tc_pata1:
        return None
    return tc / tc_pata1 if modo_cierre == PESIFICAR else tc_pata1 / tc


def candidatos_cierre(
    instrumentos: List[Dict],
    modo_cierre: str,
    tc_pata1: float,
    excluir: Iterable[str],
    ratio_minimo: float = CLOSE_RATIO,
) -> List[Tuple[Dict, float]]:
    """
    Instrumentos alternativos para cerrar la pata 2 que cumplen el ratio mínimo de cierre
    contra el TC real de la pata 1, ordenados de mejor a peor ratio.
    """
    excluidos = set(excluir)
    candidatos = []
    for inst in instrumentos:
        if inst["ticker"] in excluidos:
            continue
        r = ratio_cierre(modo_cierre, tc_pata1, inst)
        if r is not None and r >= ratio_minimo:
            candidatos.append((inst, r))
    candidatos.sort(key=lambda c: c[1], reverse=True)
    return candidatos
