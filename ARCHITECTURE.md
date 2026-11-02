# Estado de arquitectura

Última actualización: 3 de noviembre de 2026.

## Componentes y flujo de datos

- `daily_report.py` es el backend principal. Descarga datos públicos de Yahoo
  Finance con `yfinance`, calcula cotizaciones, métricas, noticias y vistas de
  cartera, y genera el dashboard estático original (`index.html`) y
  `market_data.json`.
- El dashboard Python es el producto principal: conserva los mapas de calor,
  las gráficas D3, los sparklines, el radar macro, el VIX, el buscador y la
  navegación por secciones adaptable a ordenador e iPhone. `dashboard.html` es
  una plantilla/prototipo auxiliar; React bajo `react-portfolio/` es opcional y
  consume el JSON.
- `portfolio_activity.json` es un extracto público filtrado de operaciones,
  dividendos, Saveback y beneficios de valores. No es el extracto bancario
  original. La cartera se reconstruye desde esas operaciones, aplicando coste
  FIFO, y se comprueba frente a `PORTFOLIO`.
- `agent.py` ejecuta por separado el análisis de cartera y las alertas. Usa la
  cadena de proveedores configurada en `ai_config.json`, conserva el último
  análisis válido y puede enviarlo por Telegram. Las claves se configuran como
  secretos/variables locales; no se guardan en los datos públicos.
- Los workflows de GitHub Actions publican el dashboard en Pages y ejecutan el
  agente por separado. La ejecución del agente puede tardar; al persistir un
  análisis nuevo se programa una generación posterior del dashboard.

## Datos de mercado y símbolos

- Las cotizaciones se solicitan primero al símbolo europeo configurado en
  `Company.ticker`; `Company.primary_ticker` conserva el símbolo matriz para
  noticias, fundamentales y fallback de precio. La cotización europea de hoy
  se conserva cuando está disponible; solo se prefiere una vela matriz de hoy
  si tiene una marca de tiempo posterior.
- Los símbolos `.DE`, `.F` y `.SG` se tratan como cotizaciones europeas. Yahoo
  puede retrasar o no ofrecer datos; no se garantiza que replique el precio,
  spread u horario de Trade Republic.
- Los precios, cierres anteriores y objetivos se normalizan a EUR. Para una
  cotización no EUR, el precio actual usa el FX más reciente y el cierre
  anterior usa el último cierre FX diario previo, de modo que la variación
  diaria en euros incluye el cambio de divisa.
- Distancias a máximos de 52 semanas y medias móviles y potencial de analistas
  se recalculan respecto al precio actual en EUR, convirtiendo a la divisa
  original de cada referencia fundamental cuando sea necesario.
- MercadoLibre se presenta de forma consistente como `MLB1.SG` (EUR), con
  `MELI` como ticker matriz. Ambos identificadores permanecen excluidos del
  análisis IA de posiciones, junto con los ETF excluidos configurados en
  `AI_EXCLUDED_PORTFOLIO_TICKERS`.
- Solo `MLB1.SG` de los símbolos `.SG` adicionales revisados (12D.SG, MWV.SG,
  1BE.SG, DLT.SG, MLB1.SG y NOVC.SG) devolvió datos intradía válidos en la
  comprobación de Yahoo realizada el 2 de noviembre de 2026.

## Análisis, alertas y métricas

- El dashboard periódico se genera sin llamar a la IA. El agente independiente
  crea análisis en hitos horarios, mantiene el análisis válido anterior ante
  fallos de proveedor y envía el análisis nuevo a Telegram solo tras generarse
  correctamente.
- El prompt del análisis cubre contexto macro, noticias y comportamiento de
  posiciones incluidas, decisiones mantener/comprar/vender, riesgos y
  candidatas de compra. `EUNL.DE`, `XDWT.DE`, MercadoLibre y otros símbolos
  excluidos no deben reaparecer como posiciones analizadas.
- El escáner «buy the dip» puntúa señales deterministas (drawdown de 52 semanas,
  RSI(14), SMA200 y potencial de analistas, PEG y proximidad de resultados); la
  IA puede explicar la señal, no sustituye la puntuación.
- Las métricas cuantitativas de riesgo (volatilidad, drawdown, Sharpe/Sortino,
  beta/correlación, exposiciones, look-through y stress tests) se calculan con
  históricos públicos para aportar contexto al análisis; no son predicciones.
  Las secciones de riesgo cuantitativo y próxima aportación no se muestran en
  el dashboard.
- Las alertas de movimiento y anomalía son deterministas, con deduplicación y
  estado persistido en la rama `data`. Los secretos de Telegram no se publican.

## Decisiones y límites que deben conservarse

- Mantener el dashboard Python y sus mapas de calor como superficie principal;
  no sustituirlos por el prototipo React.
- No modificar ni leer archivos locales de credenciales para tareas de código,
  ni incluir claves en commits, prompts, JSON o artefactos publicados.
- No hacer commit o push sin solicitud explícita. Si se solicita publicar,
  revisar el estado remoto y confirmar qué cambios locales están incluidos.
- No alterar el historial público de operaciones salvo que la tarea lo pida;
  sus importes y tickers alimentan la reconstrucción FIFO y los gráficos.
- Los datos Yahoo son públicos y pueden estar retrasados, incompletos o diferir
  de la plaza de negociación real. La conversión FX no elimina spreads ni
  diferencias entre proveedores.

## Validación reciente

La suite específica del informe se ejecuta con:

```powershell
python -m unittest discover -s tests -p test_daily_report.py
```

En la validación del 3 de noviembre de 2026, con MercadoLibre ya alineado a
`MLB1.SG`, pasaron las 133 pruebas. Repetir la suite tras cambios posteriores
antes de publicar.
