from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

import httpx
from lxml import html

from .config import Settings
from .errors import DocumentDownloadError
from .http_client import USER_AGENT
from .pis import PISAct


PIS_DETAIL_URL = (
    "https://pis.eprostor.gov.si/pis-evt-web/pages/javni-del/"
    "prostorskiakti/prostorski_akt_podrobnosti.xhtml"
)
PIS_FORM_ID = "skupen_pregled_prostosrkih_aktov_form"
PIS_DOWNLOAD_BUTTON = f"{PIS_FORM_ID}:prenesi_gradivo_zip_button_id"


@dataclass(frozen=True)
class CachedPDF:
    source_name: str
    local_name: str
    size_bytes: int
    sha256: str


def _safe_filename(source_name: str, digest: str) -> str:
    name = unicodedata.normalize("NFKC", PurePosixPath(source_name).name)
    stem = Path(name).stem
    stem = re.sub(r"[^\w .()-]+", "_", stem, flags=re.UNICODE).strip(" .")
    stem = stem[:100] or "document"
    return f"{stem}-{digest[:10]}.pdf"


class PISArchiveDownloader:
    def __init__(
        self, settings: Settings, *, http_client: httpx.Client | None = None
    ) -> None:
        self.settings = settings
        self._owns_client = http_client is None
        self.client = http_client or httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout_seconds, read=180.0),
            verify=settings.pis_verify_ssl,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def download_pdfs(self, act: PISAct) -> list[CachedPDF]:
        act_dir = self.settings.data_dir / "pdfs" / str(act.procedure_id)
        manifest_path = act_dir / "manifest.json"
        cached = self._read_manifest(manifest_path, act_dir)
        if cached is not None:
            return cached

        archive_path = self.settings.data_dir / "archives" / f"{act.procedure_id}.zip"
        if not archive_path.exists():
            self._download_archive(act, archive_path)
        try:
            documents = self._extract_pdfs(archive_path, act_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            archive_path.unlink(missing_ok=True)
            raise DocumentDownloadError(
                f"PIS returned an invalid archive for ‘{act.title}’."
            ) from exc
        act_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json_write(manifest_path, [asdict(item) for item in documents])
        return documents

    def _download_archive(self, act: PISAct, destination: Path) -> None:
        params = {"idPostopka": str(act.procedure_id), "veljavenAkt": "false"}
        try:
            page = self.client.get(PIS_DETAIL_URL, params=params)
            page.raise_for_status()
            document = html.fromstring(page.content)
            forms = document.xpath(f"//form[@id='{PIS_FORM_ID}']")
            if not forms:
                raise DocumentDownloadError(
                    f"PIS did not expose downloadable material for ‘{act.title}’."
                )
            form = forms[0]
            payload = {
                element.get("name"): element.get("value", "")
                for element in form.xpath(".//input[@name]")
                if element.get("name")
            }
            payload[PIS_DOWNLOAD_BUTTON] = PIS_DOWNLOAD_BUTTON
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".zip.part")
            size = 0
            with self.client.stream("POST", str(page.url), data=payload) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "zip" not in content_type:
                    raise DocumentDownloadError(
                        f"PIS did not return a document archive for ‘{act.title}’."
                    )
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > self.settings.max_archive_bytes:
                            raise DocumentDownloadError(
                                f"The PIS archive for ‘{act.title}’ exceeds the configured limit."
                            )
                        output.write(chunk)
            os.replace(temporary, destination)
        except DocumentDownloadError:
            destination.with_suffix(".zip.part").unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            destination.with_suffix(".zip.part").unlink(missing_ok=True)
            raise DocumentDownloadError(
                f"Could not download PIS material for ‘{act.title}’."
            ) from exc

    def _extract_pdfs(self, archive_path: Path, destination: Path) -> list[CachedPDF]:
        destination.mkdir(parents=True, exist_ok=True)
        results: list[CachedPDF] = []
        total_uncompressed = 0
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not member.filename.lower().endswith(".pdf"):
                    continue
                total_uncompressed += member.file_size
                if member.file_size > self.settings.max_pdf_bytes:
                    raise DocumentDownloadError(
                        f"{PurePosixPath(member.filename).name} exceeds the configured PDF limit."
                    )
                if total_uncompressed > self.settings.max_archive_bytes * 2:
                    raise DocumentDownloadError(
                        "The expanded PIS archive exceeds the configured safety limit."
                    )
                digest = hashlib.sha256()
                temporary = destination / f".{len(results)}.pdf.part"
                written = 0
                with archive.open(member) as source, temporary.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > self.settings.max_pdf_bytes:
                            raise DocumentDownloadError(
                                f"A PDF in {archive_path.name} exceeds the configured limit."
                            )
                        digest.update(chunk)
                        output.write(chunk)
                checksum = digest.hexdigest()
                local_name = _safe_filename(member.filename, checksum)
                final_path = destination / local_name
                if final_path.exists():
                    temporary.unlink(missing_ok=True)
                else:
                    os.replace(temporary, final_path)
                results.append(
                    CachedPDF(
                        source_name=member.filename,
                        local_name=local_name,
                        size_bytes=written,
                        sha256=checksum,
                    )
                )
        return results

    @staticmethod
    def _read_manifest(manifest_path: Path, act_dir: Path) -> list[CachedPDF] | None:
        if not manifest_path.exists():
            return None
        try:
            rows = json.loads(manifest_path.read_text(encoding="utf-8"))
            documents = [CachedPDF(**row) for row in rows]
        except (OSError, ValueError, TypeError):
            return None
        if all((act_dir / item.local_name).is_file() for item in documents):
            return documents
        return None

    @staticmethod
    def _atomic_json_write(path: Path, data: object) -> None:
        temporary = path.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
