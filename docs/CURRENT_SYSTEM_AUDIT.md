# CURRENT SYSTEM AUDIT

**Fecha:** 2026-08-19
**Alcance:** repositorio `dpineyro99/sportstar`
**Autor:** Phase 0 — Audit

---

## 1. Hallazgo principal

**No existe un sistema previo que auditar en este repositorio.**

Evidencia recogida directamente del repo:

| Verificación | Comando | Resultado |
|---|---|---|
| Commits totales | `git rev-list --all --count` | `1` |
| Archivos que han existido alguna vez (todas las refs) | `git log --all --diff-filter=A --name-only` | `README.md` únicamente |
| Contenido del README | `cat README.md` | `# sportstar` (11 bytes) |
| Stashes | `git stash list` | vacío |
| Ramas | `git branch -a` | `main`, `claude/sports-betting-intelligence-audit-6n7fjd` |
| Repos accesibles en la sesión | `list_repos` | solo `dpineyro99/sportstar` |

No hay rastro de `papi-sports-intelligence`, ni de FastAPI, SQLite, Streamlit,
scripts de sincronización, ni de las tablas `games` / `teams` / `odds_snapshots` /
`model_pick_snapshots` / `paper_bets` descritas en el brief.

**Confirmado con el usuario (2026-08-19):** el sistema descrito era el plan mental,
no código existente. Se arranca limpio.

### Consecuencia para el roadmap

Los pasos de Phase 0 que dependían de código existente quedan sin objeto:

- ~~STEP 2: ejecutar tests existentes~~ — no hay tests.
- ~~STEP 3: identificar cómo funciona hoy el ingestion / odds / modelo / dashboard~~ — no hay implementación.
- ~~STEP 5: dibujar arquitectura actual~~ — no hay arquitectura actual.
- ~~STEP 8: clasificar KEEP / IMPROVE / REFACTOR / REPLACE / REMOVE~~ — todo es **ADD**.

Lo que sí sobrevive y se entrega: **STEP 6 (arquitectura objetivo)**,
**STEP 7 (roadmap)** y **STEP 10 (resumen ejecutivo)**.

### La única ventaja real de empezar limpio

No hay que negociar con deuda técnica. Las tres decisiones que en un sistema
heredado son carísimas de revertir se pueden tomar bien desde el commit 1:

1. **Snapshots de odds append-only** (nunca un `UPDATE` sobre un precio).
2. **Features con `as_of` timestamp** (imposibilita el data leakage por construcción).
3. **Taxonomía de mercados genérica** (moneyline es un caso particular, no el modelo base).

Retrofitear cualquiera de las tres sobre un sistema ya poblado significa perder
el histórico. Ver `ARCHITECTURE.md` §2.

---

## 2. Auditoría del plan (no del código)

Como no hay código que criticar, aplico el mismo escrutinio al diseño propuesto
en el brief. Estos son los puntos donde, en mi experiencia, este tipo de proyecto
falla — y dónde el brief ya acierta o todavía tiene un hueco.

### 2.1 Lo que el brief acierta y hay que proteger

| Decisión | Por qué importa |
|---|---|
| Separar `implied` / `no-vig` / `model` probability | Es el error #1 en sistemas amateur: comparar el modelo contra el precio con vig infla el edge ~2-3% de forma sistemática y constante. |
| CLV como métrica central | Demostrar un ROI real del +3% exige ~5.000-8.000 apuestas; un beat-close rate del 55%, ~500-1.000. El CLV es 8-10x más eficiente en muestra y es el feedback honesto más rápido que existe. Ver R8. |
| Snapshots históricos, nunca sobrescribir | Sin esto el backtest es irreproducible y el CLV incalculable. Irrecuperable a posteriori. |
| Separar `candidate` de `recommended` | Permite medir el filtro por separado del modelo. Sin esta separación no se puede saber si el filtro añade o destruye valor. |
| "Trata resultados extraordinarios como bugs" | Correcto y poco común. Lo convertimos en código, no en buena intención (§2.3). |
| Model registry con versión por predicción | Sin esto, cualquier retrain borra la capacidad de evaluar el histórico. |

### 2.2 Huecos y riesgos del plan tal como está escrito

