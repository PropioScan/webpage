"""Detached worker for one disk-backed parcel analysis job."""

from __future__ import annotations

import argparse
import logging

from .config import settings
from .jobs import JobManager
from .models import JobStatus
from .service import ParcelSearchService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.ensure_directories()
    manager = JobManager(
        ParcelSearchService(settings),
        workers=1,
        data_dir=settings.data_dir,
        execution_mode="thread",
        base_dir=settings.base_dir,
        retention_days=settings.job_retention_days,
        result_cache_days=settings.result_cache_days,
    )
    try:
        job = manager.run_persisted(args.job_id)
        return 0 if job is not None and job.status == JobStatus.completed else 1
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=False)


if __name__ == "__main__":
    raise SystemExit(main())
