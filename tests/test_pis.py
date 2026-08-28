import httpx

from app.config import Settings
from app.gurs import GURSParcel
from app.models import ParcelInformation
from app.pis import PISClient, geometry_to_wkt


def parcel() -> GURSParcel:
    geometry = {"type": "Polygon", "coordinates": [[[1, 1], [2, 1], [2, 2], [1, 1]]]}
    return GURSParcel(
        information=ParcelInformation(
            parcel_number="1", cadastral_municipality_id=1723
        ),
        geometry=geometry,
        bbox=[1, 1, 2, 2],
        eid="eid",
    )


def test_geometry_to_wkt():
    assert (
        geometry_to_wkt(parcel().geometry, parcel().bbox)
        == "POLYGON((1.000 1.000,2.000 1.000,2.000 2.000,1.000 1.000))"
    )


def test_pis_deduplicates_acts_and_reads_context(settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        layer = request.url.params["typeNames"]
        assert "INTERSECTS(GEOM,POLYGON" in request.url.params["CQL_FILTER"]
        if layer.endswith("OBM_PA_ZAKLJUCENI"):
            features = [
                {
                    "properties": {
                        "PO_EPL_ID": 256181,
                        "ID_PA": 1563,
                        "NAZIV_AKTA": "Fourth OPN update",
                        "STATUS": "VELJAVEN",
                        "VRSTAPA_OP": "Občinski prostorski načrt",
                        "URL": "https://pis.example/act",
                    }
                }
            ]
        elif layer.endswith("OBM_PA_V_PRIPRAVI"):
            features = [{"properties": {"PO_EPL_ID": 256181, "NAZIV_PA": "Duplicate"}}]
        else:
            features = [
                {
                    "properties": {
                        "PO_EPL_ID": 256181,
                        "NAZIV_PA": "Fourth OPN update",
                        "NRP_OZN": "G",
                        "NRP_OPIS": "Gozdna zemljišča",
                        "EUP_OZN": "RŽ-138",
                        "PEUP_OZN": None,
                    }
                }
            ]
        return httpx.Response(200, json={"features": features})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = PISClient(settings, http_client=http)
    acts = client.find_acts(parcel())
    contexts = client.planning_context(parcel())
    assert len(acts) == 1
    assert acts[0].title == "Fourth OPN update"
    assert contexts[0].land_use_code == "G"
    assert contexts[0].planning_unit == "RŽ-138"


def test_planning_context_uses_actual_overlap_and_skips_boundary_touch(
    settings: Settings,
):
    test_parcel = GURSParcel(
        information=ParcelInformation(
            parcel_number="1", cadastral_municipality_id=1723
        ),
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        },
        bbox=[0, 0, 10, 10],
        eid="eid",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "PO_EPL_ID": 1,
                            "NRP_OZN": "SS",
                            "NRP_OPIS": "Stanovanjske površine",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]]
                            ],
                        },
                    },
                    {
                        "properties": {
                            "PO_EPL_ID": 1,
                            "NRP_OZN": "K1",
                            "NRP_OPIS": "Kmetijska zemljišča",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[10, 0], [12, 0], [12, 10], [10, 10], [10, 0]]
                            ],
                        },
                    },
                ]
            },
        )

    client = PISClient(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    contexts = client.planning_context(test_parcel)

    assert len(contexts) == 1
    assert contexts[0].land_use_code == "SS"
    assert contexts[0].parcel_share_percent == 50.0
