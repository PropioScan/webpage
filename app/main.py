from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .admin import (
    ADMIN_COOKIE,
    AdminAuth,
    AdminAuthenticationError,
    AdminConfigurationError,
    AdminLogReader,
    AdminRateLimitError,
)
from .captcha import (
    HUMAN_CHECK_COOKIE,
    HUMAN_CHECK_MAX_AGE_SECONDS,
    CaptchaRejectedError,
    CaptchaUnavailableError,
    TurnstileVerifier,
)
from .config import settings
from .ga4 import Ga4ConfigurationError, Ga4QueryError, Ga4Reporter
from .jobs import JobManager
from .models import AdminLoginRequest, JobView, PrivacyEventRequest, SearchRequest
from .privacy import PrivacyEventStore
from .report import (
    generate_location_report,
    prepare_report_regime_map_preview,
    report_filename,
    resolve_report_map_preview,
)
from .service import ParcelSearchService
from .traffic import RequestFilters, TrafficStore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings.ensure_directories()
service = ParcelSearchService(settings)
jobs = JobManager(
    service,
    settings.search_workers,
    settings.data_dir,
    execution_mode=settings.job_execution_mode,
    base_dir=settings.base_dir,
    python_executable=settings.job_python_executable,
    retention_days=settings.job_retention_days,
    result_cache_days=settings.result_cache_days,
)
privacy_events = PrivacyEventStore(
    settings.data_dir,
    retention_days=settings.privacy_retention_days,
)
traffic = TrafficStore(
    settings.data_dir,
    retention_days=settings.traffic_retention_days,
    group_secret=settings.traffic_group_secret or settings.admin_session_secret or "",
)
admin_auth = AdminAuth(
    username=settings.admin_username,
    password_hash=settings.admin_password_hash,
    session_secret=settings.admin_session_secret,
    traffic=traffic,
    session_hours=settings.admin_session_hours,
)
admin_logs = AdminLogReader(settings.base_dir, settings.data_dir)
ga4 = Ga4Reporter(
    settings.google_analytics_property_id,
    settings.google_analytics_credentials_file,
    timeout_seconds=settings.http_timeout_seconds,
)
captcha = TurnstileVerifier(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    privacy_events.purge()
    traffic.purge()
    jobs.purge_expired()
    privacy_cleanup = asyncio.create_task(_periodic_privacy_cleanup())
    try:
        yield
    finally:
        privacy_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await privacy_cleanup
        captcha.close()
        jobs.executor.shutdown(wait=False, cancel_futures=False)


async def _periodic_privacy_cleanup() -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        privacy_events.purge()
        traffic.purge()
        jobs.purge_expired()


app = FastAPI(
    title="Propioscan",
    version="1.7.0",
    description="Parcel-focused GURS, PIS, GJI, GeoHub, and eVRD research for Slovenia.",
    lifespan=lifespan,
)
static_dir = settings.base_dir / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def admin_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.url.path == "/admin" or request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; "
            "frame-src https://challenges.cloudflare.com; "
            "connect-src 'self' https://challenges.cloudflare.com; "
            "img-src 'self' data:; style-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    return Response(
        content=(
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /admin\n"
            "Disallow: /api/\n"
            "Sitemap: https://propioscan.com/sitemap.xml\n"
        ),
        media_type="text/plain",
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "  <url>\n"
            "    <loc>https://propioscan.com/</loc>\n"
            "    <lastmod>2026-08-31</lastmod>\n"
            "  </url>\n"
            "</urlset>\n"
        ),
        media_type="application/xml",
    )


@app.get("/admin", include_in_schema=False)
def admin_index() -> FileResponse:
    return FileResponse(
        static_dir / "admin.html",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "ai_configured": bool(settings.openai_api_key),
        "ai_model": settings.openai_model,
        "captcha_required": settings.captcha_required,
        "captcha_configured": settings.captcha_configured,
        "google_analytics_configured": settings.google_analytics_configured,
        "google_analytics_reporting_configured": ga4.configured,
    }


@app.get("/api/config", include_in_schema=False)
def public_config() -> dict[str, str | bool | None]:
    return {
        "captcha_required": settings.captcha_required,
        "captcha_configured": settings.captcha_configured,
        "turnstile_site_key": (
            settings.turnstile_site_key if settings.captcha_configured else None
        ),
        "google_analytics_measurement_id": (
            settings.google_analytics_measurement_id
            if settings.google_analytics_configured
            else None
        ),
    }


