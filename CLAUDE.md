# CLAUDE.md

Trading bots for **dólar MEP arbitrage** on the Argentine market (BYMA/MERV bonds and corporate bonds, "ONs"). They connect to the Primary API used by Cocos Capital / Matriz (`api.cocos.xoms.com.ar` REST, `wss://matriz.cocos.xoms.com.ar/ws` WebSocket) and trade the peso (`ticker`, e.g. `AL30`) and dollar (`tickerD`, e.g. `AL30D`) versions of the same instrument in the `24hs` settlement.

Code, comments, logs and identifiers are in **Spanish**. Keep new code in Spanish to match.

> ⚠️ **`executer_v4.py` and `compradores/*.py` send REAL orders with real money.** Never run them to "try something". Use the pytest suites, or `diagnostico_v4.py` / `analizadores/*.py` (read-only, they only print tables) for a look at live market data.

## Layout

| Path | Status | What it is |
|---|---|---|
| `arbitrador_v4/` | **Active development** | Modular, tested rewrite of the arbitrage bot. Has its own [README](arbitrador_v4/README.md). |
| `comun/` | Shared package | Code used by `compradores/` and `analizadores/` (not by v4): `rutas` (`PROJECT_ROOT`, `configurar_logging`, `leer_credenciales`), `instrumentos`, `cotizaciones` (`Cotizacion`, `parsear_registro`, `LibroCotizaciones`), `cliente_rest` (`CocosMatrizClient`), `feed_matriz` / `feed_primary` (WebSockets), `ordenes` (`enviar_limit_y_verificar`), `tablas`. |
| `compradores/` | In use | "Buyer" bots on top of `compradores/base.py` (`CompradorBase`, `correr_comprador`). `comprador_pesos.py --plazo 24hs\|CI` buys in pesos when its rate beats AL30 (replaces `comprador_v2` and `comprador_CI`). `comprador_D_CI.py` buys USD CI below USD 24hs. |
| `analizadores/` | In use, read-only | `analizador.py [--feed matriz\|primary]` prints 24hs/CI arbitrage tables (v1 parameters; replaces `analizador_v1/v2`, formerly `arbitrador_v1/v2`). |
| `deprecated/` | Do not modify | Old versions (`example_v1..v3.py`, `dolarMEP.py`, `comprador.py`, `xirr.py`), plus the pre-refactor originals of the compradores (`comprador_v2.py`, `comprador_CI.py`, `comprador_D_CI.py`) and analizadores (`arbitrador_v1.py`, `arbitrador_v2.py`, `arbitradorC.py`), kept as reference only: their paths resolve from their own folder, so they do not run from here. v4 is based on `deprecated/example_v1.py`. |
| `data/instrumentos.csv` | Config | One row per instrument, no header: `ticker,tickerD,habilitado(1/0),min_quant,max_quant`. `#` lines are comments. Shared by v4, the compradores (only `habilitado=1` rows) and `analizador.py` (all rows). |
| `data/mis_activos.csv` | Config | Tickers held in the portfolio (used by `analizadores/analizador.py`). |
| `config.ini`, `token.csv` | **Secrets** | Broker credentials (`[credentials]`: `matriz_username/password/account/session_id/conn_id`, plus Balanz and FIX sections). Git-ignored and not tracked (they live only on disk); `config.example.ini` is the tracked template with empty values. Older versions are still in the git history, so treat those credentials as exposed. Never print, log, commit changes to, or send their contents anywhere. |
| `.agents/skills/analisis-estatico-codigo/` | Tooling | A static-analysis/code-review skill (in Spanish) that writes its report to `results/`. |

## comun, compradores, analizadores

`pyproject.toml` declares the packages `comun`, `compradores`, `analizadores`; they are installed in the venv with `pip install -e . --config-settings editable_mode=compat`, so scripts import `from comun... import ...` and run from any directory. Paths resolve from the project root (`config.ini`, `data/`, `logs/`). `arbitrador_v4` still has its own copy of the REST client/WebSocket in `mercado.py`.

```powershell
.venv\Scripts\python.exe -m pytest                                  # ALL tests, incl. arbitrador_v4 (fast, no network)
.venv\Scripts\python.exe -m pytest tests/test_estrategia.py -k consenso   # a single test
.venv\Scripts\python.exe analizadores\analizador.py --feed matriz    # read-only
.venv\Scripts\python.exe compradores\comprador_pesos.py --plazo CI   # LIVE TRADING
```

