const loginShell = document.querySelector("#admin-login");
const dashboard = document.querySelector("#admin-dashboard");
const loginForm = document.querySelector("#admin-login-form");
const loginButton = document.querySelector("#admin-login-button");
const loginError = document.querySelector("#admin-login-error");
const humanStatus = document.querySelector("#admin-human-status");
const summary = document.querySelector("#admin-summary");
const requestSummary = document.querySelector("#request-summary");
const filters = document.querySelector("#request-filters");
const download = document.querySelector("#statistics-download");
const pageStatus = document.querySelector("#page-status");
const analyticsPeriod = document.querySelector("#analytics-period");
const analyticsMessage = document.querySelector("#analytics-message");
const analyticsContent = document.querySelector("#analytics-content");
const analyticsDownload = document.querySelector("#analytics-download");
const openaiPeriod = document.querySelector("#openai-period");
const openaiMessage = document.querySelector("#openai-message");
const openaiContent = document.querySelector("#openai-content");
const openaiDownload = document.querySelector("#openai-download");
const PAGE_SIZE = 100;
let captchaToken = null;
let turnstileWidget = null;
let captchaRequired = true;
let pendingLogin = null;
let currentOffset = 0;
let currentData = null;

initializeAdmin();

async function initializeAdmin() {
  bindControls();
  const session = await api("/api/admin/session", {}, true);
  if (session?.authenticated) {
    showDashboard(session);
    return;
  }
  await prepareLogin();
}

function bindControls() {
  loginForm.addEventListener("submit", login);
  document.querySelector("#admin-logout").addEventListener("click", logout);
  document.querySelectorAll("[data-admin-tab]").forEach((button) => button.addEventListener("click", () => openTab(button.dataset.adminTab)));
  document.querySelectorAll("[data-open-admin-tab]").forEach((button) => button.addEventListener("click", () => openTab(button.dataset.openAdminTab)));
  filters.addEventListener("submit", (event) => { event.preventDefault(); currentOffset = 0; loadRequests(); });
  document.querySelector("#filters-reset").addEventListener("click", () => { filters.reset(); currentOffset = 0; loadRequests(); });
  document.querySelector("#page-previous").addEventListener("click", () => { currentOffset = Math.max(0, currentOffset - PAGE_SIZE); loadRequests(); });
  document.querySelector("#page-next").addEventListener("click", () => { currentOffset += PAGE_SIZE; loadRequests(); });
  document.querySelector("#logs-refresh").addEventListener("click", loadLogs);
  document.querySelector("#log-source").addEventListener("change", loadLogs);
  document.querySelector("#log-lines").addEventListener("change", loadLogs);
  document.querySelector("#analytics-refresh").addEventListener("click", () => loadAnalytics(true));
  analyticsPeriod.addEventListener("change", () => loadAnalytics());
  document.querySelector("#openai-refresh").addEventListener("click", loadOpenAIUsage);
  openaiPeriod.addEventListener("change", loadOpenAIUsage);
}

async function prepareLogin() {
  const config = await api("/api/admin/config");
  if (!config?.configured) {
    showLoginError("Skrbniški dostop še ni konfiguriran na strežniku.");
    humanStatus.textContent = "Konfiguracija je potrebna.";
    return;
  }
  if (!config.captcha_required) {
    captchaRequired = false;
    humanStatus.textContent = "Varnostno preverjanje v tem okolju ni zahtevano.";
    loginButton.disabled = false;
    return;
  }
  if (!config.captcha_configured || !config.turnstile_site_key) {
    showLoginError("Varnostno preverjanje ni konfigurirano.");
    return;
  }
  try {
    await loadTurnstile();
    turnstileWidget = window.turnstile.render("#admin-turnstile", {
      sitekey: config.turnstile_site_key,
      action: "admin_login",
      appearance: "interaction-only",
      execution: "execute",
      language: "sl",
      callback: (token) => {
        captchaToken = token;
        humanStatus.textContent = "Preverjanje je uspešno. Prijavljam …";
        humanStatus.classList.add("is-success");
        completeLogin();
      },
      "expired-callback": () => resetCaptcha("Preverjanje je poteklo. Kliknite Prijavi se za nov poskus."),
      "error-callback": () => {
        pendingLogin = null;
        resetCaptcha("Preverjanje ni uspelo. Kliknite Prijavi se in poskusite znova.");
        showLoginError("Varnostno preverjanje ni uspelo. Poskusite znova.");
      },
    });
    humanStatus.textContent = "Varnostno preverjanje se bo začelo, ko kliknete Prijavi se.";
    loginButton.disabled = false;
  } catch {
    showLoginError("Varnostnega preverjanja ni bilo mogoče naložiti.");
    humanStatus.textContent = "Osvežite stran in poskusite znova.";
  }
}