**R1 — "Fair probability" contra el book equivocado.** *(riesgo alto)*
El brief define `Edge = Model P - Market Fair P`, pero no especifica *de qué
mercado* sale la fair probability. Quitarle el vig al precio del mismo book donde
vas a apostar es casi inútil: los books recreativos tienen precios sesgados a
propósito. La referencia debe ser un **consenso de sharp books** (Pinnacle,
Circa, BetOnline), y la apuesta se ejecuta en el book **recreativo** que ofrezca
el mejor precio. El edge real vive en esa diferencia, no en la diferencia contra
uno mismo. → Reflejado en `ARCHITECTURE.md` §4.2.

**R2 — Quitar el vig proporcionalmente está sesgado.** *(riesgo medio)*
El método obvio (dividir cada implied prob por la suma) asume que el vig se
reparte igual entre ambos lados. Empíricamente no es así: los underdogs cargan
más vig (favorite-longshot bias). En un mercado -120/+105 el error es pequeño;
en +450 es material y siempre te hace ver edge donde no lo hay. → Implementar
proporcional (default), **Shin** y **power method**, elegir por evidencia contra
closing lines reales, no por preferencia. → `ARCHITECTURE.md` §4.1.

**R3 — El Confidence Score puede volverse pseudociencia.** *(riesgo medio)*
El brief pide 0-10 "no arbitrario", pero cualquier fórmula que escribamos hoy —
sin datos — es arbitraria por definición. Recomendación: en v1 el confidence es
una función **explícita, documentada y provisional** de cantidades ya medibles, y
se marca como `confidence_version = 0` hasta que exista backtest que permita
recalibrar los pesos. Mismo tratamiento, más estricto, para el **PAPI SCORE**:
no se define hasta Phase 4. El brief ya lo dice; el riesgo es que se nos olvide
bajo la tentación de tener un número bonito en el dashboard.

**R4 — Elo / rolling averages son un baseline débil frente al mercado.** *(riesgo alto, y es el riesgo central del proyecto)*
Hay que ser honestos sobre la línea base real: el closing line de un sharp book
es una de las predicciones mejor calibradas que existen en cualquier dominio.
Un Elo con rolling averages **no le va a ganar**. En MLB moneyline concretamente,
el mercado es muy eficiente y el vig es bajo. Esto no invalida el proyecto, pero
sí dicta la estrategia: el primer objetivo no es "ganarle al cierre", es
**empatar con el cierre estando calibrados**. Si el modelo v1 alcanza un Brier
score comparable al del mercado de apertura, ya tenemos una base sobre la que
buscar nichos. Si el modelo ni siquiera se acerca, ningún filtro de edge lo va a
arreglar — y el sistema debe decírnoslo en Phase 2, no en Phase 9.

**Resolución (discutido 2026-08-19).** El riesgo se reformula al separar las dos
fuentes de edge (`ARCHITECTURE.md` §1.1): el *edge de modelo* efectivamente es
difícil y no se asume alcanzable, pero el *edge estructural* — dispersión de
precios entre books, líneas obsoletas, sesgo recreativo — no requiere modelo y
la arquitectura ya lo captura. Dos consecuencias adoptadas:

1. El primer modelo del sistema es `market_consensus_v1`, el propio consenso
   sharp sin vig (`ARCHITECTURE.md` §5.3). Valida el pipeline sin depender de la
   calidad del modelado, cuantifica el edge estructural en aislamiento, y fija la
   vara: ningún modelo se despliega si no bate su Brier score.
2. R4 deja de ser amenaza existencial y pasa a ser criterio de aceptación
   verificable. → `ROADMAP.md` Phase 2.

**R8 — La muestra necesaria no cabe en el calendario.** *(riesgo alto, detectado al revisar el roadmap)*
Con ~15 partidos MLB diarios y apostando ~15% del slate salen 2-3 apuestas al día:
**60-90 en 30 días**. Con esa muestra un beat-close rate del 55% es
indistinguible del azar, y el ROI necesitaría años. El criterio de salida
original de Phase 4 ("30 días") era operativo disfrazado de estadístico.

