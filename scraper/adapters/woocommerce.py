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
from scraper.utils import fetch_json, fetch_html, logger

class WooCommerceAdapter(ProductAdapter):
    name: str = "WooCommerce"

    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        if html:
            if "woocommerce" in html.lower() or "wc-block" in html or "wp-content/plugins/woocommerce" in html:
                return True
        if "/wp-json/wc/" in url or "/product-category/" in url:
            return True
        return False

    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()

        if "/product/" in path:
            page_type = "Product"
        elif "/product-category/" in path or "/shop" in path:
            page_type = "Category / Listing"
        else:
            page_type = "Store Page"

        return {
            "domain": parsed.netloc.replace("www.", ""),
            "platform": "WooCommerce",
            "page_type": page_type,
            "requires_js": False,
            "has_jsonld": True,
            "public_api_available": True,
            "extraction_method": "WooCommerce REST Store API & Semantic HTML"
        }

    def discover_products(
        self,
        url: str,
        max_products: Optional[int] = None,
        use_browser: bool = False
    ) -> List[Any]:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"

        # If single product URL
        if "/product/" in parsed.path:
            return [url]

        # Try WooCommerce Store API endpoint first
        store_api_url = f"{root}/wp-json/wc/store/v1/products"
        data = fetch_json(store_api_url, params={"per_page": 100})
        if data and isinstance(data, list) and len(data) > 0:
            logger.info(f"Discovered {len(data)} products from WooCommerce Store API")
            if max_products:
                return data[:max_products]
            return data

        # Fallback to HTML crawling of product links
        html = fetch_html(url)
        if not html:
            return [url]

        soup = BeautifulSoup(html, "html.parser")
        product_links = set()
        
        # Look for standard WooCommerce product loop links
        for a in soup.select("ul.products li.product a.woocommerce-LoopProduct-link, .product a[href*='/product/']"):
            href = a.get("href")
            if href:
                product_links.add(urljoin(url, href))

        if product_links:
            logger.info(f"Discovered {len(product_links)} products from WooCommerce HTML")
            res = list(product_links)
            return res[:max_products] if max_products else res

        return [url]

    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False
    ) -> Optional[Dict[str, Any]]:
        # If passed raw dict from Store API
        if isinstance(url_or_raw, dict) and "id" in url_or_raw:
            return self._parse_wc_store_api(url_or_raw, source_url)

        # If passed URL string
        if isinstance(url_or_raw, str):
            product_url = url_or_raw
            html = fetch_html(product_url)
            if not html:
                return None
            return self._parse_wc_html(html, product_url)

        return None

    def _parse_wc_store_api(self, raw: Dict[str, Any], source_url: str) -> Dict[str, Any]:
        product_id = str(raw.get("id", ""))
        name = raw.get("name", "")
        sku = raw.get("sku", "")
        desc = clean_html_description(raw.get("description", "") or raw.get("short_description", ""))
        permalink = raw.get("permalink", source_url)

        # Prices
        prices = raw.get("prices", {})
        price_val = prices.get("price")
        regular_val = prices.get("regular_price")
        sale_val = prices.get("sale_price")
        currency = prices.get("currency_code", "")

        norm_price = normalize_price(price_val)
        if norm_price and isinstance(price_val, str) and len(str(price_val)) > 3 and "." not in str(price_val):
            # Minor units (e.g. cents)
            norm_price = norm_price / 100

        norm_orig = normalize_price(regular_val)
        if norm_orig and isinstance(regular_val, str) and len(str(regular_val)) > 3 and "." not in str(regular_val):
            norm_orig = norm_orig / 100

        # Images
        raw_imgs = raw.get("images", [])
        main_img, add_imgs, all_imgs = process_images(raw_imgs, base_url=permalink)

        # Categories
        categories = [c.get("name", "") for c in raw.get("categories", []) if c.get("name")]
        cat_str = " > ".join(categories) if categories else "General"

        # Attributes (Sizes, Colors, Specs)
        attributes = raw.get("attributes", [])
        colors = []
        sizes = []
        specs = {}
        for attr in attributes:
            attr_name = attr.get("name", "").lower()
            terms = [t.get("name", "") for t in attr.get("terms", [])]
            specs[attr.get("name", "")] = ", ".join(terms)
            if "color" in attr_name or "colour" in attr_name:
                colors.extend(terms)
            elif "size" in attr_name:
                sizes.extend(terms)

        # Variations
        variations = raw.get("variations", [])
        var_dicts = []
        for v in variations:
            v_id = str(v.get("id", ""))
            v_sku = str(v.get("sku", ""))
            v_data = {
                "variant_id": v_id,
                "variant_sku": v_sku,
                "variant_name": str(v.get("attributes", "")),
                "variant_price": normalize_price(v.get("price")),
                "variant_availability": "In Stock" if v.get("is_in_stock", True) else "Out of Stock",
            }
            var_dicts.append(v_data)

        item = {
            "source_url": source_url or permalink,
            "product_id": product_id,
            "sku": sku or f"WC-{product_id}",
            "product_name": name,
            "title": name,
            "description": desc,
            "brand": "",
            "category": cat_str,
            "product_url": permalink,
            "price": norm_price or "",
            "original_price": norm_orig or norm_price or "",
            "currency": currency,
            "availability": "In Stock" if raw.get("is_in_stock", True) else "Out of Stock",
            "stock_status": "In Stock" if raw.get("is_in_stock", True) else "Out of Stock",
            "colors": "|".join(colors),
            "sizes": "|".join(sizes),
            "variants": var_dicts,
            "variant_count": len(var_dicts),
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": add_imgs,
            "all_images": all_imgs,
            "specifications": specs,
        }
        return normalize_universal_product(item, source_url=source_url or permalink)

    def _parse_wc_html(self, html: str, url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")

        # 1. Try JSON-LD first
        from scraper.adapters.generic import GenericAdapter
        generic = GenericAdapter()
        jsonld_res = generic._try_jsonld_single(soup, url)
        if jsonld_res:
            return jsonld_res

        # 2. Heuristic WooCommerce HTML parsing
        title_el = soup.select_one("h1.product_title, h1.entry-title")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = soup.select_one(".price .woocommerce-Price-amount, .price")
        price_str = price_el.get_text(strip=True) if price_el else ""

        sku_el = soup.select_one(".sku_wrapper .sku")
        sku = sku_el.get_text(strip=True) if sku_el else ""

        desc_el = soup.select_one(".woocommerce-product-details__short-description, #tab-description")
        desc = clean_html_description(str(desc_el)) if desc_el else ""

        # Category breadcrumbs
        cat_links = [a.get_text(strip=True) for a in soup.select(".woocommerce-breadcrumb a, .posted_in a")]
        cat_str = " > ".join(cat_links[1:]) if len(cat_links) > 1 else (cat_links[0] if cat_links else "General")

        # Images
        img_els = soup.select(".woocommerce-product-gallery img, .wp-post-image")
        imgs = [img.get("src") or img.get("data-src") for img in img_els if img.get("src") or img.get("data-src")]
        main_img, add_imgs, all_imgs = process_images(imgs, base_url=url)

        # Variations
        variations_form = soup.select_one("form.variations_form")
        var_dicts = []
        if variations_form and variations_form.get("data-product_variations"):
            try:
                raw_vars = json.loads(variations_form["data-product_variations"])
                for v in raw_vars:
                    var_dicts.append({
                        "variant_id": str(v.get("variation_id", "")),
                        "variant_sku": str(v.get("sku", "")),
                        "variant_price": v.get("display_price", ""),
                        "variant_availability": "In Stock" if v.get("is_in_stock") else "Out of Stock",
                        "variant_image": (v.get("image") or {}).get("src", ""),
                    })
            except Exception:
                pass

        item = {
            "source_url": url,
            "product_id": sku or "",
            "sku": sku,
            "product_name": title,
            "title": title,
            "description": desc,
            "category": cat_str,
            "product_url": url,
            "price": price_str,
            "availability": "In Stock",
            "stock_status": "In Stock",
            "variants": var_dicts,
            "variant_count": len(var_dicts),
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": add_imgs,
            "all_images": all_imgs,
        }
        return normalize_universal_product(item, source_url=url)
