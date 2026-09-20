from typing import List, Dict, Any, Optional
import config
from scraper.utils import fetch_json, logger

class CatalogDiscovery:
    """Discovers all products and collections available on Tehzeeb Libas."""

    def __init__(self):
        self.collections_map: Dict[str, str] = {}

    def fetch_collections(self) -> Dict[str, str]:
        """
        Fetches all published collections and returns handle -> title mapping.
        """
        logger.info("Discovering site collections...")
        url = config.COLLECTIONS_JSON_URL
        data = fetch_json(url, params={"limit": 250})
        if data and "collections" in data:
            for col in data["collections"]:
                handle = col.get("handle", "")
                title = col.get("title", "")
                if handle and title:
                    self.collections_map[handle] = title
            logger.info(f"Discovered {len(self.collections_map)} collections.")
        return self.collections_map

    def discover_all_products(self, category_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Discovers all products via pagination from /products.json or collection endpoints.
        """
        self.fetch_collections()
        discovered_products: List[Dict[str, Any]] = []
        seen_ids = set()

        if category_filter:
            logger.info(f"Filtering discovery for category/collection matching: '{category_filter}'")
            # Find matching collection handle
            target_handle = None
            for handle, title in self.collections_map.items():
                if category_filter.lower() in title.lower() or category_filter.lower() in handle.lower():
                    target_handle = handle
                    break
            
            if target_handle:
                page = 1
                while True:
                    url = f"{config.BASE_URL}/collections/{target_handle}/products.json"
                    data = fetch_json(url, params={"page": page, "limit": 250})
                    if not data or "products" not in data or not data["products"]:
                        break
                    for p in data["products"]:
                        if p["id"] not in seen_ids:
                            seen_ids.add(p["id"])
                            discovered_products.append(p)
                    page += 1
                logger.info(f"Discovered {len(discovered_products)} products under category '{category_filter}'.")
                return discovered_products
            else:
                logger.warning(f"Category '{category_filter}' not found in collections. Falling back to full catalog discovery.")

        # Full catalog discovery via /products.json pagination
        page = 1
        logger.info("Beginning full catalog product discovery...")
        while True:
            url = config.PRODUCTS_JSON_URL
            data = fetch_json(url, params={"page": page, "limit": 250})
            if not data or "products" not in data or not data["products"]:
                logger.info(f"Pagination completed at page {page - 1}.")
                break
            
            prods = data["products"]
            logger.info(f"Discovered page {page}: {len(prods)} products.")
            for p in prods:
                if p["id"] not in seen_ids:
                    seen_ids.add(p["id"])
                    discovered_products.append(p)
            
            page += 1

        logger.info(f"Total unique products discovered: {len(discovered_products)}")
        return discovered_products
