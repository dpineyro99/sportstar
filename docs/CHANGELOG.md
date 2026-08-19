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
