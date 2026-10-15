"""
core/selenium_helpers.py
========================
Reusable Selenium interaction helpers shared across all automation modules.

All helpers accept *driver* and (where applicable) *wait* as explicit
parameters so they can be used as plain functions rather than mixed into
class hierarchies.
"""

import os
import sys
import time
import base64
import hashlib
import logging
import urllib.parse

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    NoSuchElementException,
    TimeoutException,
)

from core.utils import file_content_hash, xpath_literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def set_file_input_files(driver, file_input_element, file_paths: list[str], log_func=None) -> None:
    """Attach file(s) to *file_input_element*.
    Ensures element is interactable (un-hiding CSS if necessary), and attaches
    all file paths. Supports single and multi-file batch uploads.
    """
    _log = log_func or (lambda l, m: logger.debug(m))
    abs_paths = [os.path.abspath(p) for p in file_paths]
    
    # Un-hide file input if styled with display:none or visibility:hidden
    try:
        driver.execute_script(
            "arguments[0].style.display = 'block';"
            "arguments[0].style.visibility = 'visible';"
            "arguments[0].style.opacity = '1';"
            "arguments[0].style.position = 'relative';",
            file_input_element
        )
    except Exception:
        pass

    multi_str = "\n".join(abs_paths)
    file_input_element.send_keys(multi_str)
    _log('debug', f"Attached {len(abs_paths)} file(s) to upload input.")


def is_file_ready_for_upload(filepath: str) -> tuple[bool, str]:
    """Pre-flight check: verifies that *filepath* exists, is non-zero in size,
    and can be cleanly read without being locked by another application (e.g. Acrobat/Word).
    """
    if not filepath or not os.path.isfile(filepath):
        return False, "File does not exist on disk"
    try:
        size = os.path.getsize(filepath)
        if size == 0:
            return False, "File is 0 bytes (empty)"
    except OSError as e:
        return False, f"Cannot access file metadata: {e}"

    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(4096)
            if not chunk and size > 0:
                return False, "File read returned empty chunk"
        return True, "Ready"
    except (IOError, PermissionError) as e:
        return False, f"File is locked or unreadable: {e}"


def is_portal_session_expired(driver) -> bool:
    """Check if the current browser page indicates an expired session or redirect to login."""
    try:
        curr_url = (driver.current_url or "").lower()
        if any(kw in curr_url for kw in ["login", "signin", "auth", "sessionexpired", "timeout", "loggedout"]):
            return True
        page_title = (driver.title or "").lower()
        if "login" in page_title or "sign in" in page_title or "session expired" in page_title:
            return True
    except Exception:
        pass
    return False


def set_download_behavior(driver, folder_path: str, log_func=None) -> None:
    """Force all file types to download to *folder_path* via CDP commands."""
    _log = log_func or (lambda l, m: logger.log(getattr(logging, l.upper(), logging.INFO), m))
    try:
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": folder_path,
        })
    except Exception as e:
        _log('warning', f"Page.setDownloadBehavior failed: {e}")
    try:
        driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": folder_path,
            "eventsEnabled": True,
        })
    except Exception as e:
        _log('warning', f"Browser.setDownloadBehavior failed: {e}")


def enable_downloads_in_all_windows(driver, folder_path: str, log_func=None) -> None:
    """Apply :func:`set_download_behavior` to every open window/tab."""
    main_handle = driver.current_window_handle
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            set_download_behavior(driver, folder_path, log_func)
        except Exception:
            pass
    driver.switch_to.window(main_handle)


def close_extra_windows(driver, keep_handle: str, log_func=None) -> None:
    """Close all browser windows except *keep_handle*."""
    _log = log_func or (lambda l, m: None)
    for handle in list(driver.window_handles):
        if handle != keep_handle:
            try:
                driver.switch_to.window(handle)
                _log('info', f"Closing extra window: {driver.current_url}")
                driver.close()
            except Exception:
                pass
    driver.switch_to.window(keep_handle)


