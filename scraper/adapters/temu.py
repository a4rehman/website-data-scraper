import json
import math
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from scraper.adapters.base import ProductAdapter
from scraper.cleaners import (
    normalize_universal_product,
    normalize_price,
    calculate_discount_percentage,
    detect_currency,
)
from scraper.utils import fetch_html, logger
from scraper.browser_manager import extract_with_playwright, BrowserConfig, check_browser_available

# Tracking/session params stripped from canonical product URLs (kept on the
# category URL itself, which is only ever used as a fetch target).
_TRACKING_PARAMS_PREFIX = ("_oak_", "_x_", "refer_page", "top_gallery", "spec_gallery",
                           "freesia", "show_search", "leaf_type", "utm_", "fbclid", "gclid")

# Strong block markers — these do NOT appear in healthy Temu page bodies.
# (i18n catalog strings like "Security Verification" are NOT block signals;
# block detection also requires that no goods were discovered.)
_BLOCK_MARKERS = (
    "captcha-form", "cf-chl", "__cf_chl", "verify you are human",
    "are you a robot", "unusual traffic", "please verify you are",
)


class TemuAdapter(ProductAdapter):
    name: str = "Temu"

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        return "temu.com" in (url or "").lower()

    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        parsed = urlparse(url or "")
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()

        if self._is_product_url(url):
            page_type = "Product"
        elif "search_result.html" in path or "search_key=" in query:
            page_type = "Search"
        else:
            # category.html, -o3- category paths, channel pages, collections
            page_type = "Category / Listing"

        return {
            "domain": "temu.com",
            "platform": "Temu",
            "page_type": page_type,
            "requires_js": True,
            "has_jsonld": False,
            "public_api_available": False,
            "extraction_method": "Embedded Hydration JSON (window.rawData) & Playwright JS Engine",
        }

    def _is_product_url(self, url: str) -> bool:
        """True for Temu product-detail URLs (goods.html, goods_id param, or
        SEO /-g-{id}.html paths). Category URLs must NOT match."""
        parsed = urlparse(url or "")
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
        if "goods.html" in path:
            return True
        if "goods_id=" in query:
            return True
        # SEO product paths end with -g-{digits}.html (not /g- which would
        # also match category slugs).
        if re.search(r"-g-\d+\.html$", path):
            return True
        return False

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover_products(
        self,
        url: str,
        max_products: Optional[int] = None,
        use_browser: bool = False,
        stop_event=None,
        progress_callback=None,
    ) -> List[Any]:
        # Direct product-detail URL → single discovery item (the URL itself).
        if self._is_product_url(url):
            return [url]

        logger.info(f"Discovering Temu products from: {url}")

        def emit(message: str, discovered: int = 0) -> None:
            if progress_callback:
                try:
                    progress_callback({"message": message, "discovered": discovered})
                except Exception as cb_err:
                    logger.debug(f"Temu discovery progress callback failed: {cb_err}")

        def cancelled() -> bool:
            return stop_event is not None and stop_event.is_set()

        emit("Fetching Temu category page...")

        html = fetch_html(url)
        if not html:
            raise ValueError(
                "Temu category fetch returned no HTML. The request may have been blocked "
                "(HTTP 403/429/CAPTCHA) or timed out. Check FETCH_EVENT logs for the HTTP "
                "status; no product data was fabricated."
            )

        raw_data = self._extract_window_json(html, "rawData")
        # Definitive server-side flags fail fast (before any parsing effort).
        if isinstance(raw_data, dict):
            store = raw_data.get("store") if isinstance(raw_data.get("store"), dict) else {}
            if store.get("banned") is True:
                raise ValueError("Temu access blocked: Temu reported banned=true for this session (access restriction).")
            if store.get("isRobot") is True:
                raise ValueError("Temu access blocked: Temu flagged the request as a bot (isRobot=true).")

        discovered_items: List[Dict[str, Any]] = []
        seen_keys = set()

        def add_item(item: Optional[Dict[str, Any]]) -> None:
            if not item:
                return
            key = str(item.get("goods_id") or item.get("product_url") or "")
            if key and key not in seen_keys:
                seen_keys.add(key)
                discovered_items.append(item)

        # Level 1: window.rawData hydration JSON (SSR payload)
        if isinstance(raw_data, dict):
            store = raw_data.get("store") if isinstance(raw_data.get("store"), dict) else {}
            for list_key in ("goodsList", "recGoodsList"):
                for entry in store.get(list_key) or []:
                    node = entry.get("data") if isinstance(entry, dict) and isinstance(entry.get("data"), dict) else None
                    if node is None and isinstance(entry, dict):
                        node = entry
                    add_item(self._map_raw_goods_node(node, url) if node else None)
            # Generic recursive sweep for goods nodes anywhere in the payload
            for node in self._find_goods_in_json(raw_data, url):
                add_item(node)

            has_more = bool(store.get("hasMore"))
            page_size = int(store.get("pageSize") or 40)
            local_info = store.get("localInfo") if isinstance(store.get("localInfo"), dict) else {}
            if local_info.get("currency") and discovered_items:
                for it in discovered_items:
                    it.setdefault("currency", local_info["currency"])

            emit(f"Products discovered: {len(discovered_items)} from embedded state",
                 discovered=len(discovered_items))

            # Pagination: follow &page=N while hasMore and more products are needed.
            if has_more and discovered_items and not cancelled():
                wanted = max_products or (page_size * 3)
                max_pages = max(2, min(5, math.ceil(wanted / max(page_size, 1))))
                for page_num in range(2, max_pages + 1):
                    if cancelled():
                        break
                    if max_products and len(discovered_items) >= max_products:
                        break
                    page_url = self._with_page_param(url, page_num)
                    page_html = fetch_html(page_url)
                    if not page_html:
                        logger.info(f"Temu pagination stopped: no HTML for page {page_num}")
                        break
                    page_raw = self._extract_window_json(page_html, "rawData")
                    if not isinstance(page_raw, dict):
                        break
                    page_store = page_raw.get("store") if isinstance(page_raw.get("store"), dict) else {}
                    page_list = page_store.get("goodsList") or []
                    if not page_list:
                        logger.info(f"Temu pagination stopped: empty goodsList on page {page_num}")
                        break
                    before = len(discovered_items)
                    for entry in page_list:
                        node = entry.get("data") if isinstance(entry, dict) and isinstance(entry.get("data"), dict) else None
                        if node is None and isinstance(entry, dict):
                            node = entry
                        add_item(self._map_raw_goods_node(node, url) if node else None)
                    emit(f"Products discovered: {len(discovered_items)} (page {page_num})",
                         discovered=len(discovered_items))
                    if len(discovered_items) == before:
                        break  # no new items — stop
                    if not page_store.get("hasMore"):
                        break
        else:
            logger.warning("Temu window.rawData not found or unparseable; falling back to DOM anchors")

        # Level 2: DOM anchor fallback (cleaned titles, goods id from SEO URL)
        if not discovered_items and html:
            for item in self._extract_anchor_fallback(html, url):
                add_item(item)
            if discovered_items:
                emit(f"Products discovered: {len(discovered_items)} from page links",
                     discovered=len(discovered_items))

        # Level 3: Playwright rendering (explicitly requested or static failed)
        need_browser = (not discovered_items) or (use_browser and (
            not max_products or len(discovered_items) < max_products))
        if need_browser and not cancelled():
            emit(f"Rendering Temu page with browser ({len(discovered_items)} so far)...",
                 discovered=len(discovered_items))
            browser_items = self._discover_via_playwright(
                url, max_products=max_products, stop_event=stop_event
            )
            for item in browser_items:
                add_item(item)
            emit(f"Products discovered: {len(discovered_items)} after browser render",
                 discovered=len(discovered_items))

        if not discovered_items:
            # HTML-level challenge markers only count when no goods were found
            # (healthy pages embed i18n strings like "Security Verification").
            blocked_reason = self._detect_blocked(html, None)
            if blocked_reason:
                raise ValueError(f"Temu access blocked: {blocked_reason}")
            # Explicit failure — never return the category URL as a fake product.
            logger.warning(f"No listing items discovered on Temu page: {url}")
            return []

        if max_products and len(discovered_items) > max_products:
            discovered_items = discovered_items[:max_products]

        emit(f"Products discovered: {len(discovered_items)}", discovered=len(discovered_items))
        logger.info(f"Discovered {len(discovered_items)} Temu products")
        return discovered_items

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------
    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False,
    ) -> Optional[Dict[str, Any]]:
        # Pre-extracted raw product dictionary from discovery (snake_case or camelCase)
        if isinstance(url_or_raw, dict):
            if self._looks_like_product_dict(url_or_raw):
                return self._normalize_temu_item(url_or_raw, source_url)
            return None

        if not isinstance(url_or_raw, str) or not url_or_raw.strip():
            return None

        product_url = url_or_raw.strip()

        # Static HTML: meta tags + embedded hydration JSON
        html = fetch_html(product_url)
        if html:
            raw_data = self._extract_window_json(html, "rawData")
            if isinstance(raw_data, dict):
                gid = self._goods_id_from_url(product_url)
                for node in self._find_goods_in_json(raw_data, product_url):
                    node_gid = str(node.get("goods_id") or node.get("goodsId") or "")
                    if gid and node_gid and node_gid != gid:
                        continue
                    if node.get("product_name") or node.get("title") or node.get("price"):
                        return self._normalize_temu_item(node, product_url)
            item_data = self._extract_single_embedded_goods(html, product_url)
            if item_data and (item_data.get("product_name") or item_data.get("price")):
                return self._normalize_temu_item(item_data, product_url)

        # Browser rendering fallback for direct product URL
        if use_browser:
            logger.info(f"Rendering Temu product via Playwright: {product_url}")
            rendered_data = self._extract_via_playwright(product_url)
            if rendered_data:
                return self._normalize_temu_item(rendered_data, product_url)

        return None

    def _looks_like_product_dict(self, item: Dict[str, Any]) -> bool:
        return bool(
            item.get("product_name") or item.get("title") or item.get("goods_id")
            or item.get("goodsId") or item.get("product_url")
        )

    # ------------------------------------------------------------------
    # window.rawData JSON extraction (brace-matching, string-aware)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_window_json(html: str, var_name: str) -> Optional[Any]:
        """Extract `window.<var_name> = {...};` via brace matching that is aware
        of JSON string literals (braces/escapes inside strings are ignored).
        Handles arbitrarily large single-line payloads (Temu rawData ~360KB),
        which lazy regexes cannot."""
        if not html:
            return None
        m = re.search(r"window\." + re.escape(var_name) + r"\s*=\s*", html)
        if not m:
            return None
        start = m.end()
        while start < len(html) and html[start] in " \t\r\n":
            start += 1
        if start >= len(html) or html[start] not in "{[":
            return None
        opener = html[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_str = False
        quote = ""
        escaped = False
        for i in range(start, len(html)):
            ch = html[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in ('"', "'"):
                in_str = True
                quote = ch
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    blob = html[start:i + 1]
                    try:
                        return json.loads(blob)
                    except json.JSONDecodeError as e:
                        logger.debug(f"window.{var_name} JSON parse failed: {e}")
                        return None
        logger.debug(f"window.{var_name} brace matching did not terminate")
        return None

    # ------------------------------------------------------------------
    # Blocked / challenge detection
    # ------------------------------------------------------------------
    def _detect_blocked(self, html: str, raw_data: Optional[Any]) -> Optional[str]:
        """Return a human-readable block reason, or None if the page looks healthy."""
        if isinstance(raw_data, dict):
            store = raw_data.get("store") if isinstance(raw_data.get("store"), dict) else {}
            if store.get("banned") is True:
                return "Temu reported banned=true for this session (access restriction)."
            if store.get("isRobot") is True:
                return "Temu flagged the request as a bot (isRobot=true)."

        low = (html or "").lower()
        for marker in _BLOCK_MARKERS:
            if marker in low:
                return f"Temu returned a challenge/block page (marker: {marker!r})."
        return None

    # ------------------------------------------------------------------
    # rawData goods mapping (camelCase → discovery item)
    # ------------------------------------------------------------------
    def _map_raw_goods_node(self, node: Optional[Dict[str, Any]], base_url: str) -> Optional[Dict[str, Any]]:
        """Map one rawData goods node (camelCase) to the discovery-item contract."""
        if not isinstance(node, dict):
            return None
        gid = str(node.get("goodsId") or node.get("goods_id") or "").strip()
        title = str(node.get("title") or node.get("goodsName") or node.get("goods_name") or "").strip()
        if not gid and not title:
            return None

        price_info = node.get("priceInfo") if isinstance(node.get("priceInfo"), dict) else {}
        price_str = price_info.get("priceStr") or ""
        market_str = price_info.get("marketPriceStr") or ""
        currency = str(price_info.get("currency") or "")
        # Fallback: rawData prices are integers in cents/paisa.
        if not price_str and isinstance(price_info.get("price"), (int, float)):
            price_str = str(price_info["price"] / 100.0)
        if not market_str and isinstance(price_info.get("marketPrice"), (int, float)):
            market_str = str(price_info["marketPrice"] / 100.0)

        img = str(node.get("thumbUrl") or node.get("thumb_url") or "")
        if not img and isinstance(node.get("image"), dict):
            img = str(node["image"].get("url") or "")

        comment = node.get("comment") if isinstance(node.get("comment"), dict) else {}
        rating = comment.get("goodsScore") or node.get("score") or node.get("rating") or ""
        sales_tip = node.get("salesTip") or node.get("salesNum") or node.get("sales_tip") or ""

        product_url = self._canonical_product_url(node, gid, base_url)
        if not product_url:
            return None

        return {
            "goods_id": gid,
            "product_id": gid,
            "product_name": title,
            "title": title,
            "price": price_str,
            "original_price": market_str,
            "currency": currency,
            "image_url": img,
            "product_url": product_url,
            "rating": str(rating) if rating != "" else "",
            "sold_count": str(sales_tip),
        }

    def _canonical_product_url(self, node: Dict[str, Any], gid: str, base_url: str) -> str:
        """Build a clean canonical product URL: prefer the SEO path (contains
        -g-{id}), strip tracking/session query params; fall back to goods.html?goods_id=."""
        seo = str(node.get("seoLinkUrl") or node.get("seo_link_url") or "").strip()
        if seo:
            absolute = urljoin(base_url, seo)
            parsed = urlparse(absolute)
            clean = urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, "", "", ""))
            if clean.endswith(".html") or "/goods" in parsed.path:
                return clean
        link = str(node.get("linkUrl") or node.get("link_url") or "").strip()
        if link:
            absolute = urljoin(base_url, link)
            parsed = urlparse(absolute)
            query_dict = dict(parse_qsl(parsed.query))
            goods_id = query_dict.get("goods_id") or gid
            if goods_id:
                return f"{parsed.scheme or 'https'}://{parsed.netloc}/goods.html?goods_id={goods_id}"
        if gid:
            netloc = urlparse(base_url).netloc or "www.temu.com"
            return f"https://{netloc}/goods.html?goods_id={gid}"
        return ""

    @staticmethod
    def _goods_id_from_url(url: str) -> str:
        parsed = urlparse(url or "")
        m = re.search(r"-g-(\d+)\.html", (parsed.path or "").lower())
        if m:
            return m.group(1)
        q = dict(parse_qsl(parsed.query))
        return str(q.get("goods_id") or "")

    def _with_page_param(self, url: str, page: int) -> str:
        parsed = urlparse(url)
        pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "page"]
        pairs.append(("page", str(page)))
        return urlunparse(parsed._replace(query=urlencode(pairs)))

    # ------------------------------------------------------------------
    # Recursive goods search (handles camelCase + snake_case + priceInfo)
    # ------------------------------------------------------------------
    def _find_goods_in_json(self, node: Any, base_url: str) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        if isinstance(node, dict):
            has_goods_keys = any(
                k in node for k in ("goods_id", "goodsId", "goods_name", "goodsName")
            )
            if has_goods_keys:
                mapped = self._map_raw_goods_node(node, base_url)
                if mapped:
                    found.append(mapped)
            for val in node.values():
                if isinstance(val, (dict, list)):
                    found.extend(self._find_goods_in_json(val, base_url))
        elif isinstance(node, list):
            for elem in node:
                if isinstance(elem, (dict, list)):
                    found.extend(self._find_goods_in_json(elem, base_url))
        return found

    # ------------------------------------------------------------------
    # DOM anchor fallback
    # ------------------------------------------------------------------
    def _extract_anchor_fallback(self, html: str, base_url: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not any(k in href for k in ("goods.html", "-g-", "goods_id")):
                continue
            m = re.search(r"-g-(\d+)\.html", href)
            gid = m.group(1) if m else ""
            if not gid:
                q = dict(parse_qsl(urlparse(href).query))
                gid = str(q.get("goods_id") or "")
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            clean_url = urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, "", "", ""))
            title = self._clean_anchor_title(a.get("title") or a.get_text(" ", strip=True))
            if not title:
                title_el = a.find(class_=lambda cl: cl and any(
                    k in str(cl).lower() for k in ("title", "name", "desc")))
                if title_el:
                    title = self._clean_anchor_title(title_el.get_text(" ", strip=True))
            img = a.find("img")
            img_src = (img.get("src") or img.get("data-src") or img.get("data-original") or "") if img else ""
            if gid and (title or img_src):
                items.append({
                    "goods_id": gid,
                    "product_id": gid,
                    "product_name": title,
                    "title": title,
                    "image_url": img_src,
                    "product_url": clean_url or urljoin(base_url, f"/goods.html?goods_id={gid}"),
                })
        return items

    @staticmethod
    def _clean_anchor_title(text: str) -> str:
        """Strip Temu DOM chrome from anchor text: 'Top pick' badges and
        'Open in new tab.' accessibility suffixes."""
        t = (text or "").strip()
        t = re.sub(r"^\s*Top\s*pick\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*Open(s)?\s+in\s+new\s+tab\.?\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s+", " ", t).strip(" ,|-")
        return t

    # ------------------------------------------------------------------
    # Single-product embedded extraction (meta tags + rawData)
    # ------------------------------------------------------------------
    def _extract_single_embedded_goods(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        title_meta = soup.find("meta", property="og:title") or soup.find("meta", name="title")
        desc_meta = soup.find("meta", property="og:description") or soup.find("meta", name="description")
        img_meta = soup.find("meta", property="og:image")
        price_meta = (
            soup.find("meta", property="product:price:amount")
            or soup.find("meta", property="og:price:amount")
        )
        currency_meta = soup.find("meta", property="product:price:currency")

        title = title_meta.get("content", "") if title_meta else ""
        desc = desc_meta.get("content", "") if desc_meta else ""
        img = img_meta.get("content", "") if img_meta else ""
        price = price_meta.get("content", "") if price_meta else ""
        currency = currency_meta.get("content", "") if currency_meta else ""

        if not price:
            for s in soup.find_all("script"):
                content = s.string or s.text or ""
                if ("goods" in content or "product" in content) and "price" in content:
                    price_match = re.search(
                        r'["\'](?:displayPrice|price|salePrice|priceStr)["\']\s*:\s*["\']?([0-9\.,]+)',
                        content,
                    )
                    if price_match:
                        price = price_match.group(1)
                        break

        gid = self._goods_id_from_url(url)
        if title:
            return {
                "goods_id": gid,
                "product_id": gid,
                "product_name": title,
                "title": title,
                "description": desc,
                "price": price,
                "currency": currency,
                "image_url": img,
                "product_url": url,
            }
        return None

    # ------------------------------------------------------------------
    # Playwright rendering
    # ------------------------------------------------------------------
    def _discover_via_playwright(
        self,
        url: str,
        max_products: Optional[int] = None,
        stop_event=None,
    ) -> List[Dict[str, Any]]:
        diag = check_browser_available()
        if not diag.get("available"):
            logger.warning(
                f"Playwright unavailable for Temu discovery: {diag.get('message', 'no diagnostic')}"
            )
            return []

        browser_config = BrowserConfig(timeout=45000)

        def extract(page):
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            # Wait for hydration payload or product anchors to appear.
            for selector in ("a[href*='-g-']", "[data-goods-id]", "[class*='goods']"):
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    break
                except Exception:
                    continue
            # Scroll incrementally to trigger lazy-loaded goods.
            for _ in range(4):
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight * 0.9))")
                    page.wait_for_timeout(1200)
                except Exception:
                    break

            # Preferred: read window.rawData straight from the JS context.
            try:
                raw = page.evaluate("() => window.rawData || null")
            except Exception:
                raw = None
            products: List[Dict[str, Any]] = []
            if isinstance(raw, dict):
                for node in self._find_goods_in_json(raw, url):
                    products.append(node)
                    if max_products and len(products) >= max_products:
                        return products
                if products:
                    return products

            # Fallback: parse the rendered DOM.
            content = page.content()
            for item in self._extract_anchor_fallback(content, url):
                products.append(item)
                if max_products and len(products) >= max_products:
                    break
            return products

        result = extract_with_playwright(
            url=url,
            extract_fn=extract,
            config=browser_config,
            wait_until="domcontentloaded",
        )
        return result or []

    def _extract_via_playwright(self, url: str) -> Optional[Dict[str, Any]]:
        diag = check_browser_available()
        if not diag.get("available"):
            logger.warning(
                f"Playwright unavailable for Temu product render: {diag.get('message', 'no diagnostic')}"
            )
            return None

        browser_config = BrowserConfig(timeout=45000)

        def extract(page):
            page.wait_for_timeout(2500)
            try:
                raw = page.evaluate("() => window.rawData || null")
            except Exception:
                raw = None
            if isinstance(raw, dict):
                gid = self._goods_id_from_url(url)
                for node in self._find_goods_in_json(raw, url):
                    node_gid = str(node.get("goods_id") or node.get("goodsId") or "")
                    if gid and node_gid and node_gid != gid:
                        continue
                    if node.get("product_name") or node.get("price"):
                        return node

            content = page.content()
            soup = BeautifulSoup(content, "html.parser")
            title_el = soup.find(["h1", "h2"])
            title = title_el.get_text(strip=True) if title_el else ""
            price_match = re.search(r"[\$€£₹¥]\s*[\d,]+\.?\d*", content)
            price = price_match.group(0) if price_match else ""
            img = soup.find("img", src=True)
            img_src = img["src"] if img else ""
            gid = self._goods_id_from_url(url)
            return {
                "goods_id": gid,
                "product_id": gid,
                "product_name": title,
                "title": title,
                "price": price,
                "image_url": img_src,
                "product_url": url,
            }

        return extract_with_playwright(
            url=url,
            extract_fn=extract,
            config=browser_config,
            wait_until="domcontentloaded",
        )

    # ------------------------------------------------------------------
    # Normalization into the Universal Schema
    # ------------------------------------------------------------------
    def _normalize_temu_item(self, item: Dict[str, Any], source_url: str) -> Dict[str, Any]:
        """Normalize a Temu raw dict (snake_case or camelCase, including nested
        priceInfo) into the Universal Schema. Never fabricates SKU/brand/stock."""
        name = (
            item.get("product_name") or item.get("title") or item.get("goodsName") or ""
        )
        gid = str(
            item.get("goods_id") or item.get("goodsId") or item.get("product_id") or ""
        ).strip()

        price_info = item.get("priceInfo") if isinstance(item.get("priceInfo"), dict) else {}
        price_val = item.get("price") or price_info.get("priceStr") or ""
        if not price_val and isinstance(price_info.get("price"), (int, float)):
            price_val = price_info["price"] / 100.0
        orig_val = (
            item.get("original_price") or item.get("marketPriceStr")
            or price_info.get("marketPriceStr") or ""
        )
        if not orig_val and isinstance(price_info.get("marketPrice"), (int, float)):
            orig_val = price_info["marketPrice"] / 100.0

        product_url = (
            item.get("product_url") or item.get("seoLinkUrl") or source_url
        )
        if product_url and product_url.startswith("/"):
            product_url = urljoin(source_url or "https://www.temu.com", product_url)
        # Strip tracking params from the canonical URL but keep the goods path/id.
        if product_url:
            parsed = urlparse(product_url)
            if "temu.com" in (parsed.netloc or ""):
                product_url = urlunparse(
                    (parsed.scheme or "https", parsed.netloc, parsed.path, "", "", "")
                )

        main_img = (
            item.get("image_url") or item.get("thumbUrl") or item.get("thumb_url") or ""
        )
        if not main_img and isinstance(item.get("image"), dict):
            main_img = str(item["image"].get("url") or "")

        norm_p = normalize_price(price_val)
        norm_o = normalize_price(orig_val)
        disc = (
            calculate_discount_percentage(norm_o, norm_p)
            if norm_o and norm_p and norm_o > norm_p else ""
        )

        # Availability: only from extracted data — never assumed.
        availability = item.get("availability") or item.get("stock_status") or ""

        # Currency: explicit field, nested priceInfo, symbol detection on the
        # raw (pre-normalization) price strings.
        currency = (
            item.get("currency") or price_info.get("currency")
            or detect_currency(price_val) or detect_currency(orig_val) or ""
        )

        comment = item.get("comment") if isinstance(item.get("comment"), dict) else {}
        rating = item.get("rating") or comment.get("goodsScore") or ""
        sales_tip = (
            item.get("sales_tip") or item.get("sold_count")
            or item.get("salesTip") or item.get("salesNum") or ""
        )

        payload = {
            "source_domain": "temu.com",
            "source_url": source_url or product_url,
            "product_id": gid,
            # goods_id is a real Temu product identifier, but not a merchant SKU —
            # leave sku empty unless the source exposes one.
            "sku": str(item.get("sku") or ""),
            "product_name": name,
            "title": name,
            "description": item.get("description", ""),
            "brand": item.get("brand") or "",
            "vendor": item.get("vendor") or "",
            "category": item.get("category") or "",
            "product_url": product_url,
            "price": norm_p if norm_p is not None else price_val,
            "sale_price": norm_p if norm_p is not None else price_val,
            "original_price": norm_o if norm_o is not None else "",
            "discount_percentage": disc,
            "currency": currency,
            "availability": availability,
            "stock_status": availability,
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": item.get("additional_images", ""),
            "all_images": main_img,
            "rating": str(rating),
            "sold_count": str(sales_tip),
            # Provenance
            "price_source": "rawData.priceInfo" if price_val else "not_found",
            "title_source": "rawData.title" if name else "not_found",
            "image_source": "rawData.thumbUrl" if main_img else "not_found",
            "sku_source": "not_found",
            "description_source": "og:description" if item.get("description") else "not_found",
            "availability_source": "extracted" if availability else "not_found",
            "category_source": "extracted" if item.get("category") else "not_found",
            "variants_source": "not_found",
        }
        return normalize_universal_product(payload, source_url=source_url or product_url)