function login(event) {
  event.preventDefault();
  if (loginButton.disabled) return;
  pendingLogin = {
    username: document.querySelector("#admin-username").value,
    password: document.querySelector("#admin-password").value,
  };
  loginButton.disabled = true;
  loginButton.textContent = "Preverjam …";
  loginError.hidden = true;
  if (captchaRequired) {
    captchaToken = null;
    humanStatus.textContent = "Opravljam varnostno preverjanje …";
    humanStatus.classList.remove("is-success");
    window.turnstile.execute(turnstileWidget);
    return;
  }
  completeLogin();
}

async function completeLogin() {
  if (!pendingLogin) return;
  const credentials = pendingLogin;
  pendingLogin = null;
  const response = await api("/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: credentials.username,
      password: credentials.password,
      captcha_token: captchaToken,
    }),
  });
  if (response?.authenticated) {
    loginForm.reset();
    showDashboard(response);
    return;
  }
  showLoginError(response?.detail || "Prijava ni uspela.");
  loginButton.textContent = "Prijavi se";
  resetCaptcha("Kliknite Prijavi se za nov varnostni pregled.");
}

function resetCaptcha(message) {
  captchaToken = null;
  pendingLogin = null;
  loginButton.disabled = false;
  humanStatus.textContent = message || "Varnostno preverjanje se bo začelo ob prijavi.";
  humanStatus.classList.remove("is-success");
  if (turnstileWidget !== null && window.turnstile) window.turnstile.reset(turnstileWidget);
}

function showLoginError(message) {
  loginError.textContent = message;
  loginError.hidden = false;
}

function showDashboard(session) {
  loginShell.hidden = true;
  dashboard.hidden = false;
  document.querySelector("#admin-session-user").textContent = session.username || "admin";
  loadOverview();
}

async function logout() {
  await api("/api/admin/logout", { method: "POST" }, true);
  window.location.reload();
}

function openTab(name) {
  document.querySelectorAll("[data-admin-tab]").forEach((button) => button.classList.toggle("is-active", button.dataset.adminTab === name));
  document.querySelectorAll("[data-admin-panel]").forEach((panel) => { panel.hidden = panel.dataset.adminPanel !== name; });
  const titles = { overview: "Pregled uporabe", analytics: "Google Analytics", openai: "OpenAI poraba", requests: "Zahteve za parcele", logs: "Dnevniki aplikacije" };
  document.querySelector("#admin-page-title").textContent = titles[name] || "Administracija";
  if (name === "analytics") loadAnalytics();
  if (name === "openai") loadOpenAIUsage();
  if (name === "requests") loadRequests();
  if (name === "logs") loadLogs();
}

async function loadOverview() {
  const data = await api("/api/admin/requests?group_by=none&limit=8");
  if (!data) return;
  renderSummary(summary, data.summary);
  renderRequestRows(document.querySelector("#overview-requests"), data.requests, true);
}

