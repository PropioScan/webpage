from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import JobStatus
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


def test_process_mode_launches_a_detached_disk_job(
    tmp_path: Path, monkeypatch
) -> None:
    launched: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return object()

    monkeypatch.setattr("app.jobs.subprocess.Popen", fake_popen)
    manager = JobManager(
        IdleService(),
        workers=1,
        data_dir=tmp_path,
        execution_mode="process",
        base_dir=tmp_path,
        python_executable="/runtime/python",
    )
    try:
        job = manager.submit("2057 314/4")

        assert job.status == JobStatus.queued
        assert len(launched) == 1
        command, options = launched[0]
        assert command == ["/runtime/python", "-m", "app.job_worker", job.id]
        assert options["cwd"] == tmp_path
        assert options["start_new_session"] is True
        assert options["close_fds"] is True
        assert (tmp_path / "jobs" / f"{job.id}.json").is_file()
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_process_launch_failure_is_persisted(tmp_path: Path, monkeypatch) -> None:
    def fail_to_launch(*args, **kwargs):
        raise OSError("process unavailable")

    monkeypatch.setattr("app.jobs.subprocess.Popen", fail_to_launch)
    manager = JobManager(
        IdleService(),
        workers=1,
        data_dir=tmp_path,
        execution_mode="process",
        python_executable="/runtime/python",
    )
    try:
        submitted = manager.submit("2057 314/4")
        job = manager.get(submitted.id)

        assert job is not None
        assert job.status == JobStatus.failed
        assert "ozadju" in (job.error or "")
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_finished_job_results_are_purged_after_retention(tmp_path: Path) -> None:
    manager = JobManager(IdleService(), workers=1, data_dir=tmp_path, retention_days=30)
    try:
        from app.models import JobView

        now = datetime(2026, 8, 31, tzinfo=timezone.utc)
        old_job = JobView(
            id="b" * 32,
            status=JobStatus.completed,
            progress=100,
            message="Done",
            parcel_number="2057 314/4",
            created_at=now - timedelta(days=31),
            updated_at=now - timedelta(days=31),
        )
        active_job = old_job.model_copy(
            update={"id": "c" * 32, "status": JobStatus.running}
        )
        with manager._lock:
            for job in (old_job, active_job):
                manager._jobs[job.id] = job
                manager._persist_locked(job)

        assert manager.purge_expired(now) == 1
        assert not (tmp_path / "jobs" / f"{old_job.id}.json").exists()
        assert (tmp_path / "jobs" / f"{active_job.id}.json").exists()
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)
