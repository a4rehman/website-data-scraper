import os
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
JOBS_DIR = DATA_DIR / "jobs"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

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

# Resource Limits (can be overridden via env vars)
MAX_PRODUCTS_PER_JOB = int(os.getenv("MAX_PRODUCTS_PER_JOB", "500"))
MAX_PAGES_PER_JOB = int(os.getenv("MAX_PAGES_PER_JOB", "50"))
MAX_EXECUTION_SECONDS = int(os.getenv("MAX_EXECUTION_SECONDS", "300"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "10485760"))  # 10MB
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "5"))

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
    # Provenance fields (internal/debug)
    "price_source",
    "title_source",
    "image_source",
    "sku_source",
    "description_source",
    "availability_source",
    "category_source",
    "variants_source",
]

# Default output paths (for backward compatibility / CLI)
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


def get_job_paths(job_id: str) -> dict:
    """Returns paths for a specific job ID."""
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return {
        "job_dir": job_dir,
        "csv": job_dir / "products.csv",
        "json": job_dir / "products.json",
        "progress": job_dir / "progress.json",
        "report": job_dir / "scrape_report.txt",
        "logs": job_dir / "scraper.log",
        "failed": job_dir / "failed_products.csv",
    }


def generate_job_id() -> str:
    """Generates a unique job ID."""
    return uuid.uuid4().hex[:12]
