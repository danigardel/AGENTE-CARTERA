import instruments from "./instruments.mjs";

const L_AND_S_URL =
  "https://www.ls-tc.de/_rpc/json/instrument/chart/dataForInstrument";
const MARKET_TIMEZONE = "Europe/Berlin";
const MARKET_PARTS_FORMATTER = new Intl.DateTimeFormat("en", {
  timeZone: MARKET_TIMEZONE,
  weekday: "short",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const MAX_TICKERS_PER_REQUEST = 10;
const MAX_RESPONSE_BYTES = 512 * 1024;
const UPSTREAM_TIMEOUT_MS = 10_000;
const quoteCache = new Map();

function responseHeaders(origin, allowedOrigin) {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    Vary: "Origin",
  });
  if (origin === allowedOrigin) {
    headers.set("Access-Control-Allow-Origin", allowedOrigin);
    headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
    headers.set("Access-Control-Allow-Headers", "Accept");
    headers.set("Access-Control-Max-Age", "600");
  }
  return headers;
}

function jsonResponse(body, status, origin, allowedOrigin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: responseHeaders(origin, allowedOrigin),
  });
}

function partsInZone(date) {
  return MARKET_PARTS_FORMATTER.formatToParts(date).reduce((parts, item) => {
    if (item.type !== "literal") parts[item.type] = item.value;
    return parts;
  }, {});
}

function dateInMarketZone(date) {
  const parts = partsInZone(date);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function marketOffsetMs(date) {
  const parts = partsInZone(date);
  const localAsUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
    date.getUTCMilliseconds(),
  );
  return localAsUtc - date.getTime();
}

function isMarketSession(now) {
  const parts = partsInZone(now);
  const minuteOfDay = Number(parts.hour) * 60 + Number(parts.minute);
  return ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(parts.weekday)
    && minuteOfDay >= 450
    && minuteOfDay <= 1380;
}

export function parseQuotePayload(payload, ticker, now = new Date()) {
  const info = payload?.info;
  const points = payload?.series?.intraday?.data;
  if (!info || !Array.isArray(info.plotlines) || !Array.isArray(points)) {
    return null;
  }

  const previousClose = info.plotlines
    .filter((line) => line && typeof line === "object")
    .find((line) => (
      line.id === "previousDay"
      || String(line.label || "").toLowerCase().includes("vortag")
    ));
  const previousCloseEur = Number(previousClose?.value);
  if (!Number.isFinite(previousCloseEur) || previousCloseEur <= 0) return null;

  const validPoints = [];
  const offsetMs = marketOffsetMs(now);
  for (const point of points) {
    if (!Array.isArray(point) || point.length < 2) continue;
    const encodedTimestamp = Number(point[0]);
    const price = Number(point[1]);
    if (
      !Number.isFinite(encodedTimestamp)
      || !Number.isFinite(price)
      || encodedTimestamp <= 0
      || price <= 0
    ) {
      continue;
    }
    const timestamp = encodedTimestamp - offsetMs;
    if (timestamp > now.getTime() + 120_000) continue;
    validPoints.push({timestamp, price});
  }
  validPoints.sort((left, right) => left.timestamp - right.timestamp);

  let lastPoint = null;
  for (let index = validPoints.length - 1; index >= 0; index -= 1) {
    const point = validPoints[index];
    const date = new Date(point.timestamp);
    const parts = partsInZone(date);
    const minuteOfDay = Number(parts.hour) * 60 + Number(parts.minute);
    if (minuteOfDay >= 450 && minuteOfDay <= 1380) {
      lastPoint = {...point, date};
      break;
    }
  }
  if (!lastPoint) return null;

  if (isMarketSession(now)) {
    const today = dateInMarketZone(now);
    if (dateInMarketZone(lastPoint.date) !== today) return null;
    if (now.getTime() - lastPoint.timestamp > 30 * 60_000) {
      return null;
    }
  }

  return {
    ticker,
    priceEur: lastPoint.price,
    previousCloseEur,
    marketDate: dateInMarketZone(lastPoint.date),
    marketTimezone: "Europe/Madrid",
    timestamp: lastPoint.date.toISOString(),
  };
}

