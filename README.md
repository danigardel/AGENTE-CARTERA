# Radar bursátil diario

Genera `dashboard.html` con noticias del día, una señal de sentimiento para cada empresa y datos de cotización, ratios PEG y PER, consenso de analistas y precio objetivo de Yahoo Finance cuando están disponibles. Incluye además la cartera configurada en `PORTFOLIO`, con cierres históricos, rendimientos por periodo y rendimiento máximo seleccionables. La cotización y las noticias no requieren claves; el análisis IA es opcional y necesita una clave Gemini.

El reporte también solicita un análisis estructurado a Gemini Flash (`gemini-3.8-flash`) usando la API estándar `generateContent` (no Batch). Envía a Google las posiciones de `PORTFOLIO` y los datos de `COMPANIES` para redactar una evaluación de cartera, diversificación, cambios posibles, candidatas de compra y posibles ventas. No se envían claves de API dentro del prompt.
El análisis se guarda en `gemini_cache.json` con un timestamp UTC y se reutiliza hasta el siguiente hito horario de análisis. Si Gemini falla, también se persiste el error y el momento del próximo intento: se respeta el `Retry-After` o la espera indicada por Google y, si no se proporciona, se aplica una pausa (3 horas para HTTP 429 y 15 minutos para otros errores). Así se evita volver a consumir cuota en cada ejecución de cinco minutos. El archivo se excluye de Git. GitHub Actions restaura la caché persistente con `actions/cache/restore` y guarda una clave nueva por cada timestamp de intento; así se respeta que las entradas de Actions Cache sean inmutables.

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

La lista actualizada está en `COMPANIES` y la cartera fija en `PORTFOLIO`, ambas en `daily_report.py`. Los 50 elementos solicitados contienen dos entradas del ETF MSCI World Information Technology; el generador lo muestra una sola vez, con 49 instrumentos únicos. La cartera usa símbolos negociados en euros cuando están disponibles (EUNL.DE para iShares Core MSCI World, ASML.AS y XDWT.DE); los activos estadounidenses se convierten desde USD con el cambio histórico USD/EUR de cada fecha. Los periodos comparan el último cierre disponible en o antes de hoy menos 1, 7, 30, 180 y 365 días naturales. El rendimiento MAX usa las posiciones e inversiones base configuradas, sin depender de las fechas de compra. SpaceX se incluye en noticias pero no tiene ticker público porque no cotiza en bolsa; sus valores bursátiles aparecen como no disponibles. Neo Performance Materials utiliza `NEO.TO`; Alphabet clase A, `GOOGL`.

## Automatización y GitHub Pages

El workflow `.github/workflows/daily_report.yml` genera y despliega el dashboard cada **5 minutos** (el intervalo mínimo de GitHub Actions para `schedule`). GitHub puede retrasar o descartar ejecuciones programadas cuando hay carga, así que no se garantiza una actualización exacta cada 5 minutos. Si una ejecución sigue en curso, no se cancela para iniciar otra; se procesa la más reciente cuando queda disponible. El cron se ejecuta en UTC, pero la hora que aparece en el informe usa `Europe/Madrid`. También se puede iniciar manualmente con `workflow_dispatch`. Cada tarjeta muestra el último precio que Yahoo Finance devuelve durante esa generación, convertido a euros cuando hay tipo de cambio disponible; el proveedor puede retrasar los datos o no cambiar el precio cuando el mercado está cerrado. Las ejecuciones programadas deben estar habilitadas y el workflow debe estar en la rama predeterminada del repositorio.

Para activar el análisis IA, crea una clave Gemini en Google AI Studio y guárdala en el repositorio como **Settings → Secrets and variables → Actions → New repository secret → `GEMINI_API_KEY`**. Localmente, configura la variable de entorno `GEMINI_API_KEY` antes de ejecutar el informe. Si falta la clave o Gemini no responde, el informe se genera igualmente y muestra un aviso en la sección de análisis. El modelo puede configurarse con `GEMINI_MODEL`; por defecto se usa `gemini-3.8-flash`. Con respuestas válidas, el análisis se renueva en los hitos de las 00:00, 09:00, 12:00, 15:00, 18:00 y 21:00 en `Europe/Madrid`, no en cada generación del dashboard. Tras un error, solo se reintenta cuando vence la pausa persistida. Las peticiones siguen sujetas a cuotas y posibles costes del proyecto de Google.

En el repositorio, activa GitHub Pages con **GitHub Actions** como fuente de despliegue. El workflow necesita los permisos de Pages y OIDC que declara en el YAML. Tras una ejecución correcta, el dashboard se publica como `index.html`.

El dashboard incluye un mapa de calor de bloques de igual tamaño para la lista de seguimiento y la cartera, y líneas intradía del rendimiento medio de cada grupo. Los históricos intradía se descargan de Yahoo Finance en intervalos de 5 minutos al generar cada página; el cambio diario de cada bloque se calcula con los cierres previos de Yahoo. En móviles la tabla de cartera se presenta como tarjetas compactas; los datos de inversión base y valor actual solo aparecen en el periodo MAX. D3.js se carga desde CDN para generar los SVG; la página es un único HTML generado, aunque las gráficas requieren conexión para cargar D3.

## Fuentes y límites

- Google News RSS proporciona titulares; solo se conservan los publicados en la fecha local del informe. Google Translate muestra sus traducciones al español en los titulares clave y en las tarjetas; si la traducción no está disponible, se conserva el titular original y se indica el aviso.
- VADER puntúa el tono del titular original. La señal alcista/neutral/bajista es una heurística de sentimiento, no una predicción ni asesoramiento.
- Yahoo Finance vía `yfinance` aporta precio actual, variación diaria, ratios PEG y PER (TTM), consenso, precio objetivo y sector cuando el proveedor los entrega. El PEG se muestra en verde (< 1,0), naranja (1,0–1,5) y rojo (> 1,5), y sin color si falta o es negativo; ambos ratios son referencias informativas, no recomendaciones de inversión. El precio mostrado es la cotización disponible al generar ese informe; los datos pueden faltar o tener retraso según el proveedor/mercado. Los errores y datos ausentes se indican en cada tarjeta.
- Gemini Flash recibe la cartera y todas las empresas de `COMPANIES` (la lista actual puede contener más o menos de 50); el contexto sectorial solo se incluye para empresas con sector devuelto por Yahoo Finance. La respuesta se valida y se descartan recomendaciones con tickers que no están en la lista de seguimiento o en la cartera correspondiente. El análisis puede equivocarse, no conoce el horizonte ni la tolerancia al riesgo del usuario y no es asesoramiento financiero personalizado.
- Se resume el consenso por empresa. La cartera propia es una lista estática definida por el usuario; no se consultan ni infieren las tenencias internas de fondos a partir de recomendaciones.
- El desplegable de la lista de seguimiento reordena las tarjetas en el navegador (rendimiento diario, PEG, consenso o sentimiento); las empresas sin dato van siempre al final.
- En la tabla de cartera, los selectores cambian entre rendimientos 1D, 1W, 1M, 6M y 1Y, o ganancia/rendimiento MAX. Activo, posición y precio actual permanecen visibles; inversión base y valor actual aparecen solo en MAX.
- La sección de análisis IA indica la fecha/hora local de su último resultado válido, que puede ser anterior a la actualización de cotizaciones por el periodo de caché de Gemini.
