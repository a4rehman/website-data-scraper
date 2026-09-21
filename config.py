import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.getenv("BASE_URL", "https://tehzeeblibas.com").rstrip("/")
PRODUCTS_JSON_URL = f"{BASE_URL}/products.json"
COLLECTIONS_JSON_URL = f"{BASE_URL}/collections.json"

REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
TIMEOUT = int(os.getenv("TIMEOUT", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Universal Schema Columns for CSV Export
UNIVERSAL_CSV_COLUMNS = [
    "source_domain",
    "source_url",
    "product_id",
    "sku",
    "product_name",
    "title",
    "description",
    "short_description",
    "brand",
    "vendor",
    "seller",
    "category",
    "subcategory",
    "collection",
    "product_type",
    "tags",
    "product_url",
    "price",
    "sale_price",
    "original_price",
    "list_price",
    "discount_percentage",
    "currency",
    "availability",
    "stock_status",
    "colors",
    "sizes",
    "material",
    "fabric",
    "pattern",
    "variants",
    "variant_count",
    "main_image",
    "additional_images",
    "all_images",
    "video_url",
    "specifications",
    "rating",
    "review_count",
    "sold_count",
    "shipping_information",
    "scraped_at",
]

# Output Paths
UNIVERSAL_CSV_PATH = DATA_DIR / "products.csv"
UNIVERSAL_JSON_PATH = DATA_DIR / "products.json"
RAW_CSV_PATH = DATA_DIR / "products_raw.csv"
CLEAN_CSV_PATH = DATA_DIR / "products_clean.csv"
FINAL_CSV_PATH = DATA_DIR / "tehzeeb_libas_products.csv"
PROGRESS_JSON_PATH = DATA_DIR / "progress.json"
REPORT_TXT_PATH = DATA_DIR / "scrape_report.txt"

LOG_FILE_PATH = LOGS_DIR / "scraper.log"
FAILED_PRODUCTS_PATH = LOGS_DIR / "failed_products.csv"

# Custom scrape output CSV path
CUSTOM_CSV_PATH = DATA_DIR / "custom_site_products.csv"
