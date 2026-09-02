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
