from dataclasses import replace

import httpx
import pytest

from app.captcha import (
    HUMAN_CHECK_MAX_AGE_SECONDS,
    TURNSTILE_SITEVERIFY_URL,
    CaptchaRejectedError,
    CaptchaUnavailableError,
    TurnstileVerifier,
)


def captcha_settings(settings):
    return replace(
        settings,
        captcha_required=True,
        turnstile_site_key="test-site-key",
        turnstile_secret_key="test-secret-key",
    )


def test_turnstile_sends_the_token_to_the_fixed_siteverify_endpoint(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TURNSTILE_SITEVERIFY_URL
        payload = __import__("json").loads(request.content)
        assert payload["secret"] == "test-secret-key"
        assert payload["response"] == "verified-token"
        assert payload["remoteip"] == "127.0.0.1"
        return httpx.Response(200, json={"success": True, "action": "parcel_search"})

    verifier = TurnstileVerifier(
        captcha_settings(settings),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    verifier.verify("verified-token", "127.0.0.1")


def test_turnstile_rejects_missing_invalid_and_wrong_action_tokens(settings):
    configured = captcha_settings(settings)
    verifier = TurnstileVerifier(
        configured,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]})
            )
        ),
    )

    with pytest.raises(CaptchaRejectedError):
        verifier.verify(None, None)
    with pytest.raises(CaptchaRejectedError):
        verifier.verify("invalid", None)

    wrong_action = TurnstileVerifier(
        configured,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"success": True, "action": "other_action"})
            )
        ),
    )
    with pytest.raises(CaptchaRejectedError):
        wrong_action.verify("valid-but-wrong-action", None)


def test_turnstile_fails_closed_when_required_but_unconfigured(settings):
    verifier = TurnstileVerifier(replace(settings, captcha_required=True))

    with pytest.raises(CaptchaUnavailableError):
        verifier.verify("token", None)


def test_turnstile_test_keys_are_refused_on_a_public_hostname(settings):
    test_key_settings = replace(
        settings,
        captcha_required=True,
        turnstile_site_key="3x00000000000000000000FF",
        turnstile_secret_key="1x0000000000000000000000000000000AA",
    )
    verifier = TurnstileVerifier(test_key_settings)

    try:
        with pytest.raises(CaptchaUnavailableError):
            verifier.verify("XXXX.DUMMY.TOKEN.XXXX", "203.0.113.2", "propioscan.com")
    finally:
        verifier.close()


def test_human_check_receipt_is_short_lived_and_bound_to_one_parcel(settings):
    verifier = TurnstileVerifier(captcha_settings(settings))
    try:
        receipt = verifier.issue_receipt("2102 1030/15", now=1_000)

        assert receipt
        assert verifier.accepts_receipt(receipt, " 2102   1030/15 ", now=1_001)
        assert not verifier.accepts_receipt(receipt, "2057 314/4", now=1_001)
        assert not verifier.accepts_receipt(
            receipt,
            "2102 1030/15",
            now=1_000 + HUMAN_CHECK_MAX_AGE_SECONDS + 1,
        )
        replacement = "0" if receipt[-1] != "0" else "1"
        assert not verifier.accepts_receipt(
            f"{receipt[:-1]}{replacement}", "2102 1030/15", now=1_001
        )
    finally:
        verifier.close()
