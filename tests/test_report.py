from types import SimpleNamespace

import pymupdf

from app import main
from app.models import (
    ParcelInformation,
    PlanningCondition,
    PlanningContext,
    PlanningLandUseMap,
    PlanningMapEvidence,
    SearchResult,
    SpatialFinding,
)
from app.report import (
    build_report_sections,
    generate_location_report,
    report_filename,
    resolve_report_map_preview,
)


def result() -> SearchResult:
    return SearchResult(
        parcel=ParcelInformation(
            parcel_number="314/4",
            cadastral_municipality_id=2057,
            cadastral_municipality="Šentvid nad Ljubljano",
            municipality="Ljubljana",
            area_m2=1234,
        )
    )


def test_report_uses_all_official_sections_and_marks_unknowns_for_review():
    sections = build_report_sections(result())

    assert [section.number for section in sections] == list(range(1, 11))
    assert sections[0].status == "automatic"
    assert sections[3].title == "ZAČASNI UKREPI"
    assert sections[3].status == "review"
    assert sections[4].status == "review"
    assert sections[6].status == "review"
    assert sections[7].status == "review"


def test_generated_pdf_is_readable_branded_and_keeps_slovenian_characters():
    pdf = generate_location_report(result())

    assert pdf.startswith(b"%PDF")
    document = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert document.page_count >= 3
    assert "Propioscan" in text
    assert "Lokacijska informacija" in text
    assert "Šentvid nad Ljubljano" in text
    assert "NI URADNA LISTINA" in text
    assert "10" in text


def test_generated_pdf_handles_a_planning_unit_without_a_subunit():
    report_result = result()
    report_result.planning_context = [
        PlanningContext(
            land_use_code="SS",
            land_use_description="Stanovanjske površine",
            planning_unit="ŠE-123",
            subunit=None,
            parcel_share_percent=100,
        )
    ]

    pdf = generate_location_report(report_result)

    assert pdf.startswith(b"%PDF")
    document = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert "EUP ŠE-123" in text


def test_section_six_uses_requested_legal_regime_columns():
    report_result = result()
    report_result.constraints = [
        SpatialFinding(
            category="Varovalni pas elektroenergetskega omrežja",
            name="Podzemni kabelski vod 0,4 kV",
            legal_basis="112. člen Energetskega zakona (EZ-2)",
            geometry_relation="Vod seka parcelo; pas 1 m na vsako stran.",
            reference="EID GJI 123",
            source="GURS – Zbirni kataster GJI",
            source_url="https://www.e-prostor.gov.si/",
        )
    ]

    section = build_report_sections(report_result)[5]
    labels = [field.label for field in section.fields]
    values = [field.value for field in section.fields]

    assert any(label.startswith("Vrsta režima") for label in labels)
    assert any(label.startswith("Ime režima") for label in labels)
    assert any(label.startswith("Pravna podlaga") for label in labels)
    assert any(label.startswith("Vir") for label in labels)
    assert any(label.startswith("Geometrija") for label in labels)
    assert "Podzemni kabelski vod 0,4 kV" in values
    assert "112. člen Energetskega zakona (EZ-2)" in values


def test_generated_pdf_embeds_the_legal_regime_map_appendix(tmp_path):
    preview = tmp_path / "regime.png"
    image_document = pymupdf.open()
    image_page = image_document.new_page(width=640, height=390)
    image_page.draw_rect(image_page.rect, color=(0.2, 0.5, 0.3), fill=(0.91, 0.95, 0.9))
    image_page.draw_line((80, 80), (540, 310), color=(0.8, 0.15, 0.12), width=8)
    image_page.get_pixmap().save(preview)
    image_document.close()

    pdf = generate_location_report(result(), regime_map_preview_path=preview)
    document = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)

    assert "GEOMETRIJSKA PRILOGA PRAVNIH REŽIMOV" in text
    assert sum(len(page.get_images(full=True)) for page in document) >= 1


