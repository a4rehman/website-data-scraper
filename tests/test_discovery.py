"""
Regression tests for generic e-commerce discovery and extraction (Phase 3).

Uses deterministic mocked HTML fixtures modeled on the real Tehzeeb Libas
collection page (Shopify Dawn theme: product-card-wrapper cards with
/products/{handle} links, JSON-LD blocks).
"""

import json
import pytest
from bs4 import BeautifulSoup
from unittest.mock import patch

from scraper.adapters.generic import (
    GenericAdapter,
    _is_product_path,
    _normalize_product_url,
)
from scraper.adapters.shopify import ShopifyAdapter
from scraper.engine import _item_url, _item_label


# ---------------------------------------------------------------------------
# Fixture: Tehzeeb-style Shopify Dawn collection page
# ---------------------------------------------------------------------------
TEHZEEB_STYLE_HTML = """
<html>
<head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Organization", "name": "Tehzeeb Libas"}
</script>
</head>
<body>
<div class="page-content">
  <div class="product-grid">
    <div class="grid__item">
      <div class="card-wrapper product-card-wrapper underline-links-hover">
        <div class="card card--standard card--media">
          <div class="card__media">
            <img src="//tehzeeblibas.com/cdn/shop/files/anarkali.jpg?width=600" alt="Anarkali"/>
          </div>
          <div class="card__content">
            <h3 class="card__heading"><a href="/products/anarkali" class="full-unstyled-link">Anarkali Embroidered 3PC</a></h3>
            <div class="price price--on-sale"><span>Rs. 5,999</span></div>
          </div>
        </div>
      </div>
    </div>
    <div class="grid__item">
      <div class="card-wrapper product-card-wrapper underline-links-hover">
        <div class="card card--standard card--media">
          <div class="card__media">
            <img src="//tehzeeblibas.com/cdn/shop/files/ammara.jpg?width=600" alt="Ammara"/>
          </div>
          <div class="card__content">
            <h3 class="card__heading"><a href="/products/ammara" class="full-unstyled-link">Ammara 2PC</a></h3>
            <div class="price"><span>Rs. 3,588</span></div>
          </div>
        </div>
      </div>
    </div>
    <div class="grid__item">
      <div class="card-wrapper product-card-wrapper underline-links-hover">
        <div class="card card--standard card--media">
          <div class="card__media">
            <img src="//tehzeeblibas.com/cdn/shop/files/aiza.jpg?width=600" alt="Aiza"/>
          </div>
          <div class="card__content">
            <h3 class="card__heading"><a href="/products/aiza" class="full-unstyled-link">Aiza Lawn</a></h3>
            <div class="price"><span>Rs. 4,200</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <nav class="pagination"><a href="/collections/all?page=2">2</a></nav>
</div>
</body>
</html>
"""

