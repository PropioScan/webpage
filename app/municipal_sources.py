from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Settings
from .http_client import USER_AGENT
from .pdf_parser import PDFParser
from .pip_extractor import PlanningTextSource


@dataclass(frozen=True)
class _MunicipalSourceSpec:
    filename: str
    title: str
    url: str


_KRANJ_SOURCES = (
    _MunicipalSourceSpec(
        filename="kranj-ipn-consolidated.pdf",
        title="MOK – Neuradno prečiščeno besedilo Odloka o IPN",
        url=(
            "https://www.kranj.si/files/06_mestna_obcina/mestna_uprava/"
            "urad_za_okolje_in_prostor/neuradno-precisceno-besedilo.pdf"
        ),
    ),
    _MunicipalSourceSpec(
        filename="kranj-ipn-annex-1-eup.pdf",
        title="MOK – Priloga 1: Preglednica enot urejanja prostora",
        url=(
            "https://prostor.kranj.si/prostorski-akti/datoteke/3/"
            "2b4e20c0721a97923a73.pdf/"
            "Ipn_mok_odlok_priloga_1_preglednica_20EUP.pdf"
        ),
    ),
)

_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class MunicipalPlanningSourceLoader:
    """Load searchable official municipal documents when the PIS copy is scanned."""

    def __init__(
        self,
        settings: Settings,
        parser: PDFParser,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.parser = parser
        self._owns_client = http_client is None
        self.client = http_client or httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout_seconds, read=180.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf, */*"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def load(
        self, municipality: str | None
    ) -> tuple[list[PlanningTextSource], list[str]]:
        specs = self._specs_for(municipality)
        if not specs:
            return [], []

        sources: list[PlanningTextSource] = []
        warnings: list[str] = []
        for spec in specs:
            try:
                pdf_path, stale = self._get_pdf(spec)
                checksum = self._checksum(pdf_path)
                pages = self._get_pages(pdf_path, checksum)
                if sum(len(page.strip()) for page in pages) < 1_000:
                    raise ValueError("the official PDF contains no searchable text")
                sources.append(
                    PlanningTextSource(
                        title=spec.title,
                        url=spec.url,
                        pages=pages,
                    )
                )
                if stale:
                    warnings.append(
                        f"Uporabljen je predhodno shranjen izvod vira ‘{spec.title}’, "
                        "ker osvežitev trenutno ni uspela."
                    )
            except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(
                    f"Uradnega dodatnega vira ‘{spec.title}’ ni bilo mogoče prebrati "
                    f"({type(exc).__name__})."
                )
        return sources, warnings

    @staticmethod
    def _specs_for(municipality: str | None) -> tuple[_MunicipalSourceSpec, ...]:
        normalized = _normalize(municipality or "")
        if normalized in {"kranj", "mestna obcina kranj"}:
            return _KRANJ_SOURCES
        return ()

    def _get_pdf(self, spec: _MunicipalSourceSpec) -> tuple[Path, bool]:
        directory = self.settings.data_dir / "municipal_sources"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / spec.filename
        fresh = (
            destination.is_file()
            and destination.stat().st_size > 4
            and time.time() - destination.stat().st_mtime < _CACHE_MAX_AGE_SECONDS
        )
        if fresh and destination.read_bytes()[:4] == b"%PDF":
            return destination, False

        temporary = destination.with_suffix(".pdf.part")
        try:
            size = 0
            with self.client.stream("GET", spec.url) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > self.settings.max_pdf_bytes:
                            raise ValueError("the official PDF exceeds the configured limit")
                        output.write(chunk)
            if size < 5 or temporary.read_bytes()[:4] != b"%PDF":
                raise ValueError("the official source did not return a PDF")
            os.replace(temporary, destination)
            return destination, False
        except (httpx.HTTPError, OSError, ValueError):
            temporary.unlink(missing_ok=True)
            if destination.is_file() and destination.read_bytes()[:4] == b"%PDF":
                return destination, True
            raise

    def _get_pages(self, pdf_path: Path, checksum: str) -> list[str]:
        cache_path = pdf_path.with_name(f"{pdf_path.stem}-{checksum[:12]}.json")
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            pages = payload.get("pages") if isinstance(payload, dict) else None
            if isinstance(pages, list) and all(isinstance(page, str) for page in pages):
                return pages

        pages = self.parser.extract(pdf_path, checksum).pages
        temporary = cache_path.with_suffix(".json.part")
        temporary.write_text(
            json.dumps({"checksum": checksum, "pages": pages}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, cache_path)
        return pages

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()
