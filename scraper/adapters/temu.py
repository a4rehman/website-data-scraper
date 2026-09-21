import json
import re
import subprocess
import sys
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin, parse_qs
from bs4 import BeautifulSoup
import config
from scraper.adapters.base import ProductAdapter
from scraper.cleaners import (
    normalize_universal_product,
    normalize_price,
    calculate_discount_percentage,
    process_images,
    clean_html_description
)
from scraper.utils import fetch_html, logger

class TemuAdapter(ProductAdapter):
    name: str = "Temu"

    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        return "temu.com" in url.lower()

    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()
        query = parsed.query.lower()

        if "goods.html" in path or "goods_id" in query or re.search(r'/g-[0-9a-z]+', path):
            page_type = "Product"
        elif "search_result.html" in path or "search_key=" in query:
            page_type = "Search"
        elif any(kw in path for kw in ["-o3-", "channel-", "category", "collection"]):
            page_type = "Category / Listing"
        else:
            page_type = "Category / Listing"

        return {
            "domain": "temu.com",
            "platform": "Temu",
            "page_type": page_type,
            "requires_js": True,
            "has_jsonld": False,
            "public_api_available": False,
            "extraction_method": "Embedded Hydration JSON & Playwright JS Engine"
        }

    def discover_products(
        self,
        url: str,
        max_products: Optional[int] = None,
        use_browser: bool = False
    ) -> List[Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()

        # If direct product URL
        if "goods.html" in path or "goods_id" in parsed.query:
            return [url]

        logger.info(f"Discovering Temu products from: {url}")
        discovered_items: List[Any] = []
        seen_ids = set()

        # Level 1: Static HTML Embedded State Extraction
        html = fetch_html(url)
        if html:
            embedded_products = self._extract_embedded_goods(html, url)
            for item in embedded_products:
                gid = str(item.get("goods_id") or item.get("product_id") or item.get("id") or "")
                if gid and gid not in seen_ids:
                    seen_ids.add(gid)
                    discovered_items.append(item)
                elif not gid:
                    discovered_items.append(item)

            if discovered_items:
                logger.info(f"Discovered {len(discovered_items)} products from Temu embedded HTML state")

        # Level 2: Headless Browser Rendering (Playwright) if static failed or browser explicitly enabled
        if (not discovered_items or use_browser) and len(discovered_items) < (max_products or 10):
            logger.info("Attempting Playwright browser discovery for Temu dynamic listings...")
            browser_products = self._discover_via_playwright(url, max_products=max_products)
            for item in browser_products:
                gid = str(item.get("goods_id") or item.get("product_id") or item.get("product_url") or "")
                if gid and gid not in seen_ids:
                    seen_ids.add(gid)
                    discovered_items.append(item)

        if not discovered_items:
            # Fallback to the target URL itself so single extraction can attempt
            logger.warning(f"No listing items discovered on Temu page. Retaining target URL: {url}")
            return [url]

        if max_products and len(discovered_items) > max_products:
            discovered_items = discovered_items[:max_products]

        return discovered_items

    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False
    ) -> Optional[Dict[str, Any]]:
        # If passed pre-extracted raw product dictionary from discovery
        if isinstance(url_or_raw, dict) and (url_or_raw.get("product_name") or url_or_raw.get("goods_id") or url_or_raw.get("title")):
            return self._normalize_temu_item(url_or_raw, source_url)

        # If passed URL string
        if isinstance(url_or_raw, str):
            product_url = url_or_raw
            # 1. Fetch static HTML & inspect embedded hydration state
            html = fetch_html(product_url)
            if html:
                item_data = self._extract_single_embedded_goods(html, product_url)
                if item_data and (item_data.get("product_name") or item_data.get("price")):
                    return self._normalize_temu_item(item_data, product_url)

            # 2. Browser rendering fallback for direct product URL
            if use_browser:
                logger.info(f"Rendering Temu product via Playwright: {product_url}")
                rendered_data = self._extract_via_playwright(product_url)
                if rendered_data:
                    return self._normalize_temu_item(rendered_data, product_url)

        return None

    def _extract_embedded_goods(self, html: str, base_url: str) -> List[Dict[str, Any]]:
        """Extracts goods list from embedded script tags and state in Temu HTML."""
        items: List[Dict[str, Any]] = []
        soup = BeautifulSoup(html, "html.parser")

        # 1. Search script tags for rawData / hydration objects / _store / json patterns
        scripts = soup.find_all("script")
        for s in scripts:
            content = s.string or s.text or ""
            if not content or len(content) < 20:
                continue

            # Look for JSON assignments: window.x = {...} or var x = {...}
            matches = re.findall(
                r'(?:window\.[a-zA-Z0-9_$]+|var\s+[a-zA-Z0-9_$]+)\s*=\s*(\{[\s\S]*?\});?\s*(?:</script>|\n|var|window|$)',
                content
            )
            if not matches:
                # Direct JSON-like objects containing goods
                if "goods" in content or "product" in content or "goods_id" in content:
                    matches = [content]

            for m in matches:
                try:
                    data = json.loads(m)
                    extracted = self._find_goods_in_json(data, base_url)
                    if extracted:
                        items.extend(extracted)
                except Exception:
                    # Regex search for goods dicts inside unparseable JS blocks
                    sub_matches = re.findall(r'(\{"goods_id"[^\}]+\})', m)
                    for sm in sub_matches:
                        try:
                            sd = json.loads(sm)
                            gid = str(sd.get("goods_id", ""))
                            name = sd.get("goods_name", "")
                            if gid or name:
                                items.append({
                                    "goods_id": gid,
                                    "product_id": gid,
                                    "product_name": name,
                                    "title": name,
                                    "price": sd.get("price", ""),
                                    "image_url": sd.get("thumb_url", ""),
                                    "product_url": urljoin(base_url, f"/goods.html?goods_id={gid}") if gid else base_url
                                })
                        except Exception:
                            continue

        # 2. Parse anchor links matching goods patterns
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(k in href for k in ["goods.html", "/g-", "goods_id", "goods_name", "goods"]):
                full_url = urljoin(base_url, href)
                title = a.get("title") or a.get_text(strip=True)
                
                # Check child elements for title if direct text is empty
                if not title or len(title) <= 3:
                    title_el = a.find(class_=lambda cl: cl and any(k in str(cl).lower() for k in ["title", "name", "desc"]))
                    if title_el:
                        title = title_el.get_text(strip=True)

                img = a.find("img")
                img_src = (img.get("src") or img.get("data-src") or img.get("data-original") or "") if img else ""
                
                if title and len(title) > 3:
                    items.append({
                        "product_name": title,
                        "title": title,
                        "product_url": full_url,
                        "image_url": img_src,
                    })

        return items

    def _find_goods_in_json(self, node: Any, base_url: str) -> List[Dict[str, Any]]:
        """Recursively traverses nested JSON to locate goods / product items."""
        found = []
        if isinstance(node, dict):
            if "goods_id" in node or "goodsId" in node or "goods_name" in node or "goodsName" in node:
                gid = str(node.get("goods_id") or node.get("goodsId", ""))
                name = node.get("goods_name") or node.get("goodsName") or node.get("title") or node.get("name", "")
                price = node.get("price") or node.get("sale_price") or node.get("display_price") or node.get("priceStr")
                orig_price = node.get("market_price") or node.get("original_price")
                img = node.get("thumb_url") or node.get("image_url") or node.get("hd_thumb_url") or node.get("imgUrl", "")
                link = node.get("link_url") or node.get("goods_url") or (f"/goods.html?goods_id={gid}" if gid else "")
                full_link = urljoin(base_url, link) if link else base_url

                if name or gid:
                    found.append({
                        "goods_id": gid,
                        "product_id": gid,
                        "product_name": name,
                        "title": name,
                        "price": price,
                        "original_price": orig_price,
                        "image_url": img,
                        "product_url": full_link,
                        "sales_tip": node.get("sales_tip") or node.get("sales_count", ""),
                        "rating": node.get("score") or node.get("rating", ""),
                    })

            for key, val in node.items():
                if isinstance(val, (dict, list)):
                    found.extend(self._find_goods_in_json(val, base_url))

        elif isinstance(node, list):
            for elem in node:
                if isinstance(elem, (dict, list)):
                    found.extend(self._find_goods_in_json(elem, base_url))

        return found

    def _extract_single_embedded_goods(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """Extracts detail information for a single Temu product page."""
        soup = BeautifulSoup(html, "html.parser")
        
        # Check meta tags
        title_meta = soup.find("meta", property="og:title") or soup.find("meta", name="title")
        desc_meta = soup.find("meta", property="og:description") or soup.find("meta", name="description")
        img_meta = soup.find("meta", property="og:image")
        
        title = title_meta.get("content", "") if title_meta else ""
        desc = desc_meta.get("content", "") if desc_meta else ""
        img = img_meta.get("content", "") if img_meta else ""

        # Price meta or span
        price = ""
        price_meta = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
        if price_meta:
            price = price_meta.get("content", "")

        # Script tags for detailed product payload
        scripts = soup.find_all("script")
        for s in scripts:
            content = s.string or s.text or ""
            if ("goods" in content or "product" in content) and "price" in content:
                price_match = re.search(r'["\'](?:displayPrice|price|salePrice)["\']\s*:\s*["\']?([0-9\.]+)["\']?', content)
                if price_match and not price:
                    price = price_match.group(1)

        if title:
            return {
                "product_name": title,
                "title": title,
                "description": desc,
                "price": price,
                "image_url": img,
                "product_url": url,
            }
        return None

    def _discover_via_playwright(self, url: str, max_products: Optional[int] = None) -> List[Dict[str, Any]]:
        """Uses Playwright headless browser to load dynamic Temu page and extract product cards."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not installed, skipping browser discovery.")
            return []

        products: List[Dict[str, Any]] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
                )
                context = browser.new_context(
                    user_agent=config.USER_AGENT,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
                )
                page = context.new_page()
                
                try:
                    page.goto(url, wait_until="commit", timeout=30000)
                except Exception as goto_err:
                    logger.debug(f"Playwright goto warning for Temu: {goto_err}")
                
                page.wait_for_timeout(4000)

                # Dismiss potential popups
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                
                # Scroll down in increments to trigger lazy-loaded goods
                for _ in range(3):
                    try:
                        page.evaluate("window.scrollBy(0, 1000)")
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass

                content = page.content()
                browser.close()

                # Parse rendered cards
                soup = BeautifulSoup(content, "html.parser")
                cards = soup.select("[data-testid*='goods'], [class*='_2rn4l'], [class*='goods-item'], div[data-goods-id], a[href*='goods']")
                
                if not cards:
                    cards = soup.select("div[class*='product'], div[class*='item'], div[class*='card'], a[href*='g-']")

                for c in cards:
                    if c.name == "a":
                        href = c.get("href", "")
                        name = c.get("title") or c.get_text(strip=True)
                        full_link = urljoin(url, href) if href else url
                        img = c.find("img")
                        img_src = (img.get("src") or img.get("data-src") or "") if img else ""
                        if name and len(name) > 3:
                            products.append({
                                "product_name": name,
                                "product_url": full_link,
                                "image_url": img_src,
                            })
                    else:
                        name_el = c.find(["h2", "h3", "h4", "span", "p", "a"], class_=lambda cl: cl and any(k in str(cl).lower() for k in ["title", "name", "desc"]))
                        name = name_el.get_text(strip=True) if name_el else ""

                        price_el = c.find(class_=lambda cl: cl and any(k in str(cl).lower() for k in ["price", "amount", "sale"]))
                        price = price_el.get_text(strip=True) if price_el else ""

                        link = c.find("a", href=True)
                        href = link["href"] if link else ""
                        full_link = urljoin(url, href) if href else url

                        img = c.find("img")
                        img_src = (img.get("src") or img.get("data-src") or "") if img else ""

                        if name and len(name) > 3:
                            products.append({
                                "product_name": name,
                                "price": price,
                                "product_url": full_link,
                                "image_url": img_src,
                            })

                    if max_products and len(products) >= max_products:
                        break

        except Exception as e:
            logger.warning(f"Playwright Temu discovery error: {e}")

        return products

    def _extract_via_playwright(self, url: str) -> Optional[Dict[str, Any]]:
        """Renders single Temu product page with Playwright."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = browser.new_context(user_agent=config.USER_AGENT, viewport={"width": 1920, "height": 1080})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, "html.parser")
                title_el = soup.find(["h1", "h2"])
                title = title_el.get_text(strip=True) if title_el else ""
                
                price_match = re.search(r'[\$\€\£\₹\¥]\s*[\d,]+\.?\d*', content)
                price = price_match.group(0) if price_match else ""

                img = soup.find("img", src=True)
                img_src = img["src"] if img else ""

                return {
                    "product_name": title,
                    "title": title,
                    "price": price,
                    "image_url": img_src,
                    "product_url": url,
                }
        except Exception as e:
            logger.warning(f"Playwright single product error: {e}")
            return None

    def _normalize_temu_item(self, item: Dict[str, Any], source_url: str) -> Dict[str, Any]:
        """Normalizes Temu raw dictionary into the Universal Schema."""
        name = item.get("product_name") or item.get("title") or ""
        gid = str(item.get("goods_id") or item.get("product_id") or "")
        product_url = item.get("product_url") or source_url
        
        main_img = item.get("image_url") or item.get("thumb_url") or ""
        price_val = item.get("price") or item.get("sale_price") or ""
        orig_val = item.get("original_price") or item.get("market_price") or ""

        norm_p = normalize_price(price_val)
        norm_o = normalize_price(orig_val)
        disc = calculate_discount_percentage(norm_o, norm_p) if norm_o and norm_p and norm_o > norm_p else ""

        payload = {
            "source_domain": "temu.com",
            "source_url": source_url or product_url,
            "product_id": gid,
            "sku": gid or f"TEMU-{gid}",
            "product_name": name,
            "title": name,
            "description": item.get("description", ""),
            "brand": "Temu",
            "vendor": "Temu Marketplace",
            "category": item.get("category", "General"),
            "product_url": product_url,
            "price": norm_p if norm_p is not None else price_val,
            "sale_price": norm_p if norm_p is not None else price_val,
            "original_price": norm_o if norm_o is not None else "",
            "discount_percentage": disc,
            "currency": "USD" if "$" in str(price_val) else ("PKR" if "PKR" in str(price_val) or "RS" in str(price_val).upper() else ""),
            "availability": "In Stock",
            "stock_status": "In Stock",
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": item.get("additional_images", ""),
            "all_images": main_img,
            "rating": str(item.get("rating", "")),
            "sold_count": str(item.get("sales_tip") or item.get("sold_count", "")),
        }
        return normalize_universal_product(payload, source_url=source_url or product_url)