PRODUCT_PAGE_HTML = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Anarkali Embroidered 3PC",
  "sku": "TZ-001",
  "image": "https://tehzeeblibas.com/cdn/shop/files/anarkali.jpg",
  "description": "Beautiful embroidered anarkali set",
  "brand": {"@type": "Brand", "name": "Tehzeeb Libas"},
  "offers": {
    "@type": "Offer",
    "price": "5999.00",
    "priceCurrency": "PKR",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</head>
<body><h1>Anarkali Embroidered 3PC</h1></body>
</html>
"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def test_is_product_path_shopify_handles():
    assert _is_product_path("/products/anarkali")
    assert _is_product_path("/products/anarkali-embroidered-2pc/")
    assert _is_product_path("/product/anarkali")
    assert _is_product_path("/item/123")


def test_is_product_path_rejects_collections_and_indexes():
    assert not _is_product_path("/collections/all")
    assert not _is_product_path("/collections/all/products")  # nested under collection
    assert not _is_product_path("/products")                 # bare index
    assert not _is_product_path("/product")                  # bare index
    assert not _is_product_path("/cart")
    assert not _is_product_path("/search")
    assert not _is_product_path("/pages/about")


def test_normalize_product_url():
    base = "https://example.com/collections/all"
    assert _normalize_product_url("/products/a", base) == "https://example.com/products/a"
    assert _normalize_product_url("//cdn.example.com/products/b", base) == "https://cdn.example.com/products/b"
    # Tracking params stripped, fragments stripped
    assert (
        _normalize_product_url("/products/c?utm_source=x&fbclid=y#frag", base)
        == "https://example.com/products/c"
    )
    assert _normalize_product_url("not a url with spaces", base).startswith("https://")


# ---------------------------------------------------------------------------
# HTML card discovery on Tehzeeb-style markup
# ---------------------------------------------------------------------------
def test_tehzeeb_style_card_discovery():
    adapter = GenericAdapter()
    soup = BeautifulSoup(TEHZEEB_STYLE_HTML, "html.parser")
    products = adapter._extract_html_product_cards(soup, "https://tehzeeblibas.com/collections/all")
    urls = {p.get("product_url") for p in products}
    assert "https://tehzeeblibas.com/products/anarkali" in urls
    assert "https://tehzeeblibas.com/products/ammara" in urls
    assert "https://tehzeeblibas.com/products/aiza" in urls
    # The collection URL itself must never appear as a product
    assert "https://tehzeeblibas.com/collections/all" not in urls


# ---------------------------------------------------------------------------
# JSON-LD discovery: ItemList, @graph, string entries, nested wrappers
# ---------------------------------------------------------------------------
def test_jsonld_itemlist_with_products_and_urls():
    html = """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "ItemList",
      "itemListElement": [
        {"@type": "ListItem", "position": 1,
         "item": {"@type": "Product", "name": "Product A",
                  "offers": {"price": "10.00", "priceCurrency": "USD"}}},
        {"@type": "ListItem", "position": 2,
         "item": "https://example.com/products/b"},
        {"@type": "ListItem", "position": 3,
         "url": "https://example.com/products/c"}
      ]
    }
    </script></head><body></body></html>
    """
    adapter = GenericAdapter()
    soup = BeautifulSoup(html, "html.parser")
    products = adapter._extract_jsonld_listing(soup, "https://example.com/collection")
    assert len(products) == 3
    assert products[0]["product_name"] == "Product A"
    assert products[1] == {"product_url": "https://example.com/products/b"}
    assert products[2] == {"product_url": "https://example.com/products/c"}


def test_jsonld_graph_with_products():
    html = """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "Organization", "name": "Store"},
        {"@type": "Product", "name": "Graph Product",
         "offers": {"price": "9.99", "priceCurrency": "EUR"}}
      ]
    }
    </script></head><body></body></html>
    """
    adapter = GenericAdapter()
    soup = BeautifulSoup(html, "html.parser")
    product = adapter._try_jsonld_single(soup, "https://example.com/products/graph-product")
    assert product is not None
    assert product["product_name"] == "Graph Product"


def test_jsonld_string_entries_do_not_crash():
    # Regression: JSON-LD arrays containing strings used to crash with
    # "'str' object has no attribute 'get'".
    html = """
    <html><head><script type="application/ld+json">
    ["just-a-string", {"@type": "Product", "name": "Safe Product",
     "offers": {"price": "1.00", "priceCurrency": "USD"}}]
    </script></head><body></body></html>
    """
    adapter = GenericAdapter()
    soup = BeautifulSoup(html, "html.parser")
    product = adapter._try_jsonld_single(soup, "https://example.com/p/safe")
    assert product is not None
    assert product["product_name"] == "Safe Product"


def test_jsonld_list_as_toplevel_type():
    html = """
    <html><head><script type="application/ld+json">
    {"@type": ["Product", "Thing"], "name": "MultiType",
     "offers": {"price": "5", "priceCurrency": "USD"}}
    </script></head><body></body></html>
    """
    adapter = GenericAdapter()
    soup = BeautifulSoup(html, "html.parser")
    product = adapter._try_jsonld_single(soup, "https://example.com/p/multi")
    assert product is not None
    assert product["product_name"] == "MultiType"


# ---------------------------------------------------------------------------
# Discovery never returns the collection URL as a product
# ---------------------------------------------------------------------------
def test_discover_products_returns_empty_not_collection_url():
    adapter = GenericAdapter()
    with patch(
        "scraper.adapters.generic.fetch_html", return_value="<html><body>no products</body></html>"
    ):
        result = adapter.discover_products("https://example-shop.com/collections/all")
    assert result == []
    assert "https://example-shop.com/collections/all" not in result


def test_discover_products_single_product_url_passthrough():
    adapter = GenericAdapter()
    result = adapter.discover_products("https://example-shop.com/products/solo-item")
    assert result == ["https://example-shop.com/solo-item".replace("/solo", "/products/solo")]


# ---------------------------------------------------------------------------
# extract_product handles string AND dictionary discovery items safely
# ---------------------------------------------------------------------------
def test_extract_product_accepts_url_string():
    adapter = GenericAdapter()
    with patch("scraper.adapters.generic.fetch_html", return_value=PRODUCT_PAGE_HTML):
        product = adapter.extract_product(
            "https://tehzeeblibas.com/products/anarkali",
            source_url="https://tehzeeblibas.com/collections/all",
        )
    assert product is not None
    assert product["product_name"] == "Anarkali Embroidered 3PC"
    assert product["price"] == 5999
    assert product["sku"] == "TZ-001"
    assert product["brand"] == "Tehzeeb Libas"
    assert product["currency"] == "PKR"


def test_extract_product_accepts_dict_with_url_only():
    adapter = GenericAdapter()
    with patch("scraper.adapters.generic.fetch_html", return_value=PRODUCT_PAGE_HTML):
        product = adapter.extract_product(
            {"product_url": "https://tehzeeblibas.com/products/anarkali"},
            source_url="https://tehzeeblibas.com/collections/all",
        )
    assert product is not None
    assert product["product_name"] == "Anarkali Embroidered 3PC"


def test_extract_product_accepts_dict_with_full_payload():
    adapter = GenericAdapter()
    payload = {
        "product_name": "Dict Product",
        "price": "12.50",
        "product_url": "https://example.com/products/dict-product",
    }
    product = adapter.extract_product(payload, source_url="https://example.com")
    assert product is not None
    assert product["product_name"] == "Dict Product"
    assert product["price"] == 12.5


def test_extract_product_returns_none_for_bad_dict():
    adapter = GenericAdapter()
    assert adapter.extract_product({"foo": "bar"}) is None
    assert adapter.extract_product(12345) is None


# ---------------------------------------------------------------------------
# Engine item helpers handle both shapes
# ---------------------------------------------------------------------------
def test_item_url_and_label_for_both_shapes():
    assert _item_url({"product_url": "https://x/p/1"}) == "https://x/p/1"
    assert _item_url("https://x/p/2") == "https://x/p/2"
    assert _item_url({"url": "https://x/p/3"}) == "https://x/p/3"
    assert _item_url({"name": "no url"}) == ""
    assert _item_label({"product_name": "Named"}) == "Named"
    assert _item_label("https://x/p/4") == "https://x/p/4"


# ---------------------------------------------------------------------------
# Shopify adapter discovery: explicit failure, no fake product
# ---------------------------------------------------------------------------
def test_shopify_discovery_empty_returns_nothing():
    adapter = ShopifyAdapter()
    with patch("scraper.adapters.shopify.fetch_json", return_value=None):
        result = adapter.discover_products("https://tehzeeblibas.com/collections/all")
    assert result == []


def test_shopify_discovery_parses_products_json():
    adapter = ShopifyAdapter()
    payload = {
        "products": [
            {"id": 1, "title": "P1", "handle": "p1", "variants": [], "images": []},
            {"id": 2, "title": "P2", "handle": "p2", "variants": [], "images": []},
        ]
    }
    with patch("scraper.adapters.shopify.fetch_json", return_value=payload):
        result = adapter.discover_products("https://tehzeeblibas.com/collections/all", max_products=5)
    assert len(result) == 2
    assert result[0]["title"] == "P1"
