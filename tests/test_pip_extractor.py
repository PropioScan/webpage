from app.models import PlanningContext
from app.pip_extractor import (
    PlanningTextSource,
    extract_planning_conditions,
    extract_preemption_right,
    is_textual_planning_document,
)


def test_extractor_returns_seventeen_named_topics_with_descriptions():
    sources = [
        PlanningTextSource(
            title="Odlok o OPN – OPN_odlok.pdf",
            url="/api/files/1/odlok.pdf",
            pages=[
                """
                1. VRSTE DOPUSTNIH DEJAVNOSTI
                Dopustne dejavnosti v območju K1 so kmetijstvo in z njim povezane storitve.
                2. DRUGI POGOJI
                """,
                """
                2.1 Funkcionalna in oblikovna merila in pogoji
                Osnovni funkcionalni in oblikovni pogoji določajo višino, lego in oblikovanje stavb.
                2.2 NASLEDNJE POGLAVJE
                """,
            ],
        )
    ]
    contexts = [
        PlanningContext(
            land_use_code="K1",
            land_use_description="Najboljša kmetijska zemljišča",
            planning_unit="SVP-03",
        )
    ]

    conditions = extract_planning_conditions(sources, contexts)

    assert len(conditions) == 17
    assert conditions[0].title == "Vrste dopustnih dejavnosti"
    assert "kmetijstvo" in conditions[0].description
    assert conditions[0].available is True
    assert conditions[0].source_url == "/api/files/1/odlok.pdf"
    assert conditions[0].pages == [1]
    functional = next(
        item
        for item in conditions
        if item.title == "Osnovni funkcionalni in oblikovni pogoji"
    )
    assert "višino, lego" in functional.description
    assert functional.pages == [2]
    assert any(not item.available for item in conditions)
    assert all(item.description for item in conditions)


def test_only_textual_planning_documents_are_selected():
    assert is_textual_planning_document(
        "tekstualni_del/1_tekstualni_del.zip::11_odlok/OPN_odlok.pdf"
    )
    assert is_textual_planning_document("OPN_spremembe_odlok.pdf")
    assert not is_textual_planning_document("grafika/list_01.pdf")


def test_kranj_il1_uses_contextual_official_eup_values_for_all_topics():
    ordinance_pages = [""] * 65
    ordinance_pages[12] = """
    (6) SK – površine podeželskih naselij:
    - vrste objektov glede na namen:
    eno in dvostanovanjske prostostoječe stavbe ter nestanovanjske kmetijske stavbe,
    stavbe spremljajočih dejavnosti, trgovske stavbe do 300 m2 in delavnice do 5 zaposlenih.
    """
    ordinance_pages[23] = """
    2.1.2 Dopustne vrste gradenj
    11. člen
    (vrste gradenj)
    Dopustne so gradnja novih objektov, rekonstrukcije, vzdrževalna dela, odstranitve in
    spremembe namembnosti v skladu z dovoljeno namembnostjo stavb v posamezni EUP.
    """
    ordinance_pages[27] = """
    2.4.1.1 Urbanistično oblikovanje - splošni pogoji
    Posegi morajo ohranjati oblikovno enovitost EUP ter se prilagoditi okoliškim objektom,
    njihovi legi, orientaciji, gradbenim masam, naklonu strešin, kritini in smerem slemen.
    """
    ordinance_pages[40] = """
    2.4.10 Pogoji za oblikovanje okolice objektov
    Višinske razlike se urejajo s travnatimi brežinami, zasaditve pa z avtohtonimi vrstami.
    Ohranjati je treba kakovostno obstoječo vegetacijo in sanirati površine po gradnji.
    2.5.1 Pogoji za oblikovanje parcel, namenjenih gradnji,
    Pri določitvi velikosti in oblike se upoštevajo tip objekta, dovoljena izraba,
    odmiki, dostopi, parkirna mesta, manipulativne in intervencijske površine.
    """
    ordinance_pages[55] = """
    2.7.3.1 Splošni pogoji
    Gradnje so dopustne, če čezmerno ne obremenjujejo okolja in ne presegajo mejnih
    vrednosti emisij; vplive je treba preprečiti oziroma omejiti z omilitvenimi ukrepi.
    """
    ordinance_pages[60] = """
    2.7.5.1 Splošni pogoji
    Načrtovanje in gradnjo je treba zasnovati tako, da se preprečijo oziroma zmanjšajo
    škodljivi vplivi naravnih nesreč ter zagotovijo požarna varnost in varni dostopi.
    """
    ordinance = PlanningTextSource(
        title="MOK – Neuradno prečiščeno besedilo Odloka o IPN",
        url="https://www.kranj.si/neuradno-precisceno-besedilo.pdf",
        pages=ordinance_pages,
    )
    annex = PlanningTextSource(
        title="MOK – Priloga 1: Preglednica enot urejanja prostora",
        url=(
            "https://prostor.kranj.si/prostorski-akti/datoteke/3/x.pdf/"
            "Ipn_mok_odlok_priloga_1_preglednica_20EUP.pdf"
        ),
        pages=[
            "",
            "",
            "\n".join(
                [
                    "                                                                                    za stan. hiše =",
                    "                            IL 1                      SK                  /        0,35, za kmetije          25%                K+P+1               gručasta zazidava                 PIP                      /",
                    " ILOVKA                                                                                 = 0,40",
                ]
            ),
        ],
    )
    contexts = [
        PlanningContext(
            land_use_code="SK",
            land_use_description="Stanovanjske površine s kmetijsko dejavnostjo",
            planning_unit="IL 1",
        )
    ]

    conditions = extract_planning_conditions([ordinance, annex], contexts)

    assert len(conditions) == 17
    assert all(condition.available for condition in conditions)
    by_key = {condition.key: condition for condition in conditions}
    assert "0,35" in by_key["utilization"].description
    assert "0,40" in by_key["utilization"].description
    assert "25%" in by_key["utilization"].description
    assert "K+P+1" in by_key["size"].description
    assert "gručasta zazidava" in by_key["development_type"].description
    assert "Način urejanja: PIP" in by_key["utilization"].description
    assert by_key["construction"].pages == [24]
    assert "rekonstrukcije" in by_key["construction"].description
    assert all("KR P17/2" not in item.description for item in conditions)


