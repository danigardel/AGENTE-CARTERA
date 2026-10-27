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

function marketData(riskZones: Record<string, unknown> | null) {
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
    analysis: null,
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
