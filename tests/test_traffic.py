import csv
import io
from datetime import datetime, timedelta, timezone

from app.traffic import RequestFilters, TrafficStore, classify_user_agent


IPHONE_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
)


def _record(store, request_id, job_id, parcel, ip, now, visitor_id=None):
    store.record_request(
        request_id=request_id,
        job_id=job_id,
        parcel_reference=parcel,
        visitor_id=visitor_id,
        ip_address=ip,
        user_agent=IPHONE_AGENT,
        accept_language="sl-SI,sl;q=0.9",
        referer="https://propioscan.com/start?secret=no",
        analytics_consent=visitor_id is not None,
        consent_version="1.2" if visitor_id else None,
        now=now,
    )


def test_traffic_store_records_request_metadata_and_filters(tmp_path):
    store = TrafficStore(tmp_path, retention_days=30, group_secret="secret")
    now = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
    _record(store, "r1", "a" * 32, "2057 314/4", "192.0.2.1", now, "visitor-1")
    _record(store, "r2", "b" * 32, "2057 315/1", "192.0.2.2", now, None)

    result = store.query(RequestFilters(parcel="314/4", group_by="technical"))

    assert result["total"] == 1
    row = result["requests"][0]
    assert row["ip_address"] == "192.0.2.1"
    assert row["device_type"] == "mobile"
    assert row["browser_family"] == "Safari"
    assert row["os_family"] == "iOS"
    assert row["referer_host"] == "propioscan.com"
    assert row["technical_group"].startswith("T-")
    assert result["groups"][0]["request_count"] == 1


def test_csv_export_uses_selected_grouping_and_filters(tmp_path):
    store = TrafficStore(tmp_path, group_secret="secret")
    now = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
    _record(store, "r1", "a" * 32, "2057 314/4", "192.0.2.1", now)
    _record(store, "r2", "b" * 32, "2057 314/4", "192.0.2.1", now)
    _record(store, "r3", "c" * 32, "2057 999/1", "192.0.2.2", now)

    filename, content = store.export_csv(
        RequestFilters(parcel="314/4", group_by="ip")
    )
    rows = list(csv.DictReader(io.StringIO(content)))

    assert filename == "propioscan-statistika-ip.csv"
    assert rows == [
        {
            "group_by": "ip",
            "group": "192.0.2.1",
            "request_count": "2",
            "unique_ips": "1",
            "last_request": now.isoformat(),
        }
    ]

    _, detailed = store.export_csv(
        RequestFilters(parcel="314/4", group_by="none")
    )
    assert "user_agent" in detailed.splitlines()[0]
    assert "analytics_consent" in detailed.splitlines()[0]


def test_traffic_retention_and_visitor_withdrawal(tmp_path):
    store = TrafficStore(tmp_path, retention_days=30, group_secret="secret")
    now = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
    _record(
        store,
        "r1",
        "a" * 32,
        "2057 314/4",
        "192.0.2.1",
        now - timedelta(days=31),
        "visitor-1",
    )
    _record(store, "r2", "b" * 32, "2057 315/1", "192.0.2.2", now, "visitor-2")

    assert store.purge(now) == 0  # old row was already purged by the later insert
    assert store.forget_visitor("visitor-2") == 1
    row = store.query(RequestFilters(group_by="none"))["requests"][0]
    assert row["visitor_id"] is None
    assert row["analytics_consent"] == 0


def test_device_classifier_distinguishes_bot_and_desktop():
    assert classify_user_agent("Googlebot/2.1")[0] == "bot"
    assert classify_user_agent("Mozilla/5.0 (Windows NT 10.0) Firefox/120")[0] == "desktop"
