"""
core/chrome_driver.py
=====================
Reliable Chrome WebDriver launcher.

Strategy:
  1. Try Selenium's built-in Selenium Manager (bundled with Selenium >= 4.6).
     No network call required for a matching driver build.
  2. Fall back to webdriver-manager with a configurable timeout so a network
     hiccup can't hang the automation run indefinitely.

Thread-safety:
  - A module-level lock ensures only ONE thread resolves the driver path at
    a time.  All other concurrent workers wait and then reuse the cached path,
    so webdriver-manager is never called in parallel.
  - A launch semaphore caps simultaneous Chrome process spawns to avoid
    overwhelming the OS when many workers start at once.
"""

import threading
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

logger = logging.getLogger(__name__)

# ── Module-level driver-resolution state ──────────────────────────────────────
# Protects _cached_driver_path so only one thread resolves/downloads the
# ChromeDriver binary at a time.
_driver_lock: threading.Lock = threading.Lock()
_cached_driver_path: str | None = None          # set after first successful install
_selenium_manager_works: bool | None = None     # True/False after first probe

# Limits simultaneous Chrome process launches to avoid OS spawn storms.
# Stagger up to 8 windows at once; the rest queue and launch as slots free.
_launch_semaphore: threading.Semaphore = threading.Semaphore(8)


def launch_chrome_driver(
    chrome_options: Options,
    manager_timeout: int = 30,
    log_func=None,
) -> webdriver.Chrome:
    """Start a Chrome WebDriver instance, safe to call from many threads at once.

    Parameters
    ----------
    chrome_options:
        A ``selenium.webdriver.chrome.options.Options`` object pre-configured
        by the caller (download prefs, headless flag, etc.).
    manager_timeout:
        Seconds to wait for ``webdriver-manager`` to resolve a driver path
        before giving up.  Only used if the primary Selenium-Manager attempt
        fails.
    log_func:
        Optional ``callable(str)`` used for status messages.  Falls back to
        the module-level logger if not supplied.

    Returns
    -------
    webdriver.Chrome
    """
    global _cached_driver_path, _selenium_manager_works

    _log = log_func or (lambda msg: logger.info(msg))

    # ── Step 1: resolve the driver path (serialised across all threads) ───────
    with _driver_lock:
        if _selenium_manager_works is None:
            # Probe Selenium Manager without opening a Chrome window:
            # ask it to resolve the driver path only.
            _log("Resolving ChromeDriver via Selenium Manager (path probe).")
            try:
                from selenium.webdriver.common.selenium_manager import SeleniumManager
                sm = SeleniumManager()
                # binary_paths returns a dict with 'driver_path' (and 'browser_path')
                paths = sm.binary_paths(["--browser", "chrome"])
                if paths.get("driver_path"):
                    _cached_driver_path = paths["driver_path"]
                    _selenium_manager_works = True
                    _log(f"Selenium Manager resolved driver: {_cached_driver_path}")
                else:
                    raise ValueError("No driver_path returned by Selenium Manager")
            except Exception as e:
                _selenium_manager_works = False
                _log(f"Selenium Manager probe failed, will use webdriver-manager: {e}")

        if not _selenium_manager_works and _cached_driver_path is None:
            # First fallback: download/verify via webdriver-manager (once only)
            _log("Resolving ChromeDriver (attempt 2: webdriver-manager, one-time).")
            result: dict = {}

            def _install() -> None:
                try:
                    from webdriver_manager.chrome import ChromeDriverManager
                    result['path'] = ChromeDriverManager().install()
                except Exception as inner_e:
                    result['error'] = inner_e

            installer_thread = threading.Thread(target=_install, daemon=True)
            installer_thread.start()
            installer_thread.join(manager_timeout)

            if installer_thread.is_alive():
                raise RuntimeError(
                    f"webdriver-manager did not respond within {manager_timeout}s "
                    "(likely a network/proxy issue). Check your internet connection "
                    "and that Chrome is installed and up to date, then try again."
                )
            if 'error' in result:
                raise RuntimeError(
                    "Both Selenium Manager and webdriver-manager failed to provide a "
                    f"working ChromeDriver. webdriver-manager error: {result['error']}"
                )

            _cached_driver_path = result['path']
            _log(f"ChromeDriver resolved and cached: {_cached_driver_path}")

    # ── Step 2: launch Chrome (staggered to avoid OS spawn storm) ────────────
    with _launch_semaphore:
        _log("Launching Chrome window…")
        driver = webdriver.Chrome(
            service=Service(_cached_driver_path),
            options=chrome_options,
        )
        try:
            from core.settings_manager import settings
            max_wait = settings.get("max_action_wait_seconds", 20)
            page_load_to = max(60, min(100, max_wait * 3))
            driver.set_page_load_timeout(page_load_to)
            driver.set_script_timeout(30)
        except Exception:
            try:
                driver.set_page_load_timeout(60)
                driver.set_script_timeout(30)
            except Exception:
                pass

    _log("Chrome launched successfully.")
    return driver


