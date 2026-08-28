from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class PrivacyEventStore:
    """Consent-gated, pseudonymous event storage with bounded retention."""

    def __init__(self, data_dir: Path, retention_days: int = 90) -> None:
        self.directory = data_dir / "privacy"
        self.path = self.directory / "events.jsonl"
        self.retention = timedelta(days=max(1, retention_days))
        self._lock = threading.Lock()

    def record(
        self,
        *,
        visitor_id: str,
        event_type: str,
        parcel_reference: str,
        consent_version: str,
        now: datetime | None = None,
    ) -> None:
        recorded_at = now or datetime.now(timezone.utc)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        record = {
            "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
            "visitor_id": visitor_id,
            "event_type": event_type,
            "parcel_reference": parcel_reference,
            "consent_version": consent_version,
        }

        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            records = self._retained_records(recorded_at)
            records.append(record)
            self._write_records(records)

    def purge(self, now: datetime | None = None) -> int:
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)

        with self._lock:
            if not self.path.exists():
                return 0
            original_count = self._line_count()
            retained = self._retained_records(checked_at)
            self._write_records(retained)
            return max(0, original_count - len(retained))

    def forget(self, visitor_id: str) -> int:
        with self._lock:
            if not self.path.exists():
                return 0
            original_count = self._line_count()
            retained = [
                item
                for item in self._retained_records(datetime.now(timezone.utc))
                if item.get("visitor_id") != visitor_id
            ]
            self._write_records(retained)
            return max(0, original_count - len(retained))

    def _retained_records(self, now: datetime) -> list[dict[str, str]]:
        if not self.path.exists():
            return []

        cutoff = now - self.retention
        retained: list[dict[str, str]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line in source:
                try:
                    item = json.loads(line)
                    recorded_at = datetime.fromisoformat(item["recorded_at"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                if recorded_at.tzinfo is None:
                    recorded_at = recorded_at.replace(tzinfo=timezone.utc)
                if recorded_at >= cutoff:
                    retained.append(item)
        return retained

    def _write_records(self, records: list[dict[str, str]]) -> None:
        temporary_path = self.path.with_suffix(".jsonl.tmp")
        with temporary_path.open("w", encoding="utf-8") as output:
            for item in records:
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
        os.replace(temporary_path, self.path)

    def _line_count(self) -> int:
        with self.path.open("r", encoding="utf-8") as source:
            return sum(1 for _ in source)
