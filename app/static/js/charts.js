import { $, request, toast } from "./api.js";
let chart, candles, volume, average, resizeObserver;
let loadSequence = 0;
function token(name) { return getComputedStyle(document.body).getPropertyValue(name).trim(); }
export function createChart() {
  const host = $("#price-chart");
  if (!host || !window.LightweightCharts) return;
  host.innerHTML = "";
  chart = LightweightCharts.createChart(host, {
    width: Math.max(300, host.clientWidth), height: Math.max(260, host.clientHeight),
    layout: { background: { color: "transparent" }, textColor: token("--muted"), fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
    grid: { vertLines: { color: token("--line") }, horzLines: { color: token("--line") } },
    rightPriceScale: { borderColor: token("--line"), scaleMargins: { top: 0.08, bottom: 0.2 } },
    timeScale: { borderColor: token("--line"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal }, handleScroll: true, handleScale: true,
  });
  candles = chart.addCandlestickSeries({ upColor: token("--up"), downColor: token("--down"), borderVisible: false, wickUpColor: token("--up"), wickDownColor: token("--down") });
  volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  average = chart.addLineSeries({ color: token("--accent"), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  resizeObserver?.disconnect();
  resizeObserver = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect;
    if (rect && chart) chart.resize(Math.max(300, Math.floor(rect.width)), Math.max(260, Math.floor(rect.height)));
  });
  resizeObserver.observe(host); applyChartTheme();
}
export function applyChartTheme() {
  if (!chart) return;
  chart.applyOptions({
    layout: { background: { color: "transparent" }, textColor: token("--muted") },
    grid: { vertLines: { color: token("--line") }, horzLines: { color: token("--line") } },
    rightPriceScale: { borderColor: token("--line") }, timeScale: { borderColor: token("--line") },
  });
  candles.applyOptions({ upColor: token("--up"), downColor: token("--down"), wickUpColor: token("--up"), wickDownColor: token("--down") });
  average.applyOptions({ color: token("--accent") });
}
export async function loadChart(symbol, range, smaPeriod) {
  if (!chart || !symbol) return;
  const sequence = ++loadSequence; $("#chart-loading").hidden = false;
  try {
    const response = await request("/api/history?symbol=" + encodeURIComponent(symbol) + "&range=" + encodeURIComponent(range));
    if (!response.ok) throw new Error("Unable to load price history.");
    const rows = await response.json(); if (sequence !== loadSequence) return;
    candles.setData(rows.map(row => ({ time: row.time, open: row.open, high: row.high, low: row.low, close: row.close })));
    volume.setData(rows.map(row => ({ time: row.time, value: row.volume || 0, color: row.close >= row.open ? token("--up") + "55" : token("--down") + "55" })));
    const period = Number(smaPeriod), points = []; let sum = 0;
    if (period > 0) rows.forEach((row, index) => {
      sum += row.close; if (index >= period) sum -= rows[index - period].close;
      if (index >= period - 1) points.push({ time: row.time, value: sum / period });
    });
    average.setData(points);
    if (rows.length) {
      chart.timeScale().fitContent(); const last = rows[rows.length - 1];
      $("#chart-updated").textContent = "Last candle: " + (typeof last.time === "number" ? new Date(last.time * 1000).toLocaleString() : last.time);
    } else $("#chart-updated").textContent = "No historical data returned";
  } catch (error) {
    if (sequence === loadSequence && error.message !== "Your session has expired.") toast(error.message || "Unable to load chart.", "error");
  } finally { if (sequence === loadSequence) $("#chart-loading").hidden = true; }
}
export function fullscreenChart() {
  const panel = $("#price-chart");
  if (document.fullscreenElement) document.exitFullscreen?.();
  else panel?.requestFullscreen?.();
}
