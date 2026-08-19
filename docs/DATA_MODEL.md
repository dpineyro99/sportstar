# DATA MODEL

**Estado:** propuesta (Phase 0). No implementada.
**Motor:** SQLite + WAL vía SQLAlchemy + Alembic. Esquema compatible con Postgres
para migrar sin reescribir cuando la concurrencia lo pida.

Convenciones: todos los timestamps en **UTC**, tipo `TIMESTAMP`, sufijo `_at`.
IDs internos propios; los IDs de proveedores viven en `external_ids`.

---

## 1. Capa RAW (inmutable)

```sql
raw_payloads (
  id, provider, endpoint, sport_key,
  payload          JSON      -- respuesta íntegra, sin tocar
  requested_at, observed_at, -- cuándo pedimos / cuándo lo supimos
  http_status, run_id
)
```

Nunca se borra ni se parsea en esta capa. Permite reprocesar todo el histórico
cuando un normalizador tenga un bug, sin volver a pagar la API.

`observed_at` es la base del contrato point-in-time: fija cuándo el hecho estuvo
disponible **para nosotros**, que es lo que importa para el backtest — no la
fecha nominal del hecho.

---

## 2. Capa NORMALIZED

### 2.1 Catálogo

```sql
sports    (id, key, name)                       -- mlb, nba, nfl, nhl, ncaab, ncaaf
leagues   (id, sport_id, key, name, season_type)
teams     (id, league_id, key, name, abbreviation, conference, division)
players   (id, team_id, league_id, full_name, position, bats, throws, active)
venues    (id, name, city, tz, altitude, roof, park_factors JSON)

external_ids (
  id, entity_type, entity_id,   -- 'team' | 'player' | 'event'
  provider, provider_id,
  UNIQUE(entity_type, provider, provider_id)
)

entity_aliases (
  id, entity_type, entity_id, alias, source, confidence
)
```

`external_ids` + `entity_aliases` son el corazón del entity resolution
(`ARCHITECTURE.md` §5.2). Sin ellas, cada proveedor nuevo es un incendio.

### 2.2 Eventos

```sql
events (
  id, league_id, season, event_date,
  start_time,            -- UTC, programado
  actual_start_time,
  home_team_id, away_team_id, venue_id,
  status,                -- scheduled | live | final | postponed | cancelled
  home_score, away_score,
  weather JSON,
  created_at, updated_at,
  UNIQUE(league_id, event_date, home_team_id, away_team_id)
)

event_participants (       -- starters, pitchers, goalies, QBs
  id, event_id, team_id, player_id,
  role,                    -- starting_pitcher | goalie | qb | lineup_1..9
  status,                  -- projected | confirmed | scratched
  observed_at              -- clave: cuándo se confirmó
)

injuries (
  id, player_id, team_id, status, description,
  reported_at, observed_at
)
```

`event_participants.observed_at` es lo que permite responder honestamente
"¿sabíamos el lineup confirmado cuando apostamos?". Sin ese campo, el backtest
usa lineups que en su momento no existían — leakage clásico y difícil de detectar.

### 2.3 Books y mercados

```sql
sportsbooks (
  id, key, name,
  book_type,        -- sharp | recreational | exchange
  is_reference,     -- entra en el consenso sharp
  is_executable,    -- puedo apostar realmente aquí
  region
)

markets (
  id, sport_id, market_type, period, description,
  UNIQUE(sport_id, market_type, period)
)
-- market_type: moneyline | spread | total | team_total | player_prop
-- period:      game | 1H | 2H | 1Q | ... | inning_1_5

selections (
  id, event_id, market_id,
  subject_type,     -- event | team | player
  subject_id,
  side,             -- home|away|over|under|yes|no
  line,             -- -4.5, 8.5, 27.5; NULL en moneyline
  UNIQUE(event_id, market_id, subject_type, subject_id, side, line)
)
```

Esta es la taxonomía de `ARCHITECTURE.md` §2.3. Player props entran rellenando
`subject_type='player'`, sin migración.

### 2.4 Odds — APPEND ONLY

