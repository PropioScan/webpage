import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import PrivacyEventRequest, SearchRequest
from app.privacy import PrivacyEventStore


def test_privacy_event_request_requires_explicit_analytics_consent():
    with pytest.raises(ValidationError):
        PrivacyEventRequest(
            event_type="parcel_search",
            parcel_reference="2057 314/4",
            analytics_consent=False,
            consent_version="1.2",
        )

    with pytest.raises(ValidationError):
        SearchRequest(
            parcel_number="2057 314/4",
            analytics_consent=True,
            consent_version="1.1",
        )


def test_privacy_event_store_keeps_only_minimal_fields_and_applies_retention(tmp_path):
    store = PrivacyEventStore(tmp_path, retention_days=90)
    now = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)

    store.record(
        visitor_id="11111111-1111-4111-8111-111111111111",
        event_type="parcel_search",
        parcel_reference="old parcel",
        consent_version="1.2",
        now=now - timedelta(days=91),
    )
    store.record(
        visitor_id="22222222-2222-4222-8222-222222222222",
        event_type="parcel_search",
        parcel_reference="2057 314/4",
        consent_version="1.2",
        now=now,
    )

    records = [json.loads(line) for line in store.path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["visitor_id"] == "22222222-2222-4222-8222-222222222222"
    assert records[0]["parcel_reference"] == "2057 314/4"
    assert set(records[0]) == {
        "recorded_at",
        "visitor_id",
        "event_type",
        "parcel_reference",
        "consent_version",
    }

    removed = store.purge(now=now + timedelta(days=91))
    assert removed == 1
    assert store.path.read_text() == ""


def test_forget_removes_only_the_selected_visitor(tmp_path):
    store = PrivacyEventStore(tmp_path, retention_days=90)
    now = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
    selected = "11111111-1111-4111-8111-111111111111"
    retained = "22222222-2222-4222-8222-222222222222"

    for visitor_id in (selected, retained):
        store.record(
            visitor_id=visitor_id,
            event_type="parcel_search",
            parcel_reference="2057 314/4",
            consent_version="1.2",
            now=now,
        )

    assert store.forget(selected) == 1
    records = [json.loads(line) for line in store.path.read_text().splitlines()]
    assert [record["visitor_id"] for record in records] == [retained]
