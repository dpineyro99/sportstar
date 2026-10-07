# Conseguir el histórico de MLB sin instalar nada

El sistema necesita temporadas de partidos para calcular features (forma
reciente, calidad de pitcher, Elo). El entorno de desarrollo no tiene salida a
`statsapi.mlb.com`, así que los datos hay que traerlos desde fuera.

Hay dos caminos. **El A no requiere instalar nada** y funciona desde cualquier
navegador en Windows.

---

## Camino A — Navegador + GitHub web (sin instalar nada)

### 1. Descargar los 8 ficheros

Abre cada URL en una pestaña. Cuando cargue el JSON, pulsa **Ctrl+S** y guárdalo
con el nombre que se indica. Son ~1-2 MB cada uno.

| Guardar como | URL |
|---|---|
| `schedule_2024-03.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-03-20&endDate=2024-03-31&hydrate=probablePitcher |
| `schedule_2024-04.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-04-01&endDate=2024-04-30&hydrate=probablePitcher |
| `schedule_2024-05.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-05-01&endDate=2024-05-31&hydrate=probablePitcher |
| `schedule_2024-06.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-06-01&endDate=2024-06-30&hydrate=probablePitcher |
| `schedule_2024-07.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-07-01&endDate=2024-07-31&hydrate=probablePitcher |
| `schedule_2024-08.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-08-01&endDate=2024-08-31&hydrate=probablePitcher |
| `schedule_2024-09.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-09-01&endDate=2024-09-30&hydrate=probablePitcher |
| `schedule_2024-10.json` | https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-10-01&endDate=2024-10-01&hydrate=probablePitcher |

> Si Chrome muestra el JSON en un visor con pestañas, usa la pestaña **Raw** o
> **Datos sin procesar** antes de guardar. Lo que hace falta es el JSON tal cual,
> no la página del visor.

### 2. Subirlos a GitHub por la web

1. Ve a **github.com/dpineyro99/sportstar**
2. Cambia a la rama `claude/sports-betting-intelligence-audit-6n7fjd`
   (el desplegable que pone `main`)
3. **Add file** → **Upload files**
4. Arrastra los 8 ficheros
5. En el campo de ruta escribe: `data/raw/mlb/`
6. **Commit changes**

Y ya está. Los datos llegan al sistema por git.

---

## Camino B — PowerShell (si prefieres línea de comandos)

No necesita Python. PowerShell viene con Windows.

```powershell
mkdir -Force data\raw\mlb
cd data\raw\mlb
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-03-20&endDate=2024-03-31&hydrate=probablePitcher" -OutFile "schedule_2024-03.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-04-01&endDate=2024-04-30&hydrate=probablePitcher" -OutFile "schedule_2024-04.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-05-01&endDate=2024-05-31&hydrate=probablePitcher" -OutFile "schedule_2024-05.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-06-01&endDate=2024-06-30&hydrate=probablePitcher" -OutFile "schedule_2024-06.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-07-01&endDate=2024-07-31&hydrate=probablePitcher" -OutFile "schedule_2024-07.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-08-01&endDate=2024-08-31&hydrate=probablePitcher" -OutFile "schedule_2024-08.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-09-01&endDate=2024-09-30&hydrate=probablePitcher" -OutFile "schedule_2024-09.json"
Invoke-WebRequest -Uri "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2024-10-01&endDate=2024-10-01&hydrate=probablePitcher" -OutFile "schedule_2024-10.json"
```

Después, subir los ficheros por la web de GitHub como en el paso 2 del camino A,
o con git si lo tienes instalado.

---

## Verificar que llegaron bien

Una vez subidos, el sistema los lee con:

```
python -m sportstar.cli status
```

Los ficheros se aceptan comprimidos (`.json.gz`) o en JSON plano (`.json`), que
es lo que produce la descarga desde el navegador.

## Por qué solo 8 peticiones

La MLB Stats API acepta rangos de fechas, así que se pide mes a mes en vez de día
a día. Una temporada completa día a día serían 180 peticiones.

## Por qué `hydrate=probablePitcher` y nada más

El pitcher probable es la feature más importante de MLB y se conoce antes del
partido. El resto de hidrataciones (`linescore` sobre todo) multiplican el tamaño
del fichero y el normalizador no las usa.
