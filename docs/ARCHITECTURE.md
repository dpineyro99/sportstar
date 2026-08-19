# ARCHITECTURE — Sports Betting Intelligence

**Estado:** propuesta (Phase 0). No implementada.
**Regla de lectura:** este documento define *qué* y *por qué*. El esquema concreto
de tablas vive en `DATA_MODEL.md`.

---

## 1. Tesis del sistema

El sistema no predice partidos. **Busca errores de precio en el mercado.**

La diferencia es operativa, no filosófica:

- Un sistema que predice partidos optimiza accuracy → puede acertar el 70% de los
  favoritos y perder dinero.
- Un sistema que busca errores de precio optimiza **calibración** y solo actúa
  cuando su probabilidad difiere de la del mercado más allá del ruido.

Corolario que gobierna todo el diseño: **la salida primaria de un modelo es una
probabilidad calibrada, no un pick.** El pick es una consecuencia de comparar esa
probabilidad con un precio.

---

## 2. Las tres decisiones irreversibles

Se toman ahora porque retrofitearlas destruye el histórico.

### 2.1 Odds append-only

`odds_snapshots` no admite `UPDATE` ni `DELETE`. Cada observación de precio es
una fila nueva con `captured_at`. Todo lo demás son vistas derivadas:

| Concepto | Derivación |
|---|---|
| Opening line | primer snapshot por `(event, book, market, selection)` |
| Current line | último snapshot |
| Closing line | último snapshot con `captured_at < event.start_time` |
| Best available | `max(price)` entre books en un instante `T` |
| Consensus | agregación no-vig de books seleccionados en `T` |
| Line movement | serie temporal completa |

Sin esto no hay CLV, no hay backtest reproducible y no hay análisis de movimiento
de línea. Y no se puede reconstruir después: el precio de ayer se perdió ayer.

### 2.2 Features con `as_of` obligatorio

Toda fila de features lleva `as_of TIMESTAMP NOT NULL` y **solo** puede calcularse
con hechos cuyo timestamp de disponibilidad sea `< as_of`.

Esto convierte la prevención de leakage en un invariante estructural en lugar de
una disciplina. El backtest se reduce a: *recalcular features con
`as_of = bet_time`*. Si esa operación devuelve algo distinto de lo que se guardó
en producción, hay un bug — y es detectable automáticamente.

El caso sutil: los datos que llegan tarde. El ERA de un pitcher "del 3 de junio"
puede haberse corregido el 5 de junio. Por eso el criterio no es la fecha del
hecho sino **la fecha en que el hecho estuvo disponible para nosotros**
(`observed_at`), que es lo que guardamos en la capa raw.

### 2.3 Taxonomía de mercados genérica desde el commit 1

Moneyline es un caso particular. La clave canónica de un precio es:

```
(event_id, book_id, market_type, period, subject, side, line)
```

- `market_type` — moneyline | spread | total | team_total | player_prop
- `period` — game | 1H | 2H | 1Q | ... | inning_1_5
- `subject` — a qué entidad se refiere (team_id, player_id, o el evento mismo)
- `side` — home/away, over/under, yes/no
- `line` — el número (-4.5, 8.5, 27.5); `NULL` en moneyline

Con esta clave, añadir player props en Phase 9 es rellenar `subject` con un
`player_id`. Sin ella, es una migración con reescritura de todo el pipeline.

---

## 3. Flujo de datos

```
                    PROVIDERS (externos)
    schedule/scores    stats     injuries    odds     weather
          |              |          |         |          |
          v              v          v         v          v
  +--------------------------------------------------------------+
  |  RAW LAYER    payload íntegro + observed_at + provider        |
  |               inmutable, append-only, nunca se parsea aquí    |
  +--------------------------------------------------------------+
          |
          v  normalizers/  +  entity resolution
  +--------------------------------------------------------------+
  |  NORMALIZED   sports leagues teams players events books       |
  |               markets selections odds_snapshots                |
  +--------------------------------------------------------------+
          |
          v  features/   (as_of obligatorio)
  +--------------------------------------------------------------+
  |  FEATURES     feature_sets versionados, point-in-time         |
  +--------------------------------------------------------------+
          |
          v  models/     (model_version obligatorio)
  +--------------------------------------------------------------+
  |  PREDICTIONS  probabilidad + incertidumbre + linaje           |
  +--------------------------------------------------------------+
          |
          |<-------- odds/  fair probability (no-vig, sharp consensus)
          v  edge  ->  EV  ->  filters  ->  sizing
  +--------------------------------------------------------------+
  |  CANDIDATES ---filtro---> RECOMMENDATIONS                     |
  +--------------------------------------------------------------+
          |
          v  paper trading / ejecución
  +--------------------------------------------------------------+
  |  BETS  ->  settlement  ->  RESULTS  ->  CLV  ->  PERFORMANCE  |
  +--------------------------------------------------------------+
```

