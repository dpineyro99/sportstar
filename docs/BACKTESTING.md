# Backtesting — cómo funciona y qué dijo

> **Resultado, por delante de todo:** sobre 25.560 partidos de MLB (2011-2021),
> **ningún modelo bate al mercado**. Elo lo empeora, y mezclarlo con el mercado lo
> empeora en proporción al peso que se le dé. La conclusión se sostiene en train y
> en holdout, y con dos métricas independientes.

---

## 1. Qué se apuesta y contra qué se mide

El archivo histórico tiene dos precios por partido: apertura y cierre. Eso fija
el diseño y no deja alternativa razonable:

- **se apuesta a la apertura** — es el precio más temprano que existe en el dato
- **se mide contra el cierre** — es el mejor estimador público de la probabilidad real
- **se liquida con el marcador**

Apostar al cierre no tendría sentido: no habría CLV que medir, y estaríamos
usando el precio que ya incorporó toda la información.

---

## 2. La convención temporal, que es donde se cuela el leakage

El archivo no trae horas, solo fechas. Así que se inventa un orden, y se inventa
**hacia el lado seguro**:

| | |
|---|---|
| resultado de un partido del día D | conocido a las `D 23:59Z` |
| decisión de apuesta del día D | tomada a las `D 00:00Z` |

Consecuencia: ningún partido del día D alimenta ninguna predicción del día D, ni
siquiera el de la tarde sobre el de la noche. **Eso tira información real** —en
MLB hay muchas tardes con resultados ya cerrados— y se tira a propósito: errar
hacia "lo supimos después" como mucho desaprovecha un dato; errar hacia "lo
supimos antes" produce leakage, y el leakage no da error, da buenos resultados.

El bucle de `replay.py` hace cumplir la convención por su forma:

```
para cada día D, en orden:
    1. predecir TODOS los partidos de D      <- el modelo aún no vio D
    2. solo entonces, incorporar los resultados de D
```

Y luego `sanity.py` lo verifica por su cuenta, con los pares
`(as_of, observed_at)` de cada candidate. **El backtest no se cree a sí mismo.**

---

## 3. El sanity gate no es un aviso

`BacktestResult.model` y `.betting` **lanzan `SanityBlocked`** si el informe no
pasó. No hay forma de leer las métricas sin haber pasado los checks, porque la
única forma de leerlas es por esas propiedades.

Funcionó de verdad, no en teoría. En la evaluación del holdout, dos estrategias
produjeron esto:

```
elo_blend v1-w0.10   ROI +511,6% sobre 2 apuestas     -> BLOQUEADO
elo_blend v1-w0.20   ROI  +56,0% sobre 59 apuestas    -> BLOQUEADO
```

Un +511% de ROI es exactamente la clase de número que acaba citado sin su
denominador tres semanas después. El sistema se negó a imprimirlo en la tabla —
pero **sí imprime la fila, marcada como bloqueada**, porque omitirla en silencio
convertiría la tabla en un ranking de las estrategias que sobrevivieron.

### El test del oráculo

El test más valioso de la fase construye una estrategia que **mira el resultado
del partido que predice**, y comprueba dos cosas: que produce el backtest
espectacular que uno esperaría (>90% de acierto), y que el sistema lo bloquea en
vez de celebrarlo. Un backtest que no distingue un buen modelo de uno con
leakage no sirve para nada.

Detalle que quedó escrito en un test porque conviene saberlo: al oráculo lo caza
`win_rate_vs_price`, **no** `edge_distribution`. Ese check mide la media *con
signo* del edge, y el oráculo apuesta a los dos lados con edges enormes y
opuestos que se cancelan. Si algún día se toca `win_rate_vs_price`, este backtest
deja de estar protegido por donde uno creería.

---

## 4. Las dos evaluaciones, que nunca se mezclan

| | muestra | qué mide |
|---|---|---|
| **modelo** | todos los candidates (~20.000) | calibración contra el cierre |
| **filtro** | solo recomendaciones (0-8.635) | ROI, drawdown, CLV |

Difieren en dos órdenes de magnitud. Mezclarlas produce el error clásico: un ROI
espectacular sobre cuarenta apuestas presentado con la autoridad estadística de
las cuarenta mil predicciones que sí había.

### Las dos métricas del modelo, y la que parece señal sin serlo

- **`beat_market_rate`** — ¿está el modelo *más cerca del cierre* que el mercado
  de apertura? Es la métrica de `ARCHITECTURE.md` §4.6. La baseline de mercado da
  **0 por construcción**: no puede estar más cerca del cierre que ella misma.
- **`model_clv`** — cuánto se movió el cierre respecto al modelo. Es un
  diagnóstico de sesgo con signo, **no acredita señal**: para cualquier modelo
  calibrado sale ~50% por pura simetría.

Confundirlas fue un error real durante el desarrollo de esta fase. La primera
versión medía "el cierre se movió hacia mi lado" y la presentaba como "el modelo
bate al cierre". Daba 51,4% para Elo con z = +3,8 — un resultado significativo
que no medía nada. Con la métrica correcta, Elo da **27,6%**.

---

## 5. Los resultados

### Train, 2011-2018 (19.697 partidos)

