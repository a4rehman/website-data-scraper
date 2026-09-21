import json
import pytest
from bs4 import BeautifulSoup
from scraper.adapters import get_adapter, ShopifyAdapter, WooCommerceAdapter, TemuAdapter, GenericAdapter
from scraper.cleaners import normalize_universal_product

def test_adapter_selection():
    temu_adapter = get_adapter("https://www.temu.com/pk-en/womens-clothing-o3-28.html")
    assert isinstance(temu_adapter, TemuAdapter)

    shopify_url_adapter = get_adapter("https://test-store.myshopify.com/products/test-item")
    assert isinstance(shopify_url_adapter, ShopifyAdapter)

    generic_adapter = get_adapter("https://unknown-shop-example.com/item/123")
    assert isinstance(generic_adapter, GenericAdapter)

def test_shopify_adapter_analyze():
    adapter = ShopifyAdapter()
    res = adapter.analyze_url("https://example.myshopify.com/collections/dresses")
    assert res["platform"] == "Shopify"
    assert res["page_type"] == "Collection"
    assert res["public_api_available"] is True

def test_shopify_parse_raw():
    adapter = ShopifyAdapter()
    raw_data = {
        "id": 99887766,
        "title": "Embroidered Lawn Suit",
        "handle": "embroidered-lawn-suit",
        "vendor": "Tehzeeb Libas",
        "product_type": "Dresses",
        "body_html": "<p>Premium luxury fabric</p><ul><li>3 Piece Suit</li></ul>",
        "tags": ["Lawn", "Summer"],
        "options": [{"name": "Size", "position": 1, "values": ["S", "M"]}],
        "variants": [
            {
                "id": 101,
                "title": "S",
                "option1": "S",
                "sku": "SUIT-S",
                "price": "4999.00",
                "compare_at_price": "6999.00",
                "available": True
            }
        ],
        "images": [{"src": "https://cdn.shopify.com/suit1.jpg"}]
    }
    parsed = adapter._parse_shopify_raw(raw_data, source_url="https://tehzeeblibas.com")
    assert parsed["product_name"] == "Embroidered Lawn Suit"
    assert parsed["price"] == 4999
    assert parsed["sale_price"] == 4999
    assert parsed["original_price"] == 6999
    assert parsed["discount_percentage"] == "29%"
    assert parsed["brand"] == "Tehzeeb Libas"
    assert parsed["sku"] == "SUIT-S"
    assert "S" in parsed["sizes"]
    assert parsed["main_image"] == "https://cdn.shopify.com/suit1.jpg"

def test_temu_adapter_analyze():
    adapter = TemuAdapter()
    res = adapter.analyze_url("https://www.temu.com/pk-en/womens-clothing-o3-28.html")
    assert res["platform"] == "Temu"
    assert res["page_type"] == "Category / Listing"
    assert res["requires_js"] is True

def test_temu_normalize_item():
    adapter = TemuAdapter()
    item = {
        "goods_id": "60109951234",
        "product_name": "Personalized Dog Collar Engraved",
        "price": "$12.99",
        "original_price": "$24.99",
        "thumb_url": "https://img.temu.com/collar.jpg",
        "product_url": "https://www.temu.com/goods.html?goods_id=60109951234",
        "sales_tip": "10K+ sold",
        "rating": "4.8"
    }
    normalized = adapter._normalize_temu_item(item, source_url="https://www.temu.com")
    assert normalized["product_name"] == "Personalized Dog Collar Engraved"
    assert normalized["price"] == 12.99
    assert normalized["original_price"] == 24.99
    assert normalized["discount_percentage"] == "48%"
    assert normalized["currency"] == "USD"
    assert normalized["sold_count"] == "10K+ sold"

def test_woocommerce_adapter_analyze():
    adapter = WooCommerceAdapter()
    res = adapter.analyze_url("https://example.com/product/cotton-hoodie/")
    assert res["platform"] == "WooCommerce"
    assert res["page_type"] == "Product"

def test_generic_jsonld_parse():
    adapter = GenericAdapter()
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org/",
            "@type": "Product",
            "name": "Wireless Noise Cancelling Headphones",
            "image": "https://example.com/headphones.jpg",
            "description": "High fidelity audio with ANC",
            "sku": "AUDIO-990",
            "brand": {"@type": "Brand", "name": "SoundMax"},
            "offers": {
                "@type": "Offer",
                "priceCurrency": "USD",
                "price": "99.99",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
    </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    product = adapter._try_jsonld_single(soup, "https://example.com/item")
    assert product is not None
    assert product["product_name"] == "Wireless Noise Cancelling Headphones"
    assert product["price"] == 99.99
    assert product["currency"] == "USD"
    assert product["brand"] == "SoundMax"
    assert product["main_image"] == "https://example.com/headphones.jpg"