def test_section_ten_contains_all_seventeen_planning_condition_descriptions():
    report_result = result()
    report_result.planning_conditions = [
        PlanningCondition(
            key=f"topic-{index}",
            title=(
                "Vrste dopustnih dejavnosti"
                if index == 1
                else "Osnovni funkcionalni in oblikovni pogoji"
                if index == 2
                else f"Vsebinski sklop {index}"
            ),
            description=f"Opis pogoja {index} iz uradnega odloka.",
            available=True,
            source_title="Odlok o OPN",
            source_url="/api/files/1/odlok.pdf",
            pages=[index],
        )
        for index in range(1, 18)
    ]

    section = build_report_sections(report_result)[9]
    pdf = generate_location_report(report_result)
    document = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    normalized_text = " ".join(text.split())

    assert len(section.fields) == 17
    assert section.status == "partial"
    assert section.fields[0].label == "1. Vrste dopustnih dejavnosti"
    assert "Opis pogoja 17" in section.fields[-1].value
    assert "VRSTE DOPUSTNIH DEJAVNOSTI" in normalized_text
    assert "OSNOVNI FUNKCIONALNI IN OBLIKOVNI POGOJI" in normalized_text


def test_generated_pdf_embeds_the_planning_drawing_and_legend(tmp_path):
    preview = tmp_path / "map_previews" / "257973" / "drawing.png"
    preview.parent.mkdir(parents=True)
    image_document = pymupdf.open()
    image_page = image_document.new_page(width=640, height=390)
    image_page.draw_rect(image_page.rect, color=(0.2, 0.5, 0.3), fill=(0.91, 0.95, 0.9))
    image_page.draw_line((80, 80), (540, 310), color=(0.8, 0.15, 0.12), width=8)
    image_page.get_pixmap().save(preview)
    image_document.close()

    report_result = result()
    report_result.planning_land_use_map = _planning_map("/api/map-previews/257973/drawing.png")

    pdf = generate_location_report(report_result, preview)

    document = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert "IZRIS IZ PROSTORSKEGA REDA" in text
    assert "Rdeča linija" in text
    assert sum(len(page.get_images(full=True)) for page in document) >= 1


def test_report_resolves_only_an_existing_local_map_preview(tmp_path):
    preview = tmp_path / "map_previews" / "257973" / "drawing.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"png")
    report_result = result()
    report_result.planning_land_use_map = _planning_map("/api/map-previews/257973/drawing.png")

    assert resolve_report_map_preview(report_result, tmp_path) == preview

    report_result.planning_land_use_map.evidence[0].preview_url = "/api/map-previews/257973/missing.png"
    assert resolve_report_map_preview(report_result, tmp_path) is None


def test_report_filename_is_safe_for_local_download():
    assert report_filename(result()) == "propioscan-lokacijska-informacija-2057-314-4.pdf"


def test_report_endpoint_downloads_completed_result(monkeypatch):
    completed_job = SimpleNamespace(
        result=result(),
        status=SimpleNamespace(value="completed"),
    )
    monkeypatch.setattr(main.jobs, "get", lambda _: completed_job)

    response = main.download_location_report("job-id")

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert response.headers["content-disposition"].endswith(
        'filename="propioscan-lokacijska-informacija-2057-314-4.pdf"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_report_endpoint_returns_a_clear_error_when_generation_fails(monkeypatch):
    completed_job = SimpleNamespace(
        result=result(),
        status=SimpleNamespace(value="completed"),
    )
    monkeypatch.setattr(main.jobs, "get", lambda _: completed_job)
    monkeypatch.setattr(main, "generate_location_report", lambda *_: (_ for _ in ()).throw(RuntimeError("broken")))

    try:
        main.download_location_report("job-id")
    except main.HTTPException as exc:
        assert exc.status_code == 500
        assert "PDF-ja" in exc.detail
    else:
        raise AssertionError("Expected PDF generation failure to return HTTP 500")


def _planning_map(preview_url: str) -> PlanningLandUseMap:
    return PlanningLandUseMap(
        land_use_url="https://example.test/land-use",
        parcel_overlay_url="https://example.test/parcel",
        legend_url="https://example.test/legend",
        note="Informativni izris",
        evidence=[
            PlanningMapEvidence(
                act_id=257973,
                act_title="Odlok o občinskem prostorskem načrtu",
                pdf_title="Grafični prikaz namenske rabe",
                pdf_download_url="/api/files/257973/drawing.pdf",
                preview_url=preview_url,
                page=1,
                match_method="geospatial",
            )
        ],
    )