def wait_for_downloads_to_finish(folder_path: str, timeout: int = 40) -> bool:
    """Poll *folder_path* until no ``.crdownload`` / ``.tmp`` files remain."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            pending = [f for f in os.listdir(folder_path)
                       if f.endswith(('.crdownload', '.tmp'))]
        except FileNotFoundError:
            pending = []
        if not pending:
            return True
        time.sleep(0.5)
    return False


def force_download_via_fetch(
    driver,
    file_url: str,
    folder_path: str,
    file_name: str,
    log_func=None,
) -> bool:
    """Download a file using an in-browser JS fetch (preserves auth cookies).

    Converts the response to a base64 data-URL, decodes it in Python, and
    writes the bytes directly to disk — bypassing Chrome's renderer entirely.
    Returns ``True`` on success, ``False`` on any failure.
    """
    _log = log_func or (lambda l, m: logger.log(getattr(logging, l.upper(), logging.INFO), m))

    MIME_TO_EXT = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
        "image/tiff": ".tiff", "application/pdf": ".pdf",
        "text/plain": ".txt", "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }

    try:
        script = """
        const url = arguments[0];
        const callback = arguments[arguments.length - 1];
        fetch(url, { credentials: 'include' })
            .then(r => {
                if (!r.ok) { callback('ERROR:HTTP_' + r.status); return null; }
                return r.blob();
            })
            .then(blob => {
                if (!blob) return;
                if (blob.size === 0) { callback('ERROR:EMPTY_BLOB'); return; }
                const reader = new FileReader();
                reader.onloadend = () => callback(reader.result);
                reader.onerror = () => callback('ERROR:BLOB_READ_FAILED');
                reader.readAsDataURL(blob);
            })
            .catch(e => callback('ERROR:FETCH_EXCEPTION_' + e.toString()));
        """
        result = driver.execute_async_script(script, file_url)
        if not result or result.startswith('ERROR:'):
            _log('error', f"Fetch-fallback failed for '{file_name}': {result}")
            return False

        header, encoded = result.split(',', 1)
        file_bytes = base64.b64decode(encoded)

        mime_type = "application/octet-stream"
        if header.startswith("data:") and ";" in header:
            mime_type = header[5:header.index(";")]

        safe_name = file_name.strip() or os.path.basename(
            urllib.parse.urlparse(file_url).path) or "downloaded_file"
        for ch in '<>:"/\\|?*':
            safe_name = safe_name.replace(ch, '_')

        if not os.path.splitext(safe_name)[1]:
            ext = MIME_TO_EXT.get(mime_type.lower(), "")
            if ext:
                safe_name += ext
            _log('info', f"Inferred extension '{ext or 'none'}' from MIME '{mime_type}'.")

        os.makedirs(folder_path, exist_ok=True)
        out_path = os.path.join(folder_path, safe_name)

        if os.path.exists(out_path):
            existing_hash = file_content_hash(out_path)
            new_hash = hashlib.md5(file_bytes).hexdigest()
            if existing_hash == new_hash:
                _log('info', f"Identical file already on disk, skipping: {out_path}")
                return True
            base, ext = os.path.splitext(safe_name)
            counter = 1
            while os.path.exists(out_path):
                out_path = os.path.join(folder_path, f"{base}__{counter + 1}{ext}")
                counter += 1

        with open(out_path, 'wb') as fh:
            fh.write(file_bytes)
        _log('info', f"Fetch-fallback wrote file to disk: {out_path}")
        return True

    except Exception as e:
        _log('error', f"Fetch-fallback exception for '{file_name}': {e}")
        return False


# ---------------------------------------------------------------------------
# Click / interaction helpers
# ---------------------------------------------------------------------------

def click_xpath(
    driver,
    wait,
    xpath: str,
    description: str = "element",
    hover_first: bool = False,
    timeout_override: float | None = None,
    log_func=None,
):
    """Wait for *xpath* to be clickable, scroll it into view, then click it.

    Uses *timeout_override* seconds if supplied, otherwise uses *wait*'s
    built-in timeout.
    """
    _log = log_func or (lambda l, m: logger.debug(m))
    active_wait = WebDriverWait(driver, timeout_override, poll_frequency=0.2) \
        if timeout_override else wait
    el = active_wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
    except Exception:
        pass
    if hover_first:
        try:
            ActionChains(driver).move_to_element(el).perform()
            time.sleep(0.1)
        except Exception:
            pass
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    _log('debug', f"Clicked: {description}")
    return el


def try_click_xpath(
    driver,
    xpath: str,
    description: str = "element",
    timeout: float = 3,
    log_func=None,
) -> bool:
    """Attempt an optional click; return ``True`` on success, ``False`` if the
    element is absent or not clickable within *timeout* seconds."""
    try:
        if not driver.find_elements(By.XPATH, xpath):
            return False
        wait = WebDriverWait(driver, timeout, poll_frequency=0.2)
        el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        el.click()
        if log_func:
            log_func('debug', f"Clicked (optional): {description}")
        return True
    except Exception:
        return False


def detect_unsupported_format_error(driver, timeout: float = 2.0) -> str | None:
    """After an upload submit, some sites show an inline error/toast saying
    the file type isn't accepted instead of the normal success confirmation.
    Detect that here so it can be classified distinctly (skip permanently)
    rather than as a generic, possibly-transient failure that would
    otherwise keep getting retried.

    NOTE on the leaf-only restriction below: plain '//*[contains(...)]' also
    matches every ANCESTOR of a genuinely matching element, since XPath's
    normalize-space(.) concatenates ALL descendant text regardless of CSS
    visibility. In document order that ancestor (e.g. the whole upload
    panel) comes first and is usually visible even when the actual matching
    text sits inside a hidden template element buried somewhere inside it --
    causing is_displayed() to wrongly return True for the ancestor and this
    function to false-positive on every upload. Restricting to elements with
    no matching descendant ('not(.//*[...])') forces the match onto the
    smallest/innermost element that actually contains the phrase, so
    visibility is checked on the real error text itself.

    Returns the matched error text, or ``None`` if no such message appeared
    within *timeout* seconds.
    """
    lower = ("translate(normalize-space(.), "
             "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')")
    phrase_clause = (
        f"contains({lower}, 'not supported') "
        f"or contains({lower}, 'unsupported file') "
        f"or contains({lower}, 'invalid file type') "
        f"or contains({lower}, 'file type is not allowed') "
        f"or contains({lower}, 'not a valid file')"
    )
    xpath = f"//*[({phrase_clause}) and not(.//*[{phrase_clause}])]"
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            candidates = driver.find_elements(By.XPATH, xpath)
            visible = [c for c in candidates if c.is_displayed()]
            if visible:
                text = (visible[0].text or visible[0].get_attribute('textContent') or '').strip()
                return text or "Unsupported file type"
        except Exception:
            pass
        time.sleep(0.2)
    return None


def get_dialog_signatures(driver) -> set:
    """Snapshot every currently visible role='dialog' element's identity, so a
    caller can later tell a genuinely NEW dialog (just opened by a click)
    apart from a stale one that was already sitting in the DOM (e.g. a
    previous file's rejection dialog that MUI never actually unmounted after
    being 'closed').

    Uses getBoundingClientRect() rather than offsetParent/is_displayed(),
    since MUI dialogs live in a Portal and can report offsetParent=null while
    still being visually present -- width/height > 0 doesn't depend on that
    CSS visibility model and matches what the user actually sees.

    Mirrors the same signature format used by FileExporter's
    _get_open_dialog_signatures for the MUI-dialog-accumulation problem.
    """
    try:
        return set(driver.execute_script(
            """
            return Array.from(document.querySelectorAll('div[role="dialog"]'))
              .map(function(d) {
                var r = d.getBoundingClientRect();
                var visible = r.width > 0 && r.height > 0;
                return (d.getAttribute('aria-labelledby') || d.id || '') + ':' + visible;
              });
            """
        ))
    except Exception:
        return set()


def detect_malformed_filename_error(
    driver,
    timeout: float = 2.0,
    before_signatures: set | None = None,
) -> str | None:
    """After an upload submit, detect the portal's distinct rejection dialog:
    'The following files are malformed or has invalid file name: <name>'.

    This is a separate, permanent per-file rejection from the generic
    unsupported-format toast above -- the file itself (its content or its
    name) is rejected outright, so it must be skipped and never retried,
    this run or any future resumed run.

    If *before_signatures* is given (a snapshot from get_dialog_signatures()
    taken right before the triggering 'Upload submit' click), only a dialog
    whose signature is NOT already in that set counts as a rejection for
    THIS file. A dialog that was already open/visible beforehand is a stale
    leftover -- e.g. a previous file's malformed-filename dialog that
    click_dialog_ok_button() dismissed visually but MUI kept mounted in the
    DOM with is_displayed()/getBoundingClientRect() still reporting it as
    visible. Without this check, every subsequent upload (even ones the
    portal genuinely accepted) would re-match that same leftover dialog and
    be misreported as rejected -- confirmed by the file actually landing on
    the portal despite the 'rejected ... malformed' log line. This is the
    same MUI-dialog-accumulation pattern already fixed for the exporter's
    download dialogs.

    Without *before_signatures*, falls back to the original whole-page,
    leaf-only text match (see the leaf-only note on
    detect_unsupported_format_error above) for backward compatibility.

    Returns the matched dialog text (including the embedded file name), or
    ``None`` if no such NEW dialog appeared within *timeout* seconds.
    """
    end_time = time.time() + timeout

    if before_signatures is not None:
        while time.time() < end_time:
            try:
                dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                for d in dialogs:
                    try:
                        rect = driver.execute_script(
                            "var r=arguments[0].getBoundingClientRect();"
                            "return {w:r.width,h:r.height};", d)
                        if not rect or rect['w'] <= 0 or rect['h'] <= 0:
                            continue
                        sig = (d.get_attribute('aria-labelledby') or d.get_attribute('id') or '') + ':True'
                        if sig in before_signatures:
                            # Already open before this upload's submit click --
                            # a stale leftover, not this file's rejection.
                            continue
                        text = (d.text or d.get_attribute('textContent') or '')
                        lower_text = text.lower()
                        if 'malformed' in lower_text and 'invalid file name' in lower_text:
                            return text.strip() or "Malformed or invalid file name"
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.2)
        return None

    # Fallback (no before_signatures supplied): original whole-page,
    # leaf-only scan.
    lower = ("translate(normalize-space(.), "
             "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')")
    phrase_clause = f"contains({lower}, 'malformed') and contains({lower}, 'invalid file name')"
    xpath = f"//*[({phrase_clause}) and not(.//*[{phrase_clause}])]"
    while time.time() < end_time:
        try:
            candidates = driver.find_elements(By.XPATH, xpath)
            visible = [c for c in candidates if c.is_displayed()]
            if visible:
                text = (visible[0].text or visible[0].get_attribute('textContent') or '').strip()
                return text or "Malformed or invalid file name"
        except Exception:
            pass
        time.sleep(0.2)
    return None


def click_dialog_ok_button(driver, log_func=None, timeout: float = 3.0) -> bool:
    """Click an 'Ok' button on the currently-open dialog/alert -- specifically
    used to dismiss the malformed/invalid-file-name rejection dialog, which
    must be explicitly acknowledged via its own 'Ok' button (unlike the
    best-effort Escape+close fallback in dismiss_error_dialog below, which is
    used for toasts that don't require an explicit acknowledgement).

    Returns ``True`` if an 'Ok' button was found and clicked within
    *timeout* seconds, ``False`` otherwise.
    """
    _log = log_func or (lambda l, m: None)
    xpath = (
        "//button[normalize-space(translate(., 'OK', 'ok')) = 'ok'] "
        "| //input[@type='button' and normalize-space(translate(@value, 'OK', 'ok')) = 'ok'] "
        "| //input[@type='submit' and normalize-space(translate(@value, 'OK', 'ok')) = 'ok'] "
        "| //a[normalize-space(translate(., 'OK', 'ok')) = 'ok']"
    )
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            candidates = driver.find_elements(By.XPATH, xpath)
            visible = [c for c in candidates if c.is_displayed()]
            if visible:
                btn = visible[0]
                try:
                    btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                _log('debug', "Clicked 'Ok' on rejection dialog.")
                time.sleep(0.3)
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def dismiss_error_dialog(driver, log_func=None) -> None:
    """Best-effort cleanup after a rejection toast/dialog (e.g. an
    unsupported-format error): close it so the next item's attempt isn't
    blocked by a leftover overlay."""
    try:
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
    except Exception:
        pass
    try:
        for b in driver.find_elements(
            By.XPATH,
            "//button[contains(translate(., 'CLOSEOK', 'closeok'), 'close') "
            "or contains(translate(., 'CLOSEOK', 'closeok'), 'ok')]"
        ):
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                break
    except Exception:
        pass
    time.sleep(0.3)


def wait_for_popup_ok(
    driver,
    wait=None,
    xpath: str = "",
    description: str = "popup OK",
    expected_text: str | None = None,
    text_timeout: float = 10,
    log_func=None,
) -> bool:
    """Click a confirmation popup button. Returns ``True`` on success."""
    _log = log_func or (lambda l, m: None)
    if isinstance(wait, dict):
        config = wait
        xpath = config.get('popup_ok_xpath') or config.get('upload_popup_ok_xpath') or xpath
        wait = WebDriverWait(driver, 8)
    elif not xpath and isinstance(wait, str):
        xpath = wait
        wait = WebDriverWait(driver, 8)
    elif wait is None:
        wait = WebDriverWait(driver, 8)

    if not xpath:
        xpath = "//button[normalize-space(translate(., 'OK', 'ok')) = 'ok'] | //input[@type='button' and normalize-space(translate(@value, 'OK', 'ok')) = 'ok']"

    if expected_text:
        try:
            popup_text_xpath = f"//div[contains(., {xpath_literal(expected_text)})]"
            WebDriverWait(driver, text_timeout).until(
                EC.presence_of_element_located((By.XPATH, popup_text_xpath)))
        except Exception:
            _log('warning', f"Expected popup text '{expected_text}' not seen; clicking OK anyway.")
    try:
        click_xpath(driver, wait, xpath, description, log_func=log_func)
        time.sleep(0.3)
        return True
    except Exception as e:
        # Fallback to generic OK button click
        try:
            generic_ok_xpath = "//button[normalize-space(translate(., 'OK', 'ok')) = 'ok'] | //input[@type='button' and normalize-space(translate(@value, 'OK', 'ok')) = 'ok'] | //a[normalize-space(translate(., 'OK', 'ok')) = 'ok']"
            for btn in driver.find_elements(By.XPATH, generic_ok_xpath):
                if btn.is_displayed():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.3)
                    return True
        except Exception:
            pass
        _log('warning', f"No confirmation popup detected for '{description}': {e}")
        return False


def safe_click(
    driver,
    wait,
    xpath: str,
    description: str = "element",
    retries: int = 3,
    log_func=None,
):
    """Robust click with inline-onclick JS fast-path and stale-element retries."""
    _log = log_func or (lambda l, m: None)
    last_exception = None
    for attempt in range(1, retries + 1):
        try:
            elem = wait.until(
                EC.presence_of_element_located((By.XPATH, xpath)),
                message=f"Timed out waiting for '{description}' to appear (XPath: {xpath})",
            )
            onclick_code = elem.get_attribute("onclick")
            if onclick_code:
                try:
                    driver.execute_script(
                        "(function(){" + onclick_code + "}).call(arguments[0]);", elem)
                    return elem
                except Exception:
                    _log('debug', f"Direct onclick invocation failed on {description}; falling back.")

            elem = wait.until(
                EC.element_to_be_clickable((By.XPATH, xpath)),
                message=f"Timed out waiting for '{description}' to become clickable (XPath: {xpath})",
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", elem)
            time.sleep(0.12)
            try:
                elem.click()
                return elem
            except ElementClickInterceptedException:
                _log('debug', f"Native click intercepted on {description}; retrying with JS click.")
                try:
                    driver.execute_script("arguments[0].click();", elem)
                    return elem
                except TimeoutException:
                    _log('debug', f"JS click on {description} triggered navigation that hit renderer timeout; stopping pending assets.")
                    try:
                        driver.execute_script("window.stop();")
                    except Exception:
                        pass
                    return elem
            except TimeoutException:
                _log('debug', f"Click on {description} triggered navigation that hit renderer timeout; stopping pending assets.")
                try:
                    driver.execute_script("window.stop();")
                except Exception:
                    pass
                return elem

        except StaleElementReferenceException:
            last_exception = sys.exc_info()[1]
            _log('debug', f"Stale element on {description}; re-locating (attempt {attempt}/{retries}).")
            time.sleep(0.5)
        except ElementClickInterceptedException as e:
            last_exception = e
            _log('debug', f"Click intercepted on {description} (attempt {attempt}/{retries}).")
            time.sleep(0.5)

    raise last_exception if last_exception else Exception(f"Failed to click {description}")


def safe_send_keys(
    driver,
    wait,
    xpath: str,
    value: str,
    description: str = "field",
    timeout: float = 10,
    log_func=None,
):
    """Locate a text input/textarea and type *value* into it."""
    _log = log_func or (lambda l, m: None)
    end_time = time.time() + timeout
    last_exception = None
    while time.time() < end_time:
        try:
            elem = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            tag = (elem.tag_name or "").lower()
            input_type = (elem.get_attribute("type") or "").lower()
            if tag == "select":
                raise Exception(f"XPath for '{description}' points at a <select> — check credentials file.")
            if tag not in ("input", "textarea"):
                raise Exception(f"XPath for '{description}' points at <{tag}> — cannot type into it.")
            if input_type in ("checkbox", "radio", "button", "submit", "hidden"):
                raise Exception(f"XPath for '{description}' points at input[type={input_type}].")
            if not elem.is_displayed():
                time.sleep(0.3)
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", elem)
            time.sleep(0.2)
            elem.clear()
            elem.send_keys(value)
            return elem
        except StaleElementReferenceException:
            last_exception = sys.exc_info()[1]
            time.sleep(0.3)
    if last_exception:
        raise last_exception
    raise Exception(f"Could not interact with '{description}' — never became visible.")


def select_student_row(
    driver,
    wait,
    list_xpath: str,
    student_id: str,
    category_label: str,
    timeout: float = 10,
    log_func=None,
):
    """Click the student row for *student_id* inside the given list container."""
    _log = log_func or (lambda l, m: None)
    row_xpath = (
        f"{list_xpath}//*[contains(normalize-space(.), '{student_id}') "
        f"and not(.//*[contains(normalize-space(.), '{student_id}')])]"
    )
    last_exception = None
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            candidates = driver.find_elements(By.XPATH, row_xpath)
            if not candidates and list_xpath:
                # Fallback: search anywhere on page if container-scoped search yielded nothing
                candidates = driver.find_elements(
                    By.XPATH,
                    f"//*[contains(normalize-space(.), '{student_id}') "
                    f"and not(.//*[contains(normalize-space(.), '{student_id}')])]"
                )

            visible = [c for c in candidates if c.is_displayed()]
            if not visible and candidates:
                # Element exists in DOM but may be scrolled out of view or in an overflow container
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                        candidates[0]
                    )
                    time.sleep(0.1)
                    if candidates[0].is_displayed():
                        visible = [candidates[0]]
                    else:
                        # Fallback to candidates[0] for JS click even if Selenium visibility heuristic is False
                        visible = [candidates[0]]
                except Exception:
                    pass

            if not visible:
                time.sleep(0.15)
                continue
            row_elem = visible[0]
            onclick_code = row_elem.get_attribute("onclick")
            onclick_target = row_elem
            if not onclick_code:
                ancestor = driver.execute_script(
                    "return arguments[0].closest('[onclick]');", row_elem)
                if ancestor:
                    onclick_target = ancestor
                    onclick_code = ancestor.get_attribute("onclick")
            if onclick_code:
                try:
                    driver.execute_script(
                        "(function(){" + onclick_code + "}).call(arguments[0]);", onclick_target)
                    _log('info', f"Selected student {student_id} in '{category_label}' list.")
                    return row_elem
                except Exception:
                    pass
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", row_elem)
            time.sleep(0.12)
            try:
                row_elem.click()
            except Exception:
                driver.execute_script("arguments[0].click();", row_elem)
            _log('info', f"Selected student {student_id} in '{category_label}' list.")
            time.sleep(0.2)
            return row_elem
        except StaleElementReferenceException:
            last_exception = sys.exc_info()[1]
            time.sleep(0.3)
    raise Exception(
        f"Could not select row for student {student_id} in '{category_label}' list: {last_exception}")


def wait_for_page_ready(driver, timeout: float = 15) -> None:
    """Wait until ``document.readyState == 'complete'``."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception:
        pass


def open_dropdown_menu(
    driver,
    wait,
    toggle_xpath: str,
    description: str = "dropdown toggle",
    timeout_override: float | None = 20,
    log_func=None,
):
    """Open a Bootstrap-style dropdown that may ALSO be wired to open on hover.

    Some portal pages (e.g. Orbund/PCR Educator profiles) load a
    'jquery-dropdown-hover.js' plugin that opens the dropdown on mouseover.
    If a caller unconditionally hovers *then* clicks the toggle, the click
    lands on an already-open menu and Bootstrap's toggle behavior CLOSES it
    again -- silently, with no exception, leaving every subsequent xpath or
    text search unable to find any menu item (this was the actual root cause
    of 'Files & Documents navigation failed' errors: the button reported a
    successful click, but 'aria-expanded' was 'false' the entire time).

    This helper hovers, checks whether the menu is now expanded, and only
    clicks if it still isn't -- then verifies the expanded state before
    returning, retrying once more if needed.
    """
    _log = log_func or (lambda l, m: logger.debug(m))
    active_wait = WebDriverWait(driver, timeout_override, poll_frequency=0.2) \
        if timeout_override else wait
    toggle_el = active_wait.until(EC.element_to_be_clickable((By.XPATH, toggle_xpath)))
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", toggle_el)
    except Exception:
        pass

    def _is_expanded() -> bool:
        try:
            if (toggle_el.get_attribute("aria-expanded") or "").lower() == "true":
                return True
            return bool(driver.execute_script(
                "var el = arguments[0]; var sib = el.nextElementSibling; "
                "return !!(sib && sib.classList && sib.classList.contains('show'));",
                toggle_el))
        except Exception:
            return False

    try:
        ActionChains(driver).move_to_element(toggle_el).perform()
        time.sleep(0.2)
    except Exception:
        pass

    if _is_expanded():
        _log('debug', f"{description}: opened via hover (site has hover-triggered dropdown JS) "
                       f"-- skipping click so it isn't immediately toggled shut.")
        return toggle_el

    for click_attempt in range(2):
        try:
            toggle_el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", toggle_el)
        _log('debug', f"Clicked: {description}")

        end_time = time.time() + 3
        while time.time() < end_time:
            if _is_expanded():
                return toggle_el
            time.sleep(0.15)
        _log('debug', f"{description}: not expanded after click attempt {click_attempt + 1}; retrying.")

    raise Exception(f"'{description}' did not expand after hover+click.")


def click_outside_field(driver) -> None:
    """Blur the focused input and click the document body (triggers onBlur/onChange)."""
    try:
        driver.execute_script(
            "if (document.activeElement) { document.activeElement.blur(); } "
            "document.body.click();"
        )
    except Exception:
        pass
    time.sleep(0.3)


import re as _re


def _strip_count_suffix(text: str) -> str:
    """Strip a trailing '(N)' file-count appended by the portal, e.g.
    'DC-Official Transcripts (6)' -> 'DC-Official Transcripts'.
    Works for any integer N and any amount of surrounding whitespace.
    Also normalizes Unicode dashes (en-dash, em-dash) and non-breaking spaces.
    """
    if not text:
        return ""
    s = _re.sub(r'\s*\(\d+\)\s*$', '', text).strip()
    return s.replace('\xa0', ' ').replace('\u2013', '-').replace('\u2014', '-').replace('\u2212', '-')


def _normalize_folder_name(text: str) -> str:
    """Return a clean, normalized lowercase folder string for strict exact comparisons."""
    if not text:
        return ""
    stripped = _strip_count_suffix(text)
    return " ".join(stripped.strip().lower().split())


def find_exact_folder_element(driver, folder_name: str, config: dict):
    """Find an element in the PCR folder listing whose visible text matches *folder_name* EXACTLY.
    Never matches folders that only contain folder_name as a substring (e.g. 'DC-Official Transcripts'
    will never match 'Official Transcripts').
    Never returns breadcrumb/nav links — only listing-table links.
    """
    lit = xpath_literal(folder_name)
    lit_lower = xpath_literal(folder_name.strip().lower())
    folder_name_clean = " ".join(folder_name.replace('\xa0', ' ').strip().lower().split())
    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''

    # Non-breadcrumb guard for unscoped fallbacks
    _nav_guard = "not(ancestor::*[contains(@class,'breadcrumb') or contains(@class,'nav') or contains(@id,'breadcrumb') or contains(@id,'nav')])"

    # 1. Exact XPath equality match (case-sensitive) — scoped to table only
    if scope:
        exact_xpaths = [
            f"{scope}//a[normalize-space(.) = {lit}]",
            f"{scope}//td[normalize-space(.) = {lit}]//a",
            f"{scope}//span[normalize-space(.) = {lit}]",
        ]
    else:
        exact_xpaths = [
            f"//table//a[normalize-space(.) = {lit} and {_nav_guard}]",
            f"//table//td[normalize-space(.) = {lit}]//a[{_nav_guard}]",
        ]
    for xp in exact_xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xp)
            for el in elements:
                if el.is_displayed():
                    return el
        except Exception:
            pass

    # 2. Exact XPath equality match (case-insensitive) — scoped to table only
    if scope:
        ci_xpaths = [
            f"{scope}//a[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower}]",
            f"{scope}//td[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower}]//a",
        ]
    else:
        ci_xpaths = [
            f"//table//a[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower} and {_nav_guard}]",
            f"//table//td[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower}]//a[{_nav_guard}]",
        ]
    for xp in ci_xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xp)
            for el in elements:
                if el.is_displayed():
                    return el
        except Exception:
            pass

    # 3. Python-side exact text scan — scoped, with breadcrumb guard
    try:
        if scope:
            scan_xpath = f"{scope}//a | {scope}//td//a"
        else:
            scan_xpath = f"//table//td//a[{_nav_guard}]"
        all_links = driver.find_elements(By.XPATH, scan_xpath)
        for el in all_links:
            if not el.is_displayed():
                continue
            raw_text = el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or ""
            clean_text = " ".join(_strip_count_suffix(raw_text.replace('\xa0', ' ').strip()).lower().split())
            if clean_text == folder_name_clean:
                return el
    except Exception:
        pass

    return None


