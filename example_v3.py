import requests
import base64
import json
import time
import threading
import websocket
import math
import statistics
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
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
AL30_MAX_QUANT_DEFAULT = 2000
UPDATE_SLEEP_INTERVAL = 1.5
REQUIRE_CONFIRMATION = True  # Requiere que la oportunidad persista al menos 2 ciclos para evitar falsos positivos
AL30_ORDER_TYPE = "MARKET"   # Orden a mercado para AL30 dada su alta liquidez; LIMIT para corporativos

# Crear directorio de logs si no existe
os.makedirs("logs", exist_ok=True)

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
            url, headers=self.headers, params=params
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
        r = requests.get(url, headers=self.headers, params=params)
        return r.json() if r.ok else {"error": r.text}

    def cancel_order(self, cl_ord_id: str, proprietary: str) -> Dict:
        """Cancelar orden por clOrdId"""
        url = f"{self.base_url}/rest/order/cancelById"
        params = {"clOrdId": cl_ord_id, "proprietary": proprietary}
        r = requests.get(url, headers=self.headers, params=params)
        return r.json() if r.ok else {"error": r.text}


class WebSocketClient:
    def __init__(self, url, dataManager, instrumentos):
        self.url = url
        self.dataManager = dataManager
        self.instrumentos = instrumentos
        self.ws = None

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
        time.sleep(WEBSOCKET_RECONNECT_DELAY)
        self.connect()

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


