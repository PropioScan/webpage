const form = document.querySelector("#search-form");
const input = document.querySelector("#parcel-number");
const button = document.querySelector("#search-button");
const buttonLabel = button.querySelector("span");
const captchaPanel = document.querySelector("#captcha-panel");
const captchaMessage = document.querySelector("#captcha-message");
const turnstileContainer = document.querySelector("#turnstile-widget");
const captchaCheck = document.querySelector("#captcha-check");
const statusPanel = document.querySelector("#status");
const statusTitle = document.querySelector("#status-title");
const statusDetail = document.querySelector("#status-detail");
const statusPercent = document.querySelector("#status-percent");
const statusProgress = document.querySelector("#status-progress");
const errorPanel = document.querySelector("#error");
const resultsSection = document.querySelector("#results");
const mobileMenu = document.querySelector(".mobile-menu");
const sidebar = document.querySelector(".app-sidebar");
const cookieBanner = document.querySelector("#cookie-banner");
const cookieDialog = document.querySelector("#cookie-dialog");
const privacyDialog = document.querySelector("#privacy-dialog");
const functionalConsentInput = document.querySelector("#cookie-functional");
const analyticsConsentInput = document.querySelector("#cookie-analytics");
const recentParcels = document.querySelector("#recent-parcels");
const reportDownload = document.querySelector("#report-download");
const reportDownloadLabel = document.querySelector("#report-download-label");
const reportDownloadStatus = document.querySelector("#report-download-status");
const cacheNotice = document.querySelector("#cache-notice");
const cacheNoticeText = document.querySelector("#cache-notice-text");
const cacheRefresh = document.querySelector("#cache-refresh");

const CONSENT_VERSION = "1.2";
const CONSENT_COOKIE = "propioscan_cookie_consent";
const RECENT_SEARCHES_KEY = "propioscan_recent_searches";
const CONSENT_MAX_AGE_SECONDS = 180 * 24 * 60 * 60;
const RECENT_SEARCH_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
const GOOGLE_COOKIE_MAX_AGE_SECONDS = 90 * 24 * 60 * 60;
const GOOGLE_EVENT_NAMES = new Set([
  "parcel_analysis_started",
  "parcel_analysis_completed",
  "parcel_analysis_failed",
  "location_report_downloaded",
  "result_tab_opened",
]);
let privacyConsent = readPrivacyConsent();
let publicConfigPromise;
let turnstileScriptPromise;
let googleAnalyticsPromise;
let googleMeasurementId = null;
let turnstileWidgetId = null;
let captchaAwaiting = false;
let captchaVerifiedToken = null;
let searchStarting = false;
let analysisStartedAt = null;
let pendingParcelReference = null;
let pendingForceRefresh = false;
let activeParcelReference = null;

initializePrivacyControls();

mobileMenu?.addEventListener("click", () => {
  const isOpen = sidebar.classList.toggle("is-open");
  mobileMenu.setAttribute("aria-expanded", String(isOpen));
});

sidebar?.querySelectorAll("a").forEach((anchor) => anchor.addEventListener("click", () => {
  sidebar.classList.remove("is-open");
  mobileMenu?.setAttribute("aria-expanded", "false");
}));

document.querySelectorAll("[data-result-tab]").forEach((tab) => tab.addEventListener("click", () => {
  activateResultTab(tab.dataset.resultTab);
}));

document.querySelectorAll("[data-open-result-tab]").forEach((control) => control.addEventListener("click", (event) => {
  if (resultsSection.hidden) return;
  event.preventDefault();
  activateResultTab(control.dataset.openResultTab);
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}));

reportDownload?.addEventListener("click", downloadLocationReport);

cacheRefresh?.addEventListener("click", () => {
  if (!activeParcelReference || searchStarting || captchaAwaiting) return;
  input.value = activeParcelReference;
  requestParcelSearch(activeParcelReference, true);
});

captchaCheck?.addEventListener("click", () => {
  if (!captchaAwaiting || turnstileWidgetId === null || !window.turnstile) return;
  captchaCheck.disabled = true;
  captchaCheck.querySelector("strong").textContent = "Preverjam …";
  captchaMessage.classList.remove("is-error", "is-success");
  captchaMessage.textContent = "Varnostno preverjanje je v teku …";
  window.turnstile.execute("#turnstile-widget");
});

function activateResultTab(name) {
  const selectedTab = document.querySelector(`[data-result-tab="${name}"]`);
  const selectedPanel = document.querySelector(`[data-result-panel="${name}"]`);
  if (!selectedTab || !selectedPanel) return;

  document.querySelectorAll("[data-result-tab]").forEach((tab) => {
    const active = tab === selectedTab;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-result-panel]").forEach((panel) => {
    panel.hidden = panel !== selectedPanel;
  });
  if (!resultsSection.hidden) {
    recordGoogleEvent("result_tab_opened", { tab_name: name });
  }
}

const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const formatMoney = (value) => value == null
  ? "Ni podatka"
  : new Intl.NumberFormat("sl-SI", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
const formatNumber = (value) => value == null ? "Ni podatka" : new Intl.NumberFormat("sl-SI").format(value);
const formatBytes = (value) => {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
};

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const parcelReference = input.value.trim();
  if (!parcelReference || button.disabled) return;
  const forceRefresh = pendingForceRefresh && pendingParcelReference === parcelReference;
  requestParcelSearch(parcelReference, forceRefresh);
});

async function requestParcelSearch(parcelReference, forceRefresh = false) {
  if (!parcelReference || searchStarting || captchaAwaiting) return;
  pendingParcelReference = parcelReference;
  pendingForceRefresh = forceRefresh;
  errorPanel.hidden = true;
  button.disabled = true;
  if (cacheRefresh) cacheRefresh.disabled = true;
  buttonLabel.textContent = "Preverjanje …";

  try {
    const config = await getPublicConfig();
    if (config.captcha_required) {
      if (!config.captcha_configured || !config.turnstile_site_key) {
        throw new Error("Varnostno preverjanje ni konfigurirano. Obrnite se na upravljavca strani.");
      }
      if (captchaVerifiedToken) {
        const token = captchaVerifiedToken;
        captchaVerifiedToken = null;
        pendingParcelReference = null;
        pendingForceRefresh = false;
        await beginParcelSearch(parcelReference, token, forceRefresh);
        return;
      }
      captchaAwaiting = true;
      await showCaptcha(config.turnstile_site_key);
      return;
    }
    pendingParcelReference = null;
    pendingForceRefresh = false;
    await beginParcelSearch(parcelReference, null, forceRefresh);
  } catch (error) {
    captchaAwaiting = false;
    pendingParcelReference = null;
    pendingForceRefresh = false;
    showError(error.message || "Varnostnega preverjanja ni bilo mogoče zagnati.");
    button.disabled = false;
    if (cacheRefresh) cacheRefresh.disabled = false;
    buttonLabel.textContent = "Analiziraj";
  }
}

async function beginParcelSearch(parcelReference, captchaToken, forceRefresh = false) {
  if (searchStarting) return;
  searchStarting = true;
  captchaAwaiting = false;
  captchaVerifiedToken = null;
  captchaPanel.hidden = true;
  if (!forceRefresh || resultsSection.hidden) resetView();
  else errorPanel.hidden = true;
  button.disabled = true;
  if (cacheRefresh) cacheRefresh.disabled = true;
  buttonLabel.textContent = "Analiziram …";
  statusPanel.hidden = false;
  updateStatus(
    0,
    forceRefresh ? "Začenjamo nov pregled virov …" : "Analiza je v čakalni vrsti …",
    "Večji arhivi PIS lahko zahtevajo nekaj minut.",
  );
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parcel_number: parcelReference,
        captcha_token: captchaToken,
        analytics_consent: Boolean(privacyConsent?.analytics),
        consent_version: privacyConsent?.analytics ? CONSENT_VERSION : null,
        force_refresh: forceRefresh,
      }),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    rememberRecentSearch(parcelReference);
    recordSearchEvent(parcelReference);
    const job = await response.json();
    analysisStartedAt = Date.now();
    recordGoogleEvent("parcel_analysis_started");
    await pollJob(job.id);
  } catch (error) {
    if (analysisStartedAt !== null) {
      recordGoogleEvent("parcel_analysis_failed", analysisDurationParameters());
      analysisStartedAt = null;
    }
    showError(error.message || "Analize ni bilo mogoče začeti.");
  } finally {
    searchStarting = false;
    pendingParcelReference = null;
    pendingForceRefresh = false;
    button.disabled = false;
    if (cacheRefresh) cacheRefresh.disabled = false;
    buttonLabel.textContent = "Analiziraj";
    if (turnstileWidgetId !== null && window.turnstile) window.turnstile.reset(turnstileWidgetId);
  }
}

