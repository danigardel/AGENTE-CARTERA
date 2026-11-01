# Radar bursátil diario

El backend Python obtiene cotizaciones, titulares y fundamentales de Yahoo Finance, calcula métricas de cartera y genera el dashboard original sin depender de IA. Un agente independiente analiza la cartera y gestiona alertas; React sigue siendo un prototipo opcional. La página publicada conserva los mapas de calor, el screener, las noticias y los sparklines, y exporta `market_data.json`. La cotización y las noticias no requieren claves.

El análisis usa la cadena configurable Gemini principal → Gemini alternativo → Gemini Flash Lite → Groq → OpenRouter Free. Cerebras y Mistral están definidos, pero deshabilitados. El agente envía al proveedor activo los datos de cartera, watchlist y noticias necesarios; no incluye claves en el prompt. Los proveedores OpenAI-compatibles usan `POST /chat/completions`, validación estricta del JSON y un perfil `reduced` (máximo inicial de 25 noticias totales, 40 titulares macro, 15 empresas de watchlist y resúmenes de 200 caracteres). Gemini usa `full`. Si un prompt se rechaza por tamaño, se reduce una vez y luego se prueba el siguiente proveedor. El último análisis válido se cachea; fallos no reemplazan esa respuesta. Las RSS macro son contexto recuperado, no grounding ni verificación independiente.

`collect_market_radar()` deduplica títulos normalizados y admite hasta 400 titulares únicos; `MAX_GEMINI_NEWS_ITEMS` permite hasta 200 noticias de empresa y `MAX_HEADLINES` hasta 8 titulares por empresa. Las fuentes macro incluyen Yahoo Finance, CNBC, MarketWatch, Seeking Alpha, WSJ, Bloomberg e Investing.com España. Los límites reales de prompt, cuotas y disponibilidad dependen del proveedor y del plan.

### Proveedores IA y cuotas consultadas

Valores consultados el **01/11/2026** en documentación oficial. Los límites de una cuenta pueden variar; los valores `max_prompt_chars` del proyecto son topes conservadores de caracteres, no cuotas oficiales.

| Proveedor | Modelo configurado | Contexto documentado | Límites / estado |
|---|---|---:|---|
| Gemini | IDs en `ai_config.json` | 1.048.576 tokens de entrada para Gemini 3.8 Flash | Proveedor principal. Cuotas del proyecto y modelos sujetos a disponibilidad. |
| Groq | `openai/gpt-oss-20b` | 131.072 tokens | La tabla oficial publica para Developer base 30 RPM, 8K TPM y 1K RPD; no son límites garantizados del nivel gratuito. Límites Free exactos: **sin verificar**, varían por organización. |
| OpenRouter | `openrouter/free` | Variable según el modelo que el router elija | Sin contexto ni cuota universal. Selección aleatoria, disponibilidad y límites variables; `model_used` registra el modelo real de la respuesta. |
| Cerebras | `qwen-3.8-27b` | 64K tokens en Free Trial | Free Trial: 5 RPM, 30K tokens no cacheados/min, 90K tokens totales/min, 1M tokens/hora y 1M/día. Requiere método de pago verificado y créditos promocionales; no es un nivel gratuito permanente. Deshabilitado. |
| Mistral | `mistral-small-2603` (candidato) | **Sin verificar** | ID, acceso gratuito y límites RPM/TPM/RPD: **sin verificar** en la documentación oficial consultada. Deshabilitado hasta verificar. |

