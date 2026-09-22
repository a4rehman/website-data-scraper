import re
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlparse, urljoin
import config

FABRIC_KEYWORDS = [
    "Arabic Lawn", "Lawn", "Chiffon", "Linen", "Cotton", "Voile", 
    "Organza", "Khaddar", "Silk", "Velvet", "Grip", "Jacquard",
    "Polyester", "Spandex", "Denim", "Wool", "Satin", "Rayon", "Fleece"
]

CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "¥": "JPY",
    "Rs.": "PKR",
    "Rs": "PKR",
    "PKR": "PKR",
    "USD": "USD",
    "EUR": "EUR",
    "GBP": "GBP",
    "INR": "INR",
    "CAD": "CAD",
    "AUD": "AUD",
}

def clean_html_description(html_content: str) -> str:
    """
    Converts HTML description string to clean readable plain text.
    Decomposes noise elements (script, style, nav, footer, etc.) while preserving
    meaningful article, main, section, paragraph, and list content separated by ' | '.
    """
    if not html_content:
        return ""
    
    # Pre-process linebreaks and list items
    html_str = re.sub(r'<br\s*/?>', '\n', str(html_content), flags=re.IGNORECASE)
    html_str = re.sub(r'</p>', '\n', html_str, flags=re.IGNORECASE)
    html_str = re.sub(r'</li>', ' | ', html_str, flags=re.IGNORECASE)
    html_str = re.sub(r'<li[^>]*>', '', html_str, flags=re.IGNORECASE)
    html_str = re.sub(r'</tr>', ' | ', html_str, flags=re.IGNORECASE)

    soup = BeautifulSoup(html_str, 'html.parser')
    
    # Remove non-content / noise elements
    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside", "form", "svg", "iframe"]):
        tag.decompose()

    text = soup.get_text()

    # Clean multiple blank lines and carriage returns
    lines = [line.strip().strip('|').strip() for line in text.splitlines() if line.strip()]
    cleaned_text = ' | '.join(line for line in lines if line)
    # Remove duplicate consecutive delimiters
    cleaned_text = re.sub(r'(\s*\|\s*)+', ' | ', cleaned_text)
    return cleaned_text.strip(' |')

def detect_currency(text: Any) -> str:
    """
    Detects currency code from string containing currency symbols or codes.
    """
    if not text:
        return ""
    s = str(text).upper()
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym.upper() in s:
            return code
    return ""

def normalize_price(price_val: Any) -> Optional[float]:
    """
    Normalizes price string or float into numeric float/int.
    Example: 'Rs. 5,999' -> 5999, '$14.99' -> 14.99
    """
    if price_val is None:
        return None
    if isinstance(price_val, (int, float)):
        return float(price_val) if not isinstance(price_val, bool) else None
    
    s = str(price_val).strip()
    if not s:
        return None
    
    # If price range like "14.99 - 25.99", extract lowest price
    if " - " in s:
        s = s.split(" - ")[0].strip()
    
    # Match numbers with optional commas and decimal places
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

def normalize_url(url: str, base_url: Optional[str] = None) -> str:
    """
    Converts relative URL to absolute URL and strips excess whitespace.
    """
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("//"):
        return f"https:{url}"
    elif url.startswith("/"):
        if base_url:
            parsed = urlparse(base_url)
            return f"{parsed.scheme or 'https'}://{parsed.netloc}{url}"
        return f"{config.BASE_URL}{url}"
    elif not url.startswith("http://") and not url.startswith("https://"):
        if base_url:
            return urljoin(base_url, url)
        return f"https://{url}"
    return url

def process_images(images: List[Any], base_url: Optional[str] = None) -> Tuple[str, str, str]:
    """
    Extracts high quality image URLs, deduplicates, and returns:
    (main_image, additional_images_pipe_separated, all_images_pipe_separated)
    """
    seen_urls = set()
    cleaned_urls = []

    for img in images:
        src = img.get("src") or img.get("url") if isinstance(img, dict) else str(img)
        if src:
            abs_url = normalize_url(src, base_url=base_url)
            # Normalize Shopify / CDN image sizing to max resolution
            abs_url = re.sub(r'_[0-9]+x[0-9]*\.(jpg|png|webp|jpeg)', r'.\1', abs_url)
            # Remove ephemeral dynamic tokens for deduplication key
            dedup_key = abs_url.split("?")[0] if "?" in abs_url else abs_url
            if dedup_key not in seen_urls:
                seen_urls.add(dedup_key)
                cleaned_urls.append(abs_url)

    if not cleaned_urls:
        return "", "", ""
    
    main_image = cleaned_urls[0]
    additional_images = "|".join(cleaned_urls[1:]) if len(cleaned_urls) > 1 else ""
    all_images = "|".join(cleaned_urls)
    return main_image, additional_images, all_images

def extract_material_and_pieces(text: str, tags: List[str] = None) -> Tuple[str, str]:
    """
    Extracts fabric material and pieces info from text/tags.
    """
    found_fabrics = []
    tags = tags or []
    combined = (str(text) + " " + " ".join(str(t) for t in tags)).lower()

    for fabric in FABRIC_KEYWORDS:
        if fabric.lower() in combined:
            found_fabrics.append(fabric)

    unique_fabrics = list(dict.fromkeys(found_fabrics))
    material_str = ", ".join(unique_fabrics) if unique_fabrics else ""

    pieces_str = ""
    if any(k in combined for k in ["3 pc", "3-piece", "3pc", "3 piece"]):
        pieces_str = "3 Piece"
    elif any(k in combined for k in ["2 pc", "2-piece", "2pc", "2 piece"]):
        pieces_str = "2 Piece"
    elif any(k in combined for k in ["1 pc", "1-piece", "1pc", "1 piece"]):
        pieces_str = "1 Piece"

    return material_str, pieces_str

