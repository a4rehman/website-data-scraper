import pytest
from scraper.engine import UniversalScrapingEngine

def test_engine_handles_empty_and_invalid_urls():
    engine = UniversalScrapingEngine()
    
    # Empty URL
    res = engine.analyze_url("")
    assert "error" in res

    # Invalid scheme / SSRF blocked
    res = engine.analyze_url("file:///etc/passwd")
    assert "error" in res
    assert "Security" in res["page_type"] or "Blocked" in str(res.get("error"))

    # Localhost blocked
    res = engine.analyze_url("http://127.0.0.1:8000")
    assert "error" in res

def test_engine_scrape_url_blocked_ssrf():
    engine = UniversalScrapingEngine()
    res = engine.scrape_url("http://localhost:9000/secret")
    assert res["success"] is False
    assert "Blocked" in res["message"] or "Validation Failed" in res["message"]

def test_engine_scrape_unreachable_domain():
    engine = UniversalScrapingEngine()
    res = engine.scrape_url("https://thisdomaindoesnotexistatall123456789.com")
    assert res["success"] is False
    assert "Unable to discover" in res["message"] or "Could not resolve" in res["message"]