Cada flecha es una frontera con contrato explícito. Ninguna capa lee dos capas
hacia atrás: el modelo no toca la raw layer, el filtro no recalcula probabilidades.

---

## 4. El núcleo matemático

Todo esto son **funciones puras** en `core/`, sin I/O, sin dependencias de deporte,
con tests deterministas obligatorios. Es la parte del sistema donde un bug es
silencioso y caro.

### 4.1 Odds → probabilidad

```
american_to_decimal(-115) = 1.8696
decimal_to_implied(1.8696) = 0.5349      # implied, CON vig
```

Quitar el vig de un mercado de dos lados (`p_raw` suman > 1):

| Método | Cuándo | Nota |
|---|---|---|
| Proporcional | default v1 | `p_i / Σp`. Simple; sesga en longshots. |
| Shin | mercados con favoritos fuertes | modela insider trading; mejor en two-way |
| Power | alternativa | resuelve `Σ p_i^k = 1` |

**No elegimos por gusto.** Se implementan los tres y se decide midiendo cuál
predice mejor el resultado real usando closing lines históricos. Hasta entonces,
proporcional con el sesgo documentado.

### 4.2 Fair probability: contra quién comparamos

Decisión de diseño crítica (riesgo R1 del audit):

```
fair_prob   <- no-vig del CONSENSO DE SHARP BOOKS
taken_price <- MEJOR PRECIO disponible en books ejecutables
edge        <- model_prob - fair_prob
EV          <- se calcula con taken_price, no con el precio sharp
```

El edge se mide contra la mejor estimación que existe del mercado. El EV se
calcula con el precio que realmente puedes conseguir. Confundirlos produce
o bien edge fantasma o bien EV subestimado.

### 4.3 EV y ROI

```
EV_por_unidad = p_model * (decimal_odds - 1) - (1 - p_model)
Expected ROI  = EV_por_unidad                # ya está normalizado a stake=1
```

Se reporta como "Expected ROI: +8.4%". Nunca se reporta EV en dólares en el
dashboard: oculta el tamaño del stake.

### 4.4 Kelly

```
b = decimal_odds - 1
f_full = (p_model * (b + 1) - 1) / b
stake  = clamp(kelly_fraction * f_full * bankroll_units, 0, max_stake)
```

Defaults: `kelly_fraction = 0.25`, `max_stake = 5 units`, `1 unit = 1% bankroll`.

Kelly asume que `p_model` es correcta. No lo es. La fracción 0.25 no es una
preferencia de riesgo: es una corrección por error de estimación del modelo.
El cap absoluto existe porque Kelly con una probabilidad mal estimada en un
longshot produce stakes absurdos, y ese es exactamente el escenario donde el
modelo es menos fiable.

### 4.5 CLV

Dos métricas, ambas necesarias:

```
CLV_precio       = (decimal_tomada / decimal_cierre) - 1
CLV_probabilidad = fair_prob_cierre - fair_prob_al_apostar
beat_close_rate  = % de apuestas con CLV_precio > 0
```

El CLV de probabilidad usa el cierre **sin vig**, que es la estimación final y
mejor calibrada del mercado. Es la métrica de referencia para decidir si una
estrategia funciona antes de tener muestra suficiente de P&L.

---

## 5. Módulos

