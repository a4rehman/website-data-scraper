import json
from typing import List, Dict, Any
import config
from scraper.utils import fetch_json, fetch_html, logger


def _extract_minimal_from_html(html: str, base_url: str) -> List[Dict[str, Any]]:
    """Fallback HTML parsing: extract title as product name and any meta price.
    This is a very naive implementation for non‑Shopify sites.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    products: List[Dict[str, Any]] = []
    # Attempt to find product-like sections – look for <meta property="og:title">
    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    title = title_tag["content"] if title_tag and title_tag.get("content") else (title_tag.get_text() if title_tag else "")
    price_tag = soup.find("meta", property="og:price:amount") or soup.find("meta", attrs={"itemprop": "price"})
    price = price_tag["content"] if price_tag and price_tag.get("content") else None
    if title:
        products.append({
            "product_id": "",
            "product_name": title.strip(),
            "price": price,
            "product_url": base_url,
            "sku": "",
            "image_url": "",
            "description": "",
            "category": "",
            "subcategory": "",
            "collection": "",
            "product_type": "",
            "brand": "",
            "availability": "",
            "stock_status": "",
            "sizes": "",
            "colors": "",
            "variants": "",
            "material": "",
            "tags": "",
            "currency": "",
            "original_price": "",
            "sale_price": "",
            "discount_percentage": "",
            "additional_image_urls": ""
        })
    return products


def scrape_custom_site(url: str) -> List[Dict[str, Any]]:
    """Scrape a generic website for product‑like info.
    Tries JSON endpoint at `<url>/products.json` first. If that fails, falls back to HTML parsing.
    Returns a list of dicts with keys compatible with the existing exporter.
    """
    base = url.rstrip('/')
    json_url = f"{base}/products.json"
    logger.info(f"Attempting to fetch JSON product feed from {json_url}")

    # Paginate through all pages of products.json
    all_raw_products: List[Dict[str, Any]] = []
    page = 1
    while True:
        data = fetch_json(json_url, params={"page": page, "limit": 250})
        if data and isinstance(data, dict) and data.get("products"):
            batch = data["products"]
            all_raw_products.extend(batch)
            logger.info(f"Page {page}: fetched {len(batch)} products (total: {len(all_raw_products)})")
            if len(batch) < 250:
                break
            page += 1
        else:
            break

    products: List[Dict[str, Any]] = []
    if all_raw_products:
        logger.info(f"Found {len(all_raw_products)} total products via JSON endpoint")
        for raw in all_raw_products:
            # image field can be a dict with "src" or a direct URL string
            raw_image = raw.get("image") or {}
            image_url = raw_image.get("src", "") if isinstance(raw_image, dict) else str(raw_image)
            # Extract additional image URLs
            raw_images = raw.get("images") or []
            additional_urls = [img.get("src", "") if isinstance(img, dict) else str(img) for img in raw_images]
            first_variant = raw.get("variants", [{}])[0] if raw.get("variants") else {}
            products.append({
                "product_id": str(raw.get("id", "")),
                "product_name": raw.get("title", ""),
                "sku": first_variant.get("sku", ""),
                "price": first_variant.get("price", ""),
                "sale_price": first_variant.get("compare_at_price", "") or "",
                "product_url": f"{base}/products/{raw.get('handle', '')}",
                "image_url": image_url,
                "description": raw.get("body_html", ""),
                "category": "",
                "subcategory": "",
                "collection": "",
                "product_type": raw.get("product_type", ""),
                "brand": raw.get("vendor", ""),
                "availability": "In Stock" if first_variant.get("available", True) else "Out of Stock",
                "stock_status": "In Stock" if first_variant.get("available", True) else "Out of Stock",
                "sizes": "",
                "colors": "",
                "variants": json.dumps(raw.get("variants", [])),
                "material": "",
                "tags": raw.get("tags", ""),
                "currency": first_variant.get("currency", ""),
                "original_price": first_variant.get("compare_at_price", "") or "",
                "discount_percentage": "",
                "additional_image_urls": ", ".join(additional_urls),
            })
    else:
        logger.warning("JSON endpoint not available or empty, falling back to HTML parsing")
        html = fetch_html(base)
        if html:
            products.extend(_extract_minimal_from_html(html, base))
        else:
            logger.error(f"Failed to fetch any data from {base}")
    return products
