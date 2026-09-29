# Radar bursátil diario

Genera `dashboard.html` con noticias del día, una señal de sentimiento para cada empresa y datos de cotización/consenso de analistas de Yahoo Finance cuando están disponibles. No requiere API keys.

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

El workflow `.github/workflows/daily_report.yml` genera y despliega el dashboard todos los días a las **21:30 y 22:45, hora de Europe/Madrid**. GitHub Actions interpreta cron en UTC; el workflow tiene cuatro disparadores UTC y filtra los dos que corresponden a la hora local según horario de verano/invierno. También se puede iniciar con `workflow_dispatch`.

En el repositorio, activa GitHub Pages con **GitHub Actions** como fuente de despliegue. El workflow necesita los permisos de Pages y OIDC que declara en el YAML. Tras una ejecución correcta, el dashboard se publica como `index.html`.

## Fuentes y límites

- Google News RSS proporciona titulares; solo se conservan los publicados en la fecha local del informe.
- VADER puntúa el tono de los titulares en inglés. La señal alcista/neutral/bajista es una heurística de sentimiento, no una predicción ni asesoramiento.
- Yahoo Finance vía `yfinance` aporta variación diaria, consenso y precio objetivo cuando el proveedor los entrega. Los errores y datos ausentes se indican en cada tarjeta.
- Se resume el consenso por empresa. No se consultan tenencias ni carteras de fondos; esos datos no se infieren a partir de recomendaciones.