class Executer:
    def __init__(
        self,
        account: str,
        client: CocosMatrizClient,
        ratio: float = ARBITER_RATIO,
        require_confirmation: bool = REQUIRE_CONFIRMATION,
    ):
        self.account = account
        self.client = client
        self.ratio = ratio
        self.require_confirmation = require_confirmation
        self.tracked_opportunities: Set[Tuple[str, str]] = set()

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
        pesos_a_USD_al30: Optional[float],
        USD_a_pesos_al30: Optional[float],
        fallback_mep: Optional[float] = None,
    ) -> Dict:
        """
        Determina cuál de las dos patas del arbitraje es más prioritaria tomando los ratios
        de AL30 como consensos de mercado:
        - inst_A (Dolarizador): Adquiere USD a pesos_a_USD_A.
          Se compara contra el costo de dolarizarse con AL30 (pesos_a_USD_al30):
          dev_A = (pesos_a_USD_al30 - pesos_a_USD_A) / pesos_a_USD_al30
          (mide cuánto más barato compramos dólares con A respecto al consenso AL30).
        - inst_B (Pesificador): Vende USD a USD_a_pesos_B.
          Se compara contra el precio obtenido al pesificarse con AL30 (USD_a_pesos_al30):
          dev_B = (USD_a_pesos_B - USD_a_pesos_al30) / USD_a_pesos_al30
          (mide cuánto más caro vendemos dólares con B respecto al consenso AL30).
        """
        pesos_a_USD_A = inst_A["pesos_a_USD"]
        USD_a_pesos_B = inst_B["USD_a_pesos"]

        # Si AL30 no está disponible, utilizar fallback de mercado
        bench_A = pesos_a_USD_al30 if (pesos_a_USD_al30 and pesos_a_USD_al30 > 1) else fallback_mep
        bench_B = USD_a_pesos_al30 if (USD_a_pesos_al30 and USD_a_pesos_al30 > 1) else fallback_mep

        if not bench_A or not bench_B:
            mid_ab = (pesos_a_USD_A + USD_a_pesos_B) / 2.0
            bench_A = bench_A or mid_ab
            bench_B = bench_B or mid_ab

        dev_A = (bench_A - pesos_a_USD_A) / bench_A
        dev_B = (USD_a_pesos_B - bench_B) / bench_B

        diff = dev_A - dev_B
        TOLERANCE = 0.0005  # Margen de 0.05% para considerar empate técnico

        ticker_A = inst_A["ticker"]
        ticker_B = inst_B["ticker"]

        if abs(diff) <= TOLERANCE:
            # En empate técnico:
            # Si uno es AL30, priorizar el instrumento corporativo (más ilíquido)
            if ticker_A == "AL30" and ticker_B != "AL30":
                priority = "PESIFICADOR"
                reason = "Empate técnico: B es corporativo (más ilíquido que AL30)"
            elif ticker_B == "AL30" and ticker_A != "AL30":
                priority = "DOLARIZADOR"
                reason = "Empate técnico: A es corporativo (más ilíquido que AL30)"
            else:
                # Desempate por volumen disponible en libro (la punta con menor tamaño va primero)
                size_A = min(inst_A.get("siVentaPesos") or 0, inst_A.get("siCompraDolar") or 0)
                size_B = min(inst_B.get("siVentaDolar") or 0, inst_B.get("siCompraPesos") or 0)
                if size_A < size_B:
                    priority = "DOLARIZADOR"
                    reason = f"Empate técnico: A tiene menor volumen ({size_A} vs {size_B})"
                else:
                    priority = "PESIFICADOR"
                    reason = f"Empate técnico: B tiene menor volumen ({size_B} vs {size_A})"
        elif diff > 0:
            priority = "DOLARIZADOR"
            reason = f"Dolarizador {ticker_A} con mayor ventaja vs AL30 (+{dev_A*100:.2f}% vs Pesificador {ticker_B} +{dev_B*100:.2f}%)"
        else:
            priority = "PESIFICADOR"
            reason = f"Pesificador {ticker_B} con mayor ventaja vs AL30 (+{dev_B*100:.2f}% vs Dolarizador {ticker_A} +{dev_A*100:.2f}%)"

        return {
            "priority": priority,
            "dev_A": dev_A,
            "dev_B": dev_B,
            "bench_A": bench_A,
            "bench_B": bench_B,
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

        # Ratios de AL30 como consensos de mercado
        p_to_u_al30, u_to_p_al30, mid_al30 = self.get_al30_benchmarks(instrumentos)
        fallback_mep, stdev_mep = self.compute_market_benchmark(instrumentos)

        if p_to_u_al30 and u_to_p_al30:
            logger.info(
                f"Consenso de mercado (AL30) → Dolarizar: {p_to_u_al30:.2f} ARS | "
                f"Pesificar: {u_to_p_al30:.2f} ARS | Mid: {mid_al30:.2f} ARS."
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
                # ========================================================
                # 1) Máximo volumen en USD que A puede generar y B puede absorber
                max_usd_A = max_A * pr_compra_dolar_A
                max_usd_B = max_B * pr_venta_dolar_B

                # Mínimo volumen en USD requerido para cumplir con min_quant de cada instrumento
                min_usd_A = min_quant_A * pr_compra_dolar_A
                min_usd_B = min_quant_B * pr_venta_dolar_B

                # Si la punta contraria no llega al monto mínimo de alguno de los instrumentos, NO arbitrar:
                # - B debe poder absorber al menos lo que genera el mínimo de A (max_usd_B >= min_usd_A)
                # - A debe poder generar al menos lo que requiere el mínimo de B (max_usd_A >= min_usd_B)
                if max_usd_B < min_usd_A or max_usd_A < min_usd_B:
                    continue

                # 3) Cuello de botella en USD para ambas patas
                usable_usd = min(max_usd_A, max_usd_B)

                # Unidades de B que se pueden comprar con usable_usd
                quant_B = int(usable_usd / pr_venta_dolar_B)
                if quant_B < min_quant_B:
                    continue

                # USD exacto requerido para comprar quant_B unidades de B
                usd_needed = quant_B * pr_venta_dolar_B

                # Unidades de A a vender para obtener al menos usd_needed
                quant_A = int(math.ceil(usd_needed / pr_compra_dolar_A))

                # Asegurar que quant_A no exceda max_A debido al redondeo
                if quant_A > max_A:
                    quant_A = max_A
                    quant_B = int((quant_A * pr_compra_dolar_A) / pr_venta_dolar_B)

                # Verificar estrictamente que ambas cantidades alcancen sus mínimos
                if quant_A < min_quant_A or quant_B < min_quant_B:
                    continue

                # Métricas de la oportunidad
                spread = USD_a_pesos_B / pesos_a_USD_A
                cost_ars = quant_A * pr_venta_pesos_A
                rev_ars = quant_B * pr_compra_pesos_B
                profit_ars = rev_ars - cost_ars
                profit_pct = ((rev_ars / cost_ars) - 1.0) * 100.0

                # Evaluación de pata prioritaria según el consenso de mercado (AL30)
                priority_info = self.calculate_leg_priority(
                    inst_A, inst_B, p_to_u_al30, u_to_p_al30, fallback_mep
                )

                candidates.append({
                    "dolarizador": inst_A,
                    "pesificador": inst_B,
                    "quant_dol": quant_A,
                    "quant_pes": quant_B,
                    "spread": spread,
                    "profit_ars": profit_ars,
                    "profit_pct": profit_pct,
                    "usd_amount": usd_needed,
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
            if self.require_confirmation and pair_key not in self.tracked_opportunities:
                self.tracked_opportunities.add(pair_key)
                logger.info(
                    f"👀 Oportunidad detectada: Dolarizar {pair_key[0]} (TC: {opp['dolarizador']['pesos_a_USD']:.2f}, Q: {opp['quant_dol']}, Min: {opp['dolarizador'].get('min_quant', 1)}) "
                    f"-> Pesificar {pair_key[1]} (TC: {opp['pesificador']['USD_a_pesos']:.2f}, Q: {opp['quant_pes']}, Min: {opp['pesificador'].get('min_quant', 1)}) | "
                    f"Spread: {opp['spread']:.4f} | USD: ~{opp['usd_amount']:.2f} | "
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
        Ejecuta las dos patas del arbitraje priorizando la pata más inusual/desplazada
        determinada por la mayor ventaja respecto a los ratios de consenso de AL30.
        """
        dol = opp["dolarizador"]
        pes = opp["pesificador"]
        quant_dol = opp["quant_dol"]
        quant_pes = opp["quant_pes"]
        priority_info = opp["priority_info"]
        priority = priority_info["priority"]

        logger.info(
            f"🚀 EJECUTANDO ARBITRAJE: Dolarizar {dol['ticker']} ({quant_dol} u. [min: {dol.get('min_quant', 1)}]) "
            f"↔ Pesificar {pes['ticker']} ({quant_pes} u. [min: {pes.get('min_quant', 1)}]) | "
            f"Spread: {opp['spread']:.4f} | USD: ~{opp['usd_amount']:.2f} | "
            f"Ganancia est.: ${opp['profit_ars']:.2f} ({opp['profit_pct']:.2f}%) | "
            f"Pata Prioritaria: {priority} ({priority_info['reason']})"
        )

        if priority == "DOLARIZADOR":
            # =========================================================================
            # EJECUCIÓN: PATA 1 = DOLARIZADOR (A), PATA 2 = PESIFICADOR (B)
            # =========================================================================
            logger.info(
                f"Paso 1 (Prioritario): COMPRAR {dol['ticker']} EN PESOS y VENDER {dol['tickerD']} EN DOLARES (LIMIT, quant={quant_dol})"
            )
            logger.info(
                f"Se dolarizaría a un tipo de cambio de {dol['pesos_a_USD']:.2f} ARS/USD (Ventaja vs AL30: {priority_info['dev_A']*100:+.2f}%)"
            )
            success, price_ratio, cum_qty = self.dolarizar(dol, quant_dol, order_type="LIMIT")
            logger.info(
                f"✅ Se dolarizó {dol['ticker']} a: {price_ratio:.4f} (cumQty={cum_qty})"
                if price_ratio
                else f"❌ No se obtuvo price ratio en dolarización de {dol['ticker']}."
            )

            if success and cum_qty > 0:
                actual_usd = cum_qty * dol["prCompraDolar"]
                quant_pes_adj = min(
                    quant_pes,
                    int(actual_usd / pes["prVentaDolar"]),
                    pes.get("max_quant", 500) if pes["ticker"] != "AL30" else AL30_MAX_QUANT_DEFAULT,
                )
                min_quant_pes = pes.get("min_quant", 1)
                order_type_pes = AL30_ORDER_TYPE if pes["ticker"] == "AL30" else "LIMIT"

                if quant_pes_adj >= min_quant_pes:
                    logger.info(
                        f"Paso 2: COMPRAR {pes['tickerD']} EN DOLARES y VENDER {pes['ticker']} EN PESOS ({order_type_pes}, quant={quant_pes_adj})"
                    )
                    logger.info(f"Se pesificaría a un tipo de cambio de {pes['USD_a_pesos']:.2f} ARS/USD")
                    success2, price_ratio2, cum_qty2 = self.pesificar(
                        pes, quant_pes_adj, order_type=order_type_pes
                    )
                    logger.info(
                        f"✅ Se pesificó {pes['ticker']} a: {price_ratio2:.4f} (cumQty={cum_qty2})"
                        if price_ratio2
                        else f"❌ No se obtuvo price ratio en pesificación de {pes['ticker']}."
                    )
                else:
                    logger.warning(
                        f"No se puede ejecutar la segunda pata de {pes['ticker']}: la cantidad ajustada ({quant_pes_adj}) "
                        f"es menor al monto mínimo permitido ({min_quant_pes})."
                    )
            else:
                logger.warning(
                    f"No se ejecutó la parte de {pes['ticker']} porque la dolarización de {dol['ticker']} no se ejecutó."
                )

        else:
            # =========================================================================
            # EJECUCIÓN: PATA 1 = PESIFICADOR (B), PATA 2 = DOLARIZADOR (A)
            # =========================================================================
            logger.info(
                f"Paso 1 (Prioritario): COMPRAR {pes['tickerD']} EN DOLARES y VENDER {pes['ticker']} EN PESOS (LIMIT, quant={quant_pes})"
            )
            logger.info(
                f"Se pesificaría a un tipo de cambio de {pes['USD_a_pesos']:.2f} ARS/USD (Ventaja vs AL30: {priority_info['dev_B']*100:+.2f}%)"
            )
            success, price_ratio, cum_qty = self.pesificar(pes, quant_pes, order_type="LIMIT")
            logger.info(
                f"✅ Se pesificó {pes['ticker']} a: {price_ratio:.4f} (cumQty={cum_qty})"
                if price_ratio
                else f"❌ No se obtuvo price ratio en pesificación de {pes['ticker']}."
            )

            if success and cum_qty > 0:
                actual_usd = cum_qty * pes["prVentaDolar"]
                quant_dol_adj = min(
                    quant_dol,
                    int(math.ceil(actual_usd / dol["prCompraDolar"])),
                    dol.get("max_quant", 500) if dol["ticker"] != "AL30" else AL30_MAX_QUANT_DEFAULT,
                )
                min_quant_dol = dol.get("min_quant", 1)
                order_type_dol = AL30_ORDER_TYPE if dol["ticker"] == "AL30" else "LIMIT"

                if quant_dol_adj >= min_quant_dol:
                    logger.info(
                        f"Paso 2: COMPRAR {dol['ticker']} EN PESOS y VENDER {dol['tickerD']} EN DOLARES ({order_type_dol}, quant={quant_dol_adj})"
                    )
                    logger.info(f"Se dolarizaría a un tipo de cambio de {dol['pesos_a_USD']:.2f} ARS/USD")
                    success2, price_ratio2, cum_qty2 = self.dolarizar(
                        dol, quant_dol_adj, order_type=order_type_dol
                    )
                    logger.info(
                        f"✅ Se dolarizó {dol['ticker']} a: {price_ratio2:.4f} (cumQty={cum_qty2})"
                        if price_ratio2
                        else f"❌ No se obtuvo price ratio en dolarización de {dol['ticker']}."
                    )
                else:
                    logger.warning(
                        f"No se puede ejecutar la segunda pata de {dol['ticker']}: la cantidad ajustada ({quant_dol_adj}) "
                        f"es menor al monto mínimo permitido ({min_quant_dol})."
                    )
            else:
                logger.warning(
                    f"No se ejecutó la parte de {dol['ticker']} porque la pesificación de {pes['ticker']} no se ejecutó."
                )

    def dolarizar(
        self, dolarizador: Dict, quant: int, order_type: str = "LIMIT"
    ) -> Tuple[bool, Optional[float], int]:
        price_ratio = None
        first_price, second_price = None, None

        # Enviar orden de compra en pesos
        orden_response = self.client.send_order(
            symbol=f"MERV - XMEV - {dolarizador['ticker']} - 24hs",
            side="BUY",
            quantity=quant,
            price=dolarizador["prVentaPesos"] if order_type == "LIMIT" else None,
            ord_type=order_type,
            market_id="ROFX",
            account=self.account,
            time_in_force="DAY",
        )

        if "error" in orden_response:
            logger.error(f"Error enviando orden de compra en pesos: {orden_response.get('error')}")
            return False, price_ratio, 0

        cl_ord_id = orden_response.get("order", {}).get("clientId")
        prop = orden_response.get("order", {}).get("proprietary")

        if not cl_ord_id or not prop:
            logger.error("No se obtuvo clOrdId o proprietary.")
            return False, price_ratio, 0

        orden_encontrada = self.client.get_orders_by_clor_id(cl_ord_id, prop)
        status = orden_encontrada.get("order", {}).get("status")
        cum_qty = orden_encontrada.get("order", {}).get("cumQty", 0)

        if status != "FILLED":
            logger.warning(f"Orden de compra no filled. Status: {status}")
            cancel_response = self.client.cancel_order(cl_ord_id, prop)
            logger.info(f"Cancelación de la orden: {cancel_response}")
            if status != "PARTIALLY_FILLED" or cum_qty <= 0:
                return False, price_ratio, 0

        if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_qty > 0):
            logger.info(f"Orden de compra filled con cumQty={cum_qty}")
            first_price = orden_encontrada.get("order", {}).get("avgPx")

            # Enviar orden complementaria de venta en dólares por la cantidad filled
            comp_response = self.client.send_order(
                symbol=f"MERV - XMEV - {dolarizador['tickerD']} - 24hs",
                side="SELL",
                quantity=cum_qty,
                price=dolarizador["prCompraDolar"] if order_type == "LIMIT" else None,
                ord_type=order_type,
                market_id="ROFX",
                account=self.account,
                time_in_force="DAY",
            )

            comp_cl_ord_id = comp_response.get("order", {}).get("clientId")
            comp_prop = comp_response.get("order", {}).get("proprietary")

            if comp_cl_ord_id and comp_prop:
                logger.info(
                    f"Orden de venta en dólares enviada → clOrdId={comp_cl_ord_id}, proprietary={comp_prop}"
                )
                comp_orden_encontrada = self.client.get_orders_by_clor_id(
                    comp_cl_ord_id, comp_prop
                )
                second_price = comp_orden_encontrada.get("order", {}).get("avgPx")
                if (
                    first_price is not None
                    and second_price is not None
                    and second_price != 0
                ):
                    price_ratio = first_price / second_price
                return True, price_ratio, cum_qty
            else:
                logger.error(
                    "No se pudo obtener clOrdId/proprietary de la venta en dólares"
                )

        return False, price_ratio, 0

    def pesificar(
        self, pesificador: Dict, quant: int, order_type: str = "LIMIT"
    ) -> Tuple[bool, Optional[float], int]:
        price_ratio = None
        first_price, second_price = None, None

        # Enviar orden de compra en dólares
        orden_response = self.client.send_order(
            symbol=f"MERV - XMEV - {pesificador['tickerD']} - 24hs",
            side="BUY",
            quantity=quant,
            price=pesificador["prVentaDolar"] if order_type == "LIMIT" else None,
            ord_type=order_type,
            market_id="ROFX",
            account=self.account,
            time_in_force="DAY",
        )

        if "error" in orden_response:
            logger.error(f"Error enviando orden de compra en dólares: {orden_response.get('error')}")
            return False, price_ratio, 0

        cl_ord_id = orden_response.get("order", {}).get("clientId")
        prop = orden_response.get("order", {}).get("proprietary")

        if not cl_ord_id or not prop:
            logger.error("No se obtuvo clOrdId o proprietary.")
            return False, price_ratio, 0

        orden_encontrada = self.client.get_orders_by_clor_id(cl_ord_id, prop)
        status = orden_encontrada.get("order", {}).get("status")
        cum_qty = orden_encontrada.get("order", {}).get("cumQty", 0)

        if status != "FILLED":
            logger.warning(f"Orden de compra no filled. Status: {status}")
            cancel_response = self.client.cancel_order(cl_ord_id, prop)
            logger.info(f"Cancelación de la orden: {cancel_response}")
            if status != "PARTIALLY_FILLED" or cum_qty <= 0:
                return False, price_ratio, 0

        if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_qty > 0):
            logger.info(f"Orden de compra filled con cumQty={cum_qty}")
            first_price = orden_encontrada.get("order", {}).get("avgPx")

            # Enviar orden complementaria de venta en pesos por la cantidad filled
            comp_response = self.client.send_order(
                symbol=f"MERV - XMEV - {pesificador['ticker']} - 24hs",
                side="SELL",
                quantity=cum_qty,
                price=pesificador["prCompraPesos"] if order_type == "LIMIT" else None,
                ord_type=order_type,
                market_id="ROFX",
                account=self.account,
                time_in_force="DAY",
            )

            comp_cl_ord_id = comp_response.get("order", {}).get("clientId")
            comp_prop = comp_response.get("order", {}).get("proprietary")

            if comp_cl_ord_id and comp_prop:
                logger.info(
                    f"Orden de venta en pesos enviada → clOrdId={comp_cl_ord_id}, proprietary={comp_prop}"
                )
                comp_orden_encontrada = self.client.get_orders_by_clor_id(
                    comp_cl_ord_id, comp_prop
                )
                second_price = comp_orden_encontrada.get("order", {}).get("avgPx")
                if (
                    first_price is not None
                    and second_price is not None
                    and second_price != 0
                ):
                    price_ratio = second_price / first_price
                return True, price_ratio, cum_qty
            else:
                logger.error(
                    "No se pudo obtener clOrdId/proprietary de la venta en pesos"
                )

        return False, price_ratio, 0


# ====================== FUNCIÓN AUXILIAR ======================
def create_instrument(ticker: str, tickerD: str, min_quant: int = 1, max_quant: int = 500) -> Dict:
    """
    Crea un diccionario de instrumento con valores inicializados.

    Args:
        ticker: Ticker en pesos
        tickerD: Ticker en dólares
        max_quant: Cantidad máxima permitida
        min_quant: Cantidad mínima permitida

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

    executer = Executer(account=account, client=client, ratio=ARBITER_RATIO, require_confirmation=REQUIRE_CONFIRMATION)

    try:
        # Mantener el hilo principal activo mientras el WebSocket escucha
        while True:
            time.sleep(UPDATE_SLEEP_INTERVAL)
            snapshot = copy.deepcopy(instrumentos)
            executer.execute(snapshot)
    except KeyboardInterrupt:
        logger.info("Exiting...")
        websocket_client.stop_websocket()
