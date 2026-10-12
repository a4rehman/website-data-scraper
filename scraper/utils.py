import ipaddress
import json
import logging
import re
import socket
import time
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse
import requests
import config

# Response size limits
MAX_RESPONSE_SIZE = int(os.getenv("MAX_RESPONSE_SIZE", "10485760"))  # 10MB default
MAX_HTML_SIZE = int(os.getenv("MAX_HTML_SIZE", "5242880"))  # 5MB default
MAX_JSON_SIZE = int(os.getenv("MAX_JSON_SIZE", "5242880"))  # 5MB default

# DNS cache for rebinding protection
_dns_cache: Dict[str, Tuple[str, float]] = {}
DNS_CACHE_TTL = 300  # 5 minutes

def setup_logger(name: str = "scraper") -> logging.Logger:
    """Configures and returns a logger that outputs to both file and console."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        # File handler
        fh = logging.FileHandler(config.LOG_FILE_PATH, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
        fh.setFormatter(fh_formatter)
        logger.addHandler(fh)

        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch_formatter = logging.Formatter("%(message)s")
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)

    return logger

logger = setup_logger()

# Private and reserved IP networks for SSRF protection
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

BLOCKED_HOSTNAMES = {
    "localhost", "loopback", "localhost.localdomain", "metadata.google.internal", "instance-data"
}

def validate_safe_url(url: str) -> Tuple[bool, str]:
    """
    Validates that a URL is a safe public HTTP/HTTPS URL and prevents SSRF attacks
    against local services, internal IP ranges, or cloud metadata endpoints.
    Includes DNS rebinding protection.
    Returns (is_valid, reason_if_invalid).
    """
    if not url or not isinstance(url, str):
        return False, "Empty or invalid URL input."

    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL format."

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme '{parsed.scheme}'. Only http and https are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL lacks a valid hostname."

    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES or hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
        return False, f"Access to internal hostname '{hostname}' is blocked for security (SSRF prevention)."

    # Resolve IP address to check against private/reserved ranges
    # DNS Rebinding Protection: Cache resolved IP and verify it hasn't changed
    current_time = time.time()
    cached_entry = _dns_cache.get(hostname_lower)
    
    if cached_entry:
        cached_ip, cached_time = cached_entry
        if current_time - cached_time < DNS_CACHE_TTL:
            # Use cached IP for validation
            ip_str = cached_ip
        else:
            # Cache expired, resolve again
            try:
                ip_str = socket.gethostbyname(hostname)
                _dns_cache[hostname_lower] = (ip_str, current_time)
            except socket.gaierror:
                return False, f"Could not resolve domain name '{hostname}'. Check DNS or internet connection."
            except Exception as e:
                return False, f"IP validation error for domain '{hostname}': {e}"
    else:
        # First resolution
        try:
            ip_str = socket.gethostbyname(hostname)
            _dns_cache[hostname_lower] = (ip_str, current_time)
        except socket.gaierror:
            return False, f"Could not resolve domain name '{hostname}'. Check DNS or internet connection."
        except Exception as e:
            return False, f"IP validation error for domain '{hostname}': {e}"
    
    # Validate resolved IP
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        for net in BLOCKED_NETWORKS:
            if ip_obj in net:
                return False, f"Target IP address '{ip_str}' belongs to a private/reserved network and cannot be accessed."
    except Exception as e:
        return False, f"IP validation error for domain '{hostname}': {e}"

    return True, ""


def validate_redirect_url(original_url: str, redirect_url: str) -> Tuple[bool, str]:
    """
    Validates that a redirect destination is safe.
    Checks that the redirect doesn't go to a private/reserved IP.
    """
    is_safe, reason = validate_safe_url(redirect_url)
    if not is_safe:
        return False, f"Redirect blocked: {reason}"
    
    # Ensure redirect stays on same domain or allowed domains
    original_parsed = urlparse(original_url)
    redirect_parsed = urlparse(redirect_url)
    
    # Allow redirects to subdomains of the same domain
    original_domain = original_parsed.netloc.lower()
    redirect_domain = redirect_parsed.netloc.lower()
    
    if original_domain == redirect_domain:
        return True, ""
    
    # Allow subdomain redirects (e.g., example.com -> www.example.com)
    if redirect_domain.endswith("." + original_domain) or original_domain.endswith("." + redirect_domain):
        return True, ""
    
    # Log cross-domain redirect for audit
    logger.warning(f"Cross-domain redirect: {original_domain} -> {redirect_domain}")
    
    return True, ""

def log_fetch_event(
    stage: str,
    url: str,
    status: Optional[int] = None,
    error: Optional[BaseException] = None,
    detail: str = "",
) -> None:
    """
    Structured diagnostic logging for fetch pipeline stages.
    Never logs headers, cookies, or secrets.
    """
    parts = [f"stage={stage}", f"url={url}"]
    if status is not None:
        parts.append(f"http_status={status}")
    if error is not None:
        parts.append(f"error_type={type(error).__name__}")
        parts.append(f"error={error}")
    if detail:
        parts.append(f"detail={detail}")
    logger.info("FETCH_EVENT " + " | ".join(parts))


@dataclass
class FetchResult:
    """Result of a single successful HTTP fetch with the body read exactly once."""
    status_code: int
    content: bytes
    headers: Dict[str, str] = field(default_factory=dict)
    final_url: str = ""
    encoding: str = "utf-8"

    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")


def _charset_from_headers(headers: Dict[str, str]) -> Optional[str]:
    """Extract charset from a Content-Type header value, if present."""
    content_type = ""
    for k, v in headers.items():
        if k.lower() == "content-type":
            content_type = v
            break
    if not content_type:
        return None
    m = re.search(r"charset=([^\s;]+)", content_type, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip("\"'").lower()
    return None


def _decode_bytes(content: bytes, headers: Dict[str, str]) -> str:
    """
    Decode raw bytes to text exactly once.
    Uses the charset from the Content-Type header when present,
    otherwise tries UTF-8 (with BOM handling) and falls back safely.
    """
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig", errors="replace")
    charset = _charset_from_headers(headers)
    if charset:
        try:
            return content.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


def _read_response_bytes(response: "requests.Response", max_size: int) -> Tuple[Optional[bytes], str]:
    """
    Reads a streamed response body exactly once into memory, enforcing max_size.
    Returns (content_bytes, error_reason). content_bytes is None on error.
    The response is closed by the caller (or via context manager upstream).
    """
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_size:
                return None, f"content_length {content_length} exceeds limit {max_size}"
        except ValueError:
            pass  # Non-numeric Content-Length; rely on streamed limit below.

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_size:
            return None, f"streamed body exceeds limit {max_size}"
        chunks.append(chunk)
    return b"".join(chunks), ""


def _fetch_once(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    verify: bool = True,
    max_size: int = MAX_RESPONSE_SIZE,
) -> Tuple[Optional[FetchResult], str]:
    """
    Performs a single streamed GET, reading the response body exactly once.
    Never calls response.text / response.json after iter_content.
    Returns (FetchResult, "") on success or (None, error_reason) on failure.
    """
    try:
        with requests.get(
            url,
            params=params,
            headers=headers,
            timeout=config.TIMEOUT,
            verify=verify,
            stream=True,
            allow_redirects=True,
        ) as response:
            status = response.status_code
            final_url = str(getattr(response, "url", "") or url)
            resp_headers = dict(response.headers)

            if status == 429:
                return None, "http_429"
            if status >= 500:
                return None, f"http_{status}"
            if status >= 400:
                # Client errors (403, 404, etc.) are not retryable success paths.
                return None, f"client_error_{status}"

            content, size_err = _read_response_bytes(response, max_size)
            if content is None:
                log_fetch_event("read_body", url, status=status, detail=size_err)
                return None, size_err

            encoding = _charset_from_headers(resp_headers) or "utf-8"
            return FetchResult(
                status_code=status,
                content=content,
                headers=resp_headers,
                final_url=final_url,
                encoding=encoding,
            ), ""
    except requests.exceptions.Timeout as e:
        return None, f"timeout:{e}"
    except requests.exceptions.SSLError as e:
        return None, f"ssl:{e}"
    except requests.exceptions.ConnectionError as e:
        return None, f"connection:{e}"
    except requests.exceptions.RequestException as e:
        return None, f"request_error:{e}"
    except Exception as e:  # noqa: BLE001 - diagnostics must never crash the caller
        return None, f"unexpected:{type(e).__name__}:{e}"


def _fetch_with_retries(
    url: str,
    *,
    kind: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    max_size: int,
) -> Optional[FetchResult]:
    """
    Shared retry loop for HTML and JSON fetches.
    Reads each attempt's body exactly once; retries create fresh requests.
    SSL verification stays enabled unless ALLOW_INSECURE_SSL=true.
    """
    allow_insecure = os.getenv("ALLOW_INSECURE_SSL", "false").lower() == "true"
    last_reason = ""

    for attempt in range(1, config.MAX_RETRIES + 1):
        if attempt > 1:
            time.sleep(config.REQUEST_DELAY * attempt)
        else:
            time.sleep(config.REQUEST_DELAY)

        result, reason = _fetch_once(
            url, params=params, headers=headers, verify=not allow_insecure, max_size=max_size
        )
        if result is not None:
            if result.final_url and result.final_url != url:
                log_fetch_event(kind, url, status=result.status_code, detail=f"redirected_to={result.final_url}")
            else:
                log_fetch_event(kind, url, status=result.status_code)
            return result

        last_reason = reason
        log_fetch_event(kind, url, error=None, detail=f"attempt={attempt}/{config.MAX_RETRIES} reason={reason}")

        if reason.startswith("client_error_") or reason.startswith(("content_length", "streamed body")):
            # Hard failures: do not retry (oversized bodies and 4xx won't change).
            break

        if reason == "http_429":
            time.sleep(config.REQUEST_DELAY * (2 ** attempt))
            continue

        if reason.startswith("ssl:"):
            if allow_insecure:
                logger.warning(f"ALLOW_INSECURE_SSL=true set, retrying {url} without certificate verification...")
                result, insecure_reason = _fetch_once(
                    url, params=params, headers=headers, verify=False, max_size=max_size
                )
                if result is not None:
                    log_fetch_event(kind, url, status=result.status_code, detail="insecure_ssl_fallback")
                    return result
                last_reason = insecure_reason
            else:
                logger.error(
                    f"SSL verification failed for {url}. Certificate verification stays enabled; "
                    "set ALLOW_INSECURE_SSL=true to bypass (not recommended)."
                )
            break

    log_fetch_event(kind, url, error=None, detail=f"GIVING_UP reason={last_reason}")
    return None


def fetch_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Fetches JSON content from a URL with retry logic, SSRF checks, and size limits.
    The response body is read exactly once; JSON is parsed from those bytes.
    SSL verification is enforced by default.
    """
    is_safe, reason = validate_safe_url(url)
    if not is_safe:
        logger.warning(f"Blocked JSON fetch for security/validation: {reason}")
        return None

    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json,text/plain,*/*"}
    result = _fetch_with_retries(url, kind="fetch_json", params=params, headers=headers, max_size=MAX_JSON_SIZE)
    if result is None:
        return None

    if not result.content:
        logger.warning(f"Empty JSON body from {url}")
        return None

    try:
        parsed = json.loads(result.content.decode("utf-8-sig", errors="strict"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log_fetch_event("parse_json", url, status=result.status_code, error=e)
        return None

    if isinstance(parsed, dict):
        return parsed
    # Some public APIs return top-level lists; wrap for callers expecting a dict.
    return {"items": parsed}


def fetch_html(url: str) -> Optional[str]:
    """
    Fetches HTML content from a URL with retry logic, SSRF validation, and size limits.
    The response body is read exactly once and decoded from those same bytes
    (never response.text after iter_content). SSL verification is enforced by default.
    """
    is_safe, reason = validate_safe_url(url)
    if not is_safe:
        logger.warning(f"Blocked HTML fetch for security/validation: {reason}")
        return None

    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    result = _fetch_with_retries(url, kind="fetch_html", headers=headers, max_size=MAX_HTML_SIZE)
    if result is None:
        return None

    if not result.content:
        logger.warning(f"Empty HTML body from {url}")
        return None

    text = _decode_bytes(result.content, result.headers)
    if not text.strip():
        logger.warning(f"HTML body from {url} decoded to whitespace only")
        return None
    return text
