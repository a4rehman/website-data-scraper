"""
Generic website scraper with multiple extraction strategies.

Strategy order:
1. Shopify JSON API (/products.json) — works for Shopify stores
2. JSON-LD structured data — works for SEO-optimized sites
3. Playwright JS rendering — works for JS-heavy SPAs (Temu, Shein, etc.)
4. Basic HTML fallback — minimal metadata extraction
"""

import json
import re
import subprocess
import sys
from typing import List, Dict, Any, Optional
import config
from scraper.utils import fetch_json, fetch_html, logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_product_row(**overrides) -> Dict[str, Any]:
    """Return a product dict with all CSV_COLUMNS keys defaulting to empty string."""
    row = {
        "product_id": "", "sku": "", "product_name": "", "description": "",
        "brand": "", "category": "", "subcategory": "", "collection": "",
        "product_type": "", "product_url": "", "image_url": "",
        "additional_image_urls": "", "price": "", "sale_price": "",
        "original_price": "", "discount_percentage": "", "currency": "",
        "availability": "", "stock_status": "", "sizes": "", "colors": "",
        "variants": "", "material": "", "tags": "",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Strategy 1 — Shopify JSON API
# ---------------------------------------------------------------------------

def _try_shopify_json(base_url: str) -> List[Dict[str, Any]]:
    """Paginate through /products.json for Shopify stores."""
    json_url = f"{base_url}/products.json"
    logger.info(f"Strategy 1: Trying Shopify JSON at {json_url}")

    all_raw: List[Dict[str, Any]] = []
    page = 1
    while True:
        data = fetch_json(json_url, params={"page": page, "limit": 250})
        if data and isinstance(data, dict) and data.get("products"):
            batch = data["products"]
            all_raw.extend(batch)
            logger.info(f"  Page {page}: {len(batch)} products (total: {len(all_raw)})")
            if len(batch) < 250:
                break
            page += 1
        else:
            break

    if not all_raw:
        return []

    products: List[Dict[str, Any]] = []
    for raw in all_raw:
        raw_image = raw.get("image") or {}
        image_url = raw_image.get("src", "") if isinstance(raw_image, dict) else str(raw_image)
        raw_images = raw.get("images") or []
        additional = [img.get("src", "") if isinstance(img, dict) else str(img) for img in raw_images]
        fv = raw.get("variants", [{}])[0] if raw.get("variants") else {}
        products.append(_empty_product_row(
            product_id=str(raw.get("id", "")),
            product_name=raw.get("title", ""),
            sku=fv.get("sku", ""),
            price=fv.get("price", ""),
            sale_price=fv.get("compare_at_price", "") or "",
            original_price=fv.get("compare_at_price", "") or "",
            product_url=f"{base_url}/products/{raw.get('handle', '')}",
            image_url=image_url,
            additional_image_urls=", ".join(additional),
            description=raw.get("body_html", ""),
            product_type=raw.get("product_type", ""),
            brand=raw.get("vendor", ""),
            availability="In Stock" if fv.get("available", True) else "Out of Stock",
            stock_status="In Stock" if fv.get("available", True) else "Out of Stock",
            currency=fv.get("currency", ""),
            variants=json.dumps(raw.get("variants", [])),
            tags=raw.get("tags", ""),
        ))
    logger.info(f"Strategy 1 SUCCESS: {len(products)} products from Shopify JSON")
    return products


# ---------------------------------------------------------------------------
# Strategy 2 — JSON-LD Structured Data
# ---------------------------------------------------------------------------

def _try_jsonld(html: str, base_url: str) -> List[Dict[str, Any]]:
    """Extract product data from <script type='application/ld+json'> blocks."""
    from bs4 import BeautifulSoup
    logger.info("Strategy 2: Trying JSON-LD structured data")
    soup = BeautifulSoup(html, "html.parser")
    products: List[Dict[str, Any]] = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            item_type = item.get("@type", "")
            # Direct Product
            if item_type == "Product":
                products.append(_parse_jsonld_product(item, base_url))
            # ItemList containing Products
            elif item_type == "ItemList":
                for elem in item.get("itemListElement", []):
                    inner = elem.get("item", elem)
                    if inner.get("@type") == "Product":
                        products.append(_parse_jsonld_product(inner, base_url))
            # BreadcrumbList, WebSite, etc. — skip
            elif "@graph" in item:
                for node in item["@graph"]:
                    if node.get("@type") == "Product":
                        products.append(_parse_jsonld_product(node, base_url))

    if products:
        logger.info(f"Strategy 2 SUCCESS: {len(products)} products from JSON-LD")
    return products


def _parse_jsonld_product(item: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """Convert a single JSON-LD Product object to our standard dict."""
    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    image = item.get("image", "")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url", image.get("contentUrl", ""))

    return _empty_product_row(
        product_id=item.get("sku", item.get("productID", "")),
        product_name=item.get("name", ""),
        sku=item.get("sku", ""),
        price=offers.get("price", offers.get("lowPrice", "")),
        currency=offers.get("priceCurrency", ""),
        description=item.get("description", ""),
        brand=(item.get("brand", {}) or {}).get("name", "") if isinstance(item.get("brand"), dict) else str(item.get("brand", "")),
        image_url=image,
        product_url=item.get("url", offers.get("url", base_url)),
        availability="In Stock" if "InStock" in str(offers.get("availability", "")) else "Out of Stock",
        stock_status="In Stock" if "InStock" in str(offers.get("availability", "")) else "Out of Stock",
    )


# ---------------------------------------------------------------------------
# Strategy 3 — Playwright JS Rendering
# ---------------------------------------------------------------------------

def _ensure_playwright_browser() -> bool:
    """Install Chromium for Playwright if not already present."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, timeout=120,
        )
        return True
    except Exception as e:
        logger.warning(f"Could not install Playwright browser: {e}")
        return False


def _try_playwright(url: str) -> List[Dict[str, Any]]:
    """Render a page with a headless browser and extract products from the rendered HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Strategy 3: Playwright not installed — skipping JS rendering")
        return []

    logger.info("Strategy 3: Rendering page with Playwright (headless browser)")
    _ensure_playwright_browser()

    rendered_html = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                user_agent=config.USER_AGENT,
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=45000)
            # Scroll down to trigger lazy-loaded content
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)
            rendered_html = page.content()
            browser.close()
    except Exception as e:
        logger.warning(f"Playwright rendering failed: {e}")
        return []

    if not rendered_html:
        return []

    # First try JSON-LD from rendered HTML
    products = _try_jsonld(rendered_html, url)
    if products:
        return products

    # Otherwise parse product cards from rendered HTML
    products = _extract_product_cards(rendered_html, url)
    if products:
        logger.info(f"Strategy 3 SUCCESS: {len(products)} products from rendered HTML")
    return products


