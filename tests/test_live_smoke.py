"""
Optional live smoke test (Phase 7).

Run ONLY when explicitly requested:

    RUN_LIVE_SMOKE=1 python -m pytest tests/test_live_smoke.py -v

Hits real Tehzeeb and Temu category pages with a 5-product cap and asserts
real (non-fabricated) data comes back. Skipped by default so CI and offline
runs stay hermetic.
"""

import os

import pytest

LIVE_ENABLED = os.environ.get("RUN_LIVE_SMOKE", "").strip().lower() in {"1", "true", "yes"}


pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="Live network smoke test disabled (set RUN_LIVE_SMOKE=1 to enable)",
)


def test_live_tehzeeb_five_products():
    from scraper.engine import UniversalScrapingEngine
    from scraper.job_store import JobStatusStore, TERMINAL_STATES

    engine = UniversalScrapingEngine()
    result = engine.scrape_url(
        "https://tehzeeblibas.com/collections/all",
        max_products=5,
        request_delay=0.5,
        use_browser=False,
    )

    # Terminal state always present
    assert result["state"] in TERMINAL_STATES
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == result["state"]

    # Real data, no fabrication
    products = result["products"]
    assert len(products) >= 1, f"expected real products, got 0 (state={result['state']}, msg={result.get('message')})"
    for p in products:
        assert p.get("product_name"), "product must have a real name"
        assert p.get("product_url", "").startswith("https://")
        assert "tehzeeblibas.com" in p.get("product_url", "")
        # No fake products: URL must be a product path, not the collection page
        assert "/collections/all" not in p.get("product_url", "")

    # At least one product should carry a real price
    priced = [p for p in products if p.get("price")]
    assert priced, "expected at least one product with a price"

    # Exports exist on disk
    assert result["csv_path"].exists()
    assert result["json_path"].exists()


def test_live_temu_five_products():
    from scraper.engine import UniversalScrapingEngine
    from scraper.job_store import JobStatusStore, TERMINAL_STATES

    engine = UniversalScrapingEngine()
    result = engine.scrape_url(
        "https://www.temu.com/category.html?opt_id=-42&opt_level=1&title=Personalized%20Products"
        "&_x_enter_scene_type=cate_tab&show_search_type=0&leaf_type=son&refer_page_el_sn=200055"
        "&refer_page_name=home&refer_page_id=10005_1791845952665_jszwnjdxhr&refer_page_sn=10005",
        max_products=5,
        request_delay=0.5,
        use_browser=False,
    )

    assert result["state"] in TERMINAL_STATES
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == result["state"]

    products = result["products"]
    assert len(products) >= 1, f"expected real products, got 0 (state={result['state']}, msg={result.get('message')})"
    for p in products:
        assert p.get("product_name"), "product must have a real name"
        assert p.get("product_url", "").startswith("https://")
        assert "temu.com" in p.get("product_url", "")
        # No fake products: URL must be a product path, not the category page
        assert "/category.html" not in p.get("product_url", "")
        assert "goods_id" in p.get("product_url", "") or "category.html" not in p.get("product_url", "")

    priced = [p for p in products if p.get("price")]
    assert priced, "expected at least one product with a price"

    assert result["csv_path"].exists()
    assert result["json_path"].exists()
