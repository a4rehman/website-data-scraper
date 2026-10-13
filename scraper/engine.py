import time
import threading
import uuid
import csv
import json as json_module
from datetime import datetime, timezone
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
from scraper.utils import fetch_html, logger, validate_safe_url, log_fetch_event
from scraper.job_store import (
    JobStatusStore,
    STATE_QUEUED,
    STATE_FETCHING,
    STATE_DISCOVERING,
    STATE_EXTRACTING,
    STATE_EXPORTING,
    STATE_COMPLETED,
    STATE_COMPLETED_WITH_ERRORS,
    STATE_CANCELLED,
    STATE_FAILED,
)


def _write_live_status(status_path: Path, data: Dict[str, Any]) -> None:
    """Write live progress status to a JSON file for UI polling."""
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        with open(status_path, "w", encoding="utf-8") as f:
            json_module.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"Could not write live status: {e}")


def _item_url(item: Any) -> str:
    """Safely extract a product URL from a discovery item (dict or string)."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("product_url") or item.get("url") or "")
    return str(item) if item is not None else ""


def _item_label(item: Any) -> str:
    """Human-readable label for a discovery item without assuming its shape."""
    if isinstance(item, dict):
        return str(item.get("product_name") or item.get("title") or item.get("name") or _item_url(item))[:80]
    return str(item)[:80]


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
        Validates URL security (SSRF prevention) before attempting fetch.
        """
        if not url or not isinstance(url, str):
            return {"error": "Please enter a valid URL."}

        url = url.strip()
        parsed_raw = urlparse(url)
        if not parsed_raw.scheme:
            url = f"https://{url}"

        is_safe, reason = validate_safe_url(url)
        if not is_safe:
            parsed = urlparse(url)
            return {
                "error": reason,
                "domain": parsed.netloc or "Blocked Domain",
                "platform": "Blocked / Untrusted URL",
                "page_type": "Security Restriction",
                "requires_js": False,
                "extraction_method": "Blocked by Security Policy (SSRF Prevention)"
            }

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
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes full extraction pipeline on a target URL with live progress tracking and exports.
        A terminal status (Completed / Completed with errors / Cancelled / Failed) is always
        written to the job store and live_status.json, including on exceptions and zero-product runs.
        """
        start_time = time.time()

        # Generate job_id if not provided and register isolated job state
        if job_id is None:
            job_id = uuid.uuid4().hex[:12]
        JobStatusStore.create_job(job_id, url=url if isinstance(url, str) else "")
        if stop_event is None:
            stop_event = JobStatusStore.cancel_event(job_id)

        def emit(state: str, message: str, **fields: Any) -> None:
            """Push a status update to the job store, callback, and live_status file."""
            elapsed = round(time.time() - start_time, 2)
            payload: Dict[str, Any] = {
                "job_id": job_id,
                "url": url if isinstance(url, str) else "",
                "state": state,
                "status": message,
                "elapsed_seconds": elapsed,
                **fields,
            }
            # Store keyed by job_id separately — never pass it twice via **payload.
            store_fields = {k: v for k, v in payload.items() if k != "job_id"}
            JobStatusStore.update(job_id, **store_fields)
            if progress_callback:
                try:
                    progress_callback(payload)
                except Exception as cb_err:
                    logger.debug(f"Progress callback failed: {cb_err}")
            if job_paths is not None:
                _write_live_status(job_paths["job_dir"] / "live_status.json", payload)

        def cancelled() -> bool:
            return (stop_event is not None and stop_event.is_set()) or JobStatusStore.is_cancel_requested(job_id)

        job_paths: Optional[dict] = None

        def failure(message: str, analysis: Optional[Dict[str, Any]] = None, state: str = STATE_FAILED) -> Dict[str, Any]:
            emit(state, message, error=message)
            logger.error(f"Engine failure [{job_id}]: {message}")
            return {
                "success": False,
                "job_id": job_id,
                "job_paths": job_paths,
                "message": message,
                "analysis": analysis or {"error": message},
                "stats": {"discovered": 0, "scraped": 0, "failed": 0, "duplicates_removed": 0},
                "products": [],
            }

        if not url or not isinstance(url, str):
            return failure("Invalid or empty URL provided.")

        url = url.strip()
        parsed_raw = urlparse(url)
        if not parsed_raw.scheme:
            url = f"https://{url}"

        is_safe, reason = validate_safe_url(url)
        if not is_safe:
            logger.warning(f"Engine blocked unsafe URL: {url} ({reason})")
            return failure(f"Access Blocked: {reason}", analysis={"error": reason, "domain": "Blocked"})

        job_paths = config.get_job_paths(job_id)
        target_csv = csv_path or job_paths["csv"]
        target_json = json_path or job_paths["json"]
        target_report = report_path or job_paths["report"]
        delay = request_delay if request_delay is not None else config.REQUEST_DELAY

        logger.info(f"=== Universal Scraping Engine Started for: {url} (job={job_id}) ===")

        # 1. Adapter Selection (Fetching source)
        emit(STATE_FETCHING, "Fetching source page and detecting platform...")
        try:
            html_preview = fetch_html(url)
            log_fetch_event("engine_preview", url, detail="ok" if html_preview else "empty_or_failed")
            adapter: ProductAdapter = get_adapter(url, html=html_preview)
            analysis = adapter.analyze_url(url, html=html_preview)
        except Exception as e:
            logger.exception(f"Adapter selection failed for {url}")
            return failure(f"Failed to analyze source URL: {e}")
        logger.info(f"Adapter: {adapter.name} | Page Type: {analysis.get('page_type')}")

        # 2. Product Discovery
        emit(
            STATE_DISCOVERING,
            f"Discovering products on {adapter.name} ({analysis.get('page_type')})...",
            adapter=adapter.name,
            page_type=analysis.get("page_type", ""),
        )
        try:
            def discovery_progress(payload: Dict[str, Any]) -> None:
                if isinstance(payload, dict):
                    emit(
                        STATE_DISCOVERING,
                        str(payload.get("message") or "Discovering products..."),
                        discovered=int(payload.get("discovered") or 0),
                    )

            discovered_raw = adapter.discover_products(
                url,
                max_products=max_products,
                use_browser=use_browser,
                stop_event=stop_event,
                progress_callback=discovery_progress,
            )
        except Exception as e:
            logger.exception(f"Product discovery raised for {url}")
            return failure(f"Product discovery failed: {e}", analysis=analysis)

        # Normalize discovery items to the agreed schema: dicts or URL strings
        discovered_raw = [i for i in (discovered_raw or []) if isinstance(i, (dict, str))]
        total_discovered = len(discovered_raw)
        logger.info(f"Total items discovered: {total_discovered}")

        if cancelled():
            emit(STATE_CANCELLED, "Cancelled during discovery — no products extracted.",
                 discovered=0, progress_count=0, total_target=0)
            return {
                "success": False,
                "job_id": job_id,
                "job_paths": job_paths,
                "cancelled": True,
                "message": "Cancelled by user during discovery.",
                "analysis": analysis,
                "stats": {"discovered": 0, "scraped": 0, "failed": 0, "duplicates_removed": 0},
                "products": [],
            }

        if not discovered_raw:
            msg = (
                "No products discovered at this URL. The site may require dynamic JavaScript "
                "rendering, block automated access, or return an empty catalog. "
                "Check the HTTP status in logs; no product data was fabricated."
            )
            return failure(msg, analysis=analysis)

        # Apply max_products ceiling
        if max_products and total_discovered > max_products:
            discovered_raw = discovered_raw[:max_products]
        total_to_process = len(discovered_raw)

        emit(
            STATE_DISCOVERING,
            f"Discovery complete: {total_to_process} products found. Preparing extraction...",
            discovered=total_discovered,
            total_target=total_to_process,
            progress_count=0,
        )

        # 3. Resume State Check
        scraped_products: List[Dict[str, Any]] = []
        scraped_keys = set()
        failed_count = 0
        failed_products: List[Dict[str, Any]] = []

        if resume:
            prog = load_progress(job_paths["progress"])
            if prog.get("url") == url:
                scraped_products = prog.get("scraped_products", [])
                scraped_keys = set(prog.get("scraped_ids", []))
                failed_count = prog.get("failed_count", 0)
                failed_products = prog.get("failed_products", [])
                logger.info(f"Resuming: Loaded {len(scraped_products)} previously saved items.")

        # 4. Detail Extraction Loop (guarded so a crash still yields a terminal state)
        try:
            for idx, item in enumerate(discovered_raw, start=1):
                if cancelled():
                    logger.warning("Scraping cancellation requested by user.")
                    break

                item_key = str(
                    (item.get("id") or item.get("product_id") or item.get("product_url") or "")
                    if isinstance(item, dict) else item
                ) or f"item-{idx}"

                if item_key in scraped_keys:
                    logger.info(f"[{idx}/{total_to_process}] Skipping already processed item: {item_key}")
                    continue

                current_name = _item_label(item)

                emit(
                    STATE_EXTRACTING,
                    f"Extracting [{idx}/{total_to_process}]: {current_name[:50]}",
                    progress_count=idx - 1,
                    total_target=total_to_process,
                    current_product=current_name,
                    extracted=len(scraped_products),
                    failed=failed_count,
                    discovered=total_discovered,
                )

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
                        failed_products.append({
                            "product_url": _item_url(item),
                            "reason": "Extraction returned None",
                            "status_code": "",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "stage": "extraction",
                        })
                        logger.warning(f"[{idx}/{total_to_process}] Extraction returned None for: {current_name}")

                except Exception as e:
                    logger.warning(f"Error extracting product [{item_key}]: {type(e).__name__}: {e}")
                    failed_count += 1
                    failed_products.append({
                        "product_url": _item_url(item),
                        "reason": f"{type(e).__name__}: {e}",
                        "status_code": "",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stage": "extraction",
                    })

                # Save progress incrementally
                if idx % 5 == 0 or idx == total_to_process:
                    save_progress(list(scraped_keys), scraped_products, failed_count, source_url=url, filepath=job_paths["progress"])
                    try:
                        with open(job_paths["failed"], "w", encoding="utf-8") as f:
                            writer = csv.DictWriter(f, fieldnames=["product_url", "reason", "status_code", "timestamp", "stage"])
                            writer.writeheader()
                            writer.writerows(failed_products)
                    except Exception as e:
                        logger.warning(f"Could not save failed products: {e}")

                # Polite rate limiting
                time.sleep(delay)
        except Exception as e:
            # Unexpected pipeline error: export what we have and mark Failed.
            logger.exception(f"Unexpected engine error during extraction [{job_id}]")
            try:
                clean_partial, dup_partial = deduplicate_products(scraped_products)
                export_to_csv(clean_partial, target_csv)
                export_to_json(clean_partial, target_json)
            except Exception:
                clean_partial, dup_partial = [], 0
            emit(
                STATE_FAILED,
                f"Failed during extraction: {type(e).__name__}: {e}. "
                f"Partial results preserved ({len(clean_partial)} products).",
                error=str(e),
                extracted=len(clean_partial),
                failed=failed_count,
                total_target=total_to_process,
            )
            return {
                "success": False,
                "job_id": job_id,
                "job_paths": job_paths,
                "analysis": analysis,
                "stats": {
                    "url": url,
                    "platform": adapter.name,
                    "discovered": total_discovered,
                    "scraped": len(clean_partial),
                    "failed": failed_count,
                    "duplicates_removed": dup_partial,
                    "elapsed_seconds": round(time.time() - start_time, 2),
                },
                "products": clean_partial,
                "csv_path": target_csv,
                "json_path": target_json,
                "state": STATE_FAILED,
                "message": f"Failed during extraction: {e}",
            }

        was_cancelled = cancelled()

        # 5-7. Deduplication, audit, and exports — guarded so any unexpected
        # error still yields a terminal state with partial results preserved.
        try:
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

            # 7. Exports (always, so cancellation preserves partial results)
            emit(STATE_EXPORTING, "Exporting results...",
                 progress_count=total_to_process, total_target=total_to_process,
                 extracted=len(clean_products), failed=failed_count, duplicates=duplicates_removed)
            export_to_csv(clean_products, target_csv)
            export_to_json(clean_products, target_json)
            generate_quality_report(stats, target_report, url=url)

            if failed_products:
                try:
                    with open(job_paths["failed"], "w", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=["product_url", "reason", "status_code", "timestamp", "stage"])
                        writer.writeheader()
                        writer.writerows(failed_products)
                    logger.info(f"Exported {len(failed_products)} failed products to {job_paths['failed']}")
                except Exception as e:
                    logger.warning(f"Could not export failed products: {e}")
        except Exception as e:
            logger.exception(f"Unexpected engine error during export [{job_id}]")
            try:
                export_to_csv(scraped_products, target_csv)
                export_to_json(scraped_products, target_json)
            except Exception:
                pass
            emit(
                STATE_FAILED,
                f"Failed during export: {type(e).__name__}: {e}. "
                f"Partial results preserved ({len(scraped_products)} products).",
                error=str(e),
                extracted=len(scraped_products),
                failed=failed_count,
                total_target=total_to_process,
            )
            return {
                "success": False,
                "job_id": job_id,
                "job_paths": job_paths,
                "analysis": analysis,
                "stats": {
                    "url": url,
                    "platform": adapter.name,
                    "discovered": total_discovered,
                    "scraped": len(scraped_products),
                    "failed": failed_count,
                    "duplicates_removed": 0,
                    "elapsed_seconds": round(time.time() - start_time, 2),
                },
                "products": scraped_products,
                "csv_path": target_csv,
                "json_path": target_json,
                "state": STATE_FAILED,
                "message": f"Failed during export: {e}",
            }

        # 8. Terminal status — never report Completed if extraction never ran or was cancelled
        if was_cancelled:
            terminal_state = STATE_CANCELLED
            message = (
                f"Cancelled. Partial results preserved: {len(clean_products)} products extracted, "
                f"{failed_count} failed."
            )
            success = bool(clean_products)
        elif not clean_products:
            terminal_state = STATE_FAILED
            message = (
                f"Extraction finished with 0 products ({failed_count} failed, "
                f"{total_discovered} discovered). No data fabricated."
            )
            success = False
        elif failed_count > 0:
            terminal_state = STATE_COMPLETED_WITH_ERRORS
            message = f"Completed with errors: {len(clean_products)} extracted, {failed_count} failed."
            success = True
        else:
            terminal_state = STATE_COMPLETED
            message = f"Complete! Extracted {len(clean_products)} products."
            success = True

        emit(
            terminal_state,
            message,
            progress_count=total_to_process,
            total_target=total_to_process,
            extracted=len(clean_products),
            failed=failed_count,
            duplicates=duplicates_removed,
            discovered=total_discovered,
            current_product="Done" if not was_cancelled else "",
        )

        logger.info(f"=== Extraction {terminal_state}: {len(clean_products)} saved to {target_csv} ===")

        return {
            "success": success,
            "cancelled": was_cancelled,
            "job_id": job_id,
            "job_paths": job_paths,
            "analysis": analysis,
            "stats": stats,
            "products": clean_products,
            "csv_path": target_csv,
            "json_path": target_json,
            "report_path": target_report,
            "state": terminal_state,
            "message": message,
        }