def safe_quit_driver(driver, timeout: float = 4.0, log_func=None) -> None:
    """Safely terminate Chrome WebDriver without hanging or raising on dead sessions.

    If ChromeDriver is hung or unresponsive (e.g. following a read timeout),
    a standard driver.quit() would block for 120 seconds in urllib3 and throw
    HTTPConnectionPool ReadTimeoutError. This helper guarantees fast, non-blocking
    termination by running quit() with a deadline and killing the underlying process if needed.
    """
    if not driver:
        return
    _log = log_func or (lambda msg: None)
    try:
        def _do_quit():
            try:
                driver.quit()
            except Exception:
                pass

        t = threading.Thread(target=_do_quit, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            _log("Chrome driver did not terminate within deadline; forcing process shutdown.")
            try:
                if hasattr(driver, "service") and driver.service and hasattr(driver.service, "process") and driver.service.process:
                    driver.service.process.kill()
            except Exception:
                pass
    except Exception as e:
        _log(f"safe_quit_driver encountered error: {e}")



def build_download_options(download_dir: str, devtools: bool = False, headless: bool = False, disable_images: bool = True) -> Options:
    """Build Chrome options tuned for silent file downloads to *download_dir*.

    *devtools*: pass True to launch with '--auto-open-devtools-for-tabs'. Some
    sites only behave correctly with DevTools attached; see
    core.learning_system.LearningEngine.should_force_devtools for how this
    gets decided automatically from past run history.

    *headless*: pass True to launch Chrome with no visible window (new
    headless mode). DevTools auto-open has no effect with no window, so it
    is skipped whenever headless is on, even if *devtools* is also True.
    """
    opts = Options()
    opts.page_load_strategy = "eager"
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,
        "profile.default_content_settings.popups": 0,
        "profile.default_content_setting_values.notifications": 2,
        "profile.content_settings.exceptions.automatic_downloads.*.setting": 1,
        "download_restrictions": 0,
        "download.extensions_to_open": "",
    }
    if disable_images and headless:
        prefs["profile.managed_default_content_settings.images"] = 2
        opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.add_experimental_option("prefs", prefs)
    opts.add_argument("--disable-features=ChromePDFViewer")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-notifications")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
    elif devtools:
        opts.add_argument("--auto-open-devtools-for-tabs")
    return opts


def build_basic_options(devtools: bool = False, headless: bool = False, disable_images: bool = True) -> Options:
    """Build standard Chrome options (no download prefs overrides).

    *headless*: pass True to launch Chrome with no visible window (new
    headless mode). '--start-maximized' and DevTools auto-open are both
    meaningless with no window, so both are skipped whenever headless is on.
    """
    opts = Options()
    opts.page_load_strategy = "eager"
    prefs = {
        "profile.default_content_setting_values.notifications": 2,
    }
    if disable_images:
        prefs["profile.managed_default_content_settings.images"] = 2
        prefs["profile.default_content_setting_values.images"] = 2
        opts.add_argument("--blink-settings=imagesEnabled=false")
        opts.add_argument("--disable-remote-fonts")
    opts.add_experimental_option("prefs", prefs)
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-breakpad")
    opts.add_argument("--disable-component-update")
    opts.add_argument("--disable-domain-reliability")
    opts.add_argument("--disable-sync")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
    else:
        opts.add_argument("--start-maximized")
        if devtools:
            opts.add_argument("--auto-open-devtools-for-tabs")
    return opts

