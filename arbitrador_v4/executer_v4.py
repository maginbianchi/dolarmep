"""
Arbitrador MEP v4 (executer_v4).

Flujo de cada ciclo (ver estrategia.py para las definiciones):
    1. Se calculan los TC de dolarización/pesificación de todos los instrumentos y el consenso
       (promedio de los dos MEP del AL30).
    2. Se buscan arbitrajes entre TODOS los pares con ratio >= 1.0008. Como en example_v1, un
       par debe aparecer en dos ciclos consecutivos para ejecutarse (filtro de falsos positivos).
    3. Se ejecuta UN arbitraje por ciclo: el de mayor desviación respecto del consenso. Se comienza
       por el instrumento más desviado (si uno es AL30, por el otro).

Ejecución de una conversión (pata 1 y cada instrumento de la pata 2 siguen las mismas reglas):
    a) Se envía la COMPRA LIMIT a la punta.
         - No se ejecuta: se cancela, se loguea y no se hace nada más con ese instrumento.
         - Parcial: se cancela el remanente y se continúa con lo ejecutado.
    b) Se envía la VENTA LIMIT por lo comprado.
         - Parcial o no ejecutada: se cancela el remanente, se continúa con lo vendido y se crea
           una orden de VENTA de la diferencia en cartera, en la misma especie/moneda en que se
           compró y al precio promedio de compra ("deshacer"). Esa orden queda viva en mercado y
           se registra en results/pendientes.csv para control manual del operador.
    c) Mínimo de mercado (min_quant de data/instrumentos.csv): nunca se envía una orden por
       menos nominales que el mínimo del instrumento. Las puntas con size menor al mínimo se
       ignoran. Si por un error de mercado se ejecuta una cantidad menor al mínimo, esa cantidad
       no se opera (ni venta ni deshacer): solo se deja en el log.

Pata 2 (cierre):
    - Se refresca la market data. Si las puntas del instrumento original siguen con precio igual o
      mejor, se usa ese instrumento.
    - Si no, o si no alcanza para cubrir el monto, se usan alternativas cuyo ratio contra el TC
      real de la pata 1 sea >= 1.0006, de mejor a peor, hasta cubrir el monto.
    - Si no se puede ejecutar o completar, se loguea y se registra en pendientes.csv.

Control de órdenes huérfanas:
    - Toda orden enviada se registra. Las órdenes de arbitraje deben terminar en un estado final
      (FILLED/CANCELLED/REJECTED/EXPIRED): después de cada arbitraje, ante una excepción y al
      detener el bot se verifica y se cancela cualquier orden de arbitraje que siga activa.
    - Las órdenes de "deshacer" se dejan vivas a propósito (las controla el operador) y se listan
      al detener el bot.

P&L: results/pnl_diario.csv (ver registro.py).
"""

from __future__ import annotations

import configparser
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

from estrategia import (
    ARBITER_RATIO,
    CLOSE_RATIO,
    DOLARIZAR,
    Oportunidad,
    buscar_oportunidades,
    calcular_consenso,
    calcular_ratios,
    candidatos_cierre,
    capacidad_nominales,
    dimensionar_oportunidad,
    moneda_compra,
    moneda_venta,
    monto,
    motivo_sin_tamano,
    nominales_para_monto,
    precio_compra,
    precio_venta,
    puntas_vigentes,
    ticker_compra,
    ticker_venta,
)
from mercado import (
    MARKET_ID_DEFAULT,
    TIME_IN_FORCE_DEFAULT,
    CocosMatrizClient,
    DataManager,
    WebSocketClient,
    load_instruments,
)
from registro import RegistroPendientes, RegistroPnL

# ====================== CONSTANTES DE EJECUCIÓN ======================
UPDATE_SLEEP_INTERVAL = 3       # Segundos entre ciclos de evaluación (como en example_v1)
REQUIRE_CONFIRMATION = True     # El par debe aparecer en 2 ciclos consecutivos
POLL_INTENTOS = 4               # Consultas de estado por orden (1 inicial + 3 reintentos)
POLL_INTERVALO_S = 0.3          # Espera entre consultas de estado
PRICE_DECIMALS = 2              # Decimales del precio de la orden de deshacer
ESTADOS_FINALES = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})

