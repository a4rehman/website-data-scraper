import ipaddress
import logging
import socket
import time
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse
import requests
import config

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
    try:
        ip_str = socket.gethostbyname(hostname)
        ip_obj = ipaddress.ip_address(ip_str)

        for net in BLOCKED_NETWORKS:
            if ip_obj in net:
                return False, f"Target IP address '{ip_str}' belongs to a private/reserved network and cannot be accessed."
    except socket.gaierror:
        # DNS resolution failure
        return False, f"Could not resolve domain name '{hostname}'. Check DNS or internet connection."
    except Exception as e:
        return False, f"IP validation error for domain '{hostname}': {e}"

    return True, ""

def fetch_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Fetches JSON content from a URL with retry logic, SSRF checks, and exponential backoff.
    """
    is_safe, reason = validate_safe_url(url)
    if not is_safe:
        logger.warning(f"Blocked JSON fetch for security/validation: {reason}")
        return None

    headers = {"User-Agent": config.USER_AGENT}
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            time.sleep(config.REQUEST_DELAY)
            response = requests.get(url, params=params, headers=headers, timeout=config.TIMEOUT)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = config.REQUEST_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited (429). Retrying in {wait_time:.1f}s for {url}")
                time.sleep(wait_time)
            else:
                logger.warning(f"HTTP {response.status_code} on attempt {attempt} for {url}")
        except requests.exceptions.SSLError as e:
            logger.warning(f"SSL error for {url}: {e}")
            break
        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout ({config.TIMEOUT}s) on attempt {attempt}/{config.MAX_RETRIES} for {url}")
            time.sleep(config.REQUEST_DELAY * attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error for {url}: {e}")
            time.sleep(config.REQUEST_DELAY * attempt)
        except Exception as e:
            logger.warning(f"Fetch error on attempt {attempt}/{config.MAX_RETRIES} for {url}: {e}")
            time.sleep(config.REQUEST_DELAY * attempt)

    logger.error(f"Failed to fetch JSON after {config.MAX_RETRIES} attempts: {url}")
    return None

def fetch_html(url: str) -> Optional[str]:
    """
    Fetches HTML content from a URL with retry logic, SSL fallback, and SSRF validation.
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
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            time.sleep(config.REQUEST_DELAY)
            response = requests.get(url, headers=headers, timeout=config.TIMEOUT)
            if response.status_code == 200:
                # Force UTF-8 if encoding is ambiguous
                if response.encoding is None or response.encoding == "ISO-8859-1":
                    response.encoding = response.apparent_encoding or "utf-8"
                return response.text
            else:
                logger.warning(f"HTTP {response.status_code} on attempt {attempt} for {url}")
        except requests.exceptions.SSLError as e:
            logger.warning(f"SSL certificate issue for {url}: {e}. Retrying without verification fallback...")
            try:
                # Fallback for sites with self-signed / missing intermediate SSL certs
                response = requests.get(url, headers=headers, timeout=config.TIMEOUT, verify=False)
                if response.status_code == 200:
                    return response.text
            except Exception as ssl_err:
                logger.error(f"SSL fallback failed for {url}: {ssl_err}")
                break
        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout on attempt {attempt}/{config.MAX_RETRIES} for {url}")
            time.sleep(config.REQUEST_DELAY * attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error for {url}: {e}")
            time.sleep(config.REQUEST_DELAY * attempt)
        except Exception as e:
            logger.warning(f"Fetch error on attempt {attempt}/{config.MAX_RETRIES} for {url}: {e}")
            time.sleep(config.REQUEST_DELAY * attempt)

    logger.error(f"Failed to fetch HTML after {config.MAX_RETRIES} attempts: {url}")
    return None
