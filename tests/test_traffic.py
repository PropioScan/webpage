import csv
import io
import json
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

    filename, content = store.export_csv(RequestFilters(parcel="314/4", group_by="ip"))
    rows = list(csv.DictReader(io.StringIO(content), delimiter=";"))

    assert filename == "propioscan-statistika-ip.csv"
    assert rows == [
        {
            "Združeno po": "IP naslov",
            "Skupina": "192.0.2.1",
            "Število zahtev": "2",
            "Različni IP-ji": "1",
            "Zadnja zahteva": now.isoformat(),
        }
    ]

    _, detailed = store.export_csv(RequestFilters(parcel="314/4", group_by="none"))
    assert "Uporabniški agent" in detailed.splitlines()[0]
    assert "Analitično soglasje" in detailed.splitlines()[0]
    assert "Telefon" in detailed


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
    assert (
        classify_user_agent("Mozilla/5.0 (Windows NT 10.0) Firefox/120")[0] == "desktop"
    )


def test_openai_usage_is_synced_from_jobs_aggregated_and_exported(tmp_path):
    store = TrafficStore(tmp_path, retention_days=30, group_secret="secret")
    now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    jobs = (
        (
            "a" * 32,
            now,
            "2057 314/4",
            {
                "configured": True,
                "model": "gpt-test",
                "calls": 2,
                "input_tokens": 1200,
                "output_tokens": 180,
                "total_tokens": 1380,
                "rate_limit_tokens": 5000,
                "rate_limit_remaining_tokens": 3620,
                "rate_limit_reset": "2s",
            },
            [],
        ),
        (
            "b" * 32,
            now - timedelta(days=1),
            "2057 315/1",
            {
                "configured": True,
                "model": "gpt-test",
                "calls": 1,
                "input_tokens": 600,
                "output_tokens": 90,
                "total_tokens": 690,
            },
            [
                "Povzetka z umetno inteligenco ni bilo mogoče pripraviti "
                "(RateLimitError); prikazan je samodejni izvleček."
            ],
        ),
    )
    for index, (job_id, timestamp, parcel, usage, warnings) in enumerate(jobs, start=1):
        _record(store, f"r{index}", job_id, parcel, f"192.0.2.{index}", timestamp)
        (jobs_dir / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "id": job_id,
                    "status": "completed",
                    "updated_at": timestamp.isoformat(),
                    "result": {
                        "openai_usage": usage,
                        "documents": [{"extraction_warnings": warnings}],
                    },
                }
            ),
            encoding="utf-8",
        )

    store.refresh_job_statuses(jobs_dir)
    report = store.openai_usage_report(7, now=now)

    assert report["summary"] == {
        "completed_analyses": 2,
        "configured_analyses": 2,
        "ai_analyses": 2,
        "calls": 3,
        "input_tokens": 1800,
        "output_tokens": 270,
        "total_tokens": 2070,
        "failures": 1,
    }
    assert report["models"] == [
        {
            "model": "gpt-test",
            "analyses": 2,
            "calls": 3,
            "input_tokens": 1800,
            "output_tokens": 270,
            "total_tokens": 2070,
        }
    ]
    assert report["latest_rate_limit"]["remaining_tokens"] == 3620
    assert len(report["daily"]) == 2
    assert report["recent"][0]["parcel_reference"] == "2057 314/4"

    filename, content = store.export_openai_usage_csv(7, now=now)
    assert filename == "propioscan-openai-7-dni.csv"
    assert "Vhodni tokeni;1800" in content
    assert "Čas zahteve;Parcela;ID opravila" in content
    assert "2057 314/4" in content


def test_cached_result_does_not_duplicate_historical_openai_usage(tmp_path):
    store = TrafficStore(tmp_path, retention_days=30, group_secret="secret")
    now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_id = "c" * 32
    _record(store, "cached-request", job_id, "2057 314/4", "192.0.2.3", now)
    (jobs_dir / f"{job_id}.json").write_text(
        json.dumps(
            {
                "id": job_id,
                "status": "completed",
                "updated_at": now.isoformat(),
                "from_cache": True,
                "result": {
                    "openai_usage": {
                        "configured": True,
                        "model": "gpt-test",
                        "calls": 4,
                        "input_tokens": 2000,
                        "output_tokens": 300,
                        "total_tokens": 2300,
                    },
                    "documents": [
                        {
                            "extraction_warnings": [
                                "Povzetka z umetno inteligenco ni bilo mogoče pripraviti."
                            ]
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    store.refresh_job_statuses(jobs_dir)
    summary = store.openai_usage_report(7, now=now)["summary"]

    assert summary["completed_analyses"] == 1
    assert summary["configured_analyses"] == 0
    assert summary["ai_analyses"] == 0
    assert summary["calls"] == 0
    assert summary["total_tokens"] == 0
    assert summary["failures"] == 0