def _extract_product_cards(html: str, base_url: str) -> List[Dict[str, Any]]:
    """Heuristic extraction of product cards from rendered HTML.
    Looks for common e-commerce patterns: elements with price-like text near link/image combos.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    products: List[Dict[str, Any]] = []
    seen_names: set = set()

    # Common CSS selectors for product cards across e-commerce sites
    card_selectors = [
        "[data-testid*='product']", "[data-testid*='item']",
        "[class*='product-card']", "[class*='productCard']",
        "[class*='product_card']", "[class*='ProductCard']",
        "[class*='goods-item']", "[class*='goodsItem']",
        "[class*='item-card']", "[class*='itemCard']",
        "[class*='search-item']", "[class*='searchItem']",
        "article[class*='product']",
        "li[class*='product']",
        "div[class*='_2rn4l']",  # Temu
    ]

    cards = []
    for selector in card_selectors:
        found = soup.select(selector)
        if found and len(found) >= 2:  # At least 2 cards = likely a product list
            cards = found
            logger.info(f"  Found {len(cards)} product cards with selector: {selector}")
            break

    # Fallback: find elements containing price patterns
    if not cards:
        price_pattern = re.compile(r'[\$\€\£\₹\¥]\s*\d+[\.,]?\d*')
        all_links = soup.find_all("a", href=True)
        for link in all_links:
            parent = link.parent
            if parent and price_pattern.search(parent.get_text()):
                cards.append(parent)
        if cards:
            logger.info(f"  Found {len(cards)} potential product elements via price pattern")

    for card in cards:
        # Extract product name
        name_el = (
            card.find(["h2", "h3", "h4", "span", "a", "p"],
                       class_=lambda c: c and any(kw in str(c).lower() for kw in ["title", "name", "product"]))
            or card.find(["h2", "h3", "h4"])
            or card.find("a", href=True)
        )
        name = name_el.get_text(strip=True) if name_el else ""
        if not name or len(name) < 3 or name in seen_names:
            continue
        seen_names.add(name)

        # Extract price
        price_text = ""
        price_el = card.find(
            class_=lambda c: c and any(kw in str(c).lower() for kw in ["price", "cost", "amount"]))
        if price_el:
            price_text = price_el.get_text(strip=True)
        else:
            price_match = re.search(r'[\$\€\£\₹\¥]\s*[\d,]+\.?\d*', card.get_text())
            if price_match:
                price_text = price_match.group(0)

        # Extract image
        img = card.find("img", src=True)
        img_url = ""
        if img:
            img_url = img.get("src", "") or img.get("data-src", "") or img.get("data-lazy-src", "")
            if img_url.startswith("//"):
                img_url = "https:" + img_url

        # Extract link
        link = card.find("a", href=True)
        product_url = ""
        if link:
            href = link["href"]
            if href.startswith("http"):
                product_url = href
            elif href.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                product_url = f"{parsed.scheme}://{parsed.netloc}{href}"

        products.append(_empty_product_row(
            product_name=name[:200],
            price=price_text,
            image_url=img_url,
            product_url=product_url or base_url,
        ))

    return products


# ---------------------------------------------------------------------------
# Strategy 4 — Basic HTML Fallback
# ---------------------------------------------------------------------------

def _try_basic_html(html: str, base_url: str) -> List[Dict[str, Any]]:
    """Minimal fallback: extract Open Graph / meta tags."""
    from bs4 import BeautifulSoup
    logger.info("Strategy 4: Basic HTML meta-tag fallback")
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    title = ""
    if title_tag:
        title = title_tag.get("content", "") or title_tag.get_text(strip=True)

    price_tag = soup.find("meta", property="og:price:amount") or soup.find("meta", attrs={"itemprop": "price"})
    price = price_tag.get("content", "") if price_tag else ""

    img_tag = soup.find("meta", property="og:image")
    img = img_tag.get("content", "") if img_tag else ""

    if title:
        return [_empty_product_row(product_name=title.strip(), price=price, image_url=img, product_url=base_url)]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_custom_site(url: str) -> List[Dict[str, Any]]:
    """Scrape a generic website for product data using multiple strategies.

    1. Shopify JSON endpoint
    2. JSON-LD structured data from static HTML
    3. Playwright headless browser rendering (for JS-heavy sites)
    4. Basic HTML meta-tag fallback
    """
    base = url.rstrip("/")
    logger.info(f"=== Starting custom site scrape: {base} ===")

    # Strategy 1: Shopify JSON
    products = _try_shopify_json(base)
    if products:
        return products

    # Fetch static HTML (used by strategies 2 and 4)
    html = fetch_html(url)

    # Strategy 2: JSON-LD from static HTML
    if html:
        products = _try_jsonld(html, base)
        if products:
            return products

    # Strategy 3: Playwright JS rendering
    products = _try_playwright(url)
    if products:
        return products

    # Strategy 4: Basic HTML fallback
    if html:
        products = _try_basic_html(html, base)
        if products:
            return products

    logger.error(f"All strategies exhausted — no products found at {base}")
    return []
