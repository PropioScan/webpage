from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    openai_api_key: str | None
    openai_model: str
    summary_language: str
    search_workers: int
    http_timeout_seconds: float
    max_archive_bytes: int
    max_pdf_bytes: int
    max_summary_context_chars: int
    pis_verify_ssl: bool
    pdf_enable_ocr: bool
    privacy_retention_days: int = 90
    captcha_required: bool = False
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    job_execution_mode: str = "thread"
    job_python_executable: str | None = None
    job_retention_days: int = 30
    traffic_retention_days: int = 30
    traffic_group_secret: str = ""
    admin_username: str | None = None
    admin_password_hash: str | None = None
    admin_session_secret: str | None = None
    admin_session_hours: int = 8
    google_analytics_measurement_id: str | None = None
    google_analytics_property_id: str | None = None
    google_analytics_credentials_file: Path | None = None

    @property
    def captcha_configured(self) -> bool:
        return bool(self.turnstile_site_key and self.turnstile_secret_key)

    @property
    def google_analytics_configured(self) -> bool:
        return bool(
            self.google_analytics_measurement_id
            and re.fullmatch(
                r"G-[A-Z0-9]{6,20}", self.google_analytics_measurement_id
            )
        )

    @property
    def google_analytics_reporting_configured(self) -> bool:
        return bool(
            self.google_analytics_property_id
            and re.fullmatch(r"[1-9][0-9]{5,19}", self.google_analytics_property_id)
            and self.google_analytics_credentials_file
            and self.google_analytics_credentials_file.is_file()
        )

    @classmethod
    def from_env(cls) -> "Settings":
        base_dir = Path(__file__).resolve().parent.parent
        configured_data_dir = Path(os.getenv("DATA_DIR", "./data"))
        if not configured_data_dir.is_absolute():
            configured_data_dir = base_dir / configured_data_dir
        analytics_credentials_value = os.getenv(
            "GOOGLE_ANALYTICS_CREDENTIALS_FILE", ""
        ).strip()
        analytics_credentials_file = (
            Path(os.path.expandvars(os.path.expanduser(analytics_credentials_value)))
            if analytics_credentials_value
            else None
        )
        if analytics_credentials_file and not analytics_credentials_file.is_absolute():
            analytics_credentials_file = base_dir / analytics_credentials_file
        job_execution_mode = os.getenv("JOB_EXECUTION_MODE", "thread").strip().lower()
        if job_execution_mode not in {"thread", "process"}:
            job_execution_mode = "thread"
        return cls(
            base_dir=base_dir,
            data_dir=configured_data_dir.resolve(),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-sol"),
            summary_language=os.getenv("SUMMARY_LANGUAGE", "Slovenian"),
            search_workers=max(1, _int("SEARCH_WORKERS", 2)),
            job_execution_mode=job_execution_mode,
            job_python_executable=os.getenv("JOB_PYTHON_EXECUTABLE") or None,
            job_retention_days=max(1, _int("JOB_RETENTION_DAYS", 30)),
            http_timeout_seconds=max(5.0, _float("HTTP_TIMEOUT_SECONDS", 60.0)),
            max_archive_bytes=max(1, _int("MAX_ARCHIVE_MB", 500)) * 1024 * 1024,
            max_pdf_bytes=max(1, _int("MAX_PDF_MB", 500)) * 1024 * 1024,
            max_summary_context_chars=max(
                4_000, _int("MAX_SUMMARY_CONTEXT_CHARS", 30_000)
            ),
            pis_verify_ssl=_bool("PIS_VERIFY_SSL", False),
            pdf_enable_ocr=_bool("PDF_ENABLE_OCR", True),
            privacy_retention_days=max(1, _int("PRIVACY_RETENTION_DAYS", 90)),
            captcha_required=_bool("CAPTCHA_REQUIRED", True),
            turnstile_site_key=os.getenv("TURNSTILE_SITE_KEY") or None,
            turnstile_secret_key=os.getenv("TURNSTILE_SECRET_KEY") or None,
            traffic_retention_days=max(1, _int("TRAFFIC_RETENTION_DAYS", 30)),
            traffic_group_secret=os.getenv("TRAFFIC_GROUP_SECRET", ""),
            admin_username=os.getenv("ADMIN_USERNAME") or None,
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH") or None,
            admin_session_secret=os.getenv("ADMIN_SESSION_SECRET") or None,
            admin_session_hours=max(1, _int("ADMIN_SESSION_HOURS", 8)),
            google_analytics_measurement_id=(
                os.getenv("GOOGLE_ANALYTICS_MEASUREMENT_ID", "").strip().upper()
                or None
            ),
            google_analytics_property_id=(
                os.getenv("GOOGLE_ANALYTICS_PROPERTY_ID", "").strip()
                or None
            ),
            google_analytics_credentials_file=(
                analytics_credentials_file.resolve()
                if analytics_credentials_file
                else None
            ),
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.data_dir / "archives",
            self.data_dir / "pdfs",
            self.data_dir / "ocr",
            self.data_dir / "map_previews",
            self.data_dir / "privacy",
            self.data_dir / "jobs",
            self.data_dir / "logs",
            self.data_dir / "traffic",
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