function filterQuery(includePaging = true) {
  const params = new URLSearchParams();
  new FormData(filters).forEach((value, key) => { if (String(value).trim()) params.set(key, value); });
  if (includePaging) {
    params.set("limit", PAGE_SIZE);
    params.set("offset", currentOffset);
  }
  return params;
}

async function loadRequests() {
  const params = filterQuery();
  const data = await api(`/api/admin/requests?${params}`);
  if (!data) return;
  currentData = data;
  renderSummary(requestSummary, data.summary);
  renderGroups(data);
  renderRequestRows(document.querySelector("#request-rows"), data.requests, false);
  document.querySelector("#request-count").textContent = `${data.total} zahtev`;
  const start = data.total ? currentOffset + 1 : 0;
  const end = Math.min(currentOffset + PAGE_SIZE, data.total);
  pageStatus.textContent = `${start}–${end} od ${data.total}`;
  document.querySelector("#page-previous").disabled = currentOffset === 0;
  document.querySelector("#page-next").disabled = end >= data.total;
  download.href = `/api/admin/requests.csv?${filterQuery(false)}`;
}

function renderSummary(container, values) {
  const cards = [
    [values.requests, "Vse zahteve"],
    [values.unique_ips, "Različni IP-ji"],
    [values.consented_visitors, "ID-ji s soglasjem"],
    [values.completed, "Končane"],
    [values.failed, "Napake"],
    [values.active, "Aktivne"],
  ];
  container.replaceChildren(...cards.map(([value, label]) => {
    const card = document.createElement("article");
    const strong = document.createElement("strong");
    const span = document.createElement("span");
    strong.textContent = value;
    span.textContent = label;
    card.append(strong, span);
    return card;
  }));
}

function renderGroups(data) {
  const card = document.querySelector("#groups-card");
  card.hidden = data.group_by === "none";
  const body = document.querySelector("#request-groups");
  body.replaceChildren();
  data.groups.forEach((group) => body.append(tableRow([group.label, group.request_count, group.unique_ips, formatDate(group.last_request)])));
  const notes = {
    visitor: "ID obiskovalca obstaja samo ob soglasju; brez njega je vsaka zahteva ločena.",
    technical: "T-* je približek iz IP-ja in naprave, ne potrjena identiteta osebe.",
    ip: "Isti IP lahko uporablja več ljudi, posameznik pa lahko uporablja več IP-jev.",
  };
  document.querySelector("#grouping-note").textContent = notes[data.group_by] || "Skupine sledijo trenutno izbranemu kriteriju.";
}

function renderRequestRows(body, rows, compact) {
  body.replaceChildren();
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = compact ? 5 : 12;
    cell.textContent = "Za izbrane filtre ni zahtev.";
    row.append(cell);
    body.append(row);
    return;
  }
  rows.forEach((item) => {
    const status = document.createElement("span");
    status.className = `status-pill status-${item.status}`;
    status.textContent = statusLabel(item.status);
    const cells = compact
      ? [formatDate(item.requested_at), item.parcel_reference, status, deviceLabel(item), item.ip_address]
      : [
        formatDate(item.requested_at),
        item.parcel_reference,
        status,
        item.ip_address,
        item.visitor_id ? item.visitor_id.slice(0, 8) : "—",
        item.technical_group,
        analyticsDeviceLabel(item.device_type),
        `${technicalLabel(item.browser_family)} / ${technicalLabel(item.os_family)}`,
        item.accept_language || "—",
        item.referer_host || "—",
        item.analytics_consent ? `Da (${item.consent_version || "—"})` : "Ne",
        item.job_id.slice(0, 8),
      ];
    body.append(tableRow(cells));
  });
}

function tableRow(values) {
  const row = document.createElement("tr");
  values.forEach((value) => {
    const cell = document.createElement("td");
    if (value instanceof Node) cell.append(value); else cell.textContent = String(value ?? "—");
    row.append(cell);
  });
  return row;
}

