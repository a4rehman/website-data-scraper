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
    data = fetch_json(json_url)
    products: List[Dict[str, Any]] = []
    if data and isinstance(data, dict) and data.get("products"):
        logger.info(f"Found {len(data['products'])} products via JSON endpoint")
        for raw in data["products"]:
            products.append({
                "product_name": raw.get("title", ""),
                "sku": raw.get("variants", [{}])[0].get("sku", ""),
                "price": raw.get("variants", [{}])[0].get("price", ""),
                "sale_price": raw.get("variants", [{}])[0].get("compare_at_price", ""),
                "product_url": f"{base}/products/{raw.get('handle', '')}",
                "image_url": raw.get("image", ""),
                "description": raw.get("body_html", ""),
                "category": "",
                "subcategory": "",
                "collection": "",
                "product_type": raw.get("product_type", ""),
                "brand": raw.get("vendor", ""),
                "availability": "In Stock" if raw.get("available", True) else "Out of Stock",
                "sizes": "",
                "colors": "",
                "variants": json.dumps(raw.get("variants", [])),
                "material": "",
                "tags": raw.get("tags", ""),
                "currency": raw.get("variants", [{}])[0].get("currency", ""),
                "original_price": raw.get("variants", [{}])[0].get("compare_at_price", ""),
                "discount_percentage": "",
                "additional_image_urls": json.dumps(raw.get("images", []))
            })
    else:
        logger.warning("JSON endpoint not available or empty, falling back to HTML parsing")
        html = fetch_html(base)
        if html:
            products.extend(_extract_minimal_from_html(html, base))
        else:
            logger.error(f"Failed to fetch any data from {base}")
    return products
