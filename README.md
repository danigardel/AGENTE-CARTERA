# Radar bursátil diario

El backend Python obtiene cotizaciones, titulares y fundamentales de Yahoo Finance, calcula métricas de cartera y puede generar un análisis con Gemini. La vista publicada es una aplicación estática React/TypeScript: Python exporta el contrato `market_data.json` y React lo carga al abrirse. Se conservan el screener, las noticias, los eventos, los gráficos y la cartera con cierres históricos. La cotización y las noticias no requieren claves; el análisis IA es opcional y necesita una clave Gemini.

El reporte también solicita un análisis estructurado a Gemini 3.8 Flash (`gemini-3.8-flash`) usando la API estándar `generateContent` (no Batch), con `gemini-3.7-flash` como alternativa. Envía a Google las posiciones de `PORTFOLIO`, los datos de `COMPANIES`, titulares/resúmenes de los RSS de Yahoo Finance y un radar global con hasta 250 titulares recientes de Yahoo Finance, CNBC, WSJ, Bloomberg e Investing.com España. El radar global conserva titulares y resúmenes tal como se publican, incluye fuente/fecha/enlace y se inyecta en `global_market_radar` para que Gemini detecte oportunidades externas. Las peticiones usan el payload estándar de generación, sin Google Search Grounding. Si se omite un feed, el informe continúa; si no se obtiene ninguna noticia de hoy, el prompt pide un análisis técnico basado en los datos disponibles. La petición usa un presupuesto de razonamiento de 2048 tokens. No se envían claves de API dentro del prompt.
El análisis se guarda en `gemini_cache.json` con un timestamp UTC y se reutiliza hasta el siguiente hito horario de análisis. `GEMINI_API_KEY` admite varias claves separadas por comas. Cada consulta empieza con la clave siguiente a la que resolvió el análisis anterior; los fallos rotan secuencialmente el pool. Si todas las claves fallan en el modelo principal con HTTP 429 o 503, se repite la rotación desde el mismo índice con `gemini-3.7-flash`; también se prueba ese modelo si el principal no existe. Solo tras agotar también el modelo alternativo se conserva el aviso: hasta el siguiente hito (`TARGET_HOURS`) si el error final identifica explícitamente una cuota diaria, o cinco minutos para 503, límites por minuto/tokens y errores 429 sin detalle diario claro. Importante: las cuotas de Gemini se aplican por proyecto, no por clave API; rotar varias claves asociadas al mismo proyecto no suma cuota. Google puede responder 429 por RPM, TPM o RPD (solicitudes/minuto, tokens/minuto o solicitudes/día). El aviso de cuota recomienda comprobar en AI Studio el proyecto realmente asociado a cada clave y los límites del modelo. La caché negativa guarda únicamente la huella SHA-256 y el índice del conjunto ordenado de claves (nunca las claves); al cambiar el conjunto se reinicia la rotación. Los archivos de caché se excluyen de Git. GitHub Actions conserva únicamente la caché de Gemini.

## Ejecución local

Requiere Python 3.10 o posterior.

```powershell
python -m pip install -r requirements.txt
python daily_report.py
```

El archivo se crea en `dashboard.html`. Se puede elegir otra ruta:

```powershell
python daily_report.py --output public\index.html
```

Para generar solo el JSON que consume React:

```powershell
New-Item -ItemType Directory -Force react-portfolio\public | Out-Null
python daily_report.py --no-html --data-output react-portfolio\public\market_data.json
```

Para probar la aplicación React localmente, genera primero el JSON anterior y
ejecuta:

```powershell
npm ci --prefix react-portfolio
npm --prefix react-portfolio run dev
```

La carga inicial muestra un estado de espera; si falta el JSON o no cumple su
versión de esquema, la página ofrece un error visible y permite reintentar.
Para compilar la salida estática:

```powershell
npm --prefix react-portfolio run build:dashboard
```

