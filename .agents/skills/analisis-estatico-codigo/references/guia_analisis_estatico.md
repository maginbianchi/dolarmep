# Guía de Análisis Estático de Código y Detección de Bugs

Esta guía establece el marco metodológico y la lista de comprobación técnica que el especialista en análisis estático debe aplicar de forma exhaustiva sobre los artefactos de código examinados.

---

## 1. Detección de Bugs y Defectos en Tiempo de Ejecución

### 1.1. Errores de Lógica y Condiciones de Borde
* **División por cero y operaciones matemáticas:** Verificar siempre que los denominadores en cálculos de ratios, porcentajes o spreads sean distintos de cero y mayores a un umbral seguro (`epsilon`).
* **Acceso a estructuras de datos:**
  * Uso de diccionarios con claves inexistentes sin comprobación previa o sin `.get()` con valor por defecto.
  * Indexación de listas o tuplas sin validación previa de longitud (`IndexError`).
* **Valores nulos (`NoneType`):** Operaciones aritméticas o llamadas a métodos sobre variables que pueden ser `None` (por ejemplo, respuestas de APIs o cotizaciones vacías).
* **Condiciones booleanas invertidas o redundantes:** Comparaciones de identidad (`is` vs `==`), operadores lógicos en cascada (`and`/`or`) con orden de evaluación incorrecto.

### 1.2. Manejo de Excepciones y Resiliencia
* **Cláusulas `except:` desnudas:** Evitar capturar `Exception` genérica o `BaseException` sin re-lanzar o sin registrar traza detallada. Esto oculta errores críticos (`KeyboardInterrupt`, `SystemExit`, `MemoryError`).
* **Bloques `try/except` excesivamente amplios:** Delimitar el bloque `try` estrictamente a la línea de código que puede fallar.
* **Manejo de reconexiones y timeouts:** Toda llamada de red, socket o HTTP debe contar con `timeout` explícito y política de reintentos con backoff exponencial.
* **Falta de limpieza en fallos:** Uso de bloques `finally` o context managers (`with`) para garantizar liberación de locks, sockets, descriptores de archivos y memoria.

### 1.3. Concurrencia, Hilos y Asincronía
* **Condiciones de Carrera (Race Conditions):** Modificación concurrente de colecciones (listas, diccionarios, sets) compartidas entre hilos (ej. callbacks de WebSocket y bucle principal) sin `threading.Lock` o estructuras concurrentes (`queue.Queue`).
* **Interbloqueos (Deadlocks):** Adquisición de múltiples candados en orden no determinista o adquisición de candados no reentrantes (`Lock` vs `RLock`).
* **Hilos huérfanos o terminación abrupta:** Configuración correcta del flag `daemon` en hilos de soporte y mecanismos de parada controlada (`threading.Event`).

---

## 2. Buenas Prácticas y Calidad de Código (Clean Code)

### 2.1. Tipado Estático y Firmas de Función
* Adopción exhaustiva de anotaciones de tipo (`typing`: `List`, `Dict`, `Optional`, `Tuple`, `Union`, `Callable`, `Protocol`).
* Uso de `TypeVar` o `TypedDict` / `dataclasses` / `Pydantic` para estructurar datos complejos en lugar de diccionarios genéricos no documentados ("Primitive Obsession").

### 2.2. Legibilidad, Estilo y Convenciones
* Cumplimiento estricto de convenciones idiomáticas (ej. PEP 8 en Python): nombres descriptivos en `snake_case` para funciones/variables, `PascalCase` para clases, `UPPER_CASE` para constantes.
* Eliminación de "números mágicos" o cadenas hardcodeadas: extracción a constantes con propósito semántico claro.
* Longitud y complejidad de funciones: Funciones de más de 40-50 líneas o con complejidad ciclomática alta (> 10) deben descomponerse en subtareas unitarias.

### 2.3. Principio DRY y Código Muerto
* Detección de duplicación de código en ramas condicionales alternativas.
* Identificación y eliminación de variables, funciones, imports o clases en desuso.

### 2.4. Seguridad y Gestión de Secretos
* Comprobación estricta de que credenciales, tokens, claves privadas o datos sensibles no estén incrustados directamente en el código fuente; deben delegarse a variables de entorno (`.env`) o archivos de configuración protegidos e ignorados por el VCS.

---

## 3. Matriz de Clasificación de Severidad

| Severidad | Criterio de Impacto | Plazo de Resolución |
| :--- | :--- | :--- |
| **Crítica (🔴)** | Falla inmediata del sistema, pérdida económica, inconsistencia de datos, vulnerabilidad grave de seguridad. | Inmediato / Hotfix |
| **Alta (🟠)** | Falla probable ante casos de borde, excepciones no controladas, pérdida silenciosa de datos, deadlocks potenciales. | Próximo ciclo / Sprint actual |
| **Media (🟡)** | Deuda técnica considerable, código fuertemente acoplado, ausencia de tipado, complejidad ciclomática excesiva. | Refactorización planificada |
| **Baja / Info (🔵)** | Inconsistencias de estilo, falta de documentación, variables con nombres poco descriptivos, micro-optimizaciones. | Mejora continua |