```
src/
  core/            # funciones puras: odds, vig, edge, ev, kelly, clv
  data/
    providers/     # un adaptador por API externa; devuelve raw
    normalizers/   # raw -> modelo canónico
    resolution/    # entity matching: teams, players, events, aliases
  features/
    base.py        # FeatureBuilder ABC, contrato as_of
    mlb/ nba/ ...  # builders por deporte
  models/
    registry.py    # versionado, carga, metadata
    base.py        # SportModel ABC: fit / predict_proba / uncertainty
    baselines/     # elo, logistic, poisson
  markets/         # taxonomía, parsing, matching de selections
  odds/            # consensus, no-vig, best price, line movement
  edge/            # candidate generation
  filters/         # candidate -> recommendation, confidence score
  portfolio/       # sizing, correlación, exposición (Phase 9)
  backtesting/     # replay point-in-time, métricas
  validation/      # calibración + sanity checks anti-bug
  settlement/      # resultados, CLV, P&L
  api/             # FastAPI
  workers/         # jobs programados
  cli/             # operación manual
tests/
  core/            # deterministas, obligatorios
  ...
docs/
frontend/          # Phase 7
```

### 5.1 Interfaces que hacen posible el multideporte

Tres puntos de extensión. Añadir un deporte = implementar los tres, sin tocar el core.

```python
class SportAdapter(Protocol):
    sport_key: str
    def markets_supported(self) -> list[MarketType]: ...
    def settle(self, event: Event, bet: Bet) -> Settlement: ...

class FeatureBuilder(Protocol):
    version: str
    def build(self, event: Event, as_of: datetime) -> FeatureVector: ...
    # contrato: prohibido leer datos con observed_at >= as_of

class SportModel(Protocol):
    name: str
    version: str
    def predict_proba(self, fv: FeatureVector) -> Prediction: ...
    # Prediction lleva probabilidad E incertidumbre, no solo el punto
```

Y en el lado de los datos:

```python
class OddsProvider(Protocol):
    def fetch(self, sport: str) -> list[RawOddsPayload]: ...
class OddsNormalizer(Protocol):
    def normalize(self, raw: RawOddsPayload) -> list[OddsSnapshot]: ...
```

**Regla dura:** el core nunca hace `if sport == "MLB"`. Si aparece esa línea,
el punto de extensión está mal diseñado.

### 5.2 Entity resolution: el módulo que todos subestiman

Emparejar el evento del proveedor de odds con el del proveedor de stats es,
empíricamente, donde se va la mitad del tiempo de mantenimiento. "NY Yankees"
vs "New York Yankees" vs "NYY". Partidos dobles. Cambios de horario. Suspensiones.

Diseño: tabla persistente de alias + matching por `(fecha, equipos normalizados)`
con umbral de confianza. **Lo que no empareja no se descarta en silencio: se
escribe en una cola de revisión y se cuenta en el log.** Un `matched: 0` debe
gritar, nunca pasar desapercibido.

---

## 6. Candidate vs Recommendation

Separación deliberada — permite medir el filtro independientemente del modelo.

**Candidate:** existe para todo par (predicción, precio) con `edge > 0`. Se
persiste siempre, aunque no se recomiende. Es lo que permite responder después
"¿qué habría pasado si el umbral fuera 2% en vez de 3%?".

**Recommendation:** un candidate que pasa todos los gates:

| Gate | v1 | Racional |
|---|---|---|
| `edge >= min_edge` | 2.0% | por debajo, es ruido del modelo |
| `EV >= min_ev` | 1.0% | tras costes |
| `line_freshness <= max_age` | 10 min | evita edge fantasma (R5) |
| `data_quality >= threshold` | sin flags críticos | |
| book disponible y ejecutable | sí | |
| dispersión entre modelos | acotada | desacuerdo alto = baja confianza |

Los umbrales v1 son **provisionales**. Se recalibran con el backtest de Phase 3,
que es exactamente la pregunta "¿qué edge mínimo funciona?".

### 6.1 Confidence Score (v0 — provisional)

Riesgo R3 del audit: cualquier fórmula escrita hoy es arbitraria. Se define
explícitamente, se versiona como `confidence_version = 0`, y se recalibra en
Phase 4 contra resultados reales.

Componentes, todos medibles hoy:

| Componente | Peso v0 | Qué mide |
|---|---|---|
| Edge / incertidumbre del modelo | 0.30 | edge en desviaciones estándar, no en puntos |
| Acuerdo entre modelos | 0.20 | dispersión del ensemble |
| Calidad de datos | 0.15 | features completas, sin stale |
| Tamaño de muestra de las features | 0.15 | equipo con 8 partidos ≠ con 80 |
| Frescura de línea | 0.10 | |
| Calibración histórica en ese bucket | 0.10 | 0 hasta tener histórico |

