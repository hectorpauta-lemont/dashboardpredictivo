# Indicadores del dashboard — fórmula, código y propósito

Referencia técnica de cada número que muestra `/dashboard/`, para corroborar
con el equipo que el cálculo hace lo que el negocio espera. Cada fila dice:
qué es en términos de negocio, la fórmula exacta, y dónde está en el código
(`archivo:línea`) para que cualquiera lo pueda abrir y leer la lógica real,
no una descripción de memoria.

**Cómo mantenerlo al día:** si se toca una fórmula en `dashboard/metrics.py`
o `rules_engine/`, actualizar la línea correspondiente acá en el mismo PR.
Los números de línea son de la rama `v2-filtros-cliente-proveedor` al
2026-09-21 — si el archivo se reordena, buscar por el nombre de la función
(`grep -n "^def nombre" dashboard/metrics.py`) en vez de confiar en el número.

---

## 1. Cómo se arma cada número — la pieza central

Todo el dashboard sale de una sola función, `build_dashboard_metrics()` en
[`dashboard/metrics.py:429`](../dashboard/metrics.py#L429), que llama a
`_window_aggregate()` ([`dashboard/metrics.py:340`](../dashboard/metrics.py#L340)).
Esa función decide, por cada día del rango pedido, de dónde sacar el dato:

- **Últimos 2 días** (`LIVE_TAIL_DAYS`, [`metrics.py:29`](../dashboard/metrics.py#L29)):
  se recalcula en vivo desde `intake.LiveSurveySnapshot` (cada poll guardado),
  vía `_daily_deltas()` ([`metrics.py:150`](../dashboard/metrics.py#L150)).
- **Más viejo que eso**: se lee de `orchestrator.DailyMetricRollup`, una tabla
  pre-calculada 1x/día por el comando `build_dashboard_rollups`
  ([`orchestrator/management/commands/build_dashboard_rollups.py`](../orchestrator/management/commands/build_dashboard_rollups.py)).

**La unidad base de "actividad" es el delta diario**: para cada encuesta,
`último snapshot del día − primer snapshot del día` en completes, entrants,
terminaciones y prescreens ([`metrics.py:202-207`](../dashboard/metrics.py#L202)).
Nunca se suma el valor acumulado tal cual — eso duplicaría actividad de días
anteriores. Si hay filtro de proveedor activo, ese delta se multiplica por la
fracción prorrateada antes de sumarse ([`metrics.py:219`](../dashboard/metrics.py#L219),
ver sección 5 más abajo).

---

## 2. Financial (banda superior del dashboard)

| Indicador (UI) | Qué es | Fórmula | Código |
|---|---|---|---|
| **Revenue** | Lo que se le cobra al cliente por los completes nuevos del período | `Σ (completes_nuevos_del_día × cpi_client)` por cada encuesta/día | [`metrics.py:220`](../dashboard/metrics.py#L220) (cálculo diario), agregado en [`metrics.py:472`](../dashboard/metrics.py#L472) vía `_financials()` [`metrics.py:323`](../dashboard/metrics.py#L323) |
| **Cost** | Lo que se le paga al proveedor por esos mismos completes | `Σ (completes_nuevos_del_día × cpi)` | [`metrics.py:221`](../dashboard/metrics.py#L221) |
| **Profit** | Ganancia bruta del período | `Σ (completes_nuevos_del_día × profit_per_complete)` — **no** es `revenue − cost` recalculado, es su propia suma independiente (debería coincidir, pero si no coinciden es señal de que `profit_per_complete` no está sincronizado con `cpi`/`cpi_client` en algún snapshot) | [`metrics.py:222`](../dashboard/metrics.py#L222) |
| **Margin %** | Rentabilidad relativa | `profit / revenue × 100` | [`metrics.py:329`](../dashboard/metrics.py#L329) (`_financials`, empieza en [`metrics.py:323`](../dashboard/metrics.py#L323)) |
| **Conv. Rate** (financial) | Promedio de `perc_conversion` de cada encuesta activa, **no** completes/transactions del período | `avg(perc_conversion)` sobre `latest_per_survey` (estado actual de cada encuesta, no del rango) | [`metrics.py:330`](../dashboard/metrics.py#L330) |
| Flechas ▲▼ % | Cambio vs. el período anterior de igual duración | `(actual − anterior) / |anterior| × 100`, comparado contra el mismo rango de días inmediatamente previo, mismo filtro de cliente/proveedor aplicado | `_pct_delta()` [`metrics.py:334`](../dashboard/metrics.py#L334), llamado en [`metrics.py:671-673`](../dashboard/metrics.py#L671) |

**Nota real, ya confirmada con Power BI (2026-09-16):** `Cost` hoy es
*solo* CPI del proveedor — el "fee" adicional que Link a veces cobra no se
trae todavía. Documentado en `notes.cost` de la respuesta de la API
([`metrics.py:689`](../dashboard/metrics.py#L689)).

---

## 3. Operations (KPIs de gestión)

**Importante — esta sección NO respeta el filtro de fecha/cliente/proveedor
del dashboard.** Es intencional (confirmado con el usuario, 2026-09-17):
representa "qué necesita acción ahora mismo", no un reporte histórico. Se
calcula sobre una ventana fija de los **últimos 5 días desde ahora**
([`metrics.py:508-510`](../dashboard/metrics.py#L508)), sin importar qué
rango esté seleccionado arriba.

El veredicto de cada encuesta sale de `rules_engine.engine.evaluate_survey()`
([`rules_engine/engine.py:49`](../rules_engine/engine.py#L49)), que corre por
cada poll en `run_allocator` y queda guardado en `audit.DecisionLog`. Orden
de precedencia (el primero que aplica gana):

```
1. out_of_scope           -- categoria no rankeable (ver check_ranking_scope)
2. blocked_exclusion      -- conflicto de proveedores mutuamente excluyentes
3. blocked_profit_floor   -- no llega al piso de margen
4. eligible_needs_cpi_input -- pasa todo, falta que el negocio ponga CPI
```
([`rules_engine/engine.py:55-64`](../rules_engine/engine.py#L55))

| Indicador (UI) | Qué es | Fórmula / regla | Código |
|---|---|---|---|
| **Eligible** | Encuestas que pasaron todos los chequeos, solo falta que alguien ponga el CPI a mano | Conteo de `verdict == eligible_needs_cpi_input` en los últimos 5 días | [`metrics.py:520`](../dashboard/metrics.py#L520) |
| **Blocked · Floor** | Encuestas que no llegan al margen mínimo | Conteo de `verdict == blocked_profit_floor` | [`metrics.py:521`](../dashboard/metrics.py#L521) |
| **Exclusion Conflicts** | Encuestas bloqueadas por dos proveedores mutuamente excluyentes activos a la vez | Conteo de `verdict == blocked_exclusion` | [`metrics.py:522`](../dashboard/metrics.py#L522) |
| **Awaiting CPI Input** | Mismo número que "Eligible" — está elegible, pero como todavía no tiene CPI puesto a mano, se muestra también acá como pendiente de acción | Igual a `eligible` | [`metrics.py:523`](../dashboard/metrics.py#L523) |
| **Auto-safe Candidates** | Heurística — **umbral formal aún NO confirmado con el negocio**, ver nota en la respuesta de la API | `eligible` + pasó el piso + sin conflicto de exclusión + margen calculado ≥ piso + 10 puntos | [`metrics.py:513-518`](../dashboard/metrics.py#L513) |
| **Decisions Today** | Cuántas evaluaciones corrió el rules engine hoy (calendario, no rango del dashboard) | Conteo de `DecisionLog` con `evaluated_at` = hoy | [`metrics.py:525-527`](../dashboard/metrics.py#L525) |

### Fórmulas de negocio detrás de estos veredictos (`rules_engine/checks.py`)

| Chequeo | Fórmula exacta | Configuración | Código |
|---|---|---|---|
| **% de margen (profit_percent)** | `round(((cpi_client − cpi_pagado) / cpi_client) × 100, 0)`, nunca negativo (recortado a 0 mínimo). Replica exacto `AllocationsListAPIView._calculate_profit` de Link — a propósito, para que el número coincida con lo que ya ve el negocio ahí | Piso: **30%** (`PROFIT_FLOOR_PERCENT`) | [`rules_engine/checks.py:18-33`](../rules_engine/checks.py#L18), config en [`rules_engine/rules_config.py:13`](../rules_engine/rules_config.py#L13) |
| **Exclusión de proveedores** | Dentro de los proveedores ya activos en las allocations de la encuesta, ¿hay 2+ que pertenecen al mismo grupo mutuamente excluyente? | Grupo actual: Prime/Lucid/Cint para "Buyer 42" — **`enabled: False`**, esperando confirmar el nombre exacto del cliente en Link antes de activarse | [`rules_engine/checks.py:77-105`](../rules_engine/checks.py#L77), config en [`rules_engine/rules_config.py:22-32`](../rules_engine/rules_config.py#L22) |
| **Alcance de ranking** | `type_industry_name` vacío o desconocido → fuera de alcance (fail-safe, nunca incluye por defecto algo que no se pudo determinar) | Categoría excluida: **"beauty"** | [`rules_engine/checks.py:114-124`](../rules_engine/checks.py#L114), config en [`rules_engine/rules_config.py:37`](../rules_engine/rules_config.py#L37) |
| **Sugerencia de CPI** | Siempre `None` — la fórmula de pricing automático **todavía no está confirmada con el negocio**, stub deliberado | — | [`rules_engine/checks.py:188-200`](../rules_engine/checks.py#L188) |

---

## 4. Statistical

| Indicador (UI) | Qué es | Fórmula | Código |
|---|---|---|---|
| **IR (current)** | Incidence Rate medido en vivo del último día con dato en el rango | Promedio de `ir` de todas las encuestas activas ese día | [`metrics.py:542`](../dashboard/metrics.py#L542), serie completa en [`metrics.py:531-535`](../dashboard/metrics.py#L531) |
| **IR slope/day** | Tendencia — ¿sube o baja el IR, y qué tan rápido? | Regresión lineal por mínimos cuadrados sobre la serie diaria de IR: `y = pendiente·x + intercepto` | `_linear_fit()` [`metrics.py:50-68`](../dashboard/metrics.py#L50) |
| **IR projected change** | Cuánto se espera que cambie el IR de acá al final del rango, según esa misma recta | `pendiente × (n_días − 1)` | [`metrics.py:66`](../dashboard/metrics.py#L66) |
| **IR direction** | Etiqueta "up"/"down"/"flat" | `up` si pendiente > 0.3, `down` si < −0.3, si no `flat` | [`metrics.py:67`](../dashboard/metrics.py#L67) |
| **Alarms · Low IR** | Cuántas alarmas de IR bajo están activas **ahora mismo** (no filtra por fecha/cliente/proveedor — estado actual, igual criterio que la sección Operations) | Conteo de `PerformanceAlarm` tipo `IR_LOW` con `is_active=True` | [`metrics.py:546-547`](../dashboard/metrics.py#L546) |
| **Alarms · High Term** | Igual, para terminación alta | Conteo de `PerformanceAlarm` tipo `TERMINATION_HIGH` activas | [`metrics.py:548-549`](../dashboard/metrics.py#L548) |
| **Termination rate** | % de quienes entraron y terminaron sin completar, del período | `terminaciones_nuevas / transacciones_nuevas × 100` | [`metrics.py:540`](../dashboard/metrics.py#L540) |

### Cuándo se dispara una alarma (`rules_engine/checks.py`)

| Alarma | Regla | Umbral | Código |
|---|---|---|---|
| **IR bajo** | Se dispara cuando `IR medido ≤ umbral` | **11%** (`IR_ALARM_THRESHOLD_PERCENT`) — un punto arriba del piso real de 10% confirmado por el jefe, para avisar antes de llegar al problema | [`rules_engine/checks.py:165-174`](../rules_engine/checks.py#L165), config [`rules_engine/rules_config.py:43-44`](../rules_engine/rules_config.py#L43) |
| **Terminación alta** | `terminaciones / prescreens × 100 ≥ umbral` | **50%** — sin confirmar un número exacto del negocio todavía, usado como "zona de peligro evidente" (caso real que lo disparó: 83%) | [`rules_engine/checks.py:177-185`](../rules_engine/checks.py#L177), config [`rules_engine/rules_config.py:50`](../rules_engine/rules_config.py#L50) |

Estas dos reglas las evalúa `run_earnings_tracker` en cada poll — ver
[`orchestrator/management/commands/run_earnings_tracker.py:196-200`](../orchestrator/management/commands/run_earnings_tracker.py#L196) —
y si disparan, crean/mantienen una fila en `orchestrator.PerformanceAlarm`.
Antes se mandaban también a Telegram; eso se desactivó (2026-09-16, ver
[`orchestrator/notifications.py:22-30`](../orchestrator/notifications.py#L22)). Desde 2026-09-24 tampoco van
al popup ni por email (con más demanda serían interminables): solo se ven en el panel
"Performance alarms". El popup queda solo para oportunidades de margen.

### Columna "Var (LOI)" del panel de alarmas

Reemplaza a "Age". La encuesta se paga/cobra por minuto de entrevista, así que
se muestra **LOI real − LOI prometido**, en minutos: `+5 min ▲` en rojo (tarda
más de lo prometido) o `−4 min ▼` en verde (cumple).

- **LOI real**: campo `loi` de la lista LIVE de Link. Link lo calcula con la
  hora de inicio y de fin de cada panelista (desde que entra hasta que
  completa). Nosotros no lo calculamos: lo leemos en cada poll y lo guardamos
  en `LiveSurveySnapshot.loi`. Link manda 0 mientras no hay completes → se
  guarda `None` y la celda muestra "—".
- **LOI prometido**: `expected_loi` del detalle de la encuesta →
  `LiveSurveySnapshot.expected_loi`.
- Se toma el último snapshot de la encuesta que tenga LOI real
  ([`metrics.py`](../dashboard/metrics.py), bloque "alarmas activas").

---

## 5. Transactions by Supplier / Supplier ranking

Fuente: **NO** son las mismas `LiveSurveySnapshot` de arriba — vienen de
`raw_allocations`, el desglose por proveedor que trae Link (requiere
`LINK_JWT_TOKEN`, vence cada ~7 días, ver `intake/link_client.py`).

| Indicador | Qué es | Fórmula | Código |
|---|---|---|---|
| **Transactions (donut)** | Entrants por proveedor, últimos 6 con más volumen | `Σ statistics.entrants` de `raw_allocations`, por `client_supplier__name` | `_supplier_breakdown()` [`metrics.py:265-299`](../dashboard/metrics.py#L265), armado final en [`metrics.py:560-564`](../dashboard/metrics.py#L560) |
| **Conv. Rate (ranking)** | % de entrants que completaron, por proveedor | `completes / entrants × 100` | [`metrics.py:295`](../dashboard/metrics.py#L295) |
| **Avg CPI (ranking)** | Promedio del `avg_cpi` que reporta Link por proveedor | `avg(avg_cpi)` de las filas con dato | [`metrics.py:296`](../dashboard/metrics.py#L296) |

**Con filtro de proveedor activo:** cuando una encuesta reparte completes
entre más de un proveedor (ej. Prime y Lucid en la misma encuesta), el
revenue/costo/completes de esa encuesta se **prorratea** según la fracción
real de completes de cada uno — nunca se le asigna todo al primero que
aparece. Una encuesta sin ese desglose se excluye del todo del filtro, nunca
se adivina. Ver `_supplier_fraction()` [`metrics.py:83-104`](../dashboard/metrics.py#L83)
para el tramo en vivo, y la misma lógica replicada en
[`orchestrator/management/commands/build_dashboard_rollups.py:25-42`](../orchestrator/management/commands/build_dashboard_rollups.py#L25)
para el histórico.

---

## 6. Summary by period / Transactions by Status / gráficos por día

| Indicador | Qué es | Fórmula | Código |
|---|---|---|---|
| **Surveys** (tabla resumen) | Encuestas distintas con actividad en el rango | Suma del set de `survey_supplier_id` vistos ese mes — **ver limitación abajo** | [`metrics.py:487-499`](../dashboard/metrics.py#L487) |
| **Completed / Transactions** | Volumen del período, agrupado por mes | Suma de los deltas diarios (ver sección 1) agrupados por `(año, mes)` | [`metrics.py:478-486`](../dashboard/metrics.py#L478) |
| **Overquota** | Actividad que no cayó en completed/terminated/prescreens | `max(0, transactions − completed − terms − prescreens)` — es un residuo, no un dato directo de Link | [`metrics.py:565`](../dashboard/metrics.py#L565) |
| **Revenue vs Cost (gráfico)** | Serie diaria | Mismo delta diario de la sección 1, uno por día | [`metrics.py:579-582`](../dashboard/metrics.py#L579) |

**Limitación conocida y documentada en la propia respuesta de la API**
(`notes.surveys`, [`metrics.py:692-696`](../dashboard/metrics.py#L692)):
fuera de los últimos 2 días, el conteo de "surveys" viene del rollup
histórico, que guarda "encuestas activas por día" — una encuesta abierta
varios días del rango se cuenta una vez por cada día, **no** es un conteo de
encuestas únicas del período completo. Se prefirió esta aproximación
documentada antes que mostrar 0 (bug anterior) o esconder el problema.

---

## 7. Qué SÍ respeta el filtro de fecha/cliente/proveedor, y qué no

| Sección | ¿Filtra por fecha? | ¿Filtra por cliente/proveedor? |
|---|---|---|
| Financial, Summary table, gráficos por día, Transactions by Supplier, Supplier ranking, Transactions by Status | ✅ Sí | ✅ Sí |
| **Operations** (Eligible, Blocked, Auto-safe, Decisions today) | ❌ No — últimos 5 días fijos desde ahora | ❌ No |
| **Alarms · Low IR / High Term** (los contadores del panel Statistical) | ❌ No — activas ahora mismo | ❌ No |
| IR current / slope / projected / direction / termination rate | ✅ Sí | ✅ Sí |

Esto es una decisión de diseño, no un descuido: Operations y las alarmas
representan "qué necesita acción hoy", no un reporte histórico — confirmado
explícitamente con el usuario el 2026-09-17.

---

## 8. Agente de margen (popup "Margin opportunity")

Versión determinista v1 — **no es un modelo de IA**, es una regla fija.

| Paso | Fórmula | Código |
|---|---|---|
| Filtro de elegibilidad | `avg_cpi` presente, `transactions ≥ 200`, `completes ≥ 50`, `conversion_rate ≥ 30%` | [`metrics.py:781-784`](../dashboard/metrics.py#L781) (`build_agent_insights`, empieza en [`metrics.py:755`](../dashboard/metrics.py#L755)) |
| CPI propuesto | `avg_cpi × 0.95` (baja fija de 5%) | [`metrics.py:786`](../dashboard/metrics.py#L786) |
| Ganancia proyectada | `completes × (avg_cpi − nuevo_cpi)`, descartado si < $50 | [`metrics.py:787-789`](../dashboard/metrics.py#L787) |

Siempre marcado `confidence: "to_validate"` y el supuesto explícito de que el
fill rate se mantiene igual al bajar el precio — nunca se aplica solo, cae en
la cola de aprobación humana. El roadmap de 3 meses ya propuesto (ver el
brief de "Agente de Margen Causal") reemplaza el `× 0.95` fijo por
elasticidad real aprendida de datos históricos, sin cambiar este contrato.
