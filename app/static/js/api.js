export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}
export async function request(url, options) {
  const response = await fetch(url, options);
  if (response.status === 401 && location.pathname !== "/login") {
    location.href = "/login";
    throw new Error("Your session has expired.");
  }
  return response;
}
export const jsonRequest = (method, body) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
export function number(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export function price(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value); return number(n, n < 1 ? 4 : 2);
}
export function percent(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value); return (n >= 0 ? "+" : "") + number(n) + "%";
}
export function changeClass(value) {
  return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
}
export function toast(message, type = "info") {
  const region = $("#toast-region"); if (!region) return;
  const item = document.createElement("div");
  item.className = "toast" + (type === "error" ? " error" : "");
  item.textContent = message; region.append(item);
  window.setTimeout(() => item.remove(), 3600);
}
