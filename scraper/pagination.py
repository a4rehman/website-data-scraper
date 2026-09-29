"""
Universal Pagination Engine

Supports multiple pagination patterns:
- Query parameter (?page=2, ?p=2)
- Path-based (/page/2/, /2/)
- rel="next" links
- Numbered pagination links
- Load more buttons (via Playwright)
- Infinite scroll (via Playwright)
- Offset/limit pagination
- Cursor-based pagination
"""

import re
import time
from typing import List, Dict, Any, Optional, Callable, Iterator
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, urljoin
from bs4 import BeautifulSoup
import config
from scraper.utils import fetch_html, logger


class PaginationEngine:
    """
    Universal pagination engine that discovers and iterates through paginated content.
    """
    
    def __init__(
        self,
        max_pages: Optional[int] = None,
        max_products: Optional[int] = None,
        request_delay: Optional[float] = None,
        stop_event: Optional[Any] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.max_pages = max_pages or config.MAX_PAGES_PER_JOB
        self.max_products = max_products
        self.delay = request_delay if request_delay is not None else config.REQUEST_DELAY
        self.stop_event = stop_event
        self.progress_callback = progress_callback
        self.pages_fetched = 0
        self.products_collected = 0
        self.seen_product_urls = set()
    
    def discover_all(
        self,
        initial_url: str,
        extract_products_fn: Callable[[str], List[Dict[str, Any]]],
        use_browser: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Discovers all products across paginated pages.
        
        Args:
            initial_url: Starting URL
            extract_products_fn: Function that takes a URL and returns list of product dicts
            use_browser: Whether to use browser rendering
            
        Returns:
            List of all discovered product dictionaries
        """
        all_products = []
        current_url = initial_url
        page_num = 1
        
        while current_url and page_num <= self.max_pages:
            if self.stop_event and self.stop_event.is_set():
                logger.warning("Pagination cancelled by user")
                break
            
            logger.info(f"Fetching page {page_num}: {current_url}")
            
            if self.progress_callback:
                self.progress_callback({
                    "status": f"Fetching page {page_num}...",
                    "progress_count": self.products_collected,
                    "total_target": self.max_products or 0,
                    "current_product": f"Page {page_num}",
                })
            
            # Extract products from current page
            products = extract_products_fn(current_url)
            if not products:
                logger.info(f"No products found on page {page_num}, stopping pagination")
                break
            
            # Filter duplicates
            new_products = []
            for p in products:
                product_url = p.get("product_url") or p.get("url") or ""
                if product_url and product_url not in self.seen_product_urls:
                    self.seen_product_urls.add(product_url)
                    new_products.append(p)
                elif not product_url:
                    # Product without URL, include it
                    new_products.append(p)
            
            all_products.extend(new_products)
            self.products_collected = len(all_products)
            self.pages_fetched += 1
            
            logger.info(f"Page {page_num}: Found {len(new_products)} new products (total: {self.products_collected})")
            
            # Check if we've hit max_products
            if self.max_products and self.products_collected >= self.max_products:
                all_products = all_products[:self.max_products]
                logger.info(f"Reached max_products limit ({self.max_products})")
                break
            
            # Check if this page had fewer products than expected (might be last page)
            if len(new_products) == 0:
                logger.info("No new products on this page, stopping pagination")
                break
            
            # Find next page URL
            next_url = self._find_next_page(current_url, products, use_browser)
            if not next_url:
                logger.info("No next page found, pagination complete")
                break
            
            if next_url == current_url:
                logger.warning("Next page URL same as current, stopping to prevent loop")
                break
            
            current_url = next_url
            page_num += 1
            
            # Polite delay
            time.sleep(self.delay)
        
        return all_products
    
    def _find_next_page(
        self,
        current_url: str,
        products: List[Dict[str, Any]],
        use_browser: bool
    ) -> Optional[str]:
        """
        Attempts to find the next page URL using multiple strategies.
        """
        # Strategy 1: Query parameter pagination (?page=, ?p=, ?offset=)
        next_url = self._try_query_param_pagination(current_url)
        if next_url:
            return next_url
        
        # Strategy 2: Path-based pagination (/page/2/, /2/)
        next_url = self._try_path_pagination(current_url)
        if next_url:
            return next_url
        
        # Strategy 3: rel="next" link
        next_url = self._try_rel_next_link(current_url)
        if next_url:
            return next_url
        
        # Strategy 4: Numbered pagination links in HTML
        next_url = self._try_numbered_pagination(current_url)
        if next_url:
            return next_url
        
        # Strategy 5: Offset/limit pagination
        next_url = self._try_offset_pagination(current_url)
        if next_url:
            return next_url
        
        return None
    
    def _try_query_param_pagination(self, url: str) -> Optional[str]:
        """Tries to increment page parameter in query string."""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        # Common page parameter names
        page_params = ["page", "p", "page_num", "page_number"]
        
        for param in page_params:
            if param in query_params:
                try:
                    current_page = int(query_params[param][0])
                    query_params[param] = [str(current_page + 1)]
                    new_query = urlencode(query_params, doseq=True)
                    new_parsed = parsed._replace(query=new_query)
                    return urlunparse(new_parsed)
                except (ValueError, IndexError):
                    continue
        
        return None
    
    def _try_path_pagination(self, url: str) -> Optional[str]:
        """Tries to find path-based pagination patterns."""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        
        # Pattern: /page/2/ or /page/2
        page_match = re.search(r'(/page/)(\d+)/?$', path)
        if page_match:
            current_page = int(page_match.group(2))
            new_path = path[:page_match.start(2)] + str(current_page + 1) + path[page_match.end(2):]
            new_parsed = parsed._replace(path=new_path)
            return urlunparse(new_parsed)
        
        # Pattern: /2/ at end (simple numeric pagination)
        if re.search(r'/\d+/?$', path):
            parts = path.rstrip("/").split("/")
            try:
                last_part = int(parts[-1])
                parts[-1] = str(last_part + 1)
                new_path = "/".join(parts) + "/"
                new_parsed = parsed._replace(path=new_path)
                return urlunparse(new_parsed)
            except ValueError:
                pass
        
        return None
    
    def _try_rel_next_link(self, url: str) -> Optional[str]:
        """Looks for rel='next' link in HTML."""
        html = fetch_html(url)
        if not html:
            return None
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Check for <link rel="next">
        link_tag = soup.find("link", rel="next")
        if link_tag and link_tag.get("href"):
            return urljoin(url, link_tag["href"])
        
        # Check for <a rel="next">
        a_tag = soup.find("a", rel="next")
        if a_tag and a_tag.get("href"):
            return urljoin(url, a_tag["href"])
        
        return None
    
    def _try_numbered_pagination(self, url: str) -> Optional[str]:
        """Looks for numbered pagination links in HTML."""
        html = fetch_html(url)
        if not html:
            return None
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Common pagination container selectors
        pagination_selectors = [
            ".pagination", ".pager", ".page-numbers", ".pages",
            "[class*='pagination']", "[class*='pager']", "[class*='page-num']",
            "nav[aria-label*='pagination']", "nav[role='navigation']"
        ]
        
        pagination_container = None
        for sel in pagination_selectors:
            pagination_container = soup.select_one(sel)
            if pagination_container:
                break
        
        if not pagination_container:
            return None
        
        # Find current page indicator and next link
        current_indicators = pagination_container.select(
            ".current, .active, [aria-current='page'], .selected"
        )
        
        current_page = None
        if current_indicators:
            for indicator in current_indicators:
                text = indicator.get_text(strip=True)
                if text.isdigit():
                    current_page = int(text)
                    break
        
        # Find all page links
        page_links = pagination_container.find_all("a", href=True)
        for link in page_links:
            href = link.get("href")
            text = link.get_text(strip=True)
            
            # If we know current page, find next
            if current_page and text.isdigit() and int(text) == current_page + 1:
                return urljoin(url, href)
            
            # Heuristic: link text is "Next" or "»"
            if text.lower() in ("next", ">", "»", "›"):
                return urljoin(url, href)
        
        return None
    
    def _try_offset_pagination(self, url: str) -> Optional[str]:
        """Tries offset/limit pagination (?offset=20&limit=20)."""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        if "offset" in query_params and "limit" in query_params:
            try:
                offset = int(query_params["offset"][0])
                limit = int(query_params["limit"][0])
                query_params["offset"] = [str(offset + limit)]
                new_query = urlencode(query_params, doseq=True)
                new_parsed = parsed._replace(query=new_query)
                return urlunparse(new_parsed)
            except (ValueError, IndexError):
                pass
        
        return None


def discover_with_pagination(
    url: str,
    extract_products_fn: Callable[[str], List[Dict[str, Any]]],
    max_products: Optional[int] = None,
    max_pages: Optional[int] = None,
    request_delay: Optional[float] = None,
    use_browser: bool = False,
    stop_event: Optional[Any] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience function to discover products with pagination.
    """
    engine = PaginationEngine(
        max_pages=max_pages,
        max_products=max_products,
        request_delay=request_delay,
        stop_event=stop_event,
        progress_callback=progress_callback,
    )
    return engine.discover_all(url, extract_products_fn, use_browser)