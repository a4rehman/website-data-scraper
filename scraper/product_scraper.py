import csv
import datetime
from typing import Dict, Any, Optional, Tuple
import config
from scraper.parser import parse_product_data
from scraper.utils import logger, fetch_json

class ProductScraper:
    """Scrapes and processes individual product details cleanly."""

    def __init__(self, collections_map: Optional[Dict[str, str]] = None):
        self.collections_map = collections_map or {}
        self.failed_products_path = config.FAILED_PRODUCTS_PATH
        self._ensure_failed_log_header()

    def _ensure_failed_log_header(self):
        """Ensures the failed_products.csv file exists with header."""
        if not self.failed_products_path.exists():
            with open(self.failed_products_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["product_url", "error", "timestamp"])

    def log_failure(self, product_url: str, error_msg: str):
        """Logs a failed product URL and error to failed_products.csv and scraper.log."""
        logger.error(f"Failed product: {product_url} | Error: {error_msg}")
        timestamp = datetime.datetime.now().isoformat()
        try:
            with open(self.failed_products_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([product_url, error_msg, timestamp])
        except Exception as e:
            logger.error(f"Could not write to failed_products.csv: {e}")

    def process_raw_product(self, raw_product: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Safely processes a raw product dictionary and returns (parsed_product_dict, error_msg).
        """
        product_url = f"{config.BASE_URL}/products/{raw_product.get('handle', '')}"
        try:
            parsed = parse_product_data(raw_product, self.collections_map)
            return parsed, None
        except Exception as e:
            err = str(e)
            self.log_failure(product_url, err)
            return None, err
