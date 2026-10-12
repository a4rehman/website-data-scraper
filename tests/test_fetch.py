"""
Regression tests for the fetch layer (Phase 2).

Covers the production bug where streamed responses were consumed via
iter_content() and then read again via response.text / response.json(),
raising "The content for this response was already consumed".
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import requests

import config
from scraper.utils import (
    fetch_html,
    fetch_json,
    _decode_bytes,
    _charset_from_headers,
    MAX_HTML_SIZE,
    MAX_JSON_SIZE,
)


class FakeStreamedResponse:
    """
    Mimics requests.Response with stream=True semantics:
    once iter_content() has been consumed, .text and .json raise the same
    RuntimeError production saw. Our fetch code must never touch .text/.json
    after streaming — it must decode the bytes it already read.
    """

    def __init__(self, status_code=200, content=b"", headers=None, url=""):
        self.status_code = status_code
        self._content = content
        self.headers = headers or {}
        self.url = url
        self.encoding = None
        self.apparent_encoding = "utf-8"
        self._consumed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size=8192):
        if self._consumed:
            raise requests.exceptions.StreamConsumedError()
        self._consumed = True
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    @property
    def text(self):
        if self._consumed:
            raise RuntimeError("The content for this response was already consumed")
        return self._content.decode("utf-8", errors="replace")

    def json(self):
        if self._consumed:
            raise RuntimeError("The content for this response was already consumed")
        return json.loads(self._content)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fast_network(monkeypatch):
    """Eliminate network delays and DNS lookups in all fetch tests."""
    monkeypatch.setattr(config, "REQUEST_DELAY", 0)
    monkeypatch.setattr("scraper.utils.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "scraper.utils.validate_safe_url", lambda url: (True, "")
    )


def _mock_get(response):
    mock = MagicMock(return_value=response)
    return mock


# ---------------------------------------------------------------------------
# 1. Successful HTML fetch (regression: response-consumption error)
# ---------------------------------------------------------------------------
def test_fetch_html_success_no_consumption_error():
    html = b"<html><body><h1>Product Page</h1></body></html>"
    resp = FakeStreamedResponse(200, html, {"Content-Type": "text/html; charset=utf-8"})
    with patch("scraper.utils.requests.get", return_value=resp):
        result = fetch_html("https://example.com/products/item")
    assert result is not None
    assert "Product Page" in result


# ---------------------------------------------------------------------------
# 2. Empty HTML body returns None (not a fake product page)
# ---------------------------------------------------------------------------
def test_fetch_html_empty_body_returns_none():
    resp = FakeStreamedResponse(200, b"", {"Content-Type": "text/html"})
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_html("https://example.com/empty") is None


# ---------------------------------------------------------------------------
# 3. Streamed response must not be re-read after iter_content
#    (the exact production error)
# ---------------------------------------------------------------------------
def test_fetch_html_streamed_response_not_reread():
    html = b"<html><body>streamed</body></html>"
    resp = FakeStreamedResponse(200, html, {"Content-Type": "text/html"})
    with patch("scraper.utils.requests.get", return_value=resp):
        result = fetch_html("https://example.com/streamed")
    # If the implementation had called response.text after iter_content,
    # FakeStreamedResponse.text would have raised RuntimeError.
    assert result == "<html><body>streamed</body></html>"


# ---------------------------------------------------------------------------
# 4. Encoding detection: charset header, BOM, and UTF-8 fallback
# ---------------------------------------------------------------------------
def test_decode_bytes_respects_charset_header():
    content = "café".encode("latin-1")
    text = _decode_bytes(content, {"Content-Type": "text/html; charset=latin-1"})
    assert text == "café"


def test_decode_bytes_utf8_bom():
    content = b"\xef\xbb\xbfhello"
    assert _decode_bytes(content, {}) == "hello"


def test_decode_bytes_default_utf8():
    content = "héllo".encode("utf-8")
    assert _decode_bytes(content, {}) == "héllo"


def test_charset_from_headers():
    assert _charset_from_headers({"Content-Type": "text/html; charset=UTF-8"}) == "utf-8"
    assert _charset_from_headers({"content-type": "application/json; charset=utf-8"}) == "utf-8"
    assert _charset_from_headers({"Content-Type": "text/html"}) is None
    assert _charset_from_headers({}) is None


# ---------------------------------------------------------------------------
# 5. Oversized responses rejected (Content-Length and streamed)
# ---------------------------------------------------------------------------
def test_fetch_html_oversized_by_content_length():
    resp = FakeStreamedResponse(
        200, b"x" * 100, {"Content-Length": str(MAX_HTML_SIZE + 1)}
    )
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_html("https://example.com/huge") is None


def test_fetch_html_oversized_by_streaming():
    # No Content-Length header; body exceeds the limit while streaming.
    big = b"x" * (MAX_HTML_SIZE + 1024)
    resp = FakeStreamedResponse(200, big, {"Content-Type": "text/html"})
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_html("https://example.com/huge2") is None


# ---------------------------------------------------------------------------
# 6. HTTP errors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [403, 404])
def test_fetch_html_client_errors_return_none(status):
    resp = FakeStreamedResponse(status, b"denied", {})
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_html("https://example.com/blocked") is None


def test_fetch_html_5xx_retries_then_fails():
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        return FakeStreamedResponse(503, b"unavailable", {})

    with patch("scraper.utils.requests.get", side_effect=fake_get):
        assert fetch_html("https://example.com/down") is None
    assert calls["n"] == config.MAX_RETRIES  # retried, then gave up


def test_fetch_html_429_retries_then_succeeds():
    responses = [
        FakeStreamedResponse(429, b"slow down", {}),
        FakeStreamedResponse(200, b"<html>ok</html>", {"Content-Type": "text/html"}),
    ]
    with patch("scraper.utils.requests.get", side_effect=responses):
        result = fetch_html("https://example.com/rate")
    assert result == "<html>ok</html>"


# ---------------------------------------------------------------------------
# 7. Timeout / connection errors
# ---------------------------------------------------------------------------
def test_fetch_html_timeout_returns_none():
    with patch(
        "scraper.utils.requests.get",
        side_effect=requests.exceptions.Timeout("timed out"),
    ):
        assert fetch_html("https://example.com/slow") is None


def test_fetch_html_connection_error_returns_none():
    with patch(
        "scraper.utils.requests.get",
        side_effect=requests.exceptions.ConnectionError("refused"),
    ):
        assert fetch_html("https://example.com/down") is None


# ---------------------------------------------------------------------------
# 8. Retry success after a transient failure
# ---------------------------------------------------------------------------
def test_fetch_html_retry_success_after_5xx():
    responses = [
        FakeStreamedResponse(500, b"err", {}),
        FakeStreamedResponse(200, b"<html>recovered</html>", {"Content-Type": "text/html"}),
    ]
    with patch("scraper.utils.requests.get", side_effect=responses):
        result = fetch_html("https://example.com/flaky")
    assert result == "<html>recovered</html>"


# ---------------------------------------------------------------------------
# 9. JSON fetch: success, streaming safety, invalid JSON, list top-level
# ---------------------------------------------------------------------------
def test_fetch_json_success():
    payload = {"products": [{"id": 1, "title": "A"}]}
    body = json.dumps(payload).encode("utf-8")
    resp = FakeStreamedResponse(200, body, {"Content-Type": "application/json"})
    with patch("scraper.utils.requests.get", return_value=resp):
        result = fetch_json("https://example.com/products.json")
    assert result == payload


def test_fetch_json_streamed_not_reread():
    # Regression: response.json() after iter_content raised RuntimeError.
    payload = {"ok": True}
    resp = FakeStreamedResponse(
        200, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    with patch("scraper.utils.requests.get", return_value=resp):
        result = fetch_json("https://example.com/data.json")
    assert result == payload


def test_fetch_json_invalid_json_returns_none():
    resp = FakeStreamedResponse(200, b"not-json{{{", {"Content-Type": "application/json"})
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_json("https://example.com/bad.json") is None


def test_fetch_json_top_level_list_wrapped():
    body = json.dumps([1, 2, 3]).encode()
    resp = FakeStreamedResponse(200, body, {"Content-Type": "application/json"})
    with patch("scraper.utils.requests.get", return_value=resp):
        result = fetch_json("https://example.com/list.json")
    assert result == {"items": [1, 2, 3]}


def test_fetch_json_empty_body_returns_none():
    resp = FakeStreamedResponse(200, b"", {"Content-Type": "application/json"})
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_json("https://example.com/empty.json") is None


def test_fetch_json_oversized_rejected():
    resp = FakeStreamedResponse(
        200, b"x" * 10, {"Content-Length": str(MAX_JSON_SIZE + 1)}
    )
    with patch("scraper.utils.requests.get", return_value=resp):
        assert fetch_json("https://example.com/big.json") is None


# ---------------------------------------------------------------------------
# 10. SSL verification stays enabled by default
# ---------------------------------------------------------------------------
def test_ssl_error_does_not_disable_verification():
    with patch(
        "scraper.utils.requests.get",
        side_effect=requests.exceptions.SSLError("cert verify failed"),
    ) as mock_get:
        result = fetch_html("https://example.com/ssl")
    assert result is None
    # Every attempt must keep verify=True (default; allow_insecure unset).
    for call in mock_get.call_args_list:
        assert call.kwargs.get("verify", True) is True
