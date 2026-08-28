# Lanzadores — el experimento, y por qué su resultado es el más informativo hasta ahora

> **Resultado:** la calidad del abridor **predice de sobra por su cuenta** y **no
> añade nada** sobre el precio de apertura. El coeficiente de la feature cae de
> `+0,2545` a `+0,0166` —factor 15— cuando el mercado entra en el modelo. El
> mercado de MLB ya lleva el abridor incorporado antes de abrir la línea.

---

## 1. Por qué este experimento

Phase 3 cerró con un diagnóstico concreto: ningún modelo batía al mercado, y el
sospechoso obvio era que el modelo **no veía al lanzador abridor**, que en MLB es
el factor dominante. Un mismo equipo con dos abridores distintos es, a efectos de
mercado, dos equipos distintos.

Así que la pregunta de esta fase no es "¿es bueno mi modelo?" sino la única que
importa a estas alturas:

> **¿contiene la calidad del abridor algo que el mercado no tenga ya?**

---

## 2. Los datos

| | |
|---|---|
| Abridores previstos | calendario hidratado de la MLB Stats API, **11 peticiones**, 99,7% de cobertura |
| Estadísticas | game log por (lanzador, temporada), **~3.400 peticiones** |
| Cobertura | 2011-2021, 1.095 lanzadores, 72.568 apariciones (50.372 aperturas) |
| Tamaño en disco | 1,1 MB comprimido, **commiteado** |

Se usa el **game log**, no el total de temporada. El total es veinte veces más
barato y completamente inservible para un backtest: incluye los partidos que se
están prediciendo.

### Advertencia honesta sobre `probablePitcher` en histórico

Para partidos ya jugados, la API devuelve en `probablePitcher` al lanzador que
**efectivamente abrió**, no necesariamente al anunciado días antes. Un abridor
que se cae a última hora aparece como si nunca hubiese estado previsto.

Es una fuga pequeña pero real y no se puede cerrar con esta fuente. Se mitiga
sola en parte: en el mercado real, una apuesta al moneyline de MLB con "listed
pitchers" se anula si cambia el abridor, así que el caso en que la fuga
importaría es también el caso en que la apuesta no existiría.

---

## 3. FIP, y el problema del tamaño de muestra

La ERA mide lo que pasó; el **FIP** mide lo que el lanzador controla. Solo usa
home runs, bases por bolas y ponches —los tres sucesos que no dependen de la
defensa— y por eso se estabiliza mucho antes:

```
fip_core = (13*HR + 3*BB - 2*K) / entradas          menor es mejor
```

Sin la constante de liga, que solo sirve para que la escala coincida con la de la
ERA; aquí lo único que importan son diferencias entre lanzadores.

Un abridor con una apertura tiene un FIP sin sentido, así que se **encoge hacia
la media de liga** en proporción a la muestra:

```
encogido = (observado·n + media_liga·k) / (n + k)      k = 400 bateadores
```

**La media de liga también es point-in-time**: se acumula sobre lo ya observado,
nunca se lee el total de la temporada.

### Validez aparente

Un FIP con el signo invertido, o la trampa de las entradas mal resuelta, pasarían
todos los tests sintéticos y darían un ranking absurdo. Así que se fija contra la
historia real. Mejores diez de 2011-2016 con ≥100 aperturas:

```
-0,749  Clayton Kershaw          -0,038  Adam Wainwright
-0,173  Stephen Strasburg        -0,016  David Price
-0,133  Chris Sale               +0,022  Félix Hernández
-0,122  Cliff Lee                +0,030  Max Scherzer
-0,104  Corey Kluber
-0,054  Madison Bumgarner        media de liga: +0,837
```

Es exactamente la lista que diría cualquiera, con Kershaw primero y por un
abismo. Los peores —Correia, Roberto Hernandez, Arroyo, Saunders, Guthrie— son
los comeinnings de la época. **La feature funciona.**

### La trampa de las entradas

`"6.1"` **no es 6,1 entradas**: son 6 entradas y 1 out, o sea **19 outs**. La
notación parece decimal y no lo es. Tratarla como un float mete un error pequeño
en cada ratio por entrada, y pequeño es justo lo que lo hace difícil de ver.
`parse_innings_pitched` lo resuelve y tiene su propia clase de tests.

---

## 4. El cruce entre las dos fuentes

El archivo de odds trae apodos y fechas; la MLB Stats API trae ids y
`officialDate`. El cruce va por **(fecha, equipos, marcador)**.

El marcador no está de adorno: **341 partidos son dobles jornadas**, dos partidos
el mismo día entre los mismos equipos. Sin él son indistinguibles y el cruce
tendría que elegir al azar, ensuciando el 2,7% de la muestra con emparejamientos
inventados.

```
cruce de abridores: 25493/25560 (99,7%)
  sin abridor 25, ambiguos 6, sin pareja 36
```

Por debajo del 90% de cruce el experimento **aborta**: comparar modelos sobre
submuestras distintas del histórico no compara nada.

---

## 5. La formulación: mercado + correcciones

```
logit(P) = b0 + b1·logit(mercado) + b2·elo_diff + b3·ventaja_abridor
```

Es mejor formulación que "modelo contra mercado" porque no obliga a elegir. Un
modelo que solo puede **sustituir** al mercado tiene que ser mejor que él en
todo; uno que lo **corrige** solo tiene que aportar en el margen. Barrera mucho
más baja — y aun así hay que superarla.

Si una corrección no aporta nada, su coeficiente sale ~0 y el modelo colapsa al
mercado. No hay que creerse nada: el propio ajuste lo dice.

