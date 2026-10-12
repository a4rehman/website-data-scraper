"""
Optional live smoke test (Phase 7).

Run ONLY when explicitly requested:

    RUN_LIVE_SMOKE=1 python -m pytest tests/test_live_smoke.py -v

Hits the real https://tehzeeblibas.com/collections/all page with a 5-product
cap and asserts real (non-fabricated) data comes back. Skipped by default so
CI and offline runs stay hermetic.
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
