import json
import pytest
from scraper.custom_scraper import _try_jsonld, _try_basic_html, _empty_product_row

def test_empty_product_row():
    row = _empty_product_row(product_name="Sample Item", price="29.99")
    assert row["product_name"] == "Sample Item"
    assert row["price"] == "29.99"
    assert "stock_status" in row
    assert "sku" in row

def test_try_jsonld():
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org/",
            "@type": "Product",
            "name": "Personalized Dog Collar",
            "image": "https://example.com/collar.jpg",
            "description": "Custom engraved dog collar",
            "sku": "DOG-123",
            "offers": {
                "@type": "Offer",
                "priceCurrency": "USD",
                "price": "14.99",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
    </head>
    <body></body>
    </html>
    """
    products = _try_jsonld(html, "https://example.com")
    assert len(products) == 1
    assert products[0]["product_name"] == "Personalized Dog Collar"
    assert products[0]["price"] == "14.99"
    assert products[0]["currency"] == "USD"
    assert products[0]["availability"] == "In Stock"
    assert products[0]["image_url"] == "https://example.com/collar.jpg"

def test_try_basic_html():
    html = """
    <html>
    <head>
        <meta property="og:title" content="Leather Dog Collar" />
        <meta property="og:price:amount" content="19.99" />
        <meta property="og:image" content="https://example.com/collar2.jpg" />
    </head>
    <body></body>
    </html>
    """
    products = _try_basic_html(html, "https://example.com/item")
    assert len(products) == 1
    assert products[0]["product_name"] == "Leather Dog Collar"
    assert products[0]["price"] == "19.99"
    assert products[0]["image_url"] == "https://example.com/collar2.jpg"
