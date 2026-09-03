import httpx

from app.config import Settings
from app.gurs import GURSClient, classify_boundary_status, parse_parcel_reference


def test_parse_parcel_reference_accepts_slash_and_normalizes_spaces():
    reference = parse_parcel_reference(" 1723 : 123 / 4 ")
    assert reference.cadastral_municipality_id == 1723
    assert reference.parcel_number == "123/4"
    assert reference.canonical == "1723 123/4"


def test_gurs_combines_cadastre_and_valuation(settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        layer = request.url.params["typeNames"]
        if layer == "SI.GURS.KN:PARCELE":
            features = [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[1, 1], [2, 1], [2, 2], [1, 1]]],
                    },
                    "bbox": [1, 1, 2, 2],
                    "properties": {
                        "EID_PARCELA": "eid-1",
                        "NAZIV": "1723 VIČ",
                        "POVRSINA": 5119,
                        "UPRAVNI_STATUSI_NAZIV_SL": "ni urejena",
                        "E_CEN": 1.5,
                        "N_CEN": 1.5,
                    },
                }
            ]
        elif layer == "SI.GURS.KN:DALJICE":
            features = [
                {
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {
                        "EID_DALJICA": f"line-{index}",
                        "UPRAVNI_STATUSI_NAZIV_SL": "ni urejena",
                    },
                }
                for index, coordinates in enumerate(
                    (
                        [[1, 1], [2, 1]],
                        [[2, 1], [2, 2]],
                        [[2, 2], [1, 1]],
                    ),
                    start=1,
                )
            ]
        elif layer == "SI.GURS.EV:PARCELA":
            features = [
                {
                    "properties": {
                        "EID_PARCELA": "eid-1",
                        "RPE_OBCINE_NAZIV": "Ljubljana",
                    }
                }
            ]
        elif layer == "SI.GURS.EV:PARC_ENOTA":
            features = [
                {
                    "properties": {
                        "ID_MODEL": "GOZ",
                        "NAZIV_MODEL": "GOZD",
                        "DELEZ_POVRSINE": 80,
                        "POSPLOSENA_VREDNOST": 3000,
                    }
                },
                {
                    "properties": {
                        "ID_MODEL": "KME",
                        "NAZIV_MODEL": "KMETIJSKO",
                        "DELEZ_POVRSINE": 20,
                        "POSPLOSENA_VREDNOST": 700,
                    }
                },
            ]
        else:
            features = [
                {"properties": {"NAZIV_NR_PARC": "gozdna zemljišča", "DELEZ": 100}}
            ]
        return httpx.Response(200, json={"features": features})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = GURSClient(settings, http_client=http)
    parcel = client.get_parcel(parse_parcel_reference("1723 1"))
    assert parcel.information.area_m2 == 5119
    assert parcel.information.municipality == "Ljubljana"
    assert parcel.information.official_valuation_eur == 3700
    assert parcel.information.valuation_units[0].model_code == "GOZ"
    assert parcel.information.land_use[0].share_percent == 100
    assert parcel.information.boundary_assessment is not None
    assert parcel.information.boundary_assessment.label == "Ni urejena"
    assert parcel.information.boundary_assessment.total_segments == 3
    assert parcel.information.boundary_assessment.evidence_complete is True


def test_boundary_status_has_ordered_partial_and_not_ordered_classes():
    ordered = classify_boundary_status(
        raw_boundary_status="urejena",
        total_segments=4,
        ordered_segments=4,
        evidence_complete=True,
    )
    partial = classify_boundary_status(
        raw_boundary_status="ni urejena",
        total_segments=5,
        ordered_segments=2,
        evidence_complete=True,
    )
    not_ordered = classify_boundary_status(
        raw_boundary_status="ni urejena",
        total_segments=3,
        ordered_segments=0,
        evidence_complete=True,
    )

    assert ordered.status == "ordered"
    assert ordered.label == "Urejena"
    assert "4 od 4" in ordered.detail
    assert partial.status == "partially_ordered"
    assert partial.label == "Delno urejena"
    assert "2 od 5" in partial.detail
    assert not_ordered.status == "not_ordered"
    assert not_ordered.label == "Ni urejena"
