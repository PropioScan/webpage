from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
        *,
        execution_mode: str = "thread",
        base_dir: Path | None = None,
        python_executable: str | None = None,
        retention_days: int = 30,
    ) -> None:
        self.service = service
        self.executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="parcel-search"
        )
        self._jobs: dict[str, JobView] = {}
        self._lock = threading.Lock()
        self._jobs_dir = data_dir / "jobs" if data_dir is not None else None
        self._worker_log = data_dir / "logs" / "job-worker.log" if data_dir else None
        self.execution_mode = execution_mode
        self.base_dir = base_dir or Path.cwd()
        self.python_executable = python_executable
        self.retention = timedelta(days=max(1, retention_days))
        if self._jobs_dir is not None:
            self._jobs_dir.mkdir(parents=True, exist_ok=True)
        if self._worker_log is not None:
            self._worker_log.parent.mkdir(parents=True, exist_ok=True)

    def submit(self, parcel_number: str) -> JobView:
        self.purge_expired()
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
        if self.execution_mode == "process":
            self._start_process_worker(job.id)
        else:
            self.executor.submit(self._run, job.id, parcel_number)
        return job.model_copy(deep=True)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Remove completed or failed disk-backed results after the retention limit."""

        if self._jobs_dir is None:
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - self.retention
        removed = 0
        for path in self._jobs_dir.glob("*.json"):
            try:
                job = JobView.model_validate_json(path.read_text(encoding="utf-8"))
                expired = job.created_at < cutoff
                finished = job.status in {JobStatus.completed, JobStatus.failed}
                if expired and finished:
                    path.unlink()
                    with self._lock:
                        self._jobs.pop(job.id, None)
                    removed += 1
            except (OSError, ValueError):
                continue
        return removed

    def run_persisted(self, job_id: str) -> JobView | None:
        """Run one queued disk-backed job in the current process."""

        job = self._load(job_id)
        if job is None or job.status != JobStatus.queued:
            return job
        with self._lock:
            self._jobs[job_id] = job
        self._run(job_id, job.parcel_number)
        return self.get(job_id)

    def _start_process_worker(self, job_id: str) -> None:
        executable = self.python_executable or sys.executable
        if Path(executable).name == "lswsgi":
            candidate = Path(sys.prefix) / "bin" / "python"
            if candidate.is_file():
                executable = str(candidate)
        command = [executable, "-m", "app.job_worker", job_id]
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            if self._worker_log is None:
                raise OSError("The process job log directory is not configured.")
            with self._worker_log.open("ab", buffering=0) as worker_log:
                subprocess.Popen(
                    command,
                    cwd=self.base_dir,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except OSError:
            logger.exception("Could not start the detached worker for job %s", job_id)
            self._fail(
                job_id,
                "Analize ni bilo mogoče zagnati v ozadju. Poskusite znova pozneje.",
            )

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
