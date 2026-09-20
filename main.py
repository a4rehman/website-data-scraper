import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any

import config
from scraper.discovery import CatalogDiscovery
from scraper.product_scraper import ProductScraper
from scraper.cleaners import deduplicate_products
from scraper.exporter import (
    export_to_csv,
    export_raw_to_csv,
    load_progress,
    save_progress,
    generate_quality_report
)
from scraper.utils import logger

def parse_args():
    parser = argparse.ArgumentParser(description="Tehzeeb Libas Production E-Commerce Scraper")
    parser.add_argument("--test", action="store_true", help="Run in test mode (scrape 10 products only)")
    parser.add_argument("--resume", action="store_true", help="Resume from previous scrape using progress.json")
    parser.add_argument("--category", type=str, default=None, help="Filter scraping by specific collection/category")
    return parser.parse_args()

def main():
    args = parse_args()
    logger.info("===========================================")
    logger.info("Starting Tehzeeb Libas Product Scraper")
    logger.info("===========================================")

    discovery = CatalogDiscovery()
    
    # Discovery Phase
    raw_products = discovery.discover_all_products(category_filter=args.category)
    total_discovered = len(raw_products)

    if not raw_products:
        logger.error("No products discovered! Exiting.")
        sys.exit(1)

    if args.test:
        logger.info("[TEST MODE] Limiting scrape target to 10 products.")
        raw_products = raw_products[:10]

    # Resume capability check
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

    # Scraping Phase
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

        # Periodic progress save
        if idx % 10 == 0 or idx == total_to_scrape:
            save_progress(list(scraped_ids), scraped_products, failed_count)

    # Data Cleaning & Deduplication Phase
    clean_products, duplicates_removed = deduplicate_products(scraped_products)

    # Data Integrity Audit
    missing_price = sum(1 for p in clean_products if not p.get("price"))
    missing_image = sum(1 for p in clean_products if not p.get("image_url"))
    missing_category = sum(1 for p in clean_products if not p.get("category"))
    missing_sku = sum(1 for p in clean_products if not p.get("sku"))

    # Export Phase
    export_to_csv(clean_products, config.FINAL_CSV_PATH)
    export_to_csv(clean_products, config.CLEAN_CSV_PATH)
    export_raw_to_csv(raw_saved_list, config.RAW_CSV_PATH)

    # Report Generation
    stats = {
        "discovered": total_discovered,
        "scraped": len(clean_products),
        "failed": failed_count,
        "duplicates_removed": duplicates_removed,
        "missing_price": missing_price,
        "missing_image": missing_image,
        "missing_category": missing_category,
        "missing_sku": missing_sku
    }
    generate_quality_report(stats, config.REPORT_TXT_PATH)

    logger.info("===========================================")
    logger.info("Scraping completed.")
    logger.info(f"Products discovered: {total_discovered}")
    logger.info(f"Products scraped: {len(clean_products)}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"Duplicates removed: {duplicates_removed}")
    logger.info(f"CSV saved to: {config.FINAL_CSV_PATH}")
    logger.info("===========================================")

if __name__ == "__main__":
    main()