ROL_ARBITRAJE = "ARBITRAJE"     # Órdenes que nunca deben quedar activas al terminar un arbitraje
ROL_DESHACER = "DESHACER"       # Ventas de remanentes que quedan vivas para el operador

# Estados del arbitraje en pnl_diario.csv
ESTADO_COMPLETO = "COMPLETO"
ESTADO_INCOMPLETO = "PATA2_INCOMPLETA"
ESTADO_SIN_PATA2 = "PATA2_SIN_EJECUCION"

# Directorios: el bot trabaja en arbitrador_v4/ y toma datos/credenciales del proyecto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
RESULTS_DIR = os.path.join(BASE_DIR, "results")
INSTRUMENTOS_PATH = os.path.join(PROJECT_ROOT, "data", "instrumentos.csv")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.ini")

logger = logging.getLogger("arbitrador_v4")


# ====================== ESTRUCTURAS ======================
@dataclass
class OrdenRegistrada:
    """Orden enviada por el bot y su último estado conocido."""

    cl_ord_id: str
    proprietary: str
    ticker: str
    side: str
    cantidad: int
    precio: Optional[float]
    rol: str
    arbitraje_id: str
    pata: int
    status: Optional[str] = None
    cum_qty: int = 0
    avg_px: Optional[float] = None
    texto: str = ""
    reportada: bool = False     # Ya se registró como no confirmada en pendientes.csv

    @property
    def es_final(self) -> bool:
        return self.status in ESTADOS_FINALES


@dataclass
class ResultadoConversion:
    """Resultado de convertir un instrumento (compra en una moneda + venta en la otra)."""

    ticker: str
    modo: str
    cantidad_pedida: int
    comprado: int = 0
    precio_compra: Optional[float] = None
    vendido: int = 0
    precio_venta: Optional[float] = None
    deshacer_cl_ord_id: Optional[str] = None

    @property
    def monto_entregado(self) -> float:
        """Moneda de compra gastada en total (incluye la parte que quedó para deshacer)."""
        return monto(self.comprado, self.precio_compra)

    @property
    def monto_convertido(self) -> float:
        """Moneda de compra efectivamente convertida (solo la porción vendida)."""
        return monto(self.vendido, self.precio_compra)

    @property
    def monto_obtenido(self) -> float:
        """Moneda de venta recibida."""
        return monto(self.vendido, self.precio_venta)

    @property
    def usd(self) -> float:
        """USD convertidos: recibidos al dolarizar, entregados al pesificar."""
        return self.monto_obtenido if self.modo == DOLARIZAR else self.monto_convertido

    @property
    def ars(self) -> float:
        """ARS convertidos: entregados al dolarizar, recibidos al pesificar."""
        return self.monto_convertido if self.modo == DOLARIZAR else self.monto_obtenido

    @property
    def tc(self) -> Optional[float]:
        """TC real obtenido (ARS por USD)."""
        return self.ars / self.usd if self.usd else None


@dataclass
class ResultadoCierre:
    """Resultado de la pata 2 (puede involucrar varios instrumentos)."""

    monto_inicial: float
    monto_restante: float
    completo: bool
    conversiones: List[ResultadoConversion] = field(default_factory=list)

    @property
    def usd(self) -> float:
        return sum(c.usd for c in self.conversiones)

    @property
    def ars(self) -> float:
        return sum(c.ars for c in self.conversiones)

    @property
    def tc(self) -> Optional[float]:
        return self.ars / self.usd if self.usd else None

    @property
    def tickers(self) -> str:
        return "+".join(c.ticker for c in self.conversiones if c.vendido > 0)


