# CHANGELOG

Decisiones y cambios con consecuencias. No se documenta lo trivial.

---

## Phase 2b — El abridor no aporta, y ahora se sabe por qué

**Estado:** completada. 870 tests, `ruff` y `mypy --strict` limpios. Detalle en
[`PITCHERS.md`](PITCHERS.md).

### El resultado

La calidad del lanzador abridor —el factor dominante de MLB— **predice de sobra
por su cuenta** y **no añade nada** sobre el precio de apertura.

| ajuste (train 2011-2018, n=18.147) | market_logit | elo_diff | starter_advantage |
|---|---|---|---|
| con mercado | +1,0141 | −0,0000 | **+0,0166** |
| sin mercado | — | +0,0049 | **+0,2545** |

Factor 15. Y con el mercado dentro, `market_logit` sale en +1,0141: el modelo
literalmente reproduce el mercado. La mejora de Brier es **+0,00001 en train**
—en muestra, con los coeficientes ajustados sobre esos mismos partidos— y
+0,00003 en holdout.

### Por qué este experimento vale más que un resultado negativo

Un coeficiente ~0 **con** el mercado dentro admite dos lecturas opuestas: que la
feature no vale nada, o que el mercado ya la contiene. Son conclusiones
contrarias —una dice tirar la feature, la otra dice que la feature es buena y hay
que buscar dónde el mercado tarda en incorporarla—. `fit(rows, use=...)` las
separa ajustando sin el mercado, y la respuesta es inequívoca: **la feature es
buena, el mercado se le adelantó**.

Que la feature es buena no se deduce solo del coeficiente. Se validó contra la
historia real: los diez mejores de 2011-2016 con ≥100 aperturas salen Kershaw
(primero y por un abismo), Strasburg, Sale, Cliff Lee, Kluber, Bumgarner,
Wainwright, Price, Félix Hernández y Scherzer. Un FIP con el signo invertido
pasaría todos los tests sintéticos y daría un ranking absurdo; este test lo caza.

### El +2,04% de ROI en holdout, y lo que destapó investigarlo

El único número positivo de la tabla, sobre 2.206 apuestas. Cinco razones
independientes para descartarlo: el signo se da la vuelta respecto a train
(−4,10% sobre 6.175), no es significativo (t = −0,93), bate al cierre solo el
40,9% con CLV medio −0,58%, su Brier es peor que el del mercado, y el retorno
medio por apuesta es de signo contrario al ROI.

Esa última discrepancia era un hueco del propio informe. **ROI y retorno medio
son cantidades distintas**: el ROI pondera por stake y el retorno medio no, así
que con Kelly pueden discrepar hasta en el signo — y aquí lo hacen (+2,04% frente
a −2,33%). El agregado positivo lo decidían unas pocas apuestas grandes mientras
la apuesta típica perdía. No es un error de cálculo, pero sí es la diferencia
entre "esta estrategia gana" y "esta estrategia acertó donde apostó fuerte".
`BettingPerformance` reporta ahora las dos cifras y avisa cuando el signo
discrepa. Reportar solo el ROI habría contado la historia al revés.

### Decisiones de datos

**Game log, no total de temporada.** El total es veinte veces más barato y
completamente inservible: incluye los partidos que se están prediciendo. Son
~3.400 peticiones contra la API oficial, gratuita y sin key.

**FIP en vez de ERA**, porque solo usa lo que el lanzador controla y por eso se
estabiliza antes. Encogido hacia la media de liga en proporción a la muestra, con
esa media también point-in-time.

**El cruce va por (fecha, equipos, marcador)** y alcanza el 99,7%. El marcador no
es decorativo: 341 partidos son dobles jornadas y sin él los dos partidos del día
entre los mismos equipos son indistinguibles. Por debajo del 90% de cruce el
experimento aborta — comparar modelos sobre submuestras distintas no compara
nada.

**Una ausencia nunca se rellena con un cero.** Un 0 en `starter_advantage`
significa "los dos abridores son igual de buenos", que no es lo mismo que "no sé
quién lanza".

**La trampa de las entradas queda cerrada con tests.** `"6.1"` no es 6,1
entradas: son 6 entradas y 1 out, 19 outs. La notación parece decimal y no lo es.

**El histórico de lanzadores sí se commitea** (1,1 MB), al revés que el de odds:
viene de la API oficial de MLB, sin restricción de licencia, y reconstruirlo es
media hora. Commitearlo hace que el experimento se reproduzca en segundos.

### Lo que esto cambia en el proyecto

**El edge de modelo en el moneyline de MLB está a efectos prácticos agotado.** No
por falta de datos: con el factor dominante medido correctamente, la mejora es de
la quinta cifra decimal. Añadir bullpen, parque o alineación son mejoras
marginales sobre un dominante que no mueve la aguja; hacerlo esperando otro
resultado sería insistir, no experimentar.

Lo que queda abierto es el **edge estructural** —comparar precios entre casas—,
que ningún histórico de consenso puede medir y que solo valida la captura en
vivo; y **mercados menos eficientes** que el moneyline de MLB, que es de los más
líquidos y mejor precificados que existen. Esta fase lo ha medido en vez de
suponerlo.

---

## Phase 3 — Backtesting engine: el mercado gana

**Estado:** completada. 784 tests, `ruff` y `mypy --strict` limpios. Detalle en
[`BACKTESTING.md`](BACKTESTING.md) y [`MODELS.md`](MODELS.md).

### El resultado

Sobre 25.560 partidos de MLB (2011-2021), **ningún modelo bate al mercado**.

| estrategia | Brier vs mercado (train) | cerca del cierre | ROI |
|---|---|---|---|
| market_consensus v1 | +0,00000 | 0,0000 (por construcción) | — |
| elo v1 | **−0,00280** | 0,2759 | **−4,66%** |
| elo_blend w=0,05 | −0,00002 | 0,4864 | — |
| elo_blend w=0,10 | −0,00005 | 0,4742 | — |
| elo_blend w=0,20 | −0,00014 | 0,4480 | −1,14% |

Elo no aporta poco: aporta negativo, y de forma **monótona en el peso de
mezcla**. Dos métricas independientes, misma dirección, mismo resultado en
holdout. Es un negativo robusto, no ruido. La conclusión operativa está en
`MODELS.md`: no se despliega nada, y lo que falta es lanzador abridor.

### El criterio de despliegue exige dos condiciones, y ya sirvió

Mejor Brier **y** más cerca del cierre. En holdout, `elo_blend w=0,05` mejoró el
Brier del mercado en +0,00002 con una tasa de cercanía al cierre de 0,42
(z = −11,6). Con una sola condición se habría promovido un modelo que no vale
nada. Con las dos, no.

### El sanity gate bloquea de verdad

`BacktestResult.model` y `.betting` **lanzan `SanityBlocked`** si el informe no
pasó: no hay forma de leer las métricas sin haber pasado los checks, porque la
única forma de leerlas es por esas propiedades.

Funcionó sobre datos reales. En el holdout, dos estrategias produjeron un ROI de
**+511,6% sobre 2 apuestas** y **+56,0% sobre 59**. El sistema se negó a
imprimirlos — pero sí imprime la fila marcada como bloqueada, porque omitirla en
silencio convertiría la tabla en un ranking de las que sobrevivieron.

