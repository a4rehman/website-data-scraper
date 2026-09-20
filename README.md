# Website Data Scraper & Streamlit Dashboard

A production-ready Python web scraper and interactive Streamlit web application for extracting, cleaning, and exporting public product catalogs into CSV format.

---

## Features

- **Full Catalog Pagination & Discovery**: Scans standard public JSON endpoints (`/products.json`, `/collections.json`) to discover all published items across all categories.
- **24 Standardized Product Fields**: Extracts `product_id`, `sku`, `product_name`, `description`, `brand`, `category`, `subcategory`, `collection`, `product_type`, `product_url`, `image_url`, `additional_image_urls`, `price`, `sale_price`, `original_price`, `discount_percentage`, `currency`, `availability`, `stock_status`, `sizes`, `colors`, `variants`, `material`, `tags`.
- **Streamlit Web Dashboard**: Interactive web GUI (`streamlit_app.py`) for starting/stopping scrapes, viewing live progress bars, auditing metrics, viewing searchable product tables with thumbnail previews, and downloading CSV reports.
- **CLI & Automation Support**: Flexible CLI entry point (`main.py`) supporting `--test`, `--resume`, and `--category` flags.
- **Data Cleaners & Normalizers**: Strips HTML tags, normalizes prices (`Rs. 5,999` -> `5999`), computes discount percentages, standardizes relative links, and deduplicates image URLs while keeping main and secondary images organized.
- **Resume & Fault Tolerance**: Periodically saves progress to `data/progress.json` so interrupted jobs resume seamlessly. Logs errors safely to `logs/failed_products.csv`.
- **Streamlit Community Cloud Ready**: Uses relative pathing and background worker execution so the web UI stays non-blocking and responsive.

---

## Repository Structure

```
website-data-scraper/
├── scraper/
│   ├── __init__.py
│   ├── discovery.py         # Catalog Discovery & Category Filtering
│   ├── product_scraper.py   # Product Details Parser & Exception Logger
│   ├── parser.py            # Field Mapping & JSON Standardizer
│   ├── cleaners.py          # Data Cleaners & Price/URL Normalizers
│   ├── exporter.py          # CSV Exporter & Report Generator
│   └── utils.py             # Resilient HTTP Client & Logging Setup
│
├── data/                    # Generated Data Files (.gitignore excluded)
│   └── .gitkeep
├── logs/                    # System & Failed Logs (.gitignore excluded)
│   └── .gitkeep
├── tests/
│   ├── test_parser.py       # Parser Unit Tests
│   ├── test_cleaners.py     # Cleaner Unit Tests
│   └── test_exporter.py     # Exporter Unit Tests
│
├── streamlit_app.py         # Streamlit Web Application Dashboard
├── main.py                  # CLI Application Entry Point
├── config.py                # Environment & Relative Path Settings
├── requirements.txt         # Project Dependencies
├── .env.example             # Template Environment File
├── .gitignore               # Excludes secrets, cache, & output datasets
└── README.md                # Project Documentation
```

---

## Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/a4rehman/website-data-scraper.git
   cd website-data-scraper
   ```

2. **Create and Activate Virtual Environment**:
   ```bash
   # Windows:
   python -m venv venv
   venv\Scripts\activate

   # Linux/macOS:
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## Streamlit Web Dashboard

Launch the interactive web application locally:

```bash
streamlit run streamlit_app.py
```

### Dashboard Features:
- **Sidebar Controls**: Choose between `Test (10 Products)`, `Full Catalog`, `Resume Previous Scrape`, or `Category / Collection`.
- **Live Progress & Metrics**: Visual progress bar (`st.progress()`) showing active product title, item counts, and live metric cards.
- **Searchable Data Preview**: Filterable data table with clickable product links and thumbnail previews.
- **One-Click Exports**: Direct download buttons for `tehzeeb_libas_products.csv`, `scrape_report.txt`, and `failed_products.csv`.

---

## Command Line Interface (CLI)

The CLI application (`main.py`) provides full command-line capability:

```bash
# 1. Run a 10-Product Test Scrape
python main.py --test

# 2. Run Full Catalog Scrape
python main.py

# 3. Resume Interrupted Scrape
python main.py --resume

# 4. Scrape Specific Category/Collection
python main.py --category "Everyday Essentials"
```

---

## Running Unit Tests

Execute the automated test suite with `pytest`:

```bash
python -m pytest tests/
```

---

## Output CSV Schema (`tehzeeb_libas_products.csv`)

| Field | Description | Example |
|---|---|---|
| `product_id` | Unique product ID | `8114111348811` |
| `sku` | Primary SKU code | `PAK-ME2-S-4` |
| `product_name` | Product Title | `MAHAM EMBROIDERED 2PC` |
| `description` | Cleaned plain text description | `This exquisite Women's Farshi Shalwar Kurta...` |
| `brand` | Vendor / Brand | `Brand Name` |
| `category` | Product Type | `Daily/Basic Wear` |
| `subcategory` | Piece classification | `2 Piece` |
| `collection` | Associated collections | `Everyday Essentials` |
| `product_type` | Website Product Type | `Daily/Basic Wear` |
| `product_url` | Full product page URL | `https://example.com/products/maham-embroidered-2pc` |
| `image_url` | High-res primary image link | `https://cdn.example.com/.../IMG_5504.jpg` |
| `additional_image_urls` | Pipe-separated secondary images | `img2.jpg\|img3.jpg\|img4.jpg` |
| `price` | Active price (numeric PKR) | `3980` |
| `sale_price` | Sale price if on discount | `3980` |
| `original_price` | List price | `4380` |
| `discount_percentage` | Computed discount | `10%` |
| `currency` | Currency code | `PKR` |
| `availability` | Stock availability | `In Stock` |
| `stock_status` | Stock status | `In Stock` |
| `sizes` | Available sizes | `S\|M\|L\|XL` |
| `colors` | Available colors | `Black` |
| `variants` | Variant summary breakdown | `Size: S \| Price: 3980 \| SKU: PAK-ME2-S-4...` |
| `material` | Fabric material | `Lawn` |
| `tags` | Pipe-separated tags | `Everyday Wear\|Lawn\|2 Piece` |

---

## Deploying to Streamlit Community Cloud

1. Push your repository to GitHub.
2. Sign in to [Streamlit Community Cloud](https://streamlit.io/cloud).
3. Click **New App**, select your repository, and set Main file path to `streamlit_app.py`.
4. Click **Deploy!**
