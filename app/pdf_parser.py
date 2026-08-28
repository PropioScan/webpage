from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from .config import Settings
from .errors import PDFExtractionError
from .models import Excerpt


@dataclass
class PDFText:
    pages: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParcelMentions:
    count: int
    excerpts: list[Excerpt]


def parcel_pattern(parcel_number: str) -> re.Pattern[str]:
    parts = parcel_number.split("/", 1)
    if len(parts) == 2:
        expression = rf"{re.escape(parts[0])}\s*/\s*{re.escape(parts[1])}"
    else:
        expression = re.escape(parcel_number)
    return re.compile(rf"(?<![\d/]){expression}(?![\d/])", re.IGNORECASE)


def _section_before(text: str, position: int) -> str | None:
    previous_lines = [
        line.strip() for line in text[:position].splitlines() if line.strip()
    ]
    for line in reversed(previous_lines[-14:]):
        if len(line) > 150:
            continue
        numbered = re.match(r"^(?:\d+(?:\.\d+)*[.)]?|[IVX]+\.)\s+.{3,}", line)
        letters = [char for char in line if char.isalpha()]
        uppercase = (
            letters and sum(char.isupper() for char in letters) / len(letters) > 0.75
        )
        if numbered or (uppercase and 3 <= len(line) <= 100):
            return line
    return None


def find_parcel_mentions(pages: list[str], parcel_number: str) -> ParcelMentions:
    pattern = parcel_pattern(parcel_number)
    excerpts: list[Excerpt] = []
    count = 0
    for page_number, raw_text in enumerate(pages, start=1):
        for match in pattern.finditer(raw_text):
            count += 1
            start = max(0, match.start() - 360)
            end = min(len(raw_text), match.end() + 520)
            excerpt_text = re.sub(r"\s+", " ", raw_text[start:end]).strip()
            if start:
                excerpt_text = "…" + excerpt_text
            if end < len(raw_text):
                excerpt_text += "…"
            if excerpts and excerpts[-1].page == page_number:
                previous = excerpts[-1].text.casefold()
                marker = re.sub(r"\s+", " ", match.group()).casefold()
                if marker in previous and abs(match.start() - start) < 120:
                    continue
            excerpts.append(
                Excerpt(
                    page=page_number,
                    section=_section_before(raw_text, match.start()),
                    text=excerpt_text,
                )
            )
    return ParcelMentions(count=count, excerpts=excerpts)


class PDFParser:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(self, path: Path, checksum: str) -> PDFText:
        parsed = self._extract_with_pypdf(path)
        empty_pages = sum(not page.strip() for page in parsed.pages)
        mostly_empty = parsed.pages and empty_pages / len(parsed.pages) >= 0.6
        if mostly_empty:
            parsed.warnings.append(
                f"{empty_pages} of {len(parsed.pages)} pages contain no extractable text."
            )
            ocr_path = self._ocr(path, checksum)
            if ocr_path:
                reparsed = self._extract_with_pypdf(ocr_path)
                reparsed.warnings.append("Scanned pages were processed with OCRmyPDF.")
                return reparsed
            parsed.warnings.append(
                "OCR was unavailable; mentions on scanned pages may be missing. Install OCRmyPDF with Slovenian language data."
            )
        return parsed

    def _extract_with_pypdf(self, path: Path) -> PDFText:
        try:
            reader = PdfReader(path, strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise PDFExtractionError(f"{path.name} is password protected.")
            pages: list[str] = []
            warnings: list[str] = []
            for index, page in enumerate(reader.pages, start=1):
                try:
                    try:
                        text = page.extract_text(extraction_mode="layout") or ""
                    except TypeError:
                        text = page.extract_text() or ""
                except Exception:
                    text = ""
                    warnings.append(f"Text extraction failed on page {index}.")
                pages.append(text)
            return PDFText(pages=pages, warnings=warnings)
        except PDFExtractionError:
            raise
        except Exception as exc:
            raise PDFExtractionError(f"Could not read {path.name} as a PDF.") from exc

    def _ocr(self, path: Path, checksum: str) -> Path | None:
        executable = shutil.which("ocrmypdf")
        if not self.settings.pdf_enable_ocr or not executable:
            return None
        destination = self.settings.data_dir / "ocr" / f"{checksum}.pdf"
        if destination.exists():
            return destination
        temporary = destination.with_name(f"{destination.stem}.part.pdf")
        try:
            subprocess.run(
                [
                    executable,
                    "--skip-text",
                    "--deskew",
                    "--language",
                    "slv+eng",
                    str(path),
                    str(temporary),
                ],
                check=True,
                capture_output=True,
                timeout=900,
            )
            temporary.replace(destination)
            return destination
        except (OSError, subprocess.SubprocessError):
            temporary.unlink(missing_ok=True)
            return None
