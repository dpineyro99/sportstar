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
2. CLV como métrica central de éxito, por encima del P&L a corto plazo.
3. Odds append-only: un precio histórico nunca se sobrescribe.
4. Features point-in-time (`as_of`): el data leakage es imposible por construcción.
5. Cada predicción sabe qué modelo y qué features la generaron.
6. Un resultado extraordinario se trata como un bug hasta demostrar lo contrario.
