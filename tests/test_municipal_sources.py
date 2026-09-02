import httpx

from app.municipal_sources import MunicipalPlanningSourceLoader
from app.pdf_parser import PDFText


class _Parser:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, path, checksum):
        self.calls += 1
        return PDFText(pages=["Uradno besedilo " + ("x" * 1_100)])


def test_kranj_official_sources_are_downloaded_and_cached(settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=b"%PDF-1.4 test document")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    parser = _Parser()
    loader = MunicipalPlanningSourceLoader(settings, parser, http_client=client)

    first, warnings = loader.load("Kranj")
    second, second_warnings = loader.load("Mestna občina Kranj")

    assert len(first) == 2
    assert len(second) == 2
    assert warnings == []
    assert second_warnings == []
    assert len(requests) == 2
    assert parser.calls == 2
    assert all(source.url.startswith("https://") for source in first)
    client.close()
