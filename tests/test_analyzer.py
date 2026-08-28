from app.models import Excerpt
from app.parcel_analyzer import extract_findings, extractive_summary


def test_extract_findings_flags_high_risk_topics():
    excerpts = [
        Excerpt(
            page=7, text="Za parcelo 123/4 velja poplavna omejitev in varovalni pas."
        )
    ]
    findings = extract_findings(excerpts)
    categories = {item.category: item for item in findings}
    assert categories["Poplave in vode"].importance == "high"
    assert categories["Omejitve"].pages == [7]
    assert "neposredno pojavlja 1-krat" in extractive_summary("1723 123/4", excerpts, 1)


def test_zero_mentions_explains_spatial_matching_without_a_false_not_found_warning():
    summary = extractive_summary("2057 314/4", [], 0)

    assert "prostorskega preseka PIS" in summary
    assert "ni pogoj za prostorsko povezavo" in summary
    assert "not found" not in summary
