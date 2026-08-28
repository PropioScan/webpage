from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject

from app.gurs import GURSParcel
from app.models import ParcelInformation
from app.planning_map import (
    build_planning_land_use_map,
    inspect_map_document,
    is_land_use_map,
    render_pdf_map_preview,
)


def _number_array(values: list[float]) -> ArrayObject:
    return ArrayObject([FloatObject(value) for value in values])


def _geopdf(path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    measure = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/GEO"),
            NameObject("/GPTS"): _number_array(
                [46.0, 14.0, 46.1, 14.0, 46.1, 14.1, 46.0, 14.1]
            ),
            NameObject("/LPTS"): _number_array([0, 1, 0, 0, 1, 0, 1, 1]),
        }
    )
    viewport = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Viewport"),
            NameObject("/BBox"): _number_array([20, 380, 580, 20]),
            NameObject("/Measure"): measure,
        }
    )
    page[NameObject("/VP")] = ArrayObject([viewport])
    with path.open("wb") as output:
        writer.write(output)


def _parcel() -> GURSParcel:
    return GURSParcel(
        information=ParcelInformation(
            parcel_number="123/4",
            cadastral_municipality_id=1723,
            area_m2=100,
        ),
        geometry={
            "type": "Polygon",
            "coordinates": [[[1, 1], [2, 1], [2, 2], [1, 1]]],
        },
        bbox=[1, 1, 2, 2],
        eid="eid",
        geometry_wgs84={
            "type": "Polygon",
            "coordinates": [
                [
                    [14.04, 46.04],
                    [14.06, 46.04],
                    [14.06, 46.06],
                    [14.04, 46.06],
                    [14.04, 46.04],
                ]
            ],
        },
        bbox_wgs84=[14.04, 46.04, 14.06, 46.06],
    )


def test_identifies_only_land_use_map_paths():
    assert is_land_use_map("graficni_del/kart_del/eup_nrp_pos/C0608.pdf")
    assert is_land_use_map("graficni_del/izvedeni_podatki/graficni_prikazi/C1.pdf")
    assert not is_land_use_map("graficni_del/kart_del/eup_gji_pos/C0608.pdf")
    assert not is_land_use_map("graficni_del/kart_del/legenda.pdf")


def test_geopdf_match_renders_a_preview_with_parcel_outline(tmp_path: Path):
    pdf = tmp_path / "C0608.pdf"
    _geopdf(pdf)
    matches = inspect_map_document(
        pdf,
        "graficni_del/kart_del/eup_nrp_pos/C0608.pdf",
        _parcel(),
        [],
    )

    assert len(matches) == 1
    assert matches[0].page == 1
    assert matches[0].match_method == "geospatial"
    preview_name = render_pdf_map_preview(
        path=pdf,
        checksum="a" * 64,
        parcel=_parcel(),
        parcel_number="123/4",
        match=matches[0],
        destination=tmp_path / "previews",
    )
    assert preview_name is not None
    assert (tmp_path / "previews" / preview_name).read_bytes().startswith(b"\x89PNG")
    second_parcel = _parcel()
    second_parcel.eid = "different-parcel"
    second_name = render_pdf_map_preview(
        path=pdf,
        checksum="a" * 64,
        parcel=second_parcel,
        parcel_number="999",
        match=matches[0],
        destination=tmp_path / "previews",
    )
    assert second_name != preview_name


def test_planning_map_contains_pis_layer_and_filtered_parcel_overlay():
    result = build_planning_land_use_map(_parcel(), [])

    assert "NRP_OPN" in result.land_use_url
    assert "PARCELE" in result.parcel_overlay_url
    assert "123%2F4" in result.parcel_overlay_url
    assert "GetLegendGraphic" in result.legend_url
