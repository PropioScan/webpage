from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .captcha import (
    CaptchaRejectedError,
    CaptchaUnavailableError,
    TurnstileVerifier,
)
from .config import settings
from .jobs import JobManager
from .models import JobView, PrivacyEventRequest, SearchRequest
from .privacy import PrivacyEventStore
from .report import generate_location_report, report_filename, resolve_report_map_preview
from .service import ParcelSearchService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings.ensure_directories()
service = ParcelSearchService(settings)
jobs = JobManager(service, settings.search_workers, settings.data_dir)
privacy_events = PrivacyEventStore(
    settings.data_dir,
    retention_days=settings.privacy_retention_days,
)
captcha = TurnstileVerifier(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    privacy_events.purge()
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


app = FastAPI(
    title="Propioscan",
    version="1.6.0",
    description="Parcel-focused GURS, PIS, GJI, GeoHub, and eVRD research for Slovenia.",
    lifespan=lifespan,
)
static_dir = settings.base_dir / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "ai_configured": bool(settings.openai_api_key),
        "ai_model": settings.openai_model,
        "captcha_required": settings.captcha_required,
        "captcha_configured": settings.captcha_configured,
    }


@app.get("/api/config", include_in_schema=False)
def public_config() -> dict[str, str | bool | None]:
    return {
        "captcha_required": settings.captcha_required,
        "captcha_configured": settings.captcha_configured,
        "turnstile_site_key": (
            settings.turnstile_site_key if settings.captcha_configured else None
        ),
    }


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
def start_search(search_request: SearchRequest, request: Request) -> JobView:
    remote_ip = request.client.host if request.client else None
    try:
        captcha.verify(
            search_request.captcha_token,
            remote_ip,
            request.url.hostname,
        )
    except CaptchaRejectedError as exc:
        raise HTTPException(
            status_code=400,
            detail="Varnostno preverjanje ni uspelo. Potrdite, da niste robot, in poskusite znova.",
        ) from exc
    except CaptchaUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Varnostno preverjanje trenutno ni na voljo. Poskusite znova pozneje.",
        ) from exc
    return jobs.submit(search_request.parcel_number.strip())


@app.get("/api/search/{job_id}", response_model=JobView)
def get_search(job_id: str) -> JobView:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Search job not found.")
    return job


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
        )
    except Exception as exc:
        logger.exception("Could not generate location-information PDF for job %s", job_id)
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
