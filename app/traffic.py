from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


GROUP_BY_VALUES = {
    "none",
    "visitor",
    "technical",
    "ip",
    "parcel",
    "device",
    "day",
    "status",
}
STATUS_VALUES = {"queued", "running", "completed", "failed"}
STATUS_LABELS = {
    "queued": "V čakalni vrsti",
    "running": "V teku",
    "completed": "Zaključeno",
    "failed": "Napaka",
}
DEVICE_LABELS = {
    "desktop": "Namizni računalnik",
    "mobile": "Telefon",
    "tablet": "Tablica",
    "bot": "Robot",
    "unknown": "Neznana naprava",
}
GROUP_LABELS = {
    "visitor": "ID obiskovalca",
    "technical": "Tehnična skupina",
    "ip": "IP naslov",
    "parcel": "Parcela",
    "device": "Naprava",
    "day": "Dan",
    "status": "Stanje",
}
OPENAI_USAGE_COLUMNS = {
    "openai_usage_synced": "INTEGER NOT NULL DEFAULT 0",
    "openai_configured": "INTEGER NOT NULL DEFAULT 0",
    "openai_model": "TEXT",
    "openai_calls": "INTEGER NOT NULL DEFAULT 0",
    "openai_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "openai_output_tokens": "INTEGER NOT NULL DEFAULT 0",
    "openai_total_tokens": "INTEGER NOT NULL DEFAULT 0",
    "openai_rate_limit_tokens": "INTEGER",
    "openai_rate_limit_remaining_tokens": "INTEGER",
    "openai_rate_limit_reset": "TEXT",
    "openai_failures": "INTEGER NOT NULL DEFAULT 0",
}


@dataclass(frozen=True)
class RequestFilters:
    date_from: str | None = None
    date_to: str | None = None
    parcel: str | None = None
    ip_address: str | None = None
    device_type: str | None = None
    status: str | None = None
    visitor: str | None = None
    group_by: str = "visitor"

    def normalized_group_by(self) -> str:
        return self.group_by if self.group_by in GROUP_BY_VALUES else "visitor"


