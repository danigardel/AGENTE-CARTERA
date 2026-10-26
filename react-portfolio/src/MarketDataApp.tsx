import { useEffect, useState } from "react";
import { fetchMarketData } from "./marketData";
import type { MarketData } from "./marketData";
import { MarketDashboardSections } from "./MarketDashboardSections";
import { PortfolioView } from "./PortfolioView";

export function MarketDataApp() {
  const [marketData, setMarketData] = useState<MarketData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setMarketData(null);
    setError(null);

    fetchMarketData(controller.signal)
      .then(setMarketData)
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "Ocurrió un error desconocido al cargar los datos del mercado.",
        );
      });

    return () => controller.abort();
  }, [attempt]);

  if (error) {
    return (
      <main className="load-state">
        <section className="load-card load-error" role="alert">
          <p className="eyebrow">Agente Cartera</p>
          <h1>No se pudo cargar el informe</h1>
          <p>{error}</p>
          <p>
            Comprueba que el cronjob haya generado <code>market_data.json</code>
            y vuelve a intentarlo.
          </p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Reintentar
          </button>
        </section>
      </main>
    );
  }

  if (!marketData) {
    return (
      <main className="load-state" aria-live="polite">
        <section className="load-card">
          <span className="loading-indicator" aria-hidden="true" />
          <h1>Cargando informe del mercado…</h1>
          <p>Consultando la última exportación de Python.</p>
        </section>
      </main>
    );
  }

  return (
    <>
      <MarketDashboardSections marketData={marketData} />
      <PortfolioView marketData={marketData} />
    </>
  );
}
