from app.pdf_parser import find_parcel_mentions, parcel_pattern


def test_parcel_pattern_has_numeric_boundaries():
    pattern = parcel_pattern("123/4")
    text = "Parcela 123/4 velja. 9123/4 and 123/45 do not match. Also 123 / 4 matches."
    assert len(pattern.findall(text)) == 2


def test_find_mentions_returns_page_and_heading_context():
    pages = [
        "4.2 DOPUSTNI POSEGI\nNa parceli št. 123/4 k.o. Vič je dopustna ureditev dostopa.",
        "VARSTVO VODA\nZa zemljišče 123 / 4 velja priobalni varovalni pas.",
    ]
    result = find_parcel_mentions(pages, "123/4")
    assert result.count == 2
    assert [item.page for item in result.excerpts] == [1, 2]
    assert result.excerpts[0].section == "4.2 DOPUSTNI POSEGI"
    assert "priobalni" in result.excerpts[1].text