async function fetchUpstreamQuote(instrumentId, ticker, cacheTtlSeconds) {
  const url = new URL(L_AND_S_URL);
  url.search = new URLSearchParams({
    instrumentId: String(instrumentId),
    marketId: "1",
    quotetype: "mid",
    series: "intraday",
    localeId: "2",
  }).toString();
  const cached = quoteCache.get(instrumentId);
  if (cached && cached.expiresAt > Date.now()) {
    return {...cached.quote, ticker};
  }
  quoteCache.delete(instrumentId);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  let upstream;
  try {
    upstream = await fetch(url, {
      headers: {
        Accept: "application/json, text/plain, */*",
        Referer: "https://www.ls-tc.de/",
        "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          + "AppleWebKit/537.36 (KHTML, like Gecko) "
          + "Chrome/131.0.0.0 Safari/537.36"
        ),
      },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
  if (!upstream.ok) throw new Error(`L&S respondió HTTP ${upstream.status}`);

  const contentLength = Number(upstream.headers.get("Content-Length"));
  if (Number.isFinite(contentLength) && contentLength > MAX_RESPONSE_BYTES) {
    throw new Error("La respuesta de L&S supera el límite de tamaño.");
  }
  const text = await upstream.text();
  if (new TextEncoder().encode(text).byteLength > MAX_RESPONSE_BYTES) {
    throw new Error("La respuesta de L&S supera el límite de tamaño.");
  }
  const quote = parseQuotePayload(JSON.parse(text), ticker);
  if (!quote) throw new Error("L&S no devolvió una cotización vigente.");

  quoteCache.set(instrumentId, {
    quote,
    expiresAt: Date.now() + cacheTtlSeconds * 1000,
  });
  return quote;
}

async function fetchQuotes(tickers, cacheTtlSeconds) {
  const results = new Array(tickers.length);
  let nextIndex = 0;
  const workerCount = Math.min(6, tickers.length);

  await Promise.all(Array.from({length: workerCount}, async () => {
    while (nextIndex < tickers.length) {
      const index = nextIndex++;
      const ticker = tickers[index];
      try {
        results[index] = await fetchUpstreamQuote(
          instruments[ticker],
          ticker,
          cacheTtlSeconds,
        );
      } catch (error) {
        console.warn(
          "L&S no disponible para el ticker",
          ticker,
          error instanceof Error ? error.name : "Error desconocido",
        );
        results[index] = null;
      }
    }
  }));
  return results;
}

export default {
  async fetch(request, env) {
    const requestUrl = new URL(request.url);
    const origin = request.headers.get("Origin");
    const allowedOrigin = env.ALLOWED_ORIGIN;

    if (origin && origin !== allowedOrigin) {
      return jsonResponse({error: "Origen no permitido."}, 403, origin, allowedOrigin);
    }

    if (requestUrl.pathname === "/healthz" && request.method === "GET") {
      return jsonResponse(
        {status: "ok", service: "live-quotes"},
        200,
        origin,
        allowedOrigin,
      );
    }

    if (requestUrl.pathname === "/quotes" && request.method === "OPTIONS") {
      if (origin !== allowedOrigin) {
        return jsonResponse({error: "Origen no permitido."}, 403, origin, allowedOrigin);
      }
      return new Response(null, {
        status: 204,
        headers: responseHeaders(origin, allowedOrigin),
      });
    }

    if (requestUrl.pathname !== "/quotes") {
      return jsonResponse({error: "Ruta no encontrada."}, 404, origin, allowedOrigin);
    }
    if (request.method !== "GET") {
      return jsonResponse({error: "Método no permitido."}, 405, origin, allowedOrigin);
    }
    if (origin !== allowedOrigin) {
      return jsonResponse({error: "Origen no permitido."}, 403, origin, allowedOrigin);
    }
    if (!isMarketSession(new Date())) {
      return jsonResponse(
        {error: "El sondeo de L&S solo está activo en horario de mercado."},
        409,
        origin,
        allowedOrigin,
      );
    }

    const requested = requestUrl.searchParams.get("tickers");
    const tickers = requested
      ? [...new Set(requested.split(",").map((ticker) => ticker.trim()))]
      : [];
    if (
      !tickers.length
      || tickers.length > MAX_TICKERS_PER_REQUEST
      || tickers.some((ticker) => !Object.hasOwn(instruments, ticker))
    ) {
      return jsonResponse(
        {
          error: `Indica entre 1 y ${MAX_TICKERS_PER_REQUEST} tickers permitidos.`,
        },
        400,
        origin,
        allowedOrigin,
      );
    }

    const cacheTtlSeconds = Math.max(
      5,
      Math.min(60, Number.parseInt(env.CACHE_TTL_SECONDS, 10) || 15),
    );
    const fetched = await fetchQuotes(tickers, cacheTtlSeconds);
    const quotes = fetched.filter(Boolean);
    const unavailableTickers = tickers.filter((_, index) => !fetched[index]);
    if (!quotes.length) {
      return jsonResponse(
        {
          error: "L&S no devolvió cotizaciones actuales.",
          unavailableTickers,
        },
        502,
        origin,
        allowedOrigin,
      );
    }
    return jsonResponse(
      {quotes, unavailableTickers},
      200,
      origin,
      allowedOrigin,
    );
  },
};
