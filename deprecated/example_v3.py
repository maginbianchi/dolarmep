import requests
import base64
import json
import time
import threading
import websocket
import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
import logging
import os
import configparser
import copy
import csv

# ====================== CONSTANTES ======================
DEFAULT_API_URL = "https://api.cocos.xoms.com.ar"
REQUEST_TIMEOUT = 15
AUTH_TIMEOUT = 15
MARKET_ID_DEFAULT = "ROFX"
TIME_IN_FORCE_DEFAULT = "DAY"
WEBSOCKET_RECONNECT_DELAY = 5
ARBITER_RATIO = 1.0008  # Ratio mínimo para disparar arbitraje (ej. 1.0006 = 0.06% spread)
# Ratio mínimo aceptable para CERRAR el arbitraje en pata 2, medido con market data actualizada
# contra el tipo de cambio real obtenido en pata 1. Si la punta original desapareció, se busca
# la mejor alternativa que cumpla este ratio; si ninguna lo cumple, se registra en pendientes.
CLOSE_RATIO = 1.0006
AL30_MAX_QUANT_DEFAULT = 2000
UPDATE_SLEEP_INTERVAL = 1.5
REQUIRE_CONFIRMATION = True  # Requiere que la oportunidad persista al menos 2 ciclos para evitar falsos positivos
AL30_ORDER_TYPE = "MARKET"   # Orden a mercado para AL30 dada su alta liquidez; LIMIT para corporativos
# STY-01: TOLERANCE movida al nivel de módulo
TOLERANCE = 0.0005          # Margen de 0.05% para considerar empate técnico en calculate_leg_priority
# TRX-03: Parámetros de polling de estado de orden
# Reintentos al consultar una orden: 1 consulta inicial + POLL_RETRIES reintentos
POLL_RETRIES = 1
POLL_INTERVAL_S = 0.3
# TRX-01/TRX-02: Reintentos de segunda pata
MAX_RETRY_PATA2 = 1
# Tamaño de lote de cotización: en BYMA los bonos soberanos y ONs cotizan cada 100 nominales
BOND_LOT_SIZE = 100
# Estados de orden considerados definitivos (la orden ya no puede recibir más fills)
ESTADOS_FINALES = ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")
# Decimales con los que se envía el precio de la orden de deshacer remanente (precio promedio de compra)
PRICE_DECIMALS = 2

# Directorios del proyecto
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
POSICIONES_PENDIENTES_PATH = os.path.join(RESULTS_DIR, "posiciones_pendientes.csv")
# P&L realizado por arbitraje (una fila por arbitraje con pata 1 ejecutada)
# - tc_pata1 / tc_pata2: tipos de cambio reales (avgPx) de cada pata, en ARS/USD
# - usd_arbitrado: USD efectivamente calzados entre ambas patas
# - usd_sin_cerrar: USD de pata 1 que no se cerraron en pata 2 (ver posiciones_pendientes.csv)
# - pnl_ars: resultado bruto en pesos (sin comisiones ni derechos de mercado)
PNL_PATH = os.path.join(RESULTS_DIR, "pnl_diario.csv")
PNL_COLUMNS = [
    "fecha",
    "hora",
    "arbitraje_id",
    "estado",
    "modo_pata1",
    "instrumento_pata1",
    "instrumento_pata2",
    "tc_pata1",
    "tc_pata2",
    "usd_arbitrado",
    "usd_sin_cerrar",
    "pnl_ars",
    "pnl_acumulado_dia",
    "operaciones_dia",
]
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs("logs", exist_ok=True)

# Columnas del CSV de posiciones pendientes
# - tipo_pendiente: NOMINALES (bonos en cartera sin vender) o MONEDA (pesos/USD sin convertir)
# - cantidad_nominales: para NOMINALES; moneda_pendiente/monto_pendiente/usd_pendiente: para MONEDA
# - tc_pata1: tipo de cambio real obtenido en pata 1 (ARS/USD)
# - mejor_alternativa / mejor_tc_disponible / ratio_mejor_alternativa: mejor cierre visto al refrescar
PENDIENTES_COLUMNS = [
    "timestamp",
    "arbitraje_id",
    "razon",
    "pata",
    "modo",
    "tipo_pendiente",
    "ticker",
    "tickerD",
    "cantidad_nominales",
    "moneda_pendiente",
    "monto_pendiente",
    "usd_pendiente",
    "precio_compra",
    "precio_intento_venta",
    "tc_pata1",
    "ratio_requerido",
    "mejor_alternativa",
    "mejor_tc_disponible",
    "ratio_mejor_alternativa",
    "cl_ord_id",
    "accion_manual_requerida",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/example_v3.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class CocosMatrizClient:
    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = DEFAULT_API_URL,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: Optional[str] = None
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.basic_auth = base64.b64encode(
            f"{username}:{password}".encode()
        ).decode()  # Para endpoints de riesgo
        self.login()

    def login(self) -> bool:
        """Login a la Primary API"""
        url = f"{self.base_url}/auth/getToken"
        headers = {"X-Username": self.username, "X-Password": self.password}
        try:
            response = requests.post(url, headers=headers, timeout=AUTH_TIMEOUT)
            response.raise_for_status()
            self.token = response.headers.get("X-Auth-Token")
            if self.token:
                self.headers["X-Auth-Token"] = self.token
                logger.info(
                    f"✅ Login exitoso - Token válido por 24h: {datetime.now()}"
                )
                return True
            else:
                logger.error("❌ Login falló: no se recibió token")
                return False
        except Exception as e:
            logger.error(f"❌ Error en login: {e}")
            return False

    # ====================== GESTIÓN DE ÓRDENES ======================
    def send_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: Optional[float] = None,
        ord_type: str = "LIMIT",
        market_id: str = MARKET_ID_DEFAULT,
        account: str = "",
        time_in_force: str = TIME_IN_FORCE_DEFAULT,
        **kwargs,
    ) -> Dict:
        url = f"{self.base_url}/rest/order/newSingleOrder"
        params = {
            "marketId": market_id,
            "symbol": symbol,
            "side": side.upper(),
            "orderQty": quantity,
            "ordType": ord_type.upper(),
            "account": account,
            "timeInForce": time_in_force.upper(),
            **kwargs,
        }
        if price is not None:
            params["price"] = str(price)

        r = requests.get(
            url, headers=self.headers, params=params, timeout=REQUEST_TIMEOUT
        )
        return r.json() if r.ok else {"error": r.text}

    def get_orders_by_clor_id(
        self, clorId: str, proprietary: str
    ) -> Dict:
        """Consultar órdenes por cuenta"""
        endpoint = "/rest/order/id"
        url = f"{self.base_url}{endpoint}"
        params = {
            "clOrdId": clorId,
            "proprietary": proprietary,
        }
        r = requests.get(url, headers=self.headers, params=params, timeout=REQUEST_TIMEOUT)
        return r.json() if r.ok else {"error": r.text}

    def cancel_order(self, cl_ord_id: str, proprietary: str) -> Dict:
        """Cancelar orden por clOrdId"""
        url = f"{self.base_url}/rest/order/cancelById"
        params = {"clOrdId": cl_ord_id, "proprietary": proprietary}
        r = requests.get(url, headers=self.headers, params=params, timeout=REQUEST_TIMEOUT)
        return r.json() if r.ok else {"error": r.text}


