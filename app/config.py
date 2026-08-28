from __future__ import annotations

import os
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

    @property
    def captcha_configured(self) -> bool:
        return bool(self.turnstile_site_key and self.turnstile_secret_key)

    @classmethod
    def from_env(cls) -> "Settings":
        base_dir = Path(__file__).resolve().parent.parent
        configured_data_dir = Path(os.getenv("DATA_DIR", "./data"))
        if not configured_data_dir.is_absolute():
            configured_data_dir = base_dir / configured_data_dir
        return cls(
            base_dir=base_dir,
            data_dir=configured_data_dir.resolve(),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-sol"),
            summary_language=os.getenv("SUMMARY_LANGUAGE", "Slovenian"),
            search_workers=max(1, _int("SEARCH_WORKERS", 2)),
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
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
