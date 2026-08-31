from datetime import datetime, timedelta, timezone

import pytest

from app.admin import (
    AdminAuth,
    AdminAuthenticationError,
    AdminLogReader,
    AdminRateLimitError,
    hash_password,
    verify_password,
)
from app.traffic import TrafficStore


def _auth(tmp_path, *, max_attempts=5):
    traffic = TrafficStore(tmp_path, group_secret="group-secret")
    return AdminAuth(
        username="skrbnik",
        password_hash=hash_password("A-very-long-test-password", salt=b"0" * 16),
        session_secret="s" * 48,
        traffic=traffic,
        max_attempts=max_attempts,
    )


def test_admin_password_is_scrypt_hashed_and_never_embeds_plaintext():
    encoded = hash_password("A-very-long-test-password", salt=b"1" * 16)

    assert encoded.startswith("scrypt$")
    assert "A-very-long-test-password" not in encoded
    assert verify_password("A-very-long-test-password", encoded)
    assert not verify_password("wrong password", encoded)


def test_admin_session_rejects_tampering_and_expiration(tmp_path):
    auth = _auth(tmp_path)
    now = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
    token = auth.authenticate(
        username="skrbnik",
        password="A-very-long-test-password",
        ip_address="192.0.2.10",
        now=now,
    )

    assert auth.verify_session(token, now=now)["sub"] == "skrbnik"
    with pytest.raises(AdminAuthenticationError):
        auth.verify_session(token + "x", now=now)
    with pytest.raises(AdminAuthenticationError):
        auth.verify_session(token, now=now + timedelta(hours=9))


def test_admin_failed_logins_are_rate_limited(tmp_path):
    auth = _auth(tmp_path, max_attempts=3)
    now = datetime(2026, 8, 31, 8, tzinfo=timezone.utc)
    for _ in range(3):
        with pytest.raises(AdminAuthenticationError):
            auth.authenticate(
                username="skrbnik",
                password="wrong password",
                ip_address="192.0.2.10",
                now=now,
            )

    with pytest.raises(AdminRateLimitError):
        auth.authenticate(
            username="skrbnik",
            password="A-very-long-test-password",
            ip_address="192.0.2.10",
            now=now,
        )


def test_admin_log_reader_redacts_secrets(tmp_path):
    (tmp_path / "stderr.log").write_text(
        "OPENAI_API_KEY=" "sk-proj-" "supersecretvalue\n"
        "password=hunter2\nnormal line\n",
        encoding="utf-8",
    )
    reader = AdminLogReader(tmp_path, tmp_path / "data")

    result = reader.tail("application")

    joined = "\n".join(result["lines"])
    assert "supersecretvalue" not in joined
    assert "hunter2" not in joined
    assert "normal line" in joined
    assert "<redacted>" in joined
