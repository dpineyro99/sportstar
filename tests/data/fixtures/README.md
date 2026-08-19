# Fixtures de proveedores

Estos ficheros son la **especificación ejecutable** del formato que esperamos de
cada API externa.

Los que hay ahora están escritos según la documentación pública, **no capturados
de las APIs reales**: la política de red del entorno donde se desarrollaron
deniega la salida a `statsapi.mlb.com` y `api.the-odds-api.com`.

## Sustituirlos por capturas reales

```bash
export SPORTSTAR_ODDS_API_KEY=...          # solo para el de odds
python -m sportstar.cli capture
pytest tests/data -q
```

`capture` sobrescribe estos ficheros con las respuestas reales y los tests pasan
a validarse contra ellas. Si algún test falla, el mensaje dice exactamente qué
clave faltaba y qué llegó en su lugar: eso es la verificación del esquema, y es
el motivo por el que los normalizadores invierten en diagnósticos precisos.

**Nunca se guarda la URL de la petición**, solo el cuerpo de la respuesta: la API
key viaja en la query string y acabaría commiteada.
