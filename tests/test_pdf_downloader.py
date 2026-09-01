import io
import zipfile

import httpx

from app.config import Settings
from app.pdf_downloader import PISArchiveDownloader
from app.pis import PISAct


def make_zip() -> bytes:
    textual_output = io.BytesIO()
    with zipfile.ZipFile(textual_output, "w") as textual_archive:
        textual_archive.writestr("11_odlok/OPN_test_odlok.pdf", b"%PDF-1.4 nested")

    unrelated_output = io.BytesIO()
    with zipfile.ZipFile(unrelated_output, "w") as unrelated_archive:
        unrelated_archive.writestr("should-not-be-read.pdf", b"%PDF-1.4 unrelated")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("tekstualni_del/odlok.pdf", b"%PDF-1.4 fake")
        archive.writestr(
            "tekstualni_del/tekstualni_del/1_tekstualni_del.zip",
            textual_output.getvalue(),
        )
        archive.writestr("priloge/okolje_presoje/op.zip", unrelated_output.getvalue())
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
    assert len(first) == 3
    assert first == second
    assert calls["post"] == 1
    assert all(
        "/" not in item.local_name and ".." not in item.local_name for item in first
    )
    assert all(
        (settings.data_dir / "pdfs" / "1" / item.local_name).is_file() for item in first
    )
    assert any(
        item.source_name.endswith(
            "1_tekstualni_del.zip::11_odlok/OPN_test_odlok.pdf"
        )
        for item in first
    )
    assert not any("should-not-be-read" in item.source_name for item in first)


def test_old_unversioned_manifest_is_ignored(settings: Settings):
    act_dir = settings.data_dir / "pdfs" / "1"
    act_dir.mkdir(parents=True)
    document = act_dir / "old.pdf"
    document.write_bytes(b"%PDF")
    manifest = act_dir / "manifest.json"
    manifest.write_text(
        '[{"source_name":"old.pdf","local_name":"old.pdf",'
        '"size_bytes":4,"sha256":"old"}]',
        encoding="utf-8",
    )

    assert PISArchiveDownloader._read_manifest(manifest, act_dir) is None
