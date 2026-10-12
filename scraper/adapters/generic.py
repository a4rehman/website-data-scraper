import json
import re
from typing import List, Dict, Any, Optional, Union
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
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
from scraper.pagination import discover_with_pagination
from scraper.browser_manager import render_page, extract_with_playwright, BrowserConfig

# Discovery item schema (agreed across adapters/engine):
#   - dict with at least "product_url" (optional: product_name, price, image_url, ...)
#   - plain product URL string
DiscoveryItem = Union[str, Dict[str, Any]]

# Path markers that identify product detail pages (Shopify "/products/handle" included)
PRODUCT_PATH_MARKERS = ("/products/", "/product/", "/p/", "/item/", "/dp/", "/goods/", "/buy/")
# Path markers that identify non-product listing/navigation pages
NON_PRODUCT_PATH_MARKERS = (
    "/collections/", "/collection/", "/category/", "/categories/",
    "/cart", "/checkout", "/search", "/pages/", "/blogs/", "/about",
    "/contact", "/policy", "/policies/", "/account", "/faq",
)


def _is_product_path(path: str) -> bool:
    """True when a URL path looks like a product detail page."""
    path = path.lower().rstrip("/")
    if not path or path.endswith("/products") or path.endswith("/product"):
        return False
    if any(m in path for m in NON_PRODUCT_PATH_MARKERS):
        # /collections/x is a listing even if it contains /products later
        if "/products/" not in path:
            return False
    return any(path.startswith(m) or m in path for m in PRODUCT_PATH_MARKERS)


