# Universal E-Commerce Product Data Extractor & Streamlit App

A modular, production-ready Python application and interactive Streamlit web interface for pasting **any e-commerce website URL** and automatically discovering, extracting, cleaning, and exporting complete product catalog data into standardized CSV and JSON formats.

---

## Key Features

- **Universal URL Extraction Engine**: Paste product URLs, collection/category links, or search pages from virtually any e-commerce site. Automatically detects the platform and selects the best extraction strategy.
- **Modular Adapter Architecture**: Includes platform adapters for:
  - **Shopify**: High-speed JSON endpoints (`/products.json`, `/collections.json`), variant matrix parsing, and CDN image URL normalization.
  - **WooCommerce**: WooCommerce Store API (`/wp-json/wc/store/v2/products`), REST endpoints, HTML structured markup, and variation form parsing.
  - **Temu**: Embedded script hydration parsing (`window._store`, `window.rawData`), anchor link discovery, and Playwright headless fallback.
  - **Generic Fallback Engine**: Schema.org JSON-LD structured data, Next.js hydration state (`__NEXT_DATA__`), OpenGraph meta properties, microdata heuristics, and dynamic Playwright JS rendering.
- **39-Field Universal Product Schema**: Captures deep product metadata including variant matrices, price history, currency, stock status, ratings, reviews, specifications, and pipe-separated images.
- **Interactive Streamlit Web Dashboard (`streamlit_app.py`)**:
  - **URL Analyzer**: Paste any URL, click "Analyze URL", and inspect domain, detected platform, page type, JS requirements, and available APIs.
  - **Live Progress Tracking**: Real-time progress bar, product counter, and status updates.
  - **Tabbed Results**: Browse data in interactive tables with thumbnail previews, raw JSON views, quality control reports, and live log feeds.
  - **Export Center**: Download generated `products.csv` and `products.json` directly from the UI.
- **Comprehensive CLI Application (`main.py`)**: Support for `--url`, `--test`, `--max-products`, `--delay`, `--browser`, `--resume`, `--no-images`, and `--no-variants`. Falls back gracefully to legacy catalog mode if `--url` is omitted.
- **Automated Test Suite**: 23 pytest cases covering adapter selection, Shopify/WooCommerce parsing, Temu normalization, schema validation, and exports.

---

## Repository Structure

```
website-data-scraper/
├── scraper/
│   ├── __init__.py          # Public package exports
│   ├── engine.py            # Universal Scraping Engine & URL Orchestrator
│   ├── adapters/            # Modular Adapter System
│   │   ├── __init__.py      # Adapter Registry & Factory Function
│   │   ├── base.py          # Abstract ProductAdapter Class
│   │   ├── shopify.py       # Shopify Adapter (products.json, collections)
│   │   ├── woocommerce.py   # WooCommerce Adapter (Store API, REST, HTML)
│   │   ├── temu.py          # Temu Adapter (Hydration state & Playwright)
│   │   └── generic.py        # Generic Engine (JSON-LD, Next.js, OpenGraph)
│   ├── discovery.py         # Catalog Discovery & Category Utilities
│   ├── product_scraper.py   # Legacy Product Scraper Engine
│   ├── parser.py            # Field Mapping & JSON Standardizer
│   ├── cleaners.py          # Universal Schema Cleaners & Price/URL Normalizers
│   ├── exporter.py          # CSV/JSON Exporters & Report Generator
│   └── utils.py             # Resilient HTTP Client & Logging Setup
│
├── data/                    # Generated Data Files (.gitignore excluded)
│   └── .gitkeep
├── logs/                    # System Logs (.gitignore excluded)
│   └── .gitkeep
├── tests/
│   ├── test_adapters.py     # Adapter Unit Tests
│   ├── test_engine.py       # Universal Engine Unit Tests
│   ├── test_cleaners.py     # Cleaner Unit Tests
│   ├── test_exporter.py     # Exporter Unit Tests
│   └── test_parser.py       # Parser Unit Tests
│
├── streamlit_app.py         # Universal Streamlit Web Dashboard
├── main.py                  # CLI Application Entry Point
├── config.py                # Schema Definitions & Application Settings
├── requirements.txt         # Project Dependencies
├── packages.txt             # System Level Dependencies (Playwright)
├── .env.example             # Template Environment File
├── .gitignore               # Secrets & Data Exclusion
└── README.md                # Project Documentation
```

---

## Installation & Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/a4rehman/website-data-scraper.git
   cd website-data-scraper
   ```

2. **Create & Activate Virtual Environment**:
   ```bash
   # Windows:
   python -m venv venv
   venv\Scripts\activate

   # Linux/macOS:
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Python Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright Browsers (Optional, for JS-heavy sites)**:
   ```bash
   playwright install chromium
   ```

---

## Streamlit Web Application

Launch the web app locally:

```bash
streamlit run streamlit_app.py
```

### Usage Steps:
1. Paste any e-commerce product or category URL in the main input field.
2. Click **Analyze URL** to view detected platform metadata.
3. Configure extraction settings in the sidebar (Max Products, Delay, Browser toggle).
4. Click **Start Extraction** and watch live progress.
5. Preview results in the Table and JSON tabs, then download your CSV or JSON data!

---

## Command Line Interface (CLI)

Run universal extractions directly from your terminal:

```bash
# Extract products from any e-commerce URL (Test mode: 5 products)
python main.py --url "https://tehzeeblibas.com/collections/all" --test

# Extract up to 20 products from a specific category
python main.py --url "https://example-shop.com/category/shoes" --max-products 20

# Enable Playwright headless browser for dynamic sites
python main.py --url "https://www.temu.com/pk-en/womens-clothing-o3-28.html" --browser --max-products 10

# Run legacy Tehzeeb Libas catalog extraction
python main.py --test
```

---

## Universal 39-Field CSV Schema

| Field Category | Fields Included |
|---|---|
| **Identity & Source** | `source_domain`, `source_url`, `product_id`, `sku`, `gtin_barcode` |
| **Basic Info** | `product_name`, `title`, `subtitle`, `description`, `short_description` |
| **Organization** | `brand`, `vendor`, `manufacturer`, `category`, `subcategory`, `collection`, `tags` |
| **Pricing & Sale** | `price`, `sale_price`, `original_price`, `discount_percentage`, `currency` |
| **Stock & Status** | `availability`, `stock_status`, `stock_quantity`, `is_on_sale` |
| **Images** | `main_image`, `image_url`, `additional_images`, `all_images` |
| **Variants & Specs** | `sizes`, `colors`, `variants`, `specifications` |
| **Reviews & Metadata** | `rating`, `review_count`, `sold_count`, `product_url`, `scraped_at` |

---

## Running Automated Tests

Run all unit and integration tests with `pytest`:

```bash
python -m pytest
```

---

## Live Demo & Deployment

The application is deployed on Streamlit Community Cloud:
[https://website-data-scraper.streamlit.app/](https://website-data-scraper.streamlit.app/)