# ====================== EXECUTER ======================
class Executer:
    """Detecta y ejecuta arbitrajes MEP entre todos los instrumentos."""

    def __init__(
        self,
        account: str,
        client,
        data_manager,
        registro_pnl: RegistroPnL,
        registro_pendientes: RegistroPendientes,
        require_confirmation: bool = REQUIRE_CONFIRMATION,
        ratio: float = ARBITER_RATIO,
        ratio_cierre: float = CLOSE_RATIO,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.account = account
        self.client = client
        self.data_manager = data_manager
        self.pnl = registro_pnl
        self.pendientes = registro_pendientes
        self.require_confirmation = require_confirmation
        self.ratio = ratio
        self.ratio_cierre = ratio_cierre
        self._sleep = sleep_fn
        self.ordenes: Dict[str, OrdenRegistrada] = {}       # órdenes de arbitraje no finales
        self.ordenes_deshacer: Dict[str, OrdenRegistrada] = {}
        self._pares_ciclo_anterior: Set[Tuple[str, str]] = set()
        self._pares_sin_tamano: Set[Tuple[str, str]] = set()  # ya logueados como bajo mínimo
        self.arbitraje_id = ""

    # ---------------------- CICLO PRINCIPAL ----------------------
    def execute(self, instrumentos: List[Dict]) -> Optional[str]:
        """
        Evalúa un snapshot de mercado y ejecuta como máximo un arbitraje.
        Devuelve el id del arbitraje ejecutado o None.
        """
        calcular_ratios(instrumentos)
        consenso = calcular_consenso(instrumentos)
        if consenso is None:
            logger.warning("No se pudo calcular el consenso (AL30 sin puntas). Se saltea el ciclo.")
            return None

        oportunidades = buscar_oportunidades(instrumentos, consenso, self.ratio)
        claves_actuales = {op.clave for op in oportunidades}

        # Confirmación en dos ciclos consecutivos (equivalente a dolarizadores/pesificadores de v1)
        if self.require_confirmation:
            confirmadas = [op for op in oportunidades if op.clave in self._pares_ciclo_anterior]
            for clave in claves_actuales - self._pares_ciclo_anterior:
                logger.info(f"Oportunidad detectada {clave[0]}→{clave[1]}: se espera confirmación.")
            for clave in self._pares_ciclo_anterior - claves_actuales:
                logger.info(f"Oportunidad {clave[0]}→{clave[1]} desapareció.")
        else:
            confirmadas = oportunidades
        self._pares_ciclo_anterior = claves_actuales

        # Los pares ignorados por tamaño se loguean una sola vez mientras sigan apareciendo
        self._pares_sin_tamano &= claves_actuales

        # Las oportunidades ya vienen ordenadas por mayor desviación de la primera pata
        for op in confirmadas:
            cantidad = dimensionar_oportunidad(op)
            if cantidad <= 0:
                if op.clave not in self._pares_sin_tamano:
                    self._pares_sin_tamano.add(op.clave)
                    logger.info(
                        f"Se ignora {op.clave[0]}→{op.clave[1]} (ratio {op.ratio:.5f}): las puntas "
                        f"no alcanzan el mínimo de mercado. {motivo_sin_tamano(op)}"
                    )
                continue
            self.ejecutar_arbitraje(op, cantidad, consenso)
            return self.arbitraje_id
        return None

    def ejecutar_arbitraje(self, op: Oportunidad, cantidad: int, consenso: float) -> None:
        """Ejecuta la pata 1 y, si convirtió algo, la pata 2. Al final verifica huérfanas."""
        primero, modo1 = op.primer_instrumento, op.modo_primera_pata
        self.arbitraje_id = (
            f"{datetime.now():%Y%m%d-%H%M%S}-{op.dolarizador['ticker']}-{op.pesificador['ticker']}"
        )
        logger.info(
            f"🚀 [{self.arbitraje_id}] Arbitraje: dolariza {op.dolarizador['ticker']} "
            f"(TC {op.dolarizador['pesos_a_USD']:.4f}, desv {op.desv_dolarizador:+.4%}) / pesifica "
            f"{op.pesificador['ticker']} (TC {op.pesificador['USD_a_pesos']:.4f}, desv "
            f"{op.desv_pesificador:+.4%}) | ratio {op.ratio:.5f} | consenso {consenso:.4f} | "
            f"comienza {modo1} {primero['ticker']} x {cantidad}"
        )
        try:
            # ---------- PATA 1 ----------
            r1 = self._convertir(primero, modo1, cantidad, pata=1)
            if r1.vendido <= 0:
                logger.warning(
                    f"[{self.arbitraje_id}] Pata 1 sin conversión ({primero['ticker']}): "
                    "no se ejecuta la pata 2."
                )
                return

            monto_pata1, tc1 = r1.monto_obtenido, r1.tc
            logger.info(
                f"[{self.arbitraje_id}] Pata 1 OK: {r1.vendido} nominales, "
                f"{monto_pata1:,.2f} {moneda_venta(modo1)} a TC {tc1:.4f}"
            )

            # ---------- PATA 2 ----------
            cierre = self._cerrar(op, monto_pata1, tc1)

            # ---------- P&L ----------
            deshacer = [c.deshacer_cl_ord_id for c in cierre.conversiones if c.deshacer_cl_ord_id]
            if not cierre.conversiones or cierre.usd <= 0:
                estado = ESTADO_SIN_PATA2
            elif cierre.completo and not deshacer:
                estado = ESTADO_COMPLETO
            else:
                # Quedó monto sin cerrar o nominales de la pata 2 en una orden de deshacer
                estado = ESTADO_INCOMPLETO
            if r1.deshacer_cl_ord_id:
                deshacer.insert(0, r1.deshacer_cl_ord_id)
            # Monto libre sin cerrar expresado en USD (al TC de la pata 1 si son pesos)
            usd_sin_cerrar = (
                cierre.monto_restante if modo1 == DOLARIZAR else cierre.monto_restante / tc1
            )
            self.pnl.registrar(
                arbitraje_id=self.arbitraje_id,
                estado=estado,
                modo_pata1=modo1,
                instrumento_pata1=primero["ticker"],
                instrumentos_pata2=cierre.tickers,
                tc1=tc1,
                tc2=cierre.tc,
                usd_pata1=r1.usd,
                usd_cerrados=cierre.usd,
                usd_sin_cerrar=max(usd_sin_cerrar, 0.0),
                ordenes_deshacer=" ".join(deshacer),
            )
        finally:
            # Pase lo que pase (incluso una excepción), ninguna orden de arbitraje queda activa
            self.verificar_huerfanas()

    # ---------------------- PATA 2 ----------------------
    def _market_actualizado(self) -> List[Dict]:
        """Snapshot fresco de la market data con los TC recalculados."""
        snapshot = self.data_manager.get_snapshot()
        calcular_ratios(snapshot)
        return snapshot

    def _cerrar(self, op: Oportunidad, monto_a_cerrar: float, tc1: float) -> ResultadoCierre:
        """
        Cierra el arbitraje convirtiendo `monto_a_cerrar` (moneda que produjo la pata 1).
        Primero el instrumento original si sus puntas siguen vigentes; luego alternativas con
        ratio >= ratio_cierre contra tc1, hasta cubrir el monto o quedarse sin candidatos.
        Antes de cada instrumento se refresca la market data.
        """
        modo = op.modo_segunda_pata
        original = op.segundo_instrumento
        excluidos = {op.primer_instrumento["ticker"]}
        restante = monto_a_cerrar
        conversiones: List[ResultadoConversion] = []
        original_evaluado = False
        # Tolerancia de redondeo: lo que no alcanza para un nominal no cuenta como incompleto
        precio_unitario = monto(1, precio_compra(original, modo))

        while True:
            snapshot = self._market_actualizado()
            candidatos: List[Tuple[Dict, str]] = []

            # 1) Instrumento original, solo si sus puntas siguen con precio igual o mejor
            if not original_evaluado:
                original_evaluado = True
                excluidos.add(original["ticker"])
                fresco = next((i for i in snapshot if i["ticker"] == original["ticker"]), None)
                if puntas_vigentes(original, fresco, modo):
                    candidatos.append((fresco, "original"))
                else:
                    logger.warning(
                        f"[{self.arbitraje_id}] Las puntas de {original['ticker']} ya no existen: "
                        f"se buscan alternativas con ratio >= {self.ratio_cierre}."
                    )

            # 2) Alternativas que cumplen el ratio de cierre contra el TC real de la pata 1
            for alt, r in candidatos_cierre(snapshot, modo, tc1, excluidos, self.ratio_cierre):
                candidatos.append((alt, f"alternativa ratio {r:.5f}"))

            # Primer candidato con tamaño suficiente para el monto restante. Un candidato cuya
            # punta o monto restante no llega al mínimo de mercado se ignora (no se opera)
            elegido = None
            for cand, origen in candidatos:
                minimo = cand.get("min_quant") or 1
                capacidad = capacidad_nominales(cand, modo)
                cantidad = min(capacidad, nominales_para_monto(restante, precio_compra(cand, modo)))
                if cantidad >= minimo:
                    elegido = (cand, cantidad, origen)
                    break
                motivo = "punta" if capacidad < minimo else "monto restante"
                logger.info(
                    f"[{self.arbitraje_id}] Se ignora {cand['ticker']} ({origen}): {motivo} de "
                    f"{cantidad} VN por debajo del mínimo de mercado ({minimo} VN)."
                )
            if elegido is None:
                break

            cand, cantidad, origen = elegido
            excluidos.add(cand["ticker"])
            logger.info(
                f"[{self.arbitraje_id}] Pata 2 con {cand['ticker']} ({origen}): {modo} x {cantidad} "
                f"| restante {restante:,.2f} {moneda_compra(modo)}"
            )
            resultado = self._convertir(cand, modo, cantidad, pata=2)
            conversiones.append(resultado)
            # Se descuenta todo lo comprado: lo no vendido queda en la orden de deshacer
            restante -= resultado.monto_entregado
            precio_unitario = monto(1, precio_compra(cand, modo))
            if restante < precio_unitario:
                break

        completo = restante < precio_unitario
        cierre = ResultadoCierre(monto_a_cerrar, max(restante, 0.0), completo, conversiones)
        if cierre.usd <= 0:
            logger.error(
                f"[{self.arbitraje_id}] No se pudo ejecutar la segunda parte del arbitraje: "
                f"quedan {restante:,.2f} {moneda_compra(modo)} sin convertir."
            )
        elif not completo:
            logger.warning(
                f"[{self.arbitraje_id}] La segunda parte no cubrió el monto de la primera: "
                f"quedan {restante:,.2f} {moneda_compra(modo)} sin convertir."
            )
        if not completo:
            self.pendientes.registrar(
                arbitraje_id=self.arbitraje_id,
                tipo="CIERRE_INCOMPLETO",
                pata=2,
                ticker=original["ticker"],
                moneda=moneda_compra(modo),
                monto=restante,
                detalle=(
                    f"{modo} pendiente: {restante:,.2f} {moneda_compra(modo)} sin convertir "
                    f"(TC pata 1 {tc1:.4f}, ratio mínimo {self.ratio_cierre})."
                ),
            )
        return cierre

    # ---------------------- CONVERSIÓN DE UN INSTRUMENTO ----------------------
    def _convertir(self, inst: Dict, modo: str, cantidad: int, pata: int) -> ResultadoConversion:
        """
        Compra `cantidad` nominales en una moneda y vende lo comprado en la otra, aplicando las
        reglas de parciales/no ejecutadas descriptas en el docstring del módulo.
        """
        t_compra, t_venta = ticker_compra(inst, modo), ticker_venta(inst, modo)
        px_compra, px_venta = precio_compra(inst, modo), precio_venta(inst, modo)
        res = ResultadoConversion(ticker=inst["ticker"], modo=modo, cantidad_pedida=cantidad)
        tag = f"[{self.arbitraje_id}] Pata {pata} {modo} {inst['ticker']}"
        minimo = inst.get("min_quant") or 1
        if cantidad < minimo:  # defensa: el dimensionamiento ya lo garantiza
            logger.error(f"{tag}: {cantidad} VN por debajo del mínimo de mercado ({minimo}). No se opera.")
            return res

        # ----- a) COMPRA -----
        logger.info(f"{tag}: COMPRA {cantidad} {t_compra} @ {px_compra} {moneda_compra(modo)}")
        compra = self._enviar_orden(t_compra, "BUY", cantidad, px_compra, ROL_ARBITRAJE, pata)
        if compra is None:
            logger.warning(f"{tag}: no se pudo enviar la compra. Sin más acciones.")
            return res
        self._esperar_ejecucion(compra)
        if compra.cum_qty < cantidad:
            logger.warning(
                f"{tag}: compra {compra.status} ({compra.cum_qty}/{cantidad}) "
                f"{compra.texto}. Se cancela el remanente."
            )
            self._cancelar_y_confirmar(compra)
        res.comprado = min(compra.cum_qty, cantidad)
        if res.comprado <= 0:
            logger.warning(f"{tag}: la compra no se ejecutó. Sin más acciones.")
            return res
        res.precio_compra = compra.avg_px or px_compra
        if not compra.avg_px:
            logger.warning(f"{tag}: compra sin avgPx; se usa el precio límite {px_compra}.")
        if res.comprado < minimo:
            # Error de mercado: no debería ejecutarse menos del mínimo. No se opera esa cantidad
            logger.error(
                f"{tag}: ⚠️ se ejecutaron {res.comprado} {t_compra}, por debajo del mínimo de "
                f"mercado ({minimo} VN). No se envía la venta: quedan en cartera sin operar."
            )
            return res

        # ----- b) VENTA -----
        logger.info(f"{tag}: VENTA {res.comprado} {t_venta} @ {px_venta} {moneda_venta(modo)}")
        venta = self._enviar_orden(t_venta, "SELL", res.comprado, px_venta, ROL_ARBITRAJE, pata)
        if venta is None:
            logger.error(f"{tag}: no se pudo enviar la venta.")
        else:
            self._esperar_ejecucion(venta)
            if venta.cum_qty < res.comprado:
                logger.warning(
                    f"{tag}: venta {venta.status} ({venta.cum_qty}/{res.comprado}) "
                    f"{venta.texto}. Se cancela el remanente."
                )
                self._cancelar_y_confirmar(venta)
            res.vendido = min(venta.cum_qty, res.comprado)
            if res.vendido > 0:
                res.precio_venta = venta.avg_px or px_venta
                if not venta.avg_px:
                    logger.warning(f"{tag}: venta sin avgPx; se usa el precio límite {px_venta}.")

        # ----- c) DESHACER la diferencia en cartera -----
        remanente = res.comprado - res.vendido
        if 0 < remanente < minimo:
            # Error de mercado: la venta dejó un remanente menor al mínimo. No se opera
            logger.error(
                f"{tag}: ⚠️ remanente de {remanente} {t_compra} por debajo del mínimo de mercado "
                f"({minimo} VN). No se envía la orden de deshacer: quedan en cartera sin operar."
            )
        elif remanente > 0:
            res.deshacer_cl_ord_id = self._deshacer(inst, modo, remanente, res.precio_compra, pata)
        return res

    def _deshacer(
        self, inst: Dict, modo: str, cantidad: int, precio: float, pata: int
    ) -> Optional[str]:
        """
        Envía una venta LIMIT de `cantidad` nominales en la misma especie/moneda en que se
        compraron y al precio promedio de compra. La orden queda viva para el operador.
        """
        ticker = ticker_compra(inst, modo)
        moneda = moneda_compra(modo)
        precio = round(float(precio), PRICE_DECIMALS)
        logger.warning(
            f"[{self.arbitraje_id}] ↩️ Deshacer: VENTA {cantidad} {ticker} @ {precio} {moneda} "
            "(precio de compra). Queda activa para control del operador."
        )
        orden = self._enviar_orden(ticker, "SELL", cantidad, precio, ROL_DESHACER, pata)
        if orden is not None:
            self._consultar(orden)
        if orden is None or orden.status in ("REJECTED", "CANCELLED", "EXPIRED"):
            detalle = orden.texto if orden else "error de envío"
            logger.error(
                f"[{self.arbitraje_id}] ❌ No quedó activa la venta de deshacer de {cantidad} "
                f"{ticker} ({detalle}). Requiere acción manual."
            )
            self.pendientes.registrar(
                arbitraje_id=self.arbitraje_id,
                tipo="DESHACER_ERROR",
                pata=pata,
                ticker=ticker,
                side="SELL",
                cantidad=cantidad,
                precio=precio,
                moneda=moneda,
                monto=monto(cantidad, precio),
                cl_ord_id=orden.cl_ord_id if orden else "",
                detalle=f"Vender manualmente {cantidad} {ticker} @ {precio} {moneda}: {detalle}",
            )
            return None
        self.pendientes.registrar(
            arbitraje_id=self.arbitraje_id,
            tipo="DESHACER_ACTIVA",
            pata=pata,
            ticker=ticker,
            side="SELL",
            cantidad=cantidad,
            precio=precio,
            moneda=moneda,
            monto=monto(cantidad, precio),
            cl_ord_id=orden.cl_ord_id,
            detalle=(
                "Ejecutada al enviarse (posición deshecha a costo)."
                if orden.status == "FILLED"
                else f"Orden viva (status {orden.status}). Controlar manualmente."
            ),
        )
        return orden.cl_ord_id

    # ---------------------- HELPERS DE ÓRDENES ----------------------
    @staticmethod
    def _symbol(ticker: str) -> str:
        return f"MERV - XMEV - {ticker} - 24hs"

    def _enviar_orden(
        self,
        ticker: str,
        side: str,
        cantidad: int,
        precio: Optional[float],
        rol: str,
        pata: int,
    ) -> Optional[OrdenRegistrada]:
        """Envía una orden LIMIT y la registra. Devuelve None si el envío falló."""
        resp = self.client.send_order(
            symbol=self._symbol(ticker),
            side=side,
            quantity=int(cantidad),
            price=precio,
            ord_type="LIMIT",
            market_id=MARKET_ID_DEFAULT,
            account=self.account,
            time_in_force=TIME_IN_FORCE_DEFAULT,
        )
        if "error" in resp or resp.get("status") == "ERROR":
            logger.error(f"Error enviando {side} {cantidad} {ticker} @ {precio}: {resp}")
            return None
        order = resp.get("order") or {}
        cl_ord_id, prop = order.get("clientId"), order.get("proprietary")
        if not cl_ord_id or not prop:
            logger.error(f"Respuesta sin clOrdId/proprietary al enviar {side} {ticker}: {resp}")
            return None
        orden = OrdenRegistrada(
            cl_ord_id=cl_ord_id,
            proprietary=prop,
            ticker=ticker,
            side=side,
            cantidad=int(cantidad),
            precio=precio,
            rol=rol,
            arbitraje_id=self.arbitraje_id,
            pata=pata,
        )
        destino = self.ordenes if rol == ROL_ARBITRAJE else self.ordenes_deshacer
        destino[cl_ord_id] = orden
        logger.info(f"Orden enviada → {side} {cantidad} {ticker} @ {precio} clOrdId={cl_ord_id}")
        return orden

    def _consultar(self, orden: OrdenRegistrada) -> OrdenRegistrada:
        """Actualiza status, cumQty y avgPx de la orden. Ante error conserva el último estado."""
        resp = self.client.get_orders_by_clor_id(orden.cl_ord_id, orden.proprietary)
        if "error" in resp:
            logger.warning(f"Error consultando {orden.cl_ord_id}: {resp.get('error')}")
            return orden
        data = resp.get("order") or {}
        orden.status = data.get("status", orden.status)
        orden.cum_qty = int(float(data.get("cumQty") or 0))
        avg_px = data.get("avgPx")
        orden.avg_px = float(avg_px) if avg_px else None
        orden.texto = data.get("text") or ""
        return orden

    def _esperar_ejecucion(self, orden: OrdenRegistrada) -> OrdenRegistrada:
        """Consulta la orden hasta que se ejecute completa, llegue a un estado final o se agoten
        los intentos. Un PARTIALLY_FILLED no corta la espera."""
        for intento in range(POLL_INTENTOS):
            if intento:
                self._sleep(POLL_INTERVALO_S)
            self._consultar(orden)
            if orden.es_final or orden.cum_qty >= orden.cantidad:
                break
        return orden

    def _cancelar_y_confirmar(self, orden: OrdenRegistrada) -> OrdenRegistrada:
        """
        Cancela el remanente y vuelve a consultar hasta un estado final, para obtener el cumQty
        definitivo (evita la carrera entre un fill tardío y la cancelación).
        """
        if orden.es_final:
            return orden
        resp = self.client.cancel_order(orden.cl_ord_id, orden.proprietary)
        if "error" in resp:
            logger.warning(f"Error cancelando {orden.cl_ord_id}: {resp.get('error')}")
        for intento in range(POLL_INTENTOS):
            if intento:
                self._sleep(POLL_INTERVALO_S)
            self._consultar(orden)
            if orden.es_final:
                break
        if not orden.es_final:
            logger.error(
                f"⚠️ No se confirmó la cancelación de {orden.cl_ord_id} ({orden.side} {orden.ticker}, "
                f"status {orden.status}, cumQty {orden.cum_qty}). Se reintentará en la verificación."
            )
        return orden

    # ---------------------- CONTROL DE HUÉRFANAS ----------------------
    def verificar_huerfanas(self) -> List[OrdenRegistrada]:
        """
        Garantiza que ninguna orden de arbitraje quede activa: consulta las que no están en un
        estado final, cancela las activas y registra en pendientes.csv las que no se pudieron
        confirmar. Devuelve la lista de órdenes que siguen sin confirmar.
        """
        sin_confirmar = []
        for cl_ord_id, orden in list(self.ordenes.items()):
            if not orden.es_final:
                self._consultar(orden)
            if not orden.es_final:
                logger.warning(
                    f"Orden de arbitraje activa {cl_ord_id} ({orden.side} {orden.ticker}): se cancela."
                )
                self._cancelar_y_confirmar(orden)
            if orden.es_final:
                del self.ordenes[cl_ord_id]  # ya no puede recibir fills: deja de seguirse
                continue
            sin_confirmar.append(orden)
            if not orden.reportada:
                orden.reportada = True
                logger.error(f"❌ Posible orden huérfana {cl_ord_id}: revisar en la comitente.")
                self.pendientes.registrar(
                    arbitraje_id=orden.arbitraje_id,
                    tipo="ORDEN_NO_CONFIRMADA",
                    pata=orden.pata,
                    ticker=orden.ticker,
                    side=orden.side,
                    cantidad=orden.cantidad,
                    precio=orden.precio,
                    cl_ord_id=cl_ord_id,
                    detalle=f"Status {orden.status}, cumQty {orden.cum_qty}. Cancelar manualmente.",
                )
        return sin_confirmar

    def cerrar(self) -> None:
        """Se llama al detener el bot: verifica huérfanas y lista las órdenes de deshacer vivas."""
        logger.info("Verificando órdenes antes de salir...")
        pendientes = self.verificar_huerfanas()
        if pendientes:
            logger.error(f"Quedan {len(pendientes)} órdenes de arbitraje sin confirmar.")
        for orden in self.ordenes_deshacer.values():
            self._consultar(orden)
            if not orden.es_final:
                logger.warning(
                    f"Orden de deshacer viva (control manual): {orden.side} {orden.cantidad} "
                    f"{orden.ticker} @ {orden.precio} clOrdId={orden.cl_ord_id} "
                    f"(status {orden.status}, cumQty {orden.cum_qty})"
                )


# ====================== MAIN ======================
def configurar_logging(results_dir: str = RESULTS_DIR) -> None:
    """Log a consola y a results/executer_v4.log."""
    os.makedirs(results_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(results_dir, "executer_v4.log"), encoding="utf-8"),
        ],
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Arbitrador MEP v4")
    parser.add_argument(
        "--duracion-segundos",
        type=int,
        default=0,
        help="Si es > 0, el bot se detiene solo (de forma limpia) al cumplirse el tiempo.",
    )
    args = parser.parse_args()
    fin = time.time() + args.duracion_segundos if args.duracion_segundos > 0 else None

    configurar_logging()
    instrumentos = load_instruments(INSTRUMENTOS_PATH)
    logger.info(f"{len(instrumentos)} instrumentos habilitados desde {INSTRUMENTOS_PATH}")

    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"No se encontró el archivo de credenciales en {CONFIG_PATH}.")
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    cred = config["credentials"]
    websocket_url = (
        f"wss://matriz.cocos.xoms.com.ar/ws?session_id={cred['matriz_session_id']}"
        f"&conn_id={cred['matriz_conn_id']}"
    )

    client = CocosMatrizClient(username=cred["matriz_username"], password=cred["matriz_password"])
    data_manager = DataManager(instrumentos)
    websocket_client = WebSocketClient(websocket_url, data_manager, instrumentos)
    websocket_client.connect()

    executer = Executer(
        account=cred["matriz_account"],
        client=client,
        data_manager=data_manager,
        registro_pnl=RegistroPnL(os.path.join(RESULTS_DIR, "pnl_diario.csv")),
        registro_pendientes=RegistroPendientes(os.path.join(RESULTS_DIR, "pendientes.csv")),
    )

    try:
        while fin is None or time.time() < fin:
            time.sleep(UPDATE_SLEEP_INTERVAL)
            try:
                executer.execute(data_manager.get_snapshot())
            except Exception:  # noqa: BLE001 - un error en un ciclo no debe detener el bot
                logger.exception("Error inesperado en el ciclo; se verifican órdenes huérfanas.")
                executer.verificar_huerfanas()
        logger.info("Duración cumplida: deteniendo el bot...")
    except KeyboardInterrupt:
        logger.info("Deteniendo el bot...")
    finally:
        executer.cerrar()
        websocket_client.stop_websocket()


if __name__ == "__main__":
    main()