Se escala a 0-10. **La justificación de los pesos es que son un punto de partida
razonable y auditables — no que estén validados.** El documento lo dice, el
dashboard lo dice, y el número no se presenta con decimales falsos de precisión.

**PAPI SCORE:** no se define hasta Phase 4. Requiere evidencia para los pesos.

---

## 7. Explicaciones honestas

`RecommendationReason` se genera de las **contribuciones reales** del modelo
(coeficientes × valores de feature en modelos lineales; SHAP en modelos de árbol),
no de texto libre:

```
1. Ventaja de starting pitcher   +2.1%
2. Ventaja de bullpen            +1.0%
3. Matchup ofensivo              +0.8%
4. Home field                    +0.6%
5. Discrepancia de mercado       +1.3%
```

Una capa de lenguaje natural puede redactar esto después. **Nunca puede añadir
un factor que el modelo no usó.** Si el modelo no consume lesiones, la explicación
no menciona lesiones — aunque quede peor.

Esto también aplica a la capa de agente conversacional de Phase 10: el LLM
consulta la API y narra lo que el sistema calculó. No estima probabilidades.

---

## 8. Observabilidad

Todo job estructurado devuelve un `JobReport` persistido:

```
SYNC ODDS  [mlb]  provider=the-odds-api  run_id=...
  events received : 84
  matched         : 81
  unmatched       : 3     -> cola de revisión (ids listados)
  snapshots       : 264
  duration        : 4.2s
  errors          : 0
```

**Un job que no encuentra nada es un fallo, no un éxito silencioso.** Si
`matched == 0` cuando `received > 0`, el job termina en estado `FAILED` con el
motivo, y aparece en Data Health. Los procesos que fallan en silencio son la
causa raíz de casi todo backtest engañoso.

Checks de Data Health: equipos faltantes, eventos duplicados, odds stale, horarios
inconsistentes, probabilidades imposibles, juegos sin odds, odds sin juego,
jugadores desconocidos, conflictos de IDs.

---

## 9. Workers

Frecuencias distintas por naturaleza del dato:

| Job | Frecuencia | Nota |
|---|---|---|
| schedule sync | 2×/día | |
| stats sync | diario post-partidos | |
| injuries / lineups | cada 30 min; 15 min pre-game | MLB: lineup confirmado importa |
| odds sync | cada 5-10 min; 2 min pre-game | el más caro y el más crítico |
| predictions | tras lineups | |
| recommendations | tras cada odds sync | el precio se mueve, el edge también |
| settlement | tras final del evento | |
| closing line capture | al `start_time` | **no se puede recuperar después** |

`closing line capture` es el job cuyo fallo es irreversible. Merece alerta propia.

---

## 10. API y estrategia mobile

La API es el contrato único. Streamlit, si se usa, es herramienta interna de
debug — **nunca el producto**. Acoplar la UI al backend cierra la puerta al
iPhone, que es el objetivo declarado.

```
GET /v1/recommendations?date=&sport=&min_confidence=
GET /v1/recommendations/{id}          # detalle + reasons + line movement
GET /v1/candidates?...                # todo, sin filtrar
GET /v1/events/{id}
GET /v1/performance?window=30d&group_by=sport|market|edge_bucket|model
GET /v1/models                        # registry + métricas por versión
GET /v1/health/data
```

Diseñada para que un cliente nativo futuro no requiera cambios de backend:
JSON puro, sin estado de sesión, paginada, con timestamps ISO-8601 en UTC.

**Fase 1 mobile:** web responsive, mobile-first. Objetivo medible: abrir el
iPhone y saber en <10 segundos qué apuestas valen la pena hoy. Una sola pantalla,
cards ordenadas por calidad, sin scroll horizontal, sin tablas densas.

**Fase 2 mobile:** PWA — instalable, safe areas, dark mode, offline del último
fetch.

**Fase 3 mobile:** evaluar nativo solo si la PWA resulta insuficiente. Las push
notifications son la única razón técnica real para dar ese salto, y no se
implementan hasta que el motor de recomendaciones tenga histórico que demuestre
que un alert vale la interrupción.
