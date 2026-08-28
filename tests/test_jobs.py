from pathlib import Path

from app.jobs import JobManager


class IdleService:
    def search(self, parcel_number, progress):  # pragma: no cover - never scheduled
        raise AssertionError("search should not run in this test")


def test_job_state_can_be_read_by_another_passenger_process(tmp_path: Path) -> None:
    first = JobManager(IdleService(), workers=1, data_dir=tmp_path)
    second = JobManager(IdleService(), workers=1, data_dir=tmp_path)
    try:
        job_id = "a" * 32
        from app.models import JobStatus, JobView
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = JobView(
            id=job_id,
            status=JobStatus.running,
            progress=40,
            message="Working",
            parcel_number="2057 314/4",
            created_at=now,
            updated_at=now,
        )
        with first._lock:
            first._jobs[job_id] = job
            first._persist_locked(job)

        loaded = second.get(job_id)

        assert loaded is not None
        assert loaded.progress == 40
        assert loaded.parcel_number == "2057 314/4"
    finally:
        first.executor.shutdown(wait=False, cancel_futures=True)
        second.executor.shutdown(wait=False, cancel_futures=True)


def test_job_state_rejects_path_traversal_ids(tmp_path: Path) -> None:
    manager = JobManager(IdleService(), workers=1, data_dir=tmp_path)
    try:
        assert manager.get("../secrets") is None
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)
