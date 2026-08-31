from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .traffic import TrafficStore, normalize_ip


ADMIN_COOKIE = "propioscan_admin_session"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


class AdminConfigurationError(RuntimeError):
    """The production-only admin credentials are incomplete."""


class AdminRateLimitError(RuntimeError):
    """Too many failed admin login attempts were recorded."""


class AdminAuthenticationError(RuntimeError):
    """The supplied admin credentials or session are invalid."""


class AdminAuth:
    def __init__(
        self,
        *,
        username: str | None,
        password_hash: str | None,
        session_secret: str | None,
        traffic: TrafficStore,
        session_hours: int = 8,
        max_attempts: int = 5,
        attempt_window_minutes: int = 15,
    ) -> None:
        self.username = username or ""
        self.password_hash = password_hash or ""
        self.session_secret = (session_secret or "").encode("utf-8")
        self.traffic = traffic
        self.session_lifetime = timedelta(hours=max(1, min(session_hours, 24)))
        self.max_attempts = max(3, min(max_attempts, 20))
        self.attempt_window = timedelta(
            minutes=max(5, min(attempt_window_minutes, 60))
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.username
            and self.password_hash.startswith("scrypt$")
            and len(self.session_secret) >= 32
        )

    def authenticate(
        self,
        *,
        username: str,
        password: str,
        ip_address: str | None,
        now: datetime | None = None,
    ) -> str:
        checked_at = _utc(now)
        if not self.configured:
            raise AdminConfigurationError("Admin access is not configured.")
        failures = self.traffic.failed_login_count(
            ip_address=ip_address,
            username=username,
            since=checked_at - self.attempt_window,
        )
        if failures >= self.max_attempts:
            raise AdminRateLimitError("Too many failed login attempts.")

        username_ok = hmac.compare_digest(username.casefold(), self.username.casefold())
        password_ok = verify_password(password, self.password_hash)
        successful = username_ok and password_ok
        self.traffic.record_login_attempt(
            ip_address=ip_address,
            username=username,
            successful=successful,
            now=checked_at,
        )
        if not successful:
            raise AdminAuthenticationError("Invalid credentials.")
        return self.create_session(self.username, now=checked_at)

    def create_session(self, username: str, *, now: datetime | None = None) -> str:
        issued_at = _utc(now)
        payload = {
            "sub": username,
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + self.session_lifetime).timestamp()),
            "nonce": secrets.token_urlsafe(16),
        }
        encoded = _base64url(json.dumps(payload, separators=(",", ":")).encode())
        signature = _base64url(hmac.new(self.session_secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify_session(
        self, token: str | None, *, now: datetime | None = None
    ) -> dict[str, Any]:
        if not self.configured or not token or token.count(".") != 1:
            raise AdminAuthenticationError("Missing admin session.")
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = _base64url(
            hmac.new(self.session_secret, encoded.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise AdminAuthenticationError("Invalid admin session.")
        try:
            payload = json.loads(_base64url_decode(encoded))
            expires = int(payload["exp"])
            subject = str(payload["sub"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AdminAuthenticationError("Invalid admin session.") from exc
        if expires <= int(_utc(now).timestamp()):
            raise AdminAuthenticationError("Expired admin session.")
        if not hmac.compare_digest(subject.casefold(), self.username.casefold()):
            raise AdminAuthenticationError("Invalid admin session subject.")
        return payload


class AdminLogReader:
    def __init__(self, base_dir: Path, data_dir: Path) -> None:
        self.sources = {
            "application": ("Aplikacija / LiteSpeed", base_dir / "stderr.log"),
            "jobs": ("Analize parcel", data_dir / "logs" / "job-worker.log"),
            "passenger": ("Passenger", base_dir / "passenger.log"),
        }

    def available_sources(self) -> list[dict[str, str | bool]]:
        return [
            {"key": key, "label": label, "available": path.is_file()}
            for key, (label, path) in self.sources.items()
        ]

    def tail(self, source: str, lines: int = 300) -> dict[str, Any]:
        if source not in self.sources:
            source = "application"
        label, path = self.sources[source]
        limit = max(20, min(lines, 1000))
        if not path.is_file():
            return {
                "source": source,
                "label": label,
                "available": False,
                "modified_at": None,
                "lines": [],
            }
        content = _tail_bytes(path, max_bytes=1024 * 1024)
        redacted = [_redact_log_line(line) for line in content.splitlines()[-limit:]]
        return {
            "source": source,
            "label": label,
            "available": True,
            "modified_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat(),
            "lines": redacted,
        }


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Admin password must contain at least 12 characters.")
    password_salt = salt or os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=password_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            _base64url(password_salt),
            _base64url(digest),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        parameters = (int(n), int(r), int(p))
        if parameters != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_base64url_decode(salt),
            n=parameters[0],
            r=parameters[1],
            p=parameters[2],
            dklen=SCRYPT_LENGTH,
        )
        return hmac.compare_digest(actual, _base64url_decode(expected))
    except (ValueError, TypeError):
        return False


def request_ip(client_host: str | None) -> str:
    return normalize_ip(client_host)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _tail_bytes(path: Path, *, max_bytes: int) -> str:
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - max_bytes))
        return source.read(max_bytes).decode("utf-8", errors="replace")


def _redact_log_line(line: str) -> str:
    result = line[:8000]
    patterns = (
        (r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}", "sk-<redacted>"),
        (
            r"(?i)((?:authorization|api[_-]?key|secret|password|captcha_token|token)\s*[=:]\s*)[^\s&,;]+",
            r"\1<redacted>",
        ),
        (r"(?i)(response=)[^&\s]+", r"\1<redacted>"),
    )
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)
    return result
