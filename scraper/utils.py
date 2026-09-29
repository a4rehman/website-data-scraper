import ipaddress
import logging
import socket
import time
import os
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

def fetch_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Fetches JSON content from a URL with retry logic, SSRF checks, and exponential backoff.
    SSL verification is always enforced. Set ALLOW_INSECURE_SSL=true to disable (not recommended).
    """
    is_safe, reason = validate_safe_url(url)
    if not is_safe:
        logger.warning(f"Blocked JSON fetch for security/validation: {reason}")
        return None

    allow_insecure = os.getenv("ALLOW_INSECURE_SSL", "false").lower() == "true"
    
    headers = {"User-Agent": config.USER_AGENT}
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            time.sleep(config.REQUEST_DELAY)
            response = requests.get(url, params=params, headers=headers, timeout=config.TIMEOUT, verify=not allow_insecure, stream=True)
            
            # Check response size before reading
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_JSON_SIZE:
                logger.warning(f"Response size {content_length} exceeds MAX_JSON_SIZE ({MAX_JSON_SIZE}) for {url}")
                response.close()
                return None
            
            # Read with size limit
            content = b""
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > MAX_JSON_SIZE:
                    logger.warning(f"Response size exceeds MAX_JSON_SIZE ({MAX_JSON_SIZE}) for {url}")
                    response.close()
                    return None
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = config.REQUEST_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited (429). Retrying in {wait_time:.1f}s for {url}")
                time.sleep(wait_time)
            else:
                logger.warning(f"HTTP {response.status_code} on attempt {attempt} for {url}")
        except requests.exceptions.SSLError as e:
            logger.warning(f"SSL certificate verification failed for {url}: {e}")
            if allow_insecure:
                logger.warning("ALLOW_INSECURE_SSL=true set, retrying without verification...")
                try:
                    response = requests.get(url, params=params, headers=headers, timeout=config.TIMEOUT, verify=False, stream=True)
                    content = b""
                    for chunk in response.iter_content(chunk_size=8192):
                        content += chunk
                        if len(content) > MAX_JSON_SIZE:
                            logger.warning(f"Response size exceeds MAX_JSON_SIZE ({MAX_JSON_SIZE}) for {url}")
                            response.close()
                            return None
                    if response.status_code == 200:
                        return response.json()
                except Exception as ssl_err:
                    logger.error(f"SSL fallback failed for {url}: {ssl_err}")
                    break
            else:
                logger.error("SSL verification failed. Set ALLOW_INSECURE_SSL=true to bypass (not recommended for production).")
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
    Fetches HTML content from a URL with retry logic and SSRF validation.
    SSL verification is always enforced. Set ALLOW_INSECURE_SSL=true to disable (not recommended).
    """
    is_safe, reason = validate_safe_url(url)
    if not is_safe:
        logger.warning(f"Blocked HTML fetch for security/validation: {reason}")
        return None

    allow_insecure = os.getenv("ALLOW_INSECURE_SSL", "false").lower() == "true"
    
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            time.sleep(config.REQUEST_DELAY)
            response = requests.get(url, headers=headers, timeout=config.TIMEOUT, verify=not allow_insecure, stream=True)
            
            # Check response size before reading
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_HTML_SIZE:
                logger.warning(f"Response size {content_length} exceeds MAX_HTML_SIZE ({MAX_HTML_SIZE}) for {url}")
                response.close()
                return None
            
            # Read with size limit
            content = b""
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > MAX_HTML_SIZE:
                    logger.warning(f"Response size exceeds MAX_HTML_SIZE ({MAX_HTML_SIZE}) for {url}")
                    response.close()
                    return None
            
            if response.status_code == 200:
                # Force UTF-8 if encoding is ambiguous
                if response.encoding is None or response.encoding == "ISO-8859-1":
                    response.encoding = response.apparent_encoding or "utf-8"
                return response.text
            else:
                logger.warning(f"HTTP {response.status_code} on attempt {attempt} for {url}")
        except requests.exceptions.SSLError as e:
            logger.warning(f"SSL certificate verification failed for {url}: {e}")
            if allow_insecure:
                logger.warning("ALLOW_INSECURE_SSL=true set, retrying without verification...")
                try:
                    response = requests.get(url, headers=headers, timeout=config.TIMEOUT, verify=False, stream=True)
                    content = b""
                    for chunk in response.iter_content(chunk_size=8192):
                        content += chunk
                        if len(content) > MAX_HTML_SIZE:
                            logger.warning(f"Response size exceeds MAX_HTML_SIZE ({MAX_HTML_SIZE}) for {url}")
                            response.close()
                            return None
                    if response.status_code == 200:
                        return response.text
                except Exception as ssl_err:
                    logger.error(f"SSL fallback failed for {url}: {ssl_err}")
                    break
            else:
                logger.error("SSL verification failed. Set ALLOW_INSECURE_SSL=true to bypass (not recommended for production).")
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