async function loadAnalytics(forceRefresh = false) {
  const days = Number(analyticsPeriod.value) || 30;
  const refreshButton = document.querySelector("#analytics-refresh");
  analyticsMessage.hidden = false;
  analyticsMessage.className = "analytics-message";
  analyticsMessage.textContent = "Nalagam Google Analytics …";
  analyticsContent.hidden = true;
  refreshButton.disabled = true;
  analyticsDownload.href = `/api/admin/analytics.csv?days=${days}`;
  const query = new URLSearchParams({ days: String(days) });
  if (forceRefresh) query.set("refresh", "true");
  const data = await api(`/api/admin/analytics?${query}`);
  refreshButton.disabled = false;
  if (!data) {
    analyticsMessage.classList.add("is-error");
    analyticsMessage.textContent = "Poročila trenutno ni mogoče naložiti.";
    return;
  }
  if (data.status !== "ready") {
    analyticsMessage.classList.add(data.status === "setup_required" ? "is-setup" : "is-error");
    analyticsMessage.textContent = data.message || data.detail || "Google Analytics trenutno ni na voljo.";
    return;
  }

  analyticsMessage.hidden = true;
  analyticsContent.hidden = false;
  renderAnalyticsSummary(data.summary || {});
  renderAnalyticsDaily(data.daily || []);
  renderAnalyticsTable(
    "#analytics-channels",
    data.channels,
    (row) => [channelLabel(row.channel), formatNumber(row.sessions), formatNumber(row.total_users)],
    3,
  );
  renderAnalyticsTable(
    "#analytics-events",
    data.events,
    (row) => [eventLabel(row.event), formatNumber(row.event_count)],
    2,
  );
  renderAnalyticsTable(
    "#analytics-devices",
    data.devices,
    (row) => [analyticsDeviceLabel(row.device), formatNumber(row.total_users)],
    2,
  );
  renderAnalyticsTable(
    "#analytics-countries",
    data.countries,
    (row) => [countryLabel(row.country), formatNumber(row.total_users)],
    2,
  );
  document.querySelector("#analytics-generated").textContent = `Osveženo ${formatDate(data.generated_at)}`;
}

function renderAnalyticsSummary(values) {
  const cards = [
    [values.total_users, "Uporabniki"],
    [values.new_users, "Novi uporabniki"],
    [values.sessions, "Seje"],
    [values.page_views, "Ogledi strani"],
    [values.engaged_sessions, "Aktivne seje"],
    [formatDuration(values.average_session_duration), "Povp. trajanje seje"],
  ];
  const container = document.querySelector("#analytics-summary");
  container.replaceChildren(...cards.map(([value, label]) => {
    const card = document.createElement("article");
    const strong = document.createElement("strong");
    const span = document.createElement("span");
    strong.textContent = typeof value === "number" ? formatNumber(value) : value;
    span.textContent = label;
    card.append(strong, span);
    return card;
  }));
}

function renderAnalyticsDaily(rows) {
  const container = document.querySelector("#analytics-daily");
  container.replaceChildren();
  if (!rows.length) {
    container.textContent = "Za izbrano obdobje še ni podatkov.";
    return;
  }
  const maximum = Math.max(1, ...rows.map((row) => Number(row.sessions) || 0));
  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "analytics-bar-row";
    const date = document.createElement("span");
    const track = document.createElement("i");
    const fill = document.createElement("b");
    const value = document.createElement("strong");
    date.textContent = formatAnalyticsDate(row.date);
    fill.style.width = `${Math.max(2, ((Number(row.sessions) || 0) / maximum) * 100)}%`;
    track.append(fill);
    value.textContent = `${formatNumber(row.sessions)} sej · ${formatNumber(row.active_users)} uporabnikov`;
    item.append(date, track, value);
    container.append(item);
  });
}

function renderAnalyticsTable(selector, rows, values, columns) {
  const body = document.querySelector(selector);
  body.replaceChildren();
  if (!rows?.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = columns;
    cell.textContent = "Podatkov še ni.";
    row.append(cell);
    body.append(row);
    return;
  }
  rows.forEach((row) => body.append(tableRow(values(row))));
}