```sql
odds_snapshots (
  id,
  selection_id, sportsbook_id,
  price_american, price_decimal,
  line,                     -- redundante pero congelada: las líneas se mueven
  implied_prob,             -- CON vig, tal cual viene
  is_available,
  captured_at,
  run_id
)
CREATE INDEX ix_odds_sel_book_time ON odds_snapshots(selection_id, sportsbook_id, captured_at);
```

**Sin `UPDATE`. Sin `DELETE`. Sin excepciones.** Si un precio cambia, es una fila
nueva. La tabla crece rápido y da igual: es el activo más valioso del sistema
y el único que no se puede reconstruir a posteriori.

Vistas derivadas (materializadas si hace falta): `v_opening_line`,
`v_current_line`, `v_closing_line`, `v_best_available`, `v_consensus_novig`.

---

## 3. Capa FEATURES

```sql
feature_sets (
  id, sport_id, name, version,
  spec JSON,                -- lista de features y cómo se calculan
  created_at
)

event_features (
  id, event_id, team_id, feature_set_id,
  as_of          NOT NULL,  -- INVARIANTE del sistema
  features       JSON,
  data_quality_score,
  missing_features JSON,
  computed_at,
  UNIQUE(event_id, team_id, feature_set_id, as_of)
)
```

`as_of` no es metadata, es parte de la clave. El mismo evento tiene features
distintas a las 10:00 y a las 18:00, y ambas son correctas para su momento.

**Invariante verificable:** ninguna feature en una fila con `as_of = T` puede
derivarse de un registro con `observed_at >= T`. `validation/sanity.py` lo
comprueba y falla duro si se viola.

---

## 4. Capa MODELS y PREDICTIONS

```sql
model_versions (
  id, name, version,        -- 'mlb_moneyline', 'v3'
  sport_id, market_type,
  algorithm, hyperparams JSON,
  feature_set_id,
  train_start, train_end,   -- split temporal, nunca aleatorio
  val_start, val_end,
  test_start, test_end,
  metrics JSON,             -- brier, log_loss, auc, calibration_error
  artifact_path, artifact_hash,
  trained_at, is_active,
  UNIQUE(name, version)
)

predictions (
  id, event_id, selection_id, model_version_id,
  event_features_id,        -- linaje exacto de features
  probability,
  prob_lower, prob_upper,   -- incertidumbre, no solo el punto
  as_of      NOT NULL,
  created_at,
  UNIQUE(selection_id, model_version_id, as_of)
)
```

Con `predictions.event_features_id` + `model_version_id` cualquier predicción
histórica es reconstruible bit a bit. Es el requisito #10 del brief
("poder reconstruir cualquier apuesta histórica") hecho esquema.

`prob_lower/upper` existen porque el Confidence Score necesita el edge medido en
desviaciones estándar, no en puntos porcentuales (`ARCHITECTURE.md` §6.1).

---

## 5. Capa EDGE

```sql
candidates (
  id, event_id, selection_id, prediction_id,

  -- precio de referencia (sharp consensus)
  reference_odds_snapshot_id,
  market_implied_prob,      -- CON vig
  market_fair_prob,         -- SIN vig
  novig_method,             -- proportional | shin | power

  -- precio ejecutable (mejor disponible)
  best_odds_snapshot_id,
  best_sportsbook_id,
  best_price_american, best_price_decimal,

  model_prob,
  edge,                     -- model_prob - market_fair_prob
  expected_value,           -- calculado con best_price
  expected_roi,

  line_age_seconds,
  data_quality_score,
  model_agreement,

  as_of, created_at
)
```

Las tres probabilidades viven en columnas separadas y nunca se sobrescriben
entre sí. Confundir `market_implied_prob` con `market_fair_prob` infla el edge
de forma sistemática — es el error más común del dominio y aquí es
estructuralmente imposible.

Se persiste **todo** candidate, incluidos los que no se recomiendan. Es lo que
permite responder después "¿qué habría pasado con umbral 2% en vez de 3%?".

