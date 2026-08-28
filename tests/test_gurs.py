import httpx

from app.config import Settings
from app.gurs import GURSClient, parse_parcel_reference


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