function channelLabel(value) {
  return ({ Direct: "Neposredno", "Organic Search": "Organsko iskanje", Referral: "Povezave z drugih strani", "Organic Social": "Družbena omrežja", Unassigned: "Nedoločeno" })[value] || value || "—";
}

function eventLabel(value) {
  return ({ parcel_analysis_started: "Začete analize", parcel_analysis_completed: "Končane analize", parcel_analysis_failed: "Neuspele analize", result_tab_opened: "Odprti zavihki rezultatov", location_report_downloaded: "Preneseni PDF-ji" })[value] || value || "—";
}

function analyticsDeviceLabel(value) {
  return ({ desktop: "Namizni računalnik", mobile: "Telefon", tablet: "Tablica", bot: "Robot", unknown: "Neznana naprava" })[value] || value || "—";
}

function technicalLabel(value) {
  return ({ Other: "Drugo", unknown: "Neznano" })[value] || value || "—";
}

function countryLabel(value) {
  return ({ Slovenia: "Slovenija", Austria: "Avstrija", Croatia: "Hrvaška", Italy: "Italija", Germany: "Nemčija", Hungary: "Madžarska", "United States": "Združene države Amerike", "United Kingdom": "Združeno kraljestvo" })[value] || value || "—";
}

function formatAnalyticsDate(value) {
  if (!/^\d{8}$/.test(value || "")) return value || "—";
  const date = new Date(`${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}T12:00:00`);
  return new Intl.DateTimeFormat("sl-SI", { day: "2-digit", month: "2-digit" }).format(date);
}

