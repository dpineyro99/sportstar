# Histórico de odds — procedencia, bug y reparación

> **Resumen:** once temporadas de MLB (2011-2021, 25.586 partidos) con moneyline
> de apertura y cierre. El volcado público del que salen **está corrompido**; el
> bug es determinista y se repara. Esto desbloquea Phase 3.

---

## 1. De dónde salen

`sportstar odds-history` descarga el archivo histórico que publica
[`flancast90/sportsbookreview-scraper`](https://github.com/flancast90/sportsbookreview-scraper)
—un volcado pre-scrapeado de sportsbookreviewsonline.com— y lo repara.

Se descarga anclado a un commit (`1a820e5`), nunca a una rama. Un backtest cuyo
dataset de entrada puede cambiar bajo los pies no es reproducible, y un cambio
silencioso del upstream se manifestaría como una "mejora" del modelo que en
realidad es otro dataset.

**Procedencia y licencia.** El código del repositorio de origen es MIT (© 2023
Finn Lancaster). Los datos subyacentes son de sportsbookreviewsonline.com y no
llevan licencia explícita. Se usan aquí para investigación propia y **no se
redistribuyen desde este repositorio**: de ahí que se descarguen en tiempo de
ejecución a `data/raw/odds_history/`, que está en `.gitignore`.

---

## 2. Qué contiene y qué no

| | |
|---|---|
| Temporadas | 2011-2021 (2020 corta: 948 partidos) |
| Partidos | 25.586 |
| Mercados | moneyline (apertura + cierre), total, runline |
| Casa | **ninguna**: es la línea de consenso que publica SBR |
| Marcas de tiempo | **ninguna**: solo se sabe cuál es la apertura y cuál el cierre |

Las dos ausencias marcan el límite de lo que se puede hacer con esto:

- **Permite** backtestear a cierre, medir CLV apertura→cierre y calibrar modelos
  contra la probabilidad justa del mercado.
- **No permite** reconstruir el movimiento intradía de la línea, comparar precios
  entre casas, ni buscar edge estructural (que es precisamente comparar casas).

Por eso `SbrGame` **no lleva `observed_at`**. El archivo no dice cuándo se
observó cada precio; inventar una marca de tiempo aquí sería la vía más directa a
un backtest con leakage, así que no se inventa.

---

## 3. El bug del upstream

El volcado publicado **no es usable tal cual**: cada fila mezcla dos partidos
distintos. El origen está en tres líneas de `scrapers/sportsbookreview.py`:

```python
progress = df.iterrows()
next(progress)                       # <- salta una fila de más
for (i1, row), (i2, next_row) in self._pairwise(progress):
```

La tabla de SBR trae **dos filas por partido** (visitante, luego local). El
DataFrame ya venía recortado con `dfs[0][1:]`, o sea con la cabecera fuera, así
que ese `next(progress)` no salta la cabecera: se come la fila del visitante del
primer partido. A partir de ahí el emparejamiento va corrido una posición, y cada
fila publicada junta el **local del partido k** con el **visitante del k+1**,
además intercambiando las etiquetas local/visitante.

### Cómo se ve

El 1 de abril de 2011 el volcado publica `Phillies @ Pirates 5-6`. La realidad
fueron dos partidos distintos:

```
Astros  @ Phillies  4-5
Pirates @ Cubs      6-3
```

Los Phillies sí marcaron 5 y los Pirates sí marcaron 6. Lo roto es que estén en
la misma fila. **Cada mitad está intacta**; lo único destruido es el
emparejamiento — y por eso se puede deshacer.

### Los síntomas agregados

| métrica | tal cual | tras reparar | valor real |
|---|---|---|---|
| "empates" (imposibles en MLB) | 2.653 | 18 | ~0 |
| sobre-redondeo p1 / p99 | −18% / +24% | +1,5% / +4,8% | 2-5% |
| % victorias del local | 48,2% | **53,5%** | ~53,5% |
| Brier de la línea de cierre | 0,2462 | **0,2404** | — |
| error de calibración (ECE) | 0,0174 | **0,0036** | ~0 |

El sobre-redondeo negativo es la firma inconfundible: significa **dos favoritos
en un mercado de dos vías**, que no existe. El 53,5% de victorias locales es el
valor histórico real de MLB.

### Contraste contra verdad externa

Cinco jornadas al azar entre 2011 y 2021, contrastadas contra la MLB Stats API
sobre la tupla completa (local, visitante, marcador local, marcador visitante):

```
2011-04-01   11/11        2016-09-15   10/10        2021-08-10   16/17
2013-07-04   14/15        2019-05-20   10/10
                                            total   61/63 = 96,8%
```

Los dos que no casan no son errores de la reparación: un partido suspendido que
la MLB Stats API devuelve sin marcador, y una segunda parte de doble jornada.

---

## 4. Por qué se detecta en vez de aplicarse a ciegas

Si el upstream arregla su scraper, aplicar la corrección de oficio **crearía** la
corrupción que hoy repara. Así que no se aplica de oficio.

`detect_pairing` construye las dos hipótesis —`AS_PUBLISHED` y `SHIFTED`— y mide
en cada una cuántos datos salen **físicamente imposibles**: empates en MLB y
sobre-redondeos fuera de rango. Se queda con la coherente. Si ninguna lo es, o si
las dos lo son por igual, aborta con `PairingUndecidable` en vez de adivinar.

Sobre el fichero real la decisión no es ajustada:

```
as_published  incoherencia=0.682 (empates=0.104, sobre-redondeo fuera de rango=0.579)
shifted       incoherencia=0.001 (empates=0.001, sobre-redondeo fuera de rango=0.001)
```

Un factor 682. El margen mínimo exigido es 0,10.

---

## 5. La auditoría

Detectar el emparejamiento no es lo mismo que validar el resultado: el detector
solo compara dos hipótesis **entre sí**, así que podría elegir bien la menos mala
de dos malas. `validation/market_history.py` es lo que dice si lo que salió es un
mercado de verdad. Cuatro checks, todos bloqueantes:

1. **Sobre-redondeo plausible.** Vig negativo es imposible; vig del 20% no es un
   mercado, es un error de emparejamiento.
2. **Tasa de victoria local.** MLB lleva décadas en 53-54%. Un 48% dice que local
   y visitante están cruzados en parte del fichero.
3. **Calibración del cierre.** La línea de cierre sin vig es el mejor estimador
   público que existe: tiene que salir casi perfectamente calibrada.
4. **El cierre gana a la apertura.** El resultado más robusto de la literatura.
   Si la apertura predice mejor, las columnas están intercambiadas.

El cuarto es el que más veces salva, porque es *interno*: no necesita ninguna
fuente externa contra la que contrastar.

Un histórico que dispara un `FATAL` no entra al backtest con una advertencia en
el log. **No entra**: `load()` lanza `HistoryRejected`.

---

## 6. Uso

```bash
sportstar odds-history            # descarga, repara, audita e informa
```

```python
from sportstar.odds_history import load

history = load("mlb", seasons=range(2011, 2020))   # train
print(history.audit.summary())
for game in history.games:
    ...   # SbrGame: equipos, marcador, moneyline de apertura y cierre
```

La caché va en `data/raw/odds_history/`, con el commit en el nombre del fichero:
pedir otro commit nunca puede leer la caché del anterior.

---

## 7. Lo que esto cambia y lo que no

**Cambia:** Phase 3 deja de depender de acumular cierres propios durante una
temporada. Hay 25.586 partidos con apertura y cierre para backtestear *hoy*, y
suficiente muestra para las afirmaciones sobre ROI que `sanity.py` exige (500
apuestas mínimo, 5.000 para creerse un ROI).

**No cambia:** sigue sin haber precios por casa, así que el **edge estructural
—comparar casas para encontrar el mejor precio— no se puede backtestear con
esto**. Esa parte del sistema solo se puede validar con la captura propia que ya
está corriendo cada hora. Son dos fuentes de edge distintas y solo una queda
desbloqueada.

Y sigue sin haber datos de lanzador abridor, que es el factor dominante en MLB.
Un backtest sobre estos 25.586 partidos mide lo que valen los modelos que
tenemos, no el techo de lo que se puede hacer.
