"""Rutas del proyecto, configuración de logging y lectura de credenciales."""

from __future__ import annotations

import configparser
import logging
import os
from typing import Dict, Optional

# Raíz del proyecto (un nivel arriba de comun/): config.ini, data/ y logs/ viven ahí
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(PROJECT_ROOT, "config.ini")
RUTA_DATA = os.path.join(PROJECT_ROOT, "data")
RUTA_LOGS = os.path.join(PROJECT_ROOT, "logs")

URL_WS_MATRIZ = "wss://matriz.cocos.xoms.com.ar/ws"


def ruta_data(nombre: str) -> str:
    """Ruta absoluta a un archivo dentro de data/."""
    return os.path.join(RUTA_DATA, nombre)


def configurar_logging(nombre_log: str, nivel: int = logging.INFO) -> None:
    """Loguea a consola y a logs/<nombre_log> (crea la carpeta si no existe)."""
    os.makedirs(RUTA_LOGS, exist_ok=True)
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(RUTA_LOGS, nombre_log), encoding="utf-8"),
        ],
        force=True,
    )


def leer_credenciales(ruta: Optional[str] = None) -> Dict[str, str]:
    """Devuelve la sección [credentials] de config.ini como dict. Nunca loguear su contenido."""
    ruta = ruta or RUTA_CONFIG
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No se encontró el archivo de credenciales en {ruta}.")
    config = configparser.ConfigParser()
    config.read(ruta)
    return dict(config["credentials"])


def url_websocket_matriz(credenciales: Dict[str, str]) -> str:
    """URL del WebSocket de Matriz con session_id y conn_id de config.ini."""
    return (
        f"{URL_WS_MATRIZ}?session_id={credenciales['matriz_session_id']}"
        f"&conn_id={credenciales['matriz_conn_id']}"
    )