Vite escribe el sitio en `public\`; la URL de los recursos es relativa para que
funcione tanto en el dominio de proyecto de GitHub Pages como bajo una ruta.
La salida generada no se versiona.

### Importar operaciones de cartera (opcional)

Se puede sustituir la cartera fija por un CSV local de operaciones, sin iniciar sesión ni conectar con el broker:

```powershell
python daily_report.py --portfolio-csv trades.csv
```

También se admite la variable `PORTFOLIO_TRANSACTIONS_CSV`. El CSV debe incluir `date,ticker,name,side,quantity,price_eur,currency`; `fees_eur` es opcional y por defecto vale cero. `date` usa `AAAA-MM-DD`, `side` es `BUY` o `SELL`, `price_eur` y `fees_eur` se expresan en euros, y `currency` indica la divisa de cotización (`EUR` o `USD`). Una operación de compra de ejemplo sería `2026-01-15,ASML.AS,ASML,BUY,0.5,700,EUR,1.00`. Las ventas reducen el coste de la posición usando coste medio; no se calculan impuestos ni ganancias realizadas. El CSV reemplaza, no complementa, la lista estática `PORTFOLIO`. Si falta, la app usa las posiciones configuradas en el código.

Con un CSV válido se añade una curva histórica estimada, calculada con las operaciones y cierres diarios públicos de Yahoo Finance; excluye efectivo, dividendos cobrados, impuestos y otros activos no incluidos en el archivo. La disponibilidad histórica y la conversión USD/EUR dependen de Yahoo Finance.

El archivo `portfolio_activity.json` contiene el extracto de inversión filtrado que se muestra públicamente en la sección «Mi cartera». Solo incluye compras, ventas, dividendos, Saveback y beneficios de valores; excluye el efectivo y los datos de cuenta, contraparte, referencias y descripciones del extracto. El dashboard muestra un resumen y un historial desplegable con fechas, ticker, participaciones e importes; dividendos y beneficios no se suman como nuevas participaciones. Este archivo se publica junto con el dashboard, por lo que sus importes e historial son públicos. No subas el extracto bancario original. Se carga automáticamente al generar el reporte; se puede sustituir con `python daily_report.py --portfolio-activity portfolio_activity.json`. `--portfolio-csv` conserva el formato simplificado documentado arriba y tiene prioridad si se especifica.

La lista de seguimiento está en `COMPANIES` y la cartera fija en `PORTFOLIO`, ambas en `daily_report.py`; ahora incluye 57 empresas e instrumentos. Los tickers se comprobaron contra los ISIN del extracto: las nueve posiciones que siguen abiertas coinciden con `PORTFOLIO`; NKE (Nike), NVO (Novo Nordisk ADR) y CSH2.PA (Smart Overnight Return) aparecen en movimientos históricos, pero sus participaciones se vendieron por completo. Space Innovators de VanEck se consulta con el ticker `JEDI` (ISIN `IE000YU9K6K2`). SpaceX está incluida con el ticker `SPCX` (NASDAQ). La cartera usa símbolos negociados en euros cuando están disponibles (EUNL.DE para iShares Core MSCI World, ASML.AS y XDWT.DE); los activos estadounidenses se convierten desde USD con el cambio histórico USD/EUR de cada fecha. Los periodos comparan el último cierre disponible en o antes de hoy menos 1, 7, 30, 180 y 365 días naturales. El rendimiento MAX usa las posiciones e inversiones base configuradas, sin depender de las fechas de compra.

## Automatización y GitHub Pages

El workflow `.github/workflows/daily_report.yml` instala Python y Node, ejecuta las pruebas React, exporta `react-portfolio\public\market_data.json`, compila el frontend en `public\` y despliega esa carpeta a GitHub Pages cada **5 minutos** (el intervalo mínimo de GitHub Actions para `schedule`). GitHub puede retrasar o descartar ejecuciones programadas cuando hay carga, así que no se garantiza una actualización exacta cada 5 minutos. Si una ejecución sigue en curso, no se cancela para iniciar otra; se procesa la más reciente cuando queda disponible. El cron se ejecuta en UTC, pero la hora que aparece en el informe usa `Europe/Madrid`. También se puede iniciar manualmente con `workflow_dispatch`. Cada tarjeta muestra el último precio que Yahoo Finance devuelve durante esa generación, convertido a euros cuando hay tipo de cambio disponible; el proveedor puede retrasar los datos o no cambiar el precio cuando el mercado está cerrado. Las ejecuciones programadas deben estar habilitadas y el workflow debe estar en la rama predeterminada del repositorio.

El archivo `market_data.json` publicado es accesible públicamente, como el resto
del sitio. Incluye las posiciones y el historial de inversión que ya se
mostraban en el dashboard; no contiene claves de API ni el extracto bancario
original.

Para activar el análisis IA, crea una o varias claves Gemini en Google AI Studio y guárdalas en un único secreto de repositorio **Settings → Secrets and variables → Actions → New repository secret → `GEMINI_API_KEY`**, separadas por comas y sin espacios obligatorios (por ejemplo `clave1,clave2,clave3`). Localmente, configura `GEMINI_API_KEY` con el mismo formato antes de ejecutar el informe. Si falta una clave o Gemini no responde, el informe se genera igualmente y muestra un aviso en la sección de análisis. El modelo predeterminado es `gemini-3.8-flash`; puede cambiarse con `GEMINI_MODEL`, y cuando el modelo elegido devuelve un error de modelo inexistente se prueba `gemini-3.7-flash`. El presupuesto de razonamiento está fijado en 2048 tokens. `INVESTOR_PROFILE` permite personalizar el perfil; este valor se envía a Gemini, así que no incluyas información sensible. En GitHub Actions, los ajustes opcionales se leen de **Actions variables**. Con respuestas válidas, el análisis se renueva en los hitos de las 00:00, 09:00, 12:00, 15:00, 18:00 y 21:00 en `Europe/Madrid`, no en cada generación del dashboard. La clave inicial rota de una consulta a la siguiente, y los fallos prueban secuencialmente el resto del pool; solo cuando todas fallan se publica el aviso hasta el próximo hito. Los feeds macro RSS son públicos y no requieren claves; las peticiones a Gemini siguen sujetas a cuotas y posibles costes del proyecto.

En el repositorio, activa GitHub Pages con **GitHub Actions** como fuente de despliegue. El workflow necesita los permisos de Pages y OIDC que declara en el YAML. Tras una ejecución correcta, el dashboard se publica como `index.html`.

El dashboard incluye mapas de calor, curvas intradía y sparklines por activo. Los gráficos de la lista de seguimiento muestran la sesión del día actual (cierre de mercado previo hasta la última cotización disponible), no una ventana móvil de 24 horas; las tarjetas sin cotización de hoy ocultan el sparkline. La cartera pondera la variación intradía de cada posición por su valor de mercado y la compara con los ETF S&P 500 EUR (`SXR8.DE`) y MSCI World EUR (`EUNL.DE`). El selector de periodo cambia también la curva comparativa a 1D, 1W, 1M, 6M o 1Y. Las curvas históricas revalorizan las posiciones actuales con cierres pasados (no reconstruyen entradas/salidas anteriores); los activos USD se convierten a EUR con el USD/EUR histórico. Si faltan valores actuales de posiciones, la serie intradía ponderada usa pesos iguales para las posiciones con datos. El mapa de cartera asigna a cada posición un área proporcional a su porcentaje del valor actual total. Una cotización solo se considera actualizada hoy si Yahoo Finance proporciona la fecha de mercado y esta coincide con la fecha local de su bolsa; cotizaciones antiguas o sin fecha/datos se ordenan debajo de una línea discontinua en la lista, sus sparklines se ocultan y sus bloques de heatmap conservan el color `var(--bg)`. Los mapas utilizan etiquetas cortas para los nombres largos; el texto se centra, se recorta dentro de cada bloque y se omite si no cabe completo, sin partir palabras. Sus SVG se redimensionan al ancho disponible. El dashboard reorganiza tarjetas, controles, tablas y gráficos según el ancho de pantalla, incluidos móviles alrededor de 390 px y ventanas estrechas por zoom. Los históricos intradía se descargan de Yahoo Finance en intervalos de 5 minutos al generar cada página. En móviles la tabla de cartera se presenta como tarjetas compactas; los datos de inversión base y valor actual solo aparecen en el periodo MAX. D3.js se carga desde CDN para generar los SVG; la página es un único HTML generado, aunque las gráficas requieren conexión para cargar D3.

## Fuentes y límites

- Google News RSS proporciona titulares del dashboard y Yahoo Finance RSS aporta titulares/resúmenes para el análisis por empresa; el radar macro agrega hasta 250 titulares de hoy de Yahoo Finance, CNBC, WSJ, Bloomberg e Investing.com España, sin traducirlos. En todos los casos solo se conservan los publicados en la fecha local del informe. Google Translate traduce secuencialmente los titulares al español en las tarjetas, con una pausa de 0,2 segundos entre solicitudes para reducir respuestas HTTP 429.
- VADER puntúa el tono del titular original. La señal alcista/neutral/bajista es una heurística de sentimiento, no una predicción ni asesoramiento.
- Yahoo Finance vía `yfinance` aporta precio actual, variación diaria, ratios PEG y PER (TTM), consenso, precio objetivo, sector, capitalización, beta, rentabilidad por dividendo, distancia al máximo de 52 semanas, fecha de resultados y fecha ex-dividendo cuando están disponibles. El screener filtra localmente por texto de nombre/ticker, sector, PEG, PER, capitalización, dividendo, beta, variación diaria, consenso y retroceso desde máximos; el contador se actualiza al instante y los datos stale permanecen separados al final. No efectúa consultas adicionales al cambiar filtros. La agenda muestra fechas futuras disponibles en una ventana de 45 días. El PEG se muestra en verde (< 1,0), naranja (1,0–1,5) y rojo (> 1,5), y sin color si falta o es negativo; los ratios son referencias informativas, no recomendaciones de inversión. Los datos pueden faltar o tener retraso.
- Gemini Flash recibe la cartera, las empresas de `COMPANIES` y el nodo `global_market_radar`; el contexto sectorial solo se incluye para empresas con sector devuelto por Yahoo Finance. La respuesta se valida y se descartan recomendaciones con tickers que no están en la lista de seguimiento o en la cartera correspondiente. El análisis puede equivocarse, no conoce el horizonte ni la tolerancia al riesgo del usuario y no es asesoramiento financiero personalizado.
- Se resume el consenso por empresa. La cartera usa `PORTFOLIO` o, si se indica, un CSV local de operaciones del usuario; no se consulta la cuenta del broker ni se infieren las tenencias internas de fondos.
- El screener permite filtrar por búsqueda instantánea de nombre/ticker, sector, PEG, PER, capitalización, dividendo, beta, variación diaria, consenso y retroceso de 52 semanas; también puede ordenar por rendimiento diario, PEG, PER, capitalización, dividendo, beta, consenso o sentimiento. Se ejecuta en el navegador sobre los datos ya incluidos en el informe.
- La interfaz publica `window.DashboardMarketData.applySnapshot([...])` y `window.DashboardMarketData.applyDelta([...])` para integrar en el futuro un proveedor autorizado del lado cliente. Cada actualización acepta `ticker`, `priceEur`, `previousCloseEur`, `marketDate` (`AAAA-MM-DD`), `marketTimezone` y, opcionalmente, `timestamp` ISO 8601. El controlador calcula el cambio porcentual, actualiza tarjetas, tabla, heatmaps, sparklines y curvas 1D, y vuelve a aplicar la separación stale/fresh. Es solo una interfaz de integración: no abre conexiones, no consulta Trade Republic y no convierte por sí sola el HTML estático en streaming en tiempo real.
- La importación CSV no inicia sesión ni consulta endpoints privados de Trade Republic. La reconstrucción histórica es una estimación del valor de las posiciones abiertas según las operaciones suministradas y los cierres disponibles; no calcula rentabilidad ponderada por flujos, efectivo, impuestos, dividendos ni acciones corporativas.
- En la tabla de cartera, los selectores cambian entre rendimientos 1D, 1W, 1M, 6M y 1Y, precios de cierre históricos en euros, o ganancia/rendimiento MAX. Activo, posición, inversión base, precio actual y valor actual permanecen siempre visibles. El gráfico comparativo de cartera sincroniza su periodo con el selector, junto con S&P 500 y MSCI World.
- Cada tarjeta de seguimiento incluye un sparkline SVG sin ejes ni etiquetas, usando los cierres intradía de 5 minutos limitados a la fecha local de la sesión bursátil; no representa una ventana móvil de 24 horas y se oculta cuando la cotización no pertenece a la sesión actual.
- La sección de análisis IA indica la fecha/hora local de su último resultado válido, que puede ser anterior a la actualización de cotizaciones por el periodo de caché de Gemini.
