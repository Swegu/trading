import { $, request, jsonRequest } from "./api.js";

let setupRequired = false;
async function init() {
  try {
    const response = await request("/api/status");
    const status = await response.json();
    setupRequired = Boolean(status.setup_required);
    if (setupRequired) {
      $("#login-eyebrow").textContent = "FIRST-TIME SETUP";
      $("#login-title").textContent = "Create your admin account";
      $("#login-description").textContent = "This account will manage your Markets installation. Choose a strong password.";
      $("#login-submit").innerHTML = 'Create administrator <span>→</span>';
      $("#password").autocomplete = "new-password";
      $("#password").minLength = 8;
    }
  } catch (error) {
    $("#login-error").textContent = "Unable to connect to Markets. Check that the application is running.";
  }
}
$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#login-submit");
  const error = $("#login-error");
  button.disabled = true; error.textContent = "";
  const payload = { username: $("#username").value.trim(), password: $("#password").value };
  try {
    const response = await request(setupRequired ? "/api/setup" : "/api/login", jsonRequest("POST", payload));
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Unable to sign in.");
    location.href = "/";
  } catch (exception) {
    error.textContent = exception.message || "Unable to sign in.";
    button.disabled = false;
  }
});
init();
