from pathlib import Path
import json

from lxml import html


ROOT = Path(__file__).resolve().parents[1]


def _page():
    return html.parse(ROOT / "static" / "index.html").getroot()


def test_results_are_split_into_accessible_tabs():
    page = _page()
    tabs = page.xpath("//*[@id='result-tabs']//*[@role='tab']")

    assert [tab.get("data-result-tab") for tab in tabs] == [
        "overview",
        "report",
        "technical",
        "acts",
    ]
    assert "Hitri pregled parcele" in tabs[0].text_content()
    assert "Lokacijska informacija" in tabs[1].text_content()
    assert "is-featured" in tabs[1].get("class", "").split()
    assert "Prostorski akti" in tabs[-1].text_content()
    assert tabs[0].get("aria-selected") == "true"
    assert tabs[1].get("aria-selected") == "false"
    assert tabs[2].get("aria-selected") == "false"
    assert tabs[3].get("aria-selected") == "false"


def test_planning_acts_and_documents_are_not_in_overview_panel():
    page = _page()
    overview = page.get_element_by_id("result-panel-overview")
    acts = page.get_element_by_id("result-panel-acts")
    technical = page.get_element_by_id("result-panel-technical")

    assert overview.xpath(".//*[@id='parcel-visuals']")
    assert not overview.xpath(".//*[@id='planning-acts']")
    assert not overview.xpath(".//*[@id='documents']")
    assert acts.xpath(".//*[@id='planning-context']")
    assert acts.xpath(".//*[@id='planning-acts']")
    assert technical.xpath(".//*[@id='documents']")
    assert "Sekcija za projektante" in technical.text_content()


def test_risk_scale_explains_every_grade():
    page = _page()
    overview = page.get_element_by_id("result-panel-overview")
    grades = overview.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' grade-scale ')]//b/text()")

    assert grades == ["5", "4", "3", "2", "1"]
    for element_id in ("protected-areas", "cultural-heritage", "constraints", "risks"):
        assert overview.xpath(f".//*[@id='{element_id}']")


def test_overview_uses_requested_single_and_four_column_layouts():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert ".compact-access { grid-template-columns: 1fr; }" in styles
    assert ".compact-access .infrastructure-grid { grid-template-columns: 1fr; gap: 7px; }" in styles
    assert ".risk-score-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));" in styles


def test_frontend_builds_an_image_tab_for_each_visual():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function renderVisualGallery" in script
    assert 'label: "Ortofoto"' in script
    assert 'label: "Namenska raba"' in script
    assert 'index === 0 ? "Izris iz prostorskega reda"' in script
    assert "function buildAreaLegend" in script
    assert "function gradeFindings" in script


def test_location_report_includes_the_planning_drawing_and_legend():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function buildOfficialMapAttachment" in script
    assert "mapAttachment: planningDrawing?.preview_url" in script
    assert "buildAreaLegend(assessment, planningMap?.legend_url)" in script


def test_document_explanations_are_presented_in_slovenian():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert '"Povzetek za parcelo"' in script
    assert '"prostorska povezava PIS"' in script
    assert "neposredna omemba parcele" in script
    assert '"Odpri PDF ↗"' in script
    assert "Parcel-specific summary" not in script
    assert "literal mention" not in script


def test_analysis_requires_a_user_triggered_turnstile_check():
    page = _page()
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert page.xpath("//*[@id='captcha-panel' and @hidden]")
    assert page.xpath("//*[@id='turnstile-widget']")
    assert page.xpath("//*[@id='captcha-check' and @disabled]")
    assert "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" in script
    assert 'action: "parcel_search"' in script
    assert 'appearance: "interaction-only"' in script
    assert 'execution: "execute"' in script
    assert 'window.turnstile.execute("#turnstile-widget")' in script
    assert "captcha_token: captchaToken" in script
    assert "captchaVerifiedToken = token" in script
    assert 'buttonLabel.textContent = "Analiziraj"' in script
    assert "async function beginParcelSearch" in script


