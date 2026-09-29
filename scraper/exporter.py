import csv
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
import config
from scraper.utils import logger

CSV_COLUMNS = config.UNIVERSAL_CSV_COLUMNS

def export_to_csv(products: List[Dict[str, Any]], filepath: Path) -> bool:
    """
    Exports a list of processed product dictionaries to UTF-8 CSV format using UNIVERSAL_CSV_COLUMNS.
    Handles quotes, commas, multiline strings, Urdu/Arabic/Chinese, and emojis cleanly.
    Does not silently drop extra fields - includes all fields from products.
    """
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Collect all unique columns from all products
        all_columns = set(CSV_COLUMNS)
        for p in products:
            all_columns.update(p.keys())
        
        # Sort columns for consistent ordering - priority columns first
        priority_columns = list(CSV_COLUMNS)
        extra_columns = sorted([c for c in all_columns if c not in CSV_COLUMNS])
        fieldnames = priority_columns + extra_columns
        
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                quoting=csv.QUOTE_MINIMAL,
                extrasaction="ignore"
            )
            writer.writeheader()
            for p in products:
                # Ensure every column is a string or number, not raw nested objects in CSV
                row = {}
                for col in fieldnames:
                    val = p.get(col, "")
                    if isinstance(val, (dict, list)):
                        row[col] = json.dumps(val, ensure_ascii=False)
                    elif val is None:
                        row[col] = ""
                    else:
                        row[col] = val
                writer.writerow(row)
        logger.info(f"Successfully saved {len(products)} products to CSV: {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to export CSV to {filepath}: {e}")
        return False

def export_to_json(products: List[Dict[str, Any]], filepath: Path) -> bool:
    """
    Exports a list of products to formatted JSON preserving nested structures
    (variants, specifications, images) where applicable.
    """
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        formatted_products = []
        for p in products:
            item = dict(p)
            # Fields that may be JSON strings - decode them for structured JSON
            json_string_fields = [
                "variants", "specifications", "all_images", "additional_images",
                "colors", "sizes", "tags"
            ]
            for field in json_string_fields:
                if isinstance(item.get(field), str) and item[field].strip().startswith(("{", "[")):
                    try:
                        item[field] = json.loads(item[field])
                    except Exception:
                        pass
            formatted_products.append(item)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(formatted_products, f, ensure_ascii=False, indent=2)
        logger.info(f"Successfully saved {len(products)} products to JSON: {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to export JSON to {filepath}: {e}")
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

def load_progress(filepath: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads saved progress state from data/progress.json or job-specific path.
    """
    target = filepath or config.PROGRESS_JSON_PATH
    if target.exists():
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read progress file {target}: {e}")
    return {"scraped_ids": [], "scraped_products": [], "failed_count": 0, "url": ""}

def save_progress(
    scraped_ids: List[str],
    scraped_products: List[Dict[str, Any]],
    failed_count: int,
    source_url: str = "",
    filepath: Optional[Path] = None
):
    """
    Saves current scraping progress state to data/progress.json or job-specific path.
    """
    target = filepath or config.PROGRESS_JSON_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "url": source_url,
            "scraped_ids": list(scraped_ids),
            "scraped_products": scraped_products,
            "failed_count": failed_count,
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not save progress.json: {e}")

def generate_quality_report(stats: Dict[str, Any], filepath: Path, url: str = ""):
    """
    Generates data quality report text file with comprehensive statistics.
    """
    report_content = f"""==================================================
UNIVERSAL E-COMMERCE PRODUCT SCRAPE REPORT
==================================================
Target URL:                   {url or stats.get('url', 'N/A')}
Detected Platform:            {stats.get('platform', 'N/A')}
Total Products Discovered:    {stats.get('discovered', 0)}
Total Products Extracted:     {stats.get('scraped', 0)}
Total Failed / Skipped:       {stats.get('failed', 0)}
Duplicates Removed:           {stats.get('duplicates_removed', 0)}

DATA INTEGRITY AUDIT:
--------------------------------------------------
Products Missing Title:       {stats.get('missing_title', 0)}
Products Missing Price:       {stats.get('missing_price', 0)}
Products Missing Description: {stats.get('missing_description', 0)}
Products Missing Image:       {stats.get('missing_image', 0)}
Products Missing Category:    {stats.get('missing_category', 0)}
Products Missing SKU:         {stats.get('missing_sku', 0)}
Products With Variants:       {stats.get('has_variants', 0)}

OUTPUT ARTIFACTS GENERATED:
--------------------------------------------------
Main CSV Dataset:             {config.UNIVERSAL_CSV_PATH}
Main JSON Dataset:            {config.UNIVERSAL_JSON_PATH}
Scrape Report:                {filepath}
Scraper Log:                  {config.LOG_FILE_PATH}
==================================================
"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_content)
        logger.info(f"Scrape report written to {filepath}")
    except Exception as e:
        logger.error(f"Failed to write report file: {e}")
