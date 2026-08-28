from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    result = Settings(
        base_dir=tmp_path,
        data_dir=tmp_path / "data",
        openai_api_key=None,
        openai_model="test-model",
        summary_language="English",
        search_workers=1,
        http_timeout_seconds=5,
        max_archive_bytes=10 * 1024 * 1024,
        max_pdf_bytes=2 * 1024 * 1024,
        max_summary_context_chars=10_000,
        pis_verify_ssl=False,
        pdf_enable_ocr=False,
    )
    result.ensure_directories()
    return result
