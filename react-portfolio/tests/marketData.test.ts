import assert from "node:assert/strict";
import test from "node:test";
import { isMarketData } from "../src/marketData.ts";

const legacyRiskZones = {
  status: "ok",
  atr_pct: 2,
  review_eur: 95,
  review_pct: -5,
  target_eur: 110,
  target_pct: 10,
  atr_days: 14,
  atr_multiplier: 2.5,
  peak_window: 22,
  reward_risk: 2,
};

function marketData(
  riskZones: Record<string, unknown> | null,
  analysis: Record<string, unknown> | null = null,
  alertsToday?: unknown,
  quantSummary?: unknown,
  macroIndicators?: unknown,
) {
  return {
    schema_version: 1,
    generated_at: "2026-10-26T12:00:00+01:00",
    market_summary: {
      sentiment_label: "Neutral",
      sentiment_signal: "neutral",
      sentiment_score: null,
      companies_with_news: 0,
      headline_count: 0,
      analyst_counts: {},
      analyst_total: 0,
      consensus_summary: "",
      key_headlines: [],
      company_count: 0,
      current_quote_count: 0,
      sector_performance: [],
    },
    watchlist: [],
    portfolio: {
      positions: [
        {
          ticker: "TEST",
          name: "Example",
          asset_class: "stock",
          currency: "EUR",
          shares: 1,
          investment_eur: 100,
          invested_cost_eur: 100,
          avg_cost_eur: 100,
          current_price_eur: 100,
          current_value_eur: 100,
          day_delta_eur: 0,
          day_delta_pct: 0,
          returns_pct: {},
          prices_eur: {},
          max_profit_eur: 0,
          max_return_pct: 0,
          risk_zones: riskZones,
          price_date: "2026-10-26",
          quote_is_stale: false,
          sector: null,
          quote_type: "EQUITY",
          realized_pnl_eur: 0,
          warning: null,
        },
      ],
      totals: {
        investment_base_eur: 100,
        open_cost_basis_eur: 100,
        current_value_eur: 100,
        valuation_complete: true,
        priced_positions: 1,
        position_count: 1,
        unrealized_pnl_eur: 0,
        realized_pnl_eur: 0,
        total_return_pct: 0,
        daily_change_eur: 0,
        returns_pct: {},
      },
    },
    analysis,
    ...(alertsToday === undefined ? {} : { alerts_today: alertsToday }),
    ...(quantSummary === undefined ? {} : { quant_summary: quantSummary }),
    ...(macroIndicators === undefined
      ? {}
      : { macro_indicators: macroIndicators }),
    market_events: [],
    portfolio_activity: [],
    charts: {
      portfolio_period_series: {},
      benchmark_period_series: {},
      portfolio_history_series: [],
    },
  };
}

test("accepts legacy market data without risk zones and legacy risk-zone fields", () => {
  assert.equal(isMarketData(marketData(null)), true);
  assert.equal(isMarketData(marketData(legacyRiskZones)), true);
});

test("accepts optional VIX macro data and rejects malformed values", () => {
  assert.equal(
    isMarketData(
      marketData(null, null, undefined, undefined, {
        vix: { price: 18.5, change_pct: 2.1 },
      }),
    ),
    true,
  );
  assert.equal(
    isMarketData(
      marketData(null, null, undefined, undefined, {
        vix: { price: null, change_pct: null },
      }),
    ),
    true,
  );
  assert.equal(
    isMarketData(
      marketData(null, null, undefined, undefined, {
        vix: { price: "18.5", change_pct: 2.1 },
      }),
    ),
    false,
  );
});