class WebSocketClient:
    def __init__(self, url, dataManager, instrumentos):
        self.url = url
        self.dataManager = dataManager
        self.instrumentos = instrumentos
        self.ws = None
        # CONC-02: límite de reconexiones automáticas
        self._reconnect_count = 0
        self._max_reconnects = 10

    def connect(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws.keep_running = True
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()

        return wst

    def on_message(self, ws, message):
        data = []
        if message[0] != "X":
            if message[0] == "M":
                data.append(message)
            else:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError as e:
                    logger.warning(f"❌ JSON Decode Error: {e}")
                    return
        else:
            logger.warning("Message is not market data")
            return
        self.dataManager.update_instrument_data(data)

    def on_error(self, ws, error):
        logger.error(f"Error WebSocket: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info("### Closed connection ###")
        logger.info(f"WebSocket cerrado (code: {close_status_code}): {close_msg}")
        # CONC-02: guard contra reconexión infinita
        if self._reconnect_count < self._max_reconnects:
            self._reconnect_count += 1
            logger.info(f"Reconectando... intento {self._reconnect_count}/{self._max_reconnects}")
            time.sleep(WEBSOCKET_RECONNECT_DELAY)
            self.connect()
        else:
            logger.error(
                f"Se alcanzó el máximo de reconexiones ({self._max_reconnects}). "
                "WebSocket detenido permanentemente."
            )

    def on_open(self, ws):
        logger.info("### Opened connection ###")
        ws.send(self.create_subscription_message())

    def create_subscription_message(self):
        aux = [
            "md.bm_MERV_{0}_24hs".format(inst["ticker"]) for inst in self.instrumentos
        ] + [
            "md.bm_MERV_{0}_24hs".format(inst["tickerD"]) for inst in self.instrumentos
        ]
        return (
            '{"_req":"S","topicType":"md","topics":'
            + str(aux).replace(" ", "").replace("'", '"')
            + ',"replace":false}'
        )

    def stop_websocket(self):
        if self.ws:
            self.ws.close()
            logger.info("WebSocket detenido")
        self.ws = None


class DataManager:
    """Gerencia datos de instrumentos actualizados desde el WebSocket."""

    def __init__(self, instrumentos: List[Dict]) -> None:
        self.instrumentos_by_ticker = {
            inst["ticker"]: inst for inst in instrumentos
        }
        self.instrumentos_by_tickerD = {
            inst["tickerD"]: inst for inst in instrumentos
        }
        # CONC-01: lock para acceso thread-safe desde WebSocket y main loop
        self._lock = threading.RLock()

    def update_instrument_data(self, data: List) -> None:
        """
        Actualiza datos de instrumentos a partir de mensajes del WebSocket.
        Formato de mensaje por columnas:
        values[0]: Topic (ej. M:bm_MERV_AL30_24hs)
        values[2]: Tamaño Bid
        values[3]: Precio Bid
        values[4]: Precio Ask
        values[5]: Tamaño Ask
        """
        with self._lock:  # CONC-01: proteger escrituras concurrentes
            for record in data:
                values = str(record).split("|")
                if len(values) < 5:
                    continue

                if "_24hs" not in values[0]:
                    continue

                ticker = (
                    values[0].removeprefix("M:bm_MERV_").removesuffix("_24hs")
                )

                pr_bid = float(values[3]) if values[3] and values[3] != "-100" else None
                pr_ask = float(values[4]) if values[4] and values[4] != "-100" else None
                si_bid = float(values[2]) if len(values) > 2 and values[2] and values[2] != "0" else None
                si_ask = float(values[5]) if len(values) > 5 and values[5] and values[5] != "0" else None

                # Actualizar por ticker de pesos
                if ticker in self.instrumentos_by_ticker:
                    inst = self.instrumentos_by_ticker[ticker]
                    inst["prCompraPesos"] = pr_bid
                    inst["prVentaPesos"] = pr_ask
                    inst["siCompraPesos"] = si_bid
                    inst["siVentaPesos"] = si_ask

                # Actualizar por ticker de dólares
                elif ticker in self.instrumentos_by_tickerD:
                    inst = self.instrumentos_by_tickerD[ticker]
                    inst["prCompraDolar"] = pr_bid
                    inst["prVentaDolar"] = pr_ask
                    inst["siCompraDolar"] = si_bid
                    inst["siVentaDolar"] = si_ask

    def get_snapshot(self) -> List[Dict]:
        """CONC-01: retorna copia profunda thread-safe del estado actual."""
        with self._lock:
            return copy.deepcopy(list(self.instrumentos_by_ticker.values()))


class Executer:
    def __init__(
        self,
        account: str,
        client: CocosMatrizClient,
        ratio: float = ARBITER_RATIO,
        require_confirmation: bool = REQUIRE_CONFIRMATION,
        data_manager: Optional["DataManager"] = None,
    ):
        self.account = account
        self.client = client
        self.ratio = ratio
        self.require_confirmation = require_confirmation
        # Acceso a market data viva para refrescar cotizaciones antes de la pata 2
        self.data_manager = data_manager
        self.tracked_opportunities: Set[Tuple[str, str]] = set()
        self._ultimo_snapshot: List[Dict] = []
        # Contexto del arbitraje / pata en curso (se vuelca en el CSV de pendientes)
        self._arb_ctx: Dict = {}
        self._pata_ctx: Dict = {}
        # P&L del día: se recupera de pnl_diario.csv para no perder el acumulado si se reinicia el bot
        self._pnl_fecha, self._pnl_acumulado, self._pnl_ops = self._cargar_pnl_del_dia()
        logger.info(
            f"💰 P&L acumulado del día {self._pnl_fecha}: {self._pnl_acumulado:+,.2f} ARS "
            f"({self._pnl_ops} operaciones)"
        )

    # ====================== P&L DIARIO ======================
    @staticmethod
    def _cargar_pnl_del_dia() -> Tuple[str, float, int]:
        """Suma el pnl_ars de las filas de hoy en pnl_diario.csv (0 si el archivo no existe)."""
        hoy = datetime.now().strftime("%Y-%m-%d")
        total, ops = 0.0, 0
        try:
            with open(PNL_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("fecha") == hoy:
                        total += float(row.get("pnl_ars") or 0)
                        ops += 1
        except FileNotFoundError:
            logger.warning(f"No existe {PNL_PATH}: el P&L del día arranca en 0.")
        return hoy, total, ops

    def _registrar_pnl(
        self,
        estado: str,
        modo1: str,
        instrumento1: str,
        instrumento2: str,
        tc_pata1: float,
        tc_pata2: Optional[float],
        usd_arbitrado: float,
        usd_sin_cerrar: float,
    ) -> None:
        """
        Calcula el P&L realizado del arbitraje y lo acumula en el día.
        - Pata 1 DOLARIZAR (compré USD a tc_pata1, vendí a tc_pata2): pnl = USD × (tc_pata2 − tc_pata1)
        - Pata 1 PESIFICAR (vendí USD a tc_pata1, recompré a tc_pata2): pnl = USD × (tc_pata1 − tc_pata2)
        """
        ahora = datetime.now()
        hoy = ahora.strftime("%Y-%m-%d")
        if hoy != self._pnl_fecha:  # cambio de día con el bot corriendo
            self._pnl_fecha, self._pnl_acumulado, self._pnl_ops = hoy, 0.0, 0

        if tc_pata2 is None or usd_arbitrado <= 0:
            pnl = 0.0
        elif modo1 == "DOLARIZAR":
            pnl = usd_arbitrado * (tc_pata2 - tc_pata1)
        else:
            pnl = usd_arbitrado * (tc_pata1 - tc_pata2)
        self._pnl_acumulado += pnl
        self._pnl_ops += 1

        fila = {
            "fecha": hoy,
            "hora": ahora.strftime("%H:%M:%S"),
            "arbitraje_id": self._arb_ctx.get("arbitraje_id", ""),
            "estado": estado,
            "modo_pata1": modo1,
            "instrumento_pata1": instrumento1,
            "instrumento_pata2": instrumento2,
            "tc_pata1": f"{tc_pata1:.4f}",
            "tc_pata2": f"{tc_pata2:.4f}" if tc_pata2 else "",
            "usd_arbitrado": f"{usd_arbitrado:.2f}",
            "usd_sin_cerrar": f"{usd_sin_cerrar:.2f}",
            "pnl_ars": f"{pnl:.2f}",
            "pnl_acumulado_dia": f"{self._pnl_acumulado:.2f}",
            "operaciones_dia": self._pnl_ops,
        }
        with open(PNL_PATH, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=PNL_COLUMNS).writerow(fila)
        logger.info(
            f"💰 P&L {fila['arbitraje_id']} [{estado}]: {pnl:+,.2f} ARS sobre USD {usd_arbitrado:.2f} | "
            f"Acumulado del día ({self._pnl_ops} ops): {self._pnl_acumulado:+,.2f} ARS"
        )

    def _registrar_posicion_pendiente(
        self,
        instrumento: Dict,
        cantidad,
        razon: str,
        precio_compra: Optional[float] = None,
        precio_intento_venta: Optional[float] = None,
        accion_manual: str = "Revisar y ejecutar manualmente en broker",
        **extra,
    ) -> None:
        """
        Persiste en results/posiciones_pendientes.csv una posición abierta para recuperación manual.
        Completa automáticamente arbitraje_id, pata, modo y tc_pata1 desde el contexto en curso;
        `extra` permite informar cualquier otra columna de PENDIENTES_COLUMNS.
        """
        def fmt(v):
            if v is None:
                return ""
            if isinstance(v, float):
                return f"{v:.4f}"
            return v

        registro: Dict[str, Any] = {c: "" for c in PENDIENTES_COLUMNS}
        registro.update({k: fmt(v) for k, v in self._arb_ctx.items() if k in registro})
        registro.update({k: fmt(v) for k, v in self._pata_ctx.items() if k in registro})
        registro.update({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "razon": razon,
            "ticker": instrumento.get("ticker"),
            "tickerD": instrumento.get("tickerD"),
            "cantidad_nominales": cantidad,
            "precio_compra": fmt(precio_compra),
            "precio_intento_venta": fmt(precio_intento_venta),
            "accion_manual_requerida": accion_manual,
        })
        for k, v in extra.items():
            if k in registro:
                registro[k] = fmt(v)
            else:
                logger.debug(f"Columna desconocida en pendientes: {k}")

        with open(POSICIONES_PENDIENTES_PATH, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=PENDIENTES_COLUMNS).writerow(registro)
        logger.critical(
            f"🚨 POSICIÓN INCONCLUSA REGISTRADA para ejecución manual en {POSICIONES_PENDIENTES_PATH}: {registro}"
        )

    @staticmethod
    def _calculate_ratios(instrumentos: List[Dict]) -> None:
        """
        Calcula ratios USD/pesos para todos los instrumentos.
        - pesos_a_USD: Tipo de cambio implícito al dolarizar (comprar en pesos a prVentaPesos, vender en dólares a prCompraDolar).
        - USD_a_pesos: Tipo de cambio implícito al pesificar (comprar en dólares a prVentaDolar, vender en pesos a prCompraPesos).
        """
        for item in instrumentos:
            pr_compra_pesos = item.get("prCompraPesos")
            pr_venta_dolar = item.get("prVentaDolar")
            pr_venta_pesos = item.get("prVentaPesos")
            pr_compra_dolar = item.get("prCompraDolar")

            item["USD_a_pesos"] = (
                float(pr_compra_pesos / pr_venta_dolar)
                if pr_compra_pesos and pr_venta_dolar and pr_compra_pesos > 0 and pr_venta_dolar > 0
                else None
            )
            item["pesos_a_USD"] = (
                float(pr_venta_pesos / pr_compra_dolar)
                if pr_venta_pesos and pr_compra_dolar and pr_venta_pesos > 0 and pr_compra_dolar > 0
                else None
            )

    @staticmethod
    def get_al30_benchmarks(instrumentos: List[Dict]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Obtiene los ratios de AL30 como consensos de mercado:
        - pesos_a_USD_al30: tasa para dolarizarse con AL30 (costo de comprar USD en AL30).
        - USD_a_pesos_al30: tasa para pesificarse con AL30 (ingreso al vender USD en AL30).
        - mid_al30: punto medio entre ambas tasas.
        """
        al30 = next((i for i in instrumentos if i["ticker"] == "AL30"), None)
        if not al30:
            return None, None, None

        p_to_u = al30.get("pesos_a_USD")
        u_to_p = al30.get("USD_a_pesos")
        mid = (p_to_u + u_to_p) / 2.0 if (p_to_u and u_to_p) else (p_to_u or u_to_p)
        return p_to_u, u_to_p, mid

    @staticmethod
    def compute_market_benchmark(instrumentos: List[Dict]) -> Tuple[Optional[float], Optional[float]]:
        """
        Calcula la mediana y desviación estándar del tipo de cambio MEP en el universo de instrumentos.
        Se utiliza como fallback en caso de que AL30 no tenga cotizaciones activas en el ciclo.
        """
        rates: List[float] = []
        for inst in instrumentos:
            p_to_u = inst.get("pesos_a_USD")
            u_to_p = inst.get("USD_a_pesos")
            if p_to_u and u_to_p and p_to_u > 1 and u_to_p > 1:
                rates.append((p_to_u + u_to_p) / 2.0)
            elif p_to_u and p_to_u > 1:
                rates.append(p_to_u)
            elif u_to_p and u_to_p > 1:
                rates.append(u_to_p)

        if not rates:
            return None, None

        median_rate = float(statistics.median(rates))
        stdev_rate = float(statistics.stdev(rates)) if len(rates) > 1 else 0.0
        return median_rate, stdev_rate

    @staticmethod
    def calculate_leg_priority(
        inst_A: Dict,
        inst_B: Dict,
        consenso_mep: Optional[float],
        fallback_mep: Optional[float] = None,
    ) -> Dict:
        """
        Determina qué pata del arbitraje se ejecuta primero.

        Consenso de mercado: dólar MEP promedio del AL30
            consenso = (pesos_a_USD_al30 + USD_a_pesos_al30) / 2
        Si AL30 no tiene ambas puntas, se usa la mediana del universo (fallback_mep) y,
        en última instancia, el punto medio del propio par.

        Desviación favorable de cada pata respecto al consenso:
        - dev_A (Dolarizador) = (consenso - pesos_a_USD_A) / consenso
          → cuánto más barato que el consenso se compran USD con A.
        - dev_B (Pesificador) = (USD_a_pesos_B - consenso) / consenso
          → cuánto más caro que el consenso se venden USD con B.

        Reglas, en orden de precedencia:
        1. Si uno de los instrumentos es AL30, se comienza por el otro.
        2. Se comienza por la pata con mayor desviación respecto al consenso: es la punta
           más atractiva para el resto del mercado y la que antes puede desaparecer.
        3. Empate técnico (|dev_A - dev_B| <= TOLERANCE): primero la pata con menor volumen en libro.
        """
        pesos_a_USD_A = inst_A["pesos_a_USD"]
        USD_a_pesos_B = inst_B["USD_a_pesos"]
        ticker_A = inst_A["ticker"]
        ticker_B = inst_B["ticker"]

        if consenso_mep and consenso_mep > 1:
            consenso, fuente = consenso_mep, "MEP promedio AL30"
        elif fallback_mep and fallback_mep > 1:
            consenso, fuente = fallback_mep, "mediana del universo"
        else:
            consenso, fuente = (pesos_a_USD_A + USD_a_pesos_B) / 2.0, "punto medio del par"

        dev_A = (consenso - pesos_a_USD_A) / consenso
        dev_B = (USD_a_pesos_B - consenso) / consenso
        diff = dev_A - dev_B
        devs = f"dev {ticker_A}={dev_A*100:+.2f}% / dev {ticker_B}={dev_B*100:+.2f}% vs {fuente} {consenso:.2f}"

        if ticker_B == "AL30" and ticker_A != "AL30":
            priority = "DOLARIZADOR"
            reason = f"B es AL30: se comienza por {ticker_A} ({devs})"
        elif ticker_A == "AL30" and ticker_B != "AL30":
            priority = "PESIFICADOR"
            reason = f"A es AL30: se comienza por {ticker_B} ({devs})"
        elif abs(diff) <= TOLERANCE:
            # Desempate por volumen disponible en libro (la punta con menor tamaño va primero)
            size_A = min(inst_A.get("siVentaPesos") or 0, inst_A.get("siCompraDolar") or 0)
            size_B = min(inst_B.get("siVentaDolar") or 0, inst_B.get("siCompraPesos") or 0)
            if size_A < size_B:
                priority = "DOLARIZADOR"
                reason = f"Empate técnico: {ticker_A} tiene menor volumen ({size_A} vs {size_B}) ({devs})"
            else:
                priority = "PESIFICADOR"
                reason = f"Empate técnico: {ticker_B} tiene menor volumen ({size_B} vs {size_A}) ({devs})"
        elif diff > 0:
            priority = "DOLARIZADOR"
            reason = f"Dolarizador {ticker_A} con mayor desviación ({devs})"
        else:
            priority = "PESIFICADOR"
            reason = f"Pesificador {ticker_B} con mayor desviación ({devs})"

        return {
            "priority": priority,
            "dev_A": dev_A,
            "dev_B": dev_B,
            "consenso": consenso,
            "fuente_consenso": fuente,
            "reason": reason,
        }

    def execute(
        self,
        instrumentos: List[Dict],
        **kwargs,
    ) -> None:
        """
        Evalúa oportunidades de arbitraje entre TODOS los instrumentos (no solo contra AL30),
        considerando precios, montos mínimos (min_quant), tamaños/volúmenes y prioridad basada
        en los ratios de AL30 como consensos de mercado.
        """
        ratio = self.ratio
        self._calculate_ratios(instrumentos)
        self._ultimo_snapshot = instrumentos  # fallback si no hay DataManager para refrescar

        # Ratios de AL30 como consensos de mercado
        p_to_u_al30, u_to_p_al30, mid_al30 = self.get_al30_benchmarks(instrumentos)
        fallback_mep, stdev_mep = self.compute_market_benchmark(instrumentos)

        # Consenso de mercado = MEP promedio de AL30 (solo si cotizan ambas puntas)
        consenso_al30 = mid_al30 if (p_to_u_al30 and u_to_p_al30) else None

        if consenso_al30:
            logger.info(
                f"Consenso de mercado (MEP promedio AL30): {consenso_al30:.2f} ARS "
                f"[Dolarizar: {p_to_u_al30:.2f} | Pesificar: {u_to_p_al30:.2f}]."
            )
        else:
            fb_str = f"{fallback_mep:.2f}" if fallback_mep else "N/A"
            logger.warning(f"AL30 sin cotizaciones completas. Usando mediana de universo como fallback ({fb_str} ARS).")

        candidates: List[Dict] = []

        # Evaluar todos los pares posibles (A, B) con A != B
        # A = DOLARIZADOR: Comprar A en pesos a prVentaPesos_A, vender A en dólares a prCompraDolar_A
        # B = PESIFICADOR: Comprar B en dólares a prVentaDolar_B, vender B en pesos a prCompraPesos_B
        for inst_A in instrumentos:
            pesos_a_USD_A = inst_A.get("pesos_a_USD")
            if pesos_a_USD_A is None or pesos_a_USD_A <= 1:
                continue

            si_A_pesos = inst_A.get("siVentaPesos")
            si_A_dolar = inst_A.get("siCompraDolar")
            if not si_A_pesos or not si_A_dolar or si_A_pesos <= 0 or si_A_dolar <= 0:
                continue

            pr_compra_dolar_A = inst_A.get("prCompraDolar")
            pr_venta_pesos_A = inst_A.get("prVentaPesos")
            if not pr_compra_dolar_A or not pr_venta_pesos_A or pr_compra_dolar_A <= 0 or pr_venta_pesos_A <= 0:
                continue

            min_quant_A = inst_A.get("min_quant", 1)
            max_quant_A = inst_A.get("max_quant", 500)
            max_A = int(min(si_A_pesos, si_A_dolar, max_quant_A))
            # Si el instrumento A no alcanza su propio monto mínimo disponible, no operar
            if max_A < min_quant_A:
                continue

            for inst_B in instrumentos:
                if inst_A["ticker"] == inst_B["ticker"]:
                    continue

                USD_a_pesos_B = inst_B.get("USD_a_pesos")
                if USD_a_pesos_B is None or USD_a_pesos_B <= 1:
                    continue

                # Condición de arbitraje: vender dólares en B debe pagar más que comprar dólares en A (ajustado por ratio)
                if pesos_a_USD_A * ratio >= USD_a_pesos_B:
                    continue

                si_B_dolar = inst_B.get("siVentaDolar")
                si_B_pesos = inst_B.get("siCompraPesos")
                if not si_B_dolar or not si_B_pesos or si_B_dolar <= 0 or si_B_pesos <= 0:
                    continue

                pr_venta_dolar_B = inst_B.get("prVentaDolar")
                pr_compra_pesos_B = inst_B.get("prCompraPesos")
                if not pr_venta_dolar_B or not pr_compra_pesos_B or pr_venta_dolar_B <= 0 or pr_compra_pesos_B <= 0:
                    continue

                min_quant_B = inst_B.get("min_quant", 1)
                max_quant_B = inst_B.get("max_quant", 500)
                max_B = int(min(si_B_dolar, si_B_pesos, max_quant_B))
                # Si el instrumento B no alcanza su propio monto mínimo disponible, no operar
                if max_B < min_quant_B:
                    continue

                # ========================================================
                # CÁLCULO DE VOLUMEN / SIZE ENTRE LAS 4 PUNTAS INTERVINIENTES
                # Y VALIDACIÓN DE MONTOS MÍNIMOS (min_quant)
                # NOTA: En BYMA las cotizaciones de bonos/ONs son por cada 100 nominales
                # ========================================================
                # 1) Máximo volumen en USD que A puede generar y B puede absorber
                max_usd_A = (max_A * pr_compra_dolar_A) / BOND_LOT_SIZE
                max_usd_B = (max_B * pr_venta_dolar_B) / BOND_LOT_SIZE

                # Mínimo volumen en USD requerido para cumplir con min_quant de cada instrumento
                min_usd_A = (min_quant_A * pr_compra_dolar_A) / BOND_LOT_SIZE
                min_usd_B = (min_quant_B * pr_venta_dolar_B) / BOND_LOT_SIZE

                # Si la punta contraria no llega al monto mínimo de alguno de los instrumentos, NO arbitrar:
                # - B debe poder absorber al menos lo que genera el mínimo de A (max_usd_B >= min_usd_A)
                # - A debe poder generar al menos lo que requiere el mínimo de B (max_usd_A >= min_usd_B)
                if max_usd_B < min_usd_A or max_usd_A < min_usd_B:
                    continue

                # 3) Cuello de botella en USD para ambas patas
                usable_usd = min(max_usd_A, max_usd_B)

                val_usd_A = pr_compra_dolar_A / BOND_LOT_SIZE
                val_usd_B = pr_venta_dolar_B / BOND_LOT_SIZE

                # Unidades de B que se pueden comprar con usable_usd
                quant_B = int(usable_usd / val_usd_B)
                if quant_B < min_quant_B:
                    continue

                # USD requerido para comprar quant_B unidades de B
                usd_needed = quant_B * val_usd_B

                # Unidades de A a vender para financiar al menos usd_needed
                quant_A = int(math.ceil(usd_needed / val_usd_A))

                # Asegurar que quant_A no exceda max_A debido al redondeo
                if quant_A > max_A:
                    quant_A = max_A
                    quant_B = int((quant_A * val_usd_A) / val_usd_B)

                # Optimización de calce: si los USD generados por quant_A permiten comprar
                # más unidades de B sin superar max_B, aumentar quant_B para minimizar el remanente en USD
                usd_gen_A = quant_A * val_usd_A
                quant_B_opt = int(usd_gen_A / val_usd_B)
                if min_quant_B <= quant_B_opt <= max_B:
                    quant_B = quant_B_opt

                # Verificar estrictamente que ambas cantidades alcancen sus mínimos
                if quant_A < min_quant_A or quant_B < min_quant_B:
                    continue

                # Dólares efectivamente arbitrados (calzados entre ambas patas)
                usd_arbitrado = min(quant_A * val_usd_A, quant_B * val_usd_B)
                if usd_arbitrado <= 0:
                    continue

                # Remanente en USD que queda sin pesificar por redondeo discreto de nominales
                rem_usd = abs((quant_A * val_usd_A) - (quant_B * val_usd_B))

                # Filtro de descalce excesivo: si la operación es muy pequeña (< $10 USD)
                # y el remanente representa más del 20% del volumen arbitrado, descartar
                if usd_arbitrado < 10.0 and (rem_usd / usd_arbitrado) > 0.20:
                    continue

                # Métricas de la oportunidad basadas estrictamente en el capital arbitrado:
                # Comprar usd_arbitrado en A a pesos_a_USD_A y vender usd_arbitrado en B a USD_a_pesos_B
                spread = USD_a_pesos_B / pesos_a_USD_A
                cost_ars = usd_arbitrado * pesos_a_USD_A
                rev_ars = usd_arbitrado * USD_a_pesos_B
                profit_ars = rev_ars - cost_ars
                profit_pct = ((USD_a_pesos_B / pesos_a_USD_A) - 1.0) * 100.0

                # Salvaguarda: solo agregar si la ganancia estimada y el spread son estrictamente positivos
                if profit_ars <= 0 or profit_pct <= 0:
                    continue

                # Evaluación de pata prioritaria según el consenso de mercado (AL30)
                priority_info = self.calculate_leg_priority(
                    inst_A, inst_B, consenso_al30, fallback_mep
                )

                candidates.append({
                    "dolarizador": inst_A,
                    "pesificador": inst_B,
                    "quant_dol": quant_A,
                    "quant_pes": quant_B,
                    "spread": spread,
                    "profit_ars": profit_ars,
                    "profit_pct": profit_pct,
                    "usd_amount": usd_arbitrado,
                    "rem_usd": rem_usd,
                    "pair_key": (inst_A["ticker"], inst_B["ticker"]),
                    "priority_info": priority_info,
                })

        if not candidates:
            self.tracked_opportunities.clear()
            return

        # Ordenar oportunidades por mayor spread porcentual
        candidates.sort(key=lambda x: x["spread"], reverse=True)

        current_keys = {c["pair_key"] for c in candidates}
        self.tracked_opportunities.intersection_update(current_keys)

        for opp in candidates:
            pair_key = opp["pair_key"]
            rem_str = f" (Rem: ${opp['rem_usd']:.2f} USD)" if opp.get("rem_usd", 0) > 0.01 else ""
            if self.require_confirmation and pair_key not in self.tracked_opportunities:
                self.tracked_opportunities.add(pair_key)
                logger.info(
                    f"👀 Oportunidad detectada: Dolarizar {pair_key[0]} (TC: {opp['dolarizador']['pesos_a_USD']:.2f}, Q: {opp['quant_dol']}, Min: {opp['dolarizador'].get('min_quant', 1)}) "
                    f"-> Pesificar {pair_key[1]} (TC: {opp['pesificador']['USD_a_pesos']:.2f}, Q: {opp['quant_pes']}, Min: {opp['pesificador'].get('min_quant', 1)}) | "
                    f"Spread: {opp['spread']:.4f} | USD: ~{opp['usd_amount']:.2f}{rem_str} | "
                    f"Ganancia est.: ${opp['profit_ars']:.2f} ({opp['profit_pct']:.2f}%) | "
                    f"Prioridad sugerida: {opp['priority_info']['priority']} ({opp['priority_info']['reason']}). "
                    f"Esperando confirmación en siguiente ciclo..."
                )
                continue

            # Oportunidad confirmada -> Ejecutar arbitraje
            self.execute_arbitrage(opp)
            self.tracked_opportunities.discard(pair_key)
            return  # Ejecutar solo una operación por ciclo

    def execute_arbitrage(self, opp: Dict) -> None:
        """
        Ejecuta las dos patas del arbitraje comenzando por la pata prioritaria
        (ver calculate_leg_priority). La pata 2 se dimensiona con lo efectivamente
        calzado en la pata 1, respetando max_quant y min_quant. Antes de la pata 2 se
        refresca la market data: si las puntas originales ya no están, se cierra con la
        mejor alternativa que cumpla CLOSE_RATIO o, si no hay, se registra en pendientes.
        """
        dol = opp["dolarizador"]
        pes = opp["pesificador"]
        quant_dol = opp["quant_dol"]
        quant_pes = opp["quant_pes"]
        priority_info = opp["priority_info"]
        priority = priority_info["priority"]
        rem_str = f" (Rem: ${opp['rem_usd']:.2f} USD)" if opp.get("rem_usd", 0) > 0.01 else ""

        if priority == "DOLARIZADOR":
            modo1, inst1, q1 = "DOLARIZAR", dol, quant_dol
            modo2, inst2 = "PESIFICAR", pes
        else:
            modo1, inst1, q1 = "PESIFICAR", pes, quant_pes
            modo2, inst2 = "DOLARIZAR", dol

        logger.info(
            f"🚀 EJECUTANDO ARBITRAJE: Dolarizar {dol['ticker']} ({quant_dol} u. [min: {dol.get('min_quant', 1)}], "
            f"TC {dol['pesos_a_USD']:.2f}) ↔ Pesificar {pes['ticker']} ({quant_pes} u. [min: {pes.get('min_quant', 1)}], "
            f"TC {pes['USD_a_pesos']:.2f}) | Spread: {opp['spread']:.4f} | USD: ~{opp['usd_amount']:.2f}{rem_str} | "
            f"Ganancia est.: ${opp['profit_ars']:.2f} ({opp['profit_pct']:.2f}%) | "
            f"Comienza: {modo1} {inst1['ticker']} ({priority_info['reason']})"
        )

        self._arb_ctx = {
            "arbitraje_id": f"{datetime.now():%Y%m%d-%H%M%S}-{dol['ticker']}-{pes['ticker']}",
            "ratio_requerido": CLOSE_RATIO,
        }
        try:
            # ============================ PATA 1 ============================
            r1 = self._ejecutar_pata(inst1, modo1, q1, es_primera=True)
            if r1["sell_qty"] <= 0:
                logger.info(
                    f"Arbitraje {dol['ticker']} ↔ {pes['ticker']} finalizado sin pata 2: "
                    f"la pata 1 ({modo1} {inst1['ticker']}) no dejó cantidad calzada."
                )
                return

            # ======== TIPO DE CAMBIO REAL DE PATA 1 Y USD A CERRAR EN PATA 2 ========
            if modo1 == "DOLARIZAR":
                # Se compraron USD: costo = precio ARS de compra / precio USD de venta
                px_ars = r1["buy_px"] or dol["prVentaPesos"]
                px_usd = r1["sell_px"] or dol["prCompraDolar"]
                q_orig = quant_pes
            else:
                # Se vendieron USD: ingreso = precio ARS de venta / precio USD de compra
                px_usd = r1["buy_px"] or pes["prVentaDolar"]
                px_ars = r1["sell_px"] or pes["prCompraPesos"]
                q_orig = quant_dol
            tc_pata1 = px_ars / px_usd
            usd_pata1 = (r1["sell_qty"] * px_usd) / BOND_LOT_SIZE
            self._arb_ctx["tc_pata1"] = tc_pata1
            logger.info(
                f"Pata 1 cerrada: TC real {tc_pata1:.2f} ARS/USD | USD a cerrar en pata 2: {usd_pata1:.2f} | "
                f"Ratio mínimo de cierre: {CLOSE_RATIO}"
            )

            # ======== PATA 2: refrescar market data y elegir el cierre ========
            cierre, mejor_visto = self._seleccionar_cierre(
                modo2, inst2, inst1["ticker"], q_orig, usd_pata1, tc_pata1
            )
            if cierre is None:
                self._registrar_cierre_pendiente(
                    modo2, inst2, usd_pata1, tc_pata1, mejor_visto, "SIN_CIERRE_DENTRO_DE_RATIO"
                )
                self._registrar_pnl(
                    "SIN_CIERRE", modo1, inst1["ticker"], "", tc_pata1, None, 0.0, usd_pata1
                )
                return

            inst_c, q2 = cierre["inst"], cierre["q"]
            r2 = self._ejecutar_pata(inst_c, modo2, q2, es_primera=False)

            # Si el cierre elegido no alcanzaba a cubrir todos los USD (por size), registrar el remanente
            usd_sin_cubrir = usd_pata1 - cierre["usd_cubierto"]
            if not cierre["completo"]:
                self._registrar_cierre_pendiente(
                    modo2, inst_c, usd_sin_cubrir, tc_pata1, cierre, "CIERRE_PARCIAL_POR_SIZE"
                )

            # ======== P&L REALIZADO (con precios reales de ambas patas) ========
            tc_pata2: Optional[float] = None
            usd_pata2 = 0.0
            if r2["sell_qty"] > 0 and r2["buy_px"] and r2["sell_px"]:
                if modo2 == "PESIFICAR":
                    # Compré tickerD con USD y vendí ticker por pesos
                    tc_pata2 = r2["sell_px"] / r2["buy_px"]
                    usd_pata2 = (r2["sell_qty"] * r2["buy_px"]) / BOND_LOT_SIZE
                else:
                    # Compré ticker con pesos y vendí tickerD por USD
                    tc_pata2 = r2["buy_px"] / r2["sell_px"]
                    usd_pata2 = (r2["sell_qty"] * r2["sell_px"]) / BOND_LOT_SIZE
            usd_arbitrado = min(usd_pata1, usd_pata2)
            usd_sin_cerrar = max(usd_pata1 - usd_arbitrado, 0.0)
            if usd_arbitrado <= 0:
                estado = "PATA2_SIN_EJECUCION"
            elif cierre["completo"] and r2["sell_qty"] >= r2["buy_qty"]:
                estado = "COMPLETO"
            else:
                estado = "PARCIAL"
            self._registrar_pnl(
                estado, modo1, inst1["ticker"], inst_c["ticker"],
                tc_pata1, tc_pata2, usd_arbitrado, usd_sin_cerrar,
            )

            logger.info(
                f"🏁 Resultado arbitraje {dol['ticker']} ↔ {pes['ticker']}: "
                f"Pata 1 {modo1} {inst1['ticker']} compra={r1['buy_qty']} venta={r1['sell_qty']} "
                f"deshacer={r1['unwind_qty']} (TC {tc_pata1:.2f}) | "
                f"Pata 2 {modo2} {inst_c['ticker']} (q={q2}, TC {cierre['tc']:.2f}, ratio {cierre['ratio']:.5f}"
                f"{', ALTERNATIVA a ' + inst2['ticker'] if inst_c['ticker'] != inst2['ticker'] else ''}) "
                f"compra={r2['buy_qty']} venta={r2['sell_qty']} en_mercado={r2['pendiente_mercado']}"
            )

        except Exception as exc:
            # Las excepciones de órdenes se manejan dentro de _ejecutar_pata; esto cubre errores de cálculo.
            logger.exception(f"💥 Excepción inesperada en execute_arbitrage ({dol['ticker']} ↔ {pes['ticker']}): {exc}")
            self._registrar_posicion_pendiente(
                inst1, q1, f"EXCEPCION_{type(exc).__name__}",
                accion_manual="Error fuera de la ejecución de órdenes. Revisar órdenes y cartera en broker.",
            )
        finally:
            self._arb_ctx, self._pata_ctx = {}, {}

    # ====================== CIERRE DE PATA 2 CON DATOS ACTUALIZADOS ======================
    def _market_actualizado(self) -> List[Dict]:
        """Snapshot fresco del DataManager con ratios recalculados (o el último snapshot si no hay DataManager)."""
        if self.data_manager is None:
            logger.warning("Sin DataManager: la pata 2 se evalúa con el snapshot del inicio del ciclo.")
            return self._ultimo_snapshot
        mercado = self.data_manager.get_snapshot()
        self._calculate_ratios(mercado)
        return mercado

    @staticmethod
    def _evaluar_cierre(
        inst: Dict, modo: str, usd_obj: float, tc_pata1: float, quant_cap: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Evalúa un instrumento como cierre de pata 2 con sus puntas actuales.
        - PESIFICAR (vender los USD de pata 1): ratio = USD_a_pesos / tc_pata1
        - DOLARIZAR (recomprar los USD de pata 1): ratio = tc_pata1 / pesos_a_USD
        La cantidad respeta las mismas reglas de pata 2: USD a cubrir (floor al pesificar, ceil al
        dolarizar), max_quant (AL30_MAX_QUANT_DEFAULT para AL30), tamaño de ambas puntas y min_quant.
        """
        if modo == "PESIFICAR":
            px_usd, px_ars = inst.get("prVentaDolar"), inst.get("prCompraPesos")
            si_usd, si_ars = inst.get("siVentaDolar"), inst.get("siCompraPesos")
            tc = inst.get("USD_a_pesos")
        else:
            px_usd, px_ars = inst.get("prCompraDolar"), inst.get("prVentaPesos")
            si_usd, si_ars = inst.get("siCompraDolar"), inst.get("siVentaPesos")
            tc = inst.get("pesos_a_USD")
        # Chequeo explícito de None (permite al type checker descartar None en el resto del método)
        if (
            px_usd is None or px_ars is None or si_usd is None or si_ars is None or tc is None
            or min(px_usd, px_ars, si_usd, si_ars, tc) <= 0
        ):
            return None

        ratio = tc / tc_pata1 if modo == "PESIFICAR" else tc_pata1 / tc
        max_q = AL30_MAX_QUANT_DEFAULT if inst["ticker"] == "AL30" else inst.get("max_quant", 500)
        if modo == "PESIFICAR":
            q_usd = int((usd_obj * BOND_LOT_SIZE) / px_usd)
        else:
            q_usd = int(math.ceil((usd_obj * BOND_LOT_SIZE) / px_usd))
        q = min(q_usd, int(si_usd), int(si_ars), max_q)
        if quant_cap is not None:
            q = min(q, quant_cap)
        usd_cubierto = min((q * px_usd) / BOND_LOT_SIZE, usd_obj)
        return {
            "inst": inst,
            "q": q,
            "tc": tc,
            "ratio": ratio,
            "px_usd": px_usd,
            "usd_cubierto": usd_cubierto,
            # Cobertura completa: lo que falta es menor al valor de 1 nominal (redondeo inevitable)
            "completo": (usd_obj - usd_cubierto) < (px_usd / BOND_LOT_SIZE),
            "valido": q >= inst.get("min_quant", 1),
        }

    @staticmethod
    def _puntas_vigentes(original: Dict, fresco: Dict, modo: str) -> bool:
        """True si las puntas usadas al detectar la oportunidad siguen al mismo precio o mejor."""
        if modo == "PESIFICAR":
            pares = [("prVentaDolar", "le"), ("prCompraPesos", "ge")]
        else:
            pares = [("prVentaPesos", "le"), ("prCompraDolar", "ge")]
        for campo, cmp in pares:
            viejo, nuevo = original.get(campo), fresco.get(campo)
            if not viejo or not nuevo:
                return False
            if (cmp == "le" and nuevo > viejo) or (cmp == "ge" and nuevo < viejo):
                return False
        return True

    def _seleccionar_cierre(
        self,
        modo2: str,
        inst2: Dict,
        ticker_pata1: str,
        q_orig: int,
        usd_obj: float,
        tc_pata1: float,
    ) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        Refresca la market data y elige con qué instrumento cerrar la pata 2.
        1. Si las puntas originales de inst2 siguen vigentes (mismo precio o mejor), con size
           suficiente y ratio >= CLOSE_RATIO, se cierra con inst2 a precios actualizados.
        2. Si no, se evalúan todos los instrumentos (excepto el de pata 1) con sus puntas actuales.
           Entre los que cumplen ratio >= CLOSE_RATIO y min_quant: si alguno cubre todos los USD
           (size), gana el de mejor ratio (price); si ninguno cubre todo, gana el que más USD cubre.
        Retorna (cierre_elegido | None, mejor_visto) — mejor_visto es el de mejor ratio aunque no
        cumpla el tope, para informarlo en pendientes.
        """
        tag = f"[Pata 2 {modo2}]"
        mercado = self._market_actualizado()
        por_ticker = {i["ticker"]: i for i in mercado}
        fresco = por_ticker.get(inst2["ticker"])

        # ---- 1) ¿Siguen las puntas originales? ----
        q_plan = None
        plan = self._evaluar_cierre(inst2, modo2, usd_obj, tc_pata1, q_orig)
        if plan:
            q_plan = plan["q"]
        if fresco:
            campos = (
                ("prVentaDolar", "prCompraPesos") if modo2 == "PESIFICAR" else ("prVentaPesos", "prCompraDolar")
            )
            logger.info(
                f"{tag} Market data {inst2['ticker']}: "
                + " | ".join(f"{c} {inst2.get(c)} → {fresco.get(c)}" for c in campos)
            )
            orig = self._evaluar_cierre(fresco, modo2, usd_obj, tc_pata1, q_orig)
            if (
                orig
                and orig["valido"]
                and orig["ratio"] >= CLOSE_RATIO
                and self._puntas_vigentes(inst2, fresco, modo2)
                and (q_plan is None or orig["q"] >= q_plan)
            ):
                logger.info(
                    f"{tag} Puntas de {inst2['ticker']} vigentes: se cierra según lo planeado "
                    f"(q={orig['q']}, TC {orig['tc']:.2f}, ratio {orig['ratio']:.5f})."
                )
                return orig, orig

        logger.warning(
            f"⚠️ {tag} Las puntas de {inst2['ticker']} cambiaron o no alcanzan. "
            f"Buscando la mejor alternativa con ratio >= {CLOSE_RATIO}..."
        )

        # ---- 2) Buscar la mejor alternativa con datos actualizados ----
        evaluados: List[Dict] = []
        for inst in mercado:
            if inst["ticker"] == ticker_pata1:
                continue
            cap = q_orig if inst["ticker"] == inst2["ticker"] else None
            ev = self._evaluar_cierre(inst, modo2, usd_obj, tc_pata1, cap)
            if ev and ev["valido"]:
                evaluados.append(ev)

        mejor_visto = max(evaluados, key=lambda e: e["ratio"], default=None)
        aptos = [e for e in evaluados if e["ratio"] >= CLOSE_RATIO]
        # Primero los que cubren todo el monto (entre ellos decide el ratio: price);
        # si ninguno cubre todo, el que más USD cubre (size) y luego el ratio.
        aptos.sort(
            key=lambda e: (e["completo"], 0.0 if e["completo"] else e["usd_cubierto"], e["ratio"]),
            reverse=True,
        )

        for e in aptos[:3]:
            logger.info(
                f"{tag}   candidato {e['inst']['ticker']}: TC {e['tc']:.2f} | ratio {e['ratio']:.5f} | "
                f"q={e['q']} | cubre USD {e['usd_cubierto']:.2f}/{usd_obj:.2f}"
            )
        if not aptos:
            mv = (
                f"mejor visto {mejor_visto['inst']['ticker']} ratio {mejor_visto['ratio']:.5f}"
                if mejor_visto else "sin puntas operables"
            )
            logger.error(f"❌ {tag} Ninguna alternativa cumple ratio >= {CLOSE_RATIO} ({mv}).")
            return None, mejor_visto

        elegido = aptos[0]
        logger.info(
            f"✅ {tag} Cierre elegido: {elegido['inst']['ticker']} "
            f"(TC {elegido['tc']:.2f}, ratio {elegido['ratio']:.5f}, q={elegido['q']})."
        )
        return elegido, mejor_visto

    def _registrar_cierre_pendiente(
        self,
        modo2: str,
        inst: Dict,
        usd_pendiente: float,
        tc_pata1: float,
        mejor: Optional[Dict],
        razon: str,
    ) -> None:
        """Registra en pendientes la moneda que quedó sin convertir porque la pata 2 no pudo cerrarse."""
        if modo2 == "PESIFICAR":
            moneda, monto = "USD", usd_pendiente
            accion = (
                f"Pesificar USD {usd_pendiente:.2f}: vender USD a TC >= {tc_pata1 * CLOSE_RATIO:.2f} ARS/USD "
                f"para respetar el ratio {CLOSE_RATIO} (TC pata 1: {tc_pata1:.2f})."
            )
        else:
            moneda, monto = "ARS", usd_pendiente * tc_pata1
            accion = (
                f"Dolarizar ARS {monto:,.2f} para recuperar USD {usd_pendiente:.2f}: comprar USD a TC <= "
                f"{tc_pata1 / CLOSE_RATIO:.2f} ARS/USD para respetar el ratio {CLOSE_RATIO} (TC pata 1: {tc_pata1:.2f})."
            )
        self._registrar_posicion_pendiente(
            inst,
            "",
            razon,
            accion_manual=accion,
            pata="2",
            modo=modo2,
            tipo_pendiente="MONEDA",
            moneda_pendiente=moneda,
            monto_pendiente=float(monto),
            usd_pendiente=float(usd_pendiente),
            mejor_alternativa=mejor["inst"]["ticker"] if mejor else "",
            mejor_tc_disponible=mejor["tc"] if mejor else None,
            ratio_mejor_alternativa=mejor["ratio"] if mejor else None,
        )

    # ====================== HELPERS DE ÓRDENES ======================
    @staticmethod
    def _symbol(ticker: str) -> str:
        return f"MERV - XMEV - {ticker} - 24hs"

    def _enviar_orden(
        self, ticker: str, side: str, quantity: int, price: Optional[float], ord_type: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Envía una orden y retorna (clOrdId, proprietary) o (None, None) si falló."""
        resp = self.client.send_order(
            symbol=self._symbol(ticker),
            side=side,
            quantity=int(quantity),
            price=price if ord_type == "LIMIT" else None,
            ord_type=ord_type,
            market_id=MARKET_ID_DEFAULT,
            account=self.account,
            time_in_force=TIME_IN_FORCE_DEFAULT,
        )
        if "error" in resp:
            logger.error(f"Error enviando {side} {quantity} {ticker} ({ord_type} @ {price}): {resp.get('error')}")
            return None, None
        order = resp.get("order") or {}
        cl_ord_id, prop = order.get("clientId"), order.get("proprietary")
        if not cl_ord_id or not prop:
            logger.error(f"Respuesta sin clOrdId/proprietary al enviar {side} {ticker}: {resp}")
            return None, None
        px_str = f" @ {price}" if ord_type == "LIMIT" else ""
        logger.info(f"Orden enviada → {side} {quantity} {ticker} ({ord_type}{px_str}) clOrdId={cl_ord_id}")
        return cl_ord_id, prop

    def _consultar_orden(self, cl_ord_id: str, prop: str) -> Tuple[Optional[str], int, Optional[float]]:
        """Retorna (status, cumQty, avgPx) de una orden."""
        order = self.client.get_orders_by_clor_id(cl_ord_id, prop).get("order") or {}
        cum_qty = int(float(order.get("cumQty") or 0))
        avg_px = order.get("avgPx")
        avg_px = float(avg_px) if avg_px else None
        return order.get("status"), cum_qty, avg_px

    def _esperar_ejecucion(
        self, cl_ord_id: str, prop: str, quantity: int
    ) -> Tuple[Optional[str], int, Optional[float]]:
        """
        Polling hasta que la orden se ejecute completa, quede en un estado final o se agoten
        los reintentos (1 consulta + POLL_RETRIES reintentos). PARTIALLY_FILLED NO corta el polling.
        """
        status, cum_qty, avg_px = None, 0, None
        for intento in range(1 + POLL_RETRIES):
            if intento:
                time.sleep(POLL_INTERVAL_S)
            status, cum_qty, avg_px = self._consultar_orden(cl_ord_id, prop)
            if status in ESTADOS_FINALES or cum_qty >= quantity:
                break
        return status, cum_qty, avg_px

    def _cancelar_remanente(self, cl_ord_id: str, prop: str) -> Tuple[Optional[str], int, Optional[float]]:
        """
        Cancela el remanente de una orden y vuelve a consultarla hasta un estado final,
        para obtener el cumQty definitivo (evita la carrera entre un fill y el cancel).
        """
        resp = self.client.cancel_order(cl_ord_id, prop)
        if "error" in resp:
            logger.warning(f"Error cancelando {cl_ord_id}: {resp.get('error')}")
        status, cum_qty, avg_px = None, 0, None
        for intento in range(1 + POLL_RETRIES):
            if intento:
                time.sleep(POLL_INTERVAL_S)
            status, cum_qty, avg_px = self._consultar_orden(cl_ord_id, prop)
            if status in ESTADOS_FINALES:
                break
        if status not in ESTADOS_FINALES:
            logger.warning(
                f"⚠️ No se confirmó la cancelación de {cl_ord_id} (status={status}, cumQty={cum_qty}). "
                "El remanente podría seguir activo en mercado."
            )
        return status, cum_qty, avg_px

    # ====================== EJECUCIÓN DE UNA PATA ======================
    def _ejecutar_pata(self, inst: Dict, modo: str, quant: int, es_primera: bool) -> Dict:
        """
        Ejecuta una pata completa (compra + venta del mismo instrumento en monedas distintas).
        - DOLARIZAR: compra `ticker` en pesos → vende `tickerD` en dólares.
        - PESIFICAR: compra `tickerD` en dólares → vende `ticker` en pesos.
        REGLA: NUNCA se envía orden MARKET salvo que el ticker sea AL30.

        Compra (paso 1):
          - No ejecutada: pata 1 → solo se loguea y el arbitraje no continúa;
                          pata 2 → se loguea que la punta desapareció.
          - Parcial: se cancela el remanente y se continúa con lo ejecutado.
        Venta (paso 2):
          - Pata 1 incompleta: se cancela el remanente, se continúa con lo vendido y se crea
            una orden de venta por la diferencia en cartera, en la moneda de compra,
            al precio promedio de compra del paso 1.
          - Pata 2 incompleta: la orden queda en mercado hasta que se ejecute sola.

        Retorna dict con buy_qty, buy_px, sell_qty, sell_px, unwind_qty, pendiente_mercado.
        """
        if modo == "DOLARIZAR":
            t_compra, px_compra_ref, mon_compra = inst["ticker"], inst.get("prVentaPesos"), "pesos"
            t_venta, px_venta_ref, mon_venta = inst["tickerD"], inst.get("prCompraDolar"), "dólares"
        else:
            t_compra, px_compra_ref, mon_compra = inst["tickerD"], inst.get("prVentaDolar"), "dólares"
            t_venta, px_venta_ref, mon_venta = inst["ticker"], inst.get("prCompraPesos"), "pesos"

        # Salvaguarda: NUNCA orden MARKET salvo que sea AL30
        ord_type = "MARKET" if inst["ticker"] == "AL30" else "LIMIT"
        tag = f"[{'Pata 1' if es_primera else 'Pata 2'} {modo} {inst['ticker']}]"
        self._pata_ctx = {"pata": "1" if es_primera else "2", "modo": modo, "tipo_pendiente": "NOMINALES"}
        res = {"buy_qty": 0, "buy_px": None, "sell_qty": 0, "sell_px": None, "unwind_qty": 0, "pendiente_mercado": 0}
        buy_qty, buy_px, sell_qty = 0, None, 0

        try:
            # ------------------------- Paso 1: COMPRA -------------------------
            logger.info(f"{tag} Paso 1: COMPRAR {quant} {t_compra} en {mon_compra} ({ord_type})")
            status = "NO_ENVIADA"
            cl_ord_id, prop = self._enviar_orden(t_compra, "BUY", quant, px_compra_ref, ord_type)
            if cl_ord_id and prop:
                status, buy_qty, buy_px = self._esperar_ejecucion(cl_ord_id, prop, quant)
                if buy_qty < quant and status not in ESTADOS_FINALES:
                    logger.info(f"{tag} Compra {status} ({buy_qty}/{quant}). Cancelando remanente...")
                    status, buy_qty, buy_px = self._cancelar_remanente(cl_ord_id, prop)
                    if status not in ESTADOS_FINALES:
                        # La orden puede seguir viva y ejecutarse luego: nominales sin venta asociada
                        self._registrar_posicion_pendiente(
                            inst, quant - buy_qty, f"COMPRA_{status}_CANCELACION_NO_CONFIRMADA",
                            precio_compra=px_compra_ref, precio_intento_venta=px_venta_ref,
                            cl_ord_id=cl_ord_id,
                            accion_manual=(
                                f"Revisar en broker la orden de COMPRA {cl_ord_id} de {t_compra} "
                                f"(último estado {status}, ejecutado {buy_qty}/{quant}, el bot sigue con "
                                f"{buy_qty}). Si sigue activa, cancelarla; si se ejecutaron más de {buy_qty} u., "
                                f"vender el excedente de {t_venta} en {mon_venta} manualmente."
                            ),
                        )

            if buy_qty <= 0:
                if es_primera:
                    logger.info(
                        f"{tag} La compra de {t_compra} no se ejecutó (status={status}). "
                        "No se inicia el arbitraje."
                    )
                else:
                    logger.warning(
                        f"{tag} No se realizó la segunda pata porque la punta de {t_compra} desapareció "
                        f"(status={status})."
                    )
                return res

            buy_px = buy_px or px_compra_ref
            res["buy_qty"], res["buy_px"] = buy_qty, buy_px
            if buy_qty < quant:
                logger.info(f"{tag} Compra parcial: {buy_qty}/{quant} @ {buy_px}. Remanente cancelado; se continúa con {buy_qty}.")
            else:
                logger.info(f"{tag} Compra completa: {buy_qty} @ {buy_px}.")

            # ------------------------- Paso 2: VENTA --------------------------
            logger.info(f"{tag} Paso 2: VENDER {buy_qty} {t_venta} en {mon_venta} ({ord_type})")
            status2, sell_px = "NO_ENVIADA", None
            cl2, prop2 = self._enviar_orden(t_venta, "SELL", buy_qty, px_venta_ref, ord_type)
            if cl2 and prop2:
                status2, sell_qty, sell_px = self._esperar_ejecucion(cl2, prop2, buy_qty)

            if sell_qty >= buy_qty:
                res["sell_qty"], res["sell_px"] = sell_qty, sell_px or px_venta_ref
                logger.info(f"✅ {tag} Venta completa: {sell_qty} {t_venta} @ {res['sell_px']}.")
                return res

            if es_primera:
                # Cancelar remanente de la venta y continuar con lo vendido
                if cl2 and prop2 and status2 not in ESTADOS_FINALES:
                    status2, sell_qty, sell_px = self._cancelar_remanente(cl2, prop2)
                    if status2 not in ESTADOS_FINALES:
                        # La venta puede seguir viva: puede ejecutarse además de la orden de deshacer
                        self._registrar_posicion_pendiente(
                            inst, buy_qty - sell_qty, f"VENTA_{status2}_CANCELACION_NO_CONFIRMADA",
                            precio_compra=buy_px, precio_intento_venta=px_venta_ref,
                            cl_ord_id=cl2,
                            accion_manual=(
                                f"Revisar en broker la orden de VENTA {cl2} de {t_venta} (último estado "
                                f"{status2}, ejecutado {sell_qty}/{buy_qty}). Además se envió la venta de la "
                                f"diferencia de {buy_qty - sell_qty} u. de {t_compra} en {mon_compra}: cancelar "
                                "la que corresponda para no vender dos veces y verificar la tenencia final."
                            ),
                        )
                res["sell_qty"], res["sell_px"] = sell_qty, sell_px or px_venta_ref
                diferencia = buy_qty - sell_qty
                logger.warning(
                    f"{tag} Venta incompleta: {sell_qty}/{buy_qty} {t_venta} (status={status2}). "
                    f"Se continúa el arbitraje con {sell_qty}."
                )
                if diferencia > 0:
                    self._deshacer_remanente(inst, t_compra, diferencia, buy_px, mon_compra, tag)
                    res["unwind_qty"] = diferencia
            else:
                res["sell_qty"], res["sell_px"] = sell_qty, sell_px or px_venta_ref
                diferencia = buy_qty - sell_qty
                if cl2 and prop2 and status2 not in ESTADOS_FINALES:
                    res["pendiente_mercado"] = diferencia
                    logger.info(
                        f"⏳ {tag} Venta parcial {sell_qty}/{buy_qty} {t_venta} (status={status2}). "
                        f"Se deja la orden {cl2} en mercado por {diferencia} u. hasta que se ejecute sola."
                    )
                else:
                    # Rechazada / cancelada / no enviada: se reenvía para que la orden quede viva en
                    # mercado (ejecutable sola o modificable manualmente desde la consola del broker)
                    logger.warning(
                        f"⚠️ {tag} Venta de {t_venta} sin orden activa (status={status2}): "
                        f"{diferencia} u. en cartera sin vender. Reenviando orden para dejarla en mercado..."
                    )
                    cl_viva = self._dejar_venta_en_mercado(t_venta, diferencia, px_venta_ref, ord_type, tag)
                    if cl_viva:
                        res["pendiente_mercado"] = diferencia
                    else:
                        # Último recurso: no se logró dejar ninguna orden activa
                        self._registrar_posicion_pendiente(
                            inst, diferencia, f"PATA2_{modo}_VENTA_{status2}_SIN_ORDEN_ACTIVA",
                            precio_compra=buy_px, precio_intento_venta=px_venta_ref,
                            accion_manual=(
                                f"No se pudo dejar la venta en mercado tras {MAX_RETRY_PATA2} intentos. "
                                f"Vender {diferencia} u. de {t_venta} en {mon_venta} manualmente en broker."
                            ),
                        )
            return res

        except Exception as exc:
            logger.exception(f"💥 {tag} Excepción durante la ejecución: {exc}")
            abierto = buy_qty - sell_qty
            res["buy_qty"], res["buy_px"], res["sell_qty"] = buy_qty, buy_px, sell_qty
            if abierto > 0:
                self._registrar_posicion_pendiente(
                    inst, abierto, f"EXCEPCION_{modo}_{type(exc).__name__}",
                    precio_compra=buy_px, precio_intento_venta=px_venta_ref,
                    accion_manual=(
                        f"Excepción tras comprar {buy_qty} u. de {t_compra} ({sell_qty} vendidas). "
                        f"Revisar órdenes abiertas y vender {t_venta} manualmente si corresponde."
                    ),
                )
            return res

    def _dejar_venta_en_mercado(
        self, ticker: str, cantidad: int, precio: Optional[float], ord_type: str, tag: str
    ) -> Optional[str]:
        """
        Envía (con hasta MAX_RETRY_PATA2 intentos) una orden de venta y verifica que quede viva
        en mercado. No espera a que se ejecute: queda trabajando sola y puede modificarse o
        cancelarse manualmente desde la consola del broker.
        Retorna el clOrdId de la orden activa, o None si ningún intento quedó en mercado.
        """
        for intento in range(1, MAX_RETRY_PATA2 + 1):
            cl, prop = self._enviar_orden(ticker, "SELL", cantidad, precio, ord_type)
            if cl and prop:
                order = self.client.get_orders_by_clor_id(cl, prop).get("order") or {}
                status = order.get("status")
                if status == "FILLED":
                    logger.info(f"✅ {tag} Orden reenviada {cl} ejecutada: SELL {cantidad} {ticker}.")
                    return cl
                if status not in ("REJECTED", "CANCELLED", "EXPIRED"):
                    logger.info(
                        f"⏳ {tag} Orden {cl} activa en mercado (status={status}): SELL {cantidad} {ticker}"
                        f"{f' @ {precio}' if ord_type == 'LIMIT' else ''}. Queda trabajando hasta ejecutarse "
                        "o ser modificada/cancelada desde la consola."
                    )
                    return cl
                logger.warning(
                    f"{tag} Intento {intento}/{MAX_RETRY_PATA2}: orden {cl} quedó {status} "
                    f"(motivo: {order.get('text') or 'N/D'})."
                )
            else:
                logger.warning(f"{tag} Intento {intento}/{MAX_RETRY_PATA2}: no se pudo enviar la venta de {ticker}.")
            time.sleep(POLL_INTERVAL_S)
        logger.error(f"❌ {tag} No se logró dejar activa la venta de {cantidad} {ticker} tras {MAX_RETRY_PATA2} intentos.")
        return None

    def _deshacer_remanente(
        self, inst: Dict, t_compra: str, cantidad: int, buy_px: Optional[float], mon_compra: str, tag: str
    ) -> None:
        """
        Crea una orden LIMIT de venta por la diferencia en cartera, en la misma moneda en que
        se compró y al precio promedio de compra (deshace la posición a costo).
        """
        precio = round(float(buy_px), PRICE_DECIMALS) if buy_px else None
        if precio is None:
            logger.error(f"{tag} Sin precio de compra para deshacer {cantidad} u. de {t_compra}.")
            self._registrar_posicion_pendiente(
                inst, cantidad, "DESHACER_SIN_PRECIO",
                accion_manual=f"Vender {cantidad} u. de {t_compra} en {mon_compra} manualmente.",
            )
            return
        logger.info(f"↩️ {tag} Creando venta de la diferencia: {cantidad} {t_compra} en {mon_compra} @ {precio} (precio de compra).")
        cl, _ = self._enviar_orden(t_compra, "SELL", cantidad, precio, "LIMIT")
        if not cl:
            self._registrar_posicion_pendiente(
                inst, cantidad, "DESHACER_ERROR_ENVIO",
                precio_compra=precio, precio_intento_venta=precio,
                accion_manual=f"Falló el envío de la venta de la diferencia. Vender {cantidad} u. de {t_compra} en {mon_compra} manualmente.",
            )


# ====================== FUNCIÓN AUXILIAR ======================
def create_instrument(ticker: str, tickerD: str, min_quant: int = 1, max_quant: int = 500) -> Dict:
    """
    Crea un diccionario de instrumento con valores inicializados.

    Args:
        ticker: Ticker en pesos
        tickerD: Ticker en dólares
        min_quant: Cantidad mínima permitida
        max_quant: Cantidad máxima permitida

    Returns:
        Dict: Diccionario de instrumento
    """
    return {
        "ticker": ticker,
        "tickerD": tickerD,
        "prCompraPesos": None,
        "prVentaPesos": None,
        "prCompraDolar": None,
        "prVentaDolar": None,
        "siCompraPesos": None,
        "siVentaPesos": None,
        "siCompraDolar": None,
        "siVentaDolar": None,
        "cierreAnteriorPesos": None,
        "cierreAnteriorDolar": None,
        "minPesos": None,
        "minDolar": None,
        "min_quant": min_quant,
        "max_quant": max_quant,
    }


def load_instruments(path: str) -> List[Dict]:
    instrumentos = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 3 or row[2].strip() != "1":
                continue  # sin flag o flag distinto de 1 → no operar
            min_q = int(row[3].strip()) if len(row) > 3 and row[3].strip() else 1
            max_q = int(row[4].strip()) if len(row) > 4 and row[4].strip() else 500
            instrumentos.append(
                create_instrument(row[0].strip(), row[1].strip(), min_quant=min_q, max_quant=max_q)
            )
    return instrumentos

# ====================== MAIN ======================
if __name__ == "__main__":
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    
    instrumentos = load_instruments(os.path.join(PROJECT_ROOT, "data/instrumentos.csv"))

    CONFIG_FILE_PATH = os.path.join(PROJECT_ROOT, "config.ini")
    if not os.path.exists(CONFIG_FILE_PATH):
        # Fallback a directorio superior si se ejecuta dentro de /deprecated
        CONFIG_FILE_PATH = os.path.join(os.path.dirname(PROJECT_ROOT), "config.ini")

    if not os.path.exists(CONFIG_FILE_PATH):
        raise FileNotFoundError(
            f"Credentials file not found at {CONFIG_FILE_PATH}. Please create it."
        )

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE_PATH)
    usuario = config["credentials"]["matriz_username"]
    password = config["credentials"]["matriz_password"]
    account = config["credentials"]["matriz_account"]
    session_id = config["credentials"]["matriz_session_id"]
    conn_id = config["credentials"]["matriz_conn_id"]

    websocket_url = (
        f"wss://matriz.cocos.xoms.com.ar/ws?session_id={session_id}&conn_id={conn_id}"
    )

    client = CocosMatrizClient(username=usuario, password=password)
    data_manager = DataManager(instrumentos)
    websocket_client = WebSocketClient(websocket_url, data_manager, instrumentos)
    wst = websocket_client.connect()

    executer = Executer(
        account=account,
        client=client,
        ratio=ARBITER_RATIO,
        require_confirmation=REQUIRE_CONFIRMATION,
        data_manager=data_manager,
    )

    try:
        # Mantener el hilo principal activo mientras el WebSocket escucha
        while True:
            time.sleep(UPDATE_SLEEP_INTERVAL)
            # CONC-01: usar get_snapshot() thread-safe en lugar de copy.deepcopy manual
            snapshot = data_manager.get_snapshot()
            executer.execute(snapshot)
    except KeyboardInterrupt:
        logger.info("Exiting...")
        websocket_client.stop_websocket()