- Quotes are keyed by `(ticker, plazo)` in `LibroCotizaciones`; a missing quote is `None` (NaN in the analizadores' DataFrames).
- Compradores evaluate in the WebSocket thread on each update of the leg they buy. `min_quant`/`max_quant` are hard limits; a missing size means no order. Per instrument there is also a rolling cap of `FACTOR_TOPE` (5) x `max_quant` nominals every `VENTANA_TOPE_SEGUNDOS` (600 s), in `compradores/base.py`: the quantity of every order that reached the broker counts (filled or not), the last order is trimmed to the remaining room, and the counter lives in memory (a restart resets it).
- Add a comprador by subclassing `CompradorBase` (`suscripciones()`, `al_actualizar()`) and calling `correr_comprador()`. Tests use `tests/fakes.py` (`FakeClient`, `registro()`).
- `.vscode/launch.json` has one entry per script; the ones marked `⚠️ REAL` trade.

## arbitrador_v4

The modules import each other by bare name (e.g. `from estrategia import ...`), so run the scripts **from inside `arbitrador_v4/`**. Its tests live in the root `tests/` folder and run from the root with the rest of the suite: `pyproject.toml` sets `pythonpath = ["tests", "arbitrador_v4"]`.

```powershell
cd arbitrador_v4
..\.venv\Scripts\python.exe diagnostico_v4.py --segundos 120  # READ-ONLY live diagnosis
..\.venv\Scripts\python.exe executer_v4.py [--duracion-segundos N]   # LIVE TRADING
```

Runtime dependencies are in the root `requirements.txt` (v4 only needs `requests` and `websocket-client`). Dev dependencies are in `arbitrador_v4/requirements-dev.txt` (pytest 8).

### Modules

- **`estrategia.py`**: pure logic with no I/O. Exchange rates (TC), AL30 consensus, finding opportunities across all pairs, priority, sizing, choosing instruments for leg 2. All strategy constants live here (`ARBITER_RATIO = 1.0008`, `CLOSE_RATIO = 1.0006`, `CONSENSO_TICKER = "AL30"`, `PRICE_FACTOR = 100`).
- **`mercado.py`**: `CocosMatrizClient` (REST; every call has a timeout and returns `{"error": ...}` on failure instead of raising), `DataManager` (thread-safe quote store; `get_snapshot()` returns a deep copy), `WebSocketClient`, `load_instruments`.
- **`executer_v4.py`**: `Executer` (the main loop, running each leg, undo orders, orphan-order control) and `main()`. Paths are resolved from the file location: `../config.ini`, `../data/instrumentos.csv`, `results/`.
- **`registro.py`**: CSV output. `RegistroPnL` writes `results/pnl_diario.csv` (gross P&L in ARS, with a daily running total that is recovered on restart). `RegistroPendientes` writes `results/pendientes.csv` (items that need the operator: `DESHACER_ACTIVA`, `DESHACER_ERROR`, `CIERRE_INCOMPLETO`, `ORDEN_NO_CONFIRMADA`).
- **`diagnostico_v4.py`**: connects to the live WebSocket without creating a REST client and writes `results/diagnostico_v4_<fecha>.log`.
- **Tests** (`tests/test_estrategia.py`, `test_executer.py`, `test_registro_y_mercado.py`): `tests/fakes_v4.py` provides `FakeClient` (a simulated broker whose fill behavior is set per `(ticker, side)`: a fill fraction, a nominal count, `"reject"`, `"error"`, or a list of these) and `FakeDataManager`. Helper `inst(ticker, tc_dol=..., tc_pes=...)` builds an instrument with target rates. The `armar_executer` fixture in `tests/conftest.py` wires an `Executer` to the fakes with `tmp_path` CSVs and a no-op sleep.

### Domain model (needed to read the code)

- **DOLARIZAR**: buy in pesos (pay the peso ask), sell in USD (hit the USD bid). `TC_dol = prVentaPesos / prCompraDolar`.
- **PESIFICAR**: buy in USD (pay the USD ask), sell in pesos (hit the peso bid). `TC_pes = prCompraPesos / prVentaDolar`.
- Quote fields on each instrument dict: `pr|si` + `Compra|Venta` + `Pesos|Dolar`. Here `Compra` is the bid and `Venta` is the ask. A missing quote is `None`; the legacy scripts used `-100` instead.
- **Arbitrage** for a pair A≠B: `TC_pes(B) > TC_dol(A) × 1.0008`. The pair must show up in 2 consecutive cycles (`REQUIRE_CONFIRMATION`). At most one arbitrage runs per cycle (cycle length `UPDATE_SLEEP_INTERVAL = 3` s).
- **Consensus** = the average of AL30's two rates. Leg 1 is the instrument that deviates most from consensus. If one side is AL30, leg 1 is always the other side.
- Prices are quoted per 100 nominal units: `monto = q × p / 100`.
- Every order is LIMIT at the touch, with symbol `"MERV - XMEV - {ticker} - 24hs"`. A partial buy is cancelled and the bot continues with the filled amount. A partial sell is cancelled and a "deshacer" sell order for the remainder is left live at the buy price, then recorded in `pendientes.csv`.
- `min_quant` and `max_quant` (from `instrumentos.csv`) are hard limits. No order goes out below the minimum or above the maximum, and quotes with size below the minimum are ignored.
- **Invariant:** no arbitrage order (`ROL_ARBITRAJE`) may stay active. `verificar_huerfanas()` runs after each arbitrage, after any exception, and on shutdown (`cerrar()`). Undo orders (`ROL_DESHACER`) are intentionally left live.

## Conventions

- Windows, PowerShell, venv at `.venv/` in the repo root.
- `results/`, `*.log`, `.venv` are git-ignored. `.vscode` uses Pylance `basic` type checking.
- Keep `estrategia.py` free of I/O, and inject dependencies into `Executer` (client, data manager, registros, `sleep_fn`) so the strategy stays testable with the fakes. Add or adjust tests in the root `tests/` with any behavior change, and run the suite from the root before finishing.
- Logging goes through `logging.getLogger("arbitrador_v4")`. Log messages use emoji prefixes (✅ ❌ 💰).