def normalize_universal_product(data: Dict[str, Any], source_url: str = "") -> Dict[str, Any]:
    """
    Converts any input dictionary into the canonical 39-column Universal Schema format.
    Guarantees no missing keys, normalized pricing, correct timestamps, and proper typing.
    """
    product_name = data.get("product_name") or data.get("title") or data.get("name") or ""
    product_url = data.get("product_url") or data.get("url") or source_url or ""
    
    # Source domain extraction
    source_domain = ""
    target_url = product_url or source_url
    if target_url:
        try:
            parsed = urlparse(target_url)
            source_domain = parsed.netloc.replace("www.", "")
        except Exception:
            pass

    # Prices
    raw_price = data.get("price")
    raw_sale = data.get("sale_price")
    raw_orig = data.get("original_price") or data.get("list_price") or data.get("compare_at_price")
    
    norm_price = normalize_price(raw_price)
    norm_sale = normalize_price(raw_sale)
    norm_orig = normalize_price(raw_orig)

    # Determine active / sale / original relationship
    if norm_orig and norm_price and norm_orig > norm_price:
        if not norm_sale:
            norm_sale = norm_price
        discount_pct = data.get("discount_percentage") or calculate_discount_percentage(norm_orig, norm_sale)
    elif norm_sale and norm_orig and norm_orig > norm_sale:
        discount_pct = data.get("discount_percentage") or calculate_discount_percentage(norm_orig, norm_sale)
        if not norm_price:
            norm_price = norm_sale
    else:
        discount_pct = str(data.get("discount_percentage") or "")

    # Images
    main_img = data.get("main_image") or data.get("image_url") or ""
    add_imgs = data.get("additional_images") or data.get("additional_image_urls") or ""
    all_imgs = data.get("all_images") or ""

    if not all_imgs:
        img_parts = [main_img] if main_img else []
        if add_imgs:
            img_parts.extend(add_imgs.split("|"))
        all_imgs = "|".join([img for img in img_parts if img])

    # Variants
    variants_val = data.get("variants")
    variant_count = data.get("variant_count", 0)
    if isinstance(variants_val, list):
        variant_count = len(variants_val)
        variants_str = json.dumps(variants_val, ensure_ascii=False)
    elif isinstance(variants_val, str) and variants_val:
        variants_str = variants_val
        if variants_val.startswith("["):
            try:
                variant_count = len(json.loads(variants_val))
            except Exception:
                variant_count = 1
        elif "||" in variants_val:
            variant_count = len(variants_val.split("||"))
        else:
            variant_count = 1
    else:
        variants_str = ""
        variant_count = 0

    # Specifications
    specs_val = data.get("specifications")
    if isinstance(specs_val, (dict, list)):
        specs_str = json.dumps(specs_val, ensure_ascii=False)
    else:
        specs_str = str(specs_val or "")

    # Clean description
    raw_desc = data.get("description") or ""
    clean_desc = clean_html_description(raw_desc) if "<" in raw_desc else raw_desc.strip()

    # Availability
    avail = data.get("availability") or data.get("stock_status") or "In Stock"
    stock = data.get("stock_status") or avail

    # Currency
    curr = data.get("currency") or detect_currency(raw_price) or detect_currency(raw_orig) or ""

    # Timestamp
    scraped_at = data.get("scraped_at") or datetime.now(timezone.utc).isoformat()

    return {
        "source_domain": source_domain,
        "source_url": source_url or target_url,
        "product_id": str(data.get("product_id") or data.get("id") or ""),
        "sku": str(data.get("sku") or ""),
        "product_name": str(product_name).strip(),
        "title": str(data.get("title") or product_name).strip(),
        "description": clean_desc,
        "short_description": str(data.get("short_description") or ""),
        "brand": str(data.get("brand") or data.get("vendor") or ""),
        "vendor": str(data.get("vendor") or data.get("brand") or ""),
        "seller": str(data.get("seller") or data.get("vendor") or ""),
        "category": str(data.get("category") or ""),
        "subcategory": str(data.get("subcategory") or ""),
        "collection": str(data.get("collection") or ""),
        "product_type": str(data.get("product_type") or ""),
        "tags": str(data.get("tags") or ""),
        "product_url": str(product_url),
        "price": norm_price if norm_price is not None else "",
        "sale_price": norm_sale if norm_sale is not None else "",
        "original_price": norm_orig if norm_orig is not None else "",
        "list_price": norm_orig if norm_orig is not None else "",
        "discount_percentage": discount_pct,
        "currency": curr,
        "availability": avail,
        "stock_status": stock,
        "colors": str(data.get("colors") or data.get("color") or ""),
        "sizes": str(data.get("sizes") or data.get("size") or ""),
        "material": str(data.get("material") or data.get("fabric") or ""),
        "fabric": str(data.get("fabric") or data.get("material") or ""),
        "pattern": str(data.get("pattern") or ""),
        "variants": variants_str,
        "variant_count": variant_count,
        "main_image": str(main_img),
        "image_url": str(main_img),
        "additional_images": str(add_imgs),
        "all_images": str(all_imgs),
        "video_url": str(data.get("video_url") or ""),
        "specifications": specs_str,
        "rating": str(data.get("rating") or ""),
        "review_count": str(data.get("review_count") or ""),
        "sold_count": str(data.get("sold_count") or ""),
        "shipping_information": str(data.get("shipping_information") or ""),
        "scraped_at": scraped_at,
    }

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
