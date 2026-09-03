from pathlib import Path

import pytest

from app.ga4 import Ga4ConfigurationError, Ga4Reporter


def _report(
    dimensions: tuple[str, ...], metrics: tuple[str, ...], rows: list[tuple]
) -> dict:
    return {
        "dimensionHeaders": [{"name": name} for name in dimensions],
        "metricHeaders": [{"name": name} for name in metrics],
        "rows": [
            {
                "dimensionValues": [
                    {"value": str(value)} for value in values[: len(dimensions)]
                ],
                "metricValues": [
                    {"value": str(value)} for value in values[len(dimensions) :]
                ],
            }
            for values in rows
        ],
    }


def _responses() -> list[dict]:
    return [
        {
            "reports": [
                _report(
                    (),
                    (
                        "totalUsers",
                        "newUsers",
                        "sessions",
                        "screenPageViews",
                        "engagedSessions",
                        "averageSessionDuration",
                    ),
                    [(24, 17, 31, 62, 22, 48.5)],
                ),
                _report(
                    ("date",),
                    ("activeUsers", "sessions", "screenPageViews"),
                    [("20260830", 8, 10, 18), ("20260831", 12, 15, 29)],
                ),
                _report(
                    ("sessionDefaultChannelGroup",),
                    ("sessions", "totalUsers"),
                    [("Organic Search", 18, 14), ("Direct", 10, 8)],
                ),
                _report(
                    ("deviceCategory",),
                    ("totalUsers",),
                    [("desktop", 15), ("mobile", 9)],
                ),
                _report(
                    ("country",),
                    ("totalUsers",),
                    [("Slovenia", 22), ("Austria", 2)],
                ),
            ]
        },
        {
            "reports": [
                _report(
                    ("eventName",),
                    ("eventCount",),
                    [
                        ("parcel_analysis_completed", 11),
                        ("location_report_downloaded", 4),
                    ],
                )
            ]
        },
    ]


def test_ga4_reporter_builds_aggregate_admin_report_and_caches_it() -> None:
    responses = _responses()
    calls = []

    def post_json(url, payload, token):
        calls.append((url, payload, token))
        return responses[len(calls) - 1]

    reporter = Ga4Reporter(
        "properties/123456789",
        None,
        access_token_provider=lambda: "test-access-token",
        post_json=post_json,
    )

    result = reporter.report(30)

    assert result["summary"] == {
        "total_users": 24,
        "new_users": 17,
        "sessions": 31,
        "page_views": 62,
        "engaged_sessions": 22,
        "average_session_duration": 48.5,
    }
    assert result["daily"][1]["sessions"] == 15
    assert result["channels"][0] == {
        "channel": "Organic Search",
        "sessions": 18,
        "total_users": 14,
    }
    assert result["devices"][0]["device"] == "desktop"
    assert result["countries"][0]["country"] == "Slovenia"
    assert result["events"][0]["event"] == "parcel_analysis_completed"
    assert len(calls) == 2
    assert calls[0][0].endswith("/properties/123456789:batchRunReports")
    assert calls[0][2] == "test-access-token"
    assert len(calls[0][1]["requests"]) == 5
    assert len(calls[1][1]["requests"]) == 1

    cached = reporter.report(30)
    assert cached == result
    assert len(calls) == 2


def test_ga4_csv_export_contains_each_dashboard_section() -> None:
    responses = _responses()
    reporter = Ga4Reporter(
        "123456789",
        None,
        access_token_provider=lambda: "test-access-token",
        post_json=lambda *_: responses.pop(0),
    )

    filename, content = reporter.export_csv(7)

    assert filename == "propioscan-ga4-7-dni.csv"
    for text in (
        "Povzetek",
        "Po dnevih",
        "Viri obiska",
        "Naprave",
        "Države",
        "Dogodki Propioscan",
        "Zaključene analize",
        "Datum;Aktivni uporabniki;Seje;Ogledi strani",
        "Slovenija",
    ):
        assert text in content


def test_ga4_reporter_requires_numeric_property_and_credentials(tmp_path: Path) -> None:
    reporter = Ga4Reporter("G-MZLVQLMQ3B", tmp_path / "missing.json")

    assert reporter.configured is False
    with pytest.raises(Ga4ConfigurationError):
        reporter.report()


def test_ga4_reporter_rejects_unsupported_period() -> None:
    reporter = Ga4Reporter(
        "123456789",
        None,
        access_token_provider=lambda: "test-access-token",
    )

    with pytest.raises(ValueError):
        reporter.report(365)