| estrategia | n | Brier | vs mercado | cerca del cierre | z | apuestas | ROI |
|---|---|---|---|---|---|---|---|
| market_consensus v1 | 19.697 | 0,24234 | +0,00000 | 0,0000 | — | 0 | — |
| elo v1 | 19.390 | 0,24500 | **−0,00280** | 0,2759 | −62,4 | 8.635 | **−4,66%** |
| elo_blend w=0,05 | 19.697 | 0,24236 | −0,00002 | 0,4864 | −3,8 | 0 | — |
| elo_blend w=0,10 | 19.697 | 0,24239 | −0,00005 | 0,4742 | −7,2 | 0 | — |
| elo_blend w=0,20 | 19.697 | 0,24248 | −0,00014 | 0,4480 | −14,6 | 66 | −1,14% |

### Holdout, 2019-2021 (5.854 partidos)

| estrategia | n | Brier | vs mercado | cerca del cierre | z | apuestas | ROI |
|---|---|---|---|---|---|---|---|
| market_consensus v1 | 5.854 | 0,23853 | +0,00000 | 0,0000 | — | 0 | — |
| elo v1 | 5.542 | 0,24101 | −0,00316 | 0,2331 | −39,7 | 3.040 | −1,56% |
| elo_blend w=0,05 | 5.854 | 0,23851 | +0,00002 | 0,4243 | −11,6 | 0 | — |
| elo_blend w=0,10 | — | — | — | — | — | — | BLOQUEADO |
| elo_blend w=0,20 | — | — | — | — | — | — | BLOQUEADO |

### Cómo se lee

**Elo no aporta nada.** No es que aporte poco: aporta negativo, y de forma
monótona en el peso de mezcla. Cada dosis de Elo empeora tanto el Brier como la
cercanía al cierre. Dos métricas independientes, misma dirección, tendencia
monótona, y el mismo resultado en train y en holdout. Es un negativo robusto, no
ruido.

**Ojo con el `+0,00002` de `w=0,05` en holdout.** Nominalmente mejora el Brier del
mercado. Es exactamente por esto que el criterio de despliegue exige **las dos
condiciones a la vez** —mejor Brier *y* más cerca del cierre—: con
`beat_market_rate` en 0,4243 (z = −11,6), esa mejora de la quinta cifra decimal
sobre n=5.854 es ruido puro. Una condición sola habría "promovido" un modelo que
no vale nada.

**El −4,66% de ROI de Elo tiene sentido.** Con un vig del 2,5% y un modelo sin
información real, filtrar por edge selecciona precisamente los partidos donde el
modelo más se equivoca. Perder algo más que el vig es lo esperable.

---

## 6. La regla anti-overfit, y cómo se hace cumplir

El holdout se toca **una vez**. Cada iteración sobre él lo convierte en train:
las decisiones que se toman mirándolo se ajustan a él igual que lo haría un
`fit`.

Esa regla no se puede imponer con código —nada impide llamar otra vez a la
función—. Lo que sí se puede es **quitarle la deniabilidad**:
`data/backtests/holdout_ledger.json` cuenta los usos, persiste en disco, y el
contador sale impreso en cada informe con un aviso a partir del segundo.

> El ledger de este repositorio marca **2 usos** de `mlb_2019_2021`. Ambos
> produjeron la misma tabla; el segundo fue para verificar un cambio en el
> renderizado, no para elegir modelo. Se deja anotado en vez de reiniciarlo,
> porque un contador que se puede poner a cero no cuenta nada.

Por lo mismo, `--test` es un flag explícito y no el comportamiento por defecto:
tocar el holdout tiene que ser algo que alguien escribe, no algo que pasa por
ejecutar el comando de siempre.

---

## 7. Lo que este backtest NO mide

Hay que ser explícito, porque el informe da números y los números invitan a
extrapolar.

**No mide el edge estructural.** El archivo es de consenso, sin identificar la
casa. Comparar precios entre casas —que es la otra fuente de edge del sistema, y
la que la captura en vivo persigue— no se puede backtestear con esto. El
`structural_edge` que calcula el replay es siempre negativo: con un solo precio,
lo único que hay que superar es el vig.

**No mide el filtro real.** De los siete gates de producción, **cuatro no se
pueden evaluar** con este histórico: `line_freshness`, `reference_books`,
`data_quality` y `model_agreement` necesitan varias casas y marcas de tiempo por
precio. Se asumen superados y el informe lo dice en cada ejecución. La evaluación
del filtro es por tanto **parcial, y mide un filtro más permisivo que el que
corre en vivo**.

**No mide el techo del modelado.** No hay datos de lanzador abridor, que es el
factor dominante en MLB. Este backtest dice lo que valen los modelos que tenemos
—nada, sobre el mercado—, no lo que se podría conseguir con las features que
faltan.

---

## 8. Uso

```bash
sportstar backtest           # compara sobre train. Iterar aquí es gratis.
sportstar backtest --test    # además evalúa el holdout. Queda anotado.
```

```python
from sportstar.odds_history import load
from sportstar.backtesting.dataset import to_historical_games
from sportstar.backtesting.engine import run_backtest
from sportstar.backtesting.splits import temporal_split
from sportstar.backtesting.strategies import MarketConsensus

split = temporal_split(to_historical_games(load("mlb").games))
result = run_backtest(split.train, MarketConsensus())
print(result.summary())      # lanza SanityBlocked si no pasó los checks
```
