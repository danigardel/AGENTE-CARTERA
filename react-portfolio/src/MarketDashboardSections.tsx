import { useMemo, useState } from "react";
import type { MarketData, MarketHeadline, WatchlistItem } from "./marketData";

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

function formatMoney(value: number | null): string {
  return value === null ? "—" : money.format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${percent.format(value)} %`;
}

function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function tone(value: number | null): string {
  if (value === null || value === 0) return "";
  return value > 0 ? "tone-positive" : "tone-negative";
}

function HeadlineLink({ headline }: { headline: MarketHeadline }) {
  const href = safeExternalUrl(headline.url);
  const title = headline.display_title || headline.title;
  return (
    <li>
      {href ? (
        <a href={href} target="_blank" rel="noreferrer">
          {title}
        </a>
      ) : (
        <span>{title}</span>
      )}
      <small>
        {headline.source} · {new Date(headline.published).toLocaleString("es-ES")}
      </small>
    </li>
  );
}

function WatchlistTable({ items }: { items: WatchlistItem[] }) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("all");
  const sectors = useMemo(
    () =>
      [...new Set(items.map((item) => item.sector).filter((value): value is string => Boolean(value)))]
        .sort((left, right) => left.localeCompare(right, "es")),
    [items],
  );
  const visibleItems = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("es");
    return items.filter((item) => {
      const matchesQuery =
        !normalizedQuery ||
        `${item.name} ${item.ticker ?? ""}`
          .toLocaleLowerCase("es")
          .includes(normalizedQuery);
      return matchesQuery && (sector === "all" || item.sector === sector);
    });
  }, [items, query, sector]);

  return (
    <section className="dashboard-panel watchlist-panel" aria-labelledby="watchlist-title">
      <div className="dashboard-section-heading">
        <div>
          <h2 id="watchlist-title">Lista de seguimiento</h2>
          <p>{visibleItems.length} de {items.length} instrumentos</p>
        </div>
        <div className="holding-filters">
          <label className="sr-only" htmlFor="watchlist-search">
            Buscar en lista de seguimiento
          </label>
          <input
            id="watchlist-search"
            type="search"
            placeholder="Buscar ticker o empresa…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <label className="sr-only" htmlFor="watchlist-sector">
            Filtrar por sector
          </label>
          <select
            id="watchlist-sector"
            value={sector}
            onChange={(event) => setSector(event.target.value)}
          >
            <option value="all">Todos los sectores</option>
            {sectors.map((value) => (
              <option value={value} key={value}>{value}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="holdings-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Instrumento</th>
              <th scope="col">Sector</th>
              <th scope="col">Precio</th>
              <th scope="col">Cambio diario</th>
              <th scope="col">Sentimiento</th>
              <th scope="col">Analistas</th>
              <th scope="col">Noticias</th>
            </tr>
          </thead>
          <tbody>
            {visibleItems.map((item, index) => (
              <tr key={`${item.ticker ?? item.name}-${index}`}>
                <th scope="row">
                  <span className="holding-name">{item.name}</span>
                  <span className="holding-meta">{item.ticker ?? "Sin ticker"}</span>
                  {!item.market_data_current && (
                    <span className="stale-badge">Cotización desactualizada</span>
                  )}
                </th>
                <td>{item.sector ?? "—"}</td>
                <td className="numeric">{formatMoney(item.current_price_eur)}</td>
                <td className={`numeric ${tone(item.daily_change_pct)}`}>
                  {formatPercent(item.daily_change_pct)}
                </td>
                <td>{item.news_sentiment_label ?? "Sin datos"}</td>
                <td>
                  {item.analyst_consensus ?? "—"}
                  {item.analyst_count ? ` · ${item.analyst_count}` : ""}
                </td>
                <td>
                  {item.headlines.length > 0 ? (
                    <details className="row-news">
                      <summary>{item.headlines.length} titulares</summary>
                      <ul className="headline-list">
                        {item.headlines.slice(0, 4).map((headline, headlineIndex) => (
                          <HeadlineLink
                            key={`${headline.url}-${headlineIndex}`}
                            headline={headline}
                          />
                        ))}
                      </ul>
                    </details>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {visibleItems.length === 0 && (
          <p className="empty-state">No hay instrumentos para esos filtros.</p>
        )}
      </div>
    </section>
  );
}

function AnalysisSection({ marketData }: { marketData: MarketData }) {
  const analysis = marketData.analysis;
  return (
    <section className="analysis-grid" aria-labelledby="analysis-title">
      <div className="dashboard-panel analysis-main">
        <div className="dashboard-section-heading">
          <div>
            <h2 id="analysis-title">Análisis de Gemini</h2>
            <p>
              {analysis?.last_updated_utc
                ? `Actualizado ${new Date(analysis.last_updated_utc).toLocaleString("es-ES")}`
                : "Análisis generado por el job de Python"}
            </p>
          </div>
          <span className="ai-badge">IA · orientativo</span>
        </div>
        {analysis?.warning && (
          <p className="analysis-warning" role="status">{analysis.warning}</p>
        )}
        {analysis ? (
          <div className="analysis-copy">
            {analysis.portfolio_assessment && (
              <article>
                <h3>Valoración de cartera</h3>
                <p>{analysis.portfolio_assessment}</p>
              </article>
            )}
            {analysis.diversification && (
              <article>
                <h3>Diversificación</h3>
                <p>{analysis.diversification}</p>
              </article>
            )}
            {analysis.recommended_changes && (
              <article>
                <h3>Cambios recomendados</h3>
                <p>{analysis.recommended_changes}</p>
              </article>
            )}
            {analysis.market_context && (
              <article>
                <h3>Contexto de mercado</h3>
                <p>{analysis.market_context}</p>
              </article>
            )}
            {analysis.risks.length > 0 && (
              <article>
                <h3>Riesgos señalados</h3>
                <ul>{analysis.risks.map((risk, index) => <li key={index}>{risk}</li>)}</ul>
              </article>
            )}
          </div>
        ) : (
          <p>El backend no devolvió un análisis IA en esta ejecución.</p>
        )}
      </div>
      <div className="dashboard-panel">
        <h3>Ideas y candidatos</h3>
        {analysis ? (
          <>
            <IdeaList title="Compras destacadas" items={analysis.top_buys} />
            <IdeaList title="Ideas externas" items={analysis.external_ideas} />
            <IdeaList title="Candidatos de venta" items={analysis.sell_candidates} />
          </>
        ) : (
          <p className="muted-copy">Sin ideas disponibles.</p>
        )}
      </div>
    </section>
  );
}

function MarketEvents({ marketData }: { marketData: MarketData }) {
  if (marketData.market_events.length === 0) return null;
  return (
    <section className="dashboard-panel" aria-labelledby="market-events-title">
      <div className="dashboard-section-heading">
        <div>
          <h2 id="market-events-title">Próximos eventos</h2>
          <p>Resultados y fechas ex-dividendo de la lista seguida</p>
        </div>
      </div>
      <div className="event-grid">
        {marketData.market_events.map((event, index) => (
          <article
            className="event-card"
            key={`${event.event_date}-${event.ticker}-${index}`}
          >
            <time dateTime={event.event_date}>
              {new Date(event.event_date).toLocaleDateString("es-ES")}
            </time>
            <strong>{event.company}</strong>
            <span>{event.ticker} · {event.event_type}</span>
          </article>
        ))}
      </div>
    </section>
  );
}

function IdeaList({
  title,
  items,
}: {
  title: string;
  items: Record<string, string>[];
}) {
  if (items.length === 0) return null;
  return (
    <div className="idea-list">
      <h4>{title}</h4>
      <ul>
        {items.map((item, index) => (
          <li key={`${item.ticker ?? item.name ?? title}-${index}`}>
            {Object.values(item).filter(Boolean).join(" · ")}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MarketDashboardSections({ marketData }: { marketData: MarketData }) {
  const summary = marketData.market_summary;
  const topSectors = summary.sector_performance.slice(0, 4);

  return (
    <div className="market-dashboard">
      <header className="market-header">
        <div>
          <p className="eyebrow">Agente Cartera · radar diario</p>
          <h1>Mercado y seguimiento</h1>
          <p className="subheading">
            Datos de {new Date(marketData.generated_at).toLocaleString("es-ES")}
            {" · "}Actualiza el cronjob de Python.
          </p>
        </div>
        <div className={`sentiment-pill sentiment-${summary.sentiment_signal}`}>
          <span>Sentimiento de noticias</span>
          <strong>{summary.sentiment_label}</strong>
          <small>
            {summary.sentiment_score === null
              ? "Sin puntuación"
              : `${summary.sentiment_score > 0 ? "+" : ""}${summary.sentiment_score.toFixed(2)}`}
          </small>
        </div>
      </header>

      <section className="market-metrics" aria-label="Resumen de mercado">
        <article className="market-metric">
          <span>Instrumentos seguidos</span>
          <strong>{summary.company_count}</strong>
        </article>
        <article className="market-metric">
          <span>Cotizaciones actuales</span>
          <strong>{summary.current_quote_count} / {summary.company_count}</strong>
        </article>
        <article className="market-metric">
          <span>Titulares recogidos</span>
          <strong>{summary.headline_count}</strong>
        </article>
        <article className="market-metric">
          <span>Empresas con noticias</span>
          <strong>{summary.companies_with_news}</strong>
        </article>
        <article className="market-metric market-consensus">
          <span>Consenso de analistas</span>
          <strong>{summary.analyst_total}</strong>
          <small>{summary.consensus_summary}</small>
        </article>
      </section>

      {topSectors.length > 0 && (
        <section className="sector-strip" aria-label="Rendimiento sectorial medio">
          {topSectors.map((item) => (
            <article className="sector-card" key={item.sector}>
              <span>{item.sector}</span>
              <strong className={tone(item.average_daily_change_pct)}>
                {formatPercent(item.average_daily_change_pct)}
              </strong>
              <small>{item.companies} instrumentos</small>
            </article>
          ))}
        </section>
      )}

      <WatchlistTable items={marketData.watchlist} />

      <MarketEvents marketData={marketData} />

      <section className="dashboard-panel key-headlines" aria-labelledby="headlines-title">
        <div className="dashboard-section-heading">
          <div>
            <h2 id="headlines-title">Titulares destacados</h2>
            <p>Selección con mayor señal de sentimiento</p>
          </div>
        </div>
        {summary.key_headlines.length > 0 ? (
          <ul className="headline-list">
            {summary.key_headlines.map((headline, index) => (
              <HeadlineLink key={`${headline.url}-${index}`} headline={headline} />
            ))}
          </ul>
        ) : (
          <p className="muted-copy">No se recogieron titulares para este informe.</p>
        )}
      </section>

      <AnalysisSection marketData={marketData} />
    </div>
  );
}
