from types import SimpleNamespace

from app.ai_summary import ParcelSummarizer


def test_openai_usage_accumulates_and_keeps_latest_rate_limit(settings):
    summarizer = ParcelSummarizer(settings)
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150)
    )

    summarizer._record_usage(
        response,
        {
            "x-ratelimit-limit-tokens": "5000",
            "x-ratelimit-remaining-tokens": "4850",
            "x-ratelimit-reset-tokens": "2s",
        },
    )
    summarizer._record_usage(
        response,
        {
            "x-ratelimit-limit-tokens": "5000",
            "x-ratelimit-remaining-tokens": "4700",
            "x-ratelimit-reset-tokens": "2s",
        },
    )

    assert summarizer.usage.calls == 2
    assert summarizer.usage.input_tokens == 240
    assert summarizer.usage.output_tokens == 60
    assert summarizer.usage.total_tokens == 300
    assert summarizer.usage.rate_limit_tokens == 5000
    assert summarizer.usage.rate_limit_remaining_tokens == 4700
    assert summarizer.usage.rate_limit_reset == "2s"
