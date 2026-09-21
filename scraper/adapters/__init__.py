from typing import Optional, List
from scraper.adapters.base import ProductAdapter
from scraper.adapters.shopify import ShopifyAdapter
from scraper.adapters.woocommerce import WooCommerceAdapter
from scraper.adapters.temu import TemuAdapter
from scraper.adapters.generic import GenericAdapter

_ADAPTERS: List[ProductAdapter] = [
    TemuAdapter(),
    ShopifyAdapter(),
    WooCommerceAdapter(),
    GenericAdapter(),  # Fallback must always be last
]

def get_adapter(url: str, html: Optional[str] = None) -> ProductAdapter:
    """
    Selects the most suitable adapter for a given URL and HTML signature.
    """
    for adapter in _ADAPTERS:
        if adapter.can_handle(url, html=html):
            return adapter
    return _ADAPTERS[-1]

__all__ = [
    "ProductAdapter",
    "ShopifyAdapter",
    "WooCommerceAdapter",
    "TemuAdapter",
    "GenericAdapter",
    "get_adapter",
]