@app.get("/api/admin/config", include_in_schema=False)
def admin_public_config() -> dict[str, str | bool | None]:
    return {
        "configured": admin_auth.configured,
        "captcha_required": settings.captcha_required,
        "captcha_configured": settings.captcha_configured,
        "turnstile_site_key": (
            settings.turnstile_site_key if settings.captcha_configured else None
        ),
    }


@app.post("/api/admin/login", include_in_schema=False)
def admin_login(credentials: AdminLoginRequest, request: Request) -> Response:
    remote_ip = request.client.host if request.client else None
    try:
        captcha.verify(
            credentials.captcha_token,
            remote_ip,
            request.url.hostname,
            expected_action="admin_login",
        )
        token = admin_auth.authenticate(
            username=credentials.username,
            password=credentials.password,
            ip_address=remote_ip,
        )
    except AdminRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="Preveč neuspelih poskusov. Poskusite znova čez 15 minut.",
        ) from exc
    except AdminConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="Skrbniški dostop ni konfiguriran."
        ) from exc
    except AdminAuthenticationError as exc:
        raise HTTPException(
            status_code=401, detail="Napačno uporabniško ime ali geslo."
        ) from exc
    except CaptchaRejectedError as exc:
        raise HTTPException(
            status_code=400,
            detail="Varnostno preverjanje ni uspelo.",
        ) from exc
    except CaptchaUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Varnostno preverjanje trenutno ni na voljo.",
        ) from exc

    response = JSONResponse(
        {"authenticated": True, "username": settings.admin_username},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        key=ADMIN_COOKIE,
        value=token,
        max_age=settings.admin_session_hours * 60 * 60,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@app.get("/api/admin/session", include_in_schema=False)
def admin_session(request: Request) -> dict[str, str | bool]:
    session = _require_admin(request)
    return {"authenticated": True, "username": str(session["sub"])}


@app.post("/api/admin/logout", include_in_schema=False)
def admin_logout(request: Request) -> Response:
    _require_admin(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        key=ADMIN_COOKIE,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@app.get("/api/admin/requests", include_in_schema=False)
def admin_requests(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    parcel: str | None = None,
    ip: str | None = None,
    device: str | None = None,
    request_status: str | None = None,
    visitor: str | None = None,
    group_by: str = "visitor",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    _require_admin(request)
    traffic.refresh_job_statuses(settings.data_dir / "jobs")
    return traffic.query(
        RequestFilters(
            date_from=date_from,
            date_to=date_to,
            parcel=parcel,
            ip_address=ip,
            device_type=device,
            status=request_status,
            visitor=visitor,
            group_by=group_by,
        ),
        limit=limit,
        offset=offset,
    )


@app.get("/api/admin/requests.csv", include_in_schema=False)
def admin_requests_csv(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    parcel: str | None = None,
    ip: str | None = None,
    device: str | None = None,
    request_status: str | None = None,
    visitor: str | None = None,
    group_by: str = "visitor",
) -> Response:
    _require_admin(request)
    traffic.refresh_job_statuses(settings.data_dir / "jobs")
    filename, content = traffic.export_csv(
        RequestFilters(
            date_from=date_from,
            date_to=date_to,
            parcel=parcel,
            ip_address=ip,
            device_type=device,
            status=request_status,
            visitor=visitor,
            group_by=group_by,
        )
    )
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/admin/logs", include_in_schema=False)
def admin_log_tail(
    request: Request, source: str = "application", lines: int = 300
) -> dict:
    _require_admin(request)
    return {
        "sources": admin_logs.available_sources(),
        "log": admin_logs.tail(source, lines),
    }


@app.get("/api/admin/analytics", include_in_schema=False)
def admin_analytics(request: Request, days: int = 30, refresh: bool = False) -> dict:
    _require_admin(request)
    if days not in {7, 30, 90}:
        raise HTTPException(
            status_code=422, detail="Obdobje mora biti 7, 30 ali 90 dni."
        )
    if not ga4.configured:
        return {
            "status": "setup_required",
            "measurement_configured": settings.google_analytics_configured,
            "message": (
                "Za prikaz poročil dodajte numerični GA4 Property ID in "
                "storitveni račun z dovoljenjem Viewer."
            ),
        }
    try:
        return {"status": "ready", **ga4.report(days, force_refresh=refresh)}
    except Ga4ConfigurationError as exc:
        logger.warning("GA4 reporting credentials are not usable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Dostop do Google Analytics še ni pravilno nastavljen.",
        ) from exc
    except Ga4QueryError as exc:
        logger.warning("GA4 Data API query failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Google Analytics trenutno ni vrnil poročila.",
        ) from exc


@app.get("/api/admin/analytics.csv", include_in_schema=False)
def admin_analytics_csv(request: Request, days: int = 30) -> Response:
    _require_admin(request)
    if days not in {7, 30, 90}:
        raise HTTPException(
            status_code=422, detail="Obdobje mora biti 7, 30 ali 90 dni."
        )
    if not ga4.configured:
        raise HTTPException(
            status_code=503,
            detail="Dostop do poročil Google Analytics še ni nastavljen.",
        )
    try:
        filename, content = ga4.export_csv(days)
    except (Ga4ConfigurationError, Ga4QueryError) as exc:
        logger.warning("GA4 CSV export failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Poročila Google Analytics trenutno ni mogoče prenesti.",
        ) from exc
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/admin/openai-usage", include_in_schema=False)
def admin_openai_usage(request: Request, days: int = 30) -> dict:
    _require_admin(request)
    if days not in {7, 30}:
        raise HTTPException(status_code=422, detail="Obdobje mora biti 7 ali 30 dni.")
    traffic.refresh_job_statuses(settings.data_dir / "jobs")
    return {"status": "ready", **traffic.openai_usage_report(days)}


@app.get("/api/admin/openai-usage.csv", include_in_schema=False)
def admin_openai_usage_csv(request: Request, days: int = 30) -> Response:
    _require_admin(request)
    if days not in {7, 30}:
        raise HTTPException(status_code=422, detail="Obdobje mora biti 7 ali 30 dni.")
    traffic.refresh_job_statuses(settings.data_dir / "jobs")
    filename, content = traffic.export_openai_usage_csv(days)
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/privacy/events", status_code=status.HTTP_204_NO_CONTENT)
def record_privacy_event(
    event: PrivacyEventRequest,
    request: Request,
    propioscan_visitor_id: str | None = Cookie(default=None),
) -> Response:
    visitor_id = _visitor_id(propioscan_visitor_id)
    privacy_events.record(
        visitor_id=visitor_id,
        event_type=event.event_type,
        parcel_reference=" ".join(event.parcel_reference.split()),
        consent_version=event.consent_version,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key="propioscan_visitor_id",
        value=visitor_id,
        max_age=settings.privacy_retention_days * 24 * 60 * 60,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.delete("/api/privacy/visitor", status_code=status.HTTP_204_NO_CONTENT)
def forget_privacy_visitor(
    request: Request,
    propioscan_visitor_id: str | None = Cookie(default=None),
) -> Response:
    visitor_id = _existing_visitor_id(propioscan_visitor_id)
    if visitor_id:
        privacy_events.forget(visitor_id)
        traffic.forget_visitor(visitor_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        key="propioscan_visitor_id",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def _visitor_id(value: str | None) -> str:
    return _existing_visitor_id(value) or str(uuid.uuid4())


def _existing_visitor_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


@app.post("/api/search", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def start_search(
    search_request: SearchRequest,
    request: Request,
    propioscan_visitor_id: str | None = Cookie(default=None),
    human_check_receipt: str | None = Cookie(
        default=None, alias=HUMAN_CHECK_COOKIE
    ),
) -> Response:
    remote_ip = request.client.host if request.client else None
    parcel_reference = search_request.parcel_number.strip()
    reused_human_check = bool(
        search_request.force_refresh
        and not search_request.captcha_token
        and captcha.accepts_receipt(human_check_receipt, parcel_reference)
    )
    try:
        if not reused_human_check:
            captcha.verify(
                search_request.captcha_token,
                remote_ip,
                request.url.hostname,
            )
    except CaptchaRejectedError as exc:
        if search_request.force_refresh and not search_request.captcha_token:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="Za nov pregled je potrebno ponovno varnostno preverjanje.",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="Varnostno preverjanje ni uspelo. Potrdite, da niste robot, in poskusite znova.",
        ) from exc
    except CaptchaUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Varnostno preverjanje trenutno ni na voljo. Poskusite znova pozneje.",
        ) from exc
    analytics_consent = bool(
        search_request.analytics_consent and search_request.consent_version == "1.2"
    )
    visitor_id = _visitor_id(propioscan_visitor_id) if analytics_consent else None
    job = jobs.submit(
        parcel_reference,
        force_refresh=search_request.force_refresh,
    )
    try:
        traffic.record_request(
            request_id=str(uuid.uuid4()),
            job_id=job.id,
            parcel_reference=parcel_reference,
            visitor_id=visitor_id,
            ip_address=remote_ip,
            user_agent=request.headers.get("user-agent"),
            accept_language=request.headers.get("accept-language"),
            referer=request.headers.get("referer"),
            analytics_consent=analytics_consent,
            consent_version=search_request.consent_version
            if analytics_consent
            else None,
        )
        traffic.update_job_status(job.id, job.status.value, job.updated_at)
    except Exception:
        logger.exception(
            "Could not record the operational request event for job %s", job.id
        )
    response = JSONResponse(
        content=job.model_dump(mode="json"),
        status_code=status.HTTP_202_ACCEPTED,
        headers={"Cache-Control": "no-store"},
    )
    if not reused_human_check:
        receipt = captcha.issue_receipt(parcel_reference)
        if receipt:
            response.set_cookie(
                key=HUMAN_CHECK_COOKIE,
                value=receipt,
                max_age=HUMAN_CHECK_MAX_AGE_SECONDS,
                secure=request.url.scheme == "https",
                httponly=True,
                samesite="strict",
                path="/",
            )
    if visitor_id:
        response.set_cookie(
            key="propioscan_visitor_id",
            value=visitor_id,
            max_age=settings.privacy_retention_days * 24 * 60 * 60,
            secure=request.url.scheme == "https",
            httponly=True,
            samesite="lax",
            path="/",
        )
    return response


@app.get("/api/search/{job_id}", response_model=JobView)
def get_search(job_id: str) -> JobView:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Search job not found.")
    try:
        traffic.update_job_status(job.id, job.status.value, job.updated_at)
    except Exception:
        logger.exception("Could not update the operational status for job %s", job.id)
    return job


def _require_admin(request: Request) -> dict:
    try:
        return admin_auth.verify_session(request.cookies.get(ADMIN_COOKIE))
    except AdminAuthenticationError as exc:
        raise HTTPException(
            status_code=401, detail="Skrbniška prijava je potrebna."
        ) from exc


@app.get("/api/search/{job_id}/report", include_in_schema=False)
def download_location_report(job_id: str) -> Response:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Search job not found.")
    if job.result is None or job.status.value != "completed":
        raise HTTPException(status_code=409, detail="Search result is not ready.")
    try:
        pdf = generate_location_report(
            job.result,
            resolve_report_map_preview(job.result, settings.data_dir),
            prepare_report_regime_map_preview(job.result, settings.data_dir),
        )
    except Exception as exc:
        logger.exception(
            "Could not generate location-information PDF for job %s", job_id
        )
        raise HTTPException(
            status_code=500,
            detail="PDF-ja z lokacijsko informacijo ni bilo mogoče pripraviti.",
        ) from exc
    filename = report_filename(job.result)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/files/{act_id}/{filename}", include_in_schema=False)
def download_file(act_id: int, filename: str) -> FileResponse:
    root = (settings.data_dir / "pdfs" / str(act_id)).resolve()
    candidate = (root / Path(filename).name).resolve()
    if (
        candidate.parent != root
        or not candidate.is_file()
        or candidate.suffix.lower() != ".pdf"
    ):
        raise HTTPException(status_code=404, detail="PDF not found.")
    return FileResponse(
        candidate,
        media_type="application/pdf",
        filename=candidate.name,
        content_disposition_type="inline",
    )


@app.get("/api/map-previews/{act_id}/{filename}", include_in_schema=False)
def map_preview(act_id: int, filename: str) -> FileResponse:
    root = (settings.data_dir / "map_previews" / str(act_id)).resolve()
    candidate = (root / Path(filename).name).resolve()
    if (
        candidate.parent != root
        or not candidate.is_file()
        or candidate.suffix.lower() != ".png"
    ):
        raise HTTPException(status_code=404, detail="Map preview not found.")
    return FileResponse(candidate, media_type="image/png")
