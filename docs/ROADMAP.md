# ROADMAP

**Estado:** Phase 0 cerrada. Phase 1 pendiente de aprobación.

Cada fase tiene **criterios de salida verificables**. Una fase no se cierra
porque "está hecha", sino porque un comando devuelve el resultado esperado.
Nada de avanzar con una fase a medias: la deuda en este dominio no se manifiesta
como bugs, se manifiesta como backtests que mienten.

---

## Phase 0 — Audit ✅

Repositorio vacío; auditoría convertida en diseño. Entregables:
`CURRENT_SYSTEM_AUDIT.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `ROADMAP.md`.

---

## Phase 1 — Core data model + núcleo matemático ✅

Fundamentos. Sin datos externos todavía. **Completada** — ver `CHANGELOG.md`.

- Scaffolding: `pyproject`, SQLAlchemy + Alembic, pytest, ruff, mypy, CI.
- Esquema completo de `DATA_MODEL.md` en la migración inicial.
- `core/`: conversión de odds, los tres métodos de no-vig, edge, EV, Kelly, CLV.
- `validation/sanity.py` con los checks del audit §2.3.
- Seed de catálogo: sports, leagues, MLB teams, sportsbooks.

**Criterios de salida** — todos verificados
- ✅ `pytest tests/core` verde con casos deterministas calculados a mano (153 tests).
- ✅ Los tres métodos de no-vig coinciden en mercados equilibrados y divergen como se espera en asimétricos, con el test que documenta la dirección del sesgo.
- ✅ Kelly con `p=0.55, odds=+100, fraction=0.25` devuelve exactamente `0.025` del bankroll; el cap corta a 5 units.
- ✅ `alembic upgrade head` y `downgrade base` limpios, más un test que detecta deriva entre modelos y migraciones.
- ✅ Cobertura de `core/` y `validation/`: **100%** (umbral 95% forzado en CI).

---

## Phase 2 — MLB end-to-end

Un solo deporte, un solo mercado (moneyline), ciclo completo funcionando.

MLB no es donde vive la ineficiencia — es el mercado más eficiente y con el vig
más bajo de la lista. Se elige igualmente para esta fase porque es el mejor sitio
para **construir infraestructura**: datos oficiales gratuitos y granulares
(MLB Stats API), volumen diario alto y temporada larga. Las expectativas de edge
de modelo se fijan en consecuencia: bajas.

Se parte en dos sub-fases para no mezclar "¿funciona el pipeline?" con
"¿funciona el modelo?".

### 2a — Pipeline con el mercado como modelo

**Lógica interna: completada.** Ver `CHANGELOG.md`.

- ✅ Entity resolution + cola de `unmatched_entities`.
- ✅ **`market_consensus_v1`**: primer `SportModel`, devuelve el consenso sharp sin vig.
- ✅ Agregación de odds: consenso, mejor precio ejecutable, apertura, cierre, movimiento.
- ✅ Pipeline: consensus no-vig → candidates → filtros → confidence → stake.
- ✅ Instrumentación de jobs (`JobReport`) con la regla `matched == 0` ⇒ FAILED.
- 🟡 Providers HTTP (The Odds API v4, MLB Stats API) y sus normalizadores:
  escritos y testeados contra fixtures, **pendientes de verificar contra
  respuestas reales**.
- ✅ Persistencia del pipeline: candidates, recommendations, reasons y linaje completo.
- ⏳ Job de captura de closing line, **slate completo**.
- ✅ Data Health: ocho checks sobre la base, con panel persistido y salida por CLI.
- ✅ API mínima: recommendations, candidates, performance, models y data health.

Los normalizadores están escritos contra la documentación pública, no contra
respuestas verificadas: la política de red del entorno de desarrollo deniega la
salida a `statsapi.mlb.com` y `api.the-odds-api.com`. Están construidos para
fallar con diagnóstico preciso —qué clave faltaba, qué llegó en su lugar— en vez
de devolver cero en silencio, y `python -m sportstar.cli capture` convierte la
primera ejecución real en la verificación del esquema.

Al final de 2a el sistema recomienda apuestas sin que exista todavía ningún
modelo estadístico. Lo que mida aquí es edge estructural puro (`ARCHITECTURE.md`
§1.1) y es el suelo contra el que se juzga todo lo demás.

### 2b — Primer modelo estadístico

- `FeatureBuilder` MLB v1: fuerza de equipo, forma reciente, starting pitcher
  (FIP/xFIP), bullpen y su fatiga, splits de lateralidad, park factor, descanso,
  home/away. Todo con `as_of`.
- Modelos baseline: Elo con ajuste por pitcher + regresión logística.

**Criterios de salida**
- **Cobertura de cierres ≥ 95% del slate.** Habilita toda la validación posterior; sin esto Phase 3 y 4 no tienen muestra. Es el criterio que más fácil se pasa por alto y el único irrecuperable.
- **Vara de modelo:** Brier score en test temporal **≤ el de `market_consensus_v1`**. Si `mlb_moneyline_v1` no bate al mercado, no se despliega — y eso es un resultado válido de la fase, no un fracaso. Seguimos con el edge estructural y se busca el edge de modelo en mercados menos eficientes (Phase 6).
- **Reproducibilidad:** recalcular features con `as_of = bet_time` reproduce exactamente lo que se guardó en producción.
- Ninguna feature viola el invariante point-in-time (verificado, no asumido).
- `matched / received > 95%` sostenido durante 7 días de odds sync.
- Reconstruir cualquier recomendación histórica desde su linaje.

---

## Phase 3 — Backtesting engine

D1 (histórico de odds comprado) acelera esta fase pero ya no la bloquea: los
cierres del slate completo capturados desde Phase 2a generan histórico propio
utilizable.

- Replay point-in-time: reconstruye el estado del mundo en `T` y ejecuta el pipeline.
- **Evaluación contra el cierre sobre todos los candidates**, no solo sobre apuestas
  (`ARCHITECTURE.md` §4.6). Es la vía principal de validación por su muestra.
- Métricas: bets, record, units, ROI, yield, max drawdown, ratio tipo Sharpe, CLV.
- Cortes: deporte, mercado, bucket de edge, confidence, book, versión de modelo.
- Calibración: Brier, log loss, curva de fiabilidad, ROC-AUC.
- `sanity.py` integrado y bloqueante.

**Criterios de salida**
- Separación explícita en los reportes entre evaluación de **modelo** (todos los candidates) y evaluación de **filtro** (solo recomendaciones). Nunca se mezclan: tienen tamaños de muestra que difieren en dos órdenes de magnitud.
- El backtest sobre el periodo de paper trading reproduce sus resultados reales dentro de tolerancia. Si divergen, el backtest está mal — no el paper trading.
- Todo backtest pasa sanity checks o no muestra métricas.
- Curva de calibración publicada.
- Respuesta con datos a: ¿qué edge mínimo funciona? ¿qué confidence funciona?

**Regla anti-overfit:** el test set temporal se toca **una vez**. Cada iteración
sobre él lo convierte en train. Se registra cuántas veces se ha usado.

---

## Phase 4 — Live paper trading

Sin dinero real. Es la prueba honesta: el backtest puede engañarse, el paper
trading en vivo no.

- Generación automática de picks pre-partido con timestamp.
- Settlement automático + CLV.
- Reporte diario y rolling 7/30/season/all-time.
- Recalibración del Confidence Score → `confidence_version = 1`.

**Criterios de salida — operativos** (verificables en ~30 días)
- 30 días continuos sin intervención manual.
- Cero picks generados post-inicio del evento.
- Cero closing lines perdidas; cobertura del slate ≥ 95%.
- El backtest de Phase 3 reproduce los resultados del paper trading dentro de tolerancia.

**Criterios de salida — estadísticos** (no dependen del calendario, sino de la muestra)
- `model_beat_close` significativamente > 50% sobre **todos los candidates**, con n ≥ 1.000. Esta es la métrica que decide si hay ventaja real, y la muestra llega en semanas gracias a la captura del slate completo.
- Edge estructural de `market_consensus_v1` medido y positivo. Si ni el line shopping produce CLV, hay un bug en el pipeline de precios — no una falta de ventaja.

**Lo que NO es criterio de salida:** el ROI o el récord a 30 días. Con 60-90
apuestas la varianza lo domina por completo; leer ese número como señal es el
error que el riesgo R8 del audit describe. Se reporta, no se decide con él.

**PAPI SCORE** se define aquí, con evidencia. No antes.

---

## Phase 5 — NBA

El test real de la arquitectura: si añadir NBA obliga a tocar `core/`, el
diseño de Phase 1 estaba mal y se corrige antes de seguir.

- `SportAdapter` + `FeatureBuilder` NBA: off/def rating, pace, net rating, descanso,
  back-to-back, viaje, lesiones, lineup esperado.
- Modelo baseline NBA + backtest + paper trading.

**Criterios de salida**
- Cero cambios en `core/`. Cero `if sport ==` fuera de las capas de adaptador.
- NBA supera los mismos criterios que MLB en Phase 2 y 4.

---

## Phase 6 — Multideporte (NFL, NHL, NCAAB, NCAAF)

Un deporte entra solo cuando tiene: datos fiables, baseline, backtest,
paper trading y métricas. Sin excepciones — un deporte a medias contamina las
métricas agregadas y hace que el sistema parezca peor o mejor de lo que es.

Se amplía a spreads y totals, que en varios de estos deportes son mercados más
líquidos y con más ineficiencias que el moneyline.

---

## Phase 7 — Mobile UI

Web responsive mobile-first, luego PWA.

- Today (Best Bets / All Candidates / Games), Bet Detail, Performance, Models, Data Health.
- PWA instalable, dark mode, safe areas, touch targets.

**Criterio de salida:** abrir el iPhone y saber en **menos de 10 segundos** qué
apuestas valen la pena hoy. Se mide con cronómetro, no con opinión.

---

## Phase 8 — Alerts

Push notifications de edges relevantes. Requiere que Phase 4 haya demostrado que
un alert vale la interrupción. Umbral basado en histórico, no en intuición.

---

## Phase 9 — Advanced intelligence

Solo con lo anterior estable: ensembles, actualización bayesiana, modelos de
movimiento de mercado, señales de sharp books, player props, NLP de lesiones,
optimización de portfolio y gestión de apuestas correlacionadas.

El portfolio engine consume el `correlation_group` que se viene guardando desde
Phase 1.

---

## Phase 10 — AI agent

Capa conversacional sobre la API. El LLM consulta el sistema y narra; **no
estima probabilidades**. Toda cifra que diga tiene que ser trazable a una
consulta concreta.

---

## Orden de decisiones bloqueantes

| Decisión | Debe resolverse antes de |
|---|---|
| ~~D3 motor de BD~~ | ✅ resuelto: SQLite + WAL tras SQLAlchemy + Alembic |
| D5 books objetivo | Phase 2a — define qué books entran en el consenso sharp |
| D2 fuente de stats MLB | Phase 2b |
| D4 definición de unit | Phase 4 |
| D1 histórico de odds | opcional; acelera Phase 3, ya no la bloquea |

---

## Lo que no vamos a hacer

Registrado explícitamente para no reabrirlo cada dos semanas:

- Modelos complejos antes de tener baselines calibrados.
- Deportes nuevos antes de que el anterior pase Phase 4.
- Dinero real antes de Phase 4 completa con CLV positivo sostenido.
- Métricas propietarias (PAPI SCORE) sin evidencia que justifique los pesos.
- App nativa antes de que la PWA demuestre ser insuficiente.
- Optimizar el backtest hasta que quede bonito. Un backtest que mejora cada vez que lo tocas ya no mide nada.
- Desplegar un modelo que no bate a `market_consensus_v1` en calibración, por bueno que sea su ROI en backtest.
- Sacar conclusiones de un ROI con muestra de tres cifras.