El test más valioso de la fase construye un **oráculo** que mira el resultado del
partido que predice, y comprueba que produce >90% de acierto **y** que el sistema
lo bloquea. Un backtest que no distingue un buen modelo de uno con leakage no
sirve para nada.

### Un error propio que quedó documentado

La primera versión medía "el cierre se movió hacia mi lado" y lo presentaba como
"el modelo bate al cierre". Daba 51,4% para Elo con z = +3,8: significativo, y
sin contenido — para cualquier modelo calibrado esa cifra sale ~50% por pura
simetría. Con la métrica correcta de `ARCHITECTURE.md` §4.6 —¿está el modelo más
cerca del cierre que el mercado de apertura?— Elo da **27,6%**.

Queda escrito porque el número equivocado era *plausible*, y esa es la clase de
error que sobrevive a una revisión rápida.

### La convención temporal, hacia el lado seguro

El archivo no trae horas. Se fija que el resultado del día D se conoce a las
`D 23:59Z` y la decisión se toma a las `D 00:00Z`, así que **ningún partido del
día D alimenta una predicción del día D**. Eso tira información real —en MLB hay
muchas tardes con resultados ya cerrados— y se tira a propósito: errar hacia "lo
supimos después" desaprovecha un dato; errar hacia "lo supimos antes" produce
leakage, y el leakage no da error, da buenos resultados.

`replay.py` lo hace cumplir por la forma del bucle, y `sanity.py` lo verifica
después por su cuenta con los pares `(as_of, observed_at)`. El backtest no se
cree a sí mismo.

### Dobles jornadas

341 pares de partidos del archivo son dobles jornadas reales —verificadas contra
la MLB Stats API—. Sin distinguirlas colapsan en un solo evento y el check de
duplicados bloquea el backtest entero, con razón. Se distinguen con
`archive_sequence`, que **no es** el número oficial de partido de MLB: el archivo
no lo trae y su orden no coincide con el de la liga.

### El ledger del holdout

`data/backtests/holdout_ledger.json` cuenta los usos del conjunto de holdout,
persiste, y el contador sale impreso con un aviso a partir del segundo. La regla
"se toca una vez" no se puede imponer con código; lo que sí se puede es quitarle
la deniabilidad. El ledger marca hoy 2 usos, ambos con la misma conclusión y sin
elegir modelo con ninguno — se deja anotado en vez de reiniciarlo, porque un
contador que se puede poner a cero no cuenta nada.

### Lo que esta fase NO cubre

- **El edge estructural.** El archivo es de consenso, sin identificar la casa, así
  que comparar precios entre casas no se puede backtestear. Solo lo valida la
  captura en vivo.
- **El filtro real.** Cuatro de los siete gates de producción no se pueden
  evaluar con este histórico (`line_freshness`, `reference_books`,
  `data_quality`, `model_agreement`). Se asumen superados, el informe lo dice en
  cada ejecución, y la evaluación del filtro es por tanto parcial.

---

## Histórico de odds — D1 resuelto sin comprar nada

**Estado:** 25.586 partidos de MLB (2011-2021) con moneyline de apertura y cierre,
disponibles con `sportstar odds-history`. 705 tests, `ruff` y `mypy --strict`
limpios. Detalle completo en [`ODDS_HISTORY.md`](ODDS_HISTORY.md).

### La decisión

El endpoint histórico de The Odds API está detrás de plan de pago. En vez de
comprarlo, se usa el archivo público que publica
`flancast90/sportsbookreview-scraper` (MIT), anclado a un commit para que el
dataset de un backtest no pueda cambiar bajo los pies.

### El volcado venía roto, y eso es lo importante

Cada fila del archivo publicado **mezcla dos partidos distintos**: el scraper de
origen se salta una fila de más antes de emparejar, así que junta el local del
partido k con el visitante del k+1. Los síntomas son inconfundibles —2.653
"empates" imposibles, sobre-redondeos del −18%, el local ganando el 48,2%— y el
bug es determinista, así que se deshace exactamente. Tras repararlo: 18 empates,
sobre-redondeo entre 1,5% y 4,8%, local al **53,5%** (el valor real de MLB) y la
línea de cierre calibrando con un ECE de 0,0036.

Contrastado además contra la MLB Stats API: **61 de 63 partidos casan exactos**
en (local, visitante, marcador). Los dos que no son un suspendido sin marcador y
una segunda parte de doble jornada.

**La lección para el proyecto no es el bug, es cómo apareció.** No apareció
leyendo el código del upstream: apareció porque los primeros números que salieron
del fichero eran imposibles —el local ganando menos de la mitad de sus partidos,
mercados con dos favoritos— y se miraron con desconfianza en vez de seguir
adelante. Un dataset de un tercero que se acepta sin auditar no falla: **da un
número**, y ese número parece un resultado.

### Por eso la corrección se detecta, no se aplica

Si el upstream arregla su scraper, aplicar la corrección de oficio *crearía* la
corrupción que hoy repara. `detect_pairing` construye las dos hipótesis, mide en
cada una cuántos datos salen físicamente imposibles, y aborta si ninguna es
coherente o si las dos lo son por igual. Sobre el fichero real gana por un factor
682, no por un pelo.

### Auditoría bloqueante del histórico

`validation/market_history.py` audita cualquier histórico de mercado antes de
dejarlo entrar: sobre-redondeo plausible, tasa de victoria local, calibración del
cierre, y que el cierre gane a la apertura. El último es el más valioso porque es
*interno* —no necesita fuente externa contra la que contrastar—. Un `FATAL` no es
una advertencia en el log: `load()` lanza `HistoryRejected` y el histórico no se
usa.

### Qué desbloquea y qué no

Phase 3 deja de depender de acumular cierres propios durante una temporada. Pero
el archivo es de **consenso, sin identificar la casa**, así que el **edge
estructural sigue sin poder backtestearse**: comparar casas requiere precios por
casa, y esos solo salen de la captura propia. Son dos fuentes de edge y solo una
queda desbloqueada.

---

## Phase 1 — Core data model + núcleo matemático

**Estado:** completada. 204 tests, 100% de cobertura en `core/` y `validation/`,
`ruff` y `mypy --strict` limpios.

### Decisiones tomadas

**D3 — Motor de base de datos: SQLite + WAL tras SQLAlchemy + Alembic.**
A la escala del proyecto SQLite sobra durante la primera temporada completa, y
funciona sin instalar nada. El esquema se mantiene compatible con Postgres, así
que la migración se reduce a cambiar `SPORTSTAR_DATABASE_URL`. Decisión
reversible casi gratis, que es exactamente por qué se toma ahora en vez de
debatirla.

`create_db_engine` activa `foreign_keys=ON` explícitamente: SQLite trae las FKs
**desactivadas** por defecto, así que sin ese PRAGMA todas las claves ajenas del
esquema serían decorativas y las inconsistencias referenciales aparecerían meses
después, ya con datos.

### Las tres decisiones irreversibles, ahora en código

1. **Odds append-only.** `OddsSnapshot` tiene un guard de ORM que lanza
   `AppendOnlyViolation` ante cualquier `UPDATE` o `DELETE`. No es documentación
   aspiracional: el error salta en desarrollo, que es donde se comete el fallo.
