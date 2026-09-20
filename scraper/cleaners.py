import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Tuple, Optional
import config

FABRIC_KEYWORDS = [
    "Arabic Lawn", "Lawn", "Chiffon", "Linen", "Cotton", "Voile", 
    "Organza", "Khaddar", "Silk", "Velvet", "Grip", "Jacquard"
]

def clean_html_description(html_content: str) -> str:
    """
    Converts HTML description string to clean readable plain text.
    Preserves meaningful formatting while stripping tags and excess whitespace.
    """
    if not html_content:
        return ""
    
    # Replace breaks and list items with newline markers
    html_str = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
    html_str = re.sub(r'</p>', '\n', html_str, flags=re.IGNORECASE)
    html_str = re.sub(r'</li>', '\n', html_str, flags=re.IGNORECASE)

    soup = BeautifulSoup(html_str, 'html.parser')
    text = soup.get_text()

    # Clean multiple blank lines and carriage returns
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_text = ' '.join(lines)
    return cleaned_text

def normalize_price(price_val: Any) -> Optional[float]:
    """
    Normalizes price string or float into numeric float/int.
    Example: 'Rs. 5,999' -> 5999
    """
    if price_val is None:
        return None
    if isinstance(price_val, (int, float)):
        return float(price_val) if not isinstance(price_val, bool) else None
    
    s = str(price_val).strip()
    if not s:
        return None
    
    # Match numbers with optional commas and decimal places (e.g. 5,999 or 12,500.00)
    match = re.search(r'(\d[\d,]*(\.\d+)?)', s)
    if not match:
        return None
    
    num_str = match.group(1).replace(',', '')
    try:
        val = float(num_str)
        return int(val) if val.is_integer() else val
    except ValueError:
        return None

def calculate_discount_percentage(original_price: Optional[float], sale_price: Optional[float]) -> str:
    """
    Calculates discount percentage formatted string if original > sale.
    Example: (10000, 7500) -> '25%'
    """
    if original_price and sale_price and original_price > sale_price > 0:
        discount = ((original_price - sale_price) / original_price) * 100
        return f"{round(discount)}%"
    return ""

def normalize_url(url: str) -> str:
    """
    Converts relative URL to absolute URL and strips excess whitespace.
    """
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    elif url.startswith("/"):
        return f"{config.BASE_URL}{url}"
    elif not url.startswith("http://") and not url.startswith("https://"):
        return f"https://{url}"
    return url

def process_images(images: List[Dict[str, Any]]) -> Tuple[str, str]:
    """
    Extracts high quality image URLs, deduplicates, and splits into:
    (main_image_url, pipe_separated_additional_images)
    """
    seen_urls = set()
    cleaned_urls = []

    for img in images:
        src = img.get("src") if isinstance(img, dict) else str(img)
        if src:
            abs_url = normalize_url(src)
            # Remove high dynamic query parameters if present, keep standard CDN clean link
            base_url = abs_url.split("?")[0] if "?" in abs_url else abs_url
            if base_url not in seen_urls:
                seen_urls.add(base_url)
                cleaned_urls.append(abs_url)

    if not cleaned_urls:
        return "", ""
    
    main_image = cleaned_urls[0]
    additional_images = "|".join(cleaned_urls[1:]) if len(cleaned_urls) > 1 else ""
    return main_image, additional_images

def extract_material_and_pieces(text: str, tags: List[str]) -> Tuple[str, str]:
    """
    Extracts fabric material and pieces info from text/tags.
    """
    found_fabrics = []
    combined = (text + " " + " ".join(tags)).lower()

    for fabric in FABRIC_KEYWORDS:
        if fabric.lower() in combined:
            found_fabrics.append(fabric)

    # Deduplicate fabrics while preserving order
    unique_fabrics = list(dict.fromkeys(found_fabrics))
    material_str = ", ".join(unique_fabrics) if unique_fabrics else ""

    # Check pieces
    pieces_str = ""
    if "3 pc" in combined or "3-piece" in combined or "3pc" in combined or "3 piece" in combined:
        pieces_str = "3 Piece"
    elif "2 pc" in combined or "2-piece" in combined or "2pc" in combined or "2 piece" in combined:
        pieces_str = "2 Piece"
    elif "1 pc" in combined or "1-piece" in combined or "1pc" in combined or "1 piece" in combined:
        pieces_str = "1 Piece"

    return material_str, pieces_str

def deduplicate_products(products: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Deduplicates a list of product dictionaries based on product_id or product_url.
    Returns (deduplicated_list, duplicate_count).
    """
    seen_keys = set()
    unique_products = []
    duplicate_count = 0

    for p in products:
        key = str(p.get("product_id") or p.get("product_url") or p.get("sku") or "")
        if key and key in seen_keys:
            duplicate_count += 1
        else:
            if key:
                seen_keys.add(key)
            unique_products.append(p)

    return unique_products, duplicate_count