Fuentes oficiales: [Groq modelos](https://console.groq.com/docs/models), [Groq límites](https://console.groq.com/docs/rate-limits), [Cerebras catálogo](https://inference-docs.cerebras.ai/models/overview), [Cerebras Qwen 3.8 27B](https://inference-docs.cerebras.ai/models/qwen-3.8-27b.md), [Cerebras límites](https://inference-docs.cerebras.ai/support/rate-limits.md), [OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-models-router) y [Mistral modelos](https://docs.mistral.ai/models/). GitHub Models fue retirado el 30/07/2026; no es una opción.

### Configuración y privacidad

`ai_config.json` contiene el orden de proveedores, endpoints, modelos predeterminados, topes de caracteres, perfiles `full`/`reduced`, límites publicados y notas de verificación. No guardes secretos allí. `AI_CONFIG_PATH` selecciona otro archivo y `AI_PROVIDERS` permite ordenar o limitar la cadena con una lista CSV (por ejemplo, `gemini,groq,openrouter`). Para desactivar un proveedor, establece `"enabled": false` en su entrada de `providers`; Cerebras y Mistral están así por defecto. Los modelos y límites pueden sustituirse mediante las variables de entorno indicadas en el archivo. Los topes de 18.000–20.000 caracteres para respaldos equivalen a unos 4.500–5.000 tokens con la estimación conservadora caracteres/4; se aplican además de los límites de contexto y cuotas del proveedor.

En Actions, crea los secretos `GEMINI_API_KEY`, `GROQ_API_KEY` y `OPENROUTER_API_KEY`; `CEREBRAS_API_KEY`, `MISTRAL_API_KEY` y las credenciales de Telegram son opcionales. Usa las variables `AI_PROVIDERS`, `OPENROUTER_MODEL` y `ALERT_DROP_PCT` para configuración no secreta. Las claves admiten listas CSV cuando el proveedor las soporta. Un proveedor sin clave se omite. No definas `OPENROUTER_ALLOW_PAID` en Actions.

`openrouter/free` elige aleatoriamente un modelo gratuito disponible compatible con la petición; el modelo elegido puede variar entre reintentos y tener un contexto menor que el router. Para evitar costes, OpenRouter rechaza cualquier modelo salvo `openrouter/free` o un ID que termine en `:free`, excepto cuando se habilita explícitamente `OPENROUTER_ALLOW_PAID=true` en un entorno local controlado. Los proveedores de respaldo reciben datos de la cartera y noticias. Revisa sus condiciones de uso y la configuración de privacidad de OpenRouter para modelos gratuitos antes de activarlos; no incluyas datos personales en `INVESTOR_PROFILE`.

El dashboard se genera cada cinco minutos con `--no-ai` y `AI_ENABLED=false`; no llama a proveedores IA. El workflow independiente `portfolio_agent.yml` se ejecuta cada 30 minutos, hace el triaje de eventos, genera análisis en los hitos configurados y envía alertas deterministas de caídas y subidas de cartera, además de subidas de la watchlist, aunque falle la cadena IA. El análisis incluye siempre un resumen cuantitativo determinista; si no hay narrativa válida, muestra «Narrativa IA no disponible; mostrando datos verificados». El último análisis válido y el estado del agente se guardan en la rama `data`, que es pública: no contiene claves, pero sí análisis y metadatos de tickers/alertas. El dashboard lee ese estado y marca visualmente si la narrativa está desactualizada.

`ai_providers.py` clasifica errores, aplica backoff exponencial con jitter y respeta `Retry-After`. Un cortacircuitos por proveedor/modelo conserva solo fallos y hora de reapertura, nunca claves ni prompts; para OpenRouter usa la clave estable `openrouter/free`. La caché de Gemini almacena solo la huella SHA-256 del pool de claves, nunca las claves. `previous_analysis_memory` conserva contexto temporal validado. Si el prompt supera el límite, se recortan primero titulares macro, luego noticias y watchlist, sin eliminar cartera ni memoria.

## Ejecución local

Requiere Python 3.10 o posterior.

```powershell
python -m pip install -r requirements.txt
python daily_report.py
```

El archivo se crea en `dashboard.html`. Para generar sin consultar IA:

```powershell
python daily_report.py --no-ai
```

El agente autónomo puede ejecutarse manualmente con `python agent.py`. Se puede
elegir otra ruta de salida del dashboard:

```powershell
python daily_report.py --output public\index.html
```

Para generar la página original y el JSON de datos en la carpeta publicada:

```powershell
New-Item -ItemType Directory -Force public | Out-Null
python daily_report.py --output public\index.html --data-output public\market_data.json
```

Para trabajar con el prototipo opcional de React, genera el JSON en su carpeta:

```powershell
New-Item -ItemType Directory -Force react-portfolio\public | Out-Null
python daily_report.py --no-ai --no-html --data-output react-portfolio\public\market_data.json
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

React se conserva como un prototipo independiente y no reemplaza la página
publicada por GitHub Pages. Su build de Vite escribe en `public\`; la URL de los
recursos es relativa para funcionar tanto en el dominio de proyecto de GitHub
Pages como bajo una ruta. La salida generada no se versiona.

### Agente interactivo en consola

Ejecuta `python agente_interactivo.py` desde el repositorio para abrir el agente
de cartera. Lee `GEMINI_API_KEY` del entorno o, si no está definida, las claves
separadas por comas de `GEMINI_API_KEY.txt` (archivo local ignorado por Git).
Al iniciar carga la actividad de cartera y la memoria del análisis, y actualiza
precios y niveles ATR. `/cartera` muestra peso, P&L y nivel ATR; `/vivo` vuelve a
descargar precios y niveles; `/radar` actualiza los titulares macro; `/salir`
termina la sesión. Cualquier otra línea se envía a Gemini junto con el contexto
actual y un historial acotado a 20 turnos. Las consultas de chat envían a Google
el contexto de cartera cargado en memoria; no se envían automáticamente.

### Importar operaciones de cartera (opcional)

Se puede sustituir la cartera fija por un CSV local de operaciones, sin iniciar sesión ni conectar con el broker:

```powershell
python daily_report.py --portfolio-csv trades.csv
```

También se admite la variable `PORTFOLIO_TRANSACTIONS_CSV`. El CSV debe incluir `date,ticker,name,side,quantity,price_eur,currency`; `fees_eur` es opcional y por defecto vale cero. `date` usa `AAAA-MM-DD`, `side` es `BUY` o `SELL`, `price_eur` y `fees_eur` se expresan en euros, y `currency` indica la divisa de cotización (`EUR` o `USD`). Una operación de compra de ejemplo sería `2026-01-15,ASML.AS,ASML,BUY,0.5,700,EUR,1.00`. Las ventas reducen el coste de la posición usando coste medio. El CSV reemplaza, no complementa, la lista estática `PORTFOLIO`. Si falta, la app usa las posiciones configuradas en el código.

Con operaciones se añade un gráfico de beneficio neto estimado, calculado con el coste de adquisición, los precios de venta, las comisiones registradas y los cierres diarios públicos de Yahoo Finance. Cuando se usa `portfolio_activity.json`, también suma dividendos y Saveback; no suma impuestos ni efectivo. El gráfico marca las compras y ventas con el precio registrado por acción y permite filtrar 1D, 1W, 1M, 6M, YTD, 1Y y el historial total. La comparación con S&P 500 y MSCI World sigue el periodo elegido. La gráfica intradía superior muestra la variación estimada de la cartera en euros. La disponibilidad histórica y la conversión USD/EUR dependen de Yahoo Finance.

El archivo `portfolio_activity.json` contiene el extracto de inversión filtrado que se muestra públicamente en la sección «Mi cartera». Solo incluye compras, ventas, dividendos, Saveback y beneficios de valores; excluye el efectivo y los datos de cuenta, contraparte, referencias y descripciones del extracto. El dashboard muestra un resumen y un historial desplegable con fechas, ticker, participaciones e importes; dividendos y beneficios no se suman como nuevas participaciones. Este archivo se publica junto con el dashboard, por lo que sus importes e historial son públicos. No subas el extracto bancario original. Se carga automáticamente al generar el reporte; se puede sustituir con `python daily_report.py --portfolio-activity portfolio_activity.json`. `--portfolio-csv` conserva el formato simplificado documentado arriba y tiene prioridad si se especifica.

La lista de seguimiento está en `COMPANIES` y la cartera fija en `PORTFOLIO`, ambas en `daily_report.py`; ahora incluye 57 empresas e instrumentos. Los tickers se comprobaron contra los ISIN del extracto: las nueve posiciones que siguen abiertas coinciden con `PORTFOLIO`; NKE (Nike), NVO (Novo Nordisk ADR) y CSH2.PA (Smart Overnight Return) aparecen en movimientos históricos, pero sus participaciones se vendieron por completo. Space Innovators de VanEck se consulta con el ticker `JEDI` (ISIN `IE000YU9K6K2`). SpaceX está incluida con el ticker `SPCX` (NASDAQ). La cartera usa símbolos negociados en euros cuando están disponibles (EUNL.DE para iShares Core MSCI World, ASML.AS y XDWT.DE); los activos estadounidenses se convierten desde USD con el cambio histórico USD/EUR de cada fecha. Los periodos comparan el último cierre disponible en o antes de hoy menos 1, 7, 30, 180 y 365 días naturales. El rendimiento MAX usa las posiciones e inversiones base configuradas, sin depender de las fechas de compra.

## Automatización y GitHub Pages

El workflow `.github/workflows/daily_report.yml` instala Python, ejecuta la suite de pruebas y genera `public\index.html` con el dashboard original y `public\market_data.json` con los datos estructurados. Despliega esa carpeta a GitHub Pages cada **5 minutos** (el intervalo mínimo de GitHub Actions para `schedule`). GitHub puede retrasar o descartar ejecuciones programadas cuando hay carga, así que no se garantiza una actualización exacta cada 5 minutos. Si una ejecución sigue en curso, no se cancela para iniciar otra; se procesa la más reciente cuando queda disponible. El cron se ejecuta en UTC, pero la hora que aparece en el informe usa `Europe/Madrid`. También se puede iniciar manualmente con `workflow_dispatch`. Cada tarjeta muestra el último precio que Yahoo Finance devuelve durante esa generación, convertido a euros cuando hay tipo de cambio disponible; el proveedor puede retrasar los datos o no cambiar el precio cuando el mercado está cerrado. Las ejecuciones programadas deben estar habilitadas y el workflow debe estar en la rama predeterminada del repositorio.

El archivo `market_data.json` publicado es accesible públicamente, como el resto
del sitio. Incluye las posiciones y el historial de inversión que ya se
mostraban en el dashboard; no contiene claves de API ni el extracto bancario
original.

Consulta «Configuración y privacidad» para crear los secretos y elegir proveedores. El agente usa el perfil de `INVESTOR_PROFILE` y, con respuestas válidas, actualiza el análisis en los hitos de las 00:00, 09:00, 12:00, 15:00, 18:00 y 21:00 de `Europe/Madrid`, no en cada generación del dashboard. Los feeds macro RSS son públicos y no requieren claves.

El agente, cada 30 minutos, fuerza un análisis fuera de horario cuando una
posición activa cruza el nivel secundario desde máximos o varía al menos un 4 %
en el día. Cada ticker activa como máximo un análisis de anomalía por fecha
local. Por separado, una caída diaria igual o superior al 2 % genera un aviso
determinista de Telegram ante movimientos de al menos `ALERT_DROP_PCT`: caídas
y subidas de posiciones de cartera, y solo subidas para acciones de la lista
de seguimiento que no estén en cartera. Ignora cotizaciones stale, agrupa los
avisos por categoría, deduplica por ticker/fecha y reavisa cuando el movimiento
se intensifica al menos 1 punto porcentual en la misma dirección. El estado se
conserva en `agent_state.json` dentro de la rama
`data`. Configura `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` como secretos de
Actions; los valores no se escriben en la caché ni en `market_data.json`.
`agent.py` desactiva las alertas narrativas de Gemini para evitar duplicados.

En el repositorio, activa GitHub Pages con **GitHub Actions** como fuente de despliegue. El workflow necesita los permisos de Pages y OIDC que declara en el YAML. Tras una ejecución correcta, el dashboard se publica como `index.html`.

El dashboard incluye mapas de calor, curvas intradía y sparklines por activo. Los gráficos de la lista de seguimiento muestran la sesión del día actual (cierre de mercado previo hasta la última cotización disponible), no una ventana móvil de 24 horas; las tarjetas sin cotización de hoy ocultan el sparkline. La cartera pondera la variación intradía de cada posición por su valor de mercado y la compara con los ETF S&P 500 EUR (`SXR8.DE`) y MSCI World EUR (`EUNL.DE`). El selector de periodo del gráfico de beneficio sincroniza la comparación con el mismo intervalo. Para las carteras importadas, el historial se reconstruye con las operaciones registradas y el coste medio; si no hay operaciones, se mantiene la comparación de periodos basada en las posiciones actuales. Los activos USD se convierten a EUR con el USD/EUR histórico. Si faltan valores actuales de posiciones, la serie intradía ponderada usa pesos iguales para las posiciones con datos. El mapa de cartera asigna a cada posición un área proporcional a su porcentaje del valor actual total. Una cotización solo se considera actualizada hoy si Yahoo Finance proporciona la fecha de mercado y esta coincide con la fecha local de su bolsa; cotizaciones antiguas o sin fecha/datos se ordenan debajo de una línea discontinua en la lista, sus sparklines se ocultan y sus bloques de heatmap conservan el color `var(--bg)`. Los mapas utilizan etiquetas cortas para los nombres largos; el texto se centra, se recorta dentro de cada bloque y se oculta cuando no hay espacio suficiente.ada bloque y se omite si no cabe completo, sin partir palabras. Sus SVG se redimensionan al ancho disponible. El dashboard reorganiza tarjetas, controles, tablas y gráficos según el ancho de pantalla, incluidos móviles alrededor de 390 px y ventanas estrechas por zoom. Los históricos intradía se descargan de Yahoo Finance en intervalos de 5 minutos al generar cada página. En móviles la tabla de cartera se presenta como tarjetas compactas; los datos de inversión base y valor actual solo aparecen en el periodo MAX. D3.js se carga desde CDN para generar los SVG; la página es un único HTML generado, aunque las gráficas requieren conexión para cargar D3.

## Fuentes y límites

- El dashboard Python se organiza en tres vistas con navegación superior: **Mercado** reúne los heatmaps, el gráfico diario de cartera, el resumen de sentimiento, el VIX y el rendimiento sectorial; **Cartera** agrupa gráficos, posiciones y análisis cuantitativo/IA; **Seguimiento** reúne la agenda desplegable, titulares clave y el screener. La navegación adapta sus controles a móviles y escritorio; los gráficos D3 y las funciones existentes se conservan.
- Google News RSS proporciona titulares del dashboard y Yahoo Finance RSS aporta titulares/resúmenes para el análisis por empresa (hasta 8 por ticker y 200 en total); el radar macro agrega hasta 400 titulares únicos de hoy de Yahoo Finance, CNBC, MarketWatch, Seeking Alpha, WSJ, Bloomberg e Investing.com España, sin traducirlos. El índice general RSS de Yahoo se acompaña con un feed ticker-scoped de mercados por si el índice no está disponible. En todos los casos solo se conservan los publicados en la fecha local del informe. Google Translate traduce secuencialmente los titulares al español en las tarjetas, con una pausa de 0,2 segundos entre solicitudes para reducir respuestas HTTP 429.
- VADER puntúa el tono del titular original. La señal alcista/neutral/bajista es una heurística de sentimiento, no una predicción ni asesoramiento.
- Yahoo Finance vía `yfinance` obtiene también el cierre más reciente de `^VIX` y su variación diaria. El indicador usa verde por debajo de 15, naranja entre 15 y 25 y rojo por encima de 25; si Yahoo no devuelve datos, muestra valores no disponibles sin interrumpir la generación. `market_data.json` expone el dato opcional como `macro_indicators.vix`.
- Yahoo Finance vía `yfinance` aporta precio actual, variación diaria, ratios PEG y PER (TTM), consenso, precio objetivo, sector, capitalización, beta, rentabilidad por dividendo, distancia al máximo de 52 semanas, fecha de resultados y fecha ex-dividendo cuando están disponibles. El screener filtra localmente por texto de nombre/ticker, sector, PEG, PER, capitalización, dividendo, beta, variación diaria, consenso y retroceso desde máximos; el contador se actualiza al instante y los datos stale permanecen separados al final. No efectúa consultas adicionales al cambiar filtros. La agenda muestra fechas futuras disponibles en una ventana de 45 días. El PEG se muestra en verde (< 1,0), naranja (1,0–1,5) y rojo (> 1,5), y sin color si falta o es negativo; los ratios son referencias informativas, no recomendaciones de inversión. Los datos pueden faltar o tener retraso.
- El proveedor de IA activo recibe la cartera, las empresas de `COMPANIES` y el nodo `global_market_radar`; el contexto sectorial solo se incluye para empresas con sector devuelto por Yahoo Finance. La respuesta se valida y se descartan recomendaciones con tickers que no están en la lista de seguimiento o en la cartera correspondiente. El análisis puede equivocarse, no conoce el horizonte ni la tolerancia al riesgo del usuario y no es asesoramiento financiero personalizado.
- Se resume el consenso por empresa. La cartera usa `PORTFOLIO` o, si se indica, un CSV local de operaciones del usuario; no se consulta la cuenta del broker ni se infieren las tenencias internas de fondos.
- El screener permite filtrar por búsqueda instantánea de nombre/ticker, sector, PEG, PER, capitalización, dividendo, beta, variación diaria, consenso y retroceso de 52 semanas; también puede ordenar por rendimiento diario, PEG, PER, capitalización, dividendo, beta, consenso o sentimiento. Se ejecuta en el navegador sobre los datos ya incluidos en el informe.
- La interfaz publica `window.DashboardMarketData.applySnapshot([...])` y `window.DashboardMarketData.applyDelta([...])` para integrar en el futuro un proveedor autorizado del lado cliente. Cada actualización acepta `ticker`, `priceEur`, `previousCloseEur`, `marketDate` (`AAAA-MM-DD`), `marketTimezone` y, opcionalmente, `timestamp` ISO 8601. El controlador calcula el cambio porcentual, actualiza tarjetas, tabla, heatmaps, sparklines y curvas 1D, y vuelve a aplicar la separación stale/fresh. Es solo una interfaz de integración: no abre conexiones, no consulta Trade Republic y no convierte por sí sola el HTML estático en streaming en tiempo real.
- La importación CSV no inicia sesión ni consulta endpoints privados de Trade Republic. La reconstrucción histórica es una estimación del valor de las posiciones abiertas según las operaciones suministradas y los cierres disponibles; no calcula rentabilidad ponderada por flujos, efectivo, impuestos, dividendos ni acciones corporativas.
- En la tabla de cartera, los selectores cambian entre rendimientos 1D, 1W, 1M, 6M y 1Y, precios de cierre históricos en euros, o ganancia/rendimiento MAX. Activo, posición, inversión base, precio actual y valor actual permanecen siempre visibles. El gráfico comparativo de cartera sincroniza su periodo con el selector, junto con S&P 500 y MSCI World.
- En la celda de cada activo de «Mi cartera», el botón de escudo abre un panel accesible con el precio actual y medio, dos niveles ATR, volatilidad y explicación personalizada. Se reutiliza el histórico diario que ya carga la cartera, sin llamadas adicionales a Yahoo Finance. El ATR(14) es la media simple de 14 rangos verdaderos. El campo `atr_pct` guarda el ATR dividido por el último cierre y expresado en puntos porcentuales (por ejemplo, `2` representa `2 %`). Los niveles principales parten siempre del precio actual en euros: revisión = `precio_eur × (1 − 2.5 × atr_pct / 100)` y toma de beneficios = `precio_eur × (1 + 2 × 2.5 × atr_pct / 100)`, con variaciones respectivas de −2.5 × `atr_pct` y +5 × `atr_pct`. El ATR porcentual se calcula en la divisa nativa y luego se aplica al precio en euros, por lo que la conversión de divisa no altera la volatilidad relativa. Como referencia secundaria se conserva el nivel desde máximos: `(máximo de las últimas 22 sesiones / último cierre − 2.5 × atr_pct / 100) × precio_eur`. El bloque secundario y su mensaje se resaltan cuando el precio actual está en o por debajo de ese nivel; el escudo permanece neutro salvo cuando faltan datos, y el nivel puede bajar cuando un máximo antiguo sale de la ventana móvil. Con menos de 15 sesiones válidas, el panel marca «Sin datos» y no inventa niveles. La confianza es una etiqueta según las sesiones válidas del histórico usadas: «high» desde 100, «medium» desde 30 y «low» por debajo de 30; no es una probabilidad estadística. Son distancias basadas en volatilidad, no predicciones, stops automáticos ni órdenes de compra/venta; no alteran precios, rentabilidades ni totales. El campo opcional `risk_zones` de `market_data.json` es aditivo para mantener la compatibilidad con datos previos.
- Cada tarjeta de seguimiento incluye un sparkline SVG sin ejes ni etiquetas, usando los cierres intradía de 5 minutos limitados a la fecha local de la sesión bursátil; no representa una ventana móvil de 24 horas y se oculta cuando la cotización no pertenece a la sesión actual.
- La sección de análisis IA indica la fecha/hora local de su último resultado válido, que puede ser anterior a la actualización de cotizaciones por el periodo de caché.
- `market_data.json` añade de forma opcional `analysis.provider_used`, `analysis.is_stale` y `alerts_today`; React valida los datos antiguos y nuevos, muestra una insignia de análisis desactualizado y resume las alertas deterministas del día.
