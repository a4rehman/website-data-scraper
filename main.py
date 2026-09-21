import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any

import config
from scraper.engine import UniversalScrapingEngine
from scraper.discovery import CatalogDiscovery
from scraper.product_scraper import ProductScraper
from scraper.cleaners import deduplicate_products
from scraper.exporter import (
    export_to_csv,
    export_to_json,
    export_raw_to_csv,
    load_progress,
    save_progress,
    generate_quality_report
)
from scraper.utils import logger

def parse_args():
    parser = argparse.ArgumentParser(description="Universal E-Commerce Product Data Extractor & Scraper")
    parser.add_argument("--url", type=str, default=None, help="Target e-commerce URL (Product, Category, Collection, or Search URL)")
    parser.add_argument("--test", action="store_true", help="Run in test mode (scrape max 10 products)")
    parser.add_argument("--max-products", type=int, default=None, help="Maximum number of products to discover and extract")
    parser.add_argument("--delay", type=float, default=None, help="Delay between requests in seconds")
    parser.add_argument("--browser", action="store_true", help="Enable Playwright headless browser rendering for JS-heavy sites")
    parser.add_argument("--resume", action="store_true", help="Resume from previous scrape using progress.json")
    parser.add_argument("--category", type=str, default=None, help="Filter catalog scraping by specific collection/category")
    parser.add_argument("--no-images", action="store_true", help="Disable image extraction")
    parser.add_argument("--no-variants", action="store_true", help="Disable variant extraction")
    return parser.parse_args()

def run_universal_scrape(args):
    """Executes scraping for any arbitrary e-commerce URL using UniversalScrapingEngine."""
    engine = UniversalScrapingEngine()
    max_count = 10 if args.test else args.max_products

    logger.info("===========================================")
    logger.info("Universal E-Commerce Product Data Extractor")
    logger.info(f"Target URL: {args.url}")
    logger.info("===========================================")

    # Analyze URL first
    analysis = engine.analyze_url(args.url)
    logger.info(f"Detected Platform:  {analysis.get('platform')}")
    logger.info(f"Detected Page Type: {analysis.get('page_type')}")
    logger.info(f"Extraction Method:  {analysis.get('extraction_method')}")
    logger.info("-------------------------------------------")

    result = engine.scrape_url(
        url=args.url,
        max_products=max_count,
        request_delay=args.delay,
        use_browser=args.browser,
        extract_images=not args.no_images,
        extract_variants=not args.no_variants,
        resume=args.resume
    )

    if result.get("success"):
        stats = result.get("stats", {})
        logger.info("===========================================")
        logger.info("Scrape Completed Successfully!")
        logger.info(f"Discovered: {stats.get('discovered')}")
        logger.info(f"Extracted:  {stats.get('scraped')}")
        logger.info(f"Failed:     {stats.get('failed')}")
        logger.info(f"Duplicates: {stats.get('duplicates_removed')}")
        logger.info(f"CSV saved:  {result.get('csv_path')}")
        logger.info(f"JSON saved: {result.get('json_path')}")
        logger.info(f"Report:     {result.get('report_path')}")
        logger.info("===========================================")
    else:
        logger.error(f"Scrape failed: {result.get('message')}")
        sys.exit(1)

def run_legacy_catalog_scrape(args):
    """Fallback legacy full catalog scraper for configured BASE_URL."""
    logger.info("===========================================")
    logger.info(f"Starting Catalog Product Scraper: {config.BASE_URL}")
    logger.info("===========================================")

    discovery = CatalogDiscovery()
    raw_products = discovery.discover_all_products(category_filter=args.category)
    total_discovered = len(raw_products)

    if not raw_products:
        logger.error("No products discovered! Exiting.")
        sys.exit(1)

    max_count = 10 if args.test else args.max_products
    if max_count:
        logger.info(f"Limiting scrape target to {max_count} products.")
        raw_products = raw_products[:max_count]

    scraped_products: List[Dict[str, Any]] = []
    scraped_ids = set()
    failed_count = 0

    if args.resume:
        logger.info("Resuming scrape from progress.json...")
        progress_data = load_progress()
        scraped_products = progress_data.get("scraped_products", [])
        scraped_ids = set(progress_data.get("scraped_ids", []))
        failed_count = progress_data.get("failed_count", 0)
        logger.info(f"Loaded {len(scraped_products)} previously scraped products from state.")

    scraper = ProductScraper(collections_map=discovery.collections_map)
    raw_saved_list = []
    total_to_scrape = len(raw_products)

    for idx, raw_p in enumerate(raw_products, start=1):
        p_id = str(raw_p.get("id"))
        p_title = raw_p.get("title", "Unknown Product")

        if p_id in scraped_ids:
            logger.info(f"[{idx}/{total_to_scrape}] Skipping already scraped: {p_title}")
            continue

        logger.info(f"[{idx}/{total_to_scrape}] Scraping: {p_title}")

        parsed_p, error = scraper.process_raw_product(raw_p)
        if parsed_p:
            scraped_products.append(parsed_p)
            scraped_ids.add(p_id)
            raw_saved_list.append(raw_p)
        else:
            failed_count += 1

        if idx % 10 == 0 or idx == total_to_scrape:
            save_progress(list(scraped_ids), scraped_products, failed_count, source_url=config.BASE_URL)

    clean_products, duplicates_removed = deduplicate_products(scraped_products)

    # Audits
    missing_title = sum(1 for p in clean_products if not p.get("product_name"))
    missing_price = sum(1 for p in clean_products if not p.get("price"))
    missing_desc = sum(1 for p in clean_products if not p.get("description"))
    missing_image = sum(1 for p in clean_products if not p.get("image_url"))
    missing_category = sum(1 for p in clean_products if not p.get("category"))
    missing_sku = sum(1 for p in clean_products if not p.get("sku"))
    has_variants = sum(1 for p in clean_products if p.get("variants"))

    # Exports
    export_to_csv(clean_products, config.FINAL_CSV_PATH)
    export_to_csv(clean_products, config.UNIVERSAL_CSV_PATH)
    export_to_json(clean_products, config.UNIVERSAL_JSON_PATH)
    export_raw_to_csv(raw_saved_list, config.RAW_CSV_PATH)

    stats = {
        "url": config.BASE_URL,
        "platform": "Shopify (Default Catalog)",
        "discovered": total_discovered,
        "scraped": len(clean_products),
        "failed": failed_count,
        "duplicates_removed": duplicates_removed,
        "missing_title": missing_title,
        "missing_price": missing_price,
        "missing_description": missing_desc,
        "missing_image": missing_image,
        "missing_category": missing_category,
        "missing_sku": missing_sku,
        "has_variants": has_variants
    }
    generate_quality_report(stats, config.REPORT_TXT_PATH, url=config.BASE_URL)

    logger.info("===========================================")
    logger.info("Scraping completed.")
    logger.info(f"Products discovered: {total_discovered}")
    logger.info(f"Products scraped: {len(clean_products)}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"Duplicates removed: {duplicates_removed}")
    logger.info(f"CSV saved to: {config.FINAL_CSV_PATH}")
    logger.info(f"JSON saved to: {config.UNIVERSAL_JSON_PATH}")
    logger.info("===========================================")

def main():
    args = parse_args()
    if args.url:
        run_universal_scrape(args)
    else:
        run_legacy_catalog_scrape(args)

if __name__ == "__main__":
    main()