def test_preemption_right_is_extracted_with_source_page_and_parcel_context():
    source = PlanningTextSource(
        title="Odlok o OPN – odlok.pdf",
        url="/api/files/1/odlok.pdf",
        pages=[
            """
            42. člen
            Občina uveljavlja predkupno pravico na vseh stavbnih zemljiščih.
            Določba velja tudi za območje enote urejanja prostora SVP-03.
            """
        ],
    )
    contexts = [
        PlanningContext(
            land_use_code="SK",
            land_use_description="Površine podeželskega naselja",
            planning_unit="SVP-03",
        )
    ]

    result = extract_preemption_right([source], contexts)

    assert result.status == "provision_found"
    assert result.source_title == source.title
    assert result.source_url == source.url
    assert result.pages == [1]
    assert result.checked_document_count == 1
    assert "Občina uveljavlja predkupno pravico" in (result.excerpt or "")
    assert "svp-03" in result.detail.casefold()
    assert "ne dokazuje" in result.detail


def test_preemption_right_absence_is_not_reported_as_proof_of_no_right():
    source = PlanningTextSource(
        title="Odlok o OPN – odlok.pdf",
        url="/api/files/1/odlok.pdf",
        pages=["Namenska raba prostora in splošni izvedbeni pogoji."],
    )

    result = extract_preemption_right([source], [])

    assert result.status == "not_found"
    assert result.checked_document_count == 1
    assert "ne izključuje" in result.detail


def test_preemption_right_reports_when_opn_text_is_unavailable():
    result = extract_preemption_right([], [])

    assert result.status == "unavailable"
    assert result.checked_document_count == 0
    assert "ni bil na voljo" in result.detail


def test_gorenja_vas_building_land_uses_the_official_municipal_ordinance():
    contexts = [
        PlanningContext(
            land_use_code="K1",
            land_use_description="Najboljša kmetijska zemljišča",
            parcel_share_percent=60.7,
        ),
        PlanningContext(
            land_use_code="A",
            land_use_description="Površine razpršene poselitve",
            planning_unit="SVP-03",
            parcel_share_percent=39.3,
        ),
    ]

    result = extract_preemption_right([], contexts, "Gorenja vas-Poljane")

    assert result.status == "applies"
    assert "Parcela posega" in result.label
    assert "A – Površine razpršene poselitve (39.3 % parcele)" in result.detail
    assert "67/2021" in result.legal_basis
    assert result.source_url and "uradni-list.si" in result.source_url
    assert "2. in 3. člen" in (result.excerpt or "")
