"""
Regression tests for live progress, terminal-state guarantees, and the
thread-safe job store (Phase 5).

Covers:
- JobStatusStore isolation / cancellation / terminal detection.
- Engine always writes a terminal state (Completed / Completed with errors /
  Cancelled / Failed) — including zero-product runs, crashes, and cancellation.
- Progress callbacks receive ordered state transitions.
- Partial results are preserved on cancellation and on mid-loop crash.
- Workers publish only to the job store (never Streamlit session state).
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import config
from scraper.engine import UniversalScrapingEngine
from scraper.job_store import (
    JobStatusStore,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_COMPLETED_WITH_ERRORS,
    STATE_DISCOVERING,
    STATE_EXTRACTING,
    STATE_EXPORTING,
    STATE_FAILED,
    STATE_FETCHING,
    TERMINAL_STATES,
)


class FakeAdapter:
    """Minimal adapter with controllable discovery/extraction behavior."""

    name = "Fake Store"

    def __init__(
        self,
        items: Optional[List[Any]] = None,
        extract_results: Optional[List[Any]] = None,
        raise_on_extract: Optional[Exception] = None,
        raise_on_discover: Optional[Exception] = None,
    ):
        self.items = items if items is not None else []
        self.extract_results = list(extract_results or [])
        self.raise_on_extract = raise_on_extract
        self.raise_on_discover = raise_on_discover
        self.extract_calls: List[Any] = []

    def analyze_url(self, url: str, html: Any = None) -> Dict[str, Any]:
        return {"platform": self.name, "page_type": "Category / Listing", "requires_js": False}

    def discover_products(self, url, max_products=None, use_browser=False,
                          stop_event=None, progress_callback=None, **kw):
        if self.raise_on_discover:
            raise self.raise_on_discover
        return list(self.items)

    def extract_product(self, item, source_url="", use_browser=False, **kw):
        self.extract_calls.append(item)
        if self.raise_on_extract:
            raise self.raise_on_extract
        if self.extract_results:
            return self.extract_results.pop(0)
        return None


@pytest.fixture(autouse=True)
def fast_and_isolated(monkeypatch, tmp_path):
    """No sleeps, no real network/DNS, per-test job dirs, clean job store."""
    monkeypatch.setattr(config, "REQUEST_DELAY", 0)
    monkeypatch.setattr("scraper.engine.time.sleep", lambda *_: None)
    monkeypatch.setattr("scraper.engine.fetch_html", lambda *a, **k: "<html>fixture</html>")
    # Tests use reserved fake domains; skip real DNS/SSRF resolution.
    monkeypatch.setattr("scraper.engine.validate_safe_url", lambda url: (True, ""))

    original_get_job_paths = config.get_job_paths

    def tmp_job_paths(job_id):
        d = tmp_path / f"jobs/{job_id}"
        d.mkdir(parents=True, exist_ok=True)
        return original_get_job_paths(job_id) if False else {
            "job_id": job_id,
            "job_dir": d,
            "csv": d / "products.csv",
            "json": d / "products.json",
            "report": d / "report.txt",
            "progress": d / "progress.json",
            "failed": d / "failed.csv",
            "logs": d / "scraper.log",
            "live_status": d / "live_status.json",
        }

    monkeypatch.setattr(config, "get_job_paths", tmp_job_paths)
    JobStatusStore._jobs.clear()
    JobStatusStore._cancel_events.clear()
    yield
    JobStatusStore._jobs.clear()
    JobStatusStore._cancel_events.clear()


def run_engine(adapter: FakeAdapter, url="https://shop.example.com/collections/all",
               **kwargs):
    import scraper.engine as engine_module
    from unittest.mock import patch

    with patch.object(engine_module, "get_adapter", lambda *a, **k: adapter):
        engine = UniversalScrapingEngine()
        return engine.scrape_url(url, **kwargs)


# ---------------------------------------------------------------------------
# JobStatusStore basics
# ---------------------------------------------------------------------------
def test_job_store_create_update_get_isolated():
    JobStatusStore.create_job("job-a", url="https://a.example")
    JobStatusStore.create_job("job-b", url="https://b.example")
    JobStatusStore.update("job-a", state=STATE_EXTRACTING, extracted=3)

    a = JobStatusStore.get("job-a")
    b = JobStatusStore.get("job-b")
    assert a["state"] == STATE_EXTRACTING and a["extracted"] == 3
    assert b["state"] == "Queued" and b["extracted"] == 0
    # get() returns a snapshot, not the live dict
    a["extracted"] = 999
    assert JobStatusStore.get("job-a")["extracted"] == 3


def test_job_store_unknown_job_is_not_terminal():
    assert JobStatusStore.is_terminal("never-created") is False


def test_job_store_cancel_flag():
    JobStatusStore.create_job("c1")
    assert JobStatusStore.is_cancel_requested("c1") is False
    JobStatusStore.request_cancel("c1")
    assert JobStatusStore.is_cancel_requested("c1") is True
    assert JobStatusStore.cancel_event("c1").is_set() is True


def test_job_store_terminal_states():
    for state in TERMINAL_STATES:
        JobStatusStore.create_job(f"t-{state}")
        JobStatusStore.update(f"t-{state}", state=state)
        assert JobStatusStore.is_terminal(f"t-{state}") is True, state


# ---------------------------------------------------------------------------
# Terminal-state guarantees
# ---------------------------------------------------------------------------
def test_zero_discovery_writes_terminal_failed():
    adapter = FakeAdapter(items=[])
    result = run_engine(adapter)
    assert result["success"] is False
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == STATE_FAILED
    assert stored["state"] in TERMINAL_STATES
    assert "No products discovered" in stored["status"]


def test_successful_run_reaches_completed():
    adapter = FakeAdapter(
        items=["https://shop.example.com/products/a", "https://shop.example.com/products/b"],
        extract_results=[
            {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"},
            {"product_name": "B", "price": 2.0, "product_url": "https://shop.example.com/products/b"},
        ],
    )
    result = run_engine(adapter)
    assert result["success"] is True
    assert result["state"] == STATE_COMPLETED
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == STATE_COMPLETED
    assert stored["extracted"] == 2


def test_all_extractions_failing_yields_failed_not_completed():
    # discover 2 items, extraction returns None for both → 0 products → Failed
    adapter = FakeAdapter(
        items=["https://shop.example.com/products/a", "https://shop.example.com/products/b"],
        extract_results=[None, None],
    )
    result = run_engine(adapter)
    assert result["success"] is False
    assert result["state"] == STATE_FAILED
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == STATE_FAILED
    assert "0 products" in stored["status"]


def test_partial_failures_yield_completed_with_errors():
    adapter = FakeAdapter(
        items=["https://shop.example.com/products/a", "https://shop.example.com/products/b"],
        extract_results=[
            {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"},
            None,
        ],
    )
    result = run_engine(adapter)
    assert result["success"] is True
    assert result["state"] == STATE_COMPLETED_WITH_ERRORS
    assert JobStatusStore.get(result["job_id"])["state"] == STATE_COMPLETED_WITH_ERRORS


def test_invalid_url_writes_terminal_failed():
    engine = UniversalScrapingEngine()
    result = engine.scrape_url("")
    assert result["success"] is False
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == STATE_FAILED
    assert stored["state"] in TERMINAL_STATES


def test_discovery_exception_writes_terminal_failed():
    adapter = FakeAdapter(raise_on_discover=RuntimeError("boom"))
    result = run_engine(adapter)
    assert result["success"] is False
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == STATE_FAILED
    assert "discovery failed" in stored["status"].lower()


def test_crash_during_extraction_writes_terminal_failed_with_partials():
    """An exception from extract_product is caught per-item; the run still
    reaches a terminal state (Failed, since 0 products succeed)."""
    adapter = FakeAdapter(
        items=[
            "https://shop.example.com/products/a",
            "https://shop.example.com/products/b",
        ],
        raise_on_extract=RuntimeError("extraction exploded"),
    )
    result = run_engine(adapter)
    assert result["state"] == STATE_FAILED
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] == STATE_FAILED
    assert stored["failed"] == 2


def test_export_stage_crash_marks_failed_and_keeps_partial_exports(monkeypatch):
    """An unexpected Exception in the export stage (dedup/export) must yield
    a Failed terminal state with partial results preserved, not a hang or an
    uncaught exception."""
    adapter = FakeAdapter(
        items=[f"https://shop.example.com/products/{c}" for c in "ab"],
    )
    adapter.extract_results = [
        {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"},
        {"product_name": "B", "price": 2.0, "product_url": "https://shop.example.com/products/b"},
    ]

    import scraper.engine as engine_module

    def dedup_crash(products):
        raise RuntimeError("unexpected engine crash")

    monkeypatch.setattr(engine_module, "deduplicate_products", dedup_crash)

    result = run_engine(adapter)
    assert result["state"] == STATE_FAILED
    assert result["success"] is False
    # Partial results preserved in the return payload and CSV
    assert len(result["products"]) == 2
    assert result["csv_path"].exists()
    stored = JobStatusStore.get(result["job_id"])
    assert stored["state"] in TERMINAL_STATES


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------
def test_cancel_before_discovery_writes_cancelled():
    adapter = FakeAdapter(items=["https://shop.example.com/products/a"])

    engine = UniversalScrapingEngine()
    job_id = "cancel-pre"
    JobStatusStore.create_job(job_id, url="https://shop.example.com/collections/all")
    JobStatusStore.request_cancel(job_id)

    result = engine.scrape_url(
        "https://shop.example.com/collections/all", job_id=job_id
    )
    assert result.get("cancelled") is True
    stored = JobStatusStore.get(job_id)
    assert stored["state"] == STATE_CANCELLED
    assert stored["state"] in TERMINAL_STATES


def test_cancel_mid_extraction_preserves_partials_and_terminal_cancelled():
    adapter = FakeAdapter(
        items=[f"https://shop.example.com/products/{c}" for c in "abcd"],
    )

    job_id = "cancel-mid"
    JobStatusStore.create_job(job_id, url="https://shop.example.com/collections/all")

    extracted_first = {"done": False}

    def extract(item, **kw):
        result = {
            "product_name": f"P-{item}",
            "price": 1.0,
            "product_url": item if isinstance(item, str) else "https://x",
        }
        if not extracted_first["done"]:
            extracted_first["done"] = True
            # Request cancellation after the first successful extraction
            JobStatusStore.request_cancel(job_id)
        return result

    adapter.extract_product = extract

    result = run_engine(adapter, job_id=job_id)
    assert result.get("cancelled") is True
    assert result["state"] == STATE_CANCELLED
    # Partial results preserved: at least 1 product exported
    assert len(result["products"]) >= 1
    assert result["csv_path"].exists()
    stored = JobStatusStore.get(job_id)
    assert stored["state"] == STATE_CANCELLED
    # Partial counts surfaced to the UI
    assert stored["extracted"] >= 1


def test_stop_event_from_caller_stops_run():
    adapter = FakeAdapter(
        items=[f"https://shop.example.com/products/{c}" for c in "abc"],
        extract_results=[
            {"product_name": f"P{i}", "price": float(i), "product_url": f"https://shop.example.com/products/{c}"}
            for i, c in enumerate("abc", start=1)
        ],
    )
    stop = threading.Event()

    def extract_and_stop(item, **kw):
        stop.set()
        return adapter.extract_results.pop(0) if adapter.extract_results else None

    adapter.extract_product = extract_and_stop

    result = run_engine(adapter, stop_event=stop)
    assert result.get("cancelled") is True
    assert result["state"] == STATE_CANCELLED


# ---------------------------------------------------------------------------
# Progress callbacks and state ordering
# ---------------------------------------------------------------------------
def test_progress_callback_receives_ordered_states():
    states: List[str] = []
    adapter = FakeAdapter(
        items=["https://shop.example.com/products/a"],
        extract_results=[
            {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"}
        ],
    )
    result = run_engine(
        adapter,
        progress_callback=lambda payload: states.append(payload.get("state")),
    )
    assert result["state"] == STATE_COMPLETED
    # Required lifecycle ordering
    assert states.index(STATE_FETCHING) < states.index(STATE_DISCOVERING)
    assert states.index(STATE_DISCOVERING) < states.index(STATE_EXTRACTING)
    assert states.index(STATE_EXTRACTING) < states.index(STATE_EXPORTING)
    assert states[-1] in TERMINAL_STATES
    # Terminal state reported both via callback and job store
    assert states[-1] == JobStatusStore.get(result["job_id"])["state"]


def test_progress_callback_exception_does_not_break_run():
    def bad_callback(payload):
        raise ValueError("callback exploded")

    adapter = FakeAdapter(
        items=["https://shop.example.com/products/a"],
        extract_results=[
            {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"}
        ],
    )
    result = run_engine(adapter, progress_callback=bad_callback)
    assert result["success"] is True
    assert result["state"] == STATE_COMPLETED


def test_live_status_file_written_and_terminal():
    adapter = FakeAdapter(
        items=["https://shop.example.com/products/a"],
        extract_results=[
            {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"}
        ],
    )
    result = run_engine(adapter)
    live_path = result["job_paths"]["job_dir"] / "live_status.json"
    assert live_path.exists()
    data = json.loads(live_path.read_text(encoding="utf-8"))
    assert data["state"] in TERMINAL_STATES
    assert data["job_id"] == result["job_id"]


def test_per_item_progress_fields_present():
    payloads: List[Dict[str, Any]] = []
    adapter = FakeAdapter(
        items=[f"https://shop.example.com/products/{c}" for c in "ab"],
        extract_results=[
            {"product_name": "A", "price": 1.0, "product_url": "https://shop.example.com/products/a"},
            {"product_name": "B", "price": 2.0, "product_url": "https://shop.example.com/products/b"},
        ],
    )
    run_engine(adapter, progress_callback=payloads.append)

    extracting = [p for p in payloads if p.get("state") == STATE_EXTRACTING]
    assert extracting, "expected per-item extraction progress events"
    for p in extracting:
        assert "progress_count" in p
        assert "total_target" in p
        assert p["total_target"] == 2


# ---------------------------------------------------------------------------
# Worker isolation: engine must not import/touch Streamlit
# ---------------------------------------------------------------------------
def test_engine_module_does_not_import_streamlit():
    import scraper.engine as engine_module
    import scraper.job_store as job_store_module

    assert "streamlit" not in engine_module.__dict__
    assert "streamlit" not in job_store_module.__dict__
    source = Path(engine_module.__file__).read_text(encoding="utf-8")
    assert "import streamlit" not in source
    source2 = Path(job_store_module.__file__).read_text(encoding="utf-8")
    assert "import streamlit" not in source2


def test_concurrent_job_updates_are_thread_safe():
    JobStatusStore.create_job("conc")
    errors = []

    def worker(n):
        try:
            for i in range(200):
                JobStatusStore.update("conc", extracted=i, progress_count=i)
                JobStatusStore.append_log("conc", f"w{n}-{i}")
                JobStatusStore.get("conc")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    snapshot = JobStatusStore.get("conc")
    assert isinstance(snapshot["extracted"], int)
    assert len(snapshot["logs"]) <= 200