def test_propioscan_brand_and_legal_footer_are_present():
    page = _page()

    assert page.xpath("//title[text()='Pregled parcele in prostorski akti | Propioscan']")
    assert page.xpath("//link[@rel='canonical' and @href='https://propioscan.com/']")
    assert page.xpath("//img[@src='/static/propioscan-mark.png']")
    assert "Propioscan" in page.text_content()

    footer = page.get_element_by_id("legal-notice")
    footer_text = " ".join(footer.text_content().split())
    assert "Podjetje in licenca" in footer_text
    assert "Vse pravice pridržane" in footer_text
    assert "CC BY 4.0" in footer_text
    assert "rezultati so informativni" in footer_text


def test_homepage_has_complete_search_and_social_metadata():
    page = _page()

    assert page.xpath("//meta[@name='description' and contains(@content, 'GURS in PIS')]")
    assert page.xpath("//meta[@name='robots' and contains(@content, 'index, follow')]")
    assert page.xpath("//meta[@property='og:locale' and @content='sl_SI']")
    assert page.xpath("//meta[@property='og:image' and starts-with(@content, 'https://propioscan.com/')]")
    assert page.xpath("//meta[@name='twitter:card' and @content='summary_large_image']")
    assert page.xpath("//link[@rel='alternate' and @hreflang='sl-SI']")
    assert page.xpath("//h1[contains(normalize-space(.), 'Pregled parcele')]")


def test_homepage_structured_data_matches_visible_service():
    page = _page()
    scripts = page.xpath("//script[@type='application/ld+json']/text()")

    assert len(scripts) == 1
    data = json.loads(scripts[0])
    types = {entry["@type"] for entry in data["@graph"]}
    assert types == {"Organization", "WebSite", "SoftwareApplication"}
    application = next(entry for entry in data["@graph"] if entry["@type"] == "SoftwareApplication")
    assert application["offers"] == {"@type": "Offer", "price": "0", "priceCurrency": "EUR"}
    assert application["operatingSystem"] == "Any"

    overview = page.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' seo-overview ')]")[0]
    overview_text = " ".join(overview.text_content().split())
    for phrase in ("Namenska raba", "prostorski akti", "Infrastruktura", "omejitve", "PDF izpis"):
        assert phrase in overview_text


def test_desktop_utility_bar_has_no_source_status_or_developer_metrics():
    page = _page()
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert not page.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' header-status ')]")
    assert not page.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' developer-zone ')]")
    assert not page.xpath("//*[@id='developer-model' or @id='developer-used' or @id='developer-remaining']")
    assert "renderDeveloperUsage" not in script


def test_cookie_controls_are_consent_first_and_reopenable():
    page = _page()
    source = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    banner = page.get_element_by_id("cookie-banner")
    assert banner.get("hidden") == ""
    assert banner.xpath(".//*[@id='cookie-reject']")
    assert banner.xpath(".//*[@id='cookie-accept']")
    assert page.xpath("//*[@id='cookie-functional']")
    assert page.xpath("//*[@id='cookie-analytics']")
    assert len(page.xpath("//*[@data-cookie-settings]")) >= 2
    assert "fonts.googleapis.com" not in source

    assert 'if (!privacyConsent?.functional) return;' in script
    assert 'if (!privacyConsent?.analytics) return;' in script
    assert 'window.localStorage.removeItem(RECENT_SEARCHES_KEY)' in script
    assert 'fetch("/api/privacy/events"' in script
    assert 'const CONSENT_VERSION = "1.2"' in script
    assert "analytics_consent: Boolean(privacyConsent?.analytics)" in script
    assert "if (!privacyConsent?.analytics) return false;" in script
    assert "https://www.googletagmanager.com/gtag/js?id=" in script
    assert 'analytics_storage: "denied"' in script
    assert 'ad_storage: "denied"' in script
    assert 'ad_user_data: "denied"' in script
    assert 'ad_personalization: "denied"' in script
    assert "allow_google_signals: false" in script
    assert "allow_ad_personalization_signals: false" in script
    assert "parcel_analysis_completed" in script
    assert "location_report_downloaded" in script
    assert "parcel_reference" not in script[script.index("function recordGoogleEvent"):script.index("function analysisDurationParameters")]
    assert "googletagmanager.com" not in source


