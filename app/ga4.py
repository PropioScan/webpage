from __future__ import annotations

import copy
import csv
import io
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx


ANALYTICS_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ANALYTICS_API = "https://analyticsdata.googleapis.com/v1beta"
ALLOWED_PERIODS = {7, 30, 90}
CUSTOM_EVENTS = (
    "parcel_analysis_started",
    "parcel_analysis_completed",
    "parcel_analysis_failed",
    "result_tab_opened",
    "location_report_downloaded",
)
CHANNEL_LABELS = {
    "Direct": "Neposredno",
    "Organic Search": "Organsko iskanje",
    "Referral": "Povezave z drugih strani",
    "Organic Social": "Družbena omrežja",
    "Unassigned": "Nedoločeno",
}
DEVICE_LABELS = {
    "desktop": "Namizni računalnik",
    "mobile": "Telefon",
    "tablet": "Tablica",
}
EVENT_LABELS = {
    "parcel_analysis_started": "Začete analize",
    "parcel_analysis_completed": "Zaključene analize",
    "parcel_analysis_failed": "Neuspele analize",
    "result_tab_opened": "Odprti zavihki rezultatov",
    "location_report_downloaded": "Prenesena poročila PDF",
}
COUNTRY_LABELS = {
    "Slovenia": "Slovenija",
    "Austria": "Avstrija",
    "Croatia": "Hrvaška",
    "Italy": "Italija",
    "Germany": "Nemčija",
    "Hungary": "Madžarska",
    "United States": "Združene države Amerike",
    "United Kingdom": "Združeno kraljestvo",
}


class Ga4ConfigurationError(RuntimeError):
    """Raised when server-side access to GA4 is incomplete."""


class Ga4QueryError(RuntimeError):
    """Raised when the Google Analytics Data API cannot return a report."""


