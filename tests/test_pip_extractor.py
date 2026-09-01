from app.models import PlanningContext
from app.pip_extractor import (
    PlanningTextSource,
    extract_planning_conditions,
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
