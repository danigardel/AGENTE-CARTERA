# Frontend React del radar

Este paquete compila la vista estática del radar bursátil. El backend genera
`public/market_data.json` con cotizaciones, watchlist, cartera, titulares y
análisis de Gemini; el frontend valida el contrato y lo carga al montarse. Los
datos de producción no usan fixtures.

El contrato de datos tiene `schema_version: 1`, se valida antes de representarse
y agrupa `market_summary`, `watchlist`, `portfolio`, `analysis`, `market_events`,
`portfolio_activity` y las series históricas de `charts`. Las modificaciones
incompatibles del contrato deben incrementar la versión y actualizar el
validador del cliente.

## Ejecutar

```powershell
npm ci
npm run dev
```

En desarrollo, genera primero el archivo servido por Vite desde la raíz del
repositorio:

```powershell
python daily_report.py --no-html --data-output react-portfolio\public\market_data.json
```

Para validar el tipado y generar el bundle de producción:

```powershell
npm run build
```

`npm run build:dashboard` usa el mismo build y escribe los archivos estáticos en
`..\public`, sin borrar el JSON generado en el paquete. El workflow de GitHub
Actions ejecuta primero el backend y después este build, y publica `public`.

Las pruebas de las fórmulas se ejecutan con el test runner integrado de Node:

```powershell
npm test
```

## Modelo y cálculos

`Holding` recoge ticker y nombre, clase de activo, divisa original, unidades,
coste medio, precio y valor de posición, cambios diarios, variación del
período, resultado realizado, capital invertido y metadatos de la cotización.
Los importes de cálculo están en euros; `currency` conserva la divisa del
activo.

Las fórmulas por posición reflejan las observadas en el chunk analizado:

- Coste base: `shares * avgCost`.
- Resultado no realizado: `positionValue - costeBase`; su porcentaje se calcula
  sobre el coste base si este es positivo.
- Porcentaje mostrado para el período: `changePct` o, si falta, la variación no
  realizada.
- Peso: `positionValue / valorTotalCartera * 100`.
- Retorno total: `(realizedPnlEur + resultadoNoRealizado) /
  investedCostEur * 100`, si hay resultado realizado de al menos 0,005 EUR,
  capital invertido positivo y precio actual disponible.

La selección de periodo usa cierres históricos del backend. Para las
transacciones disponibles, Python calcula ganancias realizadas por venta con
coste medio: el precio neto de comisiones menos el coste proporcional de las
participaciones vendidas. Dividendos e impuestos no forman parte de ese cálculo
y permanecen en el historial de movimientos. La rentabilidad total no se
muestra si falta información necesaria; las posiciones sin precio no se
presentan como valor cero. El control de privacidad oculta unidades, costes,
valores, pesos y resultados en pantalla, pero no es control de acceso ni
protección de datos en memoria.

Python genera también las curvas de cartera y benchmarks; React presenta la
serie del periodo seleccionado y, cuando existe, el historial desde las
transacciones importadas.

La evidencia disponible no permite una reconstrucción literal: el informe de
captura filtró atributos, textos, accesibilidad, scripts y red; el análisis
estático de código se limitó a un chunk del portfolio. La vista aplica
comportamientos genéricos confirmados en ese chunk —coste, rendimiento,
ponderación, cambio de periodo, privacidad y filtrado/ordenación— con diseño
original y datos del backend propio. No reproduce las clases ni todo el código
de la web analizada.
