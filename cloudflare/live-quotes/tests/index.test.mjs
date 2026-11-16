import test from "node:test";
import assert from "node:assert/strict";

import instruments from "../src/instruments.mjs";
import worker, {parseQuotePayload} from "../src/index.mjs";

const allowedOrigin = "https://danigardel.github.io";
const env = {
  ALLOWED_ORIGIN: allowedOrigin,
  CACHE_TTL_SECONDS: "15",
};

function request(path, origin = allowedOrigin) {
  const headers = origin ? {Origin: origin} : {};
  return new Request(`https://quotes.example${path}`, {headers});
}

function quotePayload(timestamp) {
  return {
    info: {
      plotlines: [{id: "previousDay", value: 100}],
    },
    series: {
      intraday: {
        data: [[timestamp, 101.5]],
      },
    },
  };
}

test("rejects requests from origins other than the published dashboard", async () => {
  const response = await worker.fetch(
    request("/quotes?tickers=6B0.SG", "https://example.com"),
    env,
  );

  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), {error: "Origen no permitido."});
});

test("rejects unknown and oversized ticker batches before contacting L&S", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => {
    throw new Error("No debe contactar al proveedor.");
  };

  try {
    const unknown = await worker.fetch(
      request("/quotes?tickers=UNKNOWN"),
      env,
    );
    assert.equal(unknown.status, 400);

    const oversizedTickers = Object.keys(instruments).slice(0, 11).join(",");
    const oversized = await worker.fetch(
      request(`/quotes?tickers=${encodeURIComponent(oversizedTickers)}`),
      env,
    );
    assert.equal(oversized.status, 400);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fetches and returns an allowlisted L&S quote", async () => {
  const originalDate = globalThis.Date;
  const originalFetch = globalThis.fetch;
  const fixedNow = new originalDate("2026-11-16T11:00:00Z");
  const encodedLocalTime = new originalDate("2026-11-16T11:59:30Z").getTime();
  let upstreamCalls = 0;
  globalThis.Date = class extends originalDate {
    constructor(...args) {
      super(...(args.length ? args : [fixedNow]));
    }

    static now() {
      return fixedNow.getTime();
    }
  };
  globalThis.fetch = async (input, options) => {
    upstreamCalls += 1;
    const url = new URL(input);
    assert.equal(url.searchParams.get("instrumentId"), String(instruments["6B0.SG"]));
    assert.equal(url.searchParams.get("quotetype"), "mid");
    assert.equal(options.headers.Referer, "https://www.ls-tc.de/");
    return new Response(JSON.stringify(quotePayload(encodedLocalTime)), {
      status: 200,
      headers: {"Content-Type": "application/json"},
    });
  };

  try {
    const response = await worker.fetch(
      request("/quotes?tickers=6B0.SG"),
      env,
    );
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(upstreamCalls, 1);
    assert.deepEqual(body.unavailableTickers, []);
    assert.equal(body.quotes[0].priceEur, 101.5);
    assert.equal(body.quotes[0].timestamp, "2026-11-16T10:59:30.000Z");
  } finally {
    globalThis.Date = originalDate;
    globalThis.fetch = originalFetch;
  }
});

test("normalizes the latest current-day EUR mid quote", () => {
  const now = new Date("2026-11-16T12:00:00+01:00");
  const lastPriceTime = new Date("2026-11-16T11:59:30Z").getTime();
  const quote = parseQuotePayload(
    quotePayload(lastPriceTime),
    "6B0.SG",
    now,
  );

  assert.deepEqual(quote, {
    ticker: "6B0.SG",
    priceEur: 101.5,
    previousCloseEur: 100,
    marketDate: "2026-11-16",
    marketTimezone: "Europe/Madrid",
    timestamp: "2026-11-16T10:59:30.000Z",
  });
});

test("rejects a stale intraday quote during the market session", () => {
  const now = new Date("2026-11-16T12:00:00+01:00");
  const staleTimestamp = new Date("2026-11-16T10:00:00Z").getTime();

  assert.equal(
    parseQuotePayload(quotePayload(staleTimestamp), "6B0.SG", now),
    null,
  );
});
