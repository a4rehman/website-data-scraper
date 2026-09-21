import json
import pytest
from pathlib import Path
from scraper.engine import UniversalScrapingEngine
from scraper.cleaners import normalize_universal_product
from scraper.exporter import export_to_csv, export_to_json, generate_quality_report
import config

def test_engine_analyze_url():
    engine = UniversalScrapingEngine()
    analysis = engine.analyze_url("https://www.temu.com/pk-en/womens-clothing-o3-28.html")
    assert analysis["platform"] == "Temu"
    assert analysis["page_type"] == "Category / Listing"
    assert analysis["requires_js"] is True

def test_universal_schema_completeness():
    raw_item = {
        "title": "Minimalist Leather Wallet",
        "price": "$45.00",
        "original_price": "$60.00",
        "image_url": "https://example.com/wallet.jpg",
        "product_url": "https://example.com/products/wallet",
        "variants": [{"size": "Standard", "color": "Brown", "price": 45}],
        "specifications": {"Material": "Full Grain Leather", "Dimensions": "4x3 inches"}
    }
    normalized = normalize_universal_product(raw_item, source_url="https://example.com/products/wallet")
    
    # Verify all 39 columns exist
    for col in config.UNIVERSAL_CSV_COLUMNS:
        assert col in normalized, f"Missing expected column in normalized schema: {col}"

    assert normalized["product_name"] == "Minimalist Leather Wallet"
    assert normalized["price"] == 45
    assert normalized["original_price"] == 60
    assert normalized["discount_percentage"] == "25%"
    assert normalized["currency"] == "USD"
    assert normalized["source_domain"] == "example.com"
    assert normalized["variant_count"] == 1

def test_export_csv_and_json(tmp_path: Path):
    test_csv = tmp_path / "test_products.csv"
    test_json = tmp_path / "test_products.json"
    test_report = tmp_path / "test_report.txt"

    sample_products = [
        normalize_universal_product({
            "title": "Item A",
            "price": "19.99",
            "product_url": "https://example.com/a"
        }),
        normalize_universal_product({
            "title": "Item B",
            "price": "29.99",
            "product_url": "https://example.com/b"
        })
    ]

    # CSV Export
    csv_ok = export_to_csv(sample_products, test_csv)
    assert csv_ok is True
    assert test_csv.exists()
    assert test_csv.stat().st_size > 50

    # JSON Export
    json_ok = export_to_json(sample_products, test_json)
    assert json_ok is True
    assert test_json.exists()
    with open(test_json, "r", encoding="utf-8") as f:
        loaded = json.load(f)
        assert len(loaded) == 2
        assert loaded[0]["product_name"] == "Item A"

    # Report Generation
    stats = {
        "url": "https://example.com",
        "platform": "Generic",
        "discovered": 2,
        "scraped": 2,
        "failed": 0,
        "duplicates_removed": 0
    }
    generate_quality_report(stats, test_report)
    assert test_report.exists()
    assert "Item" not in test_report.read_text()  # Summary only
    assert "Total Products Extracted:     2" in test_report.read_text()
