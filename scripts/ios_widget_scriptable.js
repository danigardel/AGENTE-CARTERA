// Widget de cartera para Scriptable. Lee únicamente el JSON público de Pages.
const DATA_URL = "https://danigardel.github.io/AGENTE-CARTERA/market_data.json";
const DASHBOARD_URL = "https://danigardel.github.io/AGENTE-CARTERA/";
const CACHE_FILE_NAME = "agente-cartera-market-data.json";

const COLORS = {
  background: new Color("#0b0f17"),
  panel: new Color("#111827"),
  white: new Color("#f3f4f6"),
  muted: new Color("#9ca3af"),
  green: new Color("#20d69a"),
  red: new Color("#ff647c"),
  yellow: new Color("#fbbf24"),
  divider: new Color("#263244"),
};

const fm = FileManager.local();
const cachePath = fm.joinPath(fm.documentsDirectory(), CACHE_FILE_NAME);

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatCurrency(value) {
  if (!isNumber(value)) return "—";
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPercent(value) {
  if (!isNumber(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString("es-ES", {
    maximumFractionDigits: 2,
  })}%`;
}

function movementColor(value) {
  if (!isNumber(value) || value === 0) return COLORS.muted;
  return value > 0 ? COLORS.green : COLORS.red;
}

function signalLabel(signal) {
  const labels = {
    HOLD: "MANTENER",
    WATCH: "VIGILAR",
    REBALANCE: "REBALANCEAR",
    RISK_ALERT: "ALERTA DE RIESGO",
  };
  return labels[signal] || "SIN SEÑAL";
}

function signalColor(signal) {
  if (signal === "RISK_ALERT") return COLORS.red;
  if (signal === "WATCH" || signal === "REBALANCE") return COLORS.yellow;
  return COLORS.green;
}

function addText(parent, text, size, color, bold) {
  const item = parent.addText(String(text));
  item.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
  item.textColor = color;
  item.lineLimit = 2;
  return item;
}

function getGeneratedTime(data) {
  const value = data.generated_at;
  const parsed = typeof value === "string" ? new Date(value) : null;
  if (!parsed || Number.isNaN(parsed.getTime())) return "Actualización —";
  return `Actualizado ${parsed.toLocaleTimeString("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

async function loadMarketData() {
  const request = new Request(`${DATA_URL}?t=${Date.now()}`);
  request.timeoutInterval = 15;
  try {
    const data = await request.loadJSON();
    if (!data || typeof data !== "object" || !data.portfolio) {
      throw new Error("market_data.json no tiene la estructura esperada.");
    }
    fm.writeString(cachePath, JSON.stringify(data));
    return { data, fromCache: false };
  } catch (error) {
    if (!fm.fileExists(cachePath)) throw error;
    const cachedData = JSON.parse(fm.readString(cachePath));
    if (!cachedData || typeof cachedData !== "object" || !cachedData.portfolio) {
      throw new Error("La copia local de market_data.json no es válida.");
    }
    return { data: cachedData, fromCache: true };
  }
}

function activePositions(data) {
  const positions = data.portfolio && Array.isArray(data.portfolio.positions)
    ? data.portfolio.positions
    : [];
  return positions
    .filter((position) =>
      position.quote_is_stale === false && isNumber(position.day_delta_pct)
    )
    .sort((left, right) =>
      Math.abs(right.day_delta_pct) - Math.abs(left.day_delta_pct)
    );
}

function activeAlerts(data) {
  const alerts = [];
  const atrCrossed = data.quant_summary && Array.isArray(data.quant_summary.atr_crossed)
    ? data.quant_summary.atr_crossed
    : [];
  for (const item of atrCrossed) {
    alerts.push({
      text: `ATR · ${item.ticker || item.name || "Posición"}: revisar soporte`,
      color: COLORS.red,
    });
  }

  const floors = data.price_floors && typeof data.price_floors === "object"
    ? Object.entries(data.price_floors)
    : [];
  for (const [ticker, status] of floors) {
    if (!status || typeof status !== "object") continue;
    if (status.in_extreme_zone === true) {
      alerts.push({ text: `Suelo extremo · ${ticker}`, color: COLORS.red });
    } else if (status.in_plan_zone === true) {
      alerts.push({ text: `Zona suelo · ${ticker}`, color: COLORS.yellow });
    }
  }
  return alerts;
}

function summaryLines(data, maximumLines) {
  const summary = data.analysis && typeof data.analysis.telegram_summary === "string"
    ? data.analysis.telegram_summary.trim()
    : "";
  if (!summary) return "Narrativa IA no disponible.";
  return summary.split(/\s*\n\s*/).filter(Boolean).slice(0, maximumLines).join("\n");
}

function addSignal(parent, signal) {
  const row = parent.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  const badge = row.addText(signalLabel(signal));
  badge.font = Font.boldSystemFont(9);
  badge.textColor = signalColor(signal);
  badge.lineLimit = 1;
  badge.backgroundColor = COLORS.panel;
  badge.cornerRadius = 6;
  badge.setPadding(4, 7, 4, 7);
}

function addCacheNote(parent, fromCache) {
  if (!fromCache) return;
  const label = parent.addText("SIN CONEXIÓN · DATOS DE CACHÉ");
  label.font = Font.boldSystemFont(8);
  label.textColor = COLORS.yellow;
  label.lineLimit = 1;
}

function addSmallWidget(widget, data, fromCache) {
  const totals = data.portfolio.totals || {};
  const dailyPct = totals.returns_pct && totals.returns_pct["1D"];
  const dailyEur = totals.daily_change_eur;
  const signal = data.analysis && data.analysis.action_signal;

  addText(widget, "MI CARTERA", 10, COLORS.muted, true);
  widget.addSpacer(4);
  addText(widget, formatCurrency(totals.current_value_eur), 25, COLORS.white, true);
  widget.addSpacer(4);
  const movement = widget.addStack();
  movement.layoutHorizontally();
  movement.centerAlignContent();
  addText(movement, formatCurrency(dailyEur), 13, movementColor(dailyEur), true);
  movement.addSpacer(6);
  addText(movement, formatPercent(dailyPct), 12, movementColor(dailyPct), true);
  widget.addSpacer(7);
  addSignal(widget, signal);
  widget.addSpacer();
  addCacheNote(widget, fromCache);
  addText(widget, getGeneratedTime(data), 9, COLORS.muted, false);
}

function addPositions(parent, positions, count) {
  const selected = positions.slice(0, count);
  if (!selected.length) {
    addText(parent, "Sin cotizaciones actuales", 10, COLORS.muted, false);
    return;
  }
  for (const position of selected) {
    const row = parent.addStack();
    row.layoutHorizontally();
    row.centerAlignContent();
    const title = position.ticker || position.name || "Activo";
    const titleText = row.addText(title);
    titleText.font = Font.boldSystemFont(10);
    titleText.textColor = COLORS.white;
    titleText.lineLimit = 1;
    titleText.minimumScaleFactor = 0.7;
    row.addSpacer(4);
    const change = row.addText(formatPercent(position.day_delta_pct));
    change.font = Font.boldSystemFont(10);
    change.textColor = movementColor(position.day_delta_pct);
    change.lineLimit = 1;
    parent.addSpacer(1);
    addText(
      parent,
      `${formatCurrency(position.current_price_eur)} · ${formatPercent(position.day_delta_pct)}`,
      8,
      COLORS.muted,
      false
    );
    parent.addSpacer(3);
  }
}

function addAlerts(parent, alerts, maximumCount) {
  if (!alerts.length) {
    addText(parent, "Sin alertas de suelo o soporte", 9, COLORS.muted, false);
    return;
  }
  for (const alert of alerts.slice(0, maximumCount)) {
    addText(parent, alert.text, 9, alert.color, true);
  }
}

function addMediumWidget(widget, data, fromCache) {
  const totals = data.portfolio.totals || {};
  const dailyPct = totals.returns_pct && totals.returns_pct["1D"];
  const signal = data.analysis && data.analysis.action_signal;
  const positions = activePositions(data);
  const alerts = activeAlerts(data);

  const columns = widget.addStack();
  columns.layoutHorizontally();
  columns.topAlignContent();

  const left = columns.addStack();
  left.layoutVertically();
  left.size = new Size(145, 0);
  addText(left, "CARTERA", 9, COLORS.muted, true);
  left.addSpacer(4);
  addText(left, formatCurrency(totals.current_value_eur), 19, COLORS.white, true);
  addText(left, `${formatPercent(dailyPct)} hoy`, 11, movementColor(dailyPct), true);
  left.addSpacer(6);
  addSignal(left, signal);
  left.addSpacer(5);
  addText(left, formatCurrency(totals.daily_change_eur), 10, movementColor(totals.daily_change_eur), false);
  left.addSpacer();
  addText(left, getGeneratedTime(data), 8, COLORS.muted, false);

  columns.addSpacer(10);
  const right = columns.addStack();
  right.layoutVertically();
  right.addSpacer(2);
  if (alerts.length) {
    addText(right, "AVISOS", 9, COLORS.muted, true);
    right.addSpacer(4);
    addAlerts(right, alerts, 3);
  } else {
    addText(right, "MAYORES MOVIMIENTOS", 9, COLORS.muted, true);
    right.addSpacer(4);
    addPositions(right, positions, 3);
  }

  widget.addSpacer(6);
  const summary = widget.addText(summaryLines(data, 2));
  summary.font = Font.systemFont(9);
  summary.textColor = COLORS.muted;
  summary.lineLimit = 2;
  addCacheNote(widget, fromCache);
}

function addLargeWidget(widget, data, fromCache) {
  const totals = data.portfolio.totals || {};
  const dailyPct = totals.returns_pct && totals.returns_pct["1D"];
  const maxPct = totals.returns_pct && totals.returns_pct["MAX"];
  const signal = data.analysis && data.analysis.action_signal;
  const vix = data.macro_indicators && data.macro_indicators.vix
    ? data.macro_indicators.vix.price
    : null;
  const positions = activePositions(data);
  const alerts = activeAlerts(data);

  const header = widget.addStack();
  header.layoutHorizontally();
  header.topAlignContent();

  const valueColumn = header.addStack();
  valueColumn.layoutVertically();
  addText(valueColumn, "VALOR DE CARTERA", 9, COLORS.muted, true);
  addText(valueColumn, formatCurrency(totals.current_value_eur), 22, COLORS.white, true);
  addText(valueColumn, `${formatCurrency(totals.daily_change_eur)} · ${formatPercent(dailyPct)}`, 10, movementColor(dailyPct), true);

  header.addSpacer();
  const metrics = header.addStack();
  metrics.layoutVertically();
  addText(metrics, `MAX ${formatPercent(maxPct)}`, 10, movementColor(maxPct), true);
  addText(metrics, `VIX ${isNumber(vix) ? vix.toLocaleString("es-ES", { maximumFractionDigits: 1 }) : "—"}`, 10, COLORS.muted, true);
  addSignal(metrics, signal);

  widget.addSpacer(8);
  const divider = widget.addStack();
  divider.size = new Size(0, 1);
  divider.backgroundColor = COLORS.divider;
  widget.addSpacer(7);

  addText(widget, "ANÁLISIS IA", 9, COLORS.muted, true);
  widget.addSpacer(3);
  const summary = widget.addText(summaryLines(data, 4));
  summary.font = Font.systemFont(11);
  summary.textColor = COLORS.white;
  summary.lineLimit = 4;

  widget.addSpacer(7);
  const lower = widget.addStack();
  lower.layoutHorizontally();
  lower.topAlignContent();

  const positionColumn = lower.addStack();
  positionColumn.layoutVertically();
  addText(positionColumn, "MOVIMIENTOS", 9, COLORS.muted, true);
  positionColumn.addSpacer(4);
  addPositions(positionColumn, positions, 4);

  lower.addSpacer(10);
  const alertColumn = lower.addStack();
  alertColumn.layoutVertically();
  addText(alertColumn, "SUELOS / SOPORTES", 9, COLORS.muted, true);
  alertColumn.addSpacer(4);
  addAlerts(alertColumn, alerts, 4);

  widget.addSpacer();
  const footer = widget.addStack();
  footer.layoutHorizontally();
  addCacheNote(footer, fromCache);
  footer.addSpacer();
  addText(footer, getGeneratedTime(data), 8, COLORS.muted, false);
}

async function main() {
  let loaded;
  try {
    loaded = await loadMarketData();
  } catch (error) {
    const widget = new ListWidget();
    widget.backgroundColor = COLORS.background;
    widget.setPadding(14, 14, 14, 14);
    addText(widget, "CARTERA NO DISPONIBLE", 12, COLORS.white, true);
    widget.addSpacer(6);
    addText(widget, "No hay conexión ni una copia local válida de los datos.", 10, COLORS.muted, false);
    widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
    widget.url = DASHBOARD_URL;
    if (config.runsInWidget) Script.setWidget(widget);
    else await widget.presentSmall();
    Script.complete();
    return;
  }

  const widget = new ListWidget();
  widget.backgroundColor = COLORS.background;
  widget.setPadding(13, 14, 12, 14);
  widget.url = DASHBOARD_URL;

  const family = config.widgetFamily || "small";
  if (family === "medium") addMediumWidget(widget, loaded.data, loaded.fromCache);
  else if (family === "large") addLargeWidget(widget, loaded.data, loaded.fromCache);
  else addSmallWidget(widget, loaded.data, loaded.fromCache);

  widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  if (config.runsInWidget) Script.setWidget(widget);
  else if (family === "large") await widget.presentLarge();
  else if (family === "medium") await widget.presentMedium();
  else await widget.presentSmall();
  Script.complete();
}

await main();