def get_all_portal_folder_names(driver, config: dict) -> list[str]:
    """Return a list of all visible folder names in the current PCR table listing."""
    details = get_all_portal_folder_details(driver, config)
    return [d['name'] for d in details]


def get_all_portal_folder_details(driver, config: dict) -> list[dict]:
    """Return a list of folder dictionaries from the current PCR table listing:
    [{'name': 'DC-Official Transcripts', 'count': 6, 'raw': 'DC-Official Transcripts (6)'}, ...]

    NOTE: Only entries that contain a (count) suffix — e.g. 'Admissions Inquiry (3)' — are
    treated as folders. Plain links without a count (breadcrumbs, nav buttons) are skipped.
    """
    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''
    found = []
    seen = set()
    try:
        # Scoped to the configured table ONLY — never //table//a which picks up breadcrumbs
        if scope:
            scan_xpath = f"{scope}//a | {scope}//td//a"
        else:
            # Fallback: scan all table cells but NOT breadcrumb/nav wrappers
            scan_xpath = "//table//td//a[not(ancestor::*[contains(@class,'breadcrumb') or contains(@class,'nav') or contains(@id,'breadcrumb') or contains(@id,'nav')])]"
        elements = driver.find_elements(By.XPATH, scan_xpath)
        for el in elements:
            try:
                raw_text = el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or ""
                clean_raw = " ".join(raw_text.replace('\xa0', ' ').strip().split())
                if not clean_raw or len(clean_raw) < 1 or len(clean_raw) > 120:
                    continue
                if any(kw in clean_raw.lower() for kw in ["delete", "remove", "upload", "download", "back", "next", "prev", "page", "select all"]):
                    continue

                # Check if it looks like a file name (e.g. ends with .pdf, .docx, .png, etc.) — ignore files
                if _re.search(r'\.[a-zA-Z0-9]{2,5}$', clean_raw):
                    continue

                # REQUIRE (count) suffix — portal folders always show 'FolderName (N)'.
                # Links without this pattern are breadcrumbs, nav items, or file links.
                count_match = _re.search(r'\((\d+)\)\s*$', clean_raw)
                if count_match is None:
                    continue
                count = int(count_match.group(1))
                clean_name = _strip_count_suffix(clean_raw)

                if clean_name and clean_name.lower() not in seen:
                    seen.add(clean_name.lower())
                    found.append({
                        'name': clean_name,
                        'count': count,
                        'raw': clean_raw,
                    })
            except Exception:
                pass
    except Exception:
        pass
    return found


