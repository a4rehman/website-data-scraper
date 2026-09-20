import csv
import json
from pathlib import Path
from typing import List, Dict, Any
import config
from scraper.utils import logger

CSV_COLUMNS = [
    "product_id",
    "sku",
    "product_name",
    "description",
    "brand",
    "category",
    "subcategory",
    "collection",
    "product_type",
    "product_url",
    "image_url",
    "additional_image_urls",
    "price",
    "sale_price",
    "original_price",
    "discount_percentage",
    "currency",
    "availability",
    "stock_status",
    "sizes",
    "colors",
    "variants",
    "material",
    "tags"
]

def export_to_csv(products: List[Dict[str, Any]], filepath: Path) -> bool:
    """
    Exports a list of processed product dictionaries to UTF-8 CSV format.
    """
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            for p in products:
                # Ensure all columns exist in dict
                row = {col: p.get(col, "") for col in CSV_COLUMNS}
                writer.writerow(row)
        logger.info(f"Successfully saved {len(products)} products to {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to export CSV to {filepath}: {e}")
        return False

def export_raw_to_csv(raw_products: List[Dict[str, Any]], filepath: Path) -> bool:
    """
    Exports raw product data as JSON strings per row for raw auditing.
    """
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["product_id", "handle", "title", "vendor", "product_type", "raw_json"])
            for p in raw_products:
                writer.writerow([
                    p.get("id", ""),
                    p.get("handle", ""),
                    p.get("title", ""),
                    p.get("vendor", ""),
                    p.get("product_type", ""),
                    json.dumps(p, ensure_ascii=False)
                ])
        logger.info(f"Successfully saved {len(raw_products)} raw products to {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to export raw CSV: {e}")
        return False

def load_progress() -> Dict[str, Any]:
    """
    Loads saved progress state from data/progress.json.
    """
    if config.PROGRESS_JSON_PATH.exists():
        try:
            with open(config.PROGRESS_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read progress.json: {e}")
    return {"scraped_ids": [], "scraped_products": [], "failed_count": 0}

def save_progress(scraped_ids: List[str], scraped_products: List[Dict[str, Any]], failed_count: int):
    """
    Saves current scraping progress state to data/progress.json.
    """
    try:
        data = {
            "scraped_ids": scraped_ids,
            "scraped_products": scraped_products,
            "failed_count": failed_count
        }
        with open(config.PROGRESS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not save progress.json: {e}")

def generate_quality_report(stats: Dict[str, Any], filepath: Path):
    """
    Generates data quality report text file.
    """
    report_content = f"""==================================================
TEHZEEB LIBAS SCRAPE DATA QUALITY REPORT
==================================================
Total Products Discovered:    {stats.get('discovered', 0)}
Total Products Scraped:       {stats.get('scraped', 0)}
Total Failed Products:        {stats.get('failed', 0)}
Duplicates Removed:           {stats.get('duplicates_removed', 0)}

DATA INTEGRITY AUDIT:
--------------------------------------------------
Products Missing Price:       {stats.get('missing_price', 0)}
Products Missing Image:       {stats.get('missing_image', 0)}
Products Missing Category:    {stats.get('missing_category', 0)}
Products Missing SKU:         {stats.get('missing_sku', 0)}

OUTPUT FILES GENERATED:
--------------------------------------------------
Main Clean Dataset:  {config.FINAL_CSV_PATH}
Raw Dataset:         {config.RAW_CSV_PATH}
Log File:            {config.LOG_FILE_PATH}
Failed Log:          {config.FAILED_PRODUCTS_PATH}
==================================================
"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_content)
        logger.info(f"Scrape report written to {filepath}")
    except Exception as e:
        logger.error(f"Failed to write report file: {e}")
