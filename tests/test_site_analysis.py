from __future__ import annotations

import httpx

from app.config import Settings
from app.gurs import GURSParcel
from app.models import AssessmentTone, ParcelInformation, PlanningContext
from app.site_analysis import SiteAnalysisClient, assess_land_use, build_parcel_map


def parcel() -> GURSParcel:
    return GURSParcel(
        information=ParcelInformation(
            parcel_number="314/4",
            cadastral_municipality_id=2057,
            cadastral_municipality="K.O.",
        ),
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        },
        bbox=[0, 0, 10, 10],
        eid="123456",
    )


def test_mixed_land_use_is_conditional():
    result = assess_land_use(
        parcel(),
        [
            PlanningContext(
                land_use_code="A",
                land_use_description="Površine razpršene poselitve",
                parcel_share_percent=40,
            ),
            PlanningContext(
                land_use_code="K1",
                land_use_description="Najboljša kmetijska zemljišča",
                parcel_share_percent=60,
            ),
        ],
    )

    assert result.tone == AssessmentTone.caution
    assert next(item for item in result.items if item.code == "K1").tone == AssessmentTone.concern
    assert "ne ustvarja pravice graditi" in result.disclaimer


def test_land_use_combines_repeated_codes_and_calculates_area():
    test_parcel = parcel()
    test_parcel.information.area_m2 = 2_000
    result = assess_land_use(
        test_parcel,
        [
            PlanningContext(
                land_use_code="G",
                land_use_description="Gozdna zemljišča",
                planning_unit="EUP 1",
                parcel_share_percent=28.9,
            ),
            PlanningContext(
                land_use_code="G",
                land_use_description="Gozdna zemljišča",
                planning_unit="EUP 2",
                parcel_share_percent=21.1,
            ),
            PlanningContext(
                land_use_code="SS",
                land_use_description="Stanovanjske površine",
                parcel_share_percent=50,
            ),
        ],
    )

    forest = next(item for item in result.items if item.code == "G")
    assert forest.parcel_share_percent == 50
    assert forest.parcel_area_m2 == 1_000



def test_parcel_map_uses_official_orthophoto_and_filtered_overlay():
    result = build_parcel_map(parcel())

    assert "DOF025" in result.orthophoto_url
    assert "PARCELE" in result.parcel_overlay_url
    assert "314%2F4" in result.parcel_overlay_url
    assert result.ordered_boundary_overlay_url is not None
    assert "UREJENE_MEJE" in result.ordered_boundary_overlay_url
    assert result.infrastructure_overlay_url is not None
    for layer in (
        "LINIJE_VODOVOD_G",
        "LINIJE_KANALIZACIJA_G",
        "LINIJE_ELEKTRICNA_ENERGIJA_G",
        "LINIJE_ELEKTRONSKE_KOMUNIKACIJE_G",
        "LINIJE_ZEMELJSKI_PLIN_G",
        "LINIJE_TOPLOTNA_ENERGIJA_G",
    ):
        assert layer in result.infrastructure_overlay_url
    assert "LINIJE_CESTE_G" not in result.infrastructure_overlay_url
    assert result.legal_regime_overlay_url is not None
    assert "LINIJE_CESTE_G" in result.legal_regime_overlay_url
    assert "POLIGONI_LETALISCA_G" in result.legal_regime_overlay_url
    assert any(
        "SI.GURS.KN%3AOMEJITVE" in url
        for url in result.legal_regime_additional_overlay_urls
    )
    assert result.official_viewer_url.endswith("?eid=123456")


