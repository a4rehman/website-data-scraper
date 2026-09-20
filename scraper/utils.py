import logging
import time
import requests
from typing import Optional, Dict, Any
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

def fetch_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Fetches JSON content from a URL with retry logic and exponential backoff.
    """
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
        except Exception as e:
            logger.warning(f"Fetch error on attempt {attempt}/{config.MAX_RETRIES} for {url}: {e}")
            time.sleep(config.REQUEST_DELAY * attempt)

    logger.error(f"Failed to fetch JSON after {config.MAX_RETRIES} attempts: {url}")
    return None

def fetch_html(url: str) -> Optional[str]:
    """
    Fetches HTML content from a URL with retry logic.
    """
    headers = {"User-Agent": config.USER_AGENT}
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            time.sleep(config.REQUEST_DELAY)
            response = requests.get(url, headers=headers, timeout=config.TIMEOUT)
            if response.status_code == 200:
                return response.text
            else:
                logger.warning(f"HTTP {response.status_code} on attempt {attempt} for {url}")
        except Exception as e:
            logger.warning(f"Fetch error on attempt {attempt}/{config.MAX_RETRIES} for {url}: {e}")
            time.sleep(config.REQUEST_DELAY * attempt)

    logger.error(f"Failed to fetch HTML after {config.MAX_RETRIES} attempts: {url}")
    return None
