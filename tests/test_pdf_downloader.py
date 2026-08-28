import io
import zipfile

import httpx

from app.config import Settings
from app.pdf_downloader import PISArchiveDownloader
from app.pis import PISAct


def make_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("tekstualni_del/odlok.pdf", b"%PDF-1.4 fake")
        archive.writestr("../outside.pdf", b"%PDF-1.4 second")
        archive.writestr("notes.txt", b"ignored")
    return output.getvalue()


def test_downloader_posts_jsf_form_and_safely_caches_pdfs(settings: Settings):
    archive_bytes = make_zip()
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                text="""<html><body><form id="skupen_pregled_prostosrkih_aktov_form">
                <input name="skupen_pregled_prostosrkih_aktov_form" value="skupen_pregled_prostosrkih_aktov_form">
                <input name="javax.faces.ViewState" value="state">
            </form></body></html>""",
            )
        calls["post"] += 1
        assert b"prenesi_gradivo_zip_button_id" in request.content
        return httpx.Response(
            200, content=archive_bytes, headers={"content-type": "application/zip"}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PISArchiveDownloader(settings, http_client=http)
    act = PISAct(
        1, 2, "Test act", "OPN", "VELJAVEN", "https://pis.example", "completed"
    )
    first = downloader.download_pdfs(act)
    second = downloader.download_pdfs(act)
    assert len(first) == 2
    assert first == second
    assert calls["post"] == 1
    assert all(
        "/" not in item.local_name and ".." not in item.local_name for item in first
    )
    assert all(
        (settings.data_dir / "pdfs" / "1" / item.local_name).is_file() for item in first
    )
