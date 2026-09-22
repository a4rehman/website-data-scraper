import pytest
from scraper.ai_processor import (
    parse_ai_json_response,
    validate_summary_schema,
    summarize_scraped_data_with_ai,
    MAX_INPUT_CHARS
)

def test_parse_ai_json_response_markdown_block():
    markdown_json = """Here is the catalog analysis:
```json
{
  "title": "Summer Collection",
  "business_type": "Women's Fashion",
  "summary": "Extracted 15 lawn suits.",
  "key_highlights": ["Pure Cotton", "Embroidered"],
  "product_categories": ["Unstitched", "Pret"]
}
```
Hope this helps!"""

    parsed = parse_ai_json_response(markdown_json)
    assert parsed is not None
    assert parsed["title"] == "Summer Collection"
    assert parsed["business_type"] == "Women's Fashion"
    assert len(parsed["key_highlights"]) == 2

def test_parse_ai_json_response_raw_json():
    raw_json = '{"title": "Test Store", "summary": "Quick summary"}'
    parsed = parse_ai_json_response(raw_json)
    assert parsed is not None
    assert parsed["title"] == "Test Store"

def test_validate_summary_schema_missing_keys():
    incomplete_data = {"summary": "Partial response without title"}
    validated = validate_summary_schema(incomplete_data)
    assert "title" in validated
    assert "business_type" in validated
    assert "key_highlights" in validated
    assert isinstance(validated["key_highlights"], list)

def test_summarize_scraped_data_fallback_without_key():
    sample_text = "Title: Lawn Suit 1 | Price: 3990 | Brand: Tehzeeb\nTitle: Lawn Suit 2 | Price: 4990"
    res = summarize_scraped_data_with_ai(sample_text, api_key="")
    assert res["success"] is True
    assert "data" in res
    assert res["data"]["title"] != ""
