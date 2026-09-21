from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

class ProductAdapter(ABC):
    """
    Abstract base class for all website and platform adapters.
    """
    name: str = "Base"

    @abstractmethod
    def can_handle(self, url: str, html: Optional[str] = None) -> bool:
        """
        Returns True if this adapter can process the given URL or HTML signature.
        """
        pass

    @abstractmethod
    def analyze_url(self, url: str, html: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyzes a URL to detect platform, page type, whether JS is needed, etc.
        """
        pass

    @abstractmethod
    def discover_products(
        self,
        url: str,
        max_products: Optional[int] = None,
        use_browser: bool = False
    ) -> List[Any]:
        """
        Discovers product URLs or raw product objects from a category, collection, or search URL.
        """
        pass

    @abstractmethod
    def extract_product(
        self,
        url_or_raw: Any,
        source_url: str = "",
        use_browser: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Extracts product details from a single product URL or raw item data into the Universal Schema.
        """
        pass

    def get_domain(self, url: str) -> str:
        """Helper to extract clean domain."""
        try:
            parsed = urlparse(url)
            return parsed.netloc.replace("www.", "")
        except Exception:
            return ""
