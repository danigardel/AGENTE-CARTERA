# Radar bursátil diario

Genera `dashboard.html` con noticias del día, una señal de sentimiento para cada empresa y datos de cotización, ratios PEG y PER, consenso de analistas y precio objetivo de Yahoo Finance cuando están disponibles. Incluye además la cartera configurada en `PORTFOLIO`, con cierres históricos, rendimientos por periodo y rendimiento máximo seleccionables. No requiere API keys.

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

La lista de seguimiento está en `COMPANIES` y la cartera fija en `PORTFOLIO`, ambas en `daily_report.py`. La cartera usa símbolos negociados en euros cuando están disponibles (EUNL.DE para iShares Core MSCI World, ASML.AS y XDWT.DE); los activos estadounidenses se convierten desde USD con el cambio histórico USD/EUR de cada fecha. Los periodos comparan el último cierre disponible en o antes de hoy menos 1, 7, 30, 180 y 365 días naturales. El rendimiento MAX usa las posiciones e inversiones base configuradas, sin depender de las fechas de compra. Se normaliza la entrada duplicada de Palantir a una sola tarjeta. SpaceX se incluye en noticias, pero no tiene un ticker público configurado, por lo que no se muestran cotización ni consenso para ella. Neo Performance Materials utiliza `NEO.TO`; Alphabet clase A, `GOOGL`.

## Automatización y GitHub Pages

El workflow `.github/workflows/daily_report.yml` genera y despliega el dashboard cada **5 minutos** (el intervalo mínimo de GitHub Actions para `schedule`). GitHub puede retrasar o descartar ejecuciones programadas cuando hay carga, así que no se garantiza una actualización exacta cada 5 minutos. Si una ejecución sigue en curso, no se cancela para iniciar otra; se procesa la más reciente cuando queda disponible. El cron se ejecuta en UTC, pero la hora que aparece en el informe usa `Europe/Madrid`. También se puede iniciar manualmente con `workflow_dispatch`. Cada tarjeta muestra el último precio que Yahoo Finance devuelve durante esa generación, convertido a euros cuando hay tipo de cambio disponible; el proveedor puede retrasar los datos o no cambiar el precio cuando el mercado está cerrado. Las ejecuciones programadas deben estar habilitadas y el workflow debe estar en la rama predeterminada del repositorio.

En el repositorio, activa GitHub Pages con **GitHub Actions** como fuente de despliegue. El workflow necesita los permisos de Pages y OIDC que declara en el YAML. Tras una ejecución correcta, el dashboard se publica como `index.html`.

## Fuentes y límites

- Google News RSS proporciona titulares; solo se conservan los publicados en la fecha local del informe. Google Translate muestra sus traducciones al español en los titulares clave y en las tarjetas; si la traducción no está disponible, se conserva el titular original y se indica el aviso.
- VADER puntúa el tono del titular original. La señal alcista/neutral/bajista es una heurística de sentimiento, no una predicción ni asesoramiento.
- Yahoo Finance vía `yfinance` aporta precio actual, variación diaria, ratios PEG y PER (TTM), consenso y precio objetivo cuando el proveedor los entrega. El PEG se muestra en verde (< 1,0), naranja (1,0–1,5) y rojo (> 1,5), y sin color si falta o es negativo; ambos ratios son referencias informativas, no recomendaciones de inversión. El precio mostrado es la cotización disponible al generar ese informe; los datos pueden faltar o tener retraso según el proveedor/mercado. Los errores y datos ausentes se indican en cada tarjeta.
- Se resume el consenso por empresa. La cartera propia es una lista estática definida por el usuario; no se consultan ni infieren las tenencias internas de fondos a partir de recomendaciones.
- El desplegable de la lista de seguimiento reordena las tarjetas en el navegador (rendimiento diario, PEG, consenso o sentimiento); las empresas sin dato van siempre al final.
- En la tabla de cartera, los selectores de periodo y métrica cambian entre cierres/rendimientos de 1D, 1W, 1M, 6M y 1Y, o ganancia/rendimiento MAX; las columnas de activo, posición, inversión base, precio actual y valor actual permanecen.