def get_portal_files_in_current_table(driver, config: dict, wait_timeout: float = 2.0) -> list[dict]:
    """Return all files present in the current folder listing table.
    Scans table rows, looks for rows containing file download links, file extensions,
    or file cells, and returns a list of dictionaries:
    [{'name': 'ashley_Johnson_Transcript.pdf', 'raw': '...'}, ...]
    """
    snapshot = get_portal_table_snapshot(driver, config, wait_timeout=wait_timeout)
    return snapshot['files']


def get_portal_table_snapshot(driver, config: dict, wait_timeout: float = 2.5) -> dict:
    """Scan and dump the current folder listing table in ONE pass.
    Extracts all files, URLs, sizes, and row metadata into a structured snapshot
    dictionary so post-upload verification does not need to repeatedly query or reload the DOM.
    """
    import re as _re
    from urllib.parse import urljoin
    protected_folders = [
        "admissions inquiry", "students inquiry", "files & documents",
        "files and documents", "files & docs", "documents"
    ]
    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''
    snapshot = {
        'files': [],
        'by_name': {},
        'count': 0,
        'timestamp': time.time(),
    }

    end_time = time.time() + wait_timeout
    rows = []
    while time.time() < end_time:
        try:
            rows_xpath = f"{scope}//tr[.//td]" if scope else "//table//tr[.//td]"
            rows = driver.find_elements(By.XPATH, rows_xpath)
            if rows:
                break
        except Exception:
            pass
        time.sleep(0.2)

    try:
        rows_xpath = f"{scope}//tr[.//td]" if scope else "//table//tr[.//td]"
        rows = driver.find_elements(By.XPATH, rows_xpath)
        for row in rows:
            try:
                row_raw = (row.get_attribute("textContent") or "").lower()
                if any(pf in row_raw for pf in protected_folders):
                    continue

                candidate_elements = row.find_elements(By.XPATH, ".//a | .//span | .//td")
                for el in candidate_elements:
                    raw_text = el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or ""
                    title_attr = el.get_attribute("title") or el.get_attribute("data-name") or el.get_attribute("download") or ""
                    text = " ".join(raw_text.replace('\xa0', ' ').strip().split())

                    if not text or len(text) > 200 or any(kw in text.lower() for kw in ["delete", "remove", "upload", "new folder", "back", "select all"]):
                        if not title_attr:
                            continue

                    has_ext = bool(_re.search(r'\.[a-zA-Z0-9]{2,5}$', text)) or bool(_re.search(r'\.[a-zA-Z0-9]{2,5}$', title_attr))
                    if any(pf == text.lower() for pf in protected_folders):
                        continue

                    if has_ext or title_attr:
                        href = el.get_attribute('href')
                        if not href and el.tag_name != 'a':
                            anchors = el.find_elements(By.XPATH, ".//a[@href] | ..//a[@href]")
                            if anchors:
                                href = anchors[0].get_attribute('href')
                        if href and href.startswith('/'):
                            href = urljoin(driver.current_url, href)

                        # Extract size text if present in row cells
                        size_text = ""
                        try:
                            cells = row.find_elements(By.XPATH, ".//td")
                            for cell in cells:
                                c_text = (cell.get_attribute("textContent") or "").strip()
                                if _re.search(r'\b\d+(?:\.\d+)?\s*(?:KB|MB|GB|Bytes|B)\b', c_text, _re.I):
                                    size_text = c_text
                                    break
                        except Exception:
                            pass

                        clean_keys = []
                        if text:
                            clean_keys.append(text.lower().strip())
                        if title_attr:
                            clean_keys.append(title_attr.lower().strip())

                        entry = {
                            'name': title_attr or text,
                            'clean_name': clean_keys[0] if clean_keys else '',
                            'href': href or '',
                            'size_str': size_text,
                            'raw_text': row_raw,
                            'row': row,
                            'element': el,
                        }
                        snapshot['files'].append(entry)
                        for ck in clean_keys:
                            snapshot['by_name'][ck] = entry
            except Exception:
                pass
    except Exception:
        pass

    snapshot['count'] = len(snapshot['files'])
    return snapshot


