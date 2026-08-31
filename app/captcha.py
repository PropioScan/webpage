from __future__ import annotations

import logging
import uuid

import httpx

from .config import Settings


TURNSTILE_SITEVERIFY_URL = (
    "https://challenges.cloudflare.com/turnstile/v0/siteverify"
)
TURNSTILE_ACTION = "parcel_search"
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
