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

## Phase 1 — Core data model + núcleo matemático

Fundamentos. Sin datos externos todavía.

- Scaffolding: `pyproject`, SQLAlchemy + Alembic, pytest, ruff, mypy, CI.
- Esquema completo de `DATA_MODEL.md` en la migración inicial.
- `core/`: conversión de odds, los tres métodos de no-vig, edge, EV, Kelly, CLV.
- `validation/sanity.py` con los checks del audit §2.3.
- Seed de catálogo: sports, leagues, MLB teams, sportsbooks.

**Criterios de salida**
- `pytest tests/core` verde con casos deterministas calculados a mano.
- Los tres métodos de no-vig coinciden dentro de tolerancia en mercados equilibrados y divergen como se espera en longshots — con el test que lo documenta.
- Kelly con `p=0.55, odds=+100, fraction=0.25` devuelve exactamente `0.025` del bankroll; el cap corta a 5 units.
- `alembic upgrade head` y `downgrade base` limpios.
- Cobertura de `core/` > 95%. Es la única parte del sistema donde exijo ese número: un bug aquí es silencioso y contamina todo aguas abajo.

---

## Phase 2 — MLB end-to-end

Un solo deporte, un solo mercado (moneyline), ciclo completo funcionando.

- Providers: schedule/scores, stats, lineups/pitchers, odds.
- Entity resolution + cola de `unmatched_entities`.
- `FeatureBuilder` MLB v1: fuerza de equipo, forma reciente, starting pitcher
  (FIP/xFIP), bullpen y su fatiga, splits de lateralidad, park factor, descanso,
  home/away. Todo con `as_of`.
- Modelos baseline: Elo con ajuste por pitcher + regresión logística.
- Pipeline: odds sync → consensus no-vig → candidates → filtros → recommendations.
- Job de captura de closing line.
- API mínima + Data Health.

**Criterios de salida** — los dos primeros son los que de verdad importan:
- **Calibración:** Brier score del modelo en test temporal **≤ el de la línea de apertura del mercado**. Si no llega, no se avanza: se itera en features o se acepta que MLB moneyline no es nuestro mercado. Este es el riesgo R4 del audit y es el punto donde el proyecto se valida o se replantea.
- **Reproducibilidad:** recalcular features con `as_of = bet_time` reproduce exactamente lo que se guardó en producción.
- Ninguna feature viola el invariante point-in-time (verificado, no asumido).
- `matched / received > 95%` sostenido durante 7 días de odds sync.
- Reconstruir cualquier recomendación histórica desde su linaje.

---

## Phase 3 — Backtesting engine

Depende de D1 (histórico de odds). **Resolver antes de empezar.**

- Replay point-in-time: reconstruye el estado del mundo en `T` y ejecuta el pipeline.
- Métricas: bets, record, units, ROI, yield, max drawdown, ratio tipo Sharpe, CLV.
- Cortes: deporte, mercado, bucket de edge, confidence, book, versión de modelo.
- Calibración: Brier, log loss, curva de fiabilidad, ROC-AUC.
- `sanity.py` integrado y bloqueante.

**Criterios de salida**
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

**Criterios de salida**
- 30 días continuos sin intervención manual.
- **`beat_closing_line > 50%` de forma sostenida.** Esta es la métrica que decide si el sistema tiene ventaja real. El P&L a 30 días no dice nada — la varianza lo domina.
- Cero picks generados post-inicio del evento.
- Cero closing lines perdidas.

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
| D3 motor de BD | Phase 1 |
| D2 fuente de stats MLB | Phase 2 |
| D5 books objetivo | Phase 2 |
| D1 histórico de odds | Phase 3 |
| D4 definición de unit | Phase 4 |

---

## Lo que no vamos a hacer

Registrado explícitamente para no reabrirlo cada dos semanas:

- Modelos complejos antes de tener baselines calibrados.
- Deportes nuevos antes de que el anterior pase Phase 4.
- Dinero real antes de Phase 4 completa con CLV positivo sostenido.
- Métricas propietarias (PAPI SCORE) sin evidencia que justifique los pesos.
- App nativa antes de que la PWA demuestre ser insuficiente.
- Optimizar el backtest hasta que quede bonito. Un backtest que mejora cada vez que lo tocas ya no mide nada.
