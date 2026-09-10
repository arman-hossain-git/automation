"""
core/chrome_driver.py
=====================
Reliable Chrome WebDriver launcher.

Strategy:
  1. Try Selenium's built-in Selenium Manager (bundled with Selenium >= 4.6).
     No network call required for a matching driver build.
  2. Fall back to webdriver-manager with a configurable timeout so a network
     hiccup can't hang the automation run indefinitely.
"""

import threading
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

logger = logging.getLogger(__name__)


def launch_chrome_driver(
    chrome_options: Options,
    manager_timeout: int = 20,
    log_func=None,
) -> webdriver.Chrome:
    """Start a Chrome WebDriver instance.

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
    _log = log_func or (lambda msg: logger.info(msg))

    _log("Launching Chrome WebDriver (attempt 1: Selenium Manager).")
    try:
        driver = webdriver.Chrome(options=chrome_options)
        _log("Chrome launched successfully via Selenium Manager.")
        return driver
    except Exception as e:
        _log(f"Selenium Manager launch failed, trying webdriver-manager fallback: {e}")

    _log("Launching Chrome WebDriver (attempt 2: webdriver-manager fallback).")
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

    driver = webdriver.Chrome(service=Service(result['path']), options=chrome_options)
    _log("Chrome launched successfully via webdriver-manager fallback.")
    return driver


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
    prefs = {
        "profile.default_content_setting_values.notifications": 2,
    }
    if disable_images and headless:
        prefs["profile.managed_default_content_settings.images"] = 2
        opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.add_experimental_option("prefs", prefs)
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-notifications")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
    else:
        opts.add_argument("--start-maximized")
        if devtools:
            opts.add_argument("--auto-open-devtools-for-tabs")
    return opts
