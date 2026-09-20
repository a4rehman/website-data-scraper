from typing import Dict, Any, List, Optional
import config
from scraper.cleaners import (
    clean_html_description,
    normalize_price,
    calculate_discount_percentage,
    normalize_url,
    process_images,
    extract_material_and_pieces
)

def parse_product_data(raw_product: Dict[str, Any], collections_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Parses a raw product dictionary (from Shopify API) into a standardized product record.
    """
    product_id = str(raw_product.get("id", ""))
    handle = raw_product.get("handle", "")
    title = raw_product.get("title", "").strip()
    vendor = raw_product.get("vendor", "").strip() or "Tehzeeb Libas"
    product_type = raw_product.get("product_type", "").strip()
    body_html = raw_product.get("body_html", "")
    description = clean_html_description(body_html)

    # Product URL
    product_url = f"{config.BASE_URL}/products/{handle}" if handle else ""

    # Tags
    raw_tags = raw_product.get("tags", [])
    if isinstance(raw_tags, str):
        tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
    else:
        tags_list = [str(t).strip() for t in raw_tags if str(t).strip()]
    tags_str = "|".join(tags_list)

    # Images
    images = raw_product.get("images", [])
    image_url, additional_image_urls = process_images(images)

    # Variants, Sizes, Colors, Pricing, Stock
    variants_raw = raw_product.get("variants", [])
    sizes_set = set()
    colors_set = set()
    variant_summaries = []
    
    skus = []
    prices = []
    compare_at_prices = []
    any_available = False

    # Extract options mapping (e.g. Size, Color)
    options = raw_product.get("options", [])
    size_opt_name = None
    color_opt_name = None
    for opt in options:
        opt_name = opt.get("name", "").lower()
        if "size" in opt_name:
            size_opt_name = f"option{opt.get('position', 1)}"
        elif "color" in opt_name or "colour" in opt_name:
            color_opt_name = f"option{opt.get('position', 2)}"

    for v in variants_raw:
        v_sku = v.get("sku", "").strip()
        if v_sku:
            skus.append(v_sku)
        
        v_title = v.get("title", "").strip()
        v_price = normalize_price(v.get("price"))
        v_compare = normalize_price(v.get("compare_at_price"))
        v_available = bool(v.get("available", False))

        if v_price is not None:
            prices.append(v_price)
        if v_compare is not None:
            compare_at_prices.append(v_compare)

        if v_available:
            any_available = True

        # Size / Color extraction
        if size_opt_name and v.get(size_opt_name):
            sizes_set.add(str(v.get(size_opt_name)).strip())
        elif v_title and v_title not in ("Default Title", "Default"):
            # Check if title looks like a size
            if v_title.upper() in ("S", "M", "L", "XL", "XXL", "SMALL", "MEDIUM", "LARGE", "X-LARGE", "2XL", "3XL"):
                sizes_set.add(v_title.upper())

        if color_opt_name and v.get(color_opt_name):
            colors_set.add(str(v.get(color_opt_name)).strip())

        stock_str = "In Stock" if v_available else "Out of Stock"
        v_summary = f"Size: {v_title} | Price: {v_price} | SKU: {v_sku} | Stock: {stock_str}"
        variant_summaries.append(v_summary)

    # Determine Primary SKU
    primary_sku = skus[0] if skus else f"PAK-TL-{product_id}"

    # Determine Pricing
    active_price = prices[0] if prices else None
    original_price = compare_at_prices[0] if compare_at_prices else active_price
    
    if original_price and active_price and original_price > active_price:
        sale_price = active_price
        discount_percentage = calculate_discount_percentage(original_price, sale_price)
    else:
        original_price = active_price
        sale_price = ""
        discount_percentage = ""

    # Stock / Availability
    availability = "In Stock" if any_available else "Out of Stock"
    stock_status = availability

    # Sizes & Colors string
    sizes_str = "|".join(sorted(list(sizes_set)))
    colors_str = "|".join(sorted(list(colors_set)))

    # Material / Fabric & Pieces
    material, pieces_info = extract_material_and_pieces(description + " " + title, tags_list)

    # Category / Subcategory / Collection hierarchy
    category = product_type or "Women's Fashion"
    subcategory = pieces_info or "Stitched"
    collection = ""
    if collections_map:
        matched_cols = [col_title for handle, col_title in collections_map.items() if handle in tags_list or handle in handle]
        if matched_cols:
            collection = "|".join(matched_cols)

    return {
        "product_id": product_id,
        "sku": primary_sku,
        "product_name": title,
        "description": description,
        "brand": vendor,
        "category": category,
        "subcategory": subcategory,
        "collection": collection,
        "product_type": product_type,
        "product_url": product_url,
        "image_url": image_url,
        "additional_image_urls": additional_image_urls,
        "price": active_price if active_price is not None else "",
        "sale_price": sale_price,
        "original_price": original_price if original_price is not None else "",
        "discount_percentage": discount_percentage,
        "currency": "PKR",
        "availability": availability,
        "stock_status": stock_status,
        "sizes": sizes_str,
        "colors": colors_str,
        "variants": " || ".join(variant_summaries),
        "material": material,
        "tags": tags_str
    }