async function getPublicConfig() {
  if (!publicConfigPromise) {
    publicConfigPromise = fetch("/api/config", { cache: "no-store" }).then(async (response) => {
      if (!response.ok) throw new Error(await errorMessage(response));
      return response.json();
    }).catch((error) => {
      publicConfigPromise = undefined;
      throw error;
    });
  }
  return publicConfigPromise;
}

function googleConsentState(granted) {
  if (!window.gtag) return;
  window.gtag("consent", "update", {
    analytics_storage: granted ? "granted" : "denied",
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
  });
}

async function enableGoogleAnalytics() {
  if (!privacyConsent?.analytics) return false;
  if (googleMeasurementId && window.gtag) {
    window[`ga-disable-${googleMeasurementId}`] = false;
    googleConsentState(true);
    return true;
  }
  if (googleAnalyticsPromise) return googleAnalyticsPromise;

  googleAnalyticsPromise = (async () => {
    const config = await getPublicConfig();
    const measurementId = config.google_analytics_measurement_id;
    if (!/^G-[A-Z0-9]{6,20}$/.test(measurementId || "")) return false;

    googleMeasurementId = measurementId;
    window[`ga-disable-${measurementId}`] = false;
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function gtag() { window.dataLayer.push(arguments); };
    window.gtag("consent", "default", {
      analytics_storage: "denied",
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      ads_data_redaction: true,
      wait_for_update: 500,
    });
    googleConsentState(true);
    window.gtag("js", new Date());
    window.gtag("config", measurementId, {
      allow_google_signals: false,
      allow_ad_personalization_signals: false,
      anonymize_ip: true,
      cookie_expires: GOOGLE_COOKIE_MAX_AGE_SECONDS,
      cookie_update: false,
      send_page_view: true,
    });

    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
    await new Promise((resolve, reject) => {
      script.addEventListener("load", resolve, { once: true });
      script.addEventListener("error", reject, { once: true });
      document.head.append(script);
    });
    if (!privacyConsent?.analytics) {
      disableGoogleAnalytics();
      return false;
    }
    return true;
  })().catch(() => {
    googleAnalyticsPromise = undefined;
    return false;
  });
  return googleAnalyticsPromise;
}

function disableGoogleAnalytics() {
  if (googleMeasurementId) window[`ga-disable-${googleMeasurementId}`] = true;
  googleConsentState(false);
  document.cookie.split("; ").forEach((entry) => {
    const name = entry.split("=", 1)[0];
    if (!name.startsWith("_ga")) return;
    const domains = ["", window.location.hostname, `.${window.location.hostname}`];
    domains.forEach((domain) => {
      const domainPart = domain ? `; Domain=${domain}` : "";
      document.cookie = `${name}=; Max-Age=0; Path=/${domainPart}; SameSite=Lax; Secure`;
    });
  });
}

function recordGoogleEvent(name, parameters = {}) {
  if (!privacyConsent?.analytics || !GOOGLE_EVENT_NAMES.has(name)) return;
  const safeParameters = {};
  if (["overview", "report", "technical", "acts"].includes(parameters.tab_name)) {
    safeParameters.tab_name = parameters.tab_name;
  }
  if (Number.isFinite(parameters.duration_seconds)) {
    safeParameters.duration_seconds = Math.max(0, Math.min(3600, Math.round(parameters.duration_seconds)));
  }
  enableGoogleAnalytics().then((enabled) => {
    if (enabled && privacyConsent?.analytics) window.gtag("event", name, safeParameters);
  });
}

function analysisDurationParameters() {
  if (analysisStartedAt === null) return {};
  return { duration_seconds: (Date.now() - analysisStartedAt) / 1000 };
}

async function showCaptcha(siteKey) {
  captchaVerifiedToken = null;
  captchaPanel.hidden = false;
  captchaPanel.classList.remove("is-verified");
  captchaMessage.classList.remove("is-error", "is-success");
  captchaMessage.textContent = "Nalagam preverjanje …";
  captchaCheck.disabled = true;
  captchaCheck.querySelector("strong").textContent = "Preveri, da nisem robot";
  await loadTurnstileScript();

  if (turnstileWidgetId !== null) {
    window.turnstile.reset(turnstileWidgetId);
    captchaCheck.disabled = false;
    captchaMessage.textContent = "Kliknite gumb za varnostno preverjanje.";
    captchaPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  turnstileWidgetId = window.turnstile.render(turnstileContainer, {
    sitekey: siteKey,
    action: "parcel_search",
    appearance: "interaction-only",
    execution: "execute",
    language: "sl",
    size: window.matchMedia("(max-width: 420px)").matches ? "compact" : "flexible",
    callback: (token) => {
      if (!captchaAwaiting) return;
      captchaVerifiedToken = token;
      captchaAwaiting = false;
      captchaPanel.classList.add("is-verified");
      captchaMessage.classList.add("is-success");
      captchaMessage.textContent = "Preverjanje je uspešno. Zdaj lahko zaženete analizo.";
      captchaCheck.disabled = true;
      captchaCheck.querySelector("strong").textContent = "Preverjeno";
      button.disabled = false;
      buttonLabel.textContent = pendingForceRefresh ? "Preveri znova" : "Analiziraj";
    },
    "error-callback": () => {
      captchaVerifiedToken = null;
      captchaAwaiting = true;
      captchaMessage.classList.add("is-error");
      captchaMessage.textContent = "Preverjanje ni uspelo. Poskusite znova.";
      captchaCheck.disabled = false;
      captchaCheck.querySelector("strong").textContent = "Poskusi znova";
      button.disabled = true;
      buttonLabel.textContent = "Najprej preverjanje";
    },
    "expired-callback": () => {
      captchaVerifiedToken = null;
      captchaAwaiting = true;
      captchaMessage.classList.add("is-error");
      captchaMessage.textContent = "Preverjanje je poteklo. Potrdite ga znova.";
      window.turnstile.reset(turnstileWidgetId);
      captchaCheck.disabled = false;
      captchaCheck.querySelector("strong").textContent = "Preveri znova";
      button.disabled = true;
      buttonLabel.textContent = "Najprej preverjanje";
    },
  });
  captchaCheck.disabled = false;
  captchaMessage.textContent = "Kliknite gumb za varnostno preverjanje.";
  captchaPanel.scrollIntoView({ behavior: "smooth", block: "center" });
}

function loadTurnstileScript() {
  if (window.turnstile) return Promise.resolve();
  if (turnstileScriptPromise) return turnstileScriptPromise;

  turnstileScriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true;
    script.defer = true;
    script.addEventListener("load", () => window.turnstile ? resolve() : reject(new Error("Varnostno preverjanje se ni naložilo.")));
    script.addEventListener("error", () => reject(new Error("Varnostnega preverjanja ni bilo mogoče naložiti.")));
    document.head.append(script);
  }).catch((error) => {
    turnstileScriptPromise = undefined;
    throw error;
  });
  return turnstileScriptPromise;
}

async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/api/search/${encodeURIComponent(jobId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await errorMessage(response));
    const job = await response.json();
    updateStatus(job.progress, job.message, "Uradne evidence in dokumente preverjamo v ozadju.");
    if (job.status === "completed") {
      statusPanel.hidden = true;
      renderResult(job.result, job);
      recordGoogleEvent("parcel_analysis_completed", analysisDurationParameters());
      analysisStartedAt = null;
      return;
    }
    if (job.status === "failed") throw new Error(job.error || "Analiza ni uspela.");
    await new Promise((resolve) => window.setTimeout(resolve, 1400));
  }
}

async function errorMessage(response) {
  try {
    const body = await response.json();
    return body.detail || `Request failed (${response.status}).`;
  } catch {
    return `Request failed (${response.status}).`;
  }
}

function updateStatus(progress, title, detail) {
  statusTitle.textContent = title;
  statusDetail.textContent = detail;
  statusPercent.textContent = `${progress}%`;
  statusProgress.style.width = `${progress}%`;
}

