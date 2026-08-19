# sportstar

Sports Betting Intelligence platform.

El objetivo no es predecir partidos, es **encontrar dónde se equivoca el mercado**:
estimar probabilidades calibradas, compararlas contra la probabilidad justa
(sin vig) del mercado y detectar expected value real.

**Estado:** Phase 0 (diseño). Sin implementación todavía.

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/CURRENT_SYSTEM_AUDIT.md`](docs/CURRENT_SYSTEM_AUDIT.md) | Auditoría de Phase 0, riesgos y decisiones pendientes |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Arquitectura objetivo, núcleo matemático, módulos |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Esquema de base de datos |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Fases con criterios de salida verificables |

## Principios

1. Probabilidades calibradas, no picks binarios.
2. Dos fuentes de edge distintas: **estructural** (dispersión de precios entre books, no requiere modelo) y **de modelo** (nuestra probabilidad supera a la del mercado). Se miden por separado.
3. El primer modelo es el mercado. Ninguno se despliega si no bate al consenso sharp en calibración.
4. CLV como métrica central de éxito, por encima del P&L a corto plazo.
5. Odds append-only: un precio histórico nunca se sobrescribe.
6. Cierres capturados del slate completo: permite validar modelos sin apostar.
7. Features point-in-time (`as_of`): el data leakage es imposible por construcción.
8. Cada predicción sabe qué modelo y qué features la generaron.
9. Un resultado extraordinario se trata como un bug hasta demostrar lo contrario.
