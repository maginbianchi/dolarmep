"""
Fixtures compartidas.

pyproject.toml agrega tests/ y arbitrador_v4/ al sys.path (pythonpath), así los tests importan
`from fakes import ...` (comun/compradores/analizadores), `from fakes_v4 import ...` y los módulos
de arbitrador_v4 por nombre (`from estrategia import ...`).

Las fixtures de abajo son de arbitrador_v4: escenarios de mercado y un Executer conectado a dobles de prueba.
"""

from __future__ import annotations

import pytest

from executer_v4 import Executer
from fakes_v4 import FakeClient, FakeDataManager, al30, inst
from registro import RegistroPendientes, RegistroPnL


@pytest.fixture
def mercado_dolarizar_primero():
    """
    Único arbitraje: DNC3O dolariza a 1415.5 y YMCXO pesifica a 1418 (ratio ~1.00177).
    Consenso AL30 = 1416.9 → DNC3O es el más desviado → se comienza DOLARIZANDO DNC3O.
    """
    return [
        al30(),
        inst("DNC3O", tc_dol=1415.5, tc_pes=1412.0),
        inst("YMCXO", tc_dol=1421.0, tc_pes=1418.0),
        inst("TLCMO", tc_dol=1417.5, tc_pes=1416.0),  # neutro: no arbitra con nadie
    ]


@pytest.fixture
def mercado_pesificar_primero():
    """
    DNC3O dolariza a 1416.2 (desv 0.05%) y YMCXO pesifica a 1419.5 (desv 0.18%).
    YMCXO es el más desviado → se comienza PESIFICANDO YMCXO.
    """
    return [
        al30(),
        inst("DNC3O", tc_dol=1416.2, tc_pes=1413.0, size=2000, max_q=2000),
        inst("YMCXO", tc_dol=1422.0, tc_pes=1419.5),
    ]


@pytest.fixture
def armar_executer(tmp_path):
    """Fábrica: devuelve (executer, client, data_manager) para un mercado dado."""

    def _armar(mercado, comportamiento=None, require_confirmation=False):
        client = FakeClient(comportamiento)
        dm = FakeDataManager(mercado)
        executer = Executer(
            account="TEST",
            client=client,
            data_manager=dm,
            registro_pnl=RegistroPnL(str(tmp_path / "pnl_diario.csv")),
            registro_pendientes=RegistroPendientes(str(tmp_path / "pendientes.csv")),
            require_confirmation=require_confirmation,
            sleep_fn=lambda s: None,
        )
        return executer, client, dm

    return _armar
