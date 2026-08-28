# Captura programada del mercado

## Por qué existe

Con una sola jornada de datos medí 28 observaciones de edge estructural, cero por
encima del umbral del 2%. La cota superior al 95% de la frecuencia real es
**10.7%**: no se puede distinguir "nunca" de "el 5% de las veces" — y el 5%
serían unas 250 apuestas por temporada.

Esa pregunta no se responde razonando, se responde acumulando jornadas.

Además, **el closing line es la única medición del sistema cuya ventana no
vuelve**. Cada día sin capturar es CLV perdido para siempre, y el CLV es entre 8
y 10 veces más eficiente en muestra que el P&L.

## Cómo funciona

 ejecuta  cada hora entre las 16:00 y
las 04:00 UTC y commitea el snapshot. Trece capturas al día.

Cada snapshot es un fichero nuevo con marca de tiempo. La secuencia de un día
**es** el movimiento de línea; el último fichero antes del primer lanzamiento
**es** el closing line.

## Lo que hay que hacer una sola vez

### 1. Añadir la API key como secreto del repositorio

1. **github.com/dpineyro99/sportstar** → **Settings** → **Secrets and variables**
   → **Actions**
2. **New repository secret**
3. Nombre: 
4. Valor: la key de the-odds-api.com

Sin el secreto, el workflow sigue capturando el calendario pero omite las odds.

### 2. Llevar el workflow a la rama por defecto

Los eventos  de GitHub **solo disparan en la rama por defecto**. El
workflow tiene que estar en  para que la captura arranque: mientras viva
solo en la rama de trabajo, no se ejecutará sola.

Se puede probar antes de fusionar con **Actions** → **Captura de mercado** →
**Run workflow**, que sí funciona en cualquier rama.

## Cuota

Coste medido leyendo  de una respuesta real: **1 crédito
por captura** con  y .

- 13 capturas/día x 30 días = **390 créditos al mes**
- Plan gratuito: 500/mes

Queda margen para ejecuciones manuales. Si algún día se añaden totals y spreads,
el coste se multiplica por el número de mercados y de regiones — ahí habría que
recalcular la frecuencia o pasar a un plan de pago.

## Qué mirar a los pocos días



Y para ver cuántas observaciones se han acumulado, los ficheros bajo
. Con unas seis jornadas se puede empezar a descartar una
frecuencia de oportunidades del 2%; con dos semanas, estimarla de verdad.
