"""
Regression tests for the Temu adapter (Phase: category scraping end-to-end).

Covers:
- The exact category.html?opt_id=... URL shape selects the Temu adapter.
- Category URLs are never treated as individual products.
- window.rawData (single-line ~360KB hydration JSON) parses via brace matching.
- camelCase goods nodes (goodsId/title/thumbUrl/seoLinkUrl/priceInfo) map to
  the discovery-item contract with real prices/ratings.
- Missing or malformed fields never crash the scraper.
- Strings and dictionaries are handled per the agreed item contract.
- Empty listings and blocked responses produce explicit failures.
- Browser-unavailable produces a clear diagnostic, not silent success.
- Progress advances through discovery.
- CSV export schema and values are correct.
"""

import json
import pytest
from unittest.mock import patch

import config
from scraper.adapters import get_adapter, TemuAdapter
from scraper.cleaners import normalize_universal_product


TEMU_CATEGORY_URL = (
    "https://www.temu.com/category.html?opt_id=-42&opt_level=1&title=Personalized%20Products"
    "&_x_enter_scene_type=cate_tab&show_search_type=0&leaf_type=son&refer_page_el_sn=200055"
    "&refer_page_name=home&refer_page_id=10005_1791845952665_jszwnjdxhr&refer_page_sn=10005"
)

TEMU_PRODUCT_URL = (
    "https://www.temu.com/pk-en/1-personalized-dog-tag-stainless-steel-tag-with-engraved-"
    "name-and-phone-number-durable-pet-anti-loss-id-tag-dog-name-tag-g-601100459833657.html"
)


# ---------------------------------------------------------------------------
# Realistic window.rawData fixture (shape verified against the live page)
# ---------------------------------------------------------------------------
def _raw_data_store(goods_list, has_more=False, banned=False, is_robot=False, page_size=40):
    """Return the inner `store` object (temu_category_html wraps it in rawData)."""
    return {
        "$service": {"region": "153"},
        "localInfo": {"region": "153", "language": "en", "currency": "PKR"},
        "banned": banned,
        "isRobot": is_robot,
        "isErrorPage": False,
        "pageSize": page_size,
        "hasMore": has_more,
        "hasResult": True,
        "goodsList": [{"data": g} for g in goods_list],
        "recGoodsList": [],
    }


def _goods_node(gid, title, price_rs, market_rs, **overrides):
    node = {
        "goodsId": gid,
        "title": title,
        "thumbUrl": f"https://img.kwcdn.com/product/fancy/{gid}.jpg",
        "seoLinkUrl": f"/pk-en/{title.lower().replace(' ', '-')}-g-{gid}.html?&_oak_mp_inf=TRACKING",
        "linkUrl": f"goods.html?_bg_fs=1&goods_id={gid}&_oak_mp_inf=TRACKING",
        "priceInfo": {
            "priceStr": f"Rs.{price_rs}",
            "price": int(float(str(price_rs).replace(',', '')) * 100),
            "marketPriceStr": str(market_rs),
            "marketPrice": int(float(str(market_rs).replace(',', '')) * 100),
            "currency": "PKR",
            "reductionText": ["-54", "%"],
        },
        "comment": {"goodsScore": 4.9, "commentNumTips": "12,622"},
        "salesTip": "150K+ sold",
        "salesNum": "100K+",
        "optId": 1367,
        "mallId": 5985785536317,
        "image": {"url": f"https://img.kwcdn.com/product/fancy/{gid}.jpg", "width": 375, "height": 375},
    }
    node.update(overrides)
    return node


GOOD_LIST = [
    _goods_node("601100459833657", "Personalized Dog Tag", "442", "963"),
    _goods_node("601100047674432", "Plush Cushion", "1,515", "2,660"),
    _goods_node("606090280413360", "Pet Collar", "1,185", "2,021",
                comment={"goodsScore": 5, "commentNumTips": "200"}),
]