class TrafficStore:
    """Bounded operational request log and admin-login audit store."""

    def __init__(
        self,
        data_dir: Path,
        *,
        retention_days: int = 30,
        group_secret: str = "",
    ) -> None:
        self.directory = data_dir / "traffic"
        self.path = self.directory / "requests.sqlite3"
        self.retention = timedelta(days=max(1, retention_days))
        self.group_secret = group_secret.encode("utf-8")
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
                CREATE TABLE IF NOT EXISTS parcel_requests (
                    request_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    requested_at TEXT NOT NULL,
                    parcel_reference TEXT NOT NULL,
                    visitor_id TEXT,
                    ip_address TEXT NOT NULL,
                    user_agent TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    browser_family TEXT NOT NULL,
                    os_family TEXT NOT NULL,
                    accept_language TEXT NOT NULL,
                    referer_host TEXT NOT NULL,
                    analytics_consent INTEGER NOT NULL DEFAULT 0,
                    consent_version TEXT,
                    technical_group TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    status_updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parcel_requests_requested_at
                    ON parcel_requests(requested_at DESC);
                CREATE INDEX IF NOT EXISTS idx_parcel_requests_visitor
                    ON parcel_requests(visitor_id);
                CREATE INDEX IF NOT EXISTS idx_parcel_requests_ip
                    ON parcel_requests(ip_address);
                CREATE INDEX IF NOT EXISTS idx_parcel_requests_parcel
                    ON parcel_requests(parcel_reference);
                CREATE TABLE IF NOT EXISTS admin_login_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempted_at TEXT NOT NULL,
                    ip_address TEXT NOT NULL,
                    username TEXT NOT NULL,
                    successful INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_attempts_lookup
                    ON admin_login_attempts(ip_address, username, attempted_at DESC);
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(parcel_requests)")
            }
            for name, declaration in OPENAI_USAGE_COLUMNS.items():
                if name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE parcel_requests ADD COLUMN {name} {declaration}"
                    )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def record_request(
        self,
        *,
        request_id: str,
        job_id: str,
        parcel_reference: str,
        visitor_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
        accept_language: str | None,
        referer: str | None,
        analytics_consent: bool,
        consent_version: str | None,
        now: datetime | None = None,
    ) -> None:
        recorded_at = _utc(now)
        ip = normalize_ip(ip_address)
        agent = (user_agent or "")[:1000]
        device_type, browser_family, os_family = classify_user_agent(agent)
        technical_group = self._technical_group(ip, agent)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO parcel_requests (
                    request_id, job_id, requested_at, parcel_reference,
                    visitor_id, ip_address, user_agent, device_type,
                    browser_family, os_family, accept_language, referer_host,
                    analytics_consent, consent_version, technical_group,
                    status, status_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    request_id,
                    job_id,
                    recorded_at.isoformat(),
                    " ".join(parcel_reference.split())[:80],
                    visitor_id,
                    ip,
                    agent,
                    device_type,
                    browser_family,
                    os_family,
                    (accept_language or "")[:250],
                    referer_host(referer),
                    int(analytics_consent),
                    consent_version,
                    technical_group,
                    recorded_at.isoformat(),
                ),
            )
        self.purge(recorded_at)

    def update_job_status(
        self,
        job_id: str,
        status: str,
        updated_at: datetime | str | None = None,
    ) -> None:
        if status not in STATUS_VALUES:
            return
        timestamp = (
            updated_at if isinstance(updated_at, str) else _utc(updated_at).isoformat()
        )
        with self._connect() as connection:
            connection.execute(
                "UPDATE parcel_requests SET status = ?, status_updated_at = ? WHERE job_id = ?",
                (status, timestamp, job_id),
            )

    def refresh_job_statuses(self, jobs_dir: Path) -> None:
        with self._connect() as connection:
            job_rows = connection.execute(
                """
                SELECT job_id, status, status_updated_at, openai_usage_synced
                FROM parcel_requests
                WHERE status IN ('queued', 'running') OR openai_usage_synced = 0
                """
            ).fetchall()
            updates: list[tuple[Any, ...]] = []
            for row in job_rows:
                path = jobs_dir / f"{row['job_id']}.json"
                if not path.is_file():
                    continue
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                    status = item["status"]
                    updated_at = item["updated_at"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if status not in STATUS_VALUES:
                    continue
                result = item.get("result") if isinstance(item, dict) else None
                from_cache = bool(item.get("from_cache"))
                usage = (
                    result.get("openai_usage", {})
                    if isinstance(result, dict) and not from_cache
                    else {}
                )
                terminal = status in {"completed", "failed"}
                should_update = updated_at > row["status_updated_at"] or (
                    terminal and not row["openai_usage_synced"]
                )
                if not should_update:
                    continue
                updates.append(
                    (
                        status,
                        updated_at,
                        int(terminal),
                        int(bool(usage.get("configured"))),
                        str(usage.get("model") or "")[:100] or None,
                        _nonnegative_int(usage.get("calls")),
                        _nonnegative_int(usage.get("input_tokens")),
                        _nonnegative_int(usage.get("output_tokens")),
                        _nonnegative_int(usage.get("total_tokens")),
                        _optional_nonnegative_int(usage.get("rate_limit_tokens")),
                        _optional_nonnegative_int(
                            usage.get("rate_limit_remaining_tokens")
                        ),
                        str(usage.get("rate_limit_reset") or "")[:100] or None,
                        0 if from_cache else _openai_failure_count(result),
                        row["job_id"],
                    )
                )
            if updates:
                connection.executemany(
                    """
                    UPDATE parcel_requests SET
                        status = ?, status_updated_at = ?, openai_usage_synced = ?,
                        openai_configured = ?, openai_model = ?, openai_calls = ?,
                        openai_input_tokens = ?, openai_output_tokens = ?,
                        openai_total_tokens = ?, openai_rate_limit_tokens = ?,
                        openai_rate_limit_remaining_tokens = ?,
                        openai_rate_limit_reset = ?, openai_failures = ?
                    WHERE job_id = ?
                    """,
                    updates,
                )

    def openai_usage_report(
        self, days: int = 30, *, now: datetime | None = None
    ) -> dict[str, Any]:
        period = max(1, min(int(days), self.retention.days))
        generated_at = _utc(now)
        cutoff = (generated_at - timedelta(days=period - 1)).date().isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT requested_at, parcel_reference, job_id, status_updated_at,
                       openai_configured, openai_model, openai_calls,
                       openai_input_tokens, openai_output_tokens,
                       openai_total_tokens, openai_rate_limit_tokens,
                       openai_rate_limit_remaining_tokens,
                       openai_rate_limit_reset, openai_failures
                FROM parcel_requests
                WHERE status = 'completed' AND openai_usage_synced = 1
                  AND substr(requested_at, 1, 10) >= ?
                ORDER BY requested_at DESC
                """,
                (cutoff,),
            ).fetchall()

        daily: dict[str, dict[str, Any]] = {}
        models: dict[str, dict[str, Any]] = {}
        latest_limit: dict[str, Any] | None = None
        for row in rows:
            day = row["requested_at"][:10]
            day_item = daily.setdefault(
                day,
                {
                    "date": day,
                    "analyses": 0,
                    "ai_analyses": 0,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "failures": 0,
                },
            )
            day_item["analyses"] += 1
            day_item["ai_analyses"] += int(row["openai_calls"] > 0)
            for key, column in (
                ("calls", "openai_calls"),
                ("input_tokens", "openai_input_tokens"),
                ("output_tokens", "openai_output_tokens"),
                ("total_tokens", "openai_total_tokens"),
                ("failures", "openai_failures"),
            ):
                day_item[key] += int(row[column])

            model = row["openai_model"] or "Ni modela"
            model_item = models.setdefault(
                model,
                {
                    "model": model,
                    "analyses": 0,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            )
            model_item["analyses"] += int(row["openai_calls"] > 0)
            for key, column in (
                ("calls", "openai_calls"),
                ("input_tokens", "openai_input_tokens"),
                ("output_tokens", "openai_output_tokens"),
                ("total_tokens", "openai_total_tokens"),
            ):
                model_item[key] += int(row[column])

            if latest_limit is None and row["openai_rate_limit_tokens"] is not None:
                latest_limit = {
                    "limit_tokens": row["openai_rate_limit_tokens"],
                    "remaining_tokens": row["openai_rate_limit_remaining_tokens"],
                    "reset": row["openai_rate_limit_reset"],
                    "observed_at": row["status_updated_at"],
                }

        summary = {
            "completed_analyses": len(rows),
            "configured_analyses": sum(row["openai_configured"] for row in rows),
            "ai_analyses": sum(row["openai_calls"] > 0 for row in rows),
            "calls": sum(row["openai_calls"] for row in rows),
            "input_tokens": sum(row["openai_input_tokens"] for row in rows),
            "output_tokens": sum(row["openai_output_tokens"] for row in rows),
            "total_tokens": sum(row["openai_total_tokens"] for row in rows),
            "failures": sum(row["openai_failures"] for row in rows),
        }
        return {
            "period_days": period,
            "retention_days": self.retention.days,
            "generated_at": generated_at.isoformat(),
            "summary": summary,
            "daily": sorted(daily.values(), key=lambda item: item["date"]),
            "models": sorted(
                models.values(), key=lambda item: item["total_tokens"], reverse=True
            ),
            "latest_rate_limit": latest_limit,
            "recent": [
                {
                    "requested_at": row["requested_at"],
                    "parcel_reference": row["parcel_reference"],
                    "job_id": row["job_id"],
                    "model": row["openai_model"],
                    "calls": row["openai_calls"],
                    "input_tokens": row["openai_input_tokens"],
                    "output_tokens": row["openai_output_tokens"],
                    "total_tokens": row["openai_total_tokens"],
                    "failures": row["openai_failures"],
                }
                for row in rows[:100]
            ],
        }

    def export_openai_usage_csv(
        self, days: int = 30, *, now: datetime | None = None
    ) -> tuple[str, str]:
        report = self.openai_usage_report(days, now=now)
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        writer.writerow(
            ("Propioscan · OpenAI poraba", f"Zadnjih {report['period_days']} dni")
        )
        writer.writerow(())
        writer.writerow(("Povzetek", "Vrednost"))
        for key, label in (
            ("completed_analyses", "Zaključene analize"),
            ("ai_analyses", "Analize z OpenAI"),
            ("calls", "API klici"),
            ("input_tokens", "Vhodni tokeni"),
            ("output_tokens", "Izhodni tokeni"),
            ("total_tokens", "Skupaj tokeni"),
            ("failures", "Neuspešni AI povzetki"),
        ):
            writer.writerow((label, report["summary"][key]))
        writer.writerow(())
        fields = (
            ("requested_at", "Čas zahteve"),
            ("parcel_reference", "Parcela"),
            ("job_id", "ID opravila"),
            ("model", "Model"),
            ("calls", "API klici"),
            ("input_tokens", "Vhodni tokeni"),
            ("output_tokens", "Izhodni tokeni"),
            ("total_tokens", "Skupaj tokeni"),
            ("failures", "Napake"),
        )
        writer.writerow(tuple(label for _, label in fields))
        for row in report["recent"]:
            writer.writerow(tuple(row.get(field, "") for field, _ in fields))
        return f"propioscan-openai-{report['period_days']}-dni.csv", output.getvalue()

    def query(
        self,
        filters: RequestFilters,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        rows = self._filtered_rows(filters)
        group_by = filters.normalized_group_by()
        groups = self._groups(rows, group_by)
        page = rows[max(0, offset) : max(0, offset) + max(1, min(limit, 500))]
        return {
            "summary": self._summary(rows),
            "groups": groups,
            "requests": [dict(row) for row in page],
            "total": len(rows),
            "limit": max(1, min(limit, 500)),
            "offset": max(0, offset),
            "group_by": group_by,
        }

    def export_csv(self, filters: RequestFilters) -> tuple[str, str]:
        rows = self._filtered_rows(filters)
        group_by = filters.normalized_group_by()
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        if group_by != "none":
            groups = self._groups(rows, group_by)
            writer.writerow(
                ("Združeno po", "Skupina", "Število zahtev", "Različni IP-ji", "Zadnja zahteva")
            )
            for group in groups:
                writer.writerow(
                    (
                        GROUP_LABELS.get(group_by, group_by),
                        group["label"],
                        group["request_count"],
                        group["unique_ips"],
                        group["last_request"],
                    )
                )
            filename = f"propioscan-statistika-{group_by}.csv"
        else:
            writer.writerow(
                (
                    "ID zahteve",
                    "ID opravila",
                    "Čas zahteve",
                    "Parcela",
                    "Stanje",
                    "Posodobitev stanja",
                    "IP naslov",
                    "ID obiskovalca",
                    "Tehnična skupina",
                    "Vrsta naprave",
                    "Brskalnik",
                    "Operacijski sistem",
                    "Uporabniški agent",
                    "Jezik brskalnika",
                    "Gostitelj napotitve",
                    "Analitično soglasje",
                    "Različica soglasja",
                )
            )
            for row in rows:
                writer.writerow(
                    (
                        row["request_id"],
                        row["job_id"],
                        row["requested_at"],
                        row["parcel_reference"],
                        STATUS_LABELS.get(row["status"], row["status"]),
                        row["status_updated_at"],
                        row["ip_address"],
                        row["visitor_id"] or "",
                        row["technical_group"],
                        DEVICE_LABELS.get(row["device_type"], row["device_type"]),
                        _technical_label(row["browser_family"]),
                        _technical_label(row["os_family"]),
                        row["user_agent"],
                        row["accept_language"],
                        row["referer_host"],
                        "DA" if row["analytics_consent"] else "NE",
                        row["consent_version"] or "",
                    )
                )
            filename = "propioscan-zahteve.csv"
        return filename, output.getvalue()

    def forget_visitor(self, visitor_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE parcel_requests SET visitor_id = NULL, analytics_consent = 0, consent_version = NULL WHERE visitor_id = ?",
                (visitor_id,),
            )
            return max(0, cursor.rowcount)

    def purge(self, now: datetime | None = None) -> int:
        cutoff = (_utc(now) - self.retention).isoformat()
        attempts_cutoff = (_utc(now) - timedelta(days=1)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM parcel_requests WHERE requested_at < ?", (cutoff,)
            )
            connection.execute(
                "DELETE FROM admin_login_attempts WHERE attempted_at < ?",
                (attempts_cutoff,),
            )
            return max(0, cursor.rowcount)

    def record_login_attempt(
        self,
        *,
        ip_address: str | None,
        username: str,
        successful: bool,
        now: datetime | None = None,
    ) -> None:
        recorded_at = _utc(now)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM admin_login_attempts WHERE attempted_at < ?",
                ((recorded_at - timedelta(days=1)).isoformat(),),
            )
            connection.execute(
                "INSERT INTO admin_login_attempts (attempted_at, ip_address, username, successful) VALUES (?, ?, ?, ?)",
                (
                    recorded_at.isoformat(),
                    normalize_ip(ip_address),
                    username[:128].casefold(),
                    int(successful),
                ),
            )

    def failed_login_count(
        self,
        *,
        ip_address: str | None,
        username: str,
        since: datetime,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM admin_login_attempts
                WHERE successful = 0 AND attempted_at >= ?
                  AND (ip_address = ? OR username = ?)
                """,
                (
                    _utc(since).isoformat(),
                    normalize_ip(ip_address),
                    username[:128].casefold(),
                ),
            ).fetchone()
            return int(row["count"])

    def _technical_group(self, ip_address: str, user_agent: str) -> str:
        digest = hashlib.sha256(
            self.group_secret
            + b"\0"
            + ip_address.encode()
            + b"\0"
            + user_agent.encode()
        ).hexdigest()
        return f"T-{digest[:12]}"

    def _filtered_rows(self, filters: RequestFilters) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[str] = []
        if filters.date_from:
            clauses.append("requested_at >= ?")
            parameters.append(_filter_date(filters.date_from, end=False))
        if filters.date_to:
            clauses.append("requested_at <= ?")
            parameters.append(_filter_date(filters.date_to, end=True))
        if filters.parcel:
            clauses.append("parcel_reference LIKE ?")
            parameters.append(f"%{filters.parcel.strip()[:80]}%")
        if filters.ip_address:
            clauses.append("ip_address LIKE ?")
            parameters.append(f"%{filters.ip_address.strip()[:64]}%")
        if filters.device_type:
            clauses.append("device_type = ?")
            parameters.append(filters.device_type[:30])
        if filters.status in STATUS_VALUES:
            clauses.append("status = ?")
            parameters.append(filters.status)
        if filters.visitor:
            clauses.append(
                "(visitor_id LIKE ? OR technical_group LIKE ? OR request_id LIKE ?)"
            )
            value = f"%{filters.visitor.strip()[:128]}%"
            parameters.extend([value, value, value])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            return connection.execute(
                f"SELECT * FROM parcel_requests{where} ORDER BY requested_at DESC",
                parameters,
            ).fetchall()

    @staticmethod
    def _summary(rows: list[sqlite3.Row]) -> dict[str, int]:
        return {
            "requests": len(rows),
            "unique_ips": len({row["ip_address"] for row in rows}),
            "consented_visitors": len(
                {row["visitor_id"] for row in rows if row["visitor_id"]}
            ),
            "completed": sum(row["status"] == "completed" for row in rows),
            "failed": sum(row["status"] == "failed" for row in rows),
            "active": sum(row["status"] in {"queued", "running"} for row in rows),
        }

    @staticmethod
    def _groups(rows: list[sqlite3.Row], group_by: str) -> list[dict[str, Any]]:
        if group_by == "none":
            return []
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            if group_by == "visitor":
                key = row["visitor_id"] or f"request:{row['request_id']}"
                label = (
                    f"Obiskovalec {row['visitor_id'][:8]}"
                    if row["visitor_id"]
                    else "Posamezna zahteva brez analitičnega ID"
                )
            elif group_by == "technical":
                key = label = row["technical_group"]
            elif group_by == "ip":
                key = label = row["ip_address"]
            elif group_by == "parcel":
                key = label = row["parcel_reference"]
            elif group_by == "device":
                key = label = " · ".join(
                    (
                        DEVICE_LABELS.get(row["device_type"], row["device_type"]),
                        _technical_label(row["browser_family"]),
                        _technical_label(row["os_family"]),
                    )
                )
            elif group_by == "day":
                key = label = row["requested_at"][:10]
            else:
                key = row["status"]
                label = STATUS_LABELS.get(key, key)
            item = grouped.setdefault(
                key,
                {
                    "key": key,
                    "label": label,
                    "request_count": 0,
                    "unique_ips_set": set(),
                    "last_request": row["requested_at"],
                },
            )
            item["request_count"] += 1
            item["unique_ips_set"].add(row["ip_address"])
            item["last_request"] = max(item["last_request"], row["requested_at"])
        result = []
        for item in grouped.values():
            item["unique_ips"] = len(item.pop("unique_ips_set"))
            result.append(item)
        return sorted(
            result,
            key=lambda item: (item["request_count"], item["last_request"]),
            reverse=True,
        )[:500]


