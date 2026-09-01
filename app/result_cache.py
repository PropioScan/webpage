from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import SearchResult


@dataclass(frozen=True)
class CachedParcelResult:
    result: SearchResult
    stored_at: datetime
    expires_at: datetime


class ParcelResultCache:
    """Shared SQLite cache for completed parcel analyses."""

    def __init__(self, data_dir: Path, *, retention_days: int = 7) -> None:
        self.directory = data_dir / "result_cache"
        self.path = self.directory / "parcel_results.sqlite3"
        self.retention = timedelta(days=max(1, retention_days))
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS parcel_results (
                    cache_key TEXT PRIMARY KEY,
                    parcel_reference TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parcel_results_expires_at
                    ON parcel_results(expires_at);
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def get(
        self, parcel_reference: str, *, now: datetime | None = None
    ) -> CachedParcelResult | None:
        checked_at = _utc(now)
        cache_key = _cache_key(parcel_reference)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM parcel_results WHERE expires_at <= ?",
                (checked_at.isoformat(),),
            )
            row = connection.execute(
                "SELECT stored_at, expires_at, result_json FROM parcel_results WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            try:
                stored_at = _parse_datetime(row["stored_at"])
                expires_at = _parse_datetime(row["expires_at"])
                result = SearchResult.model_validate_json(row["result_json"])
            except (TypeError, ValueError):
                connection.execute(
                    "DELETE FROM parcel_results WHERE cache_key = ?", (cache_key,)
                )
                return None
        return CachedParcelResult(
            result=result,
            stored_at=stored_at,
            expires_at=expires_at,
        )

    def put(
        self,
        parcel_reference: str,
        result: SearchResult,
        *,
        stored_at: datetime | None = None,
    ) -> CachedParcelResult:
        recorded_at = _utc(stored_at)
        expires_at = recorded_at + self.retention
        normalized_reference = _normalize_reference(parcel_reference)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO parcel_results (
                    cache_key, parcel_reference, stored_at, expires_at, result_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    parcel_reference = excluded.parcel_reference,
                    stored_at = excluded.stored_at,
                    expires_at = excluded.expires_at,
                    result_json = excluded.result_json
                """,
                (
                    _cache_key(normalized_reference),
                    normalized_reference,
                    recorded_at.isoformat(),
                    expires_at.isoformat(),
                    result.model_dump_json(),
                ),
            )
        return CachedParcelResult(
            result=result.model_copy(deep=True),
            stored_at=recorded_at,
            expires_at=expires_at,
        )

    def purge(self, *, now: datetime | None = None) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM parcel_results WHERE expires_at <= ?",
                (_utc(now).isoformat(),),
            )
            return max(0, cursor.rowcount)


def _normalize_reference(parcel_reference: str) -> str:
    return " ".join(parcel_reference.split()).casefold()


def _cache_key(parcel_reference: str) -> str:
    return hashlib.sha256(
        _normalize_reference(parcel_reference).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))
