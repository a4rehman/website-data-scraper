import time
import threading
from typing import List, Dict, Any, Optional, Callable
from urllib.parse import urlparse
from pathlib import Path
import config
from scraper.adapters import get_adapter, ProductAdapter
from scraper.cleaners import deduplicate_products
from scraper.exporter import (
    export_to_csv,
    export_to_json,
    load_progress,
    save_progress,
    generate_quality_report
)
from scraper.utils import fetch_html, logger

class UniversalScrapingEngine:
    """
    Core engine orchestrating URL analysis, platform detection, product discovery,
    detail extraction, normalization, deduplication, quality audit, and exports.
    """

    def __init__(self):
        pass

    def analyze_url(self, url: str) -> Dict[str, Any]:
        """
        Analyzes the target URL to detect platform, page type, and extraction strategy.
        """
        url = url.strip()
        if not url:
            return {"error": "Empty URL provided."}

        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"https://{url}"

        # Fetch lightweight HTML to assist platform fingerprinting if not obvious
        html = None
        adapter = get_adapter(url)
        if adapter.name == "Generic E-Commerce":
            html = fetch_html(url)
            # Re-check adapter with HTML signature
            adapter = get_adapter(url, html=html)

        analysis = adapter.analyze_url(url, html=html)
        analysis["target_url"] = url
        analysis["adapter_name"] = adapter.name
        return analysis

    def scrape_url(
        self,
        url: str,
        max_products: Optional[int] = None,
        request_delay: Optional[float] = None,
        use_browser: bool = False,
        extract_images: bool = True,
        extract_variants: bool = True,
        extract_descriptions: bool = True,
        resume: bool = False,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        stop_event: Optional[threading.Event] = None,
        csv_path: Optional[Path] = None,
        json_path: Optional[Path] = None,
        report_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Executes full extraction pipeline on a target URL with live progress tracking and exports.
        """
        start_time = time.time()
        url = url.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        target_csv = csv_path or config.UNIVERSAL_CSV_PATH
        target_json = json_path or config.UNIVERSAL_JSON_PATH
        target_report = report_path or config.REPORT_TXT_PATH
        delay = request_delay if request_delay is not None else config.REQUEST_DELAY

        logger.info(f"=== Universal Scraping Engine Started for: {url} ===")
        
        # 1. Adapter Selection
        html_preview = fetch_html(url)
        adapter: ProductAdapter = get_adapter(url, html=html_preview)
        analysis = adapter.analyze_url(url, html=html_preview)
        logger.info(f"Adapter: {adapter.name} | Page Type: {analysis.get('page_type')}")

        if progress_callback:
            progress_callback({
                "status": f"🔍 Discovered platform: {adapter.name} ({analysis.get('page_type')}). Discovering products...",
                "progress_count": 0,
                "total_target": 0,
                "current_product": "Analyzing...",
            })

        # 2. Product Discovery
        discovered_raw = adapter.discover_products(
            url,
            max_products=max_products,
            use_browser=use_browser
        )
        total_discovered = len(discovered_raw)
        logger.info(f"Total items discovered: {total_discovered}")

        if not discovered_raw:
            return {
                "success": False,
                "message": "No products could be discovered at this URL.",
                "analysis": analysis,
                "stats": {"discovered": 0, "scraped": 0, "failed": 0, "duplicates_removed": 0},
                "products": [],
            }

        # Apply max_products ceiling
        if max_products and total_discovered > max_products:
            discovered_raw = discovered_raw[:max_products]

        # 3. Resume State Check
        scraped_products: List[Dict[str, Any]] = []
        scraped_keys = set()
        failed_count = 0

        if resume:
            prog = load_progress()
            if prog.get("url") == url:
                scraped_products = prog.get("scraped_products", [])
                scraped_keys = set(prog.get("scraped_ids", []))
                failed_count = prog.get("failed_count", 0)
                logger.info(f"Resuming: Loaded {len(scraped_products)} previously saved items.")

        total_to_process = len(discovered_raw)

        # 4. Detail Extraction Loop
        for idx, item in enumerate(discovered_raw, start=1):
            if stop_event and stop_event.is_set():
                logger.warning("Scraping cancellation requested by user.")
                break

            item_key = str(item.get("id") or item.get("product_id") or item.get("product_url") or item) if isinstance(item, dict) else str(item)

            if item_key in scraped_keys:
                logger.info(f"[{idx}/{total_to_process}] Skipping already processed item: {item_key}")
                continue

            current_name = item.get("product_name") or item.get("title") or item_key if isinstance(item, dict) else item_key

            if progress_callback:
                progress_callback({
                    "status": f"Extracting [{idx}/{total_to_process}]: {str(current_name)[:50]}",
                    "progress_count": len(scraped_products),
                    "total_target": total_to_process,
                    "current_product": str(current_name)[:80],
                })

            try:
                extracted = adapter.extract_product(
                    item,
                    source_url=url,
                    use_browser=use_browser
                )

                if extracted:
                    # Apply option toggles
                    if not extract_images:
                        extracted["main_image"] = ""
                        extracted["image_url"] = ""
                        extracted["additional_images"] = ""
                        extracted["all_images"] = ""
                    if not extract_variants:
                        extracted["variants"] = ""
                        extracted["variant_count"] = 0
                    if not extract_descriptions:
                        extracted["description"] = ""
                        extracted["short_description"] = ""

                    scraped_products.append(extracted)
                    scraped_keys.add(item_key)
                else:
                    failed_count += 1

            except Exception as e:
                logger.warning(f"Error extracting product [{item_key}]: {e}")
                failed_count += 1

            # Save progress incrementally
            if idx % 5 == 0 or idx == total_to_process:
                save_progress(list(scraped_keys), scraped_products, failed_count, source_url=url)

            # Polite rate limiting
            time.sleep(delay)

        # 5. Deduplication
        clean_products, duplicates_removed = deduplicate_products(scraped_products)

        # 6. Data Integrity Audit
        missing_title = sum(1 for p in clean_products if not p.get("product_name") and not p.get("title"))
        missing_price = sum(1 for p in clean_products if not p.get("price"))
        missing_desc = sum(1 for p in clean_products if not p.get("description"))
        missing_img = sum(1 for p in clean_products if not p.get("main_image") and not p.get("image_url"))
        missing_cat = sum(1 for p in clean_products if not p.get("category"))
        missing_sku = sum(1 for p in clean_products if not p.get("sku"))
        has_variants = sum(1 for p in clean_products if p.get("variant_count", 0) > 0 or p.get("variants"))

        stats = {
            "url": url,
            "platform": adapter.name,
            "discovered": total_discovered,
            "scraped": len(clean_products),
            "failed": failed_count,
            "duplicates_removed": duplicates_removed,
            "missing_title": missing_title,
            "missing_price": missing_price,
            "missing_description": missing_desc,
            "missing_image": missing_img,
            "missing_category": missing_cat,
            "missing_sku": missing_sku,
            "has_variants": has_variants,
            "elapsed_seconds": round(time.time() - start_time, 2),
        }

        # 7. Exports
        export_to_csv(clean_products, target_csv)
        export_to_json(clean_products, target_json)
        generate_quality_report(stats, target_report, url=url)

        if progress_callback:
            progress_callback({
                "status": f"✅ Complete! Extracted {len(clean_products)} products.",
                "progress_count": len(clean_products),
                "total_target": total_to_process,
                "current_product": "Done",
            })

        logger.info(f"=== Extraction Completed: {len(clean_products)} saved to {target_csv} ===")

        return {
            "success": True,
            "analysis": analysis,
            "stats": stats,
            "products": clean_products,
            "csv_path": target_csv,
            "json_path": target_json,
            "report_path": target_report,
        }