function resetView() {
  errorPanel.hidden = true;
  resultsSection.hidden = true;
  cacheNotice.hidden = true;
  activeParcelReference = null;
  activateResultTab("overview");
  document.querySelector("#parcel-overview").replaceChildren();
  document.querySelector("#parcel-visuals").replaceChildren();
  document.querySelector("#land-use-assessment").replaceChildren();
  document.querySelector("#planning-context").replaceChildren();
  document.querySelector("#planning-acts").replaceChildren();
  document.querySelector("#infrastructure").replaceChildren();
  document.querySelector("#road-access").replaceChildren();
  document.querySelector("#protected-areas").replaceChildren();
  document.querySelector("#cultural-heritage").replaceChildren();
  document.querySelector("#constraints").replaceChildren();
  document.querySelector("#risks").replaceChildren();
  document.querySelector("#document-list").replaceChildren();
  document.querySelector("#global-warnings").replaceChildren();
  document.querySelector("#document-stats").replaceChildren();
  document.querySelector("#official-form-preview").replaceChildren();
  reportDownload.href = "#";
  reportDownload.setAttribute("aria-disabled", "true");
  reportDownload.removeAttribute("aria-busy");
  reportDownloadLabel.textContent = "Prenesi PDF z vsemi podatki";
  reportDownloadStatus.hidden = true;
  reportDownloadStatus.classList.remove("is-error", "is-success");
}

function showError(message) {
  statusPanel.hidden = true;
  errorPanel.textContent = message;
  errorPanel.hidden = false;
}

function renderResult(result, job) {
  const jobId = job.id;
  activeParcelReference = `${result.parcel.cadastral_municipality_id} ${result.parcel.parcel_number}`;
  renderCacheNotice(result, job);
  renderParcel(result.parcel);
  renderVisualGallery(result.parcel_map, result.planning_land_use_map, result.land_use_assessment);
  renderLandUse(result.land_use_assessment);
  renderContext(result.planning_context || []);
  renderPlanningActs(result.planning_acts || []);
  renderInfrastructure(result.infrastructure || []);
  renderRoadAccess(result.road_access);
  renderRiskScore("#protected-areas", "Varovana območja", result.protected_areas || [], "Preverjeni sloji niso vrnili varovanega območja na parceli.");
  renderRiskScore("#cultural-heritage", "Kulturna dediščina", result.cultural_heritage || [], "Register eVRD ni vrnil režima kulturne dediščine na parceli.");
  renderRiskScore("#constraints", "Omejitve", result.constraints || [], "Preverjeni vodovarstveni in katastrski sloji niso vrnili omejitve.");
  renderRiskScore("#risks", "Tveganja", result.risks || [], "Preverjeni sloji poplav, erozije in plazov niso vrnili preseka.");
  renderDocuments(result.documents || [], result.warnings || []);
  renderOfficialForm(result, jobId);
  activateResultTab("overview");
  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderCacheNotice(result, job) {
  if (!job.from_cache) {
    cacheNotice.hidden = true;
    return;
  }
  const preparedAt = formatReportDate(job.cache_stored_at || result.completed_at);
  const expiresAt = formatReportDate(job.cache_expires_at);
  cacheNoticeText.textContent = `Podatki niso bili ponovno pridobljeni iz uradnih virov v živo. Prikazan je rezultat, shranjen v podatkovni zbirki ${preparedAt}; shranjena različica velja do ${expiresAt}. Če želite najnovejše stanje, zaženite nov pregled uradnih virov.`;
  cacheNotice.hidden = false;
}

function renderOfficialForm(result, jobId) {
  const container = document.querySelector("#official-form-preview");
  const parcel = result.parcel;
  const contexts = result.planning_context || [];
  const acts = result.planning_acts || [];
  const planningConditions = result.planning_conditions || [];
  const assessment = result.land_use_assessment;
  const planningMap = result.planning_land_use_map;
  const planningDrawing = (planningMap?.evidence || []).find((item) => item.preview_url)
    || planningMap?.evidence?.[0]
    || null;

  document.querySelector("#report-parcel-reference").textContent = `${parcel.cadastral_municipality_id} ${parcel.parcel_number}`;
  document.querySelector("#report-cadastral-municipality").textContent = `${parcel.cadastral_municipality_id} – ${parcel.cadastral_municipality || "ime ni na voljo"}`;
  document.querySelector("#report-municipality").textContent = parcel.municipality || "Občina ni določena";
  document.querySelector("#report-completed-at").textContent = formatReportDate(result.completed_at);

  const landUseRows = contexts.length
    ? contexts.map((context, index) => {
      const place = [
        context.planning_unit && `EUP ${context.planning_unit}`,
        context.subunit && `PEUP ${context.subunit}`,
      ].filter(Boolean).join(" · ");
      const share = context.parcel_share_percent == null ? "" : ` · ${context.parcel_share_percent}% parcele`;
      const value = `${context.land_use_code || "brez oznake"} – ${context.land_use_description || "opis ni na voljo"}${share}`;
      return [`Del namenske rabe ${index + 1}`, place ? `${value} · ${place}` : value];
    })
    : [["Namenska raba", "Strukturiran podatek ni bil vrnjen"]];
  if (assessment) landUseRows.push(["Orientacijska razlaga", `${assessment.label}. ${assessment.summary}`]);

  const actRows = acts.length
    ? acts.map((act) => {
      const stage = act.preparation_state === "completed" ? "veljavni / zaključeni postopek" : "akt ali postopek v pripravi";
      return [act.title, [act.act_type, act.status, stage].filter(Boolean).join(" · ")];
    })
    : [["Prostorski akti", "PIS ni vrnil akta s prostorskim presekom parcele"]];

  const regimeRows = [
    ...officialFindingRows("Varstvo narave", result.protected_areas || []),
    ...officialFindingRows("Kulturna dediščina", result.cultural_heritage || []),
    ...officialFindingRows("Pravne in prostorske omejitve", result.constraints || []),
    ...officialFindingRows("Naravne nevarnosti", result.risks || []),
  ];
  const codes = (assessment?.items || []).map((item) => item.code).filter(Boolean).join(", ") || "namenska raba ni bila strukturirano določena";
  const conditionRows = planningConditions.length
    ? planningConditions.map((condition, index) => {
      const pageLabel = (condition.pages || []).length ? `, str. ${condition.pages.join(", ")}` : "";
      const source = condition.source_title ? ` Vir: ${condition.source_title}${pageLabel}.` : "";
      return [`${index + 1}. ${condition.title}`, `${condition.description}${source}`];
    })
    : [["Prostorski izvedbeni pogoji", "Samodejni izvleček iz besedilnega dela odloka ni bil pripravljen"]];

  const sections = [
    {
      number: 1,
      title: "Zemljiška parcela, za katero se izda pregled",
      status: "automatic",
      rows: [
        ["Šifra in ime katastrske občine", `${parcel.cadastral_municipality_id} – ${parcel.cadastral_municipality || "ime ni na voljo"}`],
        ["Številka zemljiške parcele", parcel.parcel_number],
        ["Občina", parcel.municipality || "občina ni bila določena"],
        ["Površina", parcel.area_m2 == null ? "Podatek ni na voljo" : `${formatNumber(parcel.area_m2)} m²`],
      ],
      hint: "Identifikacijo in površino primerjajte z aktualnim stanjem v katastru nepremičnin GURS.",
    },
    {
      number: 2,
      title: "Namenska raba prostora",
      status: contexts.length ? "automatic" : "review",
      rows: landUseRows,
      hint: "Deleži so geometrijski presek javnega sloja. Namenska raba sama ne ustvarja pravice graditi; preverite še prostorske izvedbene pogoje.",
    },
    {
      number: 3,
      title: "Veljavni prostorski akti in akti v pripravi",
      status: acts.length ? "automatic" : "review",
      rows: actRows,
      hint: "V zavihku Prostorski akti odprite uradne zapise PIS ter preverite datum veljavnosti in vse spremembe odloka.",
    },
    {
      number: 4,
      title: "Začasni ukrepi",
      status: "review",
      rows: [["Stanje", "Razpoložljivi avtomatski viri ne omogočajo zanesljive potrditve začasnih ukrepov"]],
      hint: "Občino vprašajte za vrsto ukrepa, pravno podlago ter čas njegovega trajanja.",
    },
    {
      number: 5,
      title: "Predkupna pravica",
      status: "review",
      rows: [["Stanje", "Predkupna pravica občine ali države ni bila samodejno potrjena oziroma izključena"]],
      hint: "Pred pravnim poslom zahtevajte uradno izjavo pristojne občine in po potrebi preverite predkupno pravico države.",
    },
    {
      number: 6,
      title: "Pravni režimi",
      status: "partial",
      rows: regimeRows,
      mapAttachment: result.parcel_map?.legal_regime_overlay_url
        ? {
          type: "regime",
          parcelMap: result.parcel_map,
          findings: [
            ...(result.protected_areas || []),
            ...(result.cultural_heritage || []),
            ...(result.constraints || []),
            ...(result.risks || []),
          ],
        }
        : null,
      hint: "Za vsak zadetek so navedeni vrsta režima, ime, pravna podlaga, vir in geometrijsko razmerje do parcele. Odsotnost zadetka ne dokazuje odsotnosti vseh pravnih režimov; občinske in letališke cone potrdi pristojni organ.",
    },
    {
      number: 7,
      title: "Razvojna stopnja nepozidanega stavbnega zemljišča in taksa",
      status: "review",
      rows: [
        ["Zaznane oznake namenske rabe", codes],
        ["Razvojna stopnja", "Ni določljiva iz preverjenih avtomatskih virov"],
        ["Taksa za neizkoriščeno stavbno zemljišče", "Območje plačevanja ni bilo potrjeno"],
      ],
      hint: "Pri občini preverite uradno razvojno stopnjo zemljišča in morebitno območje plačevanja takse.",
    },
    {
      number: 8,
      title: "Soglasje za spreminjanje meje parcele",
      status: "review",
      rows: [["Stanje", "Obveznost pridobitve soglasja ni bila samodejno potrjena oziroma izključena"]],
      hint: "Pred parcelacijo ali izravnavo meje preverite obveznost soglasja in njeno pravno podlago pri občini.",
    },
    {
      number: 9,
      title: "Priloga: izsek grafičnega dela prostorskega akta",
      status: planningDrawing?.preview_url ? "automatic" : "review",
      rows: [
        ["Grafična priloga", planningDrawing?.preview_url
          ? "Izris iz prostorskega reda je vključen spodaj in v PDF poročilu"
          : "Izrisa iz prostorskega reda ni bilo mogoče samodejno pripraviti"],
        ["Kartografska dokazila", `${planningMap?.evidence?.length || 0} najdenih kartografskih listov`],
      ],
      mapAttachment: planningDrawing?.preview_url
        ? { type: "planning", evidence: planningDrawing, planningMap, assessment }
        : null,
      hint: "Za uradno prilogo uporabite grafični izsek, ki ga potrdi občina. Spletni prikaz je namenjen orientaciji.",
    },
    {
      number: 10,
      title: "Priloga: prostorski izvedbeni pogoji",
      status: planningConditions.some((condition) => condition.available) ? "partial" : "review",
      rows: conditionRows,
      hint: "Prikazanih je 17 standardiziranih vsebinskih sklopov. Izvlečki so strojno pripravljeni iz besedilnega dela odloka; pred uporabo preverite celotno uradno besedilo in pogoje za konkretni poseg.",
    },
  ];

  sections.forEach((sectionData) => container.append(officialSection(sectionData)));
  reportDownload.href = `/api/search/${encodeURIComponent(jobId)}/report`;
  reportDownload.removeAttribute("aria-disabled");
  reportDownload.download = `propioscan-lokacijska-informacija-${parcel.cadastral_municipality_id}-${parcel.parcel_number.replaceAll("/", "-")}.pdf`;
}

function formatReportDate(value) {
  if (!value) return "Datum ni na voljo";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Datum ni na voljo";
  return new Intl.DateTimeFormat("sl-SI", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Ljubljana",
  }).format(date);
}

