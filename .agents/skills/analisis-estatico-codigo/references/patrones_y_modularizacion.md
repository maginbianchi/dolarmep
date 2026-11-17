# Catálogo de Patrones de Diseño y Principios de Modularización

Este documento sirve como referencia arquitectónica para formular propuestas de desacoplamiento, modularización y aplicación de patrones de diseño GoF y de arquitectura limpia en los artefactos de código analizados.

---

## 1. Principios de Modularización (SOLID)

### 1.1. Single Responsibility Principle (SRP)
* **Definición:** Un módulo, clase o función debe tener una única razón para cambiar.
* **Code Smell habitual:** Clases "God Object" que mezclan llamadas a API REST, recepción de mensajes por WebSocket, lógica matemática de arbitraje, persistencia en disco y envío de notificaciones.
* **Solución modular:**
  * Separar la **infraestructura de red** (clientes HTTP/WS).
  * Aislar el **motor de cálculo o reglas de negocio** (cálculo de ratios, spreads, desvíos).
  * Aislar el **ejecutor o gestor de órdenes** (validación de límites, enrutamiento de transacciones).
  * Aislar los **modelos de datos** (estructuras tipadas o entidades de dominio).

### 1.2. Open/Closed Principle (OCP)
* **Definición:** El software debe estar abierto a la extensión pero cerrado a la modificación.
* **Aplicación:** Si se añade un nuevo tipo de arbitraje o un nuevo instrumento financiero, no se debe modificar el código existente con nuevos bloques `if/elif`. Se deben utilizar interfaces polimórficas o estrategias inyectables.

### 1.3. Dependency Inversion Principle (DIP)
* **Definición:** Los módulos de alto nivel no deben depender de los de bajo nivel; ambos deben depender de abstracciones.
* **Aplicación:** El motor de arbitraje no debe instanciar directamente un cliente específico de una API propietaria. Debe recibir una interfaz o abstracción genérica (ej. `BrokerClientProtocol`), facilitando el testing mediante mocks y el cambio de proveedores.

---

## 2. Patrones de Diseño Recomendados

### 2.1. Patrón Strategy (Estrategia) - *Comportamiento*
* **Propósito:** Definir una familia de algoritmos, encapsular cada uno de ellos y hacerlos intercambiables en tiempo de ejecución.
* **Caso de uso típico:** Distintos modelos de priorización de patas de arbitraje (Prioridad por Consenso AL30, Prioridad por Mediana de Mercado, Prioridad por Menor Profundidad de Libro).
* **Estructura conceptual:**
  ```python
  from typing import Protocol, Dict

  class LegPriorityStrategy(Protocol):
      def evaluate_priority(self, inst_A: Dict, inst_B: Dict, **kwargs) -> Dict:
          ...

  class AL30ConsensusStrategy:
      def evaluate_priority(self, inst_A: Dict, inst_B: Dict, **kwargs) -> Dict:
          # Lógica basada en consenso AL30
          ...

  class BookLiquidityStrategy:
      def evaluate_priority(self, inst_A: Dict, inst_B: Dict, **kwargs) -> Dict:
          # Lógica basada en menor liquidez
          ...
  ```

### 2.2. Patrón Observer / Pub-Sub (Observador) - *Comportamiento*
* **Propósito:** Notificar automáticamente a múltiples consumidores cuando ocurre un evento de mercado (ej. actualización de punta compradora o vendedora recibida por WebSocket).
* **Beneficio:** Desacopla la recepción pura de paquetes WebSocket de la lógica de evaluación de arbitraje y del subsistema de logging/auditoría.

### 2.3. Patrón Adapter (Adaptador) - *Estructural*
* **Propósito:** Convertir la interfaz de una clase o formato de datos externo en otra interfaz que el cliente espera.
* **Caso de uso típico:** Estandarizar los feeds de cotizaciones de distintos orígenes (ROFX, BYMA, Matriz, Bloomberg) hacia una estructura de mercado canónica unificada (`MarketQuote`).

### 2.4. Patrón State (Estado) - *Comportamiento*
* **Propósito:** Permitir que un objeto altere su comportamiento cuando su estado interno cambia.
* **Caso de uso típico:** Ciclo de vida de una orden compleja de dos patas:
  * `PendingValidation` -> `FirstLegSubmitted` -> `FirstLegFilled` -> `SecondLegSubmitted` -> `Completed` / `PartiallyFilledFailed`.

### 2.5. Patrón Factory / Builder - *Creacional*
* **Propósito:** Centralizar la creación y validación de objetos complejos (ej. constructores de órdenes compuestas con validación previa de `min_quant`, `max_quant` y límites de exposición).

---

## 3. Arquitectura en Capas Sugerida

Para proyectos de trading algorítmico y finanzas cuantitativas:

```text
src/
├── core/               # Constantes, configuraciones, excepciones personalizadas
├── domain/             # Entidades de negocio (Quote, Order, TradePair, Opportunity)
├── infrastructure/     # Conectores externos (REST APIs, WebSockets, DBs, CSVs)
├── services/           # Motores de cálculo, evaluadores de arbitraje, analítica
└── strategies/         # Estrategias de priorización, ejecución y arbitraje intercambiables
```
