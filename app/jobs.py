from __future__ import annotations

import threading
import uuid
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .errors import CheckerError
from .models import JobStatus, JobView, SearchResult
from .service import ParcelSearchService


logger = logging.getLogger(__name__)


class JobManager:
    def __init__(
        self,
        service: ParcelSearchService,
        workers: int,
        data_dir: Path | None = None,
    ) -> None:
        self.service = service
        self.executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="parcel-search"
        )
        self._jobs: dict[str, JobView] = {}
        self._lock = threading.Lock()
        self._jobs_dir = data_dir / "jobs" if data_dir is not None else None
        if self._jobs_dir is not None:
            self._jobs_dir.mkdir(parents=True, exist_ok=True)

    def submit(self, parcel_number: str) -> JobView:
        now = datetime.now(timezone.utc)
        job = JobView(
            id=uuid.uuid4().hex,
            status=JobStatus.queued,
            progress=0,
            message="Search queued…",
            parcel_number=parcel_number,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._persist_locked(job)
        self.executor.submit(self._run, job.id, parcel_number)
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> JobView | None:
        disk_job = self._load(job_id)
        with self._lock:
            memory_job = self._jobs.get(job_id)
            if disk_job is not None and (
                memory_job is None or disk_job.updated_at >= memory_job.updated_at
            ):
                self._jobs[job_id] = disk_job
                job = disk_job
            else:
                job = memory_job
            return job.model_copy(deep=True) if job else None

    def _update(self, job_id: str, progress: int, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.running
            job.progress = max(job.progress, min(99, progress))
            job.message = message
            job.updated_at = datetime.now(timezone.utc)
            self._persist_locked(job)

    def _run(self, job_id: str, parcel_number: str) -> None:
        self._update(job_id, 1, "Starting search…")
        try:
            result: SearchResult = self.service.search(
                parcel_number,
                lambda progress, message: self._update(job_id, progress, message),
            )
            with self._lock:
                job = self._jobs[job_id]
                job.status = JobStatus.completed
                job.progress = 100
                job.message = "Search completed."
                job.result = result
                job.updated_at = datetime.now(timezone.utc)
                self._persist_locked(job)
        except CheckerError as exc:
            self._fail(job_id, str(exc))
        except Exception:
            logger.exception("Unexpected error in parcel search job %s", job_id)
            self._fail(
                job_id,
                "The search stopped because of an unexpected internal error. Check the server log.",
            )

    def _fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.failed
            job.message = "Search failed."
            job.error = error
            job.updated_at = datetime.now(timezone.utc)
            self._persist_locked(job)

    def _job_path(self, job_id: str) -> Path | None:
        if self._jobs_dir is None or len(job_id) != 32:
            return None
        try:
            int(job_id, 16)
        except ValueError:
            return None
        return self._jobs_dir / f"{job_id}.json"

    def _load(self, job_id: str) -> JobView | None:
        path = self._job_path(job_id)
        if path is None:
            return None
        try:
            return JobView.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _persist_locked(self, job: JobView) -> None:
        path = self._job_path(job.id)
        if path is None:
            return
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(job.model_dump_json(), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
