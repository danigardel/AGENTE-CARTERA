export type AssetClass = "stock" | "fund" | "cash";
export type SentimentSignal = "bullish" | "bearish" | "neutral";
export type PortfolioPeriod = "1D" | "1W" | "1M" | "6M" | "1Y" | "MAX";

export interface MarketHeadline {
  title: string;
  title_es: string | null;
  display_title: string;
  url: string;
  source: string;
  published: string;
  sentiment: number;
  sentiment_label: string;
  sentiment_signal: SentimentSignal;
}

export interface WatchlistItem {
  ticker: string | null;
  name: string;
  sector: string | null;
  current_price_eur: number | null;
  currency: string | null;
  daily_change_abs_eur: number | null;
  daily_change_pct: number | null;
  market_date: string | null;
  market_timezone: string | null;
  market_data_current: boolean;
  news_sentiment_label: string | null;
  news_sentiment_score: number | null;
  peg_ratio: number | null;
  pe_ratio: number | null;
  analyst_consensus: string | null;
  analyst_count: number | null;
  target_mean_eur: number | null;
  fundamentals: Record<string, unknown>;
  warning: string | null;
  headlines: MarketHeadline[];
}

export interface PortfolioPositionData {
  ticker: string;
  name: string;
  asset_class: AssetClass;
  currency: string;
  shares: number;
  investment_eur: number;
  invested_cost_eur: number;
  avg_cost_eur: number | null;
  current_price_eur: number | null;
  current_value_eur: number | null;
  day_delta_eur: number | null;
  day_delta_pct: number | null;
  returns_pct: Record<string, number | null>;
  prices_eur: Record<string, number | null>;
  max_profit_eur: number | null;
  max_return_pct: number | null;
  price_date: string | null;
  quote_is_stale: boolean;
  sector: string | null;
  quote_type: string | null;
  realized_pnl_eur: number | null;
  warning: string | null;
}

export interface PortfolioData {
  positions: PortfolioPositionData[];
  totals: {
    investment_base_eur: number;
    open_cost_basis_eur: number;
    current_value_eur: number;
    valuation_complete: boolean;
    priced_positions: number;
    position_count: number;
    unrealized_pnl_eur: number | null;
    realized_pnl_eur: number | null;
    total_return_pct: number | null;
    daily_change_eur: number | null;
    returns_pct: Record<string, number | null>;
  };
}

export interface ChartPoint {
  timestamp: string;
  value: number;
}

export interface BenchmarkChart {
  ticker: string;
  name: string;
  series: ChartPoint[];
}

export interface MarketData {
  schema_version: 1;
  generated_at: string;
  market_summary: {
    sentiment_label: string;
    sentiment_signal: SentimentSignal;
    sentiment_score: number | null;
    companies_with_news: number;
    headline_count: number;
    analyst_counts: Record<string, number>;
    analyst_total: number;
    consensus_summary: string;
    key_headlines: MarketHeadline[];
    company_count: number;
    current_quote_count: number;
    sector_performance: {
      sector: string;
      average_daily_change_pct: number;
      companies: number;
    }[];
  };
  watchlist: WatchlistItem[];
  portfolio: PortfolioData;
  analysis: {
    portfolio_assessment: string | null;
    diversification: string | null;
    recommended_changes: string | null;
    top_buys: Record<string, string>[];
    external_ideas: Record<string, string>[];
    sell_candidates: Record<string, string>[];
    market_context: string | null;
    risks: string[];
    warning: string | null;
    last_updated_utc: string | null;
  } | null;
  market_events: {
    event_date: string;
    ticker: string;
    company: string;
    event_type: string;
  }[];
  portfolio_activity: {
    trade_date: string;
    action: string;
    ticker: string;
    name: string;
    asset_class: string;
    currency: string;
    shares: number | null;
    price_eur: number | null;
    amount_eur: number;
    fee_eur: number;
    tax_eur: number;
  }[];
  charts: {
    portfolio_period_series: Record<string, ChartPoint[]>;
    benchmark_period_series: Record<string, BenchmarkChart[]>;
    portfolio_history_series: ChartPoint[];
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value);
}

function isNumberRecord(value: unknown): value is Record<string, number | null> {
  return (
    isRecord(value) &&
    Object.values(value).every(isNullableNumber)
  );
}

function isStringRecordArray(value: unknown): value is Record<string, string>[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        isRecord(item) &&
        Object.values(item).every((entry) => typeof entry === "string"),
    )
  );
}