```sql
recommendations (
  id, candidate_id,
  confidence_score, confidence_version,
  recommended_stake_units,
  sizing_method,            -- flat | kelly_fractional
  kelly_fraction,
  filter_version,
  passed_filters JSON, failed_filters JSON,
  correlation_group,        -- riesgo R6: se guarda desde el día 1
  status,                   -- active | superseded | expired
  created_at
)

recommendation_reasons (
  id, recommendation_id, rank,
  factor_key, factor_label,
  contribution,             -- en puntos de probabilidad
  source                    -- model_coefficient | shap | market
)
```

`recommendation_reasons` sale de las contribuciones reales del modelo. Ninguna
fila puede existir sin un factor que el modelo haya consumido de verdad
(`ARCHITECTURE.md` §7).

---

## 6. Capa BETS y RESULTS

```sql
bets (
  id, recommendation_id, selection_id, sportsbook_id,
  is_paper,                 -- Phase 4 = todo true
  stake_units,
  price_american_taken, price_decimal_taken,
  line_taken,
  fair_prob_at_bet,
  model_prob_at_bet,
  placed_at,
  model_version_id, filter_version, confidence_version
)
```

Se copian precio, línea y probabilidades **al momento de apostar** en vez de
referenciarlas. Redundancia deliberada: la apuesta es un hecho histórico
congelado y no debe cambiar si se recalcula algo aguas arriba.

```sql
bet_results (
  id, bet_id,
  outcome,                  -- win | loss | push | void
  profit_units,

  closing_odds_snapshot_id,
  closing_price_decimal,
  closing_fair_prob,
  clv_price,                -- (dec_tomada / dec_cierre) - 1
  clv_probability,          -- fair_close - fair_at_bet
  beat_closing_line,        -- bool

  settled_at, settlement_source
)
```

CLV se calcula en el settlement porque el closing line solo existe entonces.
Si el job de captura de cierre falló, estos campos quedan `NULL` — y eso es un
incidente de Data Health, no un dato ausente cualquiera: es irrecuperable.

---

## 7. Operación

```sql
job_runs (
  id, job_name, sport_key, run_id,
  started_at, finished_at,
  status,                   -- success | partial | failed
  counters JSON,            -- received, matched, unmatched, snapshots, errors
  error_summary
)

data_health_checks (
  id, check_name, severity, entity_type, entity_id,
  message, detected_at, resolved_at
)

unmatched_entities (       -- cola de revisión de entity resolution
  id, provider, entity_type, raw_value, context JSON,
  first_seen_at, occurrences, resolved_to_id
)
```

`matched == 0 && received > 0` ⇒ `status = 'failed'`. Nunca `success`
(`ARCHITECTURE.md` §8).

---

## 8. Backtesting

```sql
backtests (
  id, name,
  model_version_id, feature_set_id, filter_version,
  period_start, period_end,
  config JSON,
  metrics JSON,
  sanity_checks JSON,       -- resultado de validation/sanity.py
  passed_sanity,            -- si es false, las métricas NO se muestran
  created_at
)

backtest_bets (
  id, backtest_id, event_id, selection_id,
  as_of, model_prob, fair_prob, edge,
  price_decimal, stake_units,
  outcome, profit_units, clv_price
)
```

`passed_sanity = false` bloquea la presentación de resultados. Un backtest que
dispara un check no produce un número con asterisco: produce un error que hay
que investigar antes de mirar el ROI (`CURRENT_SYSTEM_AUDIT.md` §2.3).

---

## 9. Índices y crecimiento

`odds_snapshots` domina el tamaño: ~6 books × ~5 mercados × ~15 eventos × ~100
capturas/día ≈ 45k filas/día por deporte en temporada alta. Manejable en SQLite
con WAL durante la primera temporada; el volumen es la razón por la que el
esquema se mantiene compatible con Postgres desde el principio.

Índices críticos: `odds_snapshots(selection_id, sportsbook_id, captured_at)`,
`events(league_id, event_date)`, `event_features(event_id, as_of)`,
`predictions(selection_id, as_of)`, `bets(placed_at)`.
