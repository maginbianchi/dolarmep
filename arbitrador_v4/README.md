# Arbitrador MEP v4

Arbitraje de dólar MEP entre todos los pares de instrumentos (bonos y ONs), basado en `deprecated/example_v1.py`.

## Estructura

| Archivo | Contenido |
|---|---|
| `executer_v4.py` | Entry point y `Executer`: ciclo, ejecución de patas, deshacer, control de huérfanas |
| `estrategia.py` | Lógica pura: TC, consenso AL30, detección de pares, prioridad, dimensionamiento, cierre |
| `mercado.py` | Cliente REST Primary API, WebSocket, `DataManager` thread-safe, carga de instrumentos |
| `registro.py` | `pnl_diario.csv` (P&L acumulado del día) y `pendientes.csv` (para el operador) |
| `../tests/` | Tests con pytest contra un broker simulado (`tests/fakes_v4.py`), unificados con los del resto del proyecto |
| `results/` | Log (`executer_v4.log`), `pnl_diario.csv`, `pendientes.csv` |

Toma las credenciales de `../config.ini` y los instrumentos de `../data/instrumentos.csv`
(`ticker,tickerD,habilitado,min_quant,max_quant`).

## Uso

```powershell
# desde arbitrador_v4/
..\.venv\Scripts\python.exe executer_v4.py        # ATENCIÓN: envía órdenes reales
```

Los tests se corren desde la raíz del proyecto: `.venv\Scripts\python.exe -m pytest`.

## Reglas principales

- Arbitraje si `TC_pesificación(B) > TC_dolarización(A) × 1.0008`, para todo par A≠B, confirmado en 2 ciclos.
- Consenso = promedio de los dos MEP del AL30. Se ejecuta primero la oportunidad y la pata más desviadas; si una es AL30, se empieza por la otra.
- Todas las órdenes son LIMIT a la punta.
- Compra parcial/no ejecutada → se cancela el remanente y se sigue con lo ejecutado.
- Venta parcial/no ejecutada → se cancela y se deja una venta de la diferencia en la misma especie y moneda en que se compró, al precio de compra. Esa orden queda viva para el operador (`pendientes.csv`, tipo `DESHACER_ACTIVA`).
- Pata 2: se refresca la market data; se usa el instrumento original si sus puntas tienen precio igual o mejor, si no (o para completar el monto) alternativas con ratio ≥ 1.0006 contra el TC real de la pata 1.
- Mínimo de mercado (`min_quant`): nunca se envía una orden por menos nominales que el mínimo del instrumento, y las puntas con size menor al mínimo se ignoran (queda en el log con el detalle). Si por un error de mercado se ejecuta una cantidad menor al mínimo, no se opera (ni venta ni deshacer): solo queda en el log.
- Máximo (`max_quant`): ninguna orden supera el máximo del instrumento.
- Ninguna orden de arbitraje queda activa: se verifica después de cada arbitraje, ante excepciones y al salir.
