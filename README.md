# sportstar

Sports Betting Intelligence platform.

El objetivo no es predecir partidos, es **encontrar dónde se equivoca el mercado**:
estimar probabilidades calibradas, compararlas contra la probabilidad justa
(sin vig) del mercado y detectar expected value real.

**Estado:** Phase 2a — pipeline, persistencia, Data Health y API funcionando con
el mercado como modelo. Proveedores escritos, pendientes de verificar contra
respuestas reales.

## Puesta en marcha

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python -m sportstar.cli init     # aplica migraciones
python -m sportstar.cli seed     # puebla el catálogo (idempotente)
python -m sportstar.cli status
python -m sportstar.cli demo     # pipeline completo con precios sintéticos
python -m sportstar.cli health   # checks de calidad de datos
python -m sportstar.cli serve    # API en http://localhost:8000/docs

pytest -q
```

## Verificar los proveedores de datos

Los normalizadores están escritos contra la documentación de cada API, no contra
respuestas verificadas. Para cerrar ese hueco hace falta salida de red a
`statsapi.mlb.com` y `api.the-odds-api.com`:

```bash
export SPORTSTAR_ODDS_API_KEY=...   # solo para el de odds
python -m sportstar.cli capture     # sobrescribe los fixtures con datos reales
pytest tests/data -q                # los valida contra el esquema esperado
```

Si algo falla, el mensaje dice qué clave faltaba y qué llegó en su lugar.

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/CURRENT_SYSTEM_AUDIT.md`](docs/CURRENT_SYSTEM_AUDIT.md) | Auditoría de Phase 0, riesgos y decisiones pendientes |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Arquitectura objetivo, núcleo matemático, módulos |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Esquema de base de datos |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Fases con criterios de salida verificables |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Decisiones y cambios con consecuencias |
| [`docs/BACKFILL_WINDOWS.md`](docs/BACKFILL_WINDOWS.md) | Cómo traer el histórico de MLB sin instalar nada |

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
