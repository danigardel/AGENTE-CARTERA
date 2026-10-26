import { useMemo, useState } from "react";
import type {
  AssetClass,
  Holding,
  HoldingSortKey,
  HoldingView,
} from "./holdings";
import type { MarketData, PortfolioPeriod } from "./marketData";
import { calculatePortfolio } from "./holdings";
import "./portfolio.css";

const money = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const percent = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const units = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 4,
});

const classLabels: Record<AssetClass, string> = {
  stock: "Acciones",
  fund: "Fondos",
  cash: "Efectivo",
};

const periods: { value: PortfolioPeriod; label: string }[] = [
  { value: "1D", label: "1 día" },
  { value: "1W", label: "1 semana" },
  { value: "1M", label: "1 mes" },
  { value: "6M", label: "6 meses" },
  { value: "1Y", label: "1 año" },
  { value: "MAX", label: "Máximo" },
];

function isAssetFilter(value: string): value is AssetClass | "all" {
  return value === "all" || value === "stock" || value === "fund" || value === "cash";
}

function isPortfolioPeriod(value: string): value is PortfolioPeriod {
  return periods.some((option) => option.value === value);
}

function formatMoney(value: number | null): string {
  return value === null ? "—" : money.format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${percent.format(value)} %`;
}

function sortRows(
  rows: HoldingView[],
  key: HoldingSortKey,
  direction: "asc" | "desc",
): HoldingView[] {
  return [...rows].sort((left, right) => {
    let result = 0;
    if (key === "name") {
      result = left.name.localeCompare(right.name, "es");
    } else {
      const leftValue =
        key === "value"
          ? left.positionValue
          : key === "daily"
            ? left.dayDeltaEur
            : left.weightPct;
      const rightValue =
        key === "value"
          ? right.positionValue
          : key === "daily"
            ? right.dayDeltaEur
            : right.weightPct;
      if (leftValue === null || rightValue === null) {
        if (leftValue === rightValue) return 0;
        return leftValue === null ? 1 : -1;
      }
      result = leftValue - rightValue;
    }
    return direction === "asc" ? result : -result;
  });
}

function PrivateValue({
  hidden,
  children,
}: {
  hidden: boolean;
  children: string;
}) {
  return (
    <span data-private="true" aria-label={hidden ? "Valor oculto" : undefined}>
      {hidden ? "••••••" : children}
    </span>
  );
}

function Metric({
  label,
  value,
  privateValue = false,
  hidden = false,
  tone,
}: {
  label: string;
  value: string;
  privateValue?: boolean;
  hidden?: boolean;
  tone?: "positive" | "negative";
}) {
  return (
    <article className="portfolio-metric">
      <span>{label}</span>
      <strong className={tone ? `tone-${tone}` : undefined}>
        {privateValue ? (
          <PrivateValue hidden={hidden}>{value}</PrivateValue>
        ) : (
          value
        )}
      </strong>
    </article>
  );
}

function PerformanceChart({
  marketData,
  period,
}: {
  marketData: MarketData;
  period: PortfolioPeriod;
}) {
  const portfolioPoints =
    period === "MAX"
      ? (() => {
          const history = marketData.charts.portfolio_history_series;
          const base = history[0]?.value;
          return base !== undefined && base > 0
            ? history.map((point) => ({
                ...point,
                value: (point.value / base - 1) * 100,
              }))
            : [];
        })()
      : (marketData.charts.portfolio_period_series[period] ?? []);
  const benchmarks =
    period === "MAX"
      ? []
      : (marketData.charts.benchmark_period_series[period] ?? []);
  const series = [
    { name: "Cartera", points: portfolioPoints, className: "chart-portfolio" },
    ...benchmarks.map((benchmark, index) => ({
      name: benchmark.name,
      points: benchmark.series,
      className: `chart-benchmark chart-benchmark-${index % 3}`,
    })),
  ].filter((item) => item.points.length > 0);

  if (series.length === 0) {
    return (
      <section className="dashboard-panel performance-chart">
        <h2>Evolución de la cartera</h2>
        <p className="muted-copy">
          No hay suficientes cierres históricos para mostrar este periodo.
        </p>
      </section>
    );
  }

  const values = series.flatMap((item) => item.points.map((point) => point.value));
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  const range = maximum - minimum || 1;
  const chart = { width: 720, height: 190, left: 36, right: 12, top: 14, bottom: 26 };
  const xFor = (index: number, length: number) =>
    length < 2
      ? (chart.left + chart.width - chart.right) / 2
      : chart.left +
        (index / (length - 1)) * (chart.width - chart.left - chart.right);
  const yFor = (value: number) =>
    chart.top +
    ((maximum - value) / range) * (chart.height - chart.top - chart.bottom);
  const zeroY = yFor(0);

  return (
    <section className="dashboard-panel performance-chart" aria-labelledby="performance-chart-title">
      <div className="dashboard-section-heading">
        <div>
          <h2 id="performance-chart-title">Evolución de la cartera</h2>
          <p>Rentabilidad desde el inicio del periodo · comparación con índices</p>
        </div>
      </div>
      <div className="chart-scroll">
        <svg
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          role="img"
          aria-label={`Evolución de la cartera y benchmarks para ${period}`}
        >
          <line
            x1={chart.left}
            y1={zeroY}
            x2={chart.width - chart.right}
            y2={zeroY}
            className="chart-zero-line"
          />
          {series.map((item) => (
            <polyline
              key={item.name}
              className={item.className}
              points={item.points
                .map(
                  (point, index) =>
                    `${xFor(index, item.points.length)},${yFor(point.value)}`,
                )
                .join(" ")}
            />
          ))}
          <text x={chart.left} y={chart.height - 5} className="chart-axis-label">
            {new Date(series[0].points[0].timestamp).toLocaleDateString("es-ES")}
          </text>
          <text
            x={chart.width - chart.right}
            y={chart.height - 5}
            textAnchor="end"
            className="chart-axis-label"
          >
            {new Date(
              series[0].points[series[0].points.length - 1].timestamp,
            ).toLocaleDateString("es-ES")}
          </text>
        </svg>
      </div>
      <ul className="chart-legend">
        {series.map((item) => (
          <li key={item.name}>
            <span className={item.className} aria-hidden="true" />
            {item.name}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PortfolioView({
  marketData,
}: {
  marketData: MarketData;
}) {
  const [query, setQuery] = useState("");
  const [assetFilter, setAssetFilter] = useState<AssetClass | "all">("all");
  const [period, setPeriod] = useState<PortfolioPeriod>("1D");
  const [sortKey, setSortKey] = useState<HoldingSortKey>("value");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [hidePrivate, setHidePrivate] = useState(false);

  const holdings = useMemo<Holding[]>(
    () =>
      marketData.portfolio.positions.map((position) => ({
        ticker: position.ticker,
        name: position.name,
        assetClass: position.asset_class,
        currency: position.currency,
        shares: position.shares,
        avgCost: position.avg_cost_eur,
        currentPrice: position.current_price_eur,
        positionValue: position.current_value_eur,
        dayDeltaEur: position.day_delta_eur,
        dayDeltaPct: position.day_delta_pct,
        changePct:
          period === "MAX"
            ? position.max_return_pct
            : (position.returns_pct[period] ?? null),
        realizedPnlEur: position.realized_pnl_eur,
        investedCostEur: position.invested_cost_eur,
        quoteAsOf: position.price_date ?? undefined,
        quoteIsStale: position.quote_is_stale,
      })),
    [marketData.portfolio.positions, period],
  );

  const { rows } = useMemo(
    () => calculatePortfolio(holdings),
    [holdings],
  );
  const totals = marketData.portfolio.totals;

  const visibleRows = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("es");
    const filtered = rows.filter((holding) => {
      const matchesQuery =
        !normalizedQuery ||
        `${holding.name} ${holding.ticker}`
          .toLocaleLowerCase("es")
          .includes(normalizedQuery);
      return (
        matchesQuery &&
        (assetFilter === "all" || holding.assetClass === assetFilter)
      );
    });
    return sortRows(filtered, sortKey, sortDirection);
  }, [assetFilter, query, rows, sortDirection, sortKey]);

  const classTotals = useMemo(
    () =>
      (["stock", "fund", "cash"] as const).map((assetClass) => {
        const value = rows
          .filter((holding) => holding.assetClass === assetClass)
          .reduce((total, holding) => total + (holding.positionValue ?? 0), 0);
        return {
          assetClass,
          value,
          weightPct:
            totals.current_value_eur > 0
              ? (value / totals.current_value_eur) * 100
              : 0,
        };
      }),
    [rows, totals.current_value_eur],
  );

  function chooseSort(nextKey: HoldingSortKey) {
    if (sortKey === nextKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(nextKey);
      setSortDirection(nextKey === "name" ? "asc" : "desc");
    }
  }

  const toneFor = (value: number | null): "positive" | "negative" | undefined =>
    value === null || value === 0
      ? undefined
      : value > 0
        ? "positive"
        : "negative";

  return (
    <main className="portfolio-page">
      <header className="portfolio-header">
        <div>
          <p className="eyebrow">Agente Cartera · vista local</p>
          <h1>Mi cartera</h1>
          <p className="subheading">
            Generada {new Date(marketData.generated_at).toLocaleString("es-ES")}
            {" · "}Precios y valores de posición convertidos a EUR por Python.
          </p>
        </div>
        <button
          className="privacy-toggle"
          type="button"
          aria-pressed={hidePrivate}
          onClick={() => setHidePrivate((current) => !current)}
        >
          {hidePrivate ? "Mostrar importes" : "Ocultar importes"}
        </button>
      </header>

      <section className="portfolio-overview" aria-label="Totales de cartera">
        <Metric
          label="Valor actual"
          value={formatMoney(totals.current_value_eur)}
          privateValue
          hidden={hidePrivate}
        />
        <Metric
          label="Coste de las posiciones"
          value={formatMoney(totals.open_cost_basis_eur)}
          privateValue
          hidden={hidePrivate}
        />
        <Metric
          label="Resultado no realizado"
          value={formatMoney(totals.unrealized_pnl_eur)}
          privateValue
          hidden={hidePrivate}
          tone={toneFor(totals.unrealized_pnl_eur)}
        />
        <Metric
          label="Variación diaria"
          value={`${formatMoney(totals.daily_change_eur)} · ${formatPercent(totals.returns_pct["1D"] ?? null)}`}
          privateValue
          hidden={hidePrivate}
          tone={toneFor(totals.daily_change_eur)}
        />
        <Metric
          label="Resultado realizado"
          value={formatMoney(totals.realized_pnl_eur)}
          privateValue
          hidden={hidePrivate}
          tone={toneFor(totals.realized_pnl_eur)}
        />
        <Metric
          label="Rentabilidad total"
          value={formatPercent(totals.total_return_pct)}
          privateValue
          hidden={hidePrivate}
          tone={toneFor(totals.total_return_pct)}
        />
      </section>

      <section className="allocation" aria-label="Distribución por tipo">
        {classTotals.map(({ assetClass, value, weightPct }) => (
          <article className="allocation-card" key={assetClass}>
            <div className="allocation-title">
              <span>{classLabels[assetClass]}</span>
              <strong>
                <PrivateValue hidden={hidePrivate}>
                  {formatPercent(weightPct)}
                </PrivateValue>
              </strong>
            </div>
            <div
              className="allocation-track"
              role="img"
              aria-label={`${classLabels[assetClass]}: ${hidePrivate ? "porcentaje oculto" : formatPercent(weightPct)}`}
            >
              <span style={{ width: `${Math.min(weightPct, 100)}%` }} />
            </div>
            <small>
              <PrivateValue hidden={hidePrivate}>{formatMoney(value)}</PrivateValue>
            </small>
          </article>
        ))}
      </section>

      <PerformanceChart marketData={marketData} period={period} />

      <section className="holdings-section" aria-labelledby="holdings-title">
        <div className="holdings-toolbar">
          <div>
            <h2 id="holdings-title">Posiciones</h2>
            <p>
              {visibleRows.length} de {rows.length} activos
              {!marketData.portfolio.totals.valuation_complete &&
                ` · cotización disponible para ${marketData.portfolio.totals.priced_positions} de ${marketData.portfolio.totals.position_count}`}
            </p>
          </div>
          <div className="holding-filters">
            <label className="sr-only" htmlFor="portfolio-period">
              Periodo de rentabilidad
            </label>
            <select
              id="portfolio-period"
              value={period}
              onChange={(event) => {
                const value = event.target.value;
                if (isPortfolioPeriod(value)) setPeriod(value);
              }}
            >
              {periods.map((option) => (
                <option value={option.value} key={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor="holding-search">
              Buscar por nombre o ticker
            </label>
            <input
              id="holding-search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar activo…"
            />
            <label className="sr-only" htmlFor="asset-filter">
              Filtrar por clase de activo
            </label>
            <select
              id="asset-filter"
              value={assetFilter}
              onChange={(event) => {
                const value = event.target.value;
                if (isAssetFilter(value)) setAssetFilter(value);
              }}
            >
              <option value="all">Todas las clases</option>
              <option value="stock">Acciones</option>
              <option value="fund">Fondos</option>
              <option value="cash">Efectivo</option>
            </select>
          </div>
        </div>

        <div className="holdings-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col" aria-sort={sortKey === "name" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" onClick={() => chooseSort("name")}>
                    Activo
                  </button>
                </th>
                <th scope="col">Unidades</th>
                <th scope="col">Coste medio</th>
                <th scope="col">Coste total</th>
                <th
                  scope="col"
                  aria-sort={sortKey === "value" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                >
                  <button type="button" onClick={() => chooseSort("value")}>
                    Valor actual
                  </button>
                </th>
                <th scope="col">Precio actual</th>
                <th
                  scope="col"
                  aria-sort={sortKey === "daily" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                >
                  <button type="button" onClick={() => chooseSort("daily")}>
                    Cambio diario
                  </button>
                </th>
                <th
                  scope="col"
                  aria-sort={sortKey === "weight" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                >
                  <button type="button" onClick={() => chooseSort("weight")}>
                    Peso
                  </button>
                </th>
                <th scope="col">Resultados</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((holding) => (
                <HoldingRow
                  key={holding.ticker}
                  holding={holding}
                  hidePrivate={hidePrivate}
                />
              ))}
            </tbody>
          </table>
          {visibleRows.length === 0 && (
            <p className="empty-state">No hay activos para esos filtros.</p>
          )}
        </div>
        <p className="method-note">
          Los precios y valores que exporta Python están convertidos a EUR. La
          rentabilidad por periodo usa cierres históricos de Yahoo Finance; las
          cotizaciones ausentes se muestran como «—» y no se sustituyen por
          importes inventados.
          {!totals.valuation_complete &&
            " El valor total es parcial porque faltan cotizaciones."}
        </p>
      </section>
      {marketData.portfolio_activity.length > 0 && (
        <details className="dashboard-panel activity-panel">
          <summary>
            Historial de inversión · {marketData.portfolio_activity.length} movimientos
          </summary>
          <div className="holdings-scroll activity-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Fecha</th>
                  <th scope="col">Movimiento</th>
                  <th scope="col">Activo</th>
                  <th scope="col">Unidades</th>
                  <th scope="col">Precio</th>
                  <th scope="col">Importe</th>
                </tr>
              </thead>
              <tbody>
                {marketData.portfolio_activity.map((activity, index) => (
                  <tr key={`${activity.trade_date}-${activity.ticker}-${index}`}>
                    <td>{new Date(activity.trade_date).toLocaleDateString("es-ES")}</td>
                    <td>{activity.action.replaceAll("_", " ")}</td>
                    <td>{activity.name} · {activity.ticker}</td>
                    <td className="numeric">
                      {activity.shares === null ? (
                        "—"
                      ) : (
                        <PrivateValue hidden={hidePrivate}>
                          {units.format(activity.shares)}
                        </PrivateValue>
                      )}
                    </td>
                    <td className="numeric">
                      <PrivateValue hidden={hidePrivate}>
                        {formatMoney(activity.price_eur)}
                      </PrivateValue>
                    </td>
                    <td className="numeric">
                      <PrivateValue hidden={hidePrivate}>
                        {formatMoney(activity.amount_eur)}
                      </PrivateValue>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </main>
  );
}

function HoldingRow({
  holding,
  hidePrivate,
}: {
  holding: HoldingView;
  hidePrivate: boolean;
}) {
  const dailyValue = holding.dayDeltaEur;
  const dailyTone =
    dailyValue === null || dailyValue === 0
      ? ""
      : dailyValue > 0
        ? "positive"
        : "negative";
  const resultTone =
    holding.unrealizedPnlEur === null || holding.unrealizedPnlEur === 0
      ? ""
      : holding.unrealizedPnlEur > 0
        ? "positive"
        : "negative";

  return (
    <tr data-holding-row="true" data-ticker={holding.ticker}>
      <th scope="row">
        <span className="holding-name">{holding.name}</span>
        <span className="holding-meta">
          {holding.ticker} · {classLabels[holding.assetClass]} · {holding.currency}
        </span>
        {holding.quoteIsStale && (
          <span className="stale-badge">Cotización pendiente</span>
        )}
        {holding.quoteAsOf && (
          <span className="quote-date">
            Actualizado {new Date(holding.quoteAsOf).toLocaleDateString("es-ES")}
          </span>
        )}
      </th>
      <td className="numeric">
        <PrivateValue hidden={hidePrivate}>{units.format(holding.shares)}</PrivateValue>
      </td>
      <td className="numeric">
        <PrivateValue hidden={hidePrivate}>{formatMoney(holding.avgCost)}</PrivateValue>
      </td>
      <td className="numeric">
        <PrivateValue hidden={hidePrivate}>
          {formatMoney(holding.costBasisEur)}
        </PrivateValue>
      </td>
      <td className="numeric emphasized">
        <PrivateValue hidden={hidePrivate}>
          {formatMoney(holding.positionValue)}
        </PrivateValue>
      </td>
      <td className="numeric">
        {holding.currentPrice === null ? "—" : formatMoney(holding.currentPrice)}
      </td>
      <td className={`numeric tone-${dailyTone}`}>
        {dailyValue === null
          ? "—"
          : (
              <>
                <PrivateValue hidden={hidePrivate}>{formatMoney(dailyValue)}</PrivateValue>
                {" · "}
                {formatPercent(holding.dayDeltaPct)}
              </>
            )}
      </td>
      <td className="numeric">
        <PrivateValue hidden={hidePrivate}>
          {formatPercent(holding.weightPct)}
        </PrivateValue>
      </td>
      <td className={`numeric ${resultTone ? `tone-${resultTone}` : ""}`}>
        <PrivateValue hidden={hidePrivate}>
          {formatMoney(holding.unrealizedPnlEur)}
        </PrivateValue>
        <span className="return-detail">
          No realizado ·{" "}
          <PrivateValue hidden={hidePrivate}>
            {formatPercent(holding.unrealizedReturnPct)}
          </PrivateValue>
        </span>
        <span className="return-detail">
          Realizado ·{" "}
          <PrivateValue hidden={hidePrivate}>
            {formatMoney(holding.realizedPnlEur ?? null)}
          </PrivateValue>
        </span>
        <span className="return-detail">
          Total ·{" "}
          <PrivateValue hidden={hidePrivate}>
            {formatPercent(holding.totalReturnPct)}
          </PrivateValue>
          {" · Período "}
          <PrivateValue hidden={hidePrivate}>
            {formatPercent(holding.displayReturnPct)}
          </PrivateValue>
        </span>
      </td>
    </tr>
  );
}
