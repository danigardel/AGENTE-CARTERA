# Radar bursátil diario

Genera `dashboard.html` con noticias del día, una señal de sentimiento para cada empresa y datos de cotización, ratio PEG, consenso de analistas y precio objetivo de Yahoo Finance cuando están disponibles. No requiere API keys.

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

La lista está en `COMPANIES` en `daily_report.py`. Se normaliza la entrada duplicada de Palantir a una sola tarjeta. SpaceX se incluye en noticias, pero no tiene un ticker público configurado, por lo que no se muestran cotización ni consenso para ella. Neo Performance Materials utiliza `NEO.TO`; Alphabet clase A, `GOOGL`.

## Automatización y GitHub Pages

El workflow `.github/workflows/daily_report.yml` genera y despliega el dashboard cada **5 minutos** (el intervalo mínimo de GitHub Actions para `schedule`). GitHub puede retrasar o descartar ejecuciones programadas cuando hay carga, así que no se garantiza una actualización exacta cada 5 minutos. Si una ejecución sigue en curso, no se cancela para iniciar otra; se procesa la más reciente cuando queda disponible. El cron se ejecuta en UTC, pero la hora que aparece en el informe usa `Europe/Madrid`. También se puede iniciar manualmente con `workflow_dispatch`. Cada tarjeta muestra el último precio que Yahoo Finance devuelve durante esa generación, convertido a euros cuando hay tipo de cambio disponible; el proveedor puede retrasar los datos o no cambiar el precio cuando el mercado está cerrado. Las ejecuciones programadas deben estar habilitadas y el workflow debe estar en la rama predeterminada del repositorio.

En el repositorio, activa GitHub Pages con **GitHub Actions** como fuente de despliegue. El workflow necesita los permisos de Pages y OIDC que declara en el YAML. Tras una ejecución correcta, el dashboard se publica como `index.html`.

## Fuentes y límites

- Google News RSS proporciona titulares; solo se conservan los publicados en la fecha local del informe.
- VADER puntúa el tono de los titulares en inglés. La señal alcista/neutral/bajista es una heurística de sentimiento, no una predicción ni asesoramiento.
- Yahoo Finance vía `yfinance` aporta precio actual, variación diaria, ratio PEG, consenso y precio objetivo cuando el proveedor los entrega. El PEG se muestra cerca de 1 en verde y por encima de 2 en rojo; es una referencia informativa, no una recomendación de inversión. El precio mostrado es la cotización disponible al generar ese informe; los datos pueden faltar o tener retraso según el proveedor/mercado. Los errores y datos ausentes se indican en cada tarjeta.
- Se resume el consenso por empresa. No se consultan tenencias ni carteras de fondos; esos datos no se infieren a partir de recomendaciones.
