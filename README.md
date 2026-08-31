# Propioscan

Propioscan is a web application for researching Slovenian cadastral parcels. One search combines the official GURS cadastral record and generalized valuation with spatial-plan matches from PIS, utility and road geometry from GJI, environmental and natural-risk overlays from GeoHub, and cultural-heritage regimes from eVRD. It also downloads the PDFs attached to matching PIS acts, locates literal parcel-number occurrences, and produces a parcel-focused summary.

The application uses the public [GURS WFS services](https://www.e-prostor.gov.si/dostopi/javni-dostop/) and the official [PIS spatial-planning WFS service](https://pis.eprostor.gov.si/pis/spletni-servisi?lang=sl). GURS states that its public data is available under CC BY 4.0; the UI attributes the source accordingly. PIS data and summaries are informational and must not replace verification with the competent municipality or the legally controlling publication.

## What is implemented

- Browser-only UI served by FastAPI; no desktop client.
- Canonical parcel input: `<cadastral municipality ID> <parcel number>`, for example `1723 123/4`.
- Live GURS cadastral area, status, quality score, cadastral income, municipality, land-use shares, and generalized value with valuation-unit breakdown.
- Exact parcel-geometry intersection against completed and in-preparation PIS planning-act layers.
- Structured OPN land-use and planning-unit context from PIS.
- Exact land-use shares clipped to the parcel, aggregated by official `NRP_OZN`
  code, translated with `NRP_OPIS`, and shown as both percentages and approximate
  square metres.
- An official PIS land-use map with the searched cadastral outline, the PIS colour
  legend, and the detected codes displayed directly in the result.
- Automatic selection of matching `kart_del/eup_nrp` GeoPDF sheets from downloaded
  planning-act archives, with a rendered preview and the searched parcel outlined
  in red. Literal parcel-label matching is used as a fallback for non-GeoPDF maps.
- A tabbed visual gallery gives the orthophoto, official land-use map, and every
  matching planning-map preview its own view, with parcel-specific code, share,
  area, and official colour legends where relevant.
- Planning acts are kept in a separate result tab, while full PDF evidence and
  extracted technical detail live in a clearly labelled `Sekcija za projektante`.
- A dedicated list of completed and in-preparation PIS spatial acts, document counts, and literal parcel-reference counts.
- Registered water, sewer, electricity, telecom, gas, and heat infrastructure on the parcel or within 100 metres from GURS GJI.
- Physical road-line proximity shown separately from the legally secured right of access to a public road.
- Intersections with protected nature, Natura 2000, natural values, caves, water-protection regimes, flood classes, erosion, landslide susceptibility, and inventoried landslides.
- Cultural-heritage protection-regime intersections from the Ministry of Culture eVRD service.
- Compact 1–5 screening scores summarize protected areas, heritage, constraints,
  and natural risks; the scale is explained in the UI and every underlying finding
  remains available in an expandable detail view.
- Official GURS orthophoto displayed in the page with a cadastral parcel-outline overlay and a link to the official viewer.
- Automatic per-act PIS ZIP retrieval using the public document page, safe extraction of every PDF, SHA-256 metadata, and persistent cache.
- Text extraction page by page, slash/spacing-tolerant parcel matching, section-heading detection, excerpts, and occurrence counts.
- Optional OCR through an installed `ocrmypdf` executable; scanned-page limitations are shown per document when OCR is unavailable.
- Optional OpenAI parcel-specific structured summaries. Without an API key, the app remains fully usable and shows a clearly labelled extractive fallback.
- Background jobs with progress polling, so long downloads do not hold open one browser request.
- Thread-backed jobs are the local-development default. WSGI/Passenger hosting
  can set `JOB_EXECUTION_MODE=process` (and, when needed,
  `JOB_PYTHON_EXECUTABLE`) to detach each analysis from the short-lived web
  worker that accepted it.
- Cloudflare Turnstile protects the analysis endpoint with a user-triggered,
  server-validated, single-use anti-bot token. Local development uses Cloudflare's
  interactive test widget; production must replace both test keys.
- A fourth results tab maps the analysis into all 10 sections of the official
  location-information template and downloads a branded, pre-filled PDF. Fields
  that cannot be established from the checked sources are explicitly marked for
  municipal confirmation; the file is never presented as an official document.
- File-size limits, fixed official hosts, safe ZIP handling, generated local filenames, and constrained PDF-serving routes.
- A protected `/admin` panel with Turnstile-checked login, rate limiting,
  eight-hour signed sessions, parcel-request filters, approximate visitor
  grouping, CSV export for active filters/grouping, and redacted application
  logs. Raw credentials are never stored in the request database.

## Run locally

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. API documentation is available at <http://127.0.0.1:8000/docs>.

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. You can also run `python main.py` after installing dependencies.

## AI summaries

Set `OPENAI_API_KEY` in `.env` to enable AI summaries. The configured default is `gpt-5.6-sol`, resolved from the current OpenAI model guidance when this project was created; change `OPENAI_MODEL` to a model available to your account if needed. The app uses the Responses API with a strict Pydantic output type and sends only the parcel-specific excerpts plus structured PIS context—not whole PDFs.

```dotenv
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.6-sol
SUMMARY_LANGUAGE=Slovenian
```

The summary prompt explicitly forbids inventing permissions or restrictions and requires page evidence. Model output is still not a legal opinion.

## OCR

`pypdf` handles searchable PDFs. For scans, install [OCRmyPDF](https://ocrmypdf.readthedocs.io/) and Slovenian plus English Tesseract language data, then leave `PDF_ENABLE_OCR=true`. The app detects mostly textless PDFs, creates a cached OCR copy, and re-runs extraction. When OCR is not present, the result warns that scanned-page references may be missing.

## Data and cache

Runtime files are written below `DATA_DIR` (default `./data`):

```text
data/
├── archives/       # one source ZIP per PIS procedure
├── pdfs/<act-id>/  # extracted PDFs and cache manifest
├── ocr/            # optional OCR-enhanced PDFs
├── map_previews/   # cached PNG previews of matching official map sheets
├── jobs/           # disk-backed analysis results, retained for 30 days
├── privacy/        # consent-gated pseudonymous analytics events
├── traffic/        # bounded request and login-audit database
└── logs/           # detached analysis-worker log
```

Repeat searches reuse the manifest and downloaded PDFs. Delete a specific act directory and its matching ZIP when you intentionally want a fresh copy. Archive and PDF limits are controlled by `MAX_ARCHIVE_MB` and `MAX_PDF_MB`.

## Privacy and cookies

Propioscan is consent-first for optional storage. Before a visitor makes a
choice, the application does not set optional cookies or write consent-gated
analytics events. The interface offers
equally accessible `Samo nujni`, granular settings, and `Sprejmi vse` actions,
and the footer keeps cookie settings and the privacy notice permanently
available.

- The necessary `propioscan_cookie_consent` cookie stores the selected
  categories for 180 days.
- Functional consent stores up to 10 recent parcel searches in browser-local
  storage for 30 days. It is not sent to the server for this feature.
- Analytics consent writes the searched parcel reference, timestamp, consent
  version, and a random pseudonymous browser ID to
  `data/privacy/events.jsonl`. It does not add a name, email, IP address, user
  agent, or document contents to that record.
- When a visitor submits an analysis, a separate operational record stores the
  parcel reference, job/status, timestamp, IP, user agent, derived device,
  browser/OS family, language and referring host for security, reliability and
  diagnostics. These records are deleted after `TRAFFIC_RETENTION_DAYS` (30 by
  default). A keyed technical `T-*` group derived from IP and user agent is an
  approximation, not a verified person.
- The analytics visitor cookie is HttpOnly, SameSite=Lax, and Secure on HTTPS.
  Events are purged after `PRIVACY_RETENTION_DAYS` (90 by default). Withdrawing
  analytics consent deletes the cookie and its associated events immediately.
- The frontend no longer contacts Google Fonts before consent; it uses system
  fonts.
- Generated location-information PDFs are returned directly to the requesting
  browser, are not persisted as user reports, and are not added to the analytics
  event log.
- Completed and failed job results are deleted after `JOB_RETENTION_DAYS` (30 by
  default). The necessary HttpOnly admin-session cookie is set only after a
  successful admin login and lasts no more than eight hours.

Before a public launch, replace the generic controller details in the privacy
notice with the registered company name, address, registration/VAT details,
and confirmed processor/DPA information. The implementation is a technical
baseline, not legal advice.

## Admin panel

The panel is available at `/admin`. Production requires four private values:
`ADMIN_USERNAME`, a scrypt `ADMIN_PASSWORD_HASH`, `ADMIN_SESSION_SECRET`, and
`TRAFFIC_GROUP_SECRET`. Keep them only in the server's private `.env`; the
example file deliberately contains blanks.

Generate a hash and random secrets locally without putting a plaintext password
in source control:

```bash
python -c 'import getpass; from app.admin import hash_password; print(hash_password(getpass.getpass("Admin password: ")))'
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Use the first random value as `ADMIN_SESSION_SECRET` and the second as
`TRAFFIC_GROUP_SECRET`. Production must also use real Cloudflare Turnstile keys;
the test keys in `.env.example` are refused on public hostnames.

Some Python certificate stores currently cannot build the certificate chain presented by the PIS document host, although browsers may succeed. `PIS_VERIFY_SSL=false` is therefore the local default for that fixed official hostname only. Set it to `true` when verification works in your environment. The GURS/PIS WFS host always uses normal certificate verification.

## Important interpretation boundary

The app keeps several legally different signals separate:

1. **Spatial applicability:** the official PIS geometry intersects the GURS parcel geometry.
2. **Literal reference:** the parcel number appears in extractable PDF text.
3. **Registered infrastructure:** a GJI object is mapped on or near the parcel; this does not prove that the parcel has a connection, spare capacity, or operator approval.
4. **Mapped road proximity:** a GJI road line touches or lies near the parcel; this does not prove public-road status, ownership, an easement, or an approved access connection.
5. **Registry intersection:** a protected-area, risk, or heritage geometry intersects the parcel; the controlling regime and current legal effect must still be checked in the source act and with the competent authority.

A plan can apply spatially without spelling out every parcel in its textual PDF, particularly when the controlling information is graphical. Conversely, an old or contextual parcel list can contain the number without granting a current building right. The interface keeps these signals separate and always links to the PIS record and local PDF evidence.

Parcel numbers are not nationally unique. A cadastral municipality ID is required to identify the GURS geometry. Literal PDF matching uses the parcel-number component because acts often identify the cadastral municipality by name rather than numeric ID; users should check the excerpt when a short/common number is involved.

## Architecture

```text
Browser → FastAPI job API → ParcelSearchService
                            ├── GURSClient (KN + EV WFS)
                            ├── PISClient (planning WFS)
                            ├── SiteAnalysisClient (GJI + GeoHub + eVRD)
                            ├── PISArchiveDownloader (JSF ZIP + PDF cache)
                            ├── PDFParser (pypdf + optional OCR)
                            └── ParcelSummarizer (OpenAI or extractive fallback)
```

Key modules are in `app/gurs.py`, `app/pis.py`, `app/site_analysis.py`, `app/pdf_downloader.py`, `app/pdf_parser.py`, `app/parcel_analyzer.py`, and `app/ai_summary.py`. New official sources can be added behind the orchestration in `app/service.py` without changing the browser job protocol.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The test suite uses mocked official endpoints and an in-memory PIS ZIP. It verifies GURS aggregation, PIS spatial queries and deduplication, safe PDF caching, parcel-boundary matching, heading context, and findings classification.

## Production deployment

Keep production values in an untracked `.env`; never commit API keys, Turnstile
secret keys, SSH keys, downloaded planning documents, parcel-result data, or
deployment backups. `.env.example` contains only blank values and Cloudflare's
published localhost test credentials.

The application can be started directly with Uvicorn as shown above or built
with the included `Dockerfile`. On WSGI-only CloudLinux hosting,
`passenger_wsgi.py` delegates to the fork-safe synchronous bridge in
`propioscan_wsgi.py`; configure detached process jobs so an LSAPI recycle cannot
interrupt a running analysis. Hosting-account paths, credentials, private keys,
and provider-specific deployment state intentionally do not belong in this
repository.
