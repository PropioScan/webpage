from dataclasses import replace


def test_google_analytics_measurement_id_is_strictly_validated(settings):
    assert replace(
        settings, google_analytics_measurement_id="G-ABC123XYZ9"
    ).google_analytics_configured
    assert not replace(
        settings, google_analytics_measurement_id="UA-123456-1"
    ).google_analytics_configured
    assert not replace(
        settings,
        google_analytics_measurement_id='G-ABC123"><script>alert(1)</script>',
    ).google_analytics_configured
