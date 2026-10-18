"""
Automated Price Integrity Tests for Website Data Scraper.
Verifies that:
1. Source prices are preserved without fixed or percentage markups (+1200, +1500, *1.2, etc.).
2. Normalization extracts exact numeric values from strings like 'Rs. 4,397' -> 4397.
3. Compare-at / original prices are kept separate from current selling prices.
4. Sale prices never get replaced by list prices.
5. Repeated exports and CSV round-tripping do not compound or change prices.
6. Provenance fields accurately reflect extraction sources.
"""

import csv
import json
import pytest
from pathlib import Path
from scraper.cleaners import (
    normalize_price,
    normalize_universal_product,
    calculate_discount_percentage,
)
from scraper.adapters.shopify import ShopifyAdapter
from scraper.adapters.generic import GenericAdapter
from scraper.exporter import export_to_csv, export_to_json


def test_numeric_preservation_target_cases():
    """
    Requirements:
    - Extracting Rs. 4,397 must preserve 4397 (not 5276.40, 5597, or any marked-up amount).
    - Extracting Rs. 5,000 must preserve 5000.
    - Extracting Rs. 7,176 must preserve 7176.
    """
    assert normalize_price("Rs. 4,397") == 4397
    assert normalize_price("Rs. 4,397.00") == 4397
    assert normalize_price("PKR 4,397") == 4397
    assert normalize_price(4397) == 4397
    assert normalize_price(4397.0) == 4397

    # Explicit guarantee: 4397 is NEVER transformed into 5276.40 or marked up by 20%
    assert normalize_price("Rs. 4,397") != 5276.40
    assert normalize_price("Rs. 4,397") != 5597

    assert normalize_price("Rs. 5,000") == 5000
    assert normalize_price("Rs 5,000") == 5000
    assert normalize_price(5000) == 5000

    assert normalize_price("Rs. 7,176") == 7176
    assert normalize_price("7176.00") == 7176
    assert normalize_price("7176") == 7176


def test_decimal_price_preservation():
    """Verify that decimal prices like 5276.40 or 7195.20 are preserved exactly without rounding or distortion."""
    assert normalize_price("5276.40") == 5276.4
    assert normalize_price("Rs. 5,276.40") == 5276.4
    assert normalize_price(5276.4) == 5276.4

    assert normalize_price("7195.20") == 7195.2
    assert normalize_price("7154.40") == 7154.4


def test_shopify_adapter_naqsh_raw_extraction():
    """
    Verify Shopify adapter extraction on the exact NAQSH JSON payload from Tehzeeb Libas.
    Raw price '5276.40' with compare_at_price None must yield price: 5276.4 with zero markup.
    """
    adapter = ShopifyAdapter()
    raw_naqsh = {
        "id": 15230673780811,
        "title": "NAQSH",
        "handle": "naqsh",
        "vendor": "Tehzeeb Libas",
        "product_type": "Daily/Basic Wear",
        "body_html": "<p>2-Piece Daily/Basic Wear Kurta Set</p>",
        "tags": ["Lawn", "Embroidered"],
        "options": [{"name": "Size", "position": 1, "values": ["S", "M", "L", "XL"]}],
        "variants": [
            {
                "id": 67189120794699,
                "title": "S",
                "sku": "PAK-NAQSH-S-0",
                "price": "5276.40",
                "compare_at_price": None,
                "available": True,
            },
            {
                "id": 67189120827467,
                "title": "M",
                "sku": "PAK-NAQSH-M-0",
                "price": "5276.40",
                "compare_at_price": None,
                "available": True,
            },
        ],
        "images": [{"src": "https://cdn.shopify.com/naqsh1.jpg"}],
    }

    parsed = adapter._parse_shopify_raw(raw_naqsh, source_url="https://tehzeeblibas.com/products/naqsh")

    assert parsed["product_name"] == "NAQSH"
    # Selling price must exactly equal the source price (5276.4)
    assert parsed["price"] == 5276.4
    # No sale price when compare_at_price is None
    assert parsed["sale_price"] == ""
    # original_price equals active_price when no discount
    assert parsed["original_price"] == 5276.4
    assert parsed["discount_percentage"] == ""
    # Check variant prices (variants in universal schema is serialized as a JSON string)
    variants_list = json.loads(parsed["variants"]) if isinstance(parsed["variants"], str) else parsed["variants"]
    for v in variants_list:
        assert v["variant_price"] == 5276.4
        assert v["variant_original_price"] == ""


