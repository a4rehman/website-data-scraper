import json
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import config
from scraper.adapters.base import ProductAdapter
from scraper.cleaners import (
    normalize_universal_product,
    normalize_price,
    calculate_discount_percentage,
    process_images,
    extract_material_and_pieces,
    clean_html_description
)
from scraper.utils import fetch_json, fetch_html, logger

class ShopifyAdapter(ProductAdapter):
    name: str = "Shopify"

    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        if "myshopify.com" in url.lower():
            return True
        if html:
            if "cdn.shopify.com" in html or "window.Shopify" in html or "Shopify.theme" in html or "/products.json" in html:
                return True
        return False

    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        parsed = urlparse(url)
        path = parsed.path.lower()
        
        if "/products/" in path:
            page_type = "Product"
        elif "/collections/" in path:
            page_type = "Collection"
        elif "/search" in path:
            page_type = "Search"
        else:
            page_type = "Catalog / Store Root"

        return {
            "domain": parsed.netloc.replace("www.", ""),
            "platform": "Shopify",
            "page_type": page_type,
            "requires_js": False,
            "has_jsonld": True,
            "public_api_available": True,
            "extraction_method": "Shopify JSON API (/products.json) & JSON-LD"
        }

    def discover_products(
        self,
        url: str,
        max_products: Optional[int] = None,
        use_browser: bool = False
    ) -> List[Any]:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path

        # If direct product URL
        if "/products/" in path and not path.endswith("/products.json") and not path.endswith("/products"):
            return [url]

        # If collection URL
        collection_match = re.search(r'/collections/([^/?#]+)', path)
        if collection_match and collection_match.group(1) != "all":
            collection_handle = collection_match.group(1)
            api_endpoint = f"{root}/collections/{collection_handle}/products.json"
            logger.info(f"Discovering Shopify products from collection: {collection_handle}")
        else:
            api_endpoint = f"{root}/products.json"
            logger.info(f"Discovering Shopify products from root: {root}")

        discovered: List[Dict[str, Any]] = []
        page = 1
        limit = 250

        while True:
            data = fetch_json(api_endpoint, params={"page": page, "limit": limit})
            if not data or not isinstance(data, dict) or "products" not in data or not data["products"]:
                break
            
            batch = data["products"]
            discovered.extend(batch)
            logger.info(f"  Shopify Page {page}: Found {len(batch)} products (Total: {len(discovered)})")

            if max_products and len(discovered) >= max_products:
                discovered = discovered[:max_products]
                break

            if len(batch) < limit:
                break
            page += 1

        # Fallback to direct single URL if nothing found
        if not discovered:
            return [url]

        return discovered

    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False
    ) -> Optional[Dict[str, Any]]:
        # If passed raw dictionary from Shopify JSON API
        if isinstance(url_or_raw, dict) and "id" in url_or_raw:
            return self._parse_shopify_raw(url_or_raw, source_url)

        # If passed URL string
        if isinstance(url_or_raw, str):
            product_url = url_or_raw
            # Attempt to fetch /products/{handle}.json
            parsed = urlparse(product_url)
            root = f"{parsed.scheme}://{parsed.netloc}"
            handle_match = re.search(r'/products/([^/?#]+)', parsed.path)
            
            if handle_match:
                handle = handle_match.group(1)
                json_url = f"{root}/products/{handle}.json"
                data = fetch_json(json_url)
                if data and isinstance(data, dict) and "product" in data:
                    return self._parse_shopify_raw(data["product"], source_url or product_url, root=root)

            # Fallback to HTML extraction
            html = fetch_html(product_url)
            if html:
                from scraper.adapters.generic import GenericAdapter
                generic = GenericAdapter()
                return generic.extract_product(product_url, source_url=source_url, html_content=html)

        return None

    def _parse_shopify_raw(
        self,
        raw: Dict[str, Any],
        source_url: str = "",
        root: Optional[str] = None
    ) -> Dict[str, Any]:
        product_id = str(raw.get("id", ""))
        handle = raw.get("handle", "")
        title = raw.get("title", "").strip()
        vendor = raw.get("vendor", "").strip()
        product_type = raw.get("product_type", "").strip()
        body_html = raw.get("body_html", "") or ""
        clean_desc = clean_html_description(body_html)

        if root:
            product_url = f"{root}/products/{handle}" if handle else source_url
        elif source_url:
            parsed = urlparse(source_url)
            product_url = f"{parsed.scheme}://{parsed.netloc}/products/{handle}" if handle else source_url
        else:
            product_url = f"{config.BASE_URL}/products/{handle}" if handle else ""

        # Tags
        raw_tags = raw.get("tags", [])
        if isinstance(raw_tags, str):
            tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
        else:
            tags_list = [str(t).strip() for t in raw_tags if str(t).strip()]
        tags_str = "|".join(tags_list)

        # Images
        raw_images = raw.get("images", [])
        if not raw_images and raw.get("image"):
            raw_images = [raw["image"]]
        main_img, add_imgs, all_imgs = process_images(raw_images, base_url=root or source_url)

        # Options map (Size, Color, Material)
        options = raw.get("options", [])
        size_opt_name = None
        color_opt_name = None
        for opt in options:
            opt_name = opt.get("name", "").lower()
            if "size" in opt_name:
                size_opt_name = f"option{opt.get('position', 1)}"
            elif "color" in opt_name or "colour" in opt_name:
                color_opt_name = f"option{opt.get('position', 2)}"

        # Variants processing
        raw_variants = raw.get("variants", [])
        sizes_set = set()
        colors_set = set()
        variant_dicts = []
        variant_summaries = []
        skus = []
        prices = []
        compare_prices = []
        any_in_stock = False

        for v in raw_variants:
            v_id = str(v.get("id", ""))
            v_sku = str(v.get("sku", "")).strip()
            if v_sku:
                skus.append(v_sku)
            v_title = str(v.get("title", "")).strip()
            v_price = normalize_price(v.get("price"))
            v_compare = normalize_price(v.get("compare_at_price"))
            v_avail = bool(v.get("available", True))

            if v_price is not None:
                prices.append(v_price)
            if v_compare is not None:
                compare_prices.append(v_compare)
            if v_avail:
                any_in_stock = True

            v_size = ""
            if size_opt_name and v.get(size_opt_name):
                v_size = str(v.get(size_opt_name)).strip()
                sizes_set.add(v_size)
            elif v_title and v_title not in ("Default Title", "Default"):
                if v_title.upper() in ("S", "M", "L", "XL", "XXL", "SMALL", "MEDIUM", "LARGE", "2XL", "3XL"):
                    v_size = v_title.upper()
                    sizes_set.add(v_size)

            v_color = ""
            if color_opt_name and v.get(color_opt_name):
                v_color = str(v.get(color_opt_name)).strip()
                colors_set.add(v_color)

            v_img = ""
            if v.get("featured_image") and isinstance(v["featured_image"], dict):
                v_img = v["featured_image"].get("src", "")
            elif v.get("image_id"):
                for im in raw_images:
                    if isinstance(im, dict) and im.get("id") == v.get("image_id"):
                        v_img = im.get("src", "")
                        break

            v_data = {
                "variant_id": v_id,
                "variant_sku": v_sku,
                "variant_name": v_title,
                "variant_price": v_price,
                "variant_original_price": v_compare or "",
                "variant_color": v_color,
                "variant_size": v_size,
                "variant_availability": "In Stock" if v_avail else "Out of Stock",
                "variant_image": v_img,
            }
            variant_dicts.append(v_data)
            variant_summaries.append(
                f"Size: {v_size or v_title} | Color: {v_color} | Price: {v_price} | SKU: {v_sku} | Stock: {'In Stock' if v_avail else 'Out of Stock'}"
            )

        primary_sku = skus[0] if skus else f"SKU-{product_id}"
        active_price = prices[0] if prices else ""
        original_price = compare_prices[0] if compare_prices else active_price
        
        if original_price and active_price and original_price > active_price:
            sale_price = active_price
            discount_pct = calculate_discount_percentage(original_price, sale_price)
        else:
            original_price = active_price
            sale_price = ""
            discount_pct = ""

        sizes_str = "|".join(sorted(list(sizes_set)))
        colors_str = "|".join(sorted(list(colors_set)))
        material, pieces = extract_material_and_pieces(clean_desc + " " + title, tags_list)

        product_dict = {
            "source_url": source_url or product_url,
            "product_id": product_id,
            "sku": primary_sku,
            "product_name": title,
            "title": title,
            "description": clean_desc,
            "brand": vendor,
            "vendor": vendor,
            "category": product_type or "General",
            "subcategory": pieces or "",
            "product_type": product_type,
            "tags": tags_str,
            "product_url": product_url,
            "price": active_price,
            "sale_price": sale_price,
            "original_price": original_price,
            "discount_percentage": discount_pct,
            "availability": "In Stock" if any_in_stock else "Out of Stock",
            "stock_status": "In Stock" if any_in_stock else "Out of Stock",
            "colors": colors_str,
            "sizes": sizes_str,
            "material": material,
            "fabric": material,
            "variants": variant_dicts,
            "variant_count": len(variant_dicts),
            "main_image": main_img,
            "image_url": main_img,
            "additional_images": add_imgs,
            "all_images": all_imgs,
        }
        return normalize_universal_product(product_dict, source_url=source_url or product_url)
