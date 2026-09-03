from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import ParcelInformation, SearchResult
from app.result_cache import RESULT_SCHEMA_VERSION, ParcelResultCache


def _result(parcel_number: str = "314/4") -> SearchResult:
    return SearchResult(
        parcel=ParcelInformation(
            cadastral_municipality_id=2057,
            parcel_number=parcel_number,
            municipality="Ljubljana",
        )
    )


def test_completed_result_is_reused_for_seven_days(tmp_path: Path) -> None:
    cache = ParcelResultCache(tmp_path, retention_days=7)
    stored_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    cache.put("2057   314/4", _result(), stored_at=stored_at)
    cached = cache.get(" 2057 314/4 ", now=stored_at + timedelta(days=6, hours=23))

    assert cached is not None
    assert cached.result.parcel.parcel_number == "314/4"
    assert cached.stored_at == stored_at
    assert cached.expires_at == stored_at + timedelta(days=7)
    assert cache.path.is_file()


def test_expired_result_is_removed(tmp_path: Path) -> None:
    cache = ParcelResultCache(tmp_path, retention_days=7)
    stored_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    cache.put("2057 314/4", _result(), stored_at=stored_at)

    assert cache.get("2057 314/4", now=stored_at + timedelta(days=7)) is None
    assert cache.purge(now=stored_at + timedelta(days=8)) == 0


def test_new_successful_result_replaces_the_previous_copy(tmp_path: Path) -> None:
    cache = ParcelResultCache(tmp_path, retention_days=7)
    first_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    second_at = first_at + timedelta(days=1)
    cache.put("2057 314/4", _result(), stored_at=first_at)

    replacement = _result()
    replacement.parcel.area_m2 = 999
    cache.put("2057 314/4", replacement, stored_at=second_at)
    cached = cache.get("2057 314/4", now=second_at)

    assert cached is not None
    assert cached.result.parcel.area_m2 == 999
    assert cached.stored_at == second_at


def test_cache_schema_requires_the_public_ownership_version() -> None:
    assert RESULT_SCHEMA_VERSION >= 8