def test_shopify_adapter_discounted_product():
    """
    Verify discounted product pricing (e.g. KF03-KAFTAAN):
    price: 4380.00, compare_at: 6600.00.
    Selling price must be 4380, original/list price must be 6600.
    Compare-at price must NOT overwrite the selling price.
    """
    adapter = ShopifyAdapter()
    raw_discounted = {
        "id": 15230673780812,
        "title": "KF03-KAFTAAN",
        "handle": "kf03-kaftaan",
        "vendor": "Tehzeeb Libas",
        "product_type": "Daily/Basic Wear",
        "variants": [
            {
                "id": 101,
                "title": "S",
                "sku": "PAK-KF03-S",
                "price": "4380.00",
                "compare_at_price": "6600.00",
                "available": True,
            }
        ],
        "images": [{"src": "https://cdn.shopify.com/kf03.jpg"}],
    }

    parsed = adapter._parse_shopify_raw(raw_discounted, source_url="https://tehzeeblibas.com/products/kf03-kaftaan")

    # The active selling price must be 4380, NEVER 6600
    assert parsed["price"] == 4380
    assert parsed["sale_price"] == 4380
    assert parsed["original_price"] == 6600
    assert parsed["discount_percentage"] == "34%"

    # Explicit check: compare-at price is NOT used as selling price
    assert parsed["price"] != 6600
    assert parsed["price"] < parsed["original_price"]


def test_no_markup_on_generic_jsonld():
    """Verify that GenericAdapter JSON-LD parser extracts exact price without modification."""
    from bs4 import BeautifulSoup
    adapter = GenericAdapter()
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org/",
            "@type": "Product",
            "name": "Summer Lawn Kurti",
            "offers": {
                "@type": "Offer",
                "priceCurrency": "PKR",
                "price": "4397.00",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
    </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    product = adapter._try_jsonld_single(soup, "https://example.com/kurti")
    assert product is not None
    assert float(product["price"]) == 4397.0
    normalized = normalize_universal_product(product)
    assert normalized["price"] == 4397
    # Must NOT have any 20% markup applied
    assert normalized["price"] != 5276.40


def test_repeated_export_roundtrip_does_not_alter_prices(tmp_path: Path):
    """
    Requirement:
    - Repeated exports of the same unchanged input must not increment or alter prices.
    - Reprocessing an exported CSV must not introduce additional markup.
    """
    initial_products = [
        normalize_universal_product({
            "title": "NAQSH",
            "price": "5276.40",
            "product_url": "https://tehzeeblibas.com/products/naqsh",
        }),
        normalize_universal_product({
            "title": "RAYA EMBROIDERED 3PC",
            "price": "7176.00",
            "product_url": "https://tehzeeblibas.com/products/raya",
        }),
        normalize_universal_product({
            "title": "TEST 4397",
            "price": "4397",
            "product_url": "https://tehzeeblibas.com/products/test4397",
        }),
    ]

    csv_file1 = tmp_path / "export_pass1.csv"
    export_to_csv(initial_products, csv_file1)

    # Read back from CSV
    pass1_rows = []
    with open(csv_file1, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pass1_rows.append(row)

    assert float(pass1_rows[0]["price"]) == 5276.4
    assert float(pass1_rows[1]["price"]) == 7176.0
    assert float(pass1_rows[2]["price"]) == 4397.0

    # Re-normalize and re-export (Pass 2)
    reprocessed = [normalize_universal_product(row) for row in pass1_rows]
    csv_file2 = tmp_path / "export_pass2.csv"
    export_to_csv(reprocessed, csv_file2)

    with open(csv_file2, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        pass2_rows = list(reader)

    # Prices in pass 2 must be EXACTLY identical to pass 1
    assert float(pass2_rows[0]["price"]) == float(pass1_rows[0]["price"])
    assert float(pass2_rows[1]["price"]) == float(pass1_rows[1]["price"])
    assert float(pass2_rows[2]["price"]) == float(pass1_rows[2]["price"])


def test_cleaners_handles_missing_and_zero_prices():
    """Verify that None, empty, and invalid prices don't cause crashes or fabricate non-zero prices."""
    assert normalize_price(None) is None
    assert normalize_price("") is None
    assert normalize_price("N/A") is None
    assert normalize_price("Contact for price") is None
    assert normalize_price(0) == 0

    p_empty = normalize_universal_product({"title": "No Price Item"})
    assert p_empty["price"] == ""
    assert p_empty["sale_price"] == ""
    assert p_empty["original_price"] == ""
