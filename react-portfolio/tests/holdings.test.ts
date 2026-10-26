import assert from "node:assert/strict";
import test from "node:test";
import { calculatePortfolio, type Holding } from "../src/holdings.ts";

const sampleHolding: Holding = {
  ticker: "TEST",
  name: "Example issuer",
  assetClass: "stock",
  currency: "EUR",
  shares: 10,
  avgCost: 5,
  currentPrice: 6,
  positionValue: 60,
  dayDeltaEur: 2.5,
  dayDeltaPct: 4.5,
  realizedPnlEur: 10,
  investedCostEur: 50,
};

test("calculates basis, unrealized and total return, daily return, and weight", () => {
  const { rows, totals } = calculatePortfolio([sampleHolding]);

  assert.equal(rows[0].costBasisEur, 50);
  assert.equal(rows[0].unrealizedPnlEur, 10);
  assert.equal(rows[0].unrealizedReturnPct, 20);
  assert.equal(rows[0].totalReturnPct, 40);
  assert.equal(rows[0].weightPct, 100);
  assert.equal(rows[0].displayReturnPct, 20);
  assert.equal(totals.valueEur, 60);
  assert.equal(totals.dailyChangeEur, 2.5);
  assert.equal(totals.dailyReturnPct, (2.5 / 57.5) * 100);
  assert.equal(totals.totalReturnPct, 40);
});

test("uses the supplied period return before falling back to unrealized return", () => {
  const { rows } = calculatePortfolio([
    { ...sampleHolding, changePct: 9 },
  ]);

  assert.equal(rows[0].displayReturnPct, 9);
});

test("does not calculate return percentages without a valid basis or quote", () => {
  const { rows, totals } = calculatePortfolio([
    {
      ...sampleHolding,
      shares: 0,
      currentPrice: null,
      investedCostEur: 0,
    },
  ]);

  assert.equal(rows[0].unrealizedReturnPct, null);
  assert.equal(rows[0].totalReturnPct, null);
  assert.equal(rows[0].weightPct, 100);
  assert.equal(totals.totalReturnPct, null);
});

test("keeps aggregate returns unknown when positions have missing market values", () => {
  const { rows, totals } = calculatePortfolio([
    sampleHolding,
    {
      ...sampleHolding,
      ticker: "MISSING",
      positionValue: null,
      currentPrice: null,
      dayDeltaEur: null,
      dayDeltaPct: null,
      avgCost: null,
      investedCostEur: null,
      realizedPnlEur: null,
    },
  ]);

  assert.equal(rows[1].unrealizedPnlEur, null);
  assert.equal(totals.valuationComplete, false);
  assert.equal(totals.unrealizedPnlEur, null);
  assert.equal(totals.dailyChangeEur, null);
});

test("returns safe zero totals for an empty portfolio", () => {
  const { rows, totals } = calculatePortfolio([]);

  assert.deepEqual(rows, []);
  assert.equal(totals.valueEur, 0);
  assert.equal(totals.dailyReturnPct, null);
  assert.equal(totals.totalReturnPct, null);
});