def get_portal_table_snapshot_adaptive(
    driver,
    config: dict,
    expected_filenames: list[str] = None,
    max_timeout: float = 4.0,
    poll_interval: float = 0.25,
    log_func=None,
) -> dict:
    """Adaptive settle polling:
    Continuously polls the folder table snapshot until all *expected_filenames*
    are present in the table dump, or until *max_timeout* is reached.

    Exits immediately (often in ~200-300ms) when all expected files have rendered,
    while gracefully accommodating slow server/AJAX delays without false negatives.
    """
    from core.utils import sanitize_upload_filename
    _log = log_func or (lambda l, m: None)

    expected_sets = []
    if expected_filenames:
        for fn in expected_filenames:
            variants = {fn.strip().lower(), " ".join(fn.strip().lower().split())}
            sanitized = sanitize_upload_filename(fn).strip().lower()
            variants.add(sanitized)
            variants.add(" ".join(sanitized.split()))
            expected_sets.append(variants)

    start_time = time.time()
    last_snapshot = {'files': [], 'by_name': {}, 'count': 0}

    while time.time() - start_time < max_timeout:
        last_snapshot = get_portal_table_snapshot(driver, config, wait_timeout=0.6)
        table_keys = set(last_snapshot.get('by_name', {}).keys())

        if not expected_sets:
            return last_snapshot

        all_found = True
        for variants in expected_sets:
            found_var = False
            for v in variants:
                if v in table_keys:
                    found_var = True
                    break
                # Prefix match or in raw text
                if any(v in k or k in v for k in table_keys if len(k) >= 8):
                    found_var = True
                    break
                if any(v in f.get('raw_text', '') or v in f.get('href', '').lower() for f in last_snapshot.get('files', [])):
                    found_var = True
                    break
            if not found_var:
                all_found = False
                break

        if all_found:
            elapsed = time.time() - start_time
            _log('debug', f"Adaptive snapshot settled in {elapsed:.2f}s (all {len(expected_sets)} expected files found).")
            return last_snapshot

        time.sleep(poll_interval)

    elapsed = time.time() - start_time
    _log('debug', f"Adaptive snapshot finished after {elapsed:.2f}s timeout.")
    return last_snapshot


