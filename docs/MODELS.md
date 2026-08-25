# MODELS — qué modelos hay, qué producen y cuál está desplegado

> **Estado a fecha de hoy: ninguno desplegado.** El único que sobrevive a la
> evaluación es `market_consensus_v1`, que por construcción no recomienda nada.
> Ver [`BACKTESTING.md`](BACKTESTING.md) para los números.

---

## 1. El criterio de despliegue

Un modelo se despliega si y solo si cumple **las dos** condiciones a la vez:

1. **mejor Brier que el mercado de apertura**, sobre todos los candidates
2. **más cerca del cierre que el mercado** más del 50% de las veces

Las dos, no una. Un modelo puede mejorar el Brier por estar mejor calibrado sin
aportar información nueva, y puede acercarse al cierre por ruido. Exigir ambas es
lo que separa señal de casualidad — y ya evitó un falso positivo real:
`elo_blend w=0,05` mejoró el Brier del mercado en holdout (+0,00002) con una
tasa de cercanía al cierre de 0,42. Con una sola condición se habría promovido.

**El ROI de backtest no es criterio de despliegue.** Su muestra es dos órdenes de
magnitud menor que la de la calibración, y llega dos temporadas más tarde.

---

## 2. Los modelos

### `market_consensus_v1` — la baseline

Devuelve la probabilidad justa —sin vig— de la **línea de apertura**.

- **Edge de modelo: 0 por construcción.** No recomienda nada, nunca.
- **`beat_market_rate`: 0 por construcción.** No puede estar más cerca del cierre
  que ella misma.
- Usa la apertura y no el cierre a propósito: el cierre es información que en el
  momento de apostar no existía, y usarlo la convertiría en un oráculo.

Su valor no es apostar, es **fijar el listón**. Sin ella cualquier modelo
"funciona", porque no hay contra qué compararlo.

| | train 2011-2018 | holdout 2019-2021 |
|---|---|---|
| Brier | 0,24234 | 0,23853 |
| ECE | 0,00286 | — |

### `elo_v1`

Elo estándar sobre resultados, sin mirar el mercado. K=4, ventaja local 24
puntos, regresión entre temporadas 0,30. `min_games=20` para no opinar sobre
equipos con dos partidos jugados: a principio de temporada el rating es casi el
inicial y la predicción es ruido que entra al backtest como si fuese señal.

**Veredicto: no aporta.** Peor Brier que el mercado en train (−0,00280) y en
holdout (−0,00316). Más cerca del cierre solo el **27,6%** de las veces. Apostando
sus 8.635 recomendaciones habría perdido un **4,66%**.

### `elo_blend_v1-w{peso}`

Mezcla lineal de Elo con el mercado de apertura, con peso fijo. Existe porque es
la forma honesta de preguntar *"¿aporta Elo algo **marginal** que el mercado no
tenga ya?"*.

**Veredicto: no.** La degradación es **monótona en el peso**:

| peso | Brier vs mercado (train) | cerca del cierre |
|---|---|---|
| 0,05 | −0,00002 | 0,4864 |
| 0,10 | −0,00005 | 0,4742 |
| 0,20 | −0,00014 | 0,4480 |

Cada dosis de Elo empeora la predicción, y lo hace en proporción a la dosis. Eso
es lo que hace robusto el negativo: no es un resultado que pueda venir del ruido.

---

## 3. Versionado

Toda predicción lleva `strategy` y `strategy_version`, y esa versión viaja con el
candidate hasta el informe. El peso del blend va **dentro** de la versión
(`v1-w0.15`) porque es un parámetro que cambia lo que el modelo predice: dos
pesos distintos son dos modelos distintos, no una configuración del mismo.

---

## 4. Por qué ninguno funciona, y qué haría falta

Elo solo sabe quién ganó. En MLB eso es una fracción pequeña de lo que determina
un partido, y el mercado ya la tiene incorporada mucho antes de la apertura.

Lo que falta, por orden de impacto esperado:

1. **Lanzador abridor.** Es el factor dominante y el modelo no lo ve en absoluto.
   Un mismo equipo con dos abridores distintos es, a efectos de mercado, dos
   equipos distintos.
2. **Bullpen** — disponibilidad y carga reciente.
3. **Parque** — factores de carrera por estadio.
4. **Alineación** — quién juega de verdad, no la plantilla.

Nada de esto está en el archivo histórico de odds; requiere endpoints
adicionales de la MLB Stats API. Es el contenido de Phase 2b.

**Hasta entonces, la respuesta honesta a "¿qué modelo desplegamos?" es
"ninguno".** Desplegar `elo_v1` sabiendo que pierde un 4,66% no sería un
experimento, sería pagar por confirmarlo.