def classify_user_agent(user_agent: str) -> tuple[str, str, str]:
    agent = user_agent or ""
    if re.search(r"bot|crawler|spider|slurp|headless", agent, re.I):
        device = "bot"
    elif re.search(r"ipad|tablet", agent, re.I):
        device = "tablet"
    elif re.search(r"mobile|iphone|ipod|android", agent, re.I):
        device = "mobile"
    else:
        device = "desktop"

    if re.search(r"edg/|edgios|edga", agent, re.I):
        browser = "Edge"
    elif re.search(r"firefox/|fxios", agent, re.I):
        browser = "Firefox"
    elif re.search(r"chrome/|crios", agent, re.I):
        browser = "Chrome"
    elif "Safari/" in agent and "Version/" in agent:
        browser = "Safari"
    else:
        browser = "Other"

    if re.search(r"iphone|ipad|ipod", agent, re.I):
        operating_system = "iOS"
    elif "Android" in agent:
        operating_system = "Android"
    elif "Windows" in agent:
        operating_system = "Windows"
    elif re.search(r"Mac OS X|Macintosh", agent):
        operating_system = "macOS"
    elif "Linux" in agent:
        operating_system = "Linux"
    else:
        operating_system = "Other"
    return device, browser, operating_system


def _technical_label(value: str) -> str:
    return "Drugo" if value in {"Other", "unknown"} else value


def normalize_ip(value: str | None) -> str:
    candidate = (value or "unknown").strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return candidate[:64] or "unknown"


def referer_host(value: str | None) -> str:
    if not value:
        return ""
    try:
        return (urlsplit(value).hostname or "")[:255]
    except ValueError:
        return ""


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _filter_date(value: str, *, end: bool) -> str:
    candidate = value.strip()[:40]
    if len(candidate) == 10:
        return f"{candidate}T{'23:59:59.999999' if end else '00:00:00'}+00:00"
    try:
        return _utc(
            datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        ).isoformat()
    except ValueError:
        return "9999-12-31T23:59:59+00:00" if end else "0001-01-01T00:00:00+00:00"


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value)


def _openai_failure_count(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    failures = 0
    for document in result.get("documents", []):
        if not isinstance(document, dict):
            continue
        for warning in document.get("extraction_warnings", []):
            if str(warning).startswith(
                "Povzetka z umetno inteligenco ni bilo mogoče pripraviti"
            ):
                failures += 1
    return failures
