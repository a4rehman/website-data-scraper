import pytest
from scraper.parser import parse_product_data

def test_parse_product_data():
    raw_product = {
        "id": 8114111348811,
        "title": "TEST EMBROIDERED 3PC",
        "handle": "test-embroidered-3pc",
        "vendor": "Tehzeeb Libas",
        "product_type": "Festive/Party Wear",
        "body_html": "<p>High quality 3 piece suit in Lawn fabric</p>",
        "tags": ["Festive Wear", "Lawn", "Chiffon"],
        "options": [{"name": "Size", "position": 1, "values": ["S", "M", "L"]}],
        "variants": [
            {
                "id": 1,
                "title": "S",
                "option1": "S",
                "sku": "PAK-TEST-S",
                "price": "5990.00",
                "compare_at_price": "7990.00",
                "available": True
            },
            {
                "id": 2,
                "title": "M",
                "option1": "M",
                "sku": "PAK-TEST-M",
                "price": "5990.00",
                "compare_at_price": "7990.00",
                "available": True
            }
        ],
        "images": [
            {"src": "https://cdn.shopify.com/img1.jpg"},
            {"src": "https://cdn.shopify.com/img2.jpg"}
        ]
    }

    parsed = parse_product_data(raw_product)

    assert parsed["product_id"] == "8114111348811"
    assert parsed["sku"] == "PAK-TEST-S"
    assert parsed["product_name"] == "TEST EMBROIDERED 3PC"
    assert "High quality 3 piece" in parsed["description"]
    assert parsed["brand"] == "Tehzeeb Libas"
    assert parsed["price"] == 5990
    assert parsed["sale_price"] == 5990
    assert parsed["original_price"] == 7990
    assert parsed["discount_percentage"] == "25%"
    assert parsed["availability"] == "In Stock"
    assert "S" in parsed["sizes"] and "M" in parsed["sizes"]
    assert parsed["material"] != ""
    assert parsed["image_url"] == "https://cdn.shopify.com/img1.jpg"
    assert parsed["additional_image_urls"] == "https://cdn.shopify.com/img2.jpg"
