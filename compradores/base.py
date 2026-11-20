"""
Base de los compradores: recibe cada cotización del feed, decide si evaluar y envía la compra.

Cada comprador concreto define:
    suscripciones()      qué (ticker, plazo) escuchar
    al_actualizar(cot)   qué hacer ante cada cotización nueva (en el hilo del WebSocket)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Set, Tuple

from comun.cliente_rest import CocosMatrizClient
from comun.cotizaciones import Cotizacion, LibroCotizaciones
from comun.feed_matriz import FeedMatriz
from comun.instrumentos import cargar_instrumentos
from comun.ordenes import enviar_limit_y_verificar, simbolo_orden
from comun.rutas import configurar_logging, leer_credenciales, url_websocket_matriz

ARBITER_RATIO = 1.002
INTERVALO_MAIN = 1  # segundos; el hilo principal solo mantiene vivo el proceso

# Tope acumulado por instrumento: como máximo FACTOR_TOPE × max_quant nominales en cualquier
# ventana móvil de VENTANA_TOPE_SEGUNDOS. Se cuenta la cantidad enviada en cada orden (aunque se
# haya ejecutado solo en parte o se haya cancelado), así que el tope nunca se subestima.
FACTOR_TOPE = 5
VENTANA_TOPE_SEGUNDOS = 600

logger = logging.getLogger(__name__)

ClaveCompra = Tuple[str, str]  # (ticker comprado, plazo)


class CompradorBase:
    def __init__(
        self,
        client,
        account: str,
        instrumentos: List[Dict],
        libro: LibroCotizaciones,
        ratio: float = ARBITER_RATIO,
        reloj: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.account = account
        self.instrumentos = instrumentos
        self.libro = libro
        self.ratio = ratio
        # Índices para acceso O(1)
        self.por_ticker = {inst["ticker"]: inst for inst in instrumentos}
        self.por_tickerD = {inst["tickerD"]: inst for inst in instrumentos}
        # Compras enviadas en la ventana: (ticker, plazo) → [(instante, cantidad), ...]
        self._reloj = reloj
        self._lock = threading.Lock()
        self._compras: Dict[ClaveCompra, Deque[Tuple[float, int]]] = {}
        self._topes_avisados: Set[ClaveCompra] = set()

    # ---------------- a definir por cada comprador ----------------
    def suscripciones(self) -> List[Tuple[str, str]]:
        raise NotImplementedError

    def al_actualizar(self, cotizacion: Cotizacion) -> None:
        raise NotImplementedError

    # ---------------- tope por ventana ----------------
    @staticmethod
    def tope_ventana(inst: Dict) -> int:
        return FACTOR_TOPE * inst["max_quant"]

    def comprado_en_ventana(self, clave: ClaveCompra) -> int:
        """Nominales enviados para (ticker, plazo) en los últimos VENTANA_TOPE_SEGUNDOS."""
        limite = self._reloj() - VENTANA_TOPE_SEGUNDOS
        with self._lock:
            compras = self._compras.get(clave)
            if not compras:
                return 0
            while compras and compras[0][0] <= limite:
                compras.popleft()
            return sum(cantidad for _, cantidad in compras)

    def disponible_en_ventana(self, inst: Dict, clave: ClaveCompra) -> int:
        return max(0, self.tope_ventana(inst) - self.comprado_en_ventana(clave))

    # ---------------- comunes ----------------
    def calcular_cantidad(
        self, inst: Dict, clave: ClaveCompra, *tamanios: Optional[float]
    ) -> Optional[int]:
        """
        min(tamaños de las puntas, max_quant, disponible en la ventana de 10 minutos).
        None si falta algún tamaño o si el resultado no llega a min_quant (límites duros).
        """
        if any(t is None for t in tamanios):
            return None
        disponible = self.disponible_en_ventana(inst, clave)
        cantidad = int(min(*tamanios, inst["max_quant"], disponible))
        if cantidad < inst["min_quant"]:
            if disponible < inst["min_quant"]:
                self._avisar_tope(inst, clave)
            return None
        self._topes_avisados.discard(clave)
        return cantidad

    def _avisar_tope(self, inst: Dict, clave: ClaveCompra) -> None:
        """Avisa una sola vez por cada vez que se alcanza el tope (no en cada tick)."""
        if clave in self._topes_avisados:
            return
        self._topes_avisados.add(clave)
        logger.warning(
            f"⚠️ Tope alcanzado para {clave[0]} {clave[1]}: {self.comprado_en_ventana(clave)} de "
            f"{self.tope_ventana(inst)} nominales en {VENTANA_TOPE_SEGUNDOS // 60} min. No se compra hasta liberar cupo."
        )

    def comprar(self, ticker: str, plazo: str, cantidad: int, precio: float) -> Dict:
        """Reserva el cupo antes de enviar; lo libera solo si el broker rechazó el envío."""
        clave = (ticker, plazo)
        registro = (self._reloj(), cantidad)
        with self._lock:
            self._compras.setdefault(clave, deque()).append(registro)
        resultado = enviar_limit_y_verificar(
            self.client, self.account, simbolo_orden(ticker, plazo), "BUY", cantidad, precio
        )
        if not resultado["enviada"]:
            with self._lock:
                self._compras[clave].remove(registro)
        return resultado


CrearComprador = Callable[[CocosMatrizClient, str, List[Dict], LibroCotizaciones], CompradorBase]


def correr_comprador(crear: CrearComprador, nombre_log: str, ruta_instrumentos: str) -> None:
    """Arma cliente, libro, comprador y feed de Matriz, y queda escuchando hasta Ctrl+C."""
    configurar_logging(nombre_log)
    instrumentos = cargar_instrumentos(ruta_instrumentos)
    credenciales = leer_credenciales()

    client = CocosMatrizClient(credenciales["matriz_username"], credenciales["matriz_password"])
    if not client.token:
        raise SystemExit("❌ No se pudo hacer login: no se inicia el comprador.")

    libro = LibroCotizaciones()
    comprador = crear(client, credenciales["matriz_account"], instrumentos, libro)
    feed = FeedMatriz(
        url_websocket_matriz(credenciales), comprador.suscripciones(), libro, comprador.al_actualizar
    )
    feed.conectar()

    try:
        while True:
            time.sleep(INTERVALO_MAIN)
    except KeyboardInterrupt:
        logger.info("Saliendo...")
        feed.detener()