function isHeadline(value: unknown): value is MarketHeadline {
  if (!isRecord(value)) return false;
  return (
    typeof value.title === "string" &&
    isNullableString(value.title_es) &&
    typeof value.display_title === "string" &&
    typeof value.url === "string" &&
    typeof value.source === "string" &&
    typeof value.published === "string" &&
    isFiniteNumber(value.sentiment) &&
    typeof value.sentiment_label === "string" &&
    (value.sentiment_signal === "bullish" ||
      value.sentiment_signal === "bearish" ||
      value.sentiment_signal === "neutral")
  );
}

function isWatchlistItem(value: unknown): value is WatchlistItem {
  if (!isRecord(value)) return false;
  return (
    isNullableString(value.ticker) &&
    typeof value.name === "string" &&
    isNullableString(value.sector) &&
    isNullableNumber(value.current_price_eur) &&
    isNullableString(value.currency) &&
    isNullableNumber(value.daily_change_abs_eur) &&
    isNullableNumber(value.daily_change_pct) &&
    isNullableString(value.market_date) &&
    isNullableString(value.market_timezone) &&
    typeof value.market_data_current === "boolean" &&
    isNullableString(value.news_sentiment_label) &&
    isNullableNumber(value.news_sentiment_score) &&
    isNullableNumber(value.peg_ratio) &&
    isNullableNumber(value.pe_ratio) &&
    isNullableString(value.analyst_consensus) &&
    isNullableNumber(value.analyst_count) &&
    isNullableNumber(value.target_mean_eur) &&
    isRecord(value.fundamentals) &&
    isNullableString(value.warning) &&
    Array.isArray(value.headlines) &&
    value.headlines.every(isHeadline)
  );
}

function isPortfolioPosition(value: unknown): value is PortfolioPositionData {
  if (!isRecord(value)) return false;
  return (
    typeof value.ticker === "string" &&
    typeof value.name === "string" &&
    (value.asset_class === "stock" ||
      value.asset_class === "fund" ||
      value.asset_class === "cash") &&
    typeof value.currency === "string" &&
    isFiniteNumber(value.shares) &&
    isFiniteNumber(value.investment_eur) &&
    isFiniteNumber(value.invested_cost_eur) &&
    isNullableNumber(value.avg_cost_eur) &&
    isNullableNumber(value.current_price_eur) &&
    isNullableNumber(value.current_value_eur) &&
    isNullableNumber(value.day_delta_eur) &&
    isNullableNumber(value.day_delta_pct) &&
    isNumberRecord(value.returns_pct) &&
    isNumberRecord(value.prices_eur) &&
    isNullableNumber(value.max_profit_eur) &&
    isNullableNumber(value.max_return_pct) &&
    isNullableString(value.price_date) &&
    typeof value.quote_is_stale === "boolean" &&
    isNullableString(value.sector) &&
    isNullableString(value.quote_type) &&
    isNullableNumber(value.realized_pnl_eur) &&
    isNullableString(value.warning)
  );
}

function isMarketEvent(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.event_date === "string" &&
    typeof value.ticker === "string" &&
    typeof value.company === "string" &&
    typeof value.event_type === "string"
  );
}

function isPortfolioActivity(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.trade_date === "string" &&
    typeof value.action === "string" &&
    typeof value.ticker === "string" &&
    typeof value.name === "string" &&
    typeof value.asset_class === "string" &&
    typeof value.currency === "string" &&
    isNullableNumber(value.shares) &&
    isNullableNumber(value.price_eur) &&
    isFiniteNumber(value.amount_eur) &&
    isFiniteNumber(value.fee_eur) &&
    isFiniteNumber(value.tax_eur)
  );
}

function isChartPoint(value: unknown): value is ChartPoint {
  return (
    isRecord(value) &&
    typeof value.timestamp === "string" &&
    isFiniteNumber(value.value)
  );
}

function isChartPointRecord(value: unknown): value is Record<string, ChartPoint[]> {
  return (
    isRecord(value) &&
    Object.values(value).every(
      (points) => Array.isArray(points) && points.every(isChartPoint),
    )
  );
}

function isBenchmarkChart(value: unknown): value is BenchmarkChart {
  return (
    isRecord(value) &&
    typeof value.ticker === "string" &&
    typeof value.name === "string" &&
    Array.isArray(value.series) &&
    value.series.every(isChartPoint)
  );
}