Solución adoptada: **no hace falta apostar para validar un modelo**
(`ARCHITECTURE.md` §4.6). Comparando `model_prob` contra la closing fair
probability de cada evento se mide la misma señal sobre el slate completo en vez
de sobre lo apostado — 1-2 órdenes de magnitud más muestra, sin riesgo. Requiere
capturar cierres de todo el slate, no solo de lo recomendado; coste marginal
despreciable. El criterio de Phase 4 queda partido en operativo y estadístico.

Efecto lateral relevante: al generar histórico propio de cierres desde el día 1,
baja la urgencia de comprar el histórico de odds de terceros (R7).

**R5 — El edge se mide contra un precio que ya no existe.** *(riesgo medio)*
Un snapshot de odds de hace 40 minutos produce edge fantasma. Necesitamos
`line_freshness` como campo de primera clase y un filtro duro. Sin esto el
backtest sale precioso y el paper trading no lo reproduce.

**R6 — Correlación tratada como problema de Phase 9.** *(riesgo medio)*
Correcto no construir el portfolio engine ahora, pero sí hay que registrar
desde el día 1 el `correlation_group` de cada apuesta (mismo evento + mismo
lado direccional). Añadir el campo después es barato; reconstruir la exposición
histórica real sin él, no.

**R7 — Coste operativo de los datos de odds.** *(riesgo bajo, pero bloqueante)*
Los históricos de odds son el input más caro del proyecto y suelen ser de pago.
El backtest de Phase 3 depende enteramente de que tengamos ese histórico. Hay que
resolver la fuente **antes** de Phase 3, o Phase 3 se convierte en "esperar seis
meses acumulando snapshots propios". → Decisión pendiente, listada en §3.

### 2.3 Guardarraíles estadísticos que se convierten en código

El brief pide escepticismo ante buenos resultados. Lo implementamos como un
módulo `validation/sanity.py` que corre automáticamente sobre cualquier backtest
y **bloquea** el reporte si dispara:

- ROI > 15% con n < 500 apuestas → sospecha de leakage o settlement incorrecto.
- Win rate > 60% en moneyline con precios promedio cercanos a pick'em.
- Cualquier feature cuyo `as_of` sea posterior al `bet_time` → leakage duro, error fatal.
- Odds usadas cuyo `captured_at` sea posterior al `start_time` del evento.
- Suma de implied probs de un mercado < 1.0 (arbitraje aparente = casi siempre precio corrupto o línea mal emparejada).
- Eventos duplicados por `(sport, start_time, home, away)`.
- Distribución de edge con media muy positiva → suele indicar error de vig, no ventaja.
- Cobertura de closing lines < 95% del slate → la validación de §4.6 pierde potencia y sesga hacia los eventos que sí se capturaron.

Un backtest que no pasa estos checks no produce número, produce un error.

---

## 3. Decisiones pendientes (bloquean Phase 1)

| # | Decisión | Recomendación | Bloquea |
|---|---|---|---|
| D1 | Proveedor de odds y si contratamos histórico | The Odds API para live. La captura propia de cierres del slate completo (R8) genera histórico útil desde el día 1, así que la compra deja de ser bloqueante y pasa a ser aceleración | Phase 3 |
| D2 | Fuente de stats MLB | MLB Stats API oficial (gratis, granular, con histórico) | Phase 2 |
| D3 | Motor de base de datos | SQLite + WAL detrás de SQLAlchemy + Alembic; migración a Postgres sin reescribir | Phase 1 |
| D4 | Bankroll de referencia en unidades | 1 unit = 1% del bankroll; todo se reporta en units, nunca en dólares | Phase 4 |
| D5 | Books objetivo | Sharp: Pinnacle (referencia). Recreativos: los que realmente puedas usar | Phase 2 |

---

## 4. Clasificación (STEP 8)

Con repositorio vacío, la clasificación completa es trivial pero se deja
registrada para trazabilidad:

- **KEEP:** nada (no hay código previo).
- **IMPROVE / REFACTOR / REPLACE / REMOVE:** no aplica.
- **ADD:** la totalidad del sistema. Orden y criterios de aceptación en `ROADMAP.md`.

---

## 5. Estado

Phase 0 cerrada. No se ha escrito código de producción. Los entregables son
este documento, `ARCHITECTURE.md`, `DATA_MODEL.md` y `ROADMAP.md`.
Phase 1 no ha comenzado — pendiente de revisión conjunta.