**Una ausencia nunca se rellena con un cero.** Un 0 en `starter_advantage`
significa "los dos abridores son igual de buenos", que no es lo mismo que "no sé
quién lanza". Sin las tres features la estrategia cae al mercado.

---

## 6. El resultado

### Los coeficientes (train 2011-2018, n=18.147)

| ajuste | market_logit | elo_diff | **starter_advantage** |
|---|---|---|---|
| con mercado | +1,0141 | −0,0000 | **+0,0166** |
| sin mercado | — | +0,0049 | **+0,2545** |

**Factor 15.** Y `market_logit` sale en +1,0141: con el mercado dentro, el modelo
literalmente reproduce el mercado.

Este contraste es el corazón del experimento. Un coeficiente ~0 **con** el
mercado admite dos lecturas opuestas —que la feature no vale nada, o que el
mercado ya la contiene— y son conclusiones contrarias: una dice tirar la feature,
la otra dice que la feature es buena y hay que buscar *dónde* el mercado tarda en
incorporarla. Ajustar sin el mercado las separa.

### El rendimiento

| | n | Brier | vs mercado | cerca del cierre | apuestas | ROI |
|---|---|---|---|---|---|---|
| **TRAIN 2011-2018** (en muestra) | | | | | | |
| market_consensus v1 | 19.697 | 0,24234 | +0,00000 | 0,0000 | 0 | — |
| market_plus (con abridor) | 19.697 | 0,24233 | **+0,00001** | 0,4648 | 0 | — |
| market_plus (sin mercado) | 19.697 | 0,24384 | −0,00151 | 0,3052 | 6.175 | −4,10% |
| **HOLDOUT 2019-2021** | | | | | | |
| market_consensus v1 | 5.854 | 0,23853 | +0,00000 | 0,0000 | 0 | — |
| market_plus (con abridor) | 5.854 | 0,23850 | **+0,00003** | 0,4636 | 0 | — |
| market_plus (sin mercado) | 5.854 | 0,23988 | −0,00135 | 0,2424 | 2.206 | +2,04% |

Lo notable del `+0,00001`: eso es **en muestra**, con los coeficientes ajustados
sobre esos mismos partidos. Incluso permitiéndole ajustarse a los datos con los
que se le evalúa, añadir el abridor al mercado mejora el Brier en la **quinta
cifra decimal**. En holdout, la tercera.

---

## 7. El `+2,04%` de ROI, y por qué no es nada

Es el único número positivo de toda la tabla, y sobre 2.206 apuestas. Merece que
se le mire de cerca — y al mirarlo aparecen **cinco** razones independientes para
descartarlo:

1. **El signo se da la vuelta entre train y holdout.** La misma estrategia dio
   −4,10% sobre 6.175 apuestas en train. Un cambio de signo entre conjuntos es la
   firma clásica del ruido.
2. **No es significativo.** t = −0,93 sobre el retorno medio. Consistente con
   cero.
3. **El retorno medio por apuesta es −2,33%**, de signo contrario al ROI. Ver
   abajo.
4. **Bate al cierre solo el 40,9% de las veces**, con CLV medio de −0,58%.
   Sistemáticamente toma precios peores que el de cierre, que es la firma de una
   estrategia perdedora.
5. **Su Brier es peor que el del mercado** (−0,00135) y queda más cerca del
   cierre solo el 24,2% de las veces. Es, medido, un modelo peor.

### El hallazgo que salió de investigarlo

El punto 3 destapó un hueco en el propio informe. **ROI y retorno medio son
cantidades distintas**: el ROI pondera por stake (`Σbeneficio / Σapostado`) y el
retorno medio no (`media(beneficio/stake)`). Con Kelly, donde cada apuesta lleva
un tamaño distinto, pueden discrepar **hasta en el signo** — y aquí lo hacen.

Significa que el agregado positivo lo deciden unas pocas apuestas grandes que
acertaron, mientras la apuesta típica perdía. No es un error de cálculo, pero sí
es la diferencia entre *"esta estrategia gana"* y *"esta estrategia acertó donde
apostó fuerte"*, que no es lo mismo y no se repite igual.

El informe ahora reporta las dos cifras y **avisa cuando discrepan en el signo**.
Reportar solo el ROI habría contado la historia al revés.

---

## 8. Qué significa esto para el proyecto

**El edge de modelo en el moneyline de MLB está, a efectos prácticos, cerrado.**
No por falta de datos: con el factor dominante del deporte medido correctamente y
validado contra la historia, la mejora sobre el precio de apertura es de la
quinta cifra decimal. El mercado incorpora el abridor antes de abrir la línea.

Eso **no** significa que no haya nada que hacer. Significa que el sitio donde
buscar cambia:

- **El edge estructural** —diferencias de precio entre casas para el mismo
  evento— es lo único que este histórico no puede medir, porque es de consenso y
  no identifica la casa. Es lo que la captura horaria lleva acumulando desde
  Phase 2a, y ahora es la única vía abierta.
- **Mercados menos eficientes** que el moneyline de MLB: props, ligas menores,
  deportes con menos volumen. El moneyline de un partido de MLB es de los
  mercados más líquidos y mejor precificados que existen, y esta fase lo ha
  medido en vez de suponerlo.

Lo que **no** se va a hacer es añadir features al modelo de moneyline de MLB
esperando otro resultado. Bullpen, parque y alineación son mejoras marginales
sobre el factor dominante, y el factor dominante ya no aporta nada.

---

## 9. Uso

```bash
sportstar pitchers            # descarga y cachea (ya está commiteado)
sportstar backtest-pitchers   # el experimento sobre train
sportstar backtest-pitchers --test   # además el holdout. Queda anotado.
```