test("accepts optional action signals and summaries while preserving old analyses", () => {
  const legacyAnalysis = {
    portfolio_assessment: "Evaluación.",
    diversification: "Diversificación.",
    recommended_changes: "Mantener.",
    top_buys: [],
    external_ideas: [],
    sell_candidates: [],
    market_context: "Contexto.",
    risks: [],
    warning: null,
    last_updated_utc: null,
  };
  assert.equal(isMarketData(marketData(null, legacyAnalysis)), true);
  assert.equal(
    isMarketData(
      marketData(null, {
        ...legacyAnalysis,
        action_signal: "RISK_ALERT",
        telegram_summary: "Revisar el soporte.",
      }),
    ),
    true,
  );
  assert.equal(
    isMarketData(
      marketData(null, {
        ...legacyAnalysis,
        action_signal: "SELL_NOW",
      }),
    ),
    false,
  );
  assert.equal(
    isMarketData(
      marketData(
        null,
        {
          ...legacyAnalysis,
          provider_used: "gemini",
          model_used: "gemini-3.8-flash",
          is_stale: true,
        },
        [
          {
            ticker: "TEST",
            name: "Example",
            daily_change_pct: -2.4,
            current_price_eur: 97.6,
            last_alerted_at: "2026-10-26T12:00:00+01:00",
          },
        ],
      ),
    ),
    true,
  );
  assert.equal(
    isMarketData(
      marketData(null, { ...legacyAnalysis, is_stale: "true" }),
    ),
    false,
  );
  assert.equal(
    isMarketData(
      marketData(null, { ...legacyAnalysis, provider_used: 7 }),
    ),
    false,
  );
  assert.equal(
    isMarketData(
      marketData(null, { ...legacyAnalysis, model_used: "openrouter/free" }),
    ),
    true,
  );
  assert.equal(
    isMarketData(
      marketData(null, { ...legacyAnalysis, model_used: 7 }),
    ),
    false,
  );
  assert.equal(
    isMarketData(
      marketData(null, legacyAnalysis, [{ ticker: "TEST" }]),
    ),
    false,
  );
});

test("accepts optional deterministic quant summary and rejects malformed entries", () => {
  const quantSummary = {
    generated_at: "2026-10-26T12:00:00+01:00",
    alert_drop_pct: 2,
    atr_crossed: [],
    volatile_daily_moves: [
      { ticker: "TEST", name: "Example", daily_change_pct: -4 },
    ],
    daily_drop_alerts: [],
    earnings_next_7_days: [],
    distance_from_52w_high: [],
  };
  assert.equal(isMarketData(marketData(null, null, undefined, quantSummary)), true);
  assert.equal(
    isMarketData(
      marketData(
        null,
        null,
        undefined,
        { ...quantSummary, atr_crossed: [{ ticker: 12, name: "Bad" }] },
      ),
    ),
    false,
  );
});

test("accepts the new levels and confidence label in the crossed secondary state", () => {
  assert.equal(
    isMarketData(
      marketData({
        ...legacyRiskZones,
        status: "crossed",
        target_eur: null,
        target_pct: null,
        primary_review_eur: 95,
        primary_review_pct: -5,
        primary_target_eur: 110,
        primary_target_pct: 10,
        confidence: "medium",
        sessions_used: 40,
      }),
    ),
    true,
  );
});

test("accepts no-data risk zones with null primary levels and rejects partial levels", () => {
  assert.equal(
    isMarketData(
      marketData({
        ...legacyRiskZones,
        status: "no_data",
        atr_pct: null,
        review_eur: null,
        review_pct: null,
        target_eur: null,
        target_pct: null,
        primary_review_eur: null,
        primary_review_pct: null,
        primary_target_eur: null,
        primary_target_pct: null,
        confidence: null,
        sessions_used: 14,
      }),
    ),
    true,
  );
  assert.equal(
    isMarketData(
      marketData({
        ...legacyRiskZones,
        primary_review_eur: 95,
      }),
    ),
    false,
  );
});

test("validates confidence labels against session thresholds and rejects partial values", () => {
  for (const [confidence, sessions] of [
    ["low", 29],
    ["medium", 30],
    ["medium", 99],
    ["high", 100],
  ] as const) {
    assert.equal(
      isMarketData(
        marketData({
          ...legacyRiskZones,
          primary_review_eur: 95,
          primary_review_pct: -5,
          primary_target_eur: 110,
          primary_target_pct: 10,
          confidence,
          sessions_used: sessions,
        }),
      ),
      true,
    );
  }
  assert.equal(
    isMarketData(
      marketData({
        ...legacyRiskZones,
        confidence: "high",
        sessions_used: 99,
      }),
    ),
    false,
  );
  assert.equal(
    isMarketData(
      marketData({
        ...legacyRiskZones,
        confidence: 1,
        sessions_used: 100,
      }),
    ),
    false,
  );
  assert.equal(
    isMarketData(
      marketData({
        ...legacyRiskZones,
        confidence: "low",
      }),
    ),
    false,
  );
});
