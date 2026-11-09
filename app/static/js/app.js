import { $, $$, request, jsonRequest, escapeHtml, price, number, percent, changeClass, toast } from "./api.js";
import { createChart, loadChart, applyChartTheme, fullscreenChart } from "./charts.js";

let settings = {};
let groups = [];
let selected = null;
let targetGroup = "";
let currentRange = "1Y";
let activeHeatGroup = 0;
let isAdmin = false;
let refreshTimer;
let searchTimer;
let searchSequence = 0;
let pendingConfirm = null;

function allAssets() {
  const seen = new Set();
  return groups.flatMap(group => group.items || []).filter(item => {
    if (seen.has(item.symbol)) return false;
    seen.add(item.symbol); return true;
  });
}
function applyAppearance() {
  const theme = settings.theme === "auto"
    ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
    : settings.theme;
  document.body.dataset.theme = theme || "dark";
  document.body.dataset.style = settings.style || "hybrid";
  document.body.dataset.density = settings.density || "balanced";
  document.body.dataset.accent = settings.accent || "mint";
  applyChartTheme();
  drawHeatmaps();
}
function setView(name) {
  $$(".view").forEach(view => view.classList.toggle("active", view.id === "view-" + name));
  $$(".nav-item,.mobile-nav [data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === name));
  if (name === "heat") drawHeatmaps();
}
function renderWatchlists() {
  const host = $("#watchlists");
  if (!groups.length) {
    host.innerHTML = '<div class="empty-state">Your watchlists are empty.<br>Search for an asset to get started.</div>';
    return;
  }
  host.innerHTML = groups.map(group => {
    const isTarget = group.name === targetGroup;
    const rows = (group.items || []).map(item => {
      const change = item.price == null ? "—" : percent(item.pct);
      return '<div class="watch-symbol ' + (selected?.symbol === item.symbol ? "selected" : "") + '" data-symbol="' + escapeHtml(item.symbol) + '" tabindex="0" role="button">' +
        '<span><b>' + escapeHtml(item.symbol) + '</b><small>' + escapeHtml(item.name) + '</small></span>' +
        '<span class="watch-quote">' + price(item.price) + '<small class="' + changeClass(item.pct) + '">' + change + '</small></span>' +
        '<button class="remove-symbol" data-remove="' + escapeHtml(item.symbol) + '" title="Remove ' + escapeHtml(item.symbol) + '" aria-label="Remove ' + escapeHtml(item.symbol) + '">×</button></div>';
    }).join("");
    return '<section class="watch-group"><div class="watch-group-title ' + (isTarget ? "target" : "") + '" data-group="' + escapeHtml(group.name) + '" role="button" tabindex="0"><span>' + escapeHtml(group.name) + '</span><span>' + (group.items || []).length + '</span></div>' + rows + '</section>';
  }).join("");
}
function renderSummary() {
  const assets = allAssets();
  const gaining = assets.filter(item => Number(item.pct) > 0).length;
  const declining = assets.filter(item => Number(item.pct) < 0).length;
  $("#summary-cards").innerHTML = [
    ["WATCHLIST ASSETS", assets.length, "Across your collections"],
    ["GAINING", gaining, "Positive daily change"],
    ["DECLINING", declining, "Negative daily change"],
    ["WATCHLISTS", groups.length, "Your asset collections"],
  ].map(card => '<article class="summary-card"><span class="card-label">' + card[0] + '</span><strong>' + card[1] + '</strong><small>' + card[2] + '</small></article>').join("");

  const tableRows = assets.slice(0, 12).map(item =>
    '<tr><td><div class="asset-cell"><button class="asset-link" data-symbol="' + escapeHtml(item.symbol) + '">' + escapeHtml(item.symbol) + '</button><small>' + escapeHtml(item.name) + '</small></div></td>' +
    '<td>' + price(item.price) + '</td><td class="' + changeClass(item.pct) + '">' + percent(item.pct) + '</td></tr>'
  ).join("");
  $("#overview-table").innerHTML = assets.length
    ? '<table><thead><tr><th>ASSET</th><th>PRICE</th><th>CHANGE</th></tr></thead><tbody>' + tableRows + '</tbody></table>'
    : '<div class="empty-state">No assets yet. Search above to add your first symbol.</div>';

  const sorted = assets.filter(item => item.price != null && Number.isFinite(Number(item.pct))).sort((a, b) => b.pct - a.pct);
  const movers = [...sorted.slice(0, 3), ...sorted.slice(-3).reverse()].filter((item, index, list) => list.findIndex(x => x.symbol === item.symbol) === index);
  $("#movers").innerHTML = movers.length ? movers.map(item =>
    '<div class="mover-row"><div><b>' + escapeHtml(item.symbol) + '</b><small>' + escapeHtml(item.name) + '</small></div><span class="mover-change ' + changeClass(item.pct) + '">' + percent(item.pct) + '</span></div>'
  ).join("") : '<div class="empty-state">Quotes will appear here when available.</div>';
}
function renderChartHeader() {
  $("#chart-symbol").textContent = selected?.symbol || "Select an asset";
  $("#chart-name").textContent = selected?.name || "Choose an instrument from your watchlists or search above.";
  $("#chart-price").textContent = price(selected?.price);
  $("#chart-change").textContent = selected ? percent(selected.pct) : "—";
  $("#chart-change").className = selected ? changeClass(selected.pct) : "muted";
  $("#selected-name").textContent = selected?.name || "—";
  $("#selected-symbol").textContent = selected?.symbol || "—";
  $("#selected-change").textContent = selected ? percent(selected.pct) : "—";
  $("#selected-change").className = selected ? changeClass(selected.pct) : "";
  $("#chart-source").textContent = selected ? "Source: " + (selected.source || "Yahoo Finance") : "Source: Yahoo Finance";
  $("#chart-updated").textContent = selected?.as_of ? "Quote date: " + new Date(selected.as_of).toLocaleString() : "Quote timestamp unavailable";
}
function colorScale() {
  const styles = getComputedStyle(document.body);
  const limit = Number(settings.heat || 3);
  return d3.scaleLinear().domain([-limit, 0, limit]).range([
    styles.getPropertyValue("--down").trim(), styles.getPropertyValue("--faint").trim(), styles.getPropertyValue("--up").trim()
  ]).clamp(true);
}
function drawMiniHeat() {
  const host = $("#overview-heat");
  const assets = allAssets().filter(item => item.price != null);
  if (!assets.length) { host.innerHTML = '<div class="empty-state">Add assets to see their performance here.</div>'; return; }
  const color = colorScale();
  host.innerHTML = assets.slice(0, 18).map(item =>
    '<div class="mini-tile" style="background:' + color(Number(item.pct) || 0) + '" data-symbol="' + escapeHtml(item.symbol) + '" role="button" tabindex="0"><b>' + escapeHtml(item.symbol) + '</b><small>' + percent(item.pct) + '</small></div>'
  ).join("");
}
function drawFullHeat() {
  const host = $("#full-heat");
  const group = groups[activeHeatGroup];
  if (!group || !group.items?.length) {
    host.innerHTML = '<div class="chart-empty">No assets in this watchlist yet.</div>'; return;
  }
  const items = group.items.filter(item => item.price != null);
  if (!items.length) { host.innerHTML = '<div class="chart-empty">No quotes are currently available for this watchlist.</div>'; return; }
  const width = host.clientWidth, height = host.clientHeight;
  if (!width || !height) return;
  const weighted = items.every(item => Number(item.w) > 0);
  const root = d3.hierarchy({ children: items.map(item => ({ ...item, weight: weighted ? item.w : 1 })) }).sum(item => item.weight || 0);
  d3.treemap().size([width, height]).paddingInner(4).round(true)(root);
  const color = colorScale();
  host.innerHTML = root.leaves().map(leaf => {
    const item = leaf.data, w = leaf.x1 - leaf.x0, h = leaf.y1 - leaf.y0;
    return '<div class="heat-tile" data-symbol="' + escapeHtml(item.symbol) + '" role="button" tabindex="0" style="left:' + leaf.x0 + 'px;top:' + leaf.y0 + 'px;width:' + w + 'px;height:' + h + 'px;background:' + color(Number(item.pct) || 0) + '">' +
      (w > 48 && h > 35 ? '<b>' + escapeHtml(item.symbol) + '</b><small>' + percent(item.pct) + '</small>' : "") + '</div>';
  }).join("");
}
function drawHeatmaps() {
  if (!window.d3) return;
  drawMiniHeat();
  const tabs = $("#heat-groups");
  tabs.innerHTML = groups.map((group, index) =>
    '<button data-heat-group="' + index + '" class="' + (index === activeHeatGroup ? "selected" : "") + '">' + escapeHtml(group.name) + '</button>'
  ).join("");
  $("#heat-caption").textContent = groups[activeHeatGroup]?.name || "No watchlists";
  drawFullHeat();
}
function updateLastUpdated() {
  $("#last-updated").textContent = "Updated " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function renderAll() {
  renderWatchlists(); renderSummary(); renderChartHeader(); drawHeatmaps();
}
async function refreshPrices() {
  try {
    const response = await request("/api/markets");
    if (!response.ok) throw new Error("Market data request failed.");
    const nextGroups = await response.json();
    const previousSymbol = selected?.symbol;
    groups = nextGroups;
    if (!targetGroup || !groups.some(group => group.name === targetGroup)) targetGroup = groups[0]?.name || "";
    const nextSelected = allAssets().find(item => item.symbol === previousSymbol) || allAssets()[0] || null;
    const changed = nextSelected?.symbol !== previousSymbol;
    selected = nextSelected;
    activeHeatGroup = Math.min(activeHeatGroup, Math.max(0, groups.length - 1));
    renderAll(); updateLastUpdated();
    if (changed && selected) await loadChart(selected.symbol, currentRange, settings.sma);
  } catch (error) {
    if (error.message !== "Your session has expired.") toast(error.message || "Unable to refresh market data.", "error");
  }
}
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    if (!document.hidden) await refreshPrices();
    scheduleRefresh();
  }, Number(settings.refresh || 60) * 1000);
}
async function selectAsset(symbol) {
  selected = allAssets().find(item => item.symbol === symbol) || null;
  renderWatchlists(); renderChartHeader(); setView("chart");
  if (selected) await loadChart(selected.symbol, currentRange, settings.sma);
}
function openConfirm(message, action) {
  pendingConfirm = action;
  $("#confirm-message").textContent = message;
  $("#confirm-dialog").showModal();
}
async function addAsset(symbol, name) {
  const group = targetGroup || groups[0]?.name || "My Watchlist";
  try {
    const response = await request("/api/watchlist", jsonRequest("POST", { group, symbol, name }));
    if (!response.ok) {
      const body = await response.json(); throw new Error(body.detail || "Unable to add this symbol.");
    }
    $("#search").value = ""; $("#search-results").hidden = true;
    await refreshPrices();
    const added = allAssets().find(item => item.symbol === symbol);
    if (added) await selectAsset(symbol);
    else toast("Added " + symbol + ". A quote is not available yet.");
  } catch (error) { toast(error.message, "error"); }
}
async function removeAsset(symbol) {
  try {
    const response = await request("/api/watchlist/" + encodeURIComponent(symbol), { method: "DELETE" });
    if (!response.ok) throw new Error("Unable to remove " + symbol + ".");
    if (selected?.symbol === symbol) selected = null;
    await refreshPrices();
    if (!selected) { renderChartHeader(); $("#price-chart").innerHTML = '<div class="chart-empty">Select an asset to load its price history.</div>'; }
    toast(symbol + " removed from your watchlists.");
  } catch (error) { toast(error.message, "error"); }
}
async function runSearch() {
  const query = $("#search").value.trim(), sequence = ++searchSequence;
  if (query.length < 2) { $("#search-results").hidden = true; return; }
  try {
    const response = await request("/api/search?q=" + encodeURIComponent(query));
    if (!response.ok) throw new Error("Search is unavailable.");
    const results = await response.json();
    if (sequence !== searchSequence || query !== $("#search").value.trim()) return;
    $("#search-results").innerHTML = results.length ? results.map(item =>
      '<button class="search-result" data-add-symbol="' + escapeHtml(item.symbol) + '" data-add-name="' + escapeHtml(item.name) + '"><span><b>' + escapeHtml(item.symbol) + '</b><small>' + escapeHtml(item.name) + (item.exch ? " · " + escapeHtml(item.exch) : "") + '</small></span><span class="search-add">＋ Add</span></button>'
    ).join("") : '<div class="empty-state">No matching assets found.</div>';
    $("#search-results").hidden = false;
  } catch (error) { if (error.message !== "Your session has expired.") toast(error.message, "error"); }
}
function changeRange(range) {
  currentRange = range;
  $$("#range-controls button").forEach(button => button.classList.toggle("selected", button.dataset.range === range));
  if (selected) loadChart(selected.symbol, currentRange, settings.sma);
}
function syncControls() {
  $("#chart-sma").value = String(settings.sma);
  $("#heat-scale").value = String(settings.heat);
  $$("[data-setting]").forEach(input => input.value = settings[input.dataset.setting]);
}
async function saveSetting(key, rawValue) {
  const value = ["refresh", "sma", "heat"].includes(key) ? Number(rawValue) : rawValue;
  try {
    const response = await request("/api/settings", jsonRequest("PUT", { [key]: value }));
    if (!response.ok) throw new Error("Could not save this preference.");
    settings = await response.json(); syncControls(); applyAppearance();
    if (key === "range" && settings.range !== currentRange) changeRange(settings.range);
    if (key === "sma" && selected) loadChart(selected.symbol, currentRange, settings.sma);
    if (key === "refresh") scheduleRefresh();
  } catch (error) { if (error.message !== "Your session has expired.") toast(error.message, "error"); }
}
async function loadUsers() {
  const response = await request("/api/users");
  if (!response.ok) return;
  const users = await response.json();
  $("#user-list").innerHTML = users.map(user =>
    '<div class="user-row"><span>' + escapeHtml(user.name) + (user.admin ? " · admin" : "") + '</span>' +
    (user.admin ? "" : '<button class="text-button" data-delete-user="' + escapeHtml(user.name) + '">Remove</button>') + '</div>'
  ).join("");
}
function bindEvents() {
  document.addEventListener("click", async event => {
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) { setView(viewButton.dataset.view); return; }
    const go = event.target.closest("[data-go]");
    if (go) { setView(go.dataset.go); return; }
    const group = event.target.closest("[data-group]");
    if (group) { targetGroup = group.dataset.group; renderWatchlists(); return; }
    const remove = event.target.closest("[data-remove]");
    if (remove) { event.stopPropagation(); openConfirm("Remove " + remove.dataset.remove + " from your watchlists?", () => removeAsset(remove.dataset.remove)); return; }
    const add = event.target.closest("[data-add-symbol]");
    if (add) { await addAsset(add.dataset.addSymbol, add.dataset.addName); return; }
    const heatGroup = event.target.closest("[data-heat-group]");
    if (heatGroup) { activeHeatGroup = Number(heatGroup.dataset.heatGroup); drawHeatmaps(); return; }
    const asset = event.target.closest("[data-symbol]");
    if (asset) { await selectAsset(asset.dataset.symbol); return; }
    const deleteUser = event.target.closest("[data-delete-user]");
    if (deleteUser) openConfirm("Remove user " + deleteUser.dataset.deleteUser + " and their watchlists?", async () => {
      const response = await request("/api/users/" + encodeURIComponent(deleteUser.dataset.deleteUser), { method: "DELETE" });
      if (!response.ok) throw new Error("Unable to remove user.");
      await loadUsers(); toast("User removed.");
    });
  });
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#search").focus(); }
    if ((event.key === "Enter" || event.key === " ") && event.target.matches('[role="button"]')) { event.preventDefault(); event.target.click(); }
  });
  $("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 280); });
  $("#search").addEventListener("focus", () => { if ($("#search").value.trim().length >= 2) runSearch(); });
  document.addEventListener("click", event => { if (!event.target.closest(".search-wrap")) $("#search-results").hidden = true; });
  $("#settings-open").addEventListener("click", async () => {
    syncControls(); $("#settings-dialog").showModal();
    if (isAdmin) await loadUsers();
  });
  $$("[data-setting]").forEach(input => input.addEventListener("change", () => saveSetting(input.dataset.setting, input.value)));
  $("#logout").addEventListener("click", async () => { await request("/api/logout", { method: "POST" }); location.href = "/login"; });
  $("#password-change").addEventListener("click", async () => {
    const message = $("#password-message"); message.textContent = "";
    try {
      const response = await request("/api/password", jsonRequest("POST", { current: $("#current-password").value, new: $("#new-password").value }));
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Password update failed.");
      message.textContent = "Password changed successfully.";
      $("#current-password").value = ""; $("#new-password").value = "";
    } catch (error) { message.textContent = error.message; }
  });
  $("#user-add").addEventListener("click", async () => {
    const message = $("#user-message"); message.textContent = "";
    try {
      const response = await request("/api/users", jsonRequest("POST", { username: $("#new-username").value, password: $("#new-user-password").value }));
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not add user.");
      $("#new-username").value = ""; $("#new-user-password").value = "";
      message.textContent = "User added."; await loadUsers();
    } catch (error) { message.textContent = error.message; }
  });
  $("#new-group").addEventListener("click", () => { $("#group-name").value = ""; $("#group-dialog").showModal(); $("#group-name").focus(); });
  $("#group-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = $("#group-name").value.trim();
    if (!name) return;
    targetGroup = name; $("#group-dialog").close(); $("#search").focus();
    toast("New watchlist selected. Add an asset using search.");
    renderWatchlists();
  });
  $$("[data-close-group]").forEach(button => button.addEventListener("click", () => $("#group-dialog").close()));
  $("#confirm-cancel").addEventListener("click", () => { pendingConfirm = null; $("#confirm-dialog").close(); });
  $("#confirm-accept").addEventListener("click", async () => {
    const action = pendingConfirm; pendingConfirm = null; $("#confirm-dialog").close();
    if (action) try { await action(); } catch (error) { toast(error.message, "error"); }
  });
  $("#range-controls").addEventListener("click", event => {
    const button = event.target.closest("[data-range]"); if (!button) return;
    changeRange(button.dataset.range); saveSetting("range", currentRange);
  });
  $("#chart-sma").addEventListener("change", event => saveSetting("sma", event.target.value));
  $("#chart-fullscreen").addEventListener("click", fullscreenChart);
  $("#heat-scale").addEventListener("change", event => saveSetting("heat", event.target.value));
  window.addEventListener("resize", () => { if (document.querySelector("#view-heat.active")) drawFullHeat(); });
  matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => { if (settings.theme === "auto") applyAppearance(); });
}
async function init() {
  bindEvents();
  try {
    const response = await request("/api/me");
    if (!response.ok) throw new Error("Unable to load your account.");
    const me = await response.json();
    settings = me.settings; isAdmin = me.admin;
    $("#account-name").textContent = me.user; $("#account-name").dataset.name = me.user;
    $("#admin-panel").hidden = !isAdmin;
    currentRange = settings.range || "1Y";
    $$("#range-controls button").forEach(button => button.classList.toggle("selected", button.dataset.range === currentRange));
    syncControls();
    applyAppearance(); createChart();
    await refreshPrices(); setView("overview"); scheduleRefresh();
  } catch (error) { if (error.message !== "Your session has expired.") toast(error.message, "error"); }
}
init();
