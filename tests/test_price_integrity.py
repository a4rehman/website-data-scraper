"""
Automated Price Integrity Tests for Website Data Scraper.

Guarantees:
1. Source prices are preserved without fixed or percentage markups (+1200, +1500, *1.2, etc.).
2. Normalization extracts exact numeric values from strings (Rs. 4,397 -> 4397; Rs. 5,000 -> 5000; Rs. 7,176 -> 7176).
3. Compare-at / original prices are kept separate from current selling prices.
4. Missing compare-at prices remain empty and are NEVER fabricated from active selling price.
5. Sale prices never get replaced by list prices; when on sale, sale_price == price.
6. Repeated exports and CSV round-tripping do not compound or change prices.
7. Provenance fields accurately reflect extraction sources and variant price variation.
8. Variants preserve their individual prices, and in-stock variants are prioritized.
"""

import csv
import json
import pytest
from pathlib import Path
import config
from scraper.cleaners import (
    normalize_price,
    normalize_universal_product,
    calculate_discount_percentage,
)
from scraper.adapters.shopify import ShopifyAdapter
from scraper.adapters.generic import GenericAdapter
from scraper.adapters.woocommerce import WooCommerceAdapter
from scraper.exporter import export_to_csv, export_to_json


# ==============================================================================
# Group A: Source Value Preservation
# ==============================================================================

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

    # Explicit guarantee: 4397 is NEVER transformed into 5276.40, 5597, or any markup
    assert normalize_price("Rs. 4,397") != 5276.40
    assert normalize_price("Rs. 4,397") != 5597

    assert normalize_price("Rs. 5,000") == 5000
    assert normalize_price("Rs 5,000") == 5000
    assert normalize_price(5000) == 5000

    assert normalize_price("Rs. 7,176") == 7176
    assert normalize_price("7176.00") == 7176
    assert normalize_price("7176") == 7176


def test_decimal_price_preservation():
    """Verify that decimal prices like 5276.40 or 7195.20 are preserved exactly without distortion."""
    assert normalize_price("5276.40") == 5276.4
    assert normalize_price("Rs. 5,276.40") == 5276.4
    assert normalize_price(5276.4) == 5276.4

    assert normalize_price("7195.20") == 7195.2
    assert normalize_price("7154.40") == 7154.4


# ==============================================================================
# Group B: Sale, Original, and Compare-at Price Semantics
# ==============================================================================

def test_shopify_adapter_naqsh_raw_extraction():
    """
    Verify Shopify adapter extraction on the exact NAQSH JSON payload from Tehzeeb Libas.
    Raw price '5276.40' with compare_at_price None must yield price: 5276.4.
    Original/compare-at price MUST remain empty (NEVER fabricated).
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
    # Not on sale: sale_price, original_price, and compare_at_price MUST remain empty
    assert parsed["sale_price"] == ""
    assert parsed["original_price"] == ""
    assert parsed["compare_at_price"] == ""
    assert parsed["list_price"] == ""
    assert parsed["discount_percentage"] == ""

    # Check variant prices (serialized as JSON string in Universal Schema)
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
    assert parsed["compare_at_price"] == 6600
    assert parsed["list_price"] == 6600
    assert parsed["discount_percentage"] == "34%"

    # Explicit check: compare-at price is NOT used as selling price
    assert parsed["price"] != 6600
    assert parsed["price"] < parsed["original_price"]


# ==============================================================================
# Group C: No Unintended Markup or Arithmetic Distortion
# ==============================================================================

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
    # Must NOT have fixed additions (+1200 or +1500)
    assert normalized["price"] != 5597
    assert normalized["price"] != 5897


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


# ==============================================================================
# Group D: Variants, Caching & In-Stock Precedence
# ==============================================================================

def test_shopify_adapter_prefers_instock_variant():
    """Verify that when variant 0 is Out of Stock, the adapter uses the first in-stock variant."""
    adapter = ShopifyAdapter()
    raw = {
        "id": 999,
        "title": "Multi Variant Item",
        "variants": [
            {"id": 1, "sku": "OOS-SKU", "price": "1000", "available": False},
            {"id": 2, "sku": "INSTOCK-SKU", "price": "1200", "available": True},
        ]
    }
    parsed = adapter._parse_shopify_raw(raw, source_url="https://tehzeeblibas.com/item")
    assert parsed["sku"] == "INSTOCK-SKU"
    assert parsed["price"] == 1200


def test_shopify_adapter_detects_varying_variant_prices():
    """Verify that varying variant prices are recorded in the price_source provenance field."""
    adapter = ShopifyAdapter()
    raw = {
        "id": 888,
        "title": "Varying Price Item",
        "variants": [
            {"id": 1, "sku": "S", "price": "4000", "available": True},
            {"id": 2, "sku": "XL", "price": "5000", "available": True},
        ]
    }
    parsed = adapter._parse_shopify_raw(raw, source_url="https://tehzeeblibas.com/item")
    assert "varies" in parsed["price_source"]
    assert "min=4000" in parsed["price_source"]
    assert "max=5000" in parsed["price_source"]


# ==============================================================================
# Group E: Schema Completeness and Missing / Edge Prices
# ==============================================================================

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
    assert p_empty["compare_at_price"] == ""


def test_schema_includes_compare_at_price():
    """Verify that compare_at_price is a first-class citizen in UNIVERSAL_CSV_COLUMNS."""
    assert "compare_at_price" in config.UNIVERSAL_CSV_COLUMNS
    p = normalize_universal_product({
        "title": "On Sale Item",
        "price": "4000",
        "original_price": "6000",
    })
    assert p["price"] == 4000
    assert p["sale_price"] == 4000
    assert p["original_price"] == 6000
    assert p["compare_at_price"] == 6000
