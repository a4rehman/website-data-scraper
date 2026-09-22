import pytest
from scraper.cleaners import (
    clean_html_description,
    normalize_price,
    detect_currency,
    normalize_universal_product,
    calculate_discount_percentage,
)

def test_clean_html_description_decomposes_noise_tags():
    html_with_noise = """
    <div>
        <script>var x = 123; alert('hack');</script>
        <style>body { color: red; }</style>
        <nav><a href="/home">Home Menu</a></nav>
        <p>This is high quality 100% Cotton Lawn suit.</p>
        <ul>
            <li>Size: Medium</li>
            <li>Color: Navy Blue</li>
        </ul>
        <footer>Copyright 2026 Store Inc</footer>
        <svg><path d="M0 0h10v10H0z"/></svg>
        <iframe></iframe>
    </div>
    """
    cleaned = clean_html_description(html_with_noise)
    assert "alert" not in cleaned
    assert "color: red" not in cleaned
    assert "Home Menu" not in cleaned
    assert "Copyright" not in cleaned
    assert "Cotton Lawn suit" in cleaned
    assert "Size: Medium" in cleaned
    assert "Color: Navy Blue" in cleaned

def test_clean_html_description_preserves_unicode_urdu():
    urdu_html = "<p>یہ ایک خوبصورت کڑھائی والا سوٹ ہے</p><ul><li>سائز: لارج</li></ul>"
    cleaned = clean_html_description(urdu_html)
    assert "یہ ایک خوبصورت کڑھائی والا سوٹ ہے" in cleaned
    assert "سائز: لارج" in cleaned

def test_normalize_price_various_formats():
    assert normalize_price("Rs. 5,999") == 5999
    assert normalize_price("$14.99") == 14.99
    assert normalize_price("USD 120.00") == 120
    assert normalize_price("14.99 - 25.99") == 14.99
    assert normalize_price(None) is None
    assert normalize_price("Free") is None

def test_detect_currency():
    assert detect_currency("Rs. 4,500") == "PKR"
    assert detect_currency("$99.00") == "USD"
    assert detect_currency("£45.50") == "GBP"
    assert detect_currency("€30.00") == "EUR"
    assert detect_currency("No price") == ""

def test_calculate_discount_percentage():
    assert calculate_discount_percentage(10000, 7500) == "25%"
    assert calculate_discount_percentage(5000, 5000) == ""
    assert calculate_discount_percentage(None, 4000) == ""