async function downloadLocationReport(event) {
  event.preventDefault();
  event.stopPropagation();
  if (reportDownload.getAttribute("aria-disabled") === "true" || reportDownload.getAttribute("aria-busy") === "true") return;

  const reportUrl = reportDownload.href;
  const filename = reportDownload.download || "propioscan-lokacijska-informacija.pdf";
  reportDownload.setAttribute("aria-busy", "true");
  reportDownloadLabel.textContent = "Pripravljam PDF …";
  reportDownloadStatus.hidden = false;
  reportDownloadStatus.classList.remove("is-error", "is-success");
  reportDownloadStatus.textContent = "Iz podatkov analize pripravljamo PDF za prenos.";

  try {
    const response = await fetch(reportUrl, {
      cache: "no-store",
      headers: { Accept: "application/pdf" },
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    if (!(response.headers.get("content-type") || "").includes("application/pdf")) {
      throw new Error("Strežnik ni vrnil veljavnega PDF dokumenta.");
    }

    const blobUrl = URL.createObjectURL(await response.blob());
    const temporaryLink = document.createElement("a");
    temporaryLink.href = blobUrl;
    temporaryLink.download = filename;
    temporaryLink.hidden = true;
    document.body.append(temporaryLink);
    temporaryLink.click();
    temporaryLink.remove();
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);

    reportDownloadStatus.classList.add("is-success");
    reportDownloadStatus.textContent = "PDF z izpolnjenimi podatki je pripravljen in se prenaša.";
    recordGoogleEvent("location_report_downloaded");
  } catch (error) {
    reportDownloadStatus.classList.add("is-error");
    reportDownloadStatus.textContent = error.message || "PDF-ja ni bilo mogoče prenesti. Poskusite znova.";
  } finally {
    reportDownload.removeAttribute("aria-busy");
    reportDownloadLabel.textContent = "Prenesi PDF z vsemi podatki";
  }
}

function officialFindingRows(group, findings) {
  if (!findings.length) return [[group, "Preverjeni spletni sloji niso vrnili preseka"]];
  return findings.flatMap((finding, index) => {
    const suffix = ` (${group} ${index + 1})`;
    const source = [finding.source, finding.reference, finding.detail].filter(Boolean).join(" · ");
    return [
      [`Vrsta režima${suffix}`, finding.category],
      [`Ime režima${suffix}`, finding.name],
      [`Pravna podlaga${suffix}`, finding.legal_basis || "V preverjenem sloju ni bila strukturirano navedena"],
      [`Vir${suffix}`, source],
      [`Geometrija${suffix}`, finding.geometry_relation || "Uradni spletni sloj geometrijsko seka območje parcele."],
    ];
  });
}

function officialSection(sectionData) {
  const section = element("article", "official-section");
  const header = element("header", "official-section-head");
  header.append(element("b", "official-section-number", sectionData.number));
  const title = element("div", "official-section-title");
  title.append(element("span", "", `Sklop ${sectionData.number} od 10`), element("h4", "", sectionData.title));
  const statusLabel = {
    automatic: "Samodejno izpolnjeno",
    partial: "Delno izpolnjeno",
    review: "Preverite pri občini",
  }[sectionData.status];
  header.append(title, element("span", `report-status is-${sectionData.status}`, statusLabel));

  const rows = element("div", "official-section-rows");
  sectionData.rows.forEach(([label, value]) => {
    const row = element("div", "official-field-row");
    row.append(element("span", "", label), element("strong", "", value || "Podatek ni na voljo"));
    rows.append(row);
  });
  const hint = element("div", "official-hint");
  hint.append(element("b", "", "Namig"), element("p", "", sectionData.hint));
  section.append(header, rows);
  if (sectionData.mapAttachment) {
    section.append(
      sectionData.mapAttachment.type === "regime"
        ? buildRegimeMapAttachment(sectionData.mapAttachment)
        : buildOfficialMapAttachment(sectionData.mapAttachment),
    );
  }
  section.append(hint);
  return section;
}

function buildOfficialMapAttachment({ evidence, planningMap, assessment }) {
  const attachment = element("figure", "official-map-attachment");
  const previewLink = link(evidence.pdf_download_url, "", "official-map-preview");
  const image = element("img", "");
  image.src = evidence.preview_url;
  image.alt = `Izris iz prostorskega reda, ${evidence.pdf_title}, stran ${evidence.page}`;
  image.loading = "lazy";
  previewLink.append(image);

  const caption = element("figcaption", "official-map-caption");
  const copy = element("div", "official-map-caption-copy");
  copy.append(
    element("span", "", "Grafična priloga · izris iz prostorskega reda"),
    element("strong", "", evidence.pdf_title),
    element("p", "", `${evidence.act_title} · stran ${evidence.page} · rdeča linija označuje obris iskane parcele`),
  );
  caption.append(copy, link(evidence.pdf_download_url, "Odpri izvirni PDF ↗", "official-map-source"));
  attachment.append(previewLink, caption, buildAreaLegend(assessment, planningMap?.legend_url));
  return attachment;
}

function buildRegimeMapAttachment({ parcelMap, findings }) {
  const attachment = element("figure", "official-map-attachment");
  const preview = element("div", "official-map-preview regime-map-preview");
  const frame = element("div", "parcel-map-frame regime-map-frame");

  const orthophoto = element("img", "parcel-map-base");
  orthophoto.src = parcelMap.orthophoto_url;
  orthophoto.alt = "Ortofoto GURS na območju parcele";
  orthophoto.loading = "lazy";
  const regimes = element("img", "parcel-map-overlay");
  regimes.src = parcelMap.legal_regime_overlay_url;
  regimes.alt = "Geometrija evidentirane GJI in javnih cest";
  regimes.loading = "lazy";
  frame.append(orthophoto);
  (parcelMap.legal_regime_additional_overlay_urls || []).forEach((url) => {
    const additional = element("img", "parcel-map-overlay municipal-regime-overlay");
    additional.src = url;
    additional.alt = "Dodatna občinska geometrija pravnega režima";
    additional.loading = "lazy";
    frame.append(additional);
  });
  frame.append(regimes);
  const parcel = element("img", "parcel-map-overlay");
  parcel.src = parcelMap.parcel_overlay_url;
  parcel.alt = "Obris iskane parcele";
  parcel.loading = "lazy";
  frame.append(parcel);
  preview.append(frame);

  const caption = element("figcaption", "official-map-caption");
  const copy = element("div", "official-map-caption-copy");
  copy.append(
    element("span", "", "Geometrijska priloga · pravni režimi"),
    element("strong", "", "GURS – Zbirni kataster GJI"),
    element("p", "", "Prikazane so evidentirane osi in objekti. Linija ni nujno uradni zunanji rob varovalnega pasu; razmerje in zakonska širina sta zapisana pri posameznem režimu."),
  );
  caption.append(copy, link(parcelMap.official_viewer_url, "Odpri uradni pregledovalnik ↗", "official-map-source"));
  attachment.append(preview, caption, buildRegimeLegend(findings));
  return attachment;
}

function buildRegimeLegend(findings) {
  const legend = element("section", "visual-legend parcel-legend");
  legend.append(element("strong", "visual-legend-title", "Legenda geometrijske priloge"));
  const items = element("div", "parcel-legend-items");
  const outline = element("span", "parcel-legend-item");
  outline.append(element("i", "parcel-outline-swatch"), document.createTextNode("Obris iskane parcele"));
  const photo = element("span", "parcel-legend-item");
  photo.append(element("i", "orthophoto-swatch"), document.createTextNode("Ortofoto GURS"));
  items.append(outline, photo);
  GJI_REGIME_MAP_LAYERS.forEach((spec) => {
    const item = element("span", "parcel-legend-item");
    item.append(element("i", spec.swatch), document.createTextNode(spec.label));
    items.append(item);
  });
  const categories = [...new Set((findings || []).map((finding) => finding.category))];
  if (categories.includes("Vplivno območje letališča")) {
    const airportZone = element("span", "parcel-legend-item");
    airportZone.append(element("i", "airport-zone-swatch"), document.createTextNode("Vplivno območje letališča (občinski sloj)"));
    items.append(airportZone);
  }
  legend.append(items);
  if (categories.length) {
    legend.append(element("p", "regime-map-found", `Zadetki za parcelo: ${categories.join("; ")}`));
  }
  return legend;
}

const GJI_MAP_LAYERS = [
  { key: "water", label: "Vodovod", layer: "SI.GURS.KGI:LINIJE_VODOVOD_G", swatch: "gji-water-swatch" },
  { key: "sewer", label: "Kanalizacija", layer: "SI.GURS.KGI:LINIJE_KANALIZACIJA_G", swatch: "gji-sewer-swatch" },
  { key: "power", label: "Električna energija", layer: "SI.GURS.KGI:LINIJE_ELEKTRICNA_ENERGIJA_G", swatch: "gji-power-swatch" },
  { key: "telecom", label: "Elektronske komunikacije", layer: "SI.GURS.KGI:LINIJE_ELEKTRONSKE_KOMUNIKACIJE_G", swatch: "gji-telecom-swatch" },
  { key: "gas", label: "Zemeljski plin", layer: "SI.GURS.KGI:LINIJE_ZEMELJSKI_PLIN_G", swatch: "gji-gas-swatch" },
  { key: "heat", label: "Toplotna energija", layer: "SI.GURS.KGI:LINIJE_TOPLOTNA_ENERGIJA_G", swatch: "gji-heat-swatch" },
];

const GJI_REGIME_MAP_LAYERS = [
  ...GJI_MAP_LAYERS,
  { key: "road", label: "Javne ceste", layer: "SI.GURS.KGI:LINIJE_CESTE_G", swatch: "gji-road-swatch" },
  { key: "airport", label: "Letališka infrastruktura (GJI)", layer: "SI.GURS.KGI:POLIGONI_LETALISCA_G", swatch: "gji-airport-swatch" },
];

function renderVisualGallery(parcelMap, planningMap, assessment) {
  const container = document.querySelector("#parcel-visuals");
  const tabs = element("div", "visual-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Slike in karte parcele");
  const panels = element("div", "visual-panels");
  const entries = [
    { label: "Ortofoto", content: buildParcelVisual(parcelMap) },
    { label: "Namenska raba", content: buildPlanningVisual(planningMap, assessment) },
  ];

  (planningMap?.evidence || []).forEach((item, index) => {
    entries.push({
      label: index === 0 ? "Izris iz prostorskega reda" : `Izris iz prostorskega reda ${index + 1}`,
      content: buildEvidenceVisual(item, planningMap, assessment),
    });
  });

  entries.forEach((entry, index) => {
    const button = element("button", index === 0 ? "is-active" : "", `${index + 1}. ${entry.label}`);
    const tabId = `visual-tab-${index + 1}`;
    const panelId = `visual-panel-${index + 1}`;
    button.id = tabId;
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", panelId);
    button.setAttribute("aria-selected", String(index === 0));
    button.tabIndex = index === 0 ? 0 : -1;

    const panel = element("section", "visual-panel");
    panel.id = panelId;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tabId);
    panel.hidden = index !== 0;
    panel.append(entry.content);
    button.addEventListener("click", () => activateVisualTab(tabs, panels, button, panel));
    tabs.append(button);
    panels.append(panel);
  });
  container.append(tabs, panels);
}