class Ga4Reporter:
    def __init__(
        self,
        property_id: str | None,
        credentials_file: Path | None,
        *,
        timeout_seconds: float = 20.0,
        cache_seconds: int = 300,
        access_token_provider: Callable[[], str] | None = None,
        post_json: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
    ) -> None:
        normalized = (property_id or "").strip()
        if normalized.startswith("properties/"):
            normalized = normalized.removeprefix("properties/")
        self.property_id = normalized
        self.credentials_file = credentials_file
        self.timeout_seconds = max(5.0, timeout_seconds)
        self.cache_seconds = max(30, cache_seconds)
        self._access_token_provider = access_token_provider
        self._post_json = post_json or self._default_post_json
        self._credentials: Any = None
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        credentials_available = bool(
            self._access_token_provider
            or (self.credentials_file and self.credentials_file.is_file())
        )
        return bool(re.fullmatch(r"[1-9][0-9]{5,19}", self.property_id)) and credentials_available

    def report(self, days: int = 30, *, force_refresh: bool = False) -> dict[str, Any]:
        if days not in ALLOWED_PERIODS:
            raise ValueError("Analytics period must be 7, 30, or 90 days")
        if not self.configured:
            raise Ga4ConfigurationError("Google Analytics reporting is not configured")

        with self._lock:
            cached = self._cache.get(days)
            if cached and not force_refresh and cached[0] > time.monotonic():
                return copy.deepcopy(cached[1])

            token = self._access_token()
            reports = self._fetch_reports(days, token)
            result = self._normalize_reports(days, reports)
            self._cache[days] = (time.monotonic() + self.cache_seconds, result)
            return copy.deepcopy(result)

    def export_csv(self, days: int = 30) -> tuple[str, str]:
        report = self.report(days)
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        writer.writerow(("Propioscan · Google Analytics 4", f"Zadnjih {days} dni"))
        writer.writerow(())
        writer.writerow(("Povzetek", "Vrednost"))
        for key, label in (
            ("total_users", "Uporabniki"),
            ("new_users", "Novi uporabniki"),
            ("sessions", "Seje"),
            ("page_views", "Ogledi strani"),
            ("engaged_sessions", "Aktivne seje"),
            ("average_session_duration", "Povprečno trajanje seje (s)"),
        ):
            writer.writerow((label, report["summary"].get(key, 0)))
        for key, title, columns in (
            (
                "daily",
                "Po dnevih",
                (("date", "Datum"), ("active_users", "Aktivni uporabniki"), ("sessions", "Seje"), ("page_views", "Ogledi strani")),
            ),
            (
                "channels",
                "Viri obiska",
                (("channel", "Vir obiska"), ("sessions", "Seje"), ("total_users", "Uporabniki")),
            ),
            ("devices", "Naprave", (("device", "Naprava"), ("total_users", "Uporabniki"))),
            ("countries", "Države", (("country", "Država"), ("total_users", "Uporabniki"))),
            ("events", "Dogodki Propioscan", (("event", "Dogodek"), ("event_count", "Število"))),
        ):
            writer.writerow(())
            writer.writerow((title,))
            writer.writerow(tuple(label for _, label in columns))
            for row in report[key]:
                writer.writerow(
                    tuple(
                        _localized_export_value(key, column, row.get(column, ""))
                        for column, _ in columns
                    )
                )
        return f"propioscan-ga4-{days}-dni.csv", output.getvalue()

    def _access_token(self) -> str:
        if self._access_token_provider:
            token = self._access_token_provider()
            if not token:
                raise Ga4ConfigurationError("Google Analytics access token is empty")
            return token
        if not self.credentials_file:
            raise Ga4ConfigurationError("Google Analytics credentials file is missing")
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials

            if self._credentials is None:
                self._credentials = Credentials.from_service_account_file(
                    str(self.credentials_file),
                    scopes=(ANALYTICS_SCOPE,),
                )
            if not self._credentials.valid:
                self._credentials.refresh(Request())
            if not self._credentials.token:
                raise Ga4ConfigurationError("Google Analytics access token is empty")
            return str(self._credentials.token)
        except Ga4ConfigurationError:
            raise
        except Exception as exc:
            raise Ga4ConfigurationError(
                "Google Analytics service-account credentials could not be used"
            ) from exc

    def _fetch_reports(self, days: int, token: str) -> dict[str, dict[str, Any]]:
        date_range = {"startDate": f"{days - 1}daysAgo", "endDate": "today"}
        specifications = [
            (
                "summary",
                {
                    "dateRanges": [date_range],
                    "metrics": [
                        {"name": "totalUsers"},
                        {"name": "newUsers"},
                        {"name": "sessions"},
                        {"name": "screenPageViews"},
                        {"name": "engagedSessions"},
                        {"name": "averageSessionDuration"},
                    ],
                },
            ),
            (
                "daily",
                {
                    "dateRanges": [date_range],
                    "dimensions": [{"name": "date"}],
                    "metrics": [
                        {"name": "activeUsers"},
                        {"name": "sessions"},
                        {"name": "screenPageViews"},
                    ],
                    "orderBys": [{"dimension": {"dimensionName": "date"}}],
                },
            ),
            (
                "channels",
                {
                    "dateRanges": [date_range],
                    "dimensions": [{"name": "sessionDefaultChannelGroup"}],
                    "metrics": [{"name": "sessions"}, {"name": "totalUsers"}],
                    "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                    "limit": "10",
                },
            ),
            (
                "devices",
                {
                    "dateRanges": [date_range],
                    "dimensions": [{"name": "deviceCategory"}],
                    "metrics": [{"name": "totalUsers"}],
                    "orderBys": [{"metric": {"metricName": "totalUsers"}, "desc": True}],
                    "limit": "10",
                },
            ),
            (
                "countries",
                {
                    "dateRanges": [date_range],
                    "dimensions": [{"name": "country"}],
                    "metrics": [{"name": "totalUsers"}],
                    "orderBys": [{"metric": {"metricName": "totalUsers"}, "desc": True}],
                    "limit": "10",
                },
            ),
            (
                "events",
                {
                    "dateRanges": [date_range],
                    "dimensions": [{"name": "eventName"}],
                    "metrics": [{"name": "eventCount"}],
                    "dimensionFilter": {
                        "filter": {
                            "fieldName": "eventName",
                            "inListFilter": {"values": list(CUSTOM_EVENTS)},
                        }
                    },
                    "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
                    "limit": "20",
                },
            ),
        ]
        endpoint = f"{ANALYTICS_API}/properties/{self.property_id}:batchRunReports"
        result: dict[str, dict[str, Any]] = {}
        for start in range(0, len(specifications), 5):
            chunk = specifications[start : start + 5]
            payload = {"requests": [request for _, request in chunk]}
            response = self._post_json(endpoint, payload, token)
            returned = response.get("reports", [])
            for index, (name, _) in enumerate(chunk):
                result[name] = returned[index] if index < len(returned) else {}
        return result

    def _default_post_json(
        self, url: str, payload: dict[str, Any], token: str
    ) -> dict[str, Any]:
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise Ga4QueryError("Google Analytics returned an invalid response")
            return data
        except Ga4QueryError:
            raise
        except Exception as exc:
            raise Ga4QueryError("Google Analytics Data API request failed") from exc

    def _normalize_reports(
        self, days: int, reports: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        summary_rows = _report_rows(reports.get("summary", {}))
        summary = summary_rows[0] if summary_rows else {}
        return {
            "period_days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_users": summary.get("totalUsers", 0),
                "new_users": summary.get("newUsers", 0),
                "sessions": summary.get("sessions", 0),
                "page_views": summary.get("screenPageViews", 0),
                "engaged_sessions": summary.get("engagedSessions", 0),
                "average_session_duration": summary.get("averageSessionDuration", 0),
            },
            "daily": [
                {
                    "date": row.get("date", ""),
                    "active_users": row.get("activeUsers", 0),
                    "sessions": row.get("sessions", 0),
                    "page_views": row.get("screenPageViews", 0),
                }
                for row in _report_rows(reports.get("daily", {}))
            ],
            "channels": [
                {
                    "channel": row.get("sessionDefaultChannelGroup", "—"),
                    "sessions": row.get("sessions", 0),
                    "total_users": row.get("totalUsers", 0),
                }
                for row in _report_rows(reports.get("channels", {}))
            ],
            "devices": [
                {
                    "device": row.get("deviceCategory", "—"),
                    "total_users": row.get("totalUsers", 0),
                }
                for row in _report_rows(reports.get("devices", {}))
            ],
            "countries": [
                {
                    "country": row.get("country", "—"),
                    "total_users": row.get("totalUsers", 0),
                }
                for row in _report_rows(reports.get("countries", {}))
            ],
            "events": [
                {
                    "event": row.get("eventName", "—"),
                    "event_count": row.get("eventCount", 0),
                }
                for row in _report_rows(reports.get("events", {}))
            ],
        }


def _report_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    dimension_names = [item.get("name", "") for item in report.get("dimensionHeaders", [])]
    metric_names = [item.get("name", "") for item in report.get("metricHeaders", [])]
    rows = []
    for raw_row in report.get("rows", []):
        row: dict[str, Any] = {}
        for name, value in zip(dimension_names, raw_row.get("dimensionValues", [])):
            row[name] = value.get("value", "")
        for name, value in zip(metric_names, raw_row.get("metricValues", [])):
            row[name] = _number(value.get("value", "0"))
        rows.append(row)
    return rows


def _number(value: str) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if number.is_integer() else number


def _localized_export_value(section: str, column: str, value: Any) -> Any:
    if section == "channels" and column == "channel":
        return CHANNEL_LABELS.get(str(value), value)
    if section == "devices" and column == "device":
        return DEVICE_LABELS.get(str(value), value)
    if section == "events" and column == "event":
        return EVENT_LABELS.get(str(value), value)
    if section == "countries" and column == "country":
        return COUNTRY_LABELS.get(str(value), value)
    return value