def verify_uploaded_file_strict(
    driver,
    upload_name: str,
    local_path: str,
    config: dict,
    table_snapshot: dict | None = None,
    deep_hash_check: bool = True,
    log_func=None,
) -> tuple[bool, str]:
    """Verify that *upload_name* is present in the portal folder listing and valid.
    Uses *table_snapshot* if provided (to avoid unnecessary DOM lookups), or dumps
    the active table on demand.

    Returns: (is_verified: bool, reason: str)
      - (True, "verified_sha256")
      - (True, "verified_dom")
      - (False, "checksum_mismatch")
      - (False, "not_in_table")
    """
    log = log_func or (lambda l, m: None)
    if table_snapshot is None:
        table_snapshot = get_portal_table_snapshot(driver, config, wait_timeout=1.5)

    target_clean = upload_name.strip().lower()
    entry = table_snapshot.get('by_name', {}).get(target_clean)

    if not entry:
        from core.utils import sanitize_upload_filename
        sanitized = sanitize_upload_filename(upload_name).strip().lower()
        entry = table_snapshot.get('by_name', {}).get(sanitized)

    if not entry:
        target_norm = " ".join(target_clean.split())
        for k, v in table_snapshot.get('by_name', {}).items():
            if " ".join(k.split()) == target_norm:
                entry = v
                break

    if not entry:
        # Flexible matching for truncated filenames (e.g. '89ffb470-1c52...jpg') or raw row text
        base_prefix = target_clean[:16] if len(target_clean) > 16 else target_clean
        for k, v in table_snapshot.get('by_name', {}).items():
            if base_prefix in k or (len(k) > 10 and k in target_clean):
                entry = v
                break

    if not entry:
        for f in table_snapshot.get('files', []):
            if target_clean in f.get('raw_text', '') or target_clean in f.get('href', '').lower():
                entry = f
                break

    if not entry:
        return False, "not_in_table"

    # Entry found in table dump
    href = entry.get('href')
    if deep_hash_check and href and (href.startswith('http') or href.startswith('/')) and local_path and os.path.isfile(local_path):
        is_match = verify_portal_file_content(driver, href, local_path, log_func=log)
        if is_match:
            return True, "verified_sha256"
        else:
            log('warning', f"Hash mismatch for '{upload_name}' via href '{href}'.")
            return False, "checksum_mismatch"

    return True, "verified_dom"


def folder_exists_in_listing(driver, folder_name: str, config: dict) -> bool:
    """Check whether *folder_name* appears in the PCR folder listing table.
    Enforces STRICT EXACT MATCH of the folder name (ignoring the portal's '(N)' count suffix).
    Never matches partial prefixes or substrings.
    """
    el = find_exact_folder_element(driver, folder_name, config)
    if el is not None:
        return True

    lit = xpath_literal(folder_name)
    folder_name_clean = " ".join(folder_name.replace('\xa0', ' ').strip().lower().split())
    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''

    candidate_xpaths = [
        f"{scope}//a[contains(normalize-space(.), {lit})]",
        f"{scope}//td[contains(normalize-space(.), {lit})]//a",
        f"//a[contains(normalize-space(.), {lit})]",
        f"//td[contains(normalize-space(.), {lit})]//a",
    ]
    for xp in candidate_xpaths:
        try:
            for c in driver.find_elements(By.XPATH, xp):
                raw_text = c.get_attribute("textContent") or c.get_attribute("innerText") or c.text or ""
                clean_text = " ".join(_strip_count_suffix(raw_text.replace('\xa0', ' ').strip()).lower().split())
                # STRICT EXACT MATCH ONLY
                if clean_text == folder_name_clean:
                    return True
        except Exception:
            pass
    return False