def test_privacy_notice_covers_core_disclosures():
    page = _page()
    policy = page.get_element_by_id("privacy-dialog")
    policy_text = " ".join(policy.text_content().split())

    for required_text in (
        "Upravljavec in kontakt",
        "Katere podatke obdelujemo",
        "Nameni in pravne podlage",
        "Viri, prejemniki in prenosi",
        "Roki hrambe",
        "Vaše pravice",
        "Informacijskem pooblaščencu",
        "operativni zapisi zahtev z IP-jem",
        "tehnično oznako skupine",
        "nista potrjena identiteta osebe",
        "Google Analytics 4",
        "Google tag se pred soglasjem ne naloži",
        "Google Signals",
        "parcelne oznake, ID-ja opravila",
    ):
        assert required_text in policy_text


def test_admin_panel_has_human_checked_login_filters_exports_and_logs():
    page = html.parse(ROOT / "static" / "admin.html").getroot()
    script = (ROOT / "static" / "admin.js").read_text(encoding="utf-8")

    assert page.xpath("//*[@id='admin-login-form']")
    assert page.xpath("//*[@id='admin-login-button' and @disabled]")
    assert page.xpath("//*[@id='admin-turnstile']")
    assert page.xpath("//*[@data-admin-tab='overview']")
    assert page.xpath("//*[@data-admin-tab='analytics']")
    assert page.xpath("//*[@data-admin-tab='requests']")
    assert page.xpath("//*[@data-admin-tab='logs']")
    assert page.xpath("//*[@id='statistics-download' and @download]")
    assert page.xpath("//*[@id='analytics-download' and @download]")
    assert page.xpath("//*[@id='analytics-period']")
    assert page.xpath("//*[@id='filter-group']")
    assert page.xpath("//*[@id='filter-ip']")
    assert 'action: "admin_login"' in script
    assert 'appearance: "interaction-only"' in script
    assert 'execution: "execute"' in script
    assert "window.turnstile.execute(turnstileWidget)" in script
    assert 'credentials: "same-origin"' in script
    assert "filterQuery(false)" in script
    assert 'api(`/api/admin/analytics?${query}`)' in script
    assert "renderAnalyticsSummary" in script
    assert "renderAnalyticsDaily" in script
    assert "Tehnična skupina ni oseba" in page.text_content()


def test_location_report_has_official_structure_and_local_download():
    page = _page()
    source = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    panel = page.get_element_by_id("result-panel-report")
    panel_text = " ".join(panel.text_content().split())

    assert "Lokacijska informacija" in panel_text
    assert "PDF z vsemi podatki" in panel_text
    assert "ne uradna listina" in panel_text
    assert page.xpath("//*[@id='report-download' and @download]")
    assert panel.xpath(".//*[@id='report-parcel-reference']")
    assert panel.xpath(".//*[@id='report-cadastral-municipality']")
    assert page.xpath("//a[@href='https://pisrs.si/api/datoteke/integracije/351620891']")
    assert 'number: 10' in script
    assert "function renderOfficialForm" in script
    assert "function officialSection" in script
    assert 'reportDownload.href = `/api/search/${encodeURIComponent(jobId)}/report`' in script
    assert "async function downloadLocationReport" in script
    assert 'headers: { Accept: "application/pdf" }' in script
    assert "Priloga: prostorski izvedbeni pogoji" in source or "Priloga: prostorski izvedbeni pogoji" in script
