import json
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin
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

class GenericAdapter(ProductAdapter):
    name: str = "Generic E-Commerce"

    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        # Fallback adapter that can handle any URL
        return True

    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()

        # Check page type indicators
        if any(kw in path for kw in ["/product/", "/p/", "/item/", "/dp/", "/goods/", "/buy/"]):
            page_type = "Product"
        elif any(kw in path for kw in ["/category/", "/collection/", "/collections/", "/c/", "/cat/", "/shop/", "/catalog/"]):
            page_type = "Category / Listing"
        elif any(kw in path for kw in ["/search", "search_key=", "q="]):
            page_type = "Search"
        else:
            page_type = "General Listing / Catalog"

        # Check for JSON-LD in HTML if available
        has_jsonld = False
        platform_name = "Generic / Custom"
        if html:
            if "application/ld+json" in html:
                has_jsonld = True
            if "magento" in html.lower() or "mage/" in html:
                platform_name = "Magento"
            elif "bigcommerce" in html.lower():
                platform_name = "BigCommerce"
            elif "prestashop" in html.lower():
                platform_name = "PrestaShop"
            elif "__NEXT_DATA__" in html:
                platform_name = "Next.js E-Commerce"

        return {
            "domain": parsed.netloc.replace("www.", ""),
            "platform": platform_name,
            "page_type": page_type,
            "requires_js": False,
            "has_jsonld": has_jsonld,
            "public_api_available": False,
            "extraction_method": "Multi-Strategy (JSON-LD → Embedded JSON → HTML Heuristics → Playwright)"
        }

    def discover_products(
        self,
        url: str,
        max_products: Optional[int] = None,
        use_browser: bool = False
    ) -> List[Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()

        # If clearly a single product URL, return it directly
        if any(kw in path for kw in ["/product/", "/p/", "/item/", "/dp/"]) and not any(kw in path for kw in ["/category/", "/categories/"]):
            return [url]

        logger.info(f"GenericAdapter: Discovering products from {url}")
        html = fetch_html(url)
        if not html:
            return [url]

        soup = BeautifulSoup(html, "html.parser")
        discovered_items: List[Any] = []
        seen_links = set()

        # Strategy 1: JSON-LD ItemList
        jsonld_items = self._extract_jsonld_listing(soup, url)
        if jsonld_items:
            logger.info(f"GenericAdapter: Found {len(jsonld_items)} items via JSON-LD ItemList")
            discovered_items.extend(jsonld_items)

        # Strategy 2: Next.js __NEXT_DATA__
        if not discovered_items:
            next_items = self._extract_nextjs_data(soup, url)
            if next_items:
                logger.info(f"GenericAdapter: Found {len(next_items)} items via Next.js hydration data")
                discovered_items.extend(next_items)

        # Strategy 3: HTML Product Link / Card Discovery
        if not discovered_items:
            cards = self._extract_html_product_cards(soup, url)
            if cards:
                logger.info(f"GenericAdapter: Found {len(cards)} product cards via semantic HTML selectors")
                discovered_items.extend(cards)

        # Strategy 4: Fallback to link crawler looking for product URL patterns
        if not discovered_items:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full_link = urljoin(url, href)
                parsed_link = urlparse(full_link)
                # Ensure same domain
                if parsed_link.netloc == parsed.netloc:
                    if any(kw in parsed_link.path.lower() for kw in ["/product/", "/p/", "/item/", "/goods/"]):
                        if full_link not in seen_links:
                            seen_links.add(full_link)
                            discovered_items.append(full_link)

        # Strategy 5: Playwright browser fallback if requested or no products found
        if (not discovered_items or use_browser) and len(discovered_items) < (max_products or 5):
            logger.info("GenericAdapter: Trying Playwright headless browser extraction...")
            browser_cards = self._discover_via_playwright(url, max_products=max_products)
            if browser_cards:
                discovered_items.extend(browser_cards)

        if not discovered_items:
            # Fallback to single URL
            return [url]

        if max_products and len(discovered_items) > max_products:
            discovered_items = discovered_items[:max_products]

        return discovered_items

    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False,
        html_content: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        # If pre-extracted dictionary with product data
        if isinstance(url_or_raw, dict) and (url_or_raw.get("product_name") or url_or_raw.get("name") or url_or_raw.get("title")):
            return normalize_universal_product(url_or_raw, source_url=source_url or url_or_raw.get("product_url", ""))

        # If URL string
        if isinstance(url_or_raw, str):
            product_url = url_or_raw
            html = html_content or fetch_html(product_url)

            if html:
                soup = BeautifulSoup(html, "html.parser")

                # Strategy 1: JSON-LD Product
                jsonld_res = self._try_jsonld_single(soup, product_url)
                if jsonld_res and jsonld_res.get("product_name"):
                    return jsonld_res

                # Strategy 2: Embedded Next.js single product
                next_res = self._try_nextjs_single(soup, product_url)
                if next_res and next_res.get("product_name"):
                    return next_res

                # Strategy 3: Microdata & OpenGraph HTML Extraction
                html_res = self._extract_single_html(soup, product_url)
                if html_res and html_res.get("product_name"):
                    return html_res

            # Strategy 4: Playwright fallback
            if use_browser:
                rendered_res = self._extract_via_playwright(product_url)
                if rendered_res:
                    return normalize_universal_product(rendered_res, source_url=product_url)

        return None

    def _try_jsonld_single(self, soup: BeautifulSoup, base_url: str) -> Optional[Dict[str, Any]]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except Exception:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "Product":
                    return self._parse_jsonld_product(item, base_url)
                elif "@graph" in item:
                    for node in item["@graph"]:
                        if node.get("@type") == "Product":
                            return self._parse_jsonld_product(node, base_url)
        return None

    def _parse_jsonld_product(self, item: Dict[str, Any], base_url: str) -> Dict[str, Any]:
        name = item.get("name", "")
        desc = clean_html_description(item.get("description", ""))
        sku = str(item.get("sku") or item.get("productID") or "")
        brand_val = item.get("brand")
        brand = brand_val.get("name", "") if isinstance(brand_val, dict) else str(brand_val or "")

        offers = item.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        price = offers.get("price") or offers.get("lowPrice", "")
        curr = offers.get("priceCurrency", "")
        avail_str = str(offers.get("availability", ""))
        avail = "In Stock" if "InStock" in avail_str else ("Out of Stock" if "OutOfStock" in avail_str else "In Stock")

        # Images
        raw_img = item.get("image", [])
        if isinstance(raw_img, (str, dict)):
            raw_img = [raw_img]
        main_img, add_imgs, all_imgs = process_images(raw_img, base_url=base_url)

        payload = {
            "source_url": base_url,
            "product_id": sku,
            "sku": sku,
            "product_name": name,
            "title": name,
            "description": desc,
            "brand": brand,
            "vendor": brand,
            "category": item.get("category", ""),
            "product_url": item.get("url") or offers.get("url") or base_url,
            "price": price,
            "currency": curr,
            "availability": avail,
            "stock_status": avail,
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": add_imgs,
            "all_images": all_imgs,
        }
        return normalize_universal_product(payload, source_url=base_url)

    def _extract_jsonld_listing(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        products = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except Exception:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "ItemList":
                    for elem in item.get("itemListElement", []):
                        inner = elem.get("item", elem)
                        if inner.get("@type") == "Product":
                            products.append(self._parse_jsonld_product(inner, base_url))
                        elif isinstance(inner, str) and inner.startswith("http"):
                            products.append({"product_url": inner})
        return products

    def _extract_nextjs_data(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        next_script = soup.find("script", id="__NEXT_DATA__")
        if not next_script or not next_script.string:
            return []
        try:
            data = json.loads(next_script.string)
            return self._search_dict_for_products(data, base_url)
        except Exception:
            return []

    def _search_dict_for_products(self, node: Any, base_url: str) -> List[Dict[str, Any]]:
        found = []
        if isinstance(node, dict):
            if ("title" in node or "name" in node) and ("price" in node or "priceRange" in node or "variants" in node):
                name = node.get("title") or node.get("name")
                price = node.get("price") or (node.get("priceRange") or {}).get("minVariantPrice", {}).get("amount")
                url = node.get("url") or node.get("slug") or node.get("handle")
                full_url = urljoin(base_url, str(url)) if url else base_url
                if name:
                    found.append({
                        "product_name": name,
                        "title": name,
                        "price": price,
                        "product_url": full_url,
                        "image_url": (node.get("image") or {}).get("url", "") if isinstance(node.get("image"), dict) else str(node.get("image") or "")
                    })

            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    found.extend(self._search_dict_for_products(v, base_url))
        elif isinstance(node, list):
            for elem in node:
                if isinstance(elem, (dict, list)):
                    found.extend(self._search_dict_for_products(elem, base_url))
        return found

    def _try_nextjs_single(self, soup: BeautifulSoup, base_url: str) -> Optional[Dict[str, Any]]:
        next_items = self._extract_nextjs_data(soup, base_url)
        if next_items:
            return normalize_universal_product(next_items[0], source_url=base_url)
        return None

    def _extract_html_product_cards(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        products = []
        seen = set()

        card_selectors = [
            "[data-testid*='product']", "[data-testid*='item']",
            ".product-card", ".productCard", ".product_card", ".ProductCard",
            ".product-item", ".productItem", ".goods-item", ".search-item",
            "article.product", "li.product"
        ]

        cards = []
        for sel in card_selectors:
            found = soup.select(sel)
            if found and len(found) >= 2:
                cards = found
                break

        for card in cards:
            name_el = card.find(["h2", "h3", "h4", "a", "span"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "prod"])) or card.find(["h2", "h3", "h4"])
            name = name_el.get_text(strip=True) if name_el else ""

            if not name or len(name) < 3 or name in seen:
                continue
            seen.add(name)

            price_el = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["price", "cost", "amount", "sale"]))
            price = price_el.get_text(strip=True) if price_el else ""
            if not price:
                price_match = re.search(r'[\$\€\£\₹\¥]\s*[\d,]+\.?\d*', card.get_text())
                price = price_match.group(0) if price_match else ""

            link_el = card.find("a", href=True)
            link = urljoin(base_url, link_el["href"]) if link_el else base_url

            img_el = card.find("img")
            img = img_el.get("src") or img_el.get("data-src") or "" if img_el else ""

            products.append({
                "product_name": name,
                "title": name,
                "price": price,
                "product_url": link,
                "image_url": img,
            })

        return products

    def _extract_single_html(self, soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
        # Title
        title_el = soup.find("h1") or soup.find("meta", property="og:title")
        title = title_el.get("content", "") if title_el and title_el.name == "meta" else (title_el.get_text(strip=True) if title_el else "")

        # Description
        desc_el = soup.find("meta", property="og:description") or soup.select_one(".product-description, #description, .description")
        desc = desc_el.get("content", "") if desc_el and desc_el.name == "meta" else (clean_html_description(str(desc_el)) if desc_el else "")

        # Price
        price_el = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount") or soup.select_one(".price, .product-price, [itemprop='price']")
        price = price_el.get("content", "") if price_el and price_el.name == "meta" else (price_el.get_text(strip=True) if price_el else "")

        # Brand
        brand_el = soup.find("meta", property="product:brand") or soup.select_one(".brand, .vendor")
        brand = brand_el.get("content", "") if brand_el and brand_el.name == "meta" else (brand_el.get_text(strip=True) if brand_el else "")

        # Images
        img_els = soup.select(".product-gallery img, .product-images img, [data-main-image], .product-photo img")
        imgs = [i.get("src") or i.get("data-src") for i in img_els if i.get("src") or i.get("data-src")]
        if not imgs:
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                imgs = [og_img["content"]]

        main_img, add_imgs, all_imgs = process_images(imgs, base_url=base_url)

        # Specifications table
        specs = {}
        for row in soup.select("table.specifications tr, table.product-attributes tr, .spec-table tr"):
            cols = row.find_all(["th", "td"])
            if len(cols) >= 2:
                k = cols[0].get_text(strip=True)
                v = cols[1].get_text(strip=True)
                if k and v:
                    specs[k] = v

        payload = {
            "source_url": base_url,
            "product_name": title,
            "title": title,
            "description": desc,
            "brand": brand,
            "vendor": brand,
            "product_url": base_url,
            "price": price,
            "availability": "In Stock",
            "stock_status": "In Stock",
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": add_imgs,
            "all_images": all_imgs,
            "specifications": specs,
        }
        return normalize_universal_product(payload, source_url=base_url)

    def _discover_via_playwright(self, url: str, max_products: Optional[int] = None) -> List[Dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = browser.new_context(user_agent=config.USER_AGENT, viewport={"width": 1920, "height": 1080})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(2000)
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, "html.parser")
                return self._extract_html_product_cards(soup, url)
        except Exception as e:
            logger.warning(f"GenericAdapter Playwright discovery error: {e}")
            return []

    def _extract_via_playwright(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = browser.new_context(user_agent=config.USER_AGENT, viewport={"width": 1920, "height": 1080})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, "html.parser")
                return self._extract_single_html(soup, url)
        except Exception as e:
            logger.warning(f"GenericAdapter Playwright single product error: {e}")
            return None
