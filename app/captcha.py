from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid

import httpx

from .config import Settings


TURNSTILE_SITEVERIFY_URL = (
    "https://challenges.cloudflare.com/turnstile/v0/siteverify"
)
TURNSTILE_ACTION = "parcel_search"
HUMAN_CHECK_COOKIE = "propioscan_human_check"
HUMAN_CHECK_MAX_AGE_SECONDS = 60 * 60
TURNSTILE_TEST_SITE_KEYS = {
    "1x00000000000000000000AA",
    "2x00000000000000000000AB",
    "1x00000000000000000000BB",
    "2x00000000000000000000BB",
    "3x00000000000000000000FF",
}
TURNSTILE_TEST_SECRET_KEYS = {
    "1x0000000000000000000000000000000AA",
    "2x0000000000000000000000000000000AA",
    "3x0000000000000000000000000000000AA",
}
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "testserver"}

logger = logging.getLogger(__name__)


class CaptchaRejectedError(RuntimeError):
    """The visitor did not provide a valid, unused Turnstile token."""


class CaptchaUnavailableError(RuntimeError):
    """Turnstile is required but is not configured or cannot be reached."""


class TurnstileVerifier:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def issue_receipt(
        self,
        parcel_reference: str,
        *,
        now: int | None = None,
    ) -> str | None:
        """Create a short-lived receipt bound to one parcel after Turnstile succeeds."""

        secret = self.settings.turnstile_secret_key
        if not self.settings.captcha_required or not secret:
            return None
        expires_at = (int(time.time()) if now is None else now) + HUMAN_CHECK_MAX_AGE_SECONDS
        parcel_digest = self._parcel_digest(parcel_reference)
        unsigned = f"v1.{expires_at}.{parcel_digest}"
        signature = hmac.new(
            secret.encode("utf-8"),
            f"propioscan-human-check:{unsigned}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{unsigned}.{signature}"

    def accepts_receipt(
        self,
        receipt: str | None,
        parcel_reference: str,
        *,
        now: int | None = None,
    ) -> bool:
        """Return whether a receipt is authentic, current, and for this parcel."""

        secret = self.settings.turnstile_secret_key
        if not self.settings.captcha_required or not secret or not receipt:
            return False
        try:
            version, expires_value, parcel_digest, supplied_signature = receipt.split(
                ".", 3
            )
            expires_at = int(expires_value)
        except (TypeError, ValueError):
            return False
        checked_at = int(time.time()) if now is None else now
        if version != "v1" or expires_at < checked_at:
            return False
        if not secrets.compare_digest(
            parcel_digest, self._parcel_digest(parcel_reference)
        ):
            return False
        unsigned = f"{version}.{expires_at}.{parcel_digest}"
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            f"propioscan-human-check:{unsigned}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return secrets.compare_digest(supplied_signature, expected_signature)

    @staticmethod
    def _parcel_digest(parcel_reference: str) -> str:
        normalized = " ".join(parcel_reference.strip().casefold().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def uses_test_keys(self) -> bool:
        return bool(
            self.settings.turnstile_site_key in TURNSTILE_TEST_SITE_KEYS
            or self.settings.turnstile_secret_key in TURNSTILE_TEST_SECRET_KEYS
        )

    def verify(
        self,
        token: str | None,
        remote_ip: str | None,
        request_hostname: str | None = None,
        *,
        expected_action: str = TURNSTILE_ACTION,
    ) -> None:
        if not self.settings.captcha_required:
            return
        if not self.settings.captcha_configured:
            raise CaptchaUnavailableError("Turnstile is not configured.")
        if self.uses_test_keys and request_hostname not in LOCAL_HOSTNAMES:
            raise CaptchaUnavailableError(
                "Turnstile test keys cannot be used on a public hostname."
            )
        if not token:
            raise CaptchaRejectedError("Turnstile token is missing.")

        payload = {
            "secret": self.settings.turnstile_secret_key,
            "response": token,
            "idempotency_key": str(uuid.uuid4()),
        }
        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            response = self.client.post(TURNSTILE_SITEVERIFY_URL, json=payload)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Turnstile verification service failed: %s", exc)
            raise CaptchaUnavailableError(
                "Turnstile verification service is unavailable."
            ) from exc

        if not result.get("success"):
            error_codes = result.get("error-codes") or []
            logger.info("Turnstile rejected a search: %s", ", ".join(error_codes))
            raise CaptchaRejectedError("Turnstile rejected the token.")

        action = result.get("action")
        if action and action != expected_action:
            logger.info("Turnstile returned an unexpected action: %s", action)
            raise CaptchaRejectedError("Turnstile action did not match.")
