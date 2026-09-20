import streamlit as st
import pandas as pd
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

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

# Page Configuration
st.set_page_config(
    page_title="Tehzeeb Libas Product Scraper",
    page_icon="🛍️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Global background worker state using st.session_state
if "scraping_in_progress" not in st.session_state:
    st.session_state.scraping_in_progress = False
if "current_status" not in st.session_state:
    st.session_state.current_status = "Idle"
if "current_product" not in st.session_state:
    st.session_state.current_product = ""
if "progress_count" not in st.session_state:
    st.session_state.progress_count = 0
if "total_target" not in st.session_state:
    st.session_state.total_target = 0
if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False

# Lock for background worker
if "worker_lock" not in st.session_state:
    st.session_state.worker_lock = threading.Lock()

def run_scraper_task(mode: str, category: Optional[str] = None):
    """
    Background worker thread running the scraper engine safely.
    """
    st.session_state.scraping_in_progress = True
    st.session_state.stop_requested = False
    st.session_state.current_status = "Discovering products..."
    st.session_state.progress_count = 0

    try:
        discovery = CatalogDiscovery()
        raw_products = discovery.discover_all_products(category_filter=category if mode == "Category / Collection" else None)
        total_discovered = len(raw_products)

        if not raw_products:
            st.session_state.current_status = "Error: No products discovered."
            st.session_state.scraping_in_progress = False
            return

        if mode == "Test (10 Products)":
            raw_products = raw_products[:10]

        st.session_state.total_target = len(raw_products)
        st.session_state.current_status = f"Scraping catalog ({len(raw_products)} items)..."

        scraped_products: List[Dict[str, Any]] = []
        scraped_ids = set()
        failed_count = 0

        if mode == "Resume Previous Scrape":
            progress_data = load_progress()
            scraped_products = progress_data.get("scraped_products", [])
            scraped_ids = set(progress_data.get("scraped_ids", []))
            failed_count = progress_data.get("failed_count", 0)

        scraper = ProductScraper(collections_map=discovery.collections_map)
        raw_saved_list = []

        for idx, raw_p in enumerate(raw_products, start=1):
            if st.session_state.stop_requested:
                st.session_state.current_status = "Scrape stopped by user."
                break

            p_id = str(raw_p.get("id"))
            p_title = raw_p.get("title", "Unknown Product")

            st.session_state.current_product = p_title
            st.session_state.progress_count = idx

            if p_id in scraped_ids:
                continue

            parsed_p, error = scraper.process_raw_product(raw_p)
            if parsed_p:
                scraped_products.append(parsed_p)
                scraped_ids.add(p_id)
                raw_saved_list.append(raw_p)
            else:
                failed_count += 1

            if idx % 5 == 0 or idx == len(raw_products):
                save_progress(list(scraped_ids), scraped_products, failed_count)

        # Deduplicate & Export
        clean_products, duplicates_removed = deduplicate_products(scraped_products)

        missing_price = sum(1 for p in clean_products if not p.get("price"))
        missing_image = sum(1 for p in clean_products if not p.get("image_url"))
        missing_category = sum(1 for p in clean_products if not p.get("category"))
        missing_sku = sum(1 for p in clean_products if not p.get("sku"))

        export_to_csv(clean_products, config.FINAL_CSV_PATH)
        export_to_csv(clean_products, config.CLEAN_CSV_PATH)
        export_raw_to_csv(raw_saved_list, config.RAW_CSV_PATH)

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

        if not st.session_state.stop_requested:
            st.session_state.current_status = "Completed Successfully!"

    except Exception as e:
        st.session_state.current_status = f"Error: {str(e)}"
    finally:
        st.session_state.scraping_in_progress = False

# --- HEADER SECTION ---
st.title("🛍️ Tehzeeb Libas Product Scraper")
st.markdown("### E-Commerce Product Data Extraction & CSV Export Dashboard")

st.markdown("---")

# --- SIDEBAR CONTROLS ---
st.sidebar.header("⚙️ Scraper Controls")

mode = st.sidebar.selectbox(
    "Scraping Mode",
    ["Test (10 Products)", "Full Catalog", "Resume Previous Scrape", "Category / Collection"]
)

category_input = None
if mode == "Category / Collection":
    category_input = st.sidebar.text_input("Category / Collection Name", placeholder="e.g. Everyday Essentials")

st.sidebar.markdown("---")

col_btn1, col_btn2 = st.sidebar.columns(2)

with col_btn1:
    start_btn = st.button("🚀 Start Scraping", disabled=st.session_state.scraping_in_progress, use_container_width=True)

with col_btn2:
    stop_btn = st.button("⏸ Stop / Cancel", disabled=not st.session_state.scraping_in_progress, use_container_width=True)

refresh_btn = st.sidebar.button("🔄 Refresh Status", use_container_width=True)

if start_btn:
    if not st.session_state.scraping_in_progress:
        worker_thread = threading.Thread(target=run_scraper_task, args=(mode, category_input), daemon=True)
        worker_thread.start()
        st.rerun()

if stop_btn:
    st.session_state.stop_requested = True
    st.sidebar.warning("Stop requested...")

# --- METRICS & STATUS ---
st.subheader("📊 Live Status & Metrics")

status_col1, status_col2 = st.columns([3, 1])
with status_col1:
    st.info(f"**Current Status:** {st.session_state.current_status}")
with status_col2:
    if st.session_state.scraping_in_progress:
        st.warning("⚡ Scraping active...")
    else:
        st.success("✅ System ready")

# Live Progress Bar
if st.session_state.scraping_in_progress or st.session_state.progress_count > 0:
    total = st.session_state.total_target if st.session_state.total_target > 0 else 1
    pct = min(st.session_state.progress_count / total, 1.0)
    st.progress(pct)
    st.caption(f"Current Item: **{st.session_state.current_product}** | Progress: **{st.session_state.progress_count} / {st.session_state.total_target}** ({int(pct*100)}%)")

# Calculate metrics from final CSV or report if available
discovered_val = 0
scraped_val = 0
failed_val = 0
dupes_val = 0
missing_price_val = 0
missing_img_val = 0
missing_cat_val = 0
missing_sku_val = 0

if config.REPORT_TXT_PATH.exists():
    try:
        report_text = config.REPORT_TXT_PATH.read_text(encoding="utf-8")
        for line in report_text.splitlines():
            if "Total Products Discovered:" in line:
                discovered_val = int(line.split(":")[-1].strip())
            elif "Total Products Scraped:" in line:
                scraped_val = int(line.split(":")[-1].strip())
            elif "Total Failed Products:" in line:
                failed_val = int(line.split(":")[-1].strip())
            elif "Duplicates Removed:" in line:
                dupes_val = int(line.split(":")[-1].strip())
            elif "Products Missing Price:" in line:
                missing_price_val = int(line.split(":")[-1].strip())
            elif "Products Missing Image:" in line:
                missing_img_val = int(line.split(":")[-1].strip())
            elif "Products Missing Category:" in line:
                missing_cat_val = int(line.split(":")[-1].strip())
            elif "Products Missing SKU:" in line:
                missing_sku_val = int(line.split(":")[-1].strip())
    except Exception:
        pass

m_col1, m_col2, m_col3, m_col4 = st.columns(4)
m_col1.metric("Discovered", discovered_val)
m_col2.metric("Scraped", scraped_val)
m_col3.metric("Failed", failed_val)
m_col4.metric("Duplicates Removed", dupes_val)

m_col5, m_col6, m_col7, m_col8 = st.columns(4)
m_col5.metric("Missing Price", missing_price_val)
m_col6.metric("Missing Image", missing_img_val)
m_col7.metric("Missing Category", missing_cat_val)
m_col8.metric("Missing SKU", missing_sku_val)

st.markdown("---")

# --- RESULTS DATA TABLE ---
st.subheader("📋 Scraped Products Data")

if config.FINAL_CSV_PATH.exists():
    try:
        df = pd.read_csv(config.FINAL_CSV_PATH, encoding="utf-8-sig")
        st.dataframe(
            df[[
                "product_name", "sku", "category", "price", "sale_price", 
                "availability", "sizes", "colors", "product_url", "image_url"
            ]],
            column_config={
                "image_url": st.column_config.ImageColumn("Image", help="Product thumbnail preview"),
                "product_url": st.column_config.LinkColumn("Product Link")
            },
            use_container_width=True,
            hide_index=True
        )
    except Exception as e:
        st.warning(f"Could not load products table: {e}")
else:
    st.info("No scraped dataset found on disk yet. Click '🚀 Start Scraping' to extract catalog products.")

st.markdown("---")

# --- DOWNLOADS & LOGS ---
d_col1, d_col2 = st.columns(2)

with d_col1:
    st.subheader("📥 Export & Downloads")
    if config.FINAL_CSV_PATH.exists():
        csv_bytes = config.FINAL_CSV_PATH.read_bytes()
        st.download_button(
            label="📥 Download Final CSV (tehzeeb_libas_products.csv)",
            data=csv_bytes,
            file_name="tehzeeb_libas_products.csv",
            mime="text/csv",
            use_container_width=True
        )

    if config.REPORT_TXT_PATH.exists():
        report_bytes = config.REPORT_TXT_PATH.read_bytes()
        st.download_button(
            label="📊 Download Scrape Report (scrape_report.txt)",
            data=report_bytes,
            file_name="scrape_report.txt",
            mime="text/plain",
            use_container_width=True
        )

    if config.FAILED_PRODUCTS_PATH.exists() and config.FAILED_PRODUCTS_PATH.stat().st_size > 50:
        failed_bytes = config.FAILED_PRODUCTS_PATH.read_bytes()
        st.download_button(
            label="⚠️ Download Failed Products (failed_products.csv)",
            data=failed_bytes,
            file_name="failed_products.csv",
            mime="text/csv",
            use_container_width=True
        )

with d_col2:
    st.subheader("📜 Scraper Logs")
    with st.expander("Show Recent Activity Logs", expanded=True):
        if config.LOG_FILE_PATH.exists():
            try:
                log_lines = config.LOG_FILE_PATH.read_text(encoding="utf-8").splitlines()[-30:]
                st.code("\n".join(log_lines), language="log")
            except Exception as e:
                st.write(f"Could not read log file: {e}")
        else:
            st.info("No log file found yet.")
