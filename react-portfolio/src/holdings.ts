export type AssetClass = "stock" | "fund" | "cash";

export interface Holding {
  ticker: string;
  name: string;
  assetClass: AssetClass;
  currency: string;
  shares: number;
  avgCost: number | null;
  currentPrice: number | null;
  positionValue: number | null;
  dayDeltaEur: number | null;
  dayDeltaPct: number | null;
  changePct?: number | null;
  realizedPnlEur?: number | null;
  investedCostEur?: number | null;
  quoteAsOf?: string;
  quoteIsStale?: boolean;
}

export type HoldingSortKey = "name" | "value" | "daily" | "weight";

export interface HoldingView extends Holding {
  costBasisEur: number | null;
  unrealizedPnlEur: number | null;
  unrealizedReturnPct: number | null;
  totalReturnPct: number | null;
  weightPct: number;
  displayReturnPct: number | null;
}

export interface PortfolioTotals {
  valueEur: number;
  valuationComplete: boolean;
  costBasisEur: number | null;
  unrealizedPnlEur: number | null;
  realizedPnlEur: number | null;
  dailyChangeEur: number | null;
  dailyReturnPct: number | null;
  totalReturnPct: number | null;
}

export function calculatePortfolio(holdings: Holding[]): {
  rows: HoldingView[];
  totals: PortfolioTotals;
} {
  const valuationComplete = holdings.every(
    (holding) => holding.positionValue !== null,
  );
  const valueEur = holdings.reduce(
    (total, holding) => total + (holding.positionValue ?? 0),
    0,
  );
  const costBases = holdings.map((holding) =>
    holding.avgCost === null ? null : holding.shares * holding.avgCost,
  );
  const costBasisComplete = costBases.every((value) => value !== null);
  const costBasisEur = costBasisComplete
    ? costBases.reduce<number>((total, value) => total + (value ?? 0), 0)
    : null;
  const unrealizedComplete = holdings.every(
    (holding, index) =>
      holding.positionValue !== null && costBases[index] !== null,
  );
  const unrealizedPnlEur = unrealizedComplete
    ? holdings.reduce(
        (total, holding, index) =>
          total + (holding.positionValue ?? 0) - (costBases[index] ?? 0),
        0,
      )
    : null;
  const realizedComplete = holdings.every(
    (holding) => holding.realizedPnlEur !== null && holding.realizedPnlEur !== undefined,
  );
  const realizedPnlEur = realizedComplete
    ? holdings.reduce(
        (total, holding) => total + (holding.realizedPnlEur ?? 0),
        0,
      )
    : null;
  const dailyComplete = holdings.every(
    (holding) => holding.dayDeltaEur !== null,
  );
  const dailyChangeEur = dailyComplete
    ? holdings.reduce(
        (total, holding) => total + (holding.dayDeltaEur ?? 0),
        0,
      )
    : null;
  const previousValueEur =
    dailyChangeEur === null ? null : valueEur - dailyChangeEur;
  const investedValues = holdings.map((holding) => holding.investedCostEur);
  const investedComplete = investedValues.every((value) => value !== null && value !== undefined);
  const totalInvestedEur = investedComplete
    ? investedValues.reduce<number>((total, value) => total + (value ?? 0), 0)
    : null;
  const hasRealizedPnl = holdings.some(
    (holding) => Math.abs(holding.realizedPnlEur ?? 0) >= 0.005,
  );

  const rows = holdings.map((holding): HoldingView => {
    const holdingCostBasis =
      holding.avgCost === null ? null : holding.shares * holding.avgCost;
    const positionValue = holding.positionValue;
    const unrealized =
      positionValue === null || holdingCostBasis === null
        ? null
        : positionValue - holdingCostBasis;
    const unrealizedReturnPct =
      holdingCostBasis !== null &&
      holdingCostBasis > 0 &&
      unrealized !== null
        ? (unrealized / holdingCostBasis) * 100
        : null;
    const realized = holding.realizedPnlEur ?? 0;
    const totalInvested = holding.investedCostEur ?? 0;
    const totalReturnPct =
      Math.abs(realized) >= 0.005 &&
      totalInvested > 0 &&
      holding.currentPrice !== null &&
      holding.currentPrice > 0 &&
      unrealized !== null
        ? ((realized + unrealized) / totalInvested) * 100
        : null;

    return {
      ...holding,
      costBasisEur: holdingCostBasis,
      unrealizedPnlEur: unrealized,
      unrealizedReturnPct,
      totalReturnPct,
      weightPct:
        valueEur > 0 && positionValue !== null
          ? (positionValue / valueEur) * 100
          : 0,
      displayReturnPct: holding.changePct ?? unrealizedReturnPct,
    };
  });

  return {
    rows,
    totals: {
      valueEur,
      valuationComplete,
      costBasisEur,
      unrealizedPnlEur,
      realizedPnlEur,
      dailyChangeEur,
      dailyReturnPct:
        previousValueEur !== null &&
        previousValueEur > 0 &&
        dailyChangeEur !== null
          ? (dailyChangeEur / previousValueEur) * 100
          : null,
      totalReturnPct:
        hasRealizedPnl &&
        realizedComplete &&
        realizedPnlEur !== null &&
        unrealizedPnlEur !== null &&
        totalInvestedEur !== null &&
        totalInvestedEur > 0
          ? ((realizedPnlEur + unrealizedPnlEur) / totalInvestedEur) * 100
          : null,
    },
  };
}