2. **Features point-in-time.** `event_features.as_of` es `NOT NULL` y parte de la
   clave única. El mismo evento tiene features distintas a las 10:00 y a las
   18:00, y ambas son correctas para su momento.
3. **Taxonomía de mercados genérica.** Un test verifica que insertar un player
   prop no requiere ningún cambio de esquema.

### Bug de diseño encontrado y corregido

**`UNIQUE` con columnas nulables no restringe nada.** En SQL `NULL != NULL`, así
que la constraint de `selections` — que incluye `line` y `subject_id`, ambas
nulables en el diseño original — dejaba de aplicar precisamente a las filas donde
esas columnas eran `NULL`, es decir **a todo el moneyline**. Dos selecciones
idénticas entraban sin error.

El síntoma no habría aparecido hasta mucho después, como partidos contados dos
veces en un backtest: exactamente el tipo de fallo silencioso que infla
resultados y parece una ventaja.

Corregido haciendo ambas columnas `NOT NULL`, con `NO_LINE = 0.0` como centinela
explícito para mercados sin línea. Un spread de 0.0 (pick'em) sigue siendo
legítimo y no colisiona, porque `market_id` ya distingue el tipo de mercado
dentro de la constraint.

Se añadió además `test_no_unique_constraint_contains_a_nullable_column`, que
recorre **todas** las tablas del metadata. La clase de bug queda cerrada para
todo el repositorio, no solo para la tabla donde apareció.

### Corrección de documentación: dirección del sesgo del vig proporcional

Los documentos de Phase 0 decían que el método proporcional "sesga en longshots
y siempre en la dirección que te hace ver edge donde no lo hay", sin precisar el
lado. Al implementar Shin y medirlo, el sesgo resultó ir en el sentido contrario
al que sugiere la intuición: el proporcional **sobreestima** la fair probability
del underdog y por tanto **subestima** la del favorito, de modo que el edge
fantasma aparece en los **favoritos**.

Sobre un mercado 0.70/0.35 con 5% de overround: proporcional da 0.6667 al
favorito, Shin 0.6750. Son 0.8 puntos, del mismo orden que los edges que
buscamos. Corregido en `ARCHITECTURE.md` §4.1 y en el riesgo R2 del audit, y
fijado en `tests/core/test_novig.py::TestShin` para que no vuelva a derivar.

### Verificaciones que valen la pena mencionar

- El ejemplo **BEST BET** del brief reproduce número a número: -115 da un
  break-even de 53.5% y un EV de +9.7% con probabilidad de modelo 58.7%.
  `tests/core/test_edge.py::TestBriefExample`.
- `-100` y `+100` son el mismo precio; la forma canónica es `+100`, así que el
  round trip americano no es la identidad. Documentado en test para que un feed
  que envíe `-100` no dispare una falsa alarma.
- `structural_edge` no acepta `model_prob` en su firma. Es la garantía, a nivel
  de tipo, de que el suelo del sistema no depende de la calidad del modelado.

### Qué NO se hizo

Ningún proveedor de datos, ningún modelo, ningún job. Phase 1 es cimiento: sin
datos externos no hay nada que pueda fallar en silencio todavía.

---

## Phase 2a — Pipeline con el mercado como modelo (lógica interna)

**Estado:** lógica completada, providers HTTP pendientes. 337 tests, 96% de
cobertura del paquete, `ruff` y `mypy --strict` limpios.

`python -m sportstar.cli demo` ejecuta el ciclo completo con precios sintéticos.

### Lo que demuestra

El pipeline produce recomendaciones **sin ningún modelo estadístico**. En la
demo, `market_consensus_v1` copia al mercado —edge de modelo exactamente 0— y aun
así recomienda 1.90 units sobre los Yankees a +115, con ROI esperado del +8.7%.
Toda la ventaja viene del edge estructural: DraftKings paga 2.15 donde el
consenso de Pinnacle y Circa dice que lo justo son ~1.98.

Es el suelo del sistema, y establece la vara contra la que se juzgará cualquier
modelo posterior.

### Bug de diseño encontrado y corregido: el gate mataba al baseline

Al ejecutar el pipeline por primera vez, **cero recomendaciones**. La causa no
era un fallo de cálculo sino de definición: los gates filtraban por el *edge de
modelo* (`model_prob − fair_prob`), que para `market_consensus_v1` es 0 por
construcción. El baseline de mercado no podía recomendar nada nunca, y con él se
iba toda la medición del edge estructural — es decir, el objetivo entero de
Phase 2a.

La corrección obliga a nombrar bien las tres magnitudes:

```
edge            = model_prob − market_fair_prob      ¿sé algo que el mercado no?
structural_edge = market_fair_prob − implied(best)   ¿es el precio mejor que el justo?
total_edge      = model_prob − implied(best)         = edge + structural_edge
```

`total_edge` es la que determina el EV: `total_edge > 0` si y solo si `EV > 0`.
Es la que filtran los gates y la que alimenta el confidence. Las otras dos se
conservan por separado para poder **atribuir** de dónde vino la ventaja, que es
justamente lo que el brief pedía no confundir nunca.

Efecto lateral: el confidence también estaba roto por lo mismo (con edge 0, el
componente `model_agreement` se iba a 0 y el score caía a 3.7 en una apuesta
buena). Ahora da 8.6.

### Hallazgo: un fallback de emparejamiento que no podía dispararse

El resolver tenía como último recurso un solapamiento de tokens (Jaccard) con
umbral 0.80. Los nombres de equipo MLB tienen 2-3 tokens, así que el máximo
alcanzable sin que la coincidencia exacta ya lo hubiera resuelto es 0.75: la red
de seguridad era código muerto para el deporte que estamos construyendo.

Sustituida por **emparejamiento por subconjunto**: los tokens del catálogo caben
enteros dentro del nombre entrante. Resuelve el caso real ("New York Yankees
Baseball Club") manteniendo las dos salvaguardas que importan:

- Es subconjunto, no solapamiento: "New York" a secas no resuelve a ninguno de
  los dos equipos de la ciudad, porque le faltan tokens.
- Si varios equipos encajan, gana el de más tokens **y solo si es estrictamente
  el más largo**. Un empate se deja sin resolver: ante ambigüedad, la cola de
  revisión es preferible a acertar por suerte.

El Jaccard se conserva para ligas con nombres largos, donde sí aporta.

### Decisiones de diseño que conviene recordar

**El orden de las operaciones al calcular el consenso.** Hay que quitar el vig a
cada book **por separado** y luego promediar. Promediar las implied con vig y
quitar el vig al final da un resultado distinto y sesgado, porque cada book carga
un margen distinto y el promedio lo mezcla con la señal. Verificado
numéricamente en `test_devig_per_book_then_average_differs_from_the_naive_order`.

**La incertidumbre del baseline sale gratis.** `market_consensus_v1` deriva su
intervalo de la discrepancia entre books de referencia: cuando Pinnacle y Circa
difieren, el mercado está menos seguro, y esa duda se propaga hasta el confidence
en vez de perderse en el promedio.

**Componentes ausentes del confidence cuentan como neutro (0.5), no se excluyen.**
Excluirlos y renormalizar haría que una apuesta sobre la que sabemos *menos*
puntuase *más alto*. Es el fallo silencioso de casi todos los scores compuestos.

**Un mercado incompleto no se estima.** Si un book no publica todos los lados, se
excluye del consenso en vez de rellenar el hueco: sin el mercado completo no se
puede saber cuánto margen lleva el precio, y una constante inventada llega intacta
hasta el edge.

### Restricción del entorno

La política de red de esta sesión deniega la salida a `statsapi.mlb.com` y
`api.the-odds-api.com` (403 en CONNECT del proxy). Los adaptadores HTTP se dejan
para escribirlos contra respuestas reales: un normalizador escrito contra un
esquema recordado se rehace entero al ver el primer payload.

### Qué NO se hizo

Providers HTTP, job de captura de cierres, API y Data Health panel. Todo lo que
falta de 2a depende de tener datos reales entrando.

---

## Phase 2a — Capa de proveedores

**Estado:** escrita y testeada contra fixtures. **Pendiente de una ejecución real
que verifique los esquemas.** 401 tests, 95% de cobertura del paquete.

### Restricción que condiciona esta entrega

La política de red del entorno de desarrollo deniega la salida a
`statsapi.mlb.com` y `api.the-odds-api.com` (403 en el CONNECT del proxy). Los
normalizadores están escritos contra la documentación pública de cada API, no
contra respuestas verificadas.

Eso no es una excusa, es una restricción de diseño: **si no puedo verificar el
esquema, invierto en que el fallo sea barato de diagnosticar.**

### Diseño derivado: fallar con diagnóstico, no en silencio

```
payload[3]: falta la clave 'commence_time'. Llegó dict con claves
['id', 'sport_key', 'home_team', 'away_team']. Suele significar que el
proveedor cambió el formato.
```

Frente a la alternativa —`matched: 0` sin explicación— la diferencia es entre
diez minutos y una tarde. Reglas concretas:

- **Cambio de forma en el nivel superior ⇒ aborta.** Si la raíz no es lo que
  esperamos, nada de lo que sigue es fiable.
- **Elemento roto ⇒ se cuenta y se sigue.** Un evento con formato raro no puede
  tirar el slate entero.
- **Cada error lleva su ruta** (`payload[3].bookmakers[1].markets[0]`) y enumera
  las claves que sí llegaron.

### Decisiones que conviene recordar

**Los providers solo traen bytes.** No interpretan, no emparejan, no tocan la
base. Permite guardar el payload íntegro en `raw_payloads` y reprocesar todo el
histórico cuando un normalizador tenga un bug, sin volver a pagar la API.

**Los normalizadores no emparejan.** Devuelven `home_team_raw` con el texto tal
cual vino; el emparejamiento lo hace `resolution/`, que sabe encolar lo que no
resuelve en vez de descartarlo.

**No se reintenta lo que no se arregla reintentando.** Un 401 o un 404 se
propagan de inmediato; solo 429 y 5xx llevan backoff. En The Odds API la cuota es
dinero: el plan gratuito son 500 peticiones al mes y cada sync de odds cuesta
`1 x mercados x regiones`. Un sync cada 10 minutos la agota en días — la
frecuencia hay que decidirla con la cuota delante.

**`gameNumber` de los dobletes.** Los dos partidos de un doblete comparten fecha
y equipos. Sin ese campo colapsan en un evento y se pierde un partido entero. Es
la causa clásica de eventos duplicados y ya está cubierta por test.

**Los books desconocidos se registran, no se descartan.** Un book nuevo en el
feed puede ser un sharp que deberíamos estar usando como referencia; enterarse
tres meses después es tarde.

**La captura nunca guarda la URL.** La API key viaja en la query string de The
Odds API y acabaría commiteada. Solo se persiste el cuerpo de la respuesta.

### Cómo verificar los esquemas

```bash
export SPORTSTAR_ODDS_API_KEY=...
python -m sportstar.cli capture
pytest tests/data -q
```

`capture` sobrescribe los fixtures con respuestas reales y los tests pasan a
validarse contra ellas. Requiere salida de red a `statsapi.mlb.com` y
`api.the-odds-api.com`.

---

## Phase 2a — Persistencia del pipeline

**Estado:** completada. 421 tests, 95% de cobertura del paquete.

Cierra el ciclo `odds_snapshots -> PricePoint -> pipeline -> candidates`, con
linaje suficiente para reconstruir cualquier apuesta histórica.

### Dos fallos de diseño que solo aparecen al intentar escribir

**1. Una FK única no puede representar un consenso.**
`Candidate.reference_odds_snapshot_id` era una clave ajena a un solo snapshot,
pero la probabilidad justa sale del promedio de N books de referencia. Con una
sola referencia el consenso es irreconstruible — y reconstruir cualquier apuesta
histórica es requisito del sistema, no una comodidad.

Sustituida por `reference_odds_snapshot_ids` (lista JSON) más
`reference_book_count` y `reference_dispersion`. Ahora un candidate guarda los
cuatro snapshots (2 books x 2 lados) que produjeron su fair probability, y un
test verifica que se pueden recuperar y que todos pertenecen a books sharp.

**2. SQLite devuelve timestamps sin zona horaria.**
`DateTime(timezone=True)` no almacena la zona en SQLite, así que al leer vuelven
*naive*. Compararlos con el `as_of` del pipeline lanza `TypeError`, y ese
contraste está en el centro de todo el sistema point-in-time.

Lo grave no es el error, es que **en Postgres habría funcionado**: el
comportamiento dependía del motor y el fallo solo habría aparecido al migrar,
con datos ya dentro.

Corregido con un `TypeDecorator` propio, `UtcDateTime`, aplicado a todas las
columnas de fecha. Al leer reetiqueta a UTC; al escribir **rechaza** los naive en
vez de asumir que ya son UTC — asumirlo produce desfases de horas que nadie
detecta hasta que un evento aparece capturado después de su propio inicio.

Efecto colateral en las migraciones: autogenerate renderizaba
`sportstar.db.base.UtcDateTime()` sin importarlo. Resuelto con un `render_item`
en `migrations/env.py` que lo emite como `sa.DateTime(timezone=True)`. Además de
arreglar el import, evita acoplar el histórico de migraciones a una clase de la
aplicación que puede moverse o renombrarse y romper migraciones ya aplicadas.

### Decisiones que conviene recordar

**Se persiste todo candidate, pase o no los filtros.** Es lo que permite
responder después "¿qué habría pasado con umbral 2%?" sin volver a simular, y lo
que separa la evaluación del modelo (todos los candidates) de la del filtro (solo
recomendaciones).

**Persistir un precio sintético es un error explícito.** Si el mejor precio no
trae `snapshot_id`, `persist_evaluation` lanza `PersistenceError` en vez de
escribir `NULL`. Un candidate cuyo precio no se puede señalar en la tabla de odds
no es reconstruible, y el `NULL` se descubriría meses después al auditar.

**Una versión de modelo es inmutable.** `ensure_model_version` crea o devuelve,
pero nunca actualiza: si cambia algo del modelo cambia la versión, o las
predicciones antiguas quedarían atribuidas a algo que ya no es lo que las generó.

**Las razones salen de la descomposición del edge, no de texto.** Para
`market_consensus_v1` hay exactamente dos factores posibles porque son las dos
únicas cosas que el modelo sabe. En la práctica solo se lista una: el edge de
modelo es 0 y no supera el mínimo. "El mercado se equivoca" y "este book paga de
más" son afirmaciones distintas con implicaciones distintas, y mezclarlas borra
justo lo que hace falta para saber si el modelo aporta.

**Una sola función de carga para producción y backtest.** `load_price_points`
cambia solo en el `as_of`. Mantener dos caminos garantizaría que se
desincronizasen y que el backtest dejara de describir lo que el sistema hace.

**`correlation_group` agrupa por evento.** Aproximación burda pero conservadora
en la dirección correcta: agrupar de más limita exposición, agrupar de menos la
multiplica sin que nadie se entere. El portfolio engine de Phase 9 la refinará.

---

## Phase 2a — Data Health

**Estado:** completada. 455 tests, 95% de cobertura del paquete.

`python -m sportstar.cli health` ejecuta los checks, sincroniza el panel y
devuelve código de salida 1 si hay algún CRITICAL — para que un problema de datos
pueda romper un cron o un paso de CI, no solo pintarse en una pantalla que nadie
mira.

### Bug previo encontrado al construirlo: el esquema prohibía los dobletes

`UNIQUE(league_id, event_date, home_team_id, away_team_id)` es la definición
literal de un doubleheader: dos partidos, mismo día, mismos equipos, misma liga.
El segundo no podía insertarse, así que el sync habría fallado en **cada doblete
de la temporada**.

Peor que el fallo es su síntoma: un partido que simplemente falta. No hay
excepción que investigar, solo un slate más corto de lo que debería — de las
cosas que tardan semanas en notarse y que mientras tanto sesgan cualquier métrica
agregada.

El normalizador ya extraía `gameNumber` y tenía test, pero `Event` no tenía dónde
guardarlo. Corregido añadiendo `game_number` a la tabla y a la constraint. Un
test verifica que caben los dos partidos del doblete y que un duplicado real
sigue rechazándose.

### Por qué existe este módulo

El modo de fallo peligroso de este sistema no es el error, es **el silencio**. Un
pipeline que sigue corriendo con datos de ayer no lanza excepciones: produce
recomendaciones plausibles sobre precios que ya no existen, y el backtest
posterior las valida encantado.

### Los ocho checks y su severidad

| Check | Severidad | Qué detecta |
|---|---|---|
| `closing_lines_missing` | CRITICAL | partido empezado sin precio previo al inicio |
| `closing_coverage` | CRITICAL | cobertura de cierres por debajo del 95% |
| `stale_odds` | CRITICAL | partido inminente cuyo precio más reciente es viejo |
| `failed_job` | CRITICAL | jobs fallidos en 24h |
| `impossible_probability` | CRITICAL | implied fuera de (0,1), decimal ≤ 1 |
| `events_without_odds` | WARNING | partido en <6h sin ningún precio |
| `unmatched_backlog` | WARNING | cola de entidades sin resolver creciendo |
| `odds_after_start` | INFO | precios in-play (legítimos, nunca como pregame) |

### Decisiones de severidad, que son la parte difícil

**`closing_lines_missing` es CRITICAL aunque hoy no rompa nada.** Es el único
fallo del sistema cuya ventana no vuelve: el precio de cierre de ayer se perdió
ayer. Sin cierre no hay CLV, y sin CLV la validación pierde la muestra que hace
viable evaluar un modelo en semanas en vez de en temporadas.

**Solo los CRITICAL rompen la salud.** Un WARNING permanente que marcase el
sistema como enfermo entrenaría a cualquiera a ignorar el indicador — que es
exactamente cómo muere un sistema de alertas.

**`odds_after_start` es INFO, no error.** El in-play es legítimo; el problema
sería usarlo como pregame. Marcarlo como error sería el mismo fallo de
calibración de alertas.

**`stale_odds` es CRITICAL y `events_without_odds` es WARNING**, aunque suenen
parecidos. No tener precios puede ser normal (los books aún no publicaron); tener
precios que se quedaron congelados significa que el sync se paró, y eso no lo
arregla ningún filtro aguas abajo.

### Persistencia de hallazgos

Un hallazgo que sigue apareciendo **no se duplica**: conserva su fila y su
`detected_at`, lo que permite responder "¿desde cuándo?" — que suele ser la
primera pregunta útil ante un problema. Los que dejan de aparecer se marcan
resueltos automáticamente; sin eso el panel se llena de ruido histórico y deja de
mirarse.

---

## Phase 2a — API HTTP

**Estado:** completada. 489 tests, 94% de cobertura del paquete.

`python -m sportstar.cli serve` levanta la API en el puerto 8000, con
documentación interactiva en `/docs`.

| Endpoint | Qué devuelve |
|---|---|
| `GET /v1/recommendations` | apuestas recomendadas, ordenadas por confianza |
| `GET /v1/recommendations/{id}` | detalle con sus razones |
| `GET /v1/candidates` | **todos** los candidates, pasaran o no los filtros |
| `GET /v1/performance` | rendimiento, siempre con tamaño de muestra |
| `GET /v1/models` | registro de modelos |
| `GET /v1/health/data` | estado de los pipelines por severidad |
| `GET /v1/health` | liveness, sin tocar la base |

### Bug encontrado al exponer los datos

**`market_implied_probability` devolvía la fair probability redicha.**

El pipeline calcula el precio de referencia como `reference_decimal = 1/fair_prob`
—correcto, porque un consenso no tiene un precio único—, y `evaluate()` derivaba
la implícita de ese decimal. El resultado: `1/(1/fair) = fair`. El campo existía,
tenía nombre distinto, y contenía exactamente el mismo número.

No rompía nada visible: simplemente un cliente que quisiera mostrar el vig del
mercado sharp habría obtenido siempre cero, y nadie lo habría cuestionado porque
el número *parecía* razonable.

Corregido añadiendo `implied_probabilities` a `ConsensusResult` —el promedio de
las implícitas CON vig de los books de referencia— y pasándolo explícitamente.
Ahora su diferencia con la fair es el margen real que carga el mercado sharp en
ese lado. `evaluate()` acepta el parámetro como opcional: con un solo book de
referencia sí existe un precio con vig del que derivarla.

### Decisiones de contrato

**La API es de solo lectura.** Un test lo verifica recorriendo las rutas: solo
`GET`. Las recomendaciones las produce el pipeline, no una petición HTTP, y
mantenerla de lectura impide que un cliente altere el histórico de decisiones —
que debe ser inmutable para poder auditarlo.

**Ninguna métrica viaja sin su tamaño de muestra.** `PerformanceOut` incluye
`n_bets`, `n_candidates`, `metrics_are_interpretable` y una nota que dice
explícitamente qué se puede leer con esa muestra. Con 90 apuestas el ROI es
varianza con formato de porcentaje, y presentarlo desnudo es la forma más rápida
de convencerse de que algo funciona sin evidencia.

**Las tres probabilidades viajan en campos separados**, igual que en el esquema.
Fusionarlas en el transporte reintroduciría por la API el error que la base
evita.

**Las probabilidades son fracciones, no porcentajes.** El formateo es decisión de
presentación; mezclar unidades en el transporte es cómo aparecen los errores de
factor 100.

**Límite duro de paginación (200).** El consumidor principal es una PWA en un
móvil con mala conexión, no un script en un servidor.

**`total_edge` se recalcula al serializar** en vez de leerse de una columna:
derivarla evita que un cambio en el pipeline deje un campo desincronizado sin que
nadie lo note.

---

## Phase 2a — Verificación del esquema de MLB Stats API

**Estado:** normalizador de MLB **verificado contra datos reales**. El de odds
sigue pendiente (falta red y API key).

Captura del 2026-08-20, 9 partidos, pegada manualmente porque la política de red
del entorno de desarrollo sigue denegando la salida a `statsapi.mlb.com`. El
fixture sintético queda sustituido por la respuesta real.

### Resultado de la verificación

| Comprobación | Resultado |
|---|---|
| Eventos normalizados | 9 / 9, cero errores de forma |
| Equipos resueltos contra el catálogo | 18 / 18, todos por nombre exacto |
| Pitchers probables en partidos futuros | presentes en los tres |
| Marcadores en partidos terminados | presentes en los seis |
| IDs de equipo del proveedor | presentes en los nueve |

El normalizador acertó el esquema. Los `STATUS_MAP`, las rutas de `teams.home.
team.name`, `probablePitcher.fullName` y `venue.name` coincidían con la
documentación.

### El bug que solo aparece con datos reales: `officialDate`

**2 de los 9 partidos (22%) tenían `officialDate` distinto de la fecha UTC de
`gameDate`.**

```
822861   start=2026-08-21T00:05Z   officialDate=2026-08-20
824153   start=2026-08-21T00:10Z   officialDate=2026-08-20
```

Un partido de noche que empieza pasada la medianoche UTC pertenece a la jornada
del día anterior. El normalizador ignoraba `officialDate` por completo, así que
`Event.event_date` se habría derivado del timestamp y **el 22% de los partidos de
cada noche se habría archivado en el día equivocado**.

Consecuencias que habría tenido:

- Una consulta "partidos de hoy" habría devuelto media jornada, con la otra mitad
  colgando del día siguiente.
- El emparejamiento con The Odds API —que razona por jornada— habría fallado
  justo en los partidos nocturnos, que son la mayoría del slate en horario
  americano.
- La constraint de unicidad de eventos incluye `event_date`, así que un doblete
  que cruzara medianoche habría quedado con fechas distintas.

Ninguna de esas cosas lanza una excepción. Se habrían manifestado como un slate
más corto de lo debido, que es el modo de fallo silencioso contra el que existe
todo el módulo de Data Health.

Corregido añadiendo `official_date` a `NormalizedEvent`, tomándolo del proveedor
y cayendo a la fecha UTC solo si faltara.

**Este es el argumento entero a favor de verificar esquemas contra respuestas
reales en vez de contra documentación.** El campo estaba en la documentación; lo
que no estaba era que importara. Solo mirando un slate real se ve que dos
partidos cruzan medianoche y que el proveedor los sigue contando en la jornada
anterior.

### Pendiente

`the_odds_api_odds.json` sigue siendo sintético. Verificarlo requiere abrir la
red a `api.the-odds-api.com` y una `SPORTSTAR_ODDS_API_KEY`.

---

## Phase 2a — Verificación con datos reales de mercado

**Estado:** Phase 2a **cerrada**. Ambos normalizadores verificados contra
capturas reales. 531 tests, 94% de cobertura.

Captura del 2026-08-20: 15 eventos MLB, 224 precios, 9 casas. Pegada manualmente
porque la red del entorno sigue cerrada.

### El fallo que habría dejado el sistema mudo desde el primer día

El catálogo sembraba como books de referencia `pinnacle`, `circa` y `betonline`.
**Ninguno de los tres aparece en el feed de `regions=us`.** Y `betonline` ni
siquiera es la clave correcta: el proveedor la llama `betonlineag`.

Sin books de referencia, `consensus_fair_probabilities` devuelve `None`, no hay
fair probability, no hay candidates. El pipeline entero habría producido cero
recomendaciones sin lanzar una sola excepción.

Este es el modo de fallo exacto contra el que existe el módulo de Data Health, y
lo habría atrapado — pero solo después de una jornada perdida.

### D5 resuelto con evidencia: vig medido, no reputación

| Book | vig medio | mercados |
|---|---|---|
| betonlineag | **2.40%** | 14 |
| lowvig | **2.40%** | 14 |
| betus | **2.73%** | 10 |
| fanduel | 3.63% | 15 |
| draftkings | 3.97% | 14 |
| mybookieag | 4.00% | 14 |
| betrivers | 4.19% | 3 |
| bovada | 4.45% | 15 |
| betmgm | 4.67% | 13 |

Referencia: `betonlineag` y `betus`. Ejecutables: el resto, **provisional hasta
que el operador confirme dónde tiene cuenta**.

### Dos marcas del mismo operador no son un consenso de dos

`lowvig` y `betonlineag` publicaron el **mismo precio en 26 de 28 mercados
(93%)**. Es la misma casa: LowVig.ag es la marca de bajo margen de BetOnline.

Promediarlas hace tres daños a la vez, ninguno visible:

1. Infla `book_count`, que es lo que mira el gate de mínimo de referencias.
2. Hunde la dispersión a **cero**, señal de "los sharp coinciden".
3. Convierte una opinión en dos, con la confianza que eso arrastra.

Corregido con `Sportsbook.operator_group` y deduplicación por operador en el
consenso. Ante varias marcas de la misma casa se conserva la de id menor, para
que el backtest sea reproducible.

### Otros dos hallazgos de los datos reales

**Marcadores en partidos sin empezar.** MLB manda `score: 0` también en partidos
programados. Guardarlo haría un partido sin jugar indistinguible de un 0-0
terminado — y la liquidación de apuestas depende justo de esa distinción. Ahora
el marcador solo se conserva si el partido ha empezado.

**Los relojes de los proveedores no coinciden.** El mismo partido:

```
MLB Stats API   2026-08-20T22:35:00Z
The Odds API    2026-08-20T22:36:00Z
```

Un minuto. Emparejar por timestamp exacto habría duplicado cada evento. La
ingesta empareja por equipos más una ventana de ±6h.

### El resultado que importa: 28 candidates, 0 recomendaciones

Con el pipeline completo sobre el mercado real:

```
mejor ventaja total:  +0.44%   (WSH @ TEX, +168 en DraftKings)
umbral de los gates:  +2.00%
recomendaciones:      0
```

**Y eso es la respuesta correcta.** Si `market_consensus_v1` hubiera escupido
cinco edges del +5% sobre un mercado real de MLB moneyline, lo correcto sería
sospechar un bug, no celebrar. El riesgo R4 del audit decía exactamente esto: el
mercado es eficiente y el line shopping puro entre estas casas no deja margen
suficiente.

Lo que el sistema demuestra hoy es que **mide bien**, no que gane. Son cosas
distintas y la primera es requisito de la segunda.

Consecuencia estratégica para Phase 2b y Phase 6: si el edge estructural en MLB
moneyline es ~0 con estas casas, la ventaja tendrá que venir del modelo o de
mercados menos eficientes. El sistema ya está listo para medir ambas cosas.

---

## Phase 2b — Cimientos de features

**Estado:** contrato point-in-time y Elo listos. Faltan las features específicas
de MLB, que necesitan histórico. 558 tests, 91% de cobertura.

### Vía de datos que no depende de la red del entorno

`python -m sportstar.cli backfill --start ... --end ...` descarga el histórico
desde una máquina **con** red y lo deja en `data/raw/mlb/` como ficheros
comprimidos que se commitean. El histórico viaja por git.

Una temporada completa son **8 peticiones**, no 180: la MLB Stats API acepta
rangos de fechas, así que se pide mes a mes. El comando es reanudable — salta lo
ya descargado — porque una descarga interrumpida a mitad de temporada no puede
obligar a empezar de cero.

Los payloads se guardan íntegros, sin normalizar, por la misma razón que
`raw_payloads`: cuando un normalizador tenga un bug se reprocesa todo sin volver
a descargar.

### El invariante que sostiene todo lo demás

Una feature con `as_of = T` solo puede derivarse de hechos conocidos
**estrictamente antes** de T.

No es una buena práctica, es la condición para que el backtest signifique algo.
El leakage no produce un error: produce resultados *mejores*. Un backtest
contaminado sale precioso, convence, y no se reproduce en paper trading — y para
cuando se nota, se han perdido meses.

Por eso no se confía a la disciplina:

- `FeatureVector` guarda su `as_of` y el hecho más reciente que consumió.
- `assert_point_in_time` compara ambos y **lanza** si se cruzan.
- El criterio es `observed_at`, no la fecha del hecho: un marcador corregido dos
  días después no estaba disponible el día del partido, por mucho que su fecha
  diga lo contrario.

La igualdad también se rechaza. Un hecho observado en el instante del corte no
estaba disponible *antes* de él, y en la práctica esa igualdad casi siempre
delata un `as_of` derivado del propio dato que se pretende usar.

### Elo reconstruido en cada corte

`fit_through(games, as_of)` recorre los partidos ordenados por `observed_at` y se
detiene antes del corte. **No hay un rating "actual" que consultar**: cada `as_of`
produce su propio estado.

Es más lento que mantener un rating global, y es la diferencia entre un backtest
reproducible y uno que miente. La alternativa rápida es exactamente por donde se
cuela el leakage: basta con que un partido se incorpore antes de tiempo para que
todo deje de significar nada, y no hay forma de notarlo mirando el resultado.

Parámetros y por qué:

| Parámetro | Valor | Razón |
|---|---|---|
| K | 4.0 | En béisbol un partido suelto es casi todo ruido; una K alta hace que el rating lo persiga |
| Ventaja local | 24 pts | ≈54% de victoria local entre iguales, el orden histórico en MLB |
| Regresión entre temporadas | 0.30 | Las plantillas cambian; arrastrar el rating entero sobrestima la continuidad |

El margen de victoria se ignora a propósito: una paliza dice poco más que una
victoria ajustada, y premiarla es otra forma de perseguir ruido.

Todos los valores se revisan contra datos en Phase 3. Hoy son convenciones
explícitas, no resultados.

---

## Phase 2b — Histórico real: 2.574 partidos de la temporada 2024

**Estado:** histórico cargado y validado. Elo funcionando sobre datos reales.
595 tests, 92% de cobertura.

El operador descargó la temporada 2024 completa con `sportstar backfill` y la
commiteó: 8 ficheros, 1.8 MB comprimidos, **2.574 partidos, cero errores de
forma**.

### Dos bugs que solo aparecen con una temporada entera delante

**1. MLB marca los aplazados y cancelados como `"Final"`.**

`abstractGameState` decía "Final" en 42 partidos que nunca se jugaron: 36
aplazados y 6 cancelados. Solo `detailedState` los distingue.

Guardarlos como terminados tiene tres consecuencias, y ninguna es cosmética:

- Data Health los marcaría **eternamente** como partidos sin closing line, y ese
  check es CRITICAL. El panel quedaría permanentemente en rojo por 42 partidos
  fantasma, que es la forma en que un sistema de alertas deja de mirarse.
- La liquidación intentaría resolver apuestas de partidos que no existieron. Un
  cancelado es **VOID** —se devuelve el dinero—, no una derrota.
- Entrarían al histórico del modelo como partidos reales sin marcador.

Corregido leyendo `detailedState` antes que el abstracto.

**2. El histórico trae pretemporada, exhibiciones y el All-Star.**

De los 2.574 partidos: 2.469 de temporada regular, **93 de pretemporada, 7
exhibiciones y 1 All-Star**.

El síntoma fue inmediato al calcular Elo: **37 equipos** en una liga de 30, y
hasta 171 partidos jugados en una temporada de 162. Los siete de más eran
"American League All-Stars" —un equipo que no existe— y rivales de exhibición
como Diablos Rojos del México y un filial de ligas menores.

Lo grave no es el ruido de esos partidos, es que la pretemporada se juega con
prospectos: sus resultados mueven el rating sin decir nada de la fuerza real del
equipo.

Corregido con `COMPETITIVE_GAME_TYPES`. Tras filtrar: **30 equipos, 162 partidos
cada uno.**

### Validación del Elo sobre la temporada real

| # | Equipo | Elo |
|---|---|---|
| 1 | Los Angeles Dodgers | 1545.3 |
| 2 | San Diego Padres | 1539.9 |
| 3 | Philadelphia Phillies | 1531.2 |
| ... | | |
| 30 | Chicago White Sox | 1396.3 |

Los Dodgers ganaron la Serie Mundial de 2024; los White Sox hicieron 41-121, el
peor récord de la era moderna. No es una métrica de calidad —Elo no pretende
batir al mercado— pero un orden absurdo habría delatado un error de signo o de
emparejamiento, y no lo hay.

Un test verifica además que el sistema sigue siendo de suma cero tras 2.436
partidos, y que a mitad de temporada ningún equipo tiene más de 120 partidos
incorporados: el invariante point-in-time se sostiene sobre datos reales, no solo
sobre casos construidos.

### Confirmación de un arreglo anterior

**676 de los 2.574 partidos (26%) cruzan medianoche UTC.** Sin el arreglo de
`officialDate`, un cuarto de cada temporada habría quedado fechado un día tarde.
Lo que en un slate de nueve partidos parecía un detalle, en una temporada son 676
partidos mal archivados.

### Nota sobre el acceso

`load_backfill` acepta también `.json` sin comprimir, y
`docs/BACKFILL_WINDOWS.md` documenta cómo traer el histórico desde un navegador
sin instalar nada. No hizo falta —el operador ejecutó el CLI— pero queda como
respaldo para máquinas sin Python.

---

## Phase 2b — El primer modelo, y por qué no se despliega

**Estado:** modelo entrenado y evaluado sobre la temporada 2024. **No pasa el
criterio de salida.** 662 tests, 92% de cobertura.

Es el resultado importante de esta fase, y es negativo. Conviene leerlo entero.

### Resultados sobre corte temporal (638 partidos de test)

| Modelo | Brier | log loss | ECE | skill |
|---|---|---|---|---|
| Tasa base (sin información) | 0.2498 | 0.6928 | 0.0134 | −0.0007 |
| Elo solo | 0.2433 | 0.6796 | 0.0329 | +0.0255 |
| Regresión logística (5 features) | 0.2425 | 0.6780 | 0.0351 | +0.0285 |

La logística mejora el Brier de Elo en **0.0008**. Prueba pareada sobre los 638
partidos: **t = −0.70**. Indistinguible de ruido.

### El fallo que las métricas escondían

Cuatro de los cinco coeficientes salieron **con el signo invertido**:

```
season_win_pct_diff  +0.406
venue_win_pct_diff   -0.160   <- peor récord local predice MÁS victorias
rest_diff            -0.069
form_diff            -0.028   <- peor forma reciente predice MÁS victorias
elo_diff             -0.010   <- peor Elo predice MÁS victorias
```

Individualmente, **todas** correlacionan en positivo con el resultado
(elo +0.11, season +0.12, venue +0.085, form +0.057). Los signos correctos
existen en los datos; es la regresión la que los invierte.

La causa es colinealidad. Sobre las filas que el modelo entrena:

```
elo_diff ~ season_win_pct_diff    0.93
season_win_pct_diff ~ venue_...   0.87
elo_diff ~ venue_win_pct_diff     0.82
```

No son cinco señales: es **una señal medida cinco veces**. Cuando las columnas
dicen lo mismo, el reparto de peso entre ellas es arbitrario y los signos se
vuelven ruido.

**Lo grave no es la métrica** —apenas se movía— **sino que las explicaciones se
vuelven mentira.** Este sistema deriva las razones de los coeficientes
(`pipeline/reasons.py`). Un coeficiente invertido convierte "descanso: −0.07%"
en desinformación con formato de dato, presentada al usuario junto a un stake
recomendado.

### Un detalle que casi hace invisible el problema

La colinealidad **solo aparece en los datos que el modelo ve**. Sobre la
temporada entera, `elo_diff` y `season_win_pct_diff` correlacionan 0.66 —por
debajo del umbral—; sobre las filas post burn-in, 0.93.

En abril el Elo apenas se ha movido de 1500 mientras el récord oscila con cinco
partidos jugados. Diagnosticar sobre el conjunto equivocado habría dado el
problema por inexistente. Hay un test que fija ambas mediciones.

### El guardarraíl: `validation/features.py`

Tres checks que corren antes de aceptar un modelo:

- **Colinealidad**: pares con |r| ≥ 0.80.
- **Signos invertidos**: coeficiente que contradice la correlación marginal de
  esa feature con el resultado. Ignora las features sin señal apreciable, porque
  ahí el signo es ruido y marcarlo sería una falsa alarma.
- **Features sin señal**: |r| < 0.02 con el resultado.

`is_interpretable` es False si hay algún signo invertido. Un modelo así puede
desplegarse si sus métricas lo justifican, pero **no puede generar
explicaciones**.

### La decisión

`DEFAULT_MODEL_FEATURES = ("elo_diff",)`.

Con una sola columna: coeficiente **+0.222** (signo correcto), Brier 0.2426
—idéntico al modelo de cinco— y **mejor calibración** (ECE 0.027 frente a 0.035).

La elección es por parsimonia e interpretabilidad, **no por rendimiento en test**.
Comparé cinco conjuntos de features sobre el mismo test set, así que ese test set
está quemado para selección: elegir por él lo convierte en entrenamiento. La
métrica de 0.2419 del mejor conjunto (`elo_diff + rest_diff`) es optimista por
construcción y no se usa para decidir.

### Qué falta para batir al mercado

El techo de este modelo no es el algoritmo, son las features. Del calendario solo
sale **fuerza de equipo**, y eso lo captura mejor un solo número que cinco.

Para tener una probabilidad que compita con un closing line hacen falta datos que
el calendario no trae:

| Feature | Qué necesita |
|---|---|
| Calidad del starting pitcher (FIP/xFIP) | estadísticas de jugador |
| Estado y fatiga del bullpen | log de lanzamientos por partido |
| Park factors | histórico por estadio |
| Alineación confirmada | endpoint de lineups |
| Clima | proveedor meteorológico |

Nada de esto está en `/api/v1/schedule`.

### Sobre el criterio de salida

El roadmap exige batir el Brier de `market_consensus_v1` para desplegar. **No se
puede evaluar todavía**: no hay odds históricas, y las de The Odds API son de
pago.

Lo que sí se puede afirmar: el modelo apenas bate a Elo, y Elo no es el mercado.
Un closing line de un book sharp está mejor calibrado que cualquiera de los tres
modelos de la tabla. La conclusión provisional es que **este modelo no batiría al
mercado**, y el criterio de salida no está en riesgo de decidirse por optimismo.

---

## Captura automática del mercado

**Estado:** red del entorno abierta. Pipeline verificado de punta a punta sobre
el mercado **en vivo**. 672 tests, 91% de cobertura.

### Acceso confirmado

| Fuente | Estado |
|---|---|
| `statsapi.mlb.com` | abierto |
| `api.the-odds-api.com` | abierto |
| `.../v4/historical/` | **401** — existe, es plan de pago |
| `baseballsavant.mlb.com` | bloqueado |
| `retrosheet.org` | bloqueado |
| GitHub raw, PyPI | abiertos |

### Verificación en vivo

Captura real del 2026-08-23, ingesta y pipeline completo:

```
SYNC_SCHEDULE  25 recibidos, 25 emparejados, 0 errores
SYNC_ODDS      11 recibidos, 11 emparejados, 178 snapshots, 0 errores
               18 candidates, 0 recomendaciones
```

Cero recomendaciones otra vez, con la mejor ventaja en +0.18%. Consistente con la
jornada anterior, y sigue sin ser evidencia suficiente para concluir nada: son 46
observaciones en total.

### Coste de cuota, medido

**1 crédito por captura** con `markets=h2h` y `regions=us`, leído de
`x-requests-remaining` en respuestas reales (498 → 497).

Corrijo una estimación anterior en la que dije que la cuota se agotaría en días:
eso era suponiendo tres mercados y varias regiones. Con la configuración actual,
los 500/mes del plan gratuito dan para ~16 capturas diarias.

### El workflow

`.github/workflows/sync.yml` captura cada hora entre 16:00 y 04:00 UTC — trece al
día, 390 al mes, dentro de cuota con margen.

Se eligió GitHub Actions y no un cron en el entorno de desarrollo porque ese
contenedor es efímero y muere con la sesión. Esto corre en infraestructura de
GitHub y sobrevive.

Detalles que importan:

- **`concurrency` sin `cancel-in-progress`**: dos capturas solapadas chocarían al
  hacer push. Se descarta la nueva en vez de cortar la que está escribiendo.
- **Reintento con `pull --rebase`**: la segunda captura se reconstruye sobre la
  primera, nunca la pisa.
- **Los eventos `schedule` de GitHub solo disparan en la rama por defecto.** El
  workflow debe llegar a `main` para que la captura arranque sola.

### Bug corregido en el lector de snapshots

`path.stem` sobre `odds_20260823T2359Z.json.gz` devuelve
`odds_20260823T2359Z.json`: solo quita la última extensión. El parseo del
timestamp fallaba con toda captura comprimida, así que la lectura del histórico
estaba rota desde el primer fichero.

### Por qué esto es lo urgente

El closing line es la única medición del sistema cuya ventana no vuelve. Cada día
sin capturar es CLV perdido para siempre, y el CLV es entre 8 y 10 veces más
eficiente en muestra que el P&L.

Con 46 observaciones no se puede distinguir "nunca hay oportunidades" de "las hay
el 5% de las veces" — y el 5% serían ~250 apuestas por temporada. Seis jornadas
permiten descartar una frecuencia del 2%; dos semanas, estimarla.
