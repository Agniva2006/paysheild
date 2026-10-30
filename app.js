const API = "/api/v1";
const $ = (selector) => document.querySelector(selector);
let token = localStorage.getItem("payshield_access_token");
let authMode = "login";
let currentAssessment = null;
let modelPolling = null;

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${API}${path}`, { ...options, headers });
  } catch {
    throw new Error("The PayShield server could not be reached. Check that it is running.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status}).`);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function showAuth(mode) {
  authMode = mode;
  document.querySelectorAll("[data-auth-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.authMode === mode);
  });
  $("#authSubmit").textContent = mode === "login" ? "Sign in" : "Create account";
  $("#authPassword").autocomplete = mode === "login" ? "current-password" : "new-password";
  $("#authMessage").textContent = "";
}

document.querySelectorAll("[data-auth-mode]").forEach((button) => {
  button.addEventListener("click", () => showAuth(button.dataset.authMode));
});

$("#authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = $("#authEmail").value.trim().toLowerCase();
  const password = $("#authPassword").value;
  const message = $("#authMessage");
  const submit = $("#authSubmit");
  message.textContent = "";
  if (!email || password.length < 8) {
    message.textContent = "Enter a valid email and a password of at least 8 characters.";
    return;
  }
  submit.disabled = true;
  submit.textContent = authMode === "login" ? "Signing in…" : "Creating account…";
  try {
    const data = await api(`/auth/${authMode === "login" ? "login" : "register"}`, {
      method: "POST", body: JSON.stringify({ email, password }),
    });
    token = data.access_token;
    localStorage.setItem("payshield_access_token", token);
    openWorkspace(data.user);
  } catch (error) {
    if (error.message.includes("already exists")) {
      message.textContent = "That email already has an account. Select Sign in.";
    } else if (error.message.includes("incorrect")) {
      message.textContent = "Email or password is incorrect.";
    } else {
      message.textContent = error.message;
    }
  } finally {
    submit.disabled = false;
    submit.textContent = authMode === "login" ? "Sign in" : "Create account";
  }
});

function openWorkspace(user) {
  $("#authView").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#accountBar").classList.remove("hidden");
  $("#accountEmail").textContent = user.email;
  $("#planBadge").textContent = (user.plan || "free").toUpperCase();
  $("#subscribeButton").classList.toggle("hidden", user.plan === "premium");
  $("#portalButton").classList.toggle("hidden", user.plan !== "premium");
  $("#planText").textContent = user.plan === "premium"
    ? "Premium: view up to 100 recent assessments."
    : "Free plan: view the latest 5 assessments. Upgrade from the top bar for extended history.";
  loadHealth();
  loadHistory();
  loadSubscription();
}

async function loadHealth() {
  const status = $("#serviceStatus");
  try {
    const data = await api("/health");
    status.classList.toggle("ready", data.database_ready && data.transaction_model_ready);
    status.classList.toggle("offline", !data.database_ready);
    status.querySelector("b").textContent = data.transaction_model_ready
      ? "API, database and IEEE-CIS model ready"
      : "API ready; model unavailable";
  } catch (error) {
    status.classList.add("offline");
    status.querySelector("b").textContent = error.message;
  }
}

$("#logoutButton").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* local session is cleared either way */ }
  localStorage.removeItem("payshield_access_token");
  token = null;
  window.location.reload();
});