function activateVisualTab(tabs, panels, selectedTab, selectedPanel) {
  tabs.querySelectorAll("[role='tab']").forEach((tab) => {
    const active = tab === selectedTab;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  panels.querySelectorAll("[role='tabpanel']").forEach((panel) => {
    panel.hidden = panel !== selectedPanel;
  });
}

function buildParcelVisual(map) {
  const wrapper = element("div", "visual-content");
  if (!map) {
    wrapper.append(element("div", "empty-card", "Uradni posnetek parcele ni na voljo."));
    return wrapper;
  }
  const card = element("article", "parcel-map-card");
  const frame = element("div", "parcel-map-frame");
  const orthophoto = element("img", "parcel-map-base");
  orthophoto.src = map.orthophoto_url;
  orthophoto.alt = "Uradni ortofoto GURS okoli parcele";
  orthophoto.loading = "eager";
  const overlay = element("img", "parcel-map-overlay");
  overlay.src = map.parcel_overlay_url;
  overlay.alt = "Obris iskane parcele";
  overlay.loading = "eager";
  frame.append(orthophoto, overlay);
  let infrastructure = null;
  if (map.infrastructure_overlay_url) {
    infrastructure = element("img", "parcel-map-overlay");
    infrastructure.src = map.infrastructure_overlay_url;
    infrastructure.alt = "Evidentirana komunalna opremljenost (GJI)";
    infrastructure.loading = "eager";
    frame.append(infrastructure);
  }
  const caption = element("div", "parcel-map-caption");
  const copy = element("div");
  copy.append(element("strong", "", map.source), element("p", "", map.note));
  caption.append(copy, link(map.official_viewer_url, "Odpri uradni pregledovalnik ↗", "map-link"));
  const syncInfrastructure = () => {
    if (!infrastructure) return;
    const selected = [];
    card.querySelectorAll(".parcel-legend-toggle").forEach((item) => {
      const box = item.querySelector("input");
      item.classList.toggle("is-off", !box.checked);
      if (box.checked) selected.push(box.dataset.layer);
    });
    applyInfrastructureOverlay(infrastructure, map.infrastructure_overlay_url, selected);
  };
  card.append(frame, caption, buildParcelLegend(map, syncInfrastructure));
  wrapper.append(card);
  return wrapper;
}

function buildPlanningVisual(map, assessment) {
  const wrapper = element("div", "visual-content");
  if (!map) {
    wrapper.append(element("div", "empty-card", "Karte namenske rabe ni bilo mogoče pripraviti."));
    return wrapper;
  }
  const card = element("article", "planning-map-card");
  const frame = element("div", "planning-map-frame");
  const landUse = element("img", "planning-map-base");
  landUse.src = map.land_use_url;
  landUse.alt = "Uradna karta namenske rabe prostora PIS";
  landUse.loading = "eager";
  const overlay = element("img", "planning-map-overlay");
  overlay.src = map.parcel_overlay_url;
  overlay.alt = "Katastrski obris iskane parcele";
  overlay.loading = "eager";
  const codes = element("div", "planning-map-codes");
  (assessment?.items || []).forEach((item) => {
    const pill = element("div", "planning-map-code");
    pill.append(
      element("b", "", item.code || "—"),
      element("span", "", item.parcel_share_percent == null ? "delež ni na voljo" : `${item.parcel_share_percent}% parcele`),
    );
    codes.append(pill);
  });
  frame.append(landUse, overlay, codes);

  const caption = element("div", "planning-map-caption");
  const copy = element("div");
  copy.append(
    element("strong", "", map.source),
    element("p", "", map.note),
    element("small", "", `Šifrant: ${map.dictionary_source}`),
  );
  caption.append(copy, link(map.source_url, "Vir PIS / WMS ↗", "map-link"));
  card.append(frame, caption, buildAreaLegend(assessment, map.legend_url));
  wrapper.append(card);
  return wrapper;
}

function buildEvidenceVisual(item, map, assessment) {
  const wrapper = element("div", "visual-content");
  const card = element("article", "planning-pdf-card visual-pdf-card");
  if (item.preview_url) {
    const previewLink = link(item.pdf_download_url, "", "planning-pdf-preview");
    const image = element("img", "");
    image.src = item.preview_url;
    image.alt = `Kartografski list ${item.pdf_title}, stran ${item.page}`;
    image.loading = "lazy";
    previewLink.replaceChildren(image);
    card.append(previewLink);
  } else {
    card.append(element("div", "empty-card", "Predogleda tega kartografskega lista ni bilo mogoče pripraviti."));
  }
  const body = element("div", "planning-pdf-body");
  const method = item.match_method === "geospatial"
    ? "GeoPDF položaj parcele"
    : "parcelna številka na karti";
  body.append(
    element("span", "", `Stran ${item.page} · ${method}`),
    element("strong", "", item.pdf_title),
    element("p", "", item.act_title),
    link(item.pdf_download_url, "Odpri izvirni PDF ↗"),
  );
  card.append(body, buildAreaLegend(assessment, map.legend_url));
  wrapper.append(card);
  return wrapper;
}

function applyInfrastructureOverlay(img, baseUrl, selectedLayers) {
  if (!selectedLayers.length) {
    img.hidden = true;
    return;
  }
  img.hidden = false;
  const next = new URL(baseUrl);
  next.searchParams.set("LAYERS", selectedLayers.join(","));
  const nextUrl = next.toString();
  if (img.src === nextUrl || img.getAttribute("src") === nextUrl) return;
  img.src = nextUrl;
}

function buildParcelLegend(map, onInfrastructureToggle) {
  const legend = element("section", "visual-legend parcel-legend");
  legend.append(element("strong", "visual-legend-title", "Legenda prikaza"));
  const items = element("div", "parcel-legend-items");
  const outline = element("span", "parcel-legend-item");
  outline.append(element("i", "parcel-outline-swatch"), document.createTextNode("Obris iskane parcele"));
  const photo = element("span", "parcel-legend-item");
  photo.append(element("i", "orthophoto-swatch"), document.createTextNode("Ortofoto posnetek GURS"));
  items.append(outline, photo);
  if (map?.infrastructure_overlay_url && onInfrastructureToggle) {
    GJI_MAP_LAYERS.forEach((spec) => {
      const item = element("label", "parcel-legend-item parcel-legend-toggle");
      item.title = "Pokaži ali skrij ta sloj na karti";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.dataset.layer = spec.layer;
      box.addEventListener("change", onInfrastructureToggle);
      item.append(box, element("i", spec.swatch), document.createTextNode(spec.label));
      items.append(item);
    });
  }
  legend.append(items);
  return legend;
}

function buildAreaLegend(assessment, legendUrl) {
  const legend = element("section", "visual-legend area-legend");
  legend.append(element("strong", "visual-legend-title", "Legenda delov parcele"));
  const grid = element("div", "area-legend-grid");
  const items = assessment?.items || [];
  if (!items.length) {
    grid.append(element("p", "area-legend-empty", "Uradni šifrant za to parcelo ni vrnil razlage."));
  }
  items.forEach((item) => {
    const row = element("div", `area-legend-item tone-${item.tone}`);
    const copy = element("div");
    copy.append(element("strong", "", item.name), element("span", "", item.label));
    const measure = [];
    if (item.parcel_share_percent != null) measure.push(`${item.parcel_share_percent}%`);
    if (item.parcel_area_m2 != null) measure.push(`${formatNumber(item.parcel_area_m2)} m²`);
    row.append(element("b", "area-code", item.code || "—"), copy, element("em", "", measure.join(" · ") || "delež ni na voljo"));
    grid.append(row);
  });
  legend.append(grid);

  if (legendUrl) {
    const official = element("details", "planning-map-legend");
    official.append(element("summary", "", "Odpri celotno uradno barvno legendo PIS"));
    const legendImage = element("img", "");
    legendImage.src = legendUrl;
    legendImage.alt = "Uradna legenda namenske rabe prostora";
    legendImage.loading = "lazy";
    official.append(legendImage);
    legend.append(official);
  }
  return legend;
}

function renderLandUse(assessment) {
  const container = document.querySelector("#land-use-assessment");
  if (!assessment) {
    container.append(element("div", "empty-card", "Namenske rabe ni bilo mogoče oceniti."));
    return;
  }
  const card = element("article", `assessment-card tone-${assessment.tone}`);
  const header = element("div", "assessment-head");
  const result = element("div");
  result.append(element("span", "assessment-label", "Orientacijska ocena"), element("h4", "", assessment.label), element("p", "", assessment.summary));
  header.append(statusMark(assessment.tone), result);
  const items = element("div", "land-use-items");
  (assessment.items || []).forEach((item) => {
    const row = element("div", `land-use-row tone-${item.tone}`);
    const code = element("span", "land-use-code", item.code || "—");
    const copy = element("div");
    const measurements = [];
    if (item.parcel_share_percent != null) measurements.push(`${item.parcel_share_percent}% parcele`);
    if (item.parcel_area_m2 != null) measurements.push(`približno ${formatNumber(item.parcel_area_m2)} m²`);
    copy.append(element("strong", "", item.name));
    if (measurements.length) copy.append(element("span", "land-use-measure", measurements.join(" · ")));
    copy.append(element("span", "", item.label), element("p", "", item.explanation));
    row.append(code, copy);
    items.append(row);
  });
  card.append(header, items, element("p", "assessment-disclaimer", assessment.disclaimer));
  container.append(card);
}

function renderPlanningActs(acts) {
  const container = document.querySelector("#planning-acts");
  if (!acts.length) {
    container.append(element("div", "empty-card", "PIS ni vrnil prostorskega akta, ki bi prostorsko presekal parcelo."));
    return;
  }
  acts.forEach((act) => {
    const card = element("article", "planning-act-card");
    const badges = element("div", "badges");
    badges.append(element("span", "badge", act.preparation_state === "completed" ? "Zaključen" : "V pripravi"));
    if (act.status) badges.append(element("span", "badge", act.status));
    const title = element("div", "planning-act-copy");
    title.append(badges, element("strong", "", act.title), element("p", "", act.act_type || "Vrsta akta ni navedena"));
    const stats = element("div", "act-stats");
    stats.append(statChip(act.document_count, "PDF"), statChip(act.literal_mention_count, "omemb parcele"), link(act.page_url, "PIS zapis ↗"));
    card.append(title, stats);
    container.append(card);
  });
}

function renderInfrastructure(items) {
  const container = document.querySelector("#infrastructure");
  if (!items.length) {
    container.append(element("div", "empty-card", "Podatki GJI niso na voljo."));
    return;
  }
  items.forEach((item) => {
    const statusTone = item.status === "on_parcel" ? "positive" : item.status === "nearby" ? "caution" : item.status === "unavailable" ? "unknown" : "concern";
    const card = element("article", `utility-point tone-${statusTone}`);
    const head = element("div", "utility-point-head");
    const copy = element("div");
    copy.append(element("strong", "", item.name), element("span", "", item.label));
    head.append(element("i", `traffic-dot tone-${statusTone}`), copy);
    card.append(head);
    const details = element("details", "compact-details");
    details.append(element("summary", "", "Podrobnosti"));
    const list = element("ul", "compact-points");
    (item.details || []).forEach((detail) => list.append(element("li", "", detail)));
    list.append(element("li", "", item.note));
    details.append(list);
    card.append(details);
    container.append(card);
  });
}

function renderRoadAccess(access) {
  const container = document.querySelector("#road-access");
  if (!access) {
    container.append(element("div", "empty-card", "Cestnega dostopa ni bilo mogoče preveriti."));
    return;
  }
  const card = element("article", `road-point tone-${access.tone}`);
  const head = element("div", "road-point-head");
  const copy = element("div");
  copy.append(element("strong", "", access.label), element("span", "", "Kartografski pokazatelj"));
  head.append(element("i", `traffic-dot tone-${access.tone}`), copy);
  const points = element("ul", "compact-points road-points");
  points.append(element("li", "", access.physical_evidence), element("li", "", access.legal_status));
  const details = element("details", "compact-details");
  details.append(element("summary", "", "Kaj ta podatek pomeni"), element("p", "", access.note));
  card.append(head, points, details);
  container.append(card);
}

function renderRiskScore(selector, title, findings, emptyText) {
  const container = document.querySelector(selector);
  const score = gradeFindings(findings);
  const card = element("article", `risk-score-card grade-${score.value}`);
  const head = element("div", "risk-score-head");
  const grade = element("strong", "risk-grade", score.value);
  const copy = element("div");
  copy.append(element("span", "", title), element("b", "", score.label));
  head.append(grade, copy, element("i", `traffic-light grade-${score.value}`));
  card.append(head);

  if (!findings.length) {
    card.append(element("p", "risk-score-note", emptyText));
    container.append(card);
    return;
  }

  const details = element("details", "risk-details");
  details.append(element("summary", "", `Prikaži ${findings.length} ${findings.length === 1 ? "zadetek" : "zadetke"}`));
  const list = element("div", "spatial-findings");
  findings.forEach((finding) => {
    list.append(buildSpatialFinding(finding));
  });
  details.append(list);
  card.append(details);
  container.append(card);
}

function gradeFindings(findings) {
  const concerns = findings.filter((item) => item.tone === "concern").length;
  const cautions = findings.filter((item) => item.tone === "caution" || item.tone === "unknown").length;
  if (concerns >= 2) return { value: 1, label: "Več pomembnih omejitev" };
  if (concerns === 1) return { value: 2, label: "Pomembna omejitev" };
  if (cautions >= 2) return { value: 3, label: "Opozorila – preveriti" };
  if (cautions === 1) return { value: 4, label: "Brez pomembne omejitve" };
  return { value: 5, label: findings.length ? "Brez omejitev" : "Brez zadetka – brez omejitev" };
}

function buildSpatialFinding(finding) {
  const card = element("article", `spatial-finding tone-${finding.tone}`);
  const head = element("div", "spatial-finding-head");
  const copy = element("div");
  copy.append(element("span", "", finding.category), element("strong", "", finding.name));
  head.append(statusMark(finding.tone), copy);
  card.append(head);
  if (finding.detail) card.append(element("p", "", finding.detail));
  const sourceText = finding.reference ? `${finding.source} · ${finding.reference}` : finding.source;
  card.append(link(finding.source_url, `${sourceText} ↗`, "finding-source"));
  return card;
}

function statusMark(tone) {
  const symbols = { positive: "✓", caution: "!", concern: "×", neutral: "·", unknown: "?" };
  return element("span", `status-mark tone-${tone}`, symbols[tone] || "?");
}

function renderParcel(parcel) {
  const overview = document.querySelector("#parcel-overview");
  const card = element("article", "parcel-card");
  const primary = element("div", "parcel-primary");
  const id = element("div", "parcel-id");
  id.append(element("span", "", "Official parcel reference"), element("strong", "", `${parcel.cadastral_municipality_id} ${parcel.parcel_number}`));
  const place = element("div", "parcel-place");
  place.append(element("strong", "", parcel.municipality || "Municipality unavailable"), document.createTextNode(parcel.cadastral_municipality || "Cadastral municipality unavailable"));
  primary.append(id, place);

  const facts = element("div", "fact-grid");
  const factRows = [
    ["Area", parcel.area_m2 == null ? "Not available" : `${formatNumber(parcel.area_m2)} m²`, parcel.area_determination_method],
    ["Boundary status", parcel.administrative_status, null],
    ["Land quality score", parcel.quality_score, null],
    ["Cadastral income", parcel.cadastral_income_eur == null ? "Not available" : formatMoney(parcel.cadastral_income_eur), null],
    ["Building parcel", parcel.building_parcel, null],
    ["Restriction in KN", parcel.restriction_recorded, null],
  ];
  factRows.forEach(([label, value, note]) => {
    const fact = element("div", "fact");
    fact.append(element("span", "", label), element("strong", "", value ?? "Not available"));
    if (note) fact.append(element("small", "", note));
    facts.append(fact);
  });
  card.append(primary, facts);

  const valuation = element("aside", "valuation-card");
  valuation.append(element("span", "", "GURS generalized value"), element("strong", "", formatMoney(parcel.official_valuation_eur)));
  valuation.append(element("p", "", "Model-based official value, not an individual appraisal or sale price."));
  (parcel.valuation_units || []).forEach((unit) => {
    const row = element("div", "valuation-row");
    const label = element("span", "", `${unit.model_code || "Model"} · ${unit.model_name || "valuation unit"}`);
    if (unit.area_share_percent != null) label.append(document.createTextNode(` · ${unit.area_share_percent}%`));
    row.append(label, element("b", "", formatMoney(unit.generalized_value_eur)));
    valuation.append(row);
  });
  (parcel.land_use || []).forEach((land) => {
    const row = element("div", "valuation-row");
    row.append(element("span", "", "Recorded use"), element("b", "", `${land.name}${land.share_percent == null ? "" : ` · ${land.share_percent}%`}`));
    valuation.append(row);
  });
  overview.append(card, valuation);
}

function renderContext(contexts) {
  const container = document.querySelector("#planning-context");
  if (!contexts.length) {
    container.append(element("div", "empty-card", "Za parcelo ni bil vrnjen strukturiran podatek o namenski rabi OPN."));
    return;
  }
  contexts.forEach((context) => {
    const card = element("article", "context-card");
    card.append(element("span", "context-code", context.land_use_code || "Brez oznake"));
    const share = context.parcel_share_percent == null ? "" : ` · ${context.parcel_share_percent}% parcele`;
    card.append(element("strong", "", `${context.land_use_description || "Opis namenske rabe ni na voljo"}${share}`));
    const detail = [context.planning_unit && `EUP ${context.planning_unit}`, context.subunit && `subunit ${context.subunit}`].filter(Boolean).join(" · ");
    card.append(element("p", "", detail || context.act_title || "Prostorska enota ni na voljo"));
    container.append(card);
  });
}

function renderDocuments(documents, warnings) {
  const stats = document.querySelector("#document-stats");
  const referenced = documents.filter((document) => document.mention_count > 0).length;
  stats.replaceChildren(statChip(documents.length, "PDF dokumentov"), statChip(referenced, "z neposredno omembo"));
  const warningContainer = document.querySelector("#global-warnings");
  warnings.forEach((warning) => warningContainer.append(element("div", "warning", warning)));
  const list = document.querySelector("#document-list");
  if (!documents.length) {
    list.append(element("div", "empty-card", "Povezani prostorski akti PIS niso vrnili PDF-ja za prenos."));
    return;
  }
  documents.forEach((document) => list.append(documentCard(document)));
}

function statChip(value, label) {
  const chip = element("span", "stat-chip");
  chip.append(element("strong", "", value), document.createTextNode(` ${label}`));
  return chip;
}

function documentCard(documentData) {
  const card = element("article", "document-card");
  const head = element("header", "document-head");
  const titleArea = element("div");
  const badges = element("div", "badges");
  badges.append(element("span", "badge", documentData.act_type || "Prostorski akt"));
  badges.append(element("span", "badge", documentData.act_status || "Status ni na voljo"));
  const mentionLabel = documentData.mention_count === 0
    ? "prostorska povezava PIS"
    : documentData.mention_count === 1
      ? "1 neposredna omemba parcele"
      : `${documentData.mention_count} neposrednih omemb parcele`;
  badges.append(element("span", `badge ${documentData.mention_count ? "mention" : "zero"}`, mentionLabel));
  titleArea.append(badges, element("h3", "", documentData.pdf_title), element("p", "", `${documentData.act_title} · ${formatBytes(documentData.size_bytes)}`));
  const actions = element("div", "document-actions");
  actions.append(link(documentData.act_page_url, "Zapis PIS ↗"), link(documentData.pdf_download_url, "Odpri PDF ↗", "primary"));
  head.append(titleArea, actions);

  const body = element("div", "document-body");
  const label = element("div", "summary-label", "Povzetek za parcelo");
  label.append(element("span", "", documentData.summary_provider));
  body.append(label, element("p", "summary", documentData.summary));

  if (documentData.important_findings?.length) {
    const findings = element("div", "findings");
    documentData.important_findings.forEach((findingData) => {
      const finding = element("div", `finding ${findingData.importance}`);
      finding.append(element("strong", "", findingData.category), element("p", "", findingData.detail));
      if (findingData.pages?.length) finding.append(element("small", "", `Strani ${findingData.pages.join(", ")}`));
      findings.append(finding);
    });
    body.append(findings);
  }

  if (documentData.excerpts?.length) {
    const details = element("details");
    details.append(element("summary", "", `Prikaži relevantne odlomke (${documentData.excerpts.length})`));
    documentData.excerpts.forEach((excerptData) => {
      const excerpt = element("blockquote", "excerpt");
      excerpt.append(element("b", "", `Stran ${excerptData.page}${excerptData.section ? ` · ${excerptData.section}` : ""}`), document.createTextNode(excerptData.text));
      details.append(excerpt);
    });
    body.append(details);
  }
  (documentData.extraction_warnings || []).forEach((warning) => body.append(element("div", "extraction-note", `Opomba: ${warning}`)));
  card.append(head, body);
  return card;
}

function link(href, text, className = "") {
  const anchor = element("a", className, text);
  anchor.href = href;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  return anchor;
}

function initializePrivacyControls() {
  if (!privacyConsent) {
    window.localStorage.removeItem(RECENT_SEARCHES_KEY);
    forgetAnalyticsVisitor();
    cookieBanner.hidden = false;
  } else {
    applyStoredConsent();
  }

  document.querySelector("#cookie-reject")?.addEventListener("click", () => {
    savePrivacyConsent({ functional: false, analytics: false });
  });
  document.querySelector("#cookie-accept")?.addEventListener("click", () => {
    savePrivacyConsent({ functional: true, analytics: true });
  });
  document.querySelector("#cookie-save")?.addEventListener("click", () => {
    savePrivacyConsent({
      functional: functionalConsentInput.checked,
      analytics: analyticsConsentInput.checked,
    });
  });
  document.querySelector("#cookie-dialog-accept")?.addEventListener("click", () => {
    savePrivacyConsent({ functional: true, analytics: true });
  });

  document.querySelectorAll("[data-cookie-settings]").forEach((control) => {
    control.addEventListener("click", openCookieSettings);
  });
  document.querySelectorAll("[data-cookie-close]").forEach((control) => {
    control.addEventListener("click", () => cookieDialog.close());
  });
  document.querySelectorAll("[data-privacy-open]").forEach((control) => {
    control.addEventListener("click", openPrivacyPolicy);
  });
  document.querySelectorAll("[data-privacy-close]").forEach((control) => {
    control.addEventListener("click", () => privacyDialog.close());
  });

  [cookieDialog, privacyDialog].forEach((dialog) => {
    dialog?.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
}

function openCookieSettings() {
  if (privacyDialog?.open) privacyDialog.close();
  functionalConsentInput.checked = Boolean(privacyConsent?.functional);
  analyticsConsentInput.checked = Boolean(privacyConsent?.analytics);
  if (!cookieDialog.open) cookieDialog.showModal();
}

function openPrivacyPolicy() {
  if (cookieDialog?.open) cookieDialog.close();
  if (!privacyDialog.open) privacyDialog.showModal();
}

function savePrivacyConsent(selection) {
  const previousAnalytics = Boolean(privacyConsent?.analytics);
  privacyConsent = {
    version: CONSENT_VERSION,
    necessary: true,
    functional: Boolean(selection.functional),
    analytics: Boolean(selection.analytics),
    updated_at: new Date().toISOString(),
  };
  setBrowserCookie(CONSENT_COOKIE, JSON.stringify(privacyConsent), CONSENT_MAX_AGE_SECONDS);

  if (!privacyConsent.functional) {
    window.localStorage.removeItem(RECENT_SEARCHES_KEY);
  }
  if (previousAnalytics && !privacyConsent.analytics) {
    forgetAnalyticsVisitor();
    disableGoogleAnalytics();
  }

  applyStoredConsent();
  cookieBanner.hidden = true;
  if (cookieDialog.open) cookieDialog.close();
}

function applyStoredConsent() {
  functionalConsentInput.checked = Boolean(privacyConsent?.functional);
  analyticsConsentInput.checked = Boolean(privacyConsent?.analytics);
  renderRecentSearches();
  if (privacyConsent?.analytics) enableGoogleAnalytics();
  else disableGoogleAnalytics();
}

function readPrivacyConsent() {
  const encoded = readBrowserCookie(CONSENT_COOKIE);
  if (!encoded) return null;
  try {
    const value = JSON.parse(encoded);
    if (
      value.version !== CONSENT_VERSION
      || value.necessary !== true
      || typeof value.functional !== "boolean"
      || typeof value.analytics !== "boolean"
    ) return null;
    return value;
  } catch {
    return null;
  }
}

function setBrowserCookie(name, value, maxAgeSeconds) {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}; Path=/; SameSite=Lax${secure}`;
}

function readBrowserCookie(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split("; ").find((entry) => entry.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

function rememberRecentSearch(parcelReference) {
  if (!privacyConsent?.functional) return;
  const now = Date.now();
  const searches = loadRecentSearches()
    .filter((item) => item.value !== parcelReference);
  searches.unshift({ value: parcelReference, saved_at: now });
  window.localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(searches.slice(0, 10)));
  renderRecentSearches();
}

function loadRecentSearches() {
  if (!privacyConsent?.functional) return [];
  try {
    const cutoff = Date.now() - RECENT_SEARCH_MAX_AGE_MS;
    const stored = JSON.parse(window.localStorage.getItem(RECENT_SEARCHES_KEY) || "[]");
    const active = stored
      .filter((item) => typeof item?.value === "string" && Number(item.saved_at) >= cutoff)
      .slice(0, 10);
    if (active.length !== stored.length) {
      window.localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(active));
    }
    return active;
  } catch {
    window.localStorage.removeItem(RECENT_SEARCHES_KEY);
    return [];
  }
}

function renderRecentSearches() {
  recentParcels.replaceChildren();
  loadRecentSearches().forEach((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    recentParcels.append(option);
  });
}

function recordSearchEvent(parcelReference) {
  if (!privacyConsent?.analytics) return;
  fetch("/api/privacy/events", {
    method: "POST",
    credentials: "same-origin",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_type: "parcel_search",
      parcel_reference: parcelReference,
      analytics_consent: true,
      consent_version: CONSENT_VERSION,
    }),
  }).catch(() => {});
}

function forgetAnalyticsVisitor() {
  fetch("/api/privacy/visitor", {
    method: "DELETE",
    credentials: "same-origin",
    keepalive: true,
  }).catch(() => {});
}