function formatNumber(value) {
  return new Intl.NumberFormat("sl-SI", { maximumFractionDigits: 1 }).format(Number(value) || 0);
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes} min ${seconds % 60} s` : `${seconds} s`;
}

async function loadOpenAIUsage() {
  const days = Number(openaiPeriod.value) || 30;
  const refreshButton = document.querySelector("#openai-refresh");
  openaiMessage.hidden = false;
  openaiMessage.className = "analytics-message";
  openaiMessage.textContent = "Nalagam porabo OpenAI …";
  openaiContent.hidden = true;
  refreshButton.disabled = true;
  openaiDownload.href = `/api/admin/openai-usage.csv?days=${days}`;
  const data = await api(`/api/admin/openai-usage?days=${days}`);
  refreshButton.disabled = false;
  if (!data || data.status !== "ready") {
    openaiMessage.classList.add("is-error");
    openaiMessage.textContent = data?.detail || "Statistike OpenAI trenutno ni mogoče naložiti.";
    return;
  }

  openaiMessage.hidden = true;
  openaiContent.hidden = false;
  renderOpenAISummary(data.summary || {});
  renderAnalyticsTable(
    "#openai-daily",
    data.daily,
    (row) => [
      formatSimpleDate(row.date),
      formatNumber(row.analyses),
      formatNumber(row.ai_analyses),
      formatNumber(row.calls),
      formatNumber(row.input_tokens),
      formatNumber(row.output_tokens),
      formatNumber(row.total_tokens),
      formatNumber(row.failures),
    ],
    8,
  );
  renderAnalyticsTable(
    "#openai-models",
    data.models,
    (row) => [row.model || "—", formatNumber(row.analyses), formatNumber(row.calls), formatNumber(row.total_tokens)],
    4,
  );
  renderAnalyticsTable(
    "#openai-recent",
    data.recent,
    (row) => [
      formatDate(row.requested_at),
      row.parcel_reference || "—",
      row.model || "—",
      formatNumber(row.calls),
      formatNumber(row.input_tokens),
      formatNumber(row.output_tokens),
      formatNumber(row.total_tokens),
      formatNumber(row.failures),
      row.job_id ? row.job_id.slice(0, 8) : "—",
    ],
    9,
  );
  renderOpenAIRateLimit(data.latest_rate_limit);
  document.querySelector("#openai-generated").textContent = `Osveženo ${formatDate(data.generated_at)}`;
}

function renderOpenAISummary(values) {
  const cards = [
    [values.ai_analyses, "Analize z OpenAI"],
    [values.calls, "API klici"],
    [values.input_tokens, "Vhodni tokeni"],
    [values.output_tokens, "Izhodni tokeni"],
    [values.total_tokens, "Skupaj tokeni"],
    [values.failures, "Neuspešni povzetki"],
  ];
  const container = document.querySelector("#openai-summary");
  container.replaceChildren(...cards.map(([value, label]) => {
    const card = document.createElement("article");
    const strong = document.createElement("strong");
    const span = document.createElement("span");
    strong.textContent = formatNumber(value);
    span.textContent = label;
    card.append(strong, span);
    return card;
  }));
}

function renderOpenAIRateLimit(limit) {
  const container = document.querySelector("#openai-rate-limit");
  container.replaceChildren();
  if (!limit || limit.limit_tokens === null) {
    container.textContent = "Omejitev še ni bila zabeležena v zaključeni analizi.";
    return;
  }
  const remaining = Number(limit.remaining_tokens) || 0;
  const total = Math.max(1, Number(limit.limit_tokens) || 0);
  const strong = document.createElement("strong");
  const label = document.createElement("span");
  const track = document.createElement("i");
  const fill = document.createElement("b");
  const note = document.createElement("p");
  strong.textContent = `${formatNumber(remaining)} / ${formatNumber(total)}`;
  label.textContent = "preostalih tokenov v zadnji zabeleženi omejitvi";
  fill.style.width = `${Math.max(0, Math.min(100, (remaining / total) * 100))}%`;
  track.append(fill);
  note.textContent = `Ponastavitev: ${limit.reset || "—"} · meritev: ${formatDate(limit.observed_at)}`;
  container.append(strong, label, track, note);
}

function formatSimpleDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return value || "—";
  return new Intl.DateTimeFormat("sl-SI", { dateStyle: "medium" }).format(new Date(`${value}T12:00:00`));
}

async function loadLogs() {
  const source = document.querySelector("#log-source").value || "application";
  const lines = document.querySelector("#log-lines").value;
  const data = await api(`/api/admin/logs?source=${encodeURIComponent(source)}&lines=${encodeURIComponent(lines)}`);
  if (!data) return;
  const select = document.querySelector("#log-source");
  if (!select.options.length) {
    data.sources.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.key;
      option.textContent = `${item.label}${item.available ? "" : " (ni datoteke)"}`;
      select.append(option);
    });
    select.value = data.log.source;
  }
  document.querySelector("#log-meta").textContent = data.log.available ? `Zadnja sprememba: ${formatDate(data.log.modified_at)} · prikaz je varnostno očiščen.` : "Ta dnevnik še ne obstaja.";
  document.querySelector("#log-output").textContent = data.log.lines.join("\n") || "Ni zapisov.";
}

async function api(url, options = {}, quietUnauthorized = false) {
  try {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
    if (response.status === 204) return {};
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && !quietUnauthorized && !loginShell.hidden) return body;
      if (response.status === 401 && dashboard && !dashboard.hidden) window.location.reload();
      return body;
    }
    return body;
  } catch {
    return null;
  }
}

function loadTurnstile() {
  if (window.turnstile) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true;
    script.defer = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Turnstile se ni naložil."));
    document.head.append(script);
  });
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("sl-SI", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
}

function statusLabel(value) {
  return ({ completed: "Končano", failed: "Napaka", running: "V teku", queued: "V vrsti" })[value] || value;
}

function deviceLabel(item) {
  return `${analyticsDeviceLabel(item.device_type)} · ${technicalLabel(item.browser_family)}`;
}