def test_site_analysis_uses_direct_gji_distance_and_exact_overlay_filter(
    settings: Settings,
):
    inside_polygon = {
        "attributes": {"OBMOCJE": "Test Natura", "SDF_ID": "SI-test"},
        "geometry": {
            "rings": [[[1, 1], [4, 1], [4, 4], [1, 4], [1, 1]]],
        },
    }
    outside_polygon = {
        "attributes": {"IME": "Outside", "ESD": "999"},
        "geometry": {
            "rings": [
                [[20, 20], [22, 20], [22, 22], [20, 22], [20, 20]]
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ipi.eprostor.gov.si":
            layer = request.url.params["typeNames"]
            if layer.endswith("OBJEKTI_L_PARCELA"):
                return httpx.Response(
                    200,
                    json={
                        "features": [
                            {
                                "properties": {
                                    "GJI_TEMATIKE_SIFRA": "3100",
                                    "GJI_TEMATIKE_NAZIV_SL": "Vodovod",
                                    "GJI_VRSTE_OBJEKTOV_NAZIV_SL": "Vodooskrbna cev",
                                }
                            }
                        ]
                    },
                )
            if layer.endswith("LINIJE_CESTE_G"):
                return httpx.Response(
                    200,
                    json={
                        "features": [
                            {
                                "geometry": {
                                    "type": "LineString",
                                    "coordinates": [[-5, 0], [15, 0]],
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"features": []})

        path = request.url.path
        if path.endswith("/5667/query"):
            return httpx.Response(
                200, json={"type": "FeatureCollection", "features": [inside_polygon]}
            )
        if path.endswith("/0/query"):
            return httpx.Response(
                200, json={"type": "FeatureCollection", "features": [outside_polygon]}
            )
        return httpx.Response(
            200, json={"type": "FeatureCollection", "features": []}
        )

    client = SiteAnalysisClient(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.analyze(parcel())

    water = next(item for item in result.infrastructure if item.key == "water")
    assert water.status == "on_parcel"
    assert result.road_access.tone == AssessmentTone.positive
    assert [item.name for item in result.protected_areas] == ["Test Natura"]
    assert result.cultural_heritage == []
    assert result.warnings == []


def test_historical_gji_shapes_become_structured_legal_regimes(settings: Settings):
    direct = [
        _direct_gji("cable-1", 2100, "Infrastruktura za električno energijo", "Kablovod - podzemni kabelski vod"),
        _direct_gji("telecom-1", 6100, "Infrastruktura za elektronske komunikacije", "Trasa"),
        _direct_gji("water-1", 3100, "Vodovodna infrastruktura", "Vodooskrbna cev"),
        _direct_gji("sewer-1", 3200, "Kanalizacijska infrastruktura", "Kanalizacijski vod"),
        _direct_gji("path-1", 1100, "Cestna infrastruktura", "Cesta - os ceste"),
    ]
    lines = [
        _line_gji(direct[0], [[5, -5], [5, 15]], attr2="0,4 kV"),
        _line_gji(direct[1], [[12, -5], [12, 15]], attr1="v zemlji"),
        _line_gji(direct[2], [[4, -5], [4, 15]]),
        _line_gji(direct[3], [[6, -5], [6, 15]]),
        _line_gji(direct[4], [[8, -5], [8, 15]], attr1="javna pot"),
        _line_gji(
            _direct_gji("regional-1", 1100, "Cestna infrastruktura", "Cesta - os ceste"),
            [[24, -5], [24, 15]],
            attr1="regionalna cesta I. reda",
        ),
    ]
    client = SiteAnalysisClient(settings)
    try:
        findings = client._gji_regime_findings(parcel(), direct, lines)
    finally:
        client.close()

    by_name = {finding.name: finding for finding in findings}
    cable = by_name["Podzemni kabelski vod 0,4 kV"]
    assert cable.legal_basis == "112. člen Energetskega zakona (EZ-2)"
    assert "1 m na vsako stran" in cable.geometry_relation
    assert cable.reference == "EID GJI cable-1"

    telecom = by_name["Elektronske komunikacije – trasa – v zemlji"]
    assert telecom.legal_basis == "17. člen Zakona o elektronskih komunikacijah (ZEKom-2)"
    assert "3 m na vsako stran" in telecom.geometry_relation
    assert telecom.distance_m == 2

    assert "Vodovod – vodooskrbna cev" in by_name
    assert "Kanalizacija – kanalizacijski vod" in by_name
    assert by_name["Javna pot"].category == "Varovalni pas javne ceste"
    regional = by_name["Regionalna cesta I. reda"]
    assert regional.distance_m == 14
    assert "15 m na vsako stran" in regional.geometry_relation


def test_radovljica_airport_zone_uses_the_municipal_vector_layer(settings: Settings):
    test_parcel = parcel()
    test_parcel.information.municipality = "Radovljica"
    response_html = """
        <html><body><table>
          <tr><td><b>AREA</b></td><td>46570659,6</td></tr>
          <tr><td><b>LABEL</b></td><td>B</td></tr>
        </table></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["QUERY_LAYERS"] == "Vplivno_obmocje_letalisca"
        return httpx.Response(200, text=response_html)

    client = SiteAnalysisClient(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    try:
        findings = client._query_municipal_regimes(test_parcel)
    finally:
        client.close()

    assert len(findings) == 1
    finding = findings[0]
    assert finding.name == "Vplivno območje letališča ALC Lesce – območje B"
    assert "64. člen" in finding.legal_basis
    assert "območju B" in finding.geometry_relation
    map_result = build_parcel_map(test_parcel)
    assert len(map_result.legal_regime_additional_overlay_urls) == 2
    assert any(
        "Vplivno_obmocje_letalisca" in url
        for url in map_result.legal_regime_additional_overlay_urls
    )


def test_cadastral_restriction_returns_concrete_boundary_consent_details(
    settings: Settings,
):
    feature = {
        "properties": {
            "EID_OMEJITEV": "120300000010111683",
            "OPIS": "https://www.uradni-list.si/1/objava.jsp?sop=2021-01-3925",
            "VRSTA_ID": 1,
            "VELJAVNOST_OD": "2026-01-05T00:00:00Z",
            "DATUM_SPREJEMA": "2021-11-24",
            "DATUM_OBJAVE": "2021-12-17",
            "ST_AKTA": "Uradni list RS, št. 196/2021 in 16/2025",
            "NASLOV_OBCINE": "Mestna občina Kranj, Slovenski trg 1, 4000 Kranj",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-2, -2], [12, -2], [12, 12], [-2, 12], [-2, -2]]
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["typeNames"] == "SI.GURS.KN:OMEJITVE"
        assert "BBOX(GEOM" in request.url.params["CQL_FILTER"]
        return httpx.Response(200, json={"features": [feature]})

    client = SiteAnalysisClient(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    try:
        findings = client._query_cadastral_restrictions(parcel())
    finally:
        client.close()

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "Območje obveznega soglasja za spreminjanje meje parcele"
    assert finding.name == (
        "Obvezno soglasje Mestne občine Kranj za spreminjanje meje parcele"
    )
    assert "parcelacijo" in finding.detail
    assert "196/2021 in 16/2025" in finding.legal_basis
    assert "Celotna parcela" in finding.geometry_relation
    assert "5. 1. 2026" in finding.reference
    assert "akt sprejet 24. 11. 2021" in finding.reference
    assert "akt objavljen 17. 12. 2021" in finding.reference
    assert finding.source == "GURS – Kataster nepremičnin, sloj OMEJITVE"
    assert finding.source_url.endswith("sop=2021-01-3925")


def _direct_gji(eid: str, code: int, theme: str, kind: str) -> dict:
    return {
        "EID_GJI": eid,
        "GJI_TEMATIKE_SIFRA": code,
        "GJI_TEMATIKE_NAZIV_SL": theme,
        "GJI_VRSTE_OBJEKTOV_NAZIV_SL": kind,
    }


def _line_gji(
    direct: dict,
    coordinates: list[list[float]],
    *,
    attr1: str | None = None,
    attr2: str | None = None,
) -> dict:
    properties = {
        **direct,
        "EID_LINIJA": direct["EID_GJI"],
        "GJI_ATR1_NAZIV_SL": attr1,
        "GJI_ATR2_NAZIV_SL": attr2,
        "GJI_OPUSCENOSTI_NAZIV_SL": "neopuščeni objekt",
    }
    properties.pop("EID_GJI", None)
    return {
        "properties": properties,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }
