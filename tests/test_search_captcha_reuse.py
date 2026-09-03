from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.captcha import CaptchaRejectedError, HUMAN_CHECK_COOKIE
from app.models import JobStatus, JobView, SearchRequest


class FakeCaptcha:
    def __init__(self, *, receipt_valid: bool) -> None:
        self.receipt_valid = receipt_valid
        self.verified_tokens: list[str | None] = []

    def accepts_receipt(self, receipt: str | None, parcel_reference: str) -> bool:
        return self.receipt_valid and receipt == "valid-receipt" and parcel_reference == "2102 1030/15"

    def verify(self, token: str | None, *_args) -> None:
        self.verified_tokens.append(token)
        if not token:
            raise CaptchaRejectedError("missing")

    def issue_receipt(self, parcel_reference: str) -> str:
        assert parcel_reference == "2102 1030/15"
        return "new-receipt"


class FakeJobs:
    def __init__(self) -> None:
        self.force_refresh: bool | None = None

    def submit(self, parcel_reference: str, *, force_refresh: bool = False) -> JobView:
        assert parcel_reference == "2102 1030/15"
        self.force_refresh = force_refresh
        now = datetime.now(timezone.utc)
        return JobView(
            id="test-job",
            status=JobStatus.queued,
            progress=0,
            message="queued",
            parcel_number=parcel_reference,
            created_at=now,
            updated_at=now,
        )


class FakeTraffic:
    def record_request(self, **_kwargs) -> None:
        pass

    def update_job_status(self, *_args) -> None:
        pass


def search_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/search",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("propioscan.com", 443),
            "scheme": "https",
        }
    )


def install_fakes(monkeypatch, captcha: FakeCaptcha) -> FakeJobs:
    fake_jobs = FakeJobs()
    monkeypatch.setattr(main, "captcha", captcha)
    monkeypatch.setattr(main, "jobs", fake_jobs)
    monkeypatch.setattr(main, "traffic", FakeTraffic())
    return fake_jobs


def test_force_refresh_reuses_recent_human_check_for_same_parcel(monkeypatch):
    fake_captcha = FakeCaptcha(receipt_valid=True)
    fake_jobs = install_fakes(monkeypatch, fake_captcha)

    response = main.start_search(
        SearchRequest(parcel_number="2102 1030/15", force_refresh=True),
        search_request(),
        propioscan_visitor_id=None,
        human_check_receipt="valid-receipt",
    )

    assert response.status_code == 202
    assert fake_jobs.force_refresh is True
    assert fake_captcha.verified_tokens == []
    assert HUMAN_CHECK_COOKIE not in response.headers.get("set-cookie", "")


def test_force_refresh_requests_captcha_when_receipt_is_missing(monkeypatch):
    fake_captcha = FakeCaptcha(receipt_valid=False)
    fake_jobs = install_fakes(monkeypatch, fake_captcha)

    with pytest.raises(HTTPException) as raised:
        main.start_search(
            SearchRequest(parcel_number="2102 1030/15", force_refresh=True),
            search_request(),
            propioscan_visitor_id=None,
            human_check_receipt=None,
        )

    assert raised.value.status_code == 428
    assert fake_jobs.force_refresh is None


def test_successful_captcha_sets_short_lived_secure_receipt(monkeypatch):
    fake_captcha = FakeCaptcha(receipt_valid=False)
    install_fakes(monkeypatch, fake_captcha)

    response = main.start_search(
        SearchRequest(parcel_number="2102 1030/15", captcha_token="turnstile-token"),
        search_request(),
        propioscan_visitor_id=None,
        human_check_receipt=None,
    )

    cookie = response.headers["set-cookie"]
    assert fake_captcha.verified_tokens == ["turnstile-token"]
    assert f"{HUMAN_CHECK_COOKIE}=new-receipt" in cookie
    assert "Max-Age=3600" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Secure" in cookie
