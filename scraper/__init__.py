from scraper.engine import UniversalScrapingEngine
from scraper.discovery import CatalogDiscovery
from scraper.cleaners import (
    clean_html_description,
    normalize_price,
    normalize_url,
    calculate_discount_percentage,
    process_images,
    normalize_universal_product,
    deduplicate_products,
)
from scraper.exporter import (
    export_to_csv,
    export_to_json,
    export_raw_to_csv,
    load_progress,
    save_progress,
    generate_quality_report,
)
from scraper.utils import logger, fetch_html, fetch_json, validate_safe_url
from scraper.ai_processor import summarize_scraped_data_with_ai, parse_ai_json_response
import scraper.adapters as adapters

__all__ = [
    "UniversalScrapingEngine",
    "CatalogDiscovery",
    "clean_html_description",
    "normalize_price",
    "normalize_url",
    "calculate_discount_percentage",
    "process_images",
    "normalize_universal_product",
    "deduplicate_products",
    "export_to_csv",
    "export_to_json",
    "export_raw_to_csv",
    "load_progress",
    "save_progress",
    "generate_quality_report",
    "logger",
    "fetch_html",
    "fetch_json",
    "validate_safe_url",
    "summarize_scraped_data_with_ai",
    "parse_ai_json_response",
    "adapters",
]
