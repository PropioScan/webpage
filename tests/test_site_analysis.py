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