$("#urlForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#assessButton");
  $("#urlMessage").className = "message";
  $("#urlMessage").textContent = "Checking URL, DNS, HTTPS and available threat intelligence…";
  button.disabled = true;
  try {
    const data = await api("/assessments/url", {
      method: "POST", body: JSON.stringify({ url: $("#paymentUrl").value.trim() }),
    });
    currentAssessment = data;
    $("#urlMessage").textContent = "Assessment complete.";
    $("#urlMessage").className = "message success-message";
    renderAssessment(data);
    watchMerchantModel(data.assessment_id);
    loadHistory();
  } catch (error) {
    $("#urlMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

function renderAssessment(data) {
  const result = data.url_assessment;
  const level = result.risk_level === "INDETERMINATE" ? "review" : result.risk_level.toLowerCase();
  const breakdown = Object.entries(result.score_breakdown)
    .map(([name, points]) => `<div class="signal"><span>${escapeHtml(name.replaceAll("_", " "))}</span><b>${points}</b></div>`).join("");
  const coverage = result.evidence.coverage;
  const signals = result.signals.map((signal) =>
    `<div class="signal ${escapeHtml(signal.severity)}"><span class="signal-mark">●</span><span>${escapeHtml(signal.text)}</span></div>`
  ).join("");
  const recommendations = data.recommendations.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const reports = data.merchant_reports.length
    ? data.merchant_reports.map((report) => `<div class="signal"><span class="signal-mark">●</span><span>${escapeHtml(report.category)}: ${escapeHtml(report.note)} <small>(unverified report)</small></span></div>`).join("")
    : `<p class="help-text">No user reports are recorded for this domain.</p>`;
  $("#websiteResult").innerHTML = `
    <div class="result-header"><div><p class="eyebrow">WEBSITE SIGNAL SCORE</p><div class="risk-score">${result.risk_score}<small>out of 100 · ${escapeHtml(result.score_type.replaceAll("_", " "))}</small></div></div><span class="risk-pill ${level}">${escapeHtml(result.risk_level)}</span></div>
    <p class="model-intro">${escapeHtml(result.score_explanation)}</p>
    <div class="result-section"><h3>SCORE BREAKDOWN</h3>${breakdown}<p class="help-text">Check coverage: ${coverage.checks_completed}/${coverage.checks_expected} · ${escapeHtml(coverage.level)}. ${escapeHtml(coverage.note)}</p></div>
    <div class="result-section"><h3>WHAT WE FOUND · ${escapeHtml(result.domain)}</h3>${signals}<p class="help-text">${escapeHtml(result.reputation_status)}</p></div>
    <div class="result-section"><h3>THREE NEXT STEPS</h3><ol class="recommendations">${recommendations}</ol></div>
    <div class="result-section"><h3>COMMUNITY REPORTS</h3>${reports}<details class="report-details"><summary>Submit a merchant report</summary><form id="reportForm" class="report-form"><select id="reportCategory"><option value="payment_concern">Payment concern</option><option value="delivery_issue">Delivery issue</option><option value="impersonation">Possible impersonation</option><option value="other">Other</option></select><textarea id="reportNote" minlength="5" maxlength="500" required placeholder="Describe what happened"></textarea><button class="button button-small button-quiet" type="submit">Submit report</button><span id="reportStatus" class="form-status"></span></form></details></div>`;
  $("#assessmentResults").classList.remove("hidden");
  renderModelForm(data);
  bindReportForm(result.domain);
}

function renderModelForm(data) {
  const model = data.transaction_model;
  $("#modelResult").innerHTML = `
    <p class="eyebrow">IEEE-CIS TRANSACTION MODEL</p><h2 class="result-title">Score the transaction</h2>
    <p class="model-intro">The URL check does not contain transaction data. Enter the facts you know to run the IEEE-CIS LightGBM ensemble. No card number or email is requested. Other dataset fields stay missing, so this score has less evidence than a merchant integration.</p>
    <form id="transactionForm" class="model-form">
      <div class="model-grid">
        <label>Payment amount<input id="amount" type="number" min="0.01" step="0.01" required placeholder="125.00"></label>
        <label>Transaction category<select id="product" required><option value="" disabled selected>Select if known</option><option value="W">Online purchase</option><option value="C">Card</option><option value="H">Healthcare</option><option value="R">Travel</option><option value="S">Other</option></select></label>
        <label>Card network<select id="cardNetwork" required><option value="" disabled selected>Select if known</option><option value="visa">Visa</option><option value="mastercard">Mastercard</option><option value="discover">Discover</option><option value="american express">American Express</option></select></label>
        <label>Card type<select id="cardType" required><option value="" disabled selected>Select if known</option><option value="credit">Credit</option><option value="debit">Debit</option></select></label>
        <label>Device type<select id="deviceType" required><option value="" disabled selected>Select if known</option><option value="desktop">Desktop</option><option value="mobile">Mobile</option></select></label>
      </div>
    <p class="help-text">This is only a small subset of IEEE-CIS fields. The full model expects hundreds of transaction, device, identity and history fields. Do not enter card numbers or passwords.</p>
      <button id="scoreButton" class="button button-primary button-wide" type="submit">Run IEEE-CIS model</button>
      <p id="modelMessage" class="message" role="status">${escapeHtml(model.message)}</p>
    </form>
    <div id="modelOutput"></div>`;
  $("#transactionForm").addEventListener("submit", submitModelScore);
}

async function submitModelScore(event) {
  event.preventDefault();
  if (!currentAssessment) return;
  const button = $("#scoreButton");
  const message = $("#modelMessage");
  message.className = "message";
  message.textContent = "Running the IEEE-CIS ensemble on the entered transaction fields…";
  button.disabled = true;
  const transaction = {
    TransactionAmt: Number($("#amount").value),
    ProductCD: $("#product").value,
    card4: $("#cardNetwork").value,
    card6: $("#cardType").value,
    DeviceType: $("#deviceType").value,
  };
  try {
    const data = await api(`/assessments/${encodeURIComponent(currentAssessment.assessment_id)}/transaction-score`, {
      method: "POST", body: JSON.stringify(transaction),
    });
    currentAssessment.transaction_model = data.transaction_model;
    clearInterval(modelPolling);
    renderModelResult(data.transaction_model);
    message.textContent = "Model assessment complete and saved to your history.";
    message.className = "message success-message";
    loadHistory();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function watchMerchantModel(assessmentId) {
  clearInterval(modelPolling);
  let checks = 0;
  modelPolling = setInterval(async () => {
    checks += 1;
    try {
      const saved = await api(`/assessments/${encodeURIComponent(assessmentId)}`);
      if (saved.transaction_result && currentAssessment?.assessment_id === assessmentId) {
        if (!$("#modelOutput").innerHTML.trim()) {
          renderModelResult(saved.transaction_result);
          $("#modelMessage").textContent = "A merchant integration sent this transaction to the IEEE-CIS model. Result received and saved.";
          $("#modelMessage").className = "message success-message";
        }
        clearInterval(modelPolling);
        loadHistory();
      }
    } catch { /* the current assessment view remains usable if a poll is interrupted */ }
    if (checks >= 30) clearInterval(modelPolling);
  }, 2000);
}

function renderModelResult(result) {
  const output = $("#modelOutput");
  const displayAction = result.recommended_action || result.action;
  const factors = (result.risk_factors || []).map((factor) => `<li>${escapeHtml(factor)}</li>`).join("");
  output.innerHTML = `<div class="model-result-output"><div class="result-header"><div><p class="eyebrow">RAW IEEE-CIS MODEL SCORE</p><div class="risk-score">${Number(result.fraud_risk_score).toFixed(4)}<small>model output from 0 to 1 · not a calibrated probability</small></div></div><span class="decision ${escapeHtml(displayAction)}">${escapeHtml(displayAction)}</span></div><p class="model-intro">${escapeHtml(result.input_quality_note || "")}</p><p class="model-intro">${escapeHtml(result.action_reason || "")}</p>${displayAction !== result.action ? `<p class="help-text">Raw policy output: ${escapeHtml(result.action)}. For partial manual input, use review instead of automatic approval or blocking.</p>` : ""}<ul class="risk-factors">${factors}</ul><p class="help-text">${Number(result.provided_feature_count || 0)} of ${Number(result.expected_feature_count || 0)} model fields supplied (${(Number(result.input_coverage || 0) * 100).toFixed(1)}%). ${escapeHtml(result.model_input_note || "")}</p></div>`;
}

function bindReportForm(domain) {
  const form = $("#reportForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = $("#reportStatus");
    try {
      await api("/merchant-reports", {
        method: "POST",
        body: JSON.stringify({ domain, category: $("#reportCategory").value, note: $("#reportNote").value.trim() }),
      });
      status.textContent = "Report received. It is unverified.";
      $("#reportNote").value = "";
    } catch (error) {
      status.textContent = error.message;
    }
  });
}

async function loadHistory() {
  try {
    const data = await api("/assessments/history");
    $("#historyList").innerHTML = data.items.length
      ? data.items.map((item) => `<div class="history-row"><span><b>${escapeHtml(item.domain)}</b><small>${escapeHtml(item.created_at)}</small></span><span class="risk-pill ${item.risk_level === "INDETERMINATE" ? "review" : escapeHtml(item.risk_level.toLowerCase())}">${escapeHtml(item.risk_level)} · ${item.risk_score}/100</span><small>${item.transaction_result ? `Model ${Number(item.transaction_result.fraud_risk_score).toFixed(4)} · ${escapeHtml(item.transaction_result.recommended_action || item.transaction_result.action)}` : "Transaction model not run"}</small></div>`).join("")
      : `<p class="help-text">Your assessment history will appear here.</p>`;
  } catch (error) {
    $("#historyList").innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
  }
}

async function loadSubscription() {
  try {
    const data = await api("/billing/subscription");
    $("#planBadge").textContent = data.plan.toUpperCase();
    $("#subscribeButton").classList.toggle("hidden", data.plan === "premium");
    $("#portalButton").classList.toggle("hidden", !data.billing_portal_available);
  } catch (error) {
    $("#billingMessage").textContent = error.message;
  }
}

$("#refreshHistory").addEventListener("click", loadHistory);
$("#subscribeButton").addEventListener("click", () => $("#planDialog").showModal());
$("#confirmSubscribe").addEventListener("click", async () => {
  const message = $("#dialogMessage");
  message.textContent = "Opening secure checkout…";
  try {
    const data = await api("/billing/checkout-session", { method: "POST" });
    window.location.assign(data.url);
  } catch (error) {
    message.textContent = error.message;
  }
});
$("#portalButton").addEventListener("click", async () => {
  try {
    const data = await api("/billing/portal-session", { method: "POST" });
    window.location.assign(data.url);
  } catch (error) {
    $("#billingMessage").textContent = error.message;
  }
});

if (token) {
  api("/auth/me").then(openWorkspace).catch(() => {
    token = null;
    localStorage.removeItem("payshield_access_token");
  });
}
