"""
Código compartido por compradores/ y analizadores/.

    rutas         PROJECT_ROOT, logging y lectura de config.ini
    instrumentos  carga de data/instrumentos.csv y data/mis_activos.csv
    cotizaciones  modelo Cotizacion, parser de mensajes de Matriz y LibroCotizaciones
    cliente_rest  CocosMatrizClient (Primary API, todas las llamadas con timeout)
    feed_matriz   WebSocket de market data de Matriz (session_id/conn_id)
    feed_primary  WebSocket de market data de Primary (token)
    ordenes       enviar una orden LIMIT, verificar y cancelar si no se completó
    tablas        armado de DataFrames desde el libro e impresión con tabulate

No contiene lógica de estrategia: eso queda en cada script.
"""
