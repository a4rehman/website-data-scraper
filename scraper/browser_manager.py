"""
Centralized Playwright Browser Manager

Provides proper lifecycle management for headless browser instances
to prevent resource leaks and ensure clean shutdown.
"""

import threading
import logging
import config
from typing import Optional, Dict, Any, Callable
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger("scraper")


@dataclass
class BrowserConfig:
    """Configuration for browser instance."""
    headless: bool = True
    viewport: Dict[str, int] = None
    user_agent: str = None
    timeout: int = 45000
    extra_args: list = None
    
    def __post_init__(self):
        if self.viewport is None:
            self.viewport = {"width": 1920, "height": 1080}
        if self.user_agent is None:
            self.user_agent = config.USER_AGENT
        if self.extra_args is None:
            self.extra_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]


class PlaywrightManager:
    """
    Thread-safe Playwright browser manager.
    Ensures proper cleanup of browser, context, and page resources.
    """
    
    _instance = None
    _lock = threading.Lock()
    _browser = None
    _playwright = None
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._browser = None
        self._playwright = None
        self._contexts = set()
        self._pages = set()
        self._cleanup_registered = False
    
    def _ensure_browser(self) -> bool:
        """Initialize Playwright and browser if not already done."""
        if self._browser is not None:
            return True
            
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not installed")
            return False
        
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            logger.info("Playwright browser launched successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to launch Playwright browser: {e}")
            self._cleanup()
            return False
    
    def new_context(self, config: Optional[BrowserConfig] = None):
        """Create a new browser context."""
        if not self._ensure_browser():
            return None
        
        if config is None:
            config = BrowserConfig()
        
        try:
            context = self._browser.new_context(
                user_agent=config.user_agent,
                viewport=config.viewport,
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
            )
            self._contexts.add(context)
            return context
        except Exception as e:
            logger.error(f"Failed to create browser context: {e}")
            return None
    
    def new_page(self, context=None, config: Optional[BrowserConfig] = None):
        """Create a new page in the given context (or default)."""
        if context is None:
            context = self.new_context(config)
            if context is None:
                return None
            # Track that we own this context
            context._owned_by_manager = True
        
        try:
            page = context.new_page()
            self._pages.add(page)
            
            # Set default timeout
            if config and config.timeout:
                page.set_default_timeout(config.timeout)
                page.set_default_navigation_timeout(config.timeout)
            
            return page
        except Exception as e:
            logger.error(f"Failed to create page: {e}")
            return None
    
    def close_context(self, context):
        """Close a specific context."""
        if context and context in self._contexts:
            try:
                # Close all pages in this context first
                for page in context.pages:
                    try:
                        page.close()
                    except Exception:
                        pass
                context.close()
                self._contexts.discard(context)
            except Exception as e:
                logger.warning(f"Error closing context: {e}")
    
    def close_page(self, page):
        """Close a specific page."""
        if page and page in self._pages:
            try:
                page.close()
                self._pages.discard(page)
            except Exception as e:
                logger.warning(f"Error closing page: {e}")
    
    def _cleanup(self):
        """Clean up all resources."""
        # Close all pages
        for page in list(self._pages):
            try:
                page.close()
            except Exception:
                pass
        self._pages.clear()
        
        # Close all contexts
        for context in list(self._contexts):
            try:
                context.close()
            except Exception:
                pass
        self._contexts.clear()
        
        # Close browser
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        
        # Stop playwright
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        
        logger.info("Playwright resources cleaned up")
    
    def shutdown(self):
        """Explicit shutdown."""
        self._cleanup()
    
    def __del__(self):
        """Destructor ensures cleanup."""
        self._cleanup()


@contextmanager
def browser_session(config: Optional[BrowserConfig] = None):
    """
    Context manager for a complete browser session.
    Ensures proper cleanup even on exceptions.
    
    Usage:
        with browser_session() as page:
            page.goto(url)
            content = page.content()
    """
    manager = PlaywrightManager()
    context = None
    page = None
    
    try:
        context = manager.new_context(config)
        if context is None:
            yield None
            return
        
        page = manager.new_page(context, config)
        if page is None:
            yield None
            return
        
        yield page
    finally:
        if page:
            manager.close_page(page)
        if context:
            manager.close_context(context)


def render_page(
    url: str,
    wait_until: str = "domcontentloaded",
    wait_for_selector: Optional[str] = None,
    scroll: bool = False,
    scroll_count: int = 3,
    scroll_delay: float = 1.5,
    config: Optional[BrowserConfig] = None,
) -> Optional[str]:
    """
    Render a page with Playwright and return HTML content.
    
    Args:
        url: URL to render
        wait_until: When to consider navigation complete
        wait_for_selector: Optional CSS selector to wait for
        scroll: Whether to scroll down to trigger lazy loading
        scroll_count: Number of scroll iterations
        scroll_delay: Delay between scrolls (seconds)
        config: Browser configuration
        
    Returns:
        Rendered HTML content or None on failure
    """
    with browser_session(config) as page:
        if page is None:
            return None
        
        try:
            page.goto(url, wait_until=wait_until, timeout=config.timeout if config else 45000)
        except Exception as e:
            logger.warning(f"Playwright navigation warning for {url}: {e}")
        
        if wait_for_selector:
            try:
                page.wait_for_selector(wait_for_selector, timeout=10000)
            except Exception:
                pass
        
        if scroll:
            for _ in range(scroll_count):
                try:
                    page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                    import time
                    time.sleep(scroll_delay)
                except Exception:
                    pass
        
        return page.content()


def extract_with_playwright(
    url: str,
    extract_fn: Callable[[Any], Any],
    config: Optional[BrowserConfig] = None,
    wait_until: str = "domcontentloaded",
    wait_for_selector: Optional[str] = None,
) -> Any:
    """
    Extract data from a rendered page using a custom extraction function.
    
    Args:
        url: URL to render
        extract_fn: Function that takes a page object and returns extracted data
        config: Browser configuration
        wait_until: Navigation wait condition
        wait_for_selector: Optional selector to wait for
        
    Returns:
        Result of extract_fn or None on failure
    """
    with browser_session(config) as page:
        if page is None:
            return None
        
        try:
            page.goto(url, wait_until=wait_until, timeout=config.timeout if config else 45000)
        except Exception as e:
            logger.warning(f"Playwright navigation warning for {url}: {e}")
        
        if wait_for_selector:
            try:
                page.wait_for_selector(wait_for_selector, timeout=10000)
            except Exception:
                pass
        
        try:
            return extract_fn(page)
        except Exception as e:
            logger.error(f"Extraction function failed: {e}")
            return None