# CHANGELOG

Decisiones y cambios con consecuencias. No se documenta lo trivial.

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