function isBenchmarkChartRecord(
  value: unknown,
): value is Record<string, BenchmarkChart[]> {
  return (
    isRecord(value) &&
    Object.values(value).every(
      (series) => Array.isArray(series) && series.every(isBenchmarkChart),
    )
  );
}

export function isMarketData(value: unknown): value is MarketData {
  if (!isRecord(value) || value.schema_version !== 1) return false;
  const summary = value.market_summary;
  const portfolio = value.portfolio;
  if (!isRecord(summary) || !isRecord(portfolio)) return false;
  const totals = portfolio.totals;
  const analysis = value.analysis;

  return (
    typeof value.generated_at === "string" &&
    typeof summary.sentiment_label === "string" &&
    (summary.sentiment_signal === "bullish" ||
      summary.sentiment_signal === "bearish" ||
      summary.sentiment_signal === "neutral") &&
    isNullableNumber(summary.sentiment_score) &&
    isFiniteNumber(summary.companies_with_news) &&
    isFiniteNumber(summary.headline_count) &&
    isRecord(summary.analyst_counts) &&
    Object.values(summary.analyst_counts).every(
      isFiniteNumber,
    ) &&
    isFiniteNumber(summary.analyst_total) &&
    typeof summary.consensus_summary === "string" &&
    Array.isArray(summary.key_headlines) &&
    summary.key_headlines.every(isHeadline) &&
    isFiniteNumber(summary.company_count) &&
    isFiniteNumber(summary.current_quote_count) &&
    Array.isArray(summary.sector_performance) &&
    summary.sector_performance.every(
      (sector) =>
        isRecord(sector) &&
        typeof sector.sector === "string" &&
        isFiniteNumber(sector.average_daily_change_pct) &&
        isFiniteNumber(sector.companies),
    ) &&
    Array.isArray(value.watchlist) &&
    value.watchlist.every(isWatchlistItem) &&
    Array.isArray(portfolio.positions) &&
    portfolio.positions.every(isPortfolioPosition) &&
    isRecord(totals) &&
    isFiniteNumber(totals.investment_base_eur) &&
    isFiniteNumber(totals.open_cost_basis_eur) &&
    isFiniteNumber(totals.current_value_eur) &&
    typeof totals.valuation_complete === "boolean" &&
    isFiniteNumber(totals.priced_positions) &&
    isFiniteNumber(totals.position_count) &&
    isNullableNumber(totals.unrealized_pnl_eur) &&
    isNullableNumber(totals.realized_pnl_eur) &&
    isNullableNumber(totals.total_return_pct) &&
    isNullableNumber(totals.daily_change_eur) &&
    isNumberRecord(totals.returns_pct) &&
    (analysis === null ||
      (isRecord(analysis) &&
        isNullableString(analysis.portfolio_assessment) &&
        isNullableString(analysis.diversification) &&
        isNullableString(analysis.recommended_changes) &&
        isStringRecordArray(analysis.top_buys) &&
        isStringRecordArray(analysis.external_ideas) &&
        isStringRecordArray(analysis.sell_candidates) &&
        isNullableString(analysis.market_context) &&
        Array.isArray(analysis.risks) &&
        analysis.risks.every((risk) => typeof risk === "string") &&
        isNullableString(analysis.warning) &&
        isNullableString(analysis.last_updated_utc))) &&
    Array.isArray(value.market_events) &&
    value.market_events.every(isMarketEvent) &&
    Array.isArray(value.portfolio_activity) &&
    value.portfolio_activity.every(isPortfolioActivity) &&
    isRecord(value.charts) &&
    isChartPointRecord(value.charts.portfolio_period_series) &&
    isBenchmarkChartRecord(value.charts.benchmark_period_series) &&
    Array.isArray(value.charts.portfolio_history_series) &&
    value.charts.portfolio_history_series.every(isChartPoint)
  );
}

export async function fetchMarketData(
  signal?: AbortSignal,
): Promise<MarketData> {
  const dataUrl = new URL(
    "market_data.json",
    new URL(import.meta.env.BASE_URL, window.location.href),
  );
  const response = await fetch(dataUrl, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(
      `No se pudo cargar el informe del mercado (HTTP ${response.status}).`,
    );
  }
  const payload: unknown = await response.json();
  if (!isMarketData(payload)) {
    throw new Error(
      "El informe del mercado no coincide con el contrato de datos esperado.",
    );
  }
  return payload;
}