def verify_portal_file_content(driver, download_url: str, local_filepath: str, log_func=None) -> bool:
    """Download a file from *download_url* using active Selenium session cookies
    and compare its file size and SHA-256 hash against *local_filepath*.

    Returns True if the portal file's inner content matches *local_filepath* exactly;
    False otherwise.
    """
    log = log_func or (lambda l, m: None)
    if not local_filepath or not os.path.isfile(local_filepath):
        return False

    try:
        import requests
        import tempfile
        from core.utils import compute_file_sha256

        local_size = os.path.getsize(local_filepath)
        local_hash = compute_file_sha256(local_filepath)

        session = requests.Session()
        for cookie in driver.get_cookies():
            session.cookies.set(cookie['name'], cookie['value'])

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(local_filepath)[1])
        os.close(tmp_fd)

        try:
            r = session.get(download_url, timeout=15, stream=True)
            if r.status_code != 200:
                log('warning', f"Download verification failed (HTTP {r.status_code}) for '{download_url}'.")
                return False

            with open(tmp_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            dl_size = os.path.getsize(tmp_path)
            if dl_size != local_size:
                log('info', f"Size mismatch for '{os.path.basename(local_filepath)}'. Local: {local_size} bytes, Portal: {dl_size} bytes.")
                return False

            dl_hash = compute_file_sha256(tmp_path)
            if dl_hash == local_hash:
                log('info', f"Content match verified (SHA-256) for '{os.path.basename(local_filepath)}'.")
                return True
            else:
                log('info', f"Content SHA-256 mismatch for '{os.path.basename(local_filepath)}'. Local: {local_hash[:8]}... Portal: {dl_hash[:8]}...")
                return False
        finally:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except Exception as e:
        log('warning', f"Error during content verification for '{download_url}': {e}")
        return False


def file_exists_in_listing(
    driver,
    file_name: str,
    config: dict,
    upload_name: str = None,
    local_path: str = None,
    table_snapshot: dict = None,
    log_func=None
) -> bool:
    """Check whether *file_name* (or *upload_name*) already appears in the CURRENT folder's
    listing table -- i.e. whether it has already been uploaded here.

    If *table_snapshot* is provided, uses the cached table dump instantly with zero DOM overhead.
    Enforces STRICT EXACT MATCH of file names (case-insensitive) to prevent partial substring matches.
    """
    import time as _time
    from core.utils import sanitize_upload_filename
    log = log_func or (lambda l, m: None)

    names_to_check = [file_name]
    if upload_name and upload_name != file_name:
        names_to_check.append(upload_name)
    sanitized = sanitize_upload_filename(file_name)
    if sanitized not in names_to_check:
        names_to_check.append(sanitized)

    names_lower = {n.strip().lower() for n in names_to_check}

    # 1. Fast path: check table_snapshot if supplied
    if table_snapshot and isinstance(table_snapshot, dict):
        by_name = table_snapshot.get('by_name', {})
        for nl in names_lower:
            if nl in by_name:
                entry = by_name[nl]
                href = entry.get('href')
                if local_path and os.path.isfile(local_path) and href and (href.startswith('http') or href.startswith('/')):
                    is_match = verify_portal_file_content(driver, href, local_path, log_func=log)
                    if is_match:
                        return True
                    else:
                        log('debug', f"Portal file '{file_name}' matched by name in snapshot, but content verification differed.")
                return True
        return False

    # 2. Live DOM scan with exact name matching
    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''

    matching_elements = []
    for _attempt in range(3):
        matching_elements = []

        try:
            row_xpath = f"{scope}//tr[.//td]" if scope else "//table//tr[.//td]"
            rows = driver.find_elements(By.XPATH, row_xpath)
            for r in rows:
                candidate_elements = r.find_elements(By.XPATH, ".//a | .//span | .//td")
                for el in candidate_elements:
                    raw_text = el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or ""
                    clean_text = " ".join(raw_text.replace('\xa0', ' ').strip().split()).lower()
                    if clean_text in names_lower:
                        matching_elements.append(el)
                        break
                if matching_elements:
                    break
        except Exception:
            pass

        if matching_elements:
            break
        if _attempt < 2:
            _time.sleep(0.3)

    if not matching_elements:
        return False

    # If local_path provided, attempt content (size + SHA-256 hash) verification
    if local_path and os.path.isfile(local_path):
        for el in matching_elements:
            try:
                href = el.get_attribute('href')
                if not href and el.tag_name != 'a':
                    anchors = el.find_elements(By.XPATH, ".//a[@href] | ..//a[@href]")
                    if anchors:
                        href = anchors[0].get_attribute('href')
                if href and (href.startswith('http') or href.startswith('/')):
                    if href.startswith('/'):
                        from urllib.parse import urljoin
                        href = urljoin(driver.current_url, href)
                    is_match = verify_portal_file_content(driver, href, local_path, log_func=log)
                    if is_match:
                        return True
                    else:
                        log('debug', f"Portal file with name '{file_name}' found, but inner content verification differed or was not verifiable.")
            except Exception as e:
                log('debug', f"Could not extract href/verify content for matching element: {e}")

    # File is confirmed present by exact filename/listing match
    return True


def _wait_for_folder_navigation(driver, inside_xpath: str, folder_name: str, log_func=None, timeout: float = 3.0) -> bool:
    """Poll up to *timeout* seconds to confirm that clicking a folder actually
    navigated INSIDE it.  Success criterion: the folder link is no longer
    visible in the listing table (because we are now viewing the folder's
    own contents, not the parent listing).

    Returns True if confirmed, False if timed out.
    """
    log = log_func or (lambda l, m: None)
    _deadline = time.time() + timeout
    while time.time() < _deadline:
        time.sleep(0.15)
        try:
            els = driver.find_elements(By.XPATH, inside_xpath)
            if not any(e.is_displayed() for e in els):
                log('debug', f"[NAV-CONFIRM] Confirmed navigation inside '{folder_name}'.")
                return True
        except Exception:
            pass
    log('debug', f"[NAV-CONFIRM] Timed out waiting for navigation inside '{folder_name}'.")
    return False


def click_folder_by_name(driver, wait, folder_name: str, config: dict, description: str, log_func=None):
    """Click a folder link in a PCR listing matching *folder_name*.
    Guarantees STRICT EXACT MATCH (e.g. 'Official Transcripts' will NEVER match
    'DC-Official Transcripts' or 'Official Transcripts - 2024').

    After clicking, waits up to 5 s to confirm that navigation INTO the folder
    actually took place (the folder link disappears from the listing table).
    If not confirmed, retries with JavaScript click. If still not confirmed after
    3 attempts, raises an exception.

    IMPORTANT: Never uses unscoped XPaths that could match breadcrumb navigation links —
    those would navigate UP the hierarchy instead of INTO the folder.
    """
    log = log_func or (lambda l, m: None)
    folder_name_clean = " ".join(folder_name.replace('\xa0', ' ').strip().lower().split())
    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''

    # Non-breadcrumb guard: exclude links that are children of breadcrumb/nav elements
    _nav_guard = "not(ancestor::*[contains(@class,'breadcrumb') or contains(@class,'nav') or contains(@id,'breadcrumb') or contains(@id,'nav')])"

    # Use strict equality XPaths (= instead of contains) to prevent prefix collisions
    lit = xpath_literal(folder_name)
    lit_lower = xpath_literal(folder_name.strip().lower())
    if scope:
        strict_candidate_xpaths = [
            f"{scope}//a[normalize-space(.) = {lit}]",
            f"{scope}//td[normalize-space(.) = {lit}]//a",
            f"{scope}//a[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower}]",
        ]
    else:
        strict_candidate_xpaths = [
            f"//table//a[normalize-space(.) = {lit} and {_nav_guard}]",
            f"//table//td[normalize-space(.) = {lit}]//a[{_nav_guard}]",
            f"//table//a[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower} and {_nav_guard}]",
        ]

    # XPath to confirm we navigated INSIDE the folder:
    # once inside, the folder link disappears from the listing table.
    _tf_lit = xpath_literal(folder_name)
    _inside_xpath = (
        f"{scope}//a[normalize-space(.) = {_tf_lit}]"
        if scope else
        f"//table//a[normalize-space(.) = {_tf_lit} and {_nav_guard}]"
    )

    end_time = time.time() + (wait._timeout if hasattr(wait, '_timeout') else 15)
    last_err = None

    while time.time() < end_time:
        try:
            # 1. First check if exact folder element is found directly
            el = find_exact_folder_element(driver, folder_name, config)
            if el:
                # Extra safety: skip if this element is a breadcrumb link
                try:
                    parent_classes = " ".join([
                        el.find_element(By.XPATH, "ancestor::*[1]").get_attribute("class") or "",
                        el.find_element(By.XPATH, "ancestor::*[2]").get_attribute("class") or "",
                    ]).lower()
                    if "breadcrumb" in parent_classes or "nav" in parent_classes:
                        log('debug', f"Skipping breadcrumb-anchored folder element for '{folder_name}'.")
                    else:
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                        time.sleep(0.15)
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        log('debug', f"Clicked exact folder '{folder_name}'.")
                        # Confirm navigation
                        if _wait_for_folder_navigation(driver, _inside_xpath, folder_name, log):
                            return
                        # Not confirmed — let loop retry
                        log('warning', f"Click on '{folder_name}' did not confirm navigation. Retrying...")
                        continue
                except Exception:
                    # If ancestor check fails, proceed with click
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                    time.sleep(0.15)
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    log('debug', f"Clicked exact folder '{folder_name}'.")
                    if _wait_for_folder_navigation(driver, _inside_xpath, folder_name, log):
                        return
                    log('warning', f"Click on '{folder_name}' (fallback) did not confirm navigation. Retrying...")
                    continue

            # 2. Gather strict-equality candidates
            candidates = []
            for xp in strict_candidate_xpaths:
                try:
                    found = driver.find_elements(By.XPATH, xp)
                    for f in found:
                        if f.is_displayed() and f not in candidates:
                            candidates.append(f)
                except Exception:
                    pass

            # Python-side exact match among candidates ONLY
            best_el = None
            for c in candidates:
                raw_text = c.get_attribute("textContent") or c.get_attribute("innerText") or c.text or ""
                clean_text = " ".join(_strip_count_suffix(raw_text.replace('\xa0', ' ').strip()).lower().split())
                if clean_text == folder_name_clean:
                    best_el = c
                    break

            if best_el:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", best_el)
                time.sleep(0.15)
                try:
                    best_el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", best_el)
                log('debug', f"Clicked exact folder '{folder_name}' via strict candidate.")
                if _wait_for_folder_navigation(driver, _inside_xpath, folder_name, log):
                    return
                log('warning', f"Candidate click on '{folder_name}' did not confirm navigation. Retrying...")
                continue

        except Exception as e:
            last_err = e
        time.sleep(0.3)

    raise Exception(f"Folder '{folder_name}' not found in listing for: {description}. Last error: {last_err}")


def safe_select_dropdown(
    driver,
    xpath: str,
    value: str,
    description: str = "dropdown",
    timeout: float = 12,
    blur_after: bool = True,
    log_func=None,
):
    """Select *value* from a ``<select>`` dropdown by visible text."""
    from selenium.webdriver.support.ui import Select
    _log = log_func or (lambda l, m: None)
    end_time = time.time() + timeout
    last_exception = None
    while time.time() < end_time:
        try:
            matches = driver.find_elements(By.XPATH, xpath)
            if not matches:
                time.sleep(0.2)
                continue
            elem = matches[0]
            if (elem.tag_name or "").lower() != "select":
                raise Exception(f"XPath for '{description}' is not a <select>.")
            if not elem.is_displayed():
                time.sleep(0.2)
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", elem)
            time.sleep(0.15)
            sel = Select(elem)
            try:
                sel.select_by_visible_text(value)
            except NoSuchElementException:
                matched = next((o for o in sel.options if o.text.strip() == value.strip()), None)
                if matched is None:
                    available = [o.text for o in sel.options]
                    raise Exception(f"No option '{value}' in '{description}'. Available: {available}")
                matched.click()
            if blur_after:
                click_outside_field(driver)
            return elem
        except StaleElementReferenceException:
            last_exception = sys.exc_info()[1]
            time.sleep(0.2)
        except Exception as e:
            last_exception = e
            time.sleep(0.2)

    raise Exception(f"Failed to select '{value}' from '{description}': {last_exception}")


def delete_portal_file_by_name(driver, wait, file_name: str, config: dict, log_func=None) -> bool:
    """Locate and delete a FILE by *file_name* from the current PCR folder table listing.
    CRITICAL: NEVER deletes a folder under any circumstance.
    """
    log = log_func or (lambda l, m: None)

    # ---------------------------------------------------------
    # SAFETY GUARD 1: Block any attempt to target a folder
    # ---------------------------------------------------------
    protected_folders = [
        "admissions inquiry", "students inquiry", "files & documents",
        "files and documents", "files & docs", "documents"
    ]
    fn_lower = file_name.strip().lower()
    if any(pf in fn_lower for pf in protected_folders):
        log('error', f"[SECURITY GUARD] Blocked attempt to delete protected folder '{file_name}'. FOLDERS MUST NEVER BE DELETED!")
        return False

    table = config.get('folder_listing_table_xpath', '')
    scope = table if table else ''

    lit = xpath_literal(file_name)
    # Require EXACT text match on the file name in the row
    row_xpath = (
        f"{scope}//tr[.//a[normalize-space(.) = {lit}] or .//td[normalize-space(.) = {lit}] or .//span[normalize-space(.) = {lit}]] "
        f"| //tr[.//a[normalize-space(.) = {lit}] or .//td[normalize-space(.) = {lit}] or .//span[normalize-space(.) = {lit}]]"
    )
    rows = driver.find_elements(By.XPATH, row_xpath)
    if not rows:
        log('warning', f"Cannot delete file '{file_name}': exact file row not found in current listing.")
        return False

    target_row = rows[0]

    # ---------------------------------------------------------
    # SAFETY GUARD 2: If the row contains a protected folder name, ABORT!
    # ---------------------------------------------------------
    row_text = (target_row.get_attribute("textContent") or "").lower()
    for pf in protected_folders:
        if pf in row_text:
            log('error', f"[SECURITY GUARD] Target row contains folder name '{pf}'. Aborting deletion — FOLDERS ARE NEVER DELETED!")
            return False

    # ---------------------------------------------------------
    # SAFETY GUARD 3: Only match FILE delete buttons (explicit title="Delete File")
    # NEVER match buttons with title/alt/href containing "folder" or "dir"
    # ---------------------------------------------------------
    delete_xpaths = [
        ".//a[.//img[contains(translate(@title,'DELETE FILE','delete file'),'delete file')]]",
        ".//img[contains(translate(@title,'DELETE FILE','delete file'),'delete file')]",
        ".//a[contains(translate(@title,'DELETE FILE','delete file'),'delete file')]",
        ".//a[.//img[contains(@src,'close') and not(contains(translate(@title,'FOLDER','folder'),'folder'))]]",
    ]

    delete_elem = None
    for dxp in delete_xpaths:
        try:
            elems = target_row.find_elements(By.XPATH, dxp)
            for el in elems:
                title_attr = (el.get_attribute("title") or "").lower()
                alt_attr = (el.get_attribute("alt") or "").lower()
                href_attr = (el.get_attribute("href") or "").lower()

                if "folder" in title_attr or "folder" in alt_attr or "folder" in href_attr:
                    log('error', f"[SECURITY GUARD] Delete element refers to a folder ({title_attr}). Blocked!")
                    continue
                if "dir" in title_attr or "directory" in title_attr:
                    continue

                delete_elem = el
                break
            if delete_elem:
                break
        except Exception:
            pass

    if not delete_elem:
        log('warning', f"Could not find file-specific delete button for '{file_name}' in row.")
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", delete_elem)
        time.sleep(0.3)
        try:
            delete_elem.click()
        except Exception:
            driver.execute_script("arguments[0].click();", delete_elem)
        log('info', f"Clicked delete file for '{file_name}'. Handling possible confirmation popup...")
        time.sleep(0.4)

        # 1. Browser native alert/confirm dialog
        try:
            alert = driver.switch_to.alert
            alert.accept()
            log('info', f"Accepted browser confirm dialog for deleting '{file_name}'.")
        except Exception:
            pass

        # 2. In-page OK / Confirm button
        ok_xpaths = [
            config.get('upload_popup_ok_xpath', '') or None,
            "//input[@type='button' and (contains(translate(@value,'OK','ok'),'ok') or contains(translate(@value,'YES','yes'),'yes'))]",
            "//button[contains(translate(normalize-space(.),'OK YES CONFIRM','ok yes confirm'),'ok') or contains(translate(normalize-space(.),'OK YES CONFIRM','ok yes confirm'),'yes')]",
            "//a[contains(translate(normalize-space(.),'OK YES','ok yes'),'ok') or contains(translate(normalize-space(.),'OK YES','ok yes'),'yes')]",
        ]
        for ok_xp in ok_xpaths:
            if ok_xp:
                try_click_xpath(driver, ok_xp, "Delete confirmation OK", timeout=2, log_func=log)

        time.sleep(0.8)
        remaining = driver.find_elements(By.XPATH, row_xpath)
        if not remaining:
            log('info', f"Successfully deleted misplaced file '{file_name}' from portal.")
            return True
        else:
            log('warning', f"File '{file_name}' still visible after delete action.")
            return False
    except Exception as e:
        log('error', f"Exception while deleting file '{file_name}': {e}")
        return False

    raise Exception(f"Could not interact with delete button within timeout.")