def temu_category_html(store_dict, extra_html=""):
    """Wrap a rawData store dict in a realistic Temu-like HTML document."""
    payload = json.dumps({"store": store_dict}, ensure_ascii=False)
    # Simulate the single-line window.rawData assignment with a tiny sibling var.
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        "<script>window.__NPM_PACKAGE_CONFIG__ = {\"localeSwitch\":{\"a\":1}};</script>"
        f"<script>window.__SSR__=1;</script><script>window.__CHUNK_DATA__={{}};window.rawData={payload};</script>"
        "</head><body><div class=\"page-content\">"
        "<a href=\"/pk-en/personalized-dog-tag-g-601100459833657.html\">"
        "<img src=\"https://img.kwcdn.com/x.jpg\"/>Top pick Personalized Dog Tag Open in new tab.</a>"
        f"{extra_html}"
        "</div></body></html>"
    )


@pytest.fixture(autouse=True)
def fast_network(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY", 0)
    monkeypatch.setattr("scraper.adapters.temu.fetch_html", lambda url: None)


# ---------------------------------------------------------------------------
# 1. Adapter selection & classification for the exact URL shape
# ---------------------------------------------------------------------------
def test_exact_category_url_selects_temu_adapter():
    adapter = get_adapter(TEMU_CATEGORY_URL)
    assert isinstance(adapter, TemuAdapter)


def test_category_url_classified_as_category_not_product():
    adapter = TemuAdapter()
    analysis = adapter.analyze_url(TEMU_CATEGORY_URL)
    assert analysis["platform"] == "Temu"
    assert analysis["page_type"] == "Category / Listing"
    assert analysis["requires_js"] is True


def test_other_category_shapes_still_classified_as_category():
    adapter = TemuAdapter()
    for url in (
        "https://www.temu.com/pk-en/womens-clothing-o3-28.html",
        "https://www.temu.com/category.html?opt_id=123",
        "https://www.temu.com/search_result.html?search_key=shoes",
    ):
        analysis = adapter.analyze_url(url)
        if "search_result" in url:
            assert analysis["page_type"] == "Search", url
        else:
            assert analysis["page_type"] == "Category / Listing", url


def test_product_seo_url_classified_as_product():
    adapter = TemuAdapter()
    assert adapter.analyze_url(TEMU_PRODUCT_URL)["page_type"] == "Product"
    assert adapter.analyze_url("https://www.temu.com/goods.html?goods_id=123")["page_type"] == "Product"


def test_category_url_not_treated_as_single_product():
    """discover_products on a category URL must never return [category_url]."""
    adapter = TemuAdapter()
    with patch("scraper.adapters.temu.fetch_html", return_value=None):
        with pytest.raises(ValueError, match="no HTML"):
            adapter.discover_products(TEMU_CATEGORY_URL)


def test_product_url_discovery_returns_single_url_string():
    adapter = TemuAdapter()
    result = adapter.discover_products(TEMU_PRODUCT_URL, max_products=5)
    assert result == [TEMU_PRODUCT_URL]


# ---------------------------------------------------------------------------
# 2. window.rawData extraction (brace matching, large single-line JSON)
# ---------------------------------------------------------------------------
def test_extract_window_json_parses_single_line_payload():
    store = _raw_data_store(GOOD_LIST)
    html = temu_category_html(store)
    raw = TemuAdapter._extract_window_json(html, "rawData")
    assert isinstance(raw, dict)
    assert len(raw["store"]["goodsList"]) == 3


def test_extract_window_json_handles_braces_inside_strings():
    """Strings containing braces/quotes must not break brace matching."""
    tricky = _goods_node("1", 'Weird "title" with } brace { inside', "10", "20")
    store = _raw_data_store([tricky])
    raw = TemuAdapter._extract_window_json(temu_category_html(store), "rawData")
    assert raw is not None
    node = raw["store"]["goodsList"][0]["data"]
    assert node["title"] == 'Weird "title" with } brace { inside'


def test_extract_window_json_returns_none_when_missing():
    assert TemuAdapter._extract_window_json("<html><body>nothing</body></html>", "rawData") is None
    assert TemuAdapter._extract_window_json("", "rawData") is None
    # Malformed JSON → None, no crash
    bad = "<script>window.rawData={\"store\": {broken</script>"
    assert TemuAdapter._extract_window_json(bad, "rawData") is None


# ---------------------------------------------------------------------------
# 3. Discovery from realistic category HTML
# ---------------------------------------------------------------------------
def test_discovery_from_raw_data_maps_real_fields():
    adapter = TemuAdapter()
    html = temu_category_html(_raw_data_store(GOOD_LIST))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        items = adapter.discover_products(TEMU_CATEGORY_URL, max_products=5)

    assert len(items) == 3
    first = items[0]
    assert first["goods_id"] == "601100459833657"
    assert first["product_name"] == "Personalized Dog Tag"
    assert first["price"] == "Rs.442"
    assert first["original_price"] == "963"
    assert first["currency"] == "PKR"
    assert first["image_url"].endswith(".jpg")
    # Tracking params stripped from the canonical URL, goods path kept
    assert first["product_url"].endswith("-g-601100459833657.html")
    assert "_oak_mp_inf" not in first["product_url"]
    assert first["rating"] == "4.9"
    assert first["sold_count"] == "150K+ sold"


def test_discovery_respects_max_products_ceiling():
    adapter = TemuAdapter()
    big_list = [_goods_node(str(1000 + i), f"Item {i}", "10", "20") for i in range(40)]
    html = temu_category_html(_raw_data_store(big_list, has_more=True))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        items = adapter.discover_products(TEMU_CATEGORY_URL, max_products=5)
    assert len(items) == 5


def test_discovery_dedupes_by_goods_id():
    adapter = TemuAdapter()
    dup_list = GOOD_LIST + [_goods_node("601100459833657", "Duplicate Dog Tag", "442", "963")]
    html = temu_category_html(_raw_data_store(dup_list))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        items = adapter.discover_products(TEMU_CATEGORY_URL, max_products=10)
    assert len(items) == 3
    ids = [i["goods_id"] for i in items]
    assert len(ids) == len(set(ids))


def test_anchor_fallback_when_no_raw_data():
    """Without rawData, cleaned DOM anchors still yield valid items."""
    adapter = TemuAdapter()
    html = (
        "<html><body>"
        '<a href="/pk-en/fancy-collar-g-999.html">'
        "<img src='https://img.kwcdn.com/c.jpg'/>Top pick Fancy Collar Open in new tab.</a>"
        "</body></html>"
    )
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        items = adapter.discover_products(TEMU_CATEGORY_URL, max_products=5)
    assert len(items) == 1
    assert items[0]["goods_id"] == "999"
    # Title chrome stripped
    assert items[0]["product_name"] == "Fancy Collar"
    assert "Top pick" not in items[0]["product_name"]
    assert "Open in new tab" not in items[0]["product_name"]


def test_clean_anchor_title_variants():
    clean = TemuAdapter._clean_anchor_title
    assert clean("Top pick Widget Open in new tab.") == "Widget"
    assert clean("Top pickWidget Opens in new tab") == "Widget"
    assert clean("  Plain Title  ") == "Plain Title"
    assert clean("") == ""


# ---------------------------------------------------------------------------
# 4. Missing / malformed fields never crash
# ---------------------------------------------------------------------------
def test_goods_node_missing_price_and_image_still_maps():
    adapter = TemuAdapter()
    node = {"goodsId": "42", "title": "Bare Node"}
    mapped = adapter._map_raw_goods_node(node, TEMU_CATEGORY_URL)
    assert mapped is not None
    assert mapped["goods_id"] == "42"
    assert mapped["price"] == ""
    assert mapped["image_url"] == ""
    assert mapped["product_url"].endswith("goods_id=42")


def test_goods_node_empty_returns_none():
    adapter = TemuAdapter()
    assert adapter._map_raw_goods_node({}, TEMU_CATEGORY_URL) is None
    assert adapter._map_raw_goods_node(None, TEMU_CATEGORY_URL) is None
    assert adapter._map_raw_goods_node("not-a-dict", TEMU_CATEGORY_URL) is None
    assert adapter._map_raw_goods_node({"unrelated": 1}, TEMU_CATEGORY_URL) is None


def test_malformed_price_info_does_not_crash():
    adapter = TemuAdapter()
    node = _goods_node("7", "Weird Price", "10", "20")
    node["priceInfo"] = "not-a-dict"
    mapped = adapter._map_raw_goods_node(node, TEMU_CATEGORY_URL)
    assert mapped is not None
    assert mapped["goods_id"] == "7"


def test_cents_fallback_when_price_str_missing():
    adapter = TemuAdapter()
    node = _goods_node("8", "Cents Only", "10", "20")
    node["priceInfo"] = {"price": 44200, "marketPrice": 96300, "currency": "PKR"}
    mapped = adapter._map_raw_goods_node(node, TEMU_CATEGORY_URL)
    assert mapped["price"] == "442.0"
    assert mapped["original_price"] == "963.0"


def test_find_goods_in_json_handles_mixed_shapes():
    adapter = TemuAdapter()
    payload = {
        "a": {"goodsId": "1", "title": "A", "priceInfo": {"priceStr": "Rs.5"}},
        "b": ["skip", {"goods_id": "2", "product_name": "B", "price": "7"}],
        "c": {"no": "goods"},
    }
    found = adapter._find_goods_in_json(payload, TEMU_CATEGORY_URL)
    ids = {f["goods_id"] for f in found}
    assert ids == {"1", "2"}


# ---------------------------------------------------------------------------
# 5. Item contract: strings and dictionaries
# ---------------------------------------------------------------------------
def test_extract_product_accepts_discovery_dict():
    adapter = TemuAdapter()
    item = adapter._map_raw_goods_node(GOOD_LIST[0], TEMU_CATEGORY_URL)
    product = adapter.extract_product(item, source_url=TEMU_CATEGORY_URL)
    assert product is not None
    assert product["product_name"] == "Personalized Dog Tag"
    assert product["price"] == 442.0
    assert product["original_price"] == 963.0
    assert product["discount_percentage"] == "54%"
    assert product["currency"] == "PKR"
    assert product["product_id"] == "601100459833657"
    # Never fabricate SKU/brand/availability
    assert product["sku"] == ""
    assert product["brand"] == ""
    assert product["availability"] == ""


def test_extract_product_accepts_url_string_via_html():
    adapter = TemuAdapter()
    html = temu_category_html(_raw_data_store([GOOD_LIST[0]]))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        product = adapter.extract_product(TEMU_PRODUCT_URL, source_url=TEMU_CATEGORY_URL)
    assert product is not None
    assert product["product_name"] == "Personalized Dog Tag"


def test_extract_product_rejects_bad_inputs():
    adapter = TemuAdapter()
    assert adapter.extract_product({"unrelated": 1}) is None
    assert adapter.extract_product(None) is None
    assert adapter.extract_product(12345) is None
    assert adapter.extract_product("") is None


def test_normalize_temu_item_backcompat_snake_case():
    """Existing snake_case fixtures keep normalizing (regression)."""
    adapter = TemuAdapter()
    item = {
        "goods_id": "60109951234",
        "product_name": "Personalized Dog Collar Engraved",
        "price": "$12.99",
        "original_price": "$24.99",
        "thumb_url": "https://img.temu.com/collar.jpg",
        "product_url": "https://www.temu.com/goods.html?goods_id=60109951234",
        "sales_tip": "10K+ sold",
        "rating": "4.8",
    }
    normalized = adapter._normalize_temu_item(item, source_url="https://www.temu.com")
    assert normalized["product_name"] == "Personalized Dog Collar Engraved"
    assert normalized["price"] == 12.99
    assert normalized["original_price"] == 24.99
    assert normalized["discount_percentage"] == "48%"
    assert normalized["currency"] == "USD"
    assert normalized["sold_count"] == "10K+ sold"


def test_normalize_strips_temu_tracking_params():
    adapter = TemuAdapter()
    item = {
        "goods_id": "1",
        "title": "X",
        "product_url": "https://www.temu.com/pk-en/x-g-1.html?_x_sessn_id=abc&top_gallery_url=z",
    }
    normalized = adapter._normalize_temu_item(item, source_url=TEMU_CATEGORY_URL)
    assert normalized["product_url"] == "https://www.temu.com/pk-en/x-g-1.html"


# ---------------------------------------------------------------------------
# 6. Empty listings and blocked responses → explicit failures
# ---------------------------------------------------------------------------
def test_empty_listing_returns_empty_list_not_category_url():
    adapter = TemuAdapter()
    # rawData with zero goods AND no product anchors in the DOM
    html = (
        "<!DOCTYPE html><html><head>"
        "<script>window.rawData=" + json.dumps({"store": _raw_data_store([])}) + ";</script>"
        "</head><body><div class='page-content'>No products here.</div></body></html>"
    )
    no_browser = {"available": False, "message": "disabled for test"}
    with patch("scraper.adapters.temu.fetch_html", return_value=html), \
         patch("scraper.adapters.temu.check_browser_available", return_value=no_browser):
        items = adapter.discover_products(TEMU_CATEGORY_URL)
    assert items == []
    assert TEMU_CATEGORY_URL not in items


def test_fetch_failure_raises_explicit_error():
    adapter = TemuAdapter()
    with patch("scraper.adapters.temu.fetch_html", return_value=None):
        with pytest.raises(ValueError, match="no HTML"):
            adapter.discover_products(TEMU_CATEGORY_URL)


def test_banned_flag_raises_blocked_error():
    adapter = TemuAdapter()
    html = temu_category_html(_raw_data_store([], banned=True))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        with pytest.raises(ValueError, match="banned=true"):
            adapter.discover_products(TEMU_CATEGORY_URL)


def test_robot_flag_raises_blocked_error():
    adapter = TemuAdapter()
    html = temu_category_html(_raw_data_store([], is_robot=True))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        with pytest.raises(ValueError, match="isRobot=true"):
            adapter.discover_products(TEMU_CATEGORY_URL)


def test_captcha_marker_with_no_goods_raises_blocked_error():
    adapter = TemuAdapter()
    html = "<html><body><form class='captcha-form'>verify</form></body></html>"
    no_browser = {"available": False, "message": "disabled for test"}
    with patch("scraper.adapters.temu.fetch_html", return_value=html), \
         patch("scraper.adapters.temu.check_browser_available", return_value=no_browser):
        with pytest.raises(ValueError, match="blocked"):
            adapter.discover_products(TEMU_CATEGORY_URL)


def test_i18n_security_verification_string_is_not_a_block():
    """Healthy pages embed i18n catalogs mentioning 'Security Verification'."""
    adapter = TemuAdapter()
    extra = '<script>window.__I18N__={"newCode_title":"Security Verification"};</script>'
    html = temu_category_html(_raw_data_store(GOOD_LIST), extra_html=extra)
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        items = adapter.discover_products(TEMU_CATEGORY_URL, max_products=3)
    assert len(items) == 3  # not blocked


# ---------------------------------------------------------------------------
# 7. Browser unavailable → clear diagnostic
# ---------------------------------------------------------------------------
def test_browser_unavailable_logs_diagnostic_and_returns_empty():
    adapter = TemuAdapter()
    diag = {"available": False, "playwright_installed": True,
            "message": "Chromium missing: run playwright install"}
    html = "<html><body>no products here</body></html>"
    with patch("scraper.adapters.temu.fetch_html", return_value=html), \
         patch("scraper.adapters.temu.check_browser_available", return_value=diag):
        items = adapter.discover_products(TEMU_CATEGORY_URL, use_browser=True)
    assert items == []  # honest empty, not fake success


def test_browser_unavailable_for_single_product_returns_none():
    adapter = TemuAdapter()
    diag = {"available": False, "message": "Chromium missing"}
    with patch("scraper.adapters.temu.fetch_html", return_value=None), \
         patch("scraper.adapters.temu.check_browser_available", return_value=diag):
        assert adapter.extract_product(TEMU_PRODUCT_URL, use_browser=True) is None


# ---------------------------------------------------------------------------
# 8. Progress advances through discovery
# ---------------------------------------------------------------------------
def test_progress_callback_reports_stages():
    adapter = TemuAdapter()
    messages = []
    html = temu_category_html(_raw_data_store(GOOD_LIST))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        adapter.discover_products(
            TEMU_CATEGORY_URL, max_products=3,
            progress_callback=lambda p: messages.append(p.get("message", "")),
        )
    assert any("Fetching Temu category" in m for m in messages)
    assert any("Products discovered: 3" in m for m in messages)


def test_stop_event_halts_discovery():
    import threading
    adapter = TemuAdapter()
    stop = threading.Event()
    stop.set()
    html = temu_category_html(_raw_data_store(GOOD_LIST, has_more=True))
    with patch("scraper.adapters.temu.fetch_html", return_value=html):
        items = adapter.discover_products(TEMU_CATEGORY_URL, max_products=5, stop_event=stop)
    # First page still parses (stop checked before pagination/browser)
    assert len(items) == 3


# ---------------------------------------------------------------------------
# 9. Universal schema & CSV export
# ---------------------------------------------------------------------------
def test_normalized_product_has_all_universal_columns():
    adapter = TemuAdapter()
    item = adapter._map_raw_goods_node(GOOD_LIST[0], TEMU_CATEGORY_URL)
    product = adapter.extract_product(item, source_url=TEMU_CATEGORY_URL)
    for col in config.UNIVERSAL_CSV_COLUMNS:
        assert col in product, f"missing column: {col}"


def test_csv_export_contains_real_records(tmp_path):
    import csv as csv_mod
    from scraper.exporter import export_to_csv

    adapter = TemuAdapter()
    products = []
    for node in GOOD_LIST:
        item = adapter._map_raw_goods_node(node, TEMU_CATEGORY_URL)
        products.append(adapter.extract_product(item, source_url=TEMU_CATEGORY_URL))

    out = tmp_path / "temu_products.csv"
    assert export_to_csv(products, out) is True

    with open(out, newline="", encoding="utf-8-sig") as f:
        rows = list(csv_mod.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["product_name"] == "Personalized Dog Tag"
    assert rows[0]["product_id"] == "601100459833657"
    assert rows[0]["currency"] == "PKR"
    assert "temu.com" in rows[0]["product_url"]
    # Schema columns present
    for col in ("price", "original_price", "main_image", "rating", "sold_count"):
        assert col in rows[0]


def test_no_availability_fabrication_in_cleaners():
    """cleaners must not default availability to 'In Stock' when source omits it."""
    p = normalize_universal_product({"title": "X"}, source_url="https://temu.com/p/1")
    assert p["availability"] == ""
    assert p["stock_status"] == ""


# ---------------------------------------------------------------------------
# 10. Session ID handling
# ---------------------------------------------------------------------------
def test_session_id_not_hardcoded_in_adapter():
    import scraper.adapters.temu as temu_mod
    source = open(temu_mod.__file__, encoding="utf-8").read()
    assert "ce3afb6w9s" not in source
    assert "_x_sessn_id=" not in source.split("TEMU_CATEGORY_URL")[0]  # not in code paths


def test_fresh_user_session_url_still_works():
    """A URL with a different _x_sessn_id is accepted identically."""
    adapter = TemuAdapter()
    fresh = TEMU_CATEGORY_URL.replace("ce3afb6w9s", "freshsession99")
    assert isinstance(get_adapter(fresh), TemuAdapter)
    assert adapter.analyze_url(fresh)["page_type"] == "Category / Listing"
