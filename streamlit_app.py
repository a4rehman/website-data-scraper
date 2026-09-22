import streamlit as st
import pandas as pd
import json
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

import config
from scraper.engine import UniversalScrapingEngine
from scraper.exporter import (
    export_to_csv,
    export_to_json,
    load_progress,
    save_progress,
    generate_quality_report
)
from scraper.ai_processor import summarize_scraped_data_with_ai
from scraper.utils import logger, validate_safe_url

# Set page layout and config
st.set_page_config(
    page_title="Universal E-Commerce Product Extractor",
    page_icon="🛍️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #4f46e5, #06b6d4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #6b7280;
        margin-bottom: 1.5rem;
    }
    .analysis-card {
        background-color: rgba(79, 70, 229, 0.06);
        border: 1px solid rgba(79, 70, 229, 0.25);
        border-radius: 10px;
        padding: 1.2rem;
        margin-top: 1rem;
        margin-bottom: 1.5rem;
    }
    .metric-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .badge-success { background: #dcfce7; color: #166534; }
    .badge-warning { background: #fef3c7; color: #92400e; }
    .badge-info { background: #e0e7ff; color: #3730a3; }
    .badge-danger { background: #fee2e2; color: #991b1b; }
</style>
""", unsafe_allow_html=True)

# Initialize Session State (Phase 10)
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
if "url_analysis" not in st.session_state:
    st.session_state.url_analysis = None
if "last_url" not in st.session_state:
    st.session_state.last_url = ""
if "last_scrape_results" not in st.session_state:
    st.session_state.last_scrape_results = None
if "ai_summary_result" not in st.session_state:
    st.session_state.ai_summary_result = None

stop_event = threading.Event()

# Title Header
st.markdown('<div class="main-header">Universal E-Commerce Product Scraper</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Extract structured product catalogs, variants, pricing, and images from any e-commerce website URL</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar Controls
# ---------------------------------------------------------------------------
st.sidebar.header("⚙️ Extraction Settings")

max_products_val = st.sidebar.number_input(
    "Max Products to Extract",
    min_value=1,
    max_value=1000,
    value=20,
    step=5,
    help="Limit the total number of products to discover and scrape"
)

request_delay_val = st.sidebar.slider(
    "Request Delay (seconds)",
    min_value=0.2,
    max_value=5.0,
    value=float(config.REQUEST_DELAY),
    step=0.2,
    help="Polite rate limiting delay between consecutive requests"
)

use_browser_val = st.sidebar.toggle(
    "Use Headless Browser (Playwright)",
    value=True,
    help="Required for dynamic JavaScript SPAs (e.g. Temu, Shein, React/Vue storefronts)"
)

st.sidebar.markdown("---")
st.sidebar.subheader("Fields & Feature Toggles")
extract_images_val = st.sidebar.checkbox("Extract High-Res Images", value=True)
extract_variants_val = st.sidebar.checkbox("Extract Variant Matrix (Size/Color)", value=True)
extract_descriptions_val = st.sidebar.checkbox("Extract Full Descriptions", value=True)
resume_val = st.sidebar.checkbox("Resume Previous Scrape State", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("🤖 AI Processing (OpenRouter / OpenAI)")
ai_api_key = st.sidebar.text_input("API Key (Optional)", type="password", help="Optional API Key for AI catalog summarization")
ai_model_name = st.sidebar.selectbox("AI Model", ["openai/gpt-3.5-turbo", "anthropic/claude-3-haiku", "google/gemini-flash-1.5"], index=0)

st.sidebar.markdown("---")

# Quick Sample Links
st.sidebar.subheader("💡 Example URLs")
if st.sidebar.button("👗 Temu Listing Sample", use_container_width=True):
    st.session_state.preset_url = "https://www.temu.com/pk-en/womens-clothing-o3-28.html"
if st.sidebar.button("🛍️ Tehzeeb Libas Catalog", use_container_width=True):
    st.session_state.preset_url = "https://tehzeeblibas.com/collections/all"
if st.sidebar.button("📦 WooCommerce Store Sample", use_container_width=True):
    st.session_state.preset_url = "https://woocommerce.com/products/"

# ---------------------------------------------------------------------------
# Main URL Input & Analysis Section
# ---------------------------------------------------------------------------
default_url = st.session_state.get("preset_url", "")
url_input = st.text_input(
    "Paste Product / Category / Collection / Search URL:",
    value=default_url,
    placeholder="e.g. https://www.temu.com/pk-en/womens-clothing-o3-28.html or https://example.com/products/item-1"
)

# Detect URL change and clear old state if needed (Phase 10)
if url_input != st.session_state.last_url:
    st.session_state.last_url = url_input
    st.session_state.url_analysis = None
    st.session_state.ai_summary_result = None

col_act1, col_act2, col_act3 = st.columns([2, 2, 3])

with col_act1:
    analyze_btn = st.button("🔍 Analyze URL", use_container_width=True, disabled=st.session_state.scraping_in_progress)

with col_act2:
    start_btn = st.button("🚀 Start Extraction", use_container_width=True, disabled=st.session_state.scraping_in_progress or not url_input.strip())

with col_act3:
    stop_btn = st.button("⏸ Stop / Cancel", use_container_width=True, disabled=not st.session_state.scraping_in_progress)

# URL Analysis Execution (Phase 2, 5, 12)
if analyze_btn:
    cleaned_url = url_input.strip()
    if not cleaned_url:
        st.warning("Please enter a target URL to analyze.")
    else:
        is_safe, reason = validate_safe_url(cleaned_url)
        if not is_safe:
            st.error(f"⚠️ Security / URL Error: {reason}")
        else:
            with st.spinner("Analyzing target URL and detecting platform architecture..."):
                try:
                    engine = UniversalScrapingEngine()
                    analysis = engine.analyze_url(cleaned_url)
                    st.session_state.url_analysis = analysis
                except Exception as e:
                    st.error(f"Unable to analyze URL: {e}")

# Display Analysis Card if available
if st.session_state.url_analysis:
    an = st.session_state.url_analysis
    if "error" in an:
        st.error(f"URL Analysis Warning: {an['error']}")
    else:
        st.markdown(f"""
        <div class="analysis-card">
            <h4 style="margin-top:0; color:#4f46e5;">🌐 Website Analysis Result</h4>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; font-size: 0.95rem;">
                <div><strong>Target Domain:</strong><br/><code>{an.get('domain', 'N/A')}</code></div>
                <div><strong>Detected Platform:</strong><br/><span class="metric-badge badge-info">{an.get('platform', 'N/A')}</span></div>
                <div><strong>Detected Page Type:</strong><br/><span class="metric-badge badge-success">{an.get('page_type', 'N/A')}</span></div>
                <div><strong>Extraction Engine:</strong><br/><code>{an.get('adapter_name', 'Generic')}</code></div>
            </div>
            <div style="margin-top: 10px; font-size: 0.9rem; color: #4b5563;">
                <strong>Strategy:</strong> {an.get('extraction_method', 'Standard Multi-Strategy')} &nbsp;|&nbsp; 
                <strong>Browser JS Rendering:</strong> {'⚡ Required' if an.get('requires_js') else '✅ Not strictly required'}
            </div>
        </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Background Scraping Task Runner
# ---------------------------------------------------------------------------
def run_scrape_worker(target_url, max_p, delay, browser, imgs, vars_flag, descs, resume_flag):
    engine = UniversalScrapingEngine()

    def update_progress(data: Dict[str, Any]):
        st.session_state.current_status = data.get("status", "")
        st.session_state.progress_count = data.get("progress_count", 0)
        st.session_state.total_target = data.get("total_target", 0)
        st.session_state.current_product = data.get("current_product", "")

    st.session_state.scraping_in_progress = True
    st.session_state.current_status = "Starting extraction..."
    stop_event.clear()

    try:
        results = engine.scrape_url(
            url=target_url,
            max_products=max_p,
            request_delay=delay,
            use_browser=browser,
            extract_images=imgs,
            extract_variants=vars_flag,
            extract_descriptions=descs,
            resume=resume_flag,
            progress_callback=update_progress,
            stop_event=stop_event
        )
        st.session_state.last_scrape_results = results
        if results.get("success"):
            st.session_state.current_status = f"✅ Completed! Extracted {len(results.get('products', []))} products."
        else:
            st.session_state.current_status = f"⚠️ {results.get('message', 'Extraction ended.')}"
    except Exception as e:
        logger.error(f"Scraping thread error: {e}")
        st.session_state.current_status = f"❌ Error: {e}"
    finally:
        st.session_state.scraping_in_progress = False

if start_btn:
    cleaned_url = url_input.strip()
    if cleaned_url and not st.session_state.scraping_in_progress:
        is_safe, reason = validate_safe_url(cleaned_url)
        if not is_safe:
            st.error(f"⚠️ Access Blocked: {reason}")
        else:
            st.session_state.progress_count = 0
            st.session_state.total_target = 0
            st.session_state.ai_summary_result = None

            worker_thread = threading.Thread(
                target=run_scrape_worker,
                args=(
                    cleaned_url,
                    max_products_val,
                    request_delay_val,
                    use_browser_val,
                    extract_images_val,
                    extract_variants_val,
                    extract_descriptions_val,
                    resume_val,
                ),
                daemon=True
            )
            worker_thread.start()
            st.rerun()

if stop_btn:
    stop_event.set()
    st.session_state.stop_requested = True
    st.warning("Stop requested. Halting scraper gracefully...")

# ---------------------------------------------------------------------------
# Live Status & Metrics Section
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("📊 Live Status & Metrics")

status_col1, status_col2 = st.columns([3, 1])
with status_col1:
    if "❌" in st.session_state.current_status:
        st.error(f"**Status:** {st.session_state.current_status}")
    elif "⚠️" in st.session_state.current_status:
        st.warning(f"**Status:** {st.session_state.current_status}")
    else:
        st.info(f"**Status:** {st.session_state.current_status}")

with status_col2:
    if st.session_state.scraping_in_progress:
        st.warning("⚡ Scraping active...")
    else:
        st.success("✅ System ready")

if st.session_state.scraping_in_progress or st.session_state.progress_count > 0:
    total = st.session_state.total_target if st.session_state.total_target > 0 else 1
    pct = min(st.session_state.progress_count / total, 1.0)
    st.progress(pct)
    st.caption(f"Current Item: **{st.session_state.current_product}** | Processed: **{st.session_state.progress_count} / {st.session_state.total_target}** ({int(pct*100)}%)")

# Calculate metrics from final files if present
discovered_val = 0
scraped_val = 0
failed_val = 0
dupes_val = 0
missing_price_val = 0
missing_img_val = 0

if config.REPORT_TXT_PATH.exists():
    try:
        report_text = config.REPORT_TXT_PATH.read_text(encoding="utf-8")
        for line in report_text.splitlines():
            if "Total Products Discovered:" in line:
                discovered_val = int(line.split(":")[-1].strip())
            elif "Total Products Extracted:" in line or "Total Products Scraped:" in line:
                scraped_val = int(line.split(":")[-1].strip())
            elif "Total Failed" in line:
                failed_val = int(line.split(":")[-1].strip())
            elif "Duplicates Removed:" in line:
                dupes_val = int(line.split(":")[-1].strip())
            elif "Products Missing Price:" in line:
                missing_price_val = int(line.split(":")[-1].strip())
            elif "Products Missing Image:" in line:
                missing_img_val = int(line.split(":")[-1].strip())
    except Exception:
        pass

m_col1, m_col2, m_col3, m_col4, m_col5, m_col6 = st.columns(6)
m_col1.metric("Discovered", discovered_val)
m_col2.metric("Extracted", scraped_val)
m_col3.metric("Failed", failed_val)
m_col4.metric("Duplicates", dupes_val)
m_col5.metric("Missing Img", missing_img_val)
m_col6.metric("Missing Price", missing_price_val)

# ---------------------------------------------------------------------------
# Data Preview & Downloads Tabs
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("📦 Results & Dataset Preview")

tab_preview, tab_json, tab_ai, tab_report, tab_logs = st.tabs([
    "📊 Product Table Preview",
    "🔍 Structured JSON View",
    "🤖 AI Insights & Summary",
    "📄 Data Quality Report",
    "📜 Live Scraper Logs"
])

with tab_preview:
    active_csv = config.UNIVERSAL_CSV_PATH if config.UNIVERSAL_CSV_PATH.exists() else config.FINAL_CSV_PATH
    if active_csv.exists() and active_csv.stat().st_size > 50:
        try:
            df = pd.read_csv(active_csv, encoding="utf-8-sig")
            st.write(f"Displaying **{len(df)}** extracted products:")
            
            preview_cols = [
                c for c in [
                    "product_name", "price", "sale_price", "original_price", "currency",
                    "brand", "category", "availability", "variant_count", "main_image", "product_url"
                ] if c in df.columns
            ]
            st.dataframe(
                df[preview_cols] if preview_cols else df,
                use_container_width=True,
                hide_index=True
            )
        except Exception as e:
            st.error(f"Could not load preview table: {e}")
    else:
        st.info("No scraped dataset available yet. Paste a URL and click 'Start Extraction' above.")

with tab_json:
    if config.UNIVERSAL_JSON_PATH.exists() and config.UNIVERSAL_JSON_PATH.stat().st_size > 10:
        try:
            with open(config.UNIVERSAL_JSON_PATH, "r", encoding="utf-8") as jf:
                raw_json = json.load(jf)
                st.write(f"Structured JSON tree ({len(raw_json)} items):")
                st.json(raw_json[:5] if len(raw_json) > 5 else raw_json)
        except Exception as e:
            st.error(f"Could not parse JSON dataset: {e}")
    else:
        st.info("No JSON dataset generated yet.")

with tab_ai:
    st.write("### 🤖 AI Catalog Insights")
    if st.button("✨ Generate AI Catalog Analysis", key="btn_ai_gen"):
        if config.UNIVERSAL_JSON_PATH.exists() and config.UNIVERSAL_JSON_PATH.stat().st_size > 10:
            with st.spinner("Analyzing catalog text with AI..."):
                try:
                    with open(config.UNIVERSAL_JSON_PATH, "r", encoding="utf-8") as jf:
                        products_data = json.load(jf)
                    summary_text = "\n".join([
                        f"Title: {p.get('product_name')} | Price: {p.get('price')} {p.get('currency')} | Category: {p.get('category')} | Brand: {p.get('brand')}"
                        for p in products_data[:20]
                    ])
                    ai_res = summarize_scraped_data_with_ai(summary_text, api_key=ai_api_key, model=ai_model_name)
                    st.session_state.ai_summary_result = ai_res
                except Exception as e:
                    st.error(f"AI summarization failed: {e}")
        else:
            st.warning("No scraped data available to analyze yet. Please run an extraction first.")

    if st.session_state.ai_summary_result:
        res = st.session_state.ai_summary_result
        if res.get("success"):
            data = res.get("data", {})
            st.success(f"**{data.get('title')}** ({data.get('business_type')})")
            st.write(f"**Summary:** {data.get('summary')}")
            if data.get("key_highlights"):
                st.write("**Key Highlights:**")
                for h in data["key_highlights"]:
                    st.markdown(f"- {h}")
        else:
            st.error(f"AI Analysis Notice: {res.get('error')}")

with tab_report:
    if config.REPORT_TXT_PATH.exists():
        st.code(config.REPORT_TXT_PATH.read_text(encoding="utf-8"), language="text")
    else:
        st.info("No quality report available yet.")

with tab_logs:
    if config.LOG_FILE_PATH.exists():
        try:
            log_lines = config.LOG_FILE_PATH.read_text(encoding="utf-8").splitlines()
            st.code("\n".join(log_lines[-40:]), language="text")
        except Exception:
            st.info("Logs empty.")
    else:
        st.info("No logs generated yet.")

# ---------------------------------------------------------------------------
# Download Center (Phase 11)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("📥 Export Center")

d_col1, d_col2, d_col3, d_col4 = st.columns(4)

with d_col1:
    csv_file = config.UNIVERSAL_CSV_PATH if config.UNIVERSAL_CSV_PATH.exists() else config.FINAL_CSV_PATH
    if csv_file.exists() and csv_file.stat().st_size > 10:
        st.download_button(
            label="📥 Download CSV Dataset",
            data=csv_file.read_bytes(),
            file_name="products.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.button("📥 Download CSV", disabled=True, use_container_width=True)

with d_col2:
    if config.UNIVERSAL_JSON_PATH.exists() and config.UNIVERSAL_JSON_PATH.stat().st_size > 10:
        st.download_button(
            label="📥 Download JSON Dataset",
            data=config.UNIVERSAL_JSON_PATH.read_bytes(),
            file_name="products.json",
            mime="application/json",
            use_container_width=True
        )
    else:
        st.button("📥 Download JSON", disabled=True, use_container_width=True)

with d_col3:
    if config.REPORT_TXT_PATH.exists():
        st.download_button(
            label="📄 Download Scrape Report",
            data=config.REPORT_TXT_PATH.read_bytes(),
            file_name="scrape_report.txt",
            mime="text/plain",
            use_container_width=True
        )
    else:
        st.button("📄 Download Report", disabled=True, use_container_width=True)

with d_col4:
    if config.LOG_FILE_PATH.exists():
        st.download_button(
            label="📜 Download Log File",
            data=config.LOG_FILE_PATH.read_bytes(),
            file_name="scraper.log",
            mime="text/plain",
            use_container_width=True
        )
    else:
        st.button("📜 Download Logs", disabled=True, use_container_width=True)