def _normalize_product_url(url: str, base_url: str) -> str:
    """Resolve relative/protocol-relative URLs and strip fragments and tracking params."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    full = urljoin(base_url, url)
    try:
        parsed = urlparse(full)
    except Exception:
        return full
    if parsed.scheme not in ("http", "https"):
        return ""
    # Drop fragment and common tracking query params; keep meaningful ones (e.g. variant)
    keep = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "mc_cid", "mc_eid")]
    query = urlencode(keep)
    normalized = urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))
    return normalized


class GenericAdapter(ProductAdapter):
    name: str = "Generic E-Commerce"

    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        # Fallback adapter that can handle any URL
        return True

    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()

        # Check page type indicators
        if _is_product_path(path):
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
        use_browser: bool = False,
        stop_event=None,
        progress_callback=None,
    ) -> List[DiscoveryItem]:
        parsed = urlparse(url)

        # If clearly a single product URL, return it directly
        if _is_product_path(parsed.path):
            return [url]

        logger.info(f"GenericAdapter: Discovering products from {url}")
        base_netloc = parsed.netloc.lower()

        def extract_from_page(page_url: str) -> List[Dict[str, Any]]:
            html = fetch_html(page_url)
            browser_note = ""
            if not html and use_browser:
                logger.info(f"Static HTML empty for {page_url}; attempting browser rendering...")
                html = render_page(page_url, scroll=True, config=BrowserConfig(timeout=45000))
                if not html:
                    browser_note = "browser rendering unavailable or failed (Chromium missing/blocked?)"
                    logger.warning(f"Browser rendering failed for {page_url}: {browser_note}")

            if not html:
                logger.warning(f"No HTML retrieved from {page_url} (blocked, empty, or fetch failed)")
                return []

            soup = BeautifulSoup(html, "html.parser")
            items: List[Dict[str, Any]] = []

            # Strategy 1: JSON-LD ItemList / Product objects
            jsonld_items = self._extract_jsonld_listing(soup, page_url)
            if jsonld_items:
                items.extend(jsonld_items)

            # Strategy 2: Next.js __NEXT_DATA__
            if not items:
                next_items = self._extract_nextjs_data(soup, page_url)
                if next_items:
                    items.extend(next_items)

            # Strategy 3: HTML Product Link / Card Discovery
            if not items:
                cards = self._extract_html_product_cards(soup, page_url)
                if cards:
                    items.extend(cards)

            # Strategy 4: Fallback to generic product-link crawl (same domain only)
            if not items:
                seen_links = set()
                for a in soup.find_all("a", href=True):
                    full_link = _normalize_product_url(a["href"], page_url)
                    if not full_link:
                        continue
                    if urlparse(full_link).netloc.lower() != base_netloc:
                        continue
                    if _is_product_path(urlparse(full_link).path) and full_link not in seen_links:
                        seen_links.add(full_link)
                        items.append({"product_url": full_link})

            return items

        # Use pagination engine to discover across all pages
        discovered_items = discover_with_pagination(
            url=url,
            extract_products_fn=extract_from_page,
            max_products=max_products,
            use_browser=use_browser,
            stop_event=stop_event,
            progress_callback=progress_callback,
        )

        if not discovered_items:
            # Explicit discovery failure — NEVER return the category/collection URL
            # as a fake product. The engine surfaces this as a clear Failed state.
            logger.warning(
                f"GenericAdapter: discovery failed for {url} — no product items found "
                "(site may block automated access or require JavaScript rendering)."
            )
            return []

        # Dedupe by product URL, preserving order
        seen = set()
        deduped: List[DiscoveryItem] = []
        for item in discovered_items:
            key = item.get("product_url", "") if isinstance(item, dict) else str(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append(item)
        return deduped

    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False,
        html_content: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        # If pre-extracted dictionary with product data
        if isinstance(url_or_raw, dict):
            # Discovery dicts may be {"product_url": ...} only, or carry full fields.
            product_url = str(url_or_raw.get("product_url") or url_or_raw.get("url") or "")
            has_payload = bool(
                url_or_raw.get("product_name") or url_or_raw.get("name") or url_or_raw.get("title")
            )
            if has_payload:
                return normalize_universal_product(
                    url_or_raw, source_url=source_url or product_url
                )
            if product_url:
                return self.extract_product(
                    product_url,
                    source_url=source_url,
                    use_browser=use_browser,
                    html_content=html_content,
                )
            return None

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

    @staticmethod
    def _iter_jsonld_nodes(data: Any):
        """
        Recursively yields every dict node in a parsed JSON-LD structure,
        flattening lists and @graph containers. Non-dict nodes are skipped
        (never call .get() on strings).
        """
        if isinstance(data, dict):
            yield data
            graph = data.get("@graph")
            if isinstance(graph, (list, dict)):
                yield from GenericAdapter._iter_jsonld_nodes(graph)
        elif isinstance(data, list):
            for elem in data:
                yield from GenericAdapter._iter_jsonld_nodes(elem)

    @staticmethod
    def _jsonld_type_is(node: Dict[str, Any], type_name: str) -> bool:
        """True when a JSON-LD node's @type matches type_name (str or list forms)."""
        node_type = node.get("@type")
        if isinstance(node_type, str):
            return node_type == type_name
        if isinstance(node_type, list):
            return type_name in node_type
        return False

    def _try_jsonld_single(self, soup: BeautifulSoup, base_url: str) -> Optional[Dict[str, Any]]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue

            for item in self._iter_jsonld_nodes(data):
                if self._jsonld_type_is(item, "Product"):
                    return self._parse_jsonld_product(item, base_url)
        return None

    def _parse_jsonld_product(self, item: Dict[str, Any], base_url: str) -> Dict[str, Any]:
        name = item.get("name", "")
        desc = clean_html_description(item.get("description", "") or "")
        sku = str(item.get("sku") or item.get("productID") or "")
        brand_val = item.get("brand")
        brand = brand_val.get("name", "") if isinstance(brand_val, dict) else str(brand_val or "")

        offers = item.get("offers", {})
        if isinstance(offers, list):
            offers = next((o for o in offers if isinstance(o, dict)), {})
        if not isinstance(offers, dict):
            offers = {}

        price = offers.get("price") or offers.get("lowPrice", "")
        curr = offers.get("priceCurrency", "")
        avail_str = str(offers.get("availability", ""))
        avail = "In Stock" if "InStock" in avail_str else ("Out of Stock" if "OutOfStock" in avail_str else "")

        # Images
        raw_img = item.get("image", [])
        if isinstance(raw_img, (str, dict)):
            raw_img = [raw_img]
        if not isinstance(raw_img, list):
            raw_img = []
        raw_img = [img for img in raw_img if isinstance(img, (str, dict))]
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
            # Provenance
            "price_source": "jsonld.offers.price",
            "title_source": "jsonld.name",
            "image_source": "jsonld.image",
            "sku_source": "jsonld.sku" if sku else "not_found",
            "description_source": "jsonld.description",
            "availability_source": "jsonld.offers.availability",
            "category_source": "jsonld.category" if item.get("category") else "not_found",
            "variants_source": "not_found",
        }
        return normalize_universal_product(payload, source_url=base_url)

    def _extract_jsonld_listing(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue

            for node in self._iter_jsonld_nodes(data):
                if self._jsonld_type_is(node, "Product"):
                    products.append(self._parse_jsonld_product(node, base_url))
                elif self._jsonld_type_is(node, "ItemList"):
                    elements = node.get("itemListElement", [])
                    if not isinstance(elements, list):
                        continue
                    for elem in elements:
                        # elem may be a Product dict, a {"item": {...}} wrapper,
                        # or a bare URL string.
                        if isinstance(elem, str):
                            url = _normalize_product_url(elem, base_url)
                            if url:
                                products.append({"product_url": url})
                        elif isinstance(elem, dict):
                            inner = elem.get("item", elem)
                            if isinstance(inner, str):
                                url = _normalize_product_url(inner, base_url)
                                if url:
                                    products.append({"product_url": url})
                            elif isinstance(inner, dict) and self._jsonld_type_is(inner, "Product"):
                                products.append(self._parse_jsonld_product(inner, base_url))
                            elif isinstance(inner, dict):
                                url = _normalize_product_url(str(inner.get("url") or inner.get("@id") or ""), base_url)
                                if url and _is_product_path(urlparse(url).path):
                                    products.append({"product_url": url})
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
                price = node.get("price")
                if price is None and isinstance(node.get("priceRange"), dict):
                    price_range = node.get("priceRange") or {}
                    min_price = price_range.get("minVariantPrice")
                    if isinstance(min_price, dict):
                        price = min_price.get("amount")
                url = node.get("url") or node.get("slug") or node.get("handle")
                full_url = urljoin(base_url, str(url)) if url else ""
                if name:
                    raw_image = node.get("image")
                    if isinstance(raw_image, dict):
                        image_url = raw_image.get("url", "")
                    elif isinstance(raw_image, str):
                        image_url = raw_image
                    else:
                        image_url = ""
                    found.append({
                        "product_name": name,
                        "title": name,
                        "price": price,
                        "product_url": full_url,
                        "image_url": image_url,
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

    def _score_product_candidate(self, card: BeautifulSoup, base_url: str) -> Optional[Dict[str, Any]]:
        """
        Scores a product candidate based on multiple signals.
        Returns product dict if score is above threshold, else None.
        """
        score = 0
        signals = {}
        
        # Signal 1: Product-like URL pattern (strong signal)
        link_el = card.find("a", href=True)
        link = _normalize_product_url(link_el["href"], base_url) if link_el else ""
        if link:
            path = urlparse(link).path.lower()
            if _is_product_path(path):
                score += 30
                signals["url_pattern"] = True
        
        # Signal 2: Has product title/name
        name_el = card.find(["h1", "h2", "h3", "h4", "h5", "h6", "a", "span", "p", "div"], 
                            class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "prod", "item"]))
        name = name_el.get_text(strip=True) if name_el else ""
        if not name and link_el:
            name = link_el.get("title") or link_el.get_text(strip=True)
        if name and len(name) >= 3:
            score += 25
            signals["has_name"] = True
        
        # Signal 3: Has price
        price_el = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["price", "cost", "amount", "sale"]))
        price = price_el.get_text(strip=True) if price_el else ""
        if not price:
            price_match = re.search(r'[\$\€\£\₹\¥]\s*[\d,]+\.?\d*', card.get_text())
            price = price_match.group(0) if price_match else ""
        if price:
            score += 25
            signals["has_price"] = True
        
        # Signal 4: Has image
        img_el = card.find("img")
        img = img_el.get("src") or img_el.get("data-src") or img_el.get("data-original") or img_el.get("data-lazy-src") or "" if img_el else ""
        if img:
            score += 15
            signals["has_image"] = True
        
        # Signal 5: Add to cart / buy button
        cart_el = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["cart", "buy", "add-to-cart", "atc", "purchase"]))
        if cart_el or (link_el and any(k in link_el.get_text(strip=True).lower() for k in ["add to cart", "buy now", "add to bag"])):
            score += 20
            signals["has_cart_button"] = True
        
        # Signal 6: SKU / product ID
        sku_el = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["sku", "product-id", "item-id", "data-id"]))
        if sku_el or (link_el and link_el.get("data-product-id")):
            score += 15
            signals["has_sku"] = True
        
        # Signal 7: Semantic HTML / microdata
        if card.find(itemprop="product") or card.find(itemtype=re.compile(r".*Product")):
            score += 20
            signals["has_microdata"] = True
        
        # Signal 8: Schema.org Product in card
        if card.find("script", type="application/ld+json"):
            try:
                import json
                data = json.loads(card.find("script", type="application/ld+json").string or "{}")
                if data.get("@type") == "Product":
                    score += 30
                    signals["has_jsonld"] = True
            except Exception:
                pass
        
        # Minimum threshold
        if score < 40:
            return None
        
        return {
            "product_name": name,
            "title": name,
            "price": price,
            "product_url": link,
            "image_url": img,
            "_discovery_score": score,
            "_discovery_signals": signals,
        }

    def _extract_html_product_cards(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        products = []
        seen_urls = set()
        seen_names = set()
        
        # Extended card selectors with more patterns
        card_selectors = [
            # Modern data attributes
            "[data-testid*='product']", "[data-testid*='item']",
            "[data-product-id]", "[data-item-id]", "[data-sku]",
            # Common class patterns (exact tokens)
            ".product-card", ".productCard", ".product_card", ".ProductCard",
            ".product-item", ".productItem", ".goods-item", ".search-item",
            ".product-tile", ".productTile", ".item-card", ".itemCard",
            ".product-grid-item", ".product-list-item",
            # Substring class patterns (Shopify Dawn "product-card-wrapper", etc.)
            "[class*='product-card']", "[class*='productCard']",
            "[class*='product_card']", "[class*='ProductCard']",
            "[class*='product-item']", "[class*='productItem']",
            "[class*='goods-item']", "[class*='goodsItem']",
            "[class*='search-item']", "[class*='searchItem']",
            # Semantic HTML
            "article.product", "li.product", "div.product",
            # E-commerce specific
            ".product", "[itemtype*='Product']", "[itemprop='itemListElement']",
            # Framework patterns
            ".MuiCard-root", ".ant-card", ".v-card", ".el-card",
        ]
        
        cards = []
        for sel in card_selectors:
            found = soup.select(sel)
            if found and len(found) >= 2:
                cards = found
                logger.debug(f"Found {len(cards)} candidate cards with selector: {sel}")
                break
        
        # Fallback: find all elements that look like product cards (repeated structures)
        if not cards:
            # Look for repeated divs/li with similar structure containing price-like text
            all_containers = soup.find_all(["div", "li", "article"])
            # Group by class pattern
            class_groups = {}
            for c in all_containers:
                classes = " ".join(c.get("class", []))
                if classes:
                    class_groups.setdefault(classes, []).append(c)
            
            for cls, elements in class_groups.items():
                if len(elements) >= 3:  # At least 3 similar elements
                    cards = elements
                    logger.debug(f"Found {len(cards)} candidate cards by class grouping: {cls}")
                    break
        
        base_path = urlparse(base_url).path.rstrip("/").lower()
        for card in cards:
            candidate = self._score_product_candidate(card, base_url)
            if candidate:
                product_url = candidate.get("product_url", "")
                product_name = candidate.get("product_name", "")

                # Require a real, distinct product URL — never emit the
                # listing/collection URL itself as a product.
                if not product_url or product_url.rstrip("/").lower().endswith(base_path or "/"):
                    continue
                if not _is_product_path(urlparse(product_url).path) and not product_name:
                    continue

                # Deduplicate by URL or name
                dedup_key = product_url or product_name
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)
                
                if product_name and product_name in seen_names:
                    continue
                seen_names.add(product_name)
                
                # Remove internal scoring fields
                candidate.pop("_discovery_score", None)
                candidate.pop("_discovery_signals", None)
                
                products.append(candidate)
        
        # Sort by score (highest first) - we lost the score, so just return as-is
        logger.info(f"Extracted {len(products)} product cards via scoring-based discovery")
        return products

    def _extract_single_html(self, soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
        # Title
        title_el = soup.find("h1") or soup.find("meta", property="og:title")
        title = title_el.get("content", "") if title_el and title_el.name == "meta" else (title_el.get_text(strip=True) if title_el else "")
        title_source = "og:title" if title_el and title_el.name == "meta" else ("h1" if title_el else "not_found")

        # Description
        desc_el = soup.find("meta", property="og:description") or soup.select_one(".product-description, #description, .description")
        desc = desc_el.get("content", "") if desc_el and desc_el.name == "meta" else (clean_html_description(str(desc_el)) if desc_el else "")
        desc_source = "og:description" if desc_el and desc_el.name == "meta" else ("html" if desc_el else "not_found")

        # Price
        price_el = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount") or soup.select_one(".price, .product-price, [itemprop='price']")
        price = price_el.get("content", "") if price_el and price_el.name == "meta" else (price_el.get_text(strip=True) if price_el else "")
        price_source = "product:price:amount" if price_el and price_el.name == "meta" and price_el.get("property") == "product:price:amount" else ("og:price:amount" if price_el and price_el.name == "meta" and price_el.get("property") == "og:price:amount" else ("html.price" if price_el else "not_found"))

        # Brand
        brand_el = soup.find("meta", property="product:brand") or soup.select_one(".brand, .vendor")
        brand = brand_el.get("content", "") if brand_el and brand_el.name == "meta" else (brand_el.get_text(strip=True) if brand_el else "")
        brand_source = "product:brand" if brand_el and brand_el.name == "meta" else ("html.brand" if brand_el else "not_found")

        # Images
        img_els = soup.select(".product-gallery img, .product-images img, [data-main-image], .product-photo img")
        imgs = [i.get("src") or i.get("data-src") for i in img_els if i.get("src") or i.get("data-src")]
        if not imgs:
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                imgs = [og_img["content"]]

        main_img, add_imgs, all_imgs = process_images(imgs, base_url=base_url)
        image_source = "og:image" if not img_els and og_img else "html.product-gallery" if img_els else "not_found"

        # Specifications table
        specs = {}
        for row in soup.select("table.specifications tr, table.product-attributes tr, .spec-table tr"):
            cols = row.find_all(["th", "td"])
            if len(cols) >= 2:
                k = cols[0].get_text(strip=True)
                v = cols[1].get_text(strip=True)
                if k and v:
                    specs[k] = v

        # Check availability from stock indicators
        stock_el = soup.select_one(".stock, .availability, [itemprop='availability'], .in-stock, .out-of-stock")
        in_stock = True
        if stock_el:
            stock_text = stock_el.get_text(strip=True).lower()
            stock_class = " ".join(stock_el.get("class", [])).lower()
            if "out of stock" in stock_text or "outofstock" in stock_text or "out-of-stock" in stock_class:
                in_stock = False

        availability = "In Stock" if in_stock else "Out of Stock" if stock_el else ""
        stock_status = availability
        avail_source = "html.stock" if stock_el else "not_found"

        payload = {
            "source_url": base_url,
            "product_name": title,
            "title": title,
            "description": desc,
            "brand": brand,
            "vendor": brand,
            "product_url": base_url,
            "price": price,
            "availability": availability,
            "stock_status": stock_status,
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": add_imgs,
            "all_images": all_imgs,
            "specifications": specs,
            # Provenance
            "price_source": price_source,
            "title_source": title_source,
            "image_source": image_source,
            "sku_source": "not_found",
            "description_source": desc_source,
            "availability_source": avail_source,
            "category_source": "not_found",
            "variants_source": "not_found",
        }
        return normalize_universal_product(payload, source_url=base_url)

    def _discover_via_playwright(self, url: str, max_products: Optional[int] = None) -> List[Dict[str, Any]]:
        browser_config = BrowserConfig(timeout=45000)
        
        def extract(page):
            page.evaluate("window.scrollBy(0, 1000)")
            page.wait_for_timeout(2000)
            content = page.content()
            soup = BeautifulSoup(content, "html.parser")
            return self._extract_html_product_cards(soup, url)
        
        result = extract_with_playwright(
            url=url,
            extract_fn=extract,
            config=browser_config,
            wait_until="domcontentloaded"
        )
        return result or []

    def _extract_via_playwright(self, url: str) -> Optional[Dict[str, Any]]:
        browser_config = BrowserConfig(timeout=45000)
        
        def extract(page):
            page.wait_for_timeout(2000)
            content = page.content()
            soup = BeautifulSoup(content, "html.parser")
            return self._extract_single_html(soup, url)
        
        return extract_with_playwright(
            url=url,
            extract_fn=extract,
            config=browser_config,
            wait_until="domcontentloaded"
        )
