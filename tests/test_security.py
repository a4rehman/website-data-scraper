import pytest
from scraper.utils import validate_safe_url

def test_validate_safe_url_valid_public_urls():
    valid_urls = [
        "https://tehzeeblibas.com/collections/all",
        "https://www.temu.com/pk-en/womens-clothing-o3-28.html",
        "http://example.com/products/item1",
        "https://woocommerce.com/products/",
    ]
    for url in valid_urls:
        is_safe, reason = validate_safe_url(url)
        assert is_safe is True, f"Expected safe URL: {url}, but got: {reason}"

def test_validate_safe_url_blocks_ssrf_localhost():
    blocked_urls = [
        "http://localhost",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8080/admin",
        "http://0.0.0.0",
    ]
    for url in blocked_urls:
        is_safe, reason = validate_safe_url(url)
        assert is_safe is False, f"Expected URL to be blocked for SSRF: {url}"
        assert "private" in reason.lower() or "internal" in reason.lower()

def test_validate_safe_url_blocks_private_ip_ranges():
    private_urls = [
        "http://10.0.0.1/secrets",
        "http://192.168.1.1/router",
        "http://172.16.0.1/internal",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata endpoint
    ]
    for url in private_urls:
        is_safe, reason = validate_safe_url(url)
        assert is_safe is False, f"Expected private IP to be blocked: {url}"

def test_validate_safe_url_blocks_unsupported_schemes():
    invalid_schemes = [
        "file:///etc/passwd",
        "file:///C:/Windows/System32",
        "gopher://example.com",
        "ftp://example.com/file",
    ]
    for url in invalid_schemes:
        is_safe, reason = validate_safe_url(url)
        assert is_safe is False, f"Expected scheme to be blocked: {url}"

def test_validate_safe_url_empty_or_invalid():
    is_safe, reason = validate_safe_url("")
    assert is_safe is False
    is_safe, reason = validate_safe_url(None)
    assert is_safe is False
