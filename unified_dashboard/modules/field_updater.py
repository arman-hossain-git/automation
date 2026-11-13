"""

modules/field_updater.py

========================

Business logic for the Field Updater automation module.

Reads a CSV file with columns:

  - link          : URL of the page to navigate to

  - <FIELD_NAME>  : Any additional column header is treated as a field label

                    to locate on the page; the cell value is what gets set.

Example CSV:

  link,GRADING_POLICY,TERM

  https://example.com/page1,Standard Grading Policy,Fall 2024

  https://example.com/page2,Letter Grade,Spring 2025

For each row, the module:

  1. Navigates to `link`

  2. For each extra column: locates the field by its header name and sets the value.

       - Detects element type (text input, textarea, select dropdown,

         checkbox, radio, or generic) and applies the correct interaction.

  3. Logs success / failure to a run-scoped CSV next to the input CSV.

Worker (parallel) support: run_parallel() spawns N Chrome instances, each

processing its own partition of the CSV rows independently.

Learning Integration:

  - Records login-page latency per domain for dynamic timeout tuning.

  - Records run summary (duration, success count) for the Learning dashboard.

"""

import os

import csv

import time

import math

import threading

import datetime

from selenium.webdriver.common.by import By

from selenium.webdriver.support.ui import WebDriverWait, Select

from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import TimeoutException

from core.chrome_driver import launch_chrome_driver, build_basic_options, safe_quit_driver

from core.settings_manager import settings

from core.selenium_helpers import (
    safe_send_keys,
    safe_select_dropdown,
    safe_click,
    is_portal_session_expired,
)

from core.learning_system import learning_engine

from core.session_manager import (

    SessionManager,

    process_item_with_retry,

    BrowserSessionError,

    is_dead_session_error,

    check_network_connection,

    wait_for_network_connection,

)

from core.utils import new_run_id, describe_exception

MODULE_ID = "field_updater"

# Thread-safe lock for CSV log writes (used when no caller-provided lock)

_csv_write_lock = threading.Lock()

# ---------------------------------------------------------------------------

# CSV parsing

# ---------------------------------------------------------------------------

def parse_field_updater_csv(csv_path: str) -> list:

    """Parse the Field Updater CSV.

    Format:

      - First column must be the page link (header: 'link', 'url', etc.).

      - Every other column header is a field label/name to locate on the page;

        the cell value in that column is the value to set.

    Example:

      link,GRADING_POLICY,TERM

      https://example.com/page,Standard Grading Policy,Fall 2024

    Returns a list of dicts:

      {'link': str, 'fields': [{'name': str, 'value': str}, ...], 'row_num': int}

    """

    rows = []

    try:

        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:

            sample = f.read(4096)

            f.seek(0)

            delimiter = ';' if (';' in sample and ',' not in sample) else ','

            reader = csv.DictReader(f, delimiter=delimiter)

            if reader.fieldnames is None:

                return []

            raw_headers = list(reader.fieldnames)

            # Normalise for link-column detection only

            norm_map = {

                h: h.strip().lower().replace(' ', '').replace('_', '')

                for h in raw_headers

            }

            # Identify the link column (first column wins if ambiguous)

            link_col = None

            for raw, norm in norm_map.items():

                if norm in ('link', 'url', 'pagelink', 'pageurl', 'studentlink'):

                    link_col = raw

                    break

            # Fallback: first column is the link

            if link_col is None and raw_headers:

                link_col = raw_headers[0]

            if link_col is None:

                return []

            # All other columns are field names

            field_cols = [h for h in raw_headers if h != link_col]

            for row_num, row in enumerate(reader, start=2):

                link = (row.get(link_col) or '').strip()

                if not link:

                    continue

                fields = []

                for col in field_cols:

                    value = (row.get(col) or '').strip()

                    # Always include the field so we attempt to set it;

                    # empty string means "clear the field".

                    fields.append({'name': col, 'value': value})

                rows.append({

                    'link': link,

                    'fields': fields,

                    'row_num': row_num,

                })

    except Exception:

        return []

    return rows

# ---------------------------------------------------------------------------

# Main class

# ---------------------------------------------------------------------------

class FieldUpdater:

    """Navigates to each page URL and updates the specified fields."""

    def __init__(self):

        self._log = lambda l, m: None

        self._on_progress = lambda d: None

        self._abort = lambda: False

    # ------------------------------------------------------------------

    # Public entry points

    # ------------------------------------------------------------------

    def run(

        self,

        config: dict,

        csv_path: str,

        devtools: bool = False,

        on_progress=None,

        abort_flag=None,

        log=None,

        session_inputs: dict | None = None,

        _module_id_override: str | None = None,

    ) -> None:

        """Single-worker (sequential) run."""

        self._log = log or (lambda l, m: None)

        self._on_progress = on_progress or (lambda d: None)

        self._abort = abort_flag or (lambda: False)

        self._run_id = getattr(self, '_run_id', None) or new_run_id()

        rows = parse_field_updater_csv(csv_path)

        if not rows:

            self._log('error', 'No valid rows found in CSV. Aborting.')

            return

        active_module_id = _module_id_override or MODULE_ID

        session_inputs = session_inputs or {

            'csv_path': os.path.normpath(csv_path).replace('\\', '/'),

            'login_url': config.get('login_url', config.get('url', '')),

        }

        session_data = SessionManager.start_session(

            active_module_id, session_inputs, total_items=len(rows)

        )

        completed_ids = set(str(x) for x in session_data.get('completed_item_ids', []))

        if completed_ids:

            self._log('info', f"[SESSION] Resuming — {len(completed_ids)} rows already done.")

        self._on_progress({'type': 'total', 'total': len(rows)})

        login_url = config.get('login_url', config.get('url', ''))

        if login_url and not login_url.startswith('http'):

            login_url = 'https://' + login_url

        domain_key = learning_engine.extract_domain(login_url)

        effective_devtools = learning_engine.resolve_devtools(devtools, domain_key, log_func=self._log)

        headless = settings.get('headless', False)

        chrome_options = build_basic_options(devtools=effective_devtools, headless=headless)

        driver = None

        run_start = time.time()

        success_count = 0

        try:

            # Launch driver with retry

            for launch_attempt in range(1, 4):

                try:

                    driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))

                    break

                except Exception as le:

                    if launch_attempt < 3 and not self._abort():

                        self._log('warning', f"Chrome launch failed (attempt {launch_attempt}/3): {le}. Retrying...")

                        time.sleep(launch_attempt * 2)

                    else:

                        raise

            if driver is None:

                raise RuntimeError("Failed to launch Chrome driver")

            learned_timeout = learning_engine.get_optimal_timeout(login_url, 'page_load', default_timeout=15.0)

            wait = WebDriverWait(driver, max(learned_timeout, 15.0))

            # Login

            if login_url:

                self._log('info', f"Navigating to login page: {login_url}")

                t0 = time.time()

                driver.get(login_url)

                learning_engine.record_latency(login_url, 'page_load', time.time() - t0)

                self._perform_login(driver, wait, config, login_url)

            def _restart_driver(max_attempts=3):

                nonlocal driver, wait

                self._log('warning', "[RECOVERY] Chrome session lost. Relaunching...")

                for attempt in range(1, max_attempts + 1):

                    if self._abort():

                        return False

                    safe_quit_driver(driver, log_func=lambda m: self._log('debug', m))

                    driver = None

                    time.sleep(attempt * 2.5)

                    try:

                        driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))

                        wait = WebDriverWait(driver, max(learned_timeout, 15.0))

                        if login_url:

                            driver.get(login_url)

                            self._perform_login(driver, wait, config, login_url)

                        self._log('info', f"[RECOVERY] Browser restored on attempt {attempt}.")

                        return True

                    except Exception as ex:

                        self._log('warning', f"[RECOVERY] Attempt {attempt}/{max_attempts} failed: {ex}")

                return False

            for idx, row in enumerate(rows):

                if self._abort():

                    break

                item_key = str(row.get('row_num', idx + 2))

                link = row['link']

                fields = row['fields']

                if item_key in completed_ids:

                    self._log('info', f"[SESSION] Skipping row {item_key} (already completed).")

                    self._on_progress({'type': 'item_done', 'completed': idx + 1})

                    continue

                self._on_progress({

                    'type': 'item_start',

                    'idx': idx,

                    'student_id': item_key,

                    'link': link,

                    'total': len(rows),

                })

                # Capture variables for closure

                _item_key = item_key

                _link = link

                _fields = fields

                def _process_row():

                    # Network guard

                    if not check_network_connection():

                        self._log('warning', "[NETWORK] Connection lost. Waiting for recovery...")

                        wait_for_network_connection(

                            retry_wait_seconds=5, abort_flag=self._abort, log_func=self._log

                        )

                        _restart_driver()

                    # Navigate to target page

                    self._log('info', f"[ROW {_item_key}] Navigating to: {_link}")

                    if driver is None:

                        if not _restart_driver():

                            raise RuntimeError("Driver could not be restarted")

                    assert driver is not None

                    driver.get(_link)

                    time.sleep(0.8)  # Let page settle

                    # Session guard: check AFTER navigation (avoids false-positive

                    # when the driver is still on the login page from init)

                    if is_portal_session_expired(driver):

                        self._log('warning', "Session expired — re-logging in...")

                        if login_url:

                            driver.get(login_url)

                            self._perform_login(driver, wait, config, login_url)

                            driver.get(_link)

                            time.sleep(0.8)

                    # Apply each field update

                    for field_spec in _fields:

                        if self._abort():

                            break

                        self._apply_field_update(driver, wait, field_spec['name'], field_spec['value'], _item_key, xpath_override=config.get('field_xpath', ''))

                    # Click save/update button after all fields are set
                    if not self._abort():
                        self._click_save(driver, wait, config, _item_key)

                row_ok, _, row_err = process_item_with_retry(

                    item_key, _process_row, active_module_id,

                    log_func=self._log, abort_flag=self._abort, track_session=False,

                )

                if not row_ok and (

                    isinstance(row_err, (BrowserSessionError, TimeoutException))

                    or (row_err is not None and is_dead_session_error(row_err))

                ):

                    if not self._abort():

                        if _restart_driver():

                            row_ok, _, row_err = process_item_with_retry(

                                item_key, _process_row, active_module_id,

                                log_func=self._log, abort_flag=self._abort, track_session=False,

                            )

                if row_ok:

                    success_count += 1

                    SessionManager.update_item_status(active_module_id, item_key, success=True)

                    self._write_log_csv(csv_path, item_key, link, 'SUCCESS', f"Updated {len(fields)} field(s).")

                else:

                    err_msg = describe_exception(row_err) if row_err else 'Unknown error'

                    self._log('error', f"[ROW {item_key}] FAILED: {err_msg}")

                    SessionManager.update_item_status(active_module_id, item_key, success=False)

                    self._write_log_csv(csv_path, item_key, link, 'FAILED', err_msg)

                self._on_progress({'type': 'item_done', 'completed': idx + 1})

        finally:

            safe_quit_driver(driver, log_func=lambda m: self._log('debug', m))

            duration = time.time() - run_start

            SessionManager.clear_session(active_module_id)

            learning_engine.record_run_summary(

                MODULE_ID, total_items=len(rows), success_count=success_count, total_files=0, duration_sec=duration

            )

            self._log(

                'info',

                f"Field Updater finished. {success_count}/{len(rows)} rows updated in {duration:.1f}s."

            )

    def run_parallel(

        self,

        n_workers: int,

        config: dict,

        csv_path: str,

        devtools: bool = False,

        on_progress=None,

        abort_flag=None,

        log=None,

    ) -> None:

        """Parallel run: split rows across N independent Chrome workers."""

        self._log = log or (lambda l, m: None)

        self._on_progress = on_progress or (lambda d: None)

        self._abort = abort_flag or (lambda: False)

        self._run_id = new_run_id()

        rows = parse_field_updater_csv(csv_path)

        if not rows:

            self._log('error', 'No valid rows found in CSV. Aborting parallel run.')

            return

        n_workers = max(1, min(n_workers, len(rows)))

        self._log('info', f"[PARALLEL] Starting {n_workers} worker(s) for {len(rows)} rows.")

        self._on_progress({'type': 'total', 'total': len(rows)})

        _lock = threading.Lock()

        completed_count = [0]

        success_count = [0]

        csv_lock = threading.Lock()

        run_start = time.time()

        chunk_size = math.ceil(len(rows) / n_workers)

        partitions = [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]

        def _worker(partition, worker_idx):

            wlog = lambda l, m: self._log(l, f"[W{worker_idx + 1}] {m}")

            wlog('info', f"Worker starting with {len(partition)} rows.")

            login_url = config.get('login_url', config.get('url', ''))

            if login_url and not login_url.startswith('http'):

                login_url = 'https://' + login_url

            domain_key = learning_engine.extract_domain(login_url)

            effective_devtools = learning_engine.resolve_devtools(devtools, domain_key, log_func=wlog)

            headless = settings.get('headless', False)

            chrome_options = build_basic_options(devtools=effective_devtools, headless=headless)

            learned_timeout = learning_engine.get_optimal_timeout(login_url, 'page_load', default_timeout=15.0)

            driver = None

            try:

                driver = launch_chrome_driver(chrome_options, log_func=lambda m: wlog('info', m))

                wait = WebDriverWait(driver, max(learned_timeout, 15.0))

                if login_url:

                    driver.get(login_url)

                    self._perform_login(driver, wait, config, login_url, log=wlog)

                for row in partition:

                    if self._abort():

                        break

                    item_key = str(row.get('row_num', '?'))

                    link = row['link']

                    fields = row['fields']

                    ok = False

                    err_detail = ''

                    try:

                        wlog('info', f"[ROW {item_key}] → {link}")

                        driver.get(link)

                        time.sleep(0.8)

                        # Session guard: check after navigation to avoid false-positives

                        if is_portal_session_expired(driver):

                            wlog('warning', "Session expired — re-logging in...")

                            if login_url:

                                driver.get(login_url)

                                self._perform_login(driver, wait, config, login_url, log=wlog)

                                driver.get(link)

                                time.sleep(0.8)

                        for field_spec in fields:

                            if self._abort():

                                break

                            self._apply_field_update(

                                driver, wait, field_spec['name'], field_spec['value'],

                                item_key, log=wlog,

                                xpath_override=config.get('field_xpath', '')

                            )

                        # Click save/update button after all fields are set
                        if not self._abort():
                            self._click_save(driver, wait, config, item_key, log=wlog)

                        ok = True

                        err_detail = f"Updated {len(fields)} field(s)."

                    except Exception as ex:

                        err_detail = describe_exception(ex)

                        wlog('error', f"[ROW {item_key}] FAILED: {err_detail}")

                    status_str = 'SUCCESS' if ok else 'FAILED'

                    self._write_log_csv(csv_path, item_key, link, status_str, err_detail, lock=csv_lock)

                    with _lock:

                        completed_count[0] += 1

                        if ok:

                            success_count[0] += 1

                        self._on_progress({'type': 'item_done', 'completed': completed_count[0]})

            except Exception as ex:

                wlog('error', f"Worker fatal error: {describe_exception(ex)}")

            finally:

                safe_quit_driver(driver, log_func=lambda m: wlog('debug', m))

                wlog('info', "Worker finished.")

        threads = [

            threading.Thread(target=_worker, args=(part, i), daemon=True)

            for i, part in enumerate(partitions)

        ]

        for t in threads:

            t.start()

        for t in threads:

            t.join()

        duration = time.time() - run_start

        learning_engine.record_run_summary(

            MODULE_ID, total_items=len(rows),

            success_count=success_count[0], total_files=0, duration_sec=duration

        )

        self._log(

            'info',

            f"[PARALLEL] All workers done. {success_count[0]}/{len(rows)} rows updated in {duration:.1f}s."

        )

    # ------------------------------------------------------------------

    # Field interaction

    # ------------------------------------------------------------------

    def _apply_field_update(
        self, driver, wait, field_name: str, value: str, item_key: str, log=None,
        xpath_override: str = ''
    ) -> None:
        """Find the field matching *field_name* and set it to *value*.

        Resolution order:
          1. <label> text match -> linked control (via for= or sibling)
          2. input/select/textarea by name, id, or placeholder
          3. aria-label attribute match
        """
        _log = log or self._log
        fn = field_name.strip()

        # If an explicit XPath is provided (from credentials field_xpath),
        # skip label-search and locate the element directly.
        if xpath_override:
            try:
                elem = WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.XPATH, xpath_override))
                )
            except Exception:
                raise TimeoutException(
                    'Field not found at xpath_override: ' + xpath_override
                )
            tag = (elem.tag_name or '').lower()
            input_type = (elem.get_attribute('type') or '').lower()
            _log('info', '[ROW ' + str(item_key) + '] (xpath) Updating <' + tag + '> -> ' + repr(value))
            try:
                driver.execute_script('arguments[0].scrollIntoView({block:"center"});', elem)
                import time as _time; _time.sleep(0.15)
            except Exception:
                pass
            if tag == 'select':
                try:
                    sel = Select(elem)
                    try:
                        sel.select_by_visible_text(value)
                    except Exception:
                        try:
                            sel.select_by_value(value)
                        except Exception:
                            for opt in sel.options:
                                if value.strip().lower() in opt.text.strip().lower():
                                    opt.click()
                                    break
                    _log('info', '[ROW ' + str(item_key) + '] Dropdown set to ' + repr(value))
                except Exception as de:
                    _log('error', '[ROW ' + str(item_key) + '] Dropdown failed: ' + str(de))
                    raise
            elif tag in ('input', 'textarea'):
                if input_type == 'checkbox':
                    desired = value.strip().lower() in ('true', 'yes', '1', 'checked', 'on')
                    if desired != elem.is_selected():
                        try:
                            elem.click()
                        except Exception:
                            driver.execute_script('arguments[0].click();', elem)
                elif input_type == 'radio':
                    if not elem.is_selected():
                        try:
                            elem.click()
                        except Exception:
                            driver.execute_script('arguments[0].click();', elem)
                else:
                    try:
                        elem.clear()
                    except Exception:
                        driver.execute_script('arguments[0].value = "";', elem)
                    try:
                        elem.send_keys(value)
                    except Exception:
                        driver.execute_script(
                            'arguments[0].value=arguments[1];'
                            'arguments[0].dispatchEvent(new Event("input",{bubbles:true}));'
                            'arguments[0].dispatchEvent(new Event("change",{bubbles:true}));',
                            elem, value
                        )
            else:
                driver.execute_script(
                    'arguments[0].value=arguments[1];'
                    'arguments[0].dispatchEvent(new Event("input",{bubbles:true}));'
                    'arguments[0].dispatchEvent(new Event("change",{bubbles:true}));',
                    elem, value
                )
            return  # Done — skip label-based search below

        def _xq(s):
            """XPath string literal, safe for any quote combination."""
            if chr(39) not in s:
                return chr(39) + s + chr(39)
            if chr(34) not in s:
                return chr(34) + s + chr(34)
            # concat() trick
            parts = s.split(chr(39))
            segs = [chr(39) + p + chr(39) for p in parts]
            return 'concat(' + (',"' + chr(39) + '",' ).join(segs) + ')'

        def _find_element():
            lq = _xq(fn)
            # Strategy 1: label / cell text -> linked control
            label_xpaths = [
                "//label[normalize-space(.)=" + lq + "]",
                "//label[contains(normalize-space(.)," + lq + ")]",
                "//*[self::th or self::td or self::span or self::div][normalize-space(.)=" + lq + "]",
            ]
            for lxp in label_xpaths:
                try:
                    label_elem = driver.find_element(By.XPATH, lxp)
                    for_id = label_elem.get_attribute('for')
                    if for_id:
                        try:
                            return driver.find_element(By.ID, for_id)
                        except Exception:
                            pass
                    for sxp in [
                        lxp + '/following-sibling::select[1]',
                        lxp + '/following-sibling::input[1]',
                        lxp + '/following-sibling::textarea[1]',
                        lxp + '/..//select[1]',
                        lxp + '/..//input[1]',
                        lxp + '/../following-sibling::*//select[1]',
                        lxp + '/../following-sibling::*//input[1]',
                        lxp + '/following::select[1]',
                        lxp + '/following::input[1]',
                        lxp + '/following::textarea[1]',
                    ]:
                        try:
                            ctrl = driver.find_element(By.XPATH, sxp)
                            if ctrl.is_displayed():
                                return ctrl
                        except Exception:
                            pass
                except Exception:
                    pass

            # Strategy 2: name / id / placeholder attribute match
            UC = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            LC = 'abcdefghijklmnopqrstuvwxyz'
            for attr in ('name', 'id', 'placeholder'):
                xp = (
                    '//*[self::input or self::select or self::textarea]'
                    '[translate(@' + attr + ',' + chr(34) + UC + chr(34) + ','
                    + chr(34) + LC + chr(34) + ')='
                    'translate(' + lq + ',' + chr(34) + UC + chr(34) + ','
                    + chr(34) + LC + chr(34) + ')]'
                )
                try:
                    ctrl = driver.find_element(By.XPATH, xp)
                    if ctrl.is_displayed():
                        return ctrl
                except Exception:
                    pass

            # Strategy 3: aria-label
            try:
                ctrl = driver.find_element(By.XPATH, '//*[@aria-label=' + lq + ']')
                if ctrl.is_displayed():
                    return ctrl
            except Exception:
                pass

            return None

        try:
            elem = WebDriverWait(driver, 12).until(lambda d: _find_element())
        except Exception:
            elem = None

        if elem is None:
            raise TimeoutException(
                'Field ' + repr(fn) + ' not found by label, name, id, placeholder, or aria-label.'
            )

        tag = (elem.tag_name or '').lower()
        input_type = (elem.get_attribute('type') or '').lower()
        _log('info', '[ROW ' + str(item_key) + '] Updating <' + tag + ' type=' + input_type + '> -> ' + repr(value))

        # Scroll element into view
        try:
            driver.execute_script(
                'arguments[0].scrollIntoView({block:' + chr(34) + 'center' + chr(34) + ',inline:' + chr(34) + 'center' + chr(34) + '});',
                elem
            )
            time.sleep(0.15)
        except Exception:
            pass

        if tag == 'select':
            try:
                sel = Select(elem)
                try:
                    sel.select_by_visible_text(value)
                except Exception:
                    try:
                        sel.select_by_value(value)
                    except Exception:
                        matched = False
                        for opt in sel.options:
                            if value.strip().lower() in opt.text.strip().lower():
                                opt.click()
                                matched = True
                                break
                        if not matched:
                            raise ValueError('No option matching ' + repr(value) + ' in dropdown ' + repr(fn))
                _log('info', '[ROW ' + str(item_key) + '] Dropdown ' + repr(fn) + ' set to ' + repr(value))
            except Exception as de:
                _log('error', '[ROW ' + str(item_key) + '] Dropdown set failed: ' + str(de))
                raise

        elif tag in ('input', 'textarea'):
            if input_type == 'checkbox':
                desired = value.strip().lower() in ('true', 'yes', '1', 'checked', 'on')
                if desired != elem.is_selected():
                    try:
                        elem.click()
                    except Exception:
                        driver.execute_script('arguments[0].click();', elem)

            elif input_type == 'radio':
                if not elem.is_selected():
                    try:
                        elem.click()
                    except Exception:
                        driver.execute_script('arguments[0].click();', elem)

            else:
                try:
                    elem.clear()
                except Exception:
                    driver.execute_script('arguments[0].value = ' + chr(34) + chr(34) + ';', elem)
                try:
                    elem.send_keys(value)
                except Exception:
                    driver.execute_script(
                        'arguments[0].value=arguments[1];'
                        'arguments[0].dispatchEvent(new Event(' + chr(34) + 'input' + chr(34) + ',{bubbles:true}));'
                        'arguments[0].dispatchEvent(new Event(' + chr(34) + 'change' + chr(34) + ',{bubbles:true}));',
                        elem, value
                    )
                _log('info', '[ROW ' + str(item_key) + '] Text field ' + repr(fn) + ' set to ' + repr(value))

        else:
            try:
                driver.execute_script(
                    'arguments[0].value=arguments[1];'
                    'arguments[0].dispatchEvent(new Event(' + chr(34) + 'input' + chr(34) + ',{bubbles:true}));'
                    'arguments[0].dispatchEvent(new Event(' + chr(34) + 'change' + chr(34) + ',{bubbles:true}));',
                    elem, value
                )
                _log('debug', '[ROW ' + str(item_key) + '] Set via JS on <' + tag + '>.')
            except Exception as je:
                _log('warning', '[ROW ' + str(item_key) + '] JS set failed (' + str(je) + '); clicking.')
                try:
                    elem.click()
                except Exception:
                    driver.execute_script('arguments[0].click();', elem)

    def _perform_login(self, driver, wait, config: dict, login_url: str, log=None) -> None:
        """Fill credentials + optional role, click login, verify page navigated away.

        Config keys (same as grade_submission for consistency):
          user / username       – login username
          password              – login password
          user_xpath            – XPath for username field
          password_xpath        – XPath for password field
          role / role_xpath     – optional role dropdown on login form
          login_xpath           – XPath for submit/login button
        """
        from selenium.webdriver.common.keys import Keys
        _log = log or self._log

        # ── Resolve field XPaths ────────────────────────────────────────
        user_xpath = config.get('user_xpath', '')
        pass_xpath = config.get('password_xpath', '')
        btn_xpath  = config.get('login_xpath', '')

        # Username value: support both 'user' and 'username' keys
        username = config.get('user', config.get('username', ''))
        password = config.get('password', '')

        # ── Fill username ───────────────────────────────────────────────
        if username and user_xpath:
            try:
                elem = wait.until(EC.element_to_be_clickable((By.XPATH, user_xpath)))
                elem.clear()
                elem.send_keys(username)
            except Exception as e:
                _log('warning', 'Could not fill username: ' + str(e)[:100])

        # ── Fill password ───────────────────────────────────────────────
        if password and pass_xpath:
            try:
                elem = wait.until(EC.element_to_be_clickable((By.XPATH, pass_xpath)))
                elem.clear()
                elem.send_keys(password)
            except Exception as e:
                _log('warning', 'Could not fill password: ' + str(e)[:100])

        # ── Fill role dropdown (optional) ───────────────────────────────
        role       = config.get('role', '')
        role_xpath = config.get('role_xpath', '')
        if role and role_xpath:
            try:
                sel_elem = Select(wait.until(
                    EC.presence_of_element_located((By.XPATH, role_xpath))
                ))
                sel_elem.select_by_visible_text(role)
            except Exception as e:
                _log('warning', 'Could not set role: ' + str(e)[:100])

        # ── Click login button ──────────────────────────────────────────
        login_url_before = driver.current_url
        clicked = False

        if btn_xpath:
            try:
                safe_click(driver, wait, btn_xpath, 'Login button', log_func=_log)
                clicked = True
            except Exception:
                # JS fallback
                try:
                    btn = driver.find_element(By.XPATH, btn_xpath)
                    driver.execute_script('arguments[0].click();', btn)
                    clicked = True
                    _log('debug', 'Login button clicked via JS fallback.')
                except Exception as je:
                    _log('warning', 'Login button JS click failed: ' + str(je)[:120])

        if not clicked and pass_xpath:
            # ENTER on password field as last resort
            try:
                driver.find_element(By.XPATH, pass_xpath).send_keys(Keys.RETURN)
                clicked = True
                _log('debug', 'Login submitted via ENTER key on password field.')
            except Exception as ke:
                _log('warning', 'ENTER fallback failed: ' + str(ke)[:100])

        # ── Wait for page navigation ────────────────────────────────────
        try:
            WebDriverWait(driver, 12).until(lambda d: d.current_url != login_url_before)
        except Exception:
            pass  # URL might stay same on some portals; rely on keyword check below

        # ── Verify we are no longer on the login page ───────────────────
        login_keywords = ('login', 'signin', 'sign_in', 'auth',
                          'sessionexpired', 'timeout', 'loggedout')
        verified = False
        deadline = time.time() + 15.0
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                curr  = (driver.current_url or '').lower()
                title = (driver.title or '').lower()
                if not (any(kw in curr for kw in login_keywords)
                        or 'login' in title or 'sign in' in title):
                    verified = True
                    break
            except Exception:
                break

        if verified:
            _log('info', 'Login successful.')
        else:
            try:
                curr  = driver.current_url or 'unknown'
                title = driver.title or 'unknown'
            except Exception:
                curr = title = 'unknown'
            _log('warning', 'Login verification failed. Still on: ' + curr + ' / ' + title)
            raise RuntimeError('Login failed - page did not leave login URL after 15 s.')

    def _click_save(self, driver, wait, config: dict, item_key: str, log=None) -> bool:
        """Click the save/update button after field updates, if save_xpath is configured.

        Returns True if a save button was found and clicked, False otherwise.
        """
        _log = log or self._log
        save_xpath = config.get('save_xpath', '')
        if not save_xpath:
            return False
        try:
            safe_click(driver, wait, save_xpath, 'Save button', log_func=_log)
            _log('info', '[ROW ' + str(item_key) + '] Save button clicked.')
            time.sleep(0.5)
            return True
        except Exception:
            # JS fallback
            try:
                btn = driver.find_element(By.XPATH, save_xpath)
                driver.execute_script('arguments[0].click();', btn)
                _log('info', '[ROW ' + str(item_key) + '] Save button clicked (JS).')
                time.sleep(0.5)
                return True
            except Exception as se:
                _log('warning', '[ROW ' + str(item_key) + '] Save button click failed: ' + str(se)[:120])
                return False

    def _write_log_csv(

        self,

        csv_path: str,

        item_key: str,

        link: str,

        status: str,

        detail: str,

        lock: threading.Lock | None = None,

    ) -> None:

        run_id = getattr(self, '_run_id', None) or new_run_id()

        log_dir = os.path.dirname(os.path.abspath(csv_path))

        log_file = os.path.join(log_dir, f"field_updater_log_{run_id}.csv")

        headers = ['RowKey', 'Link', 'Status', 'Detail', 'Timestamp']

        row_data = {

            'RowKey': item_key,

            'Link': link,

            'Status': status,

            'Detail': detail,

            'Timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),

        }

        _lock = lock or _csv_write_lock

        try:

            with _lock:

                file_exists = os.path.isfile(log_file)

                with open(log_file, 'a', newline='', encoding='utf-8') as f:

                    writer = csv.DictWriter(f, fieldnames=headers)

                    if not file_exists:

                        writer.writeheader()

                    writer.writerow(row_data)

        except Exception as e:

            self._log('error', f"Failed writing log CSV: {e}")

    def verify_logs(self, csv_path: str, log=None) -> dict:

        """Scan execution logs next to *csv_path* and return a summary."""

        import glob

        _log = log or (lambda l, m: None)

        log_dir = os.path.dirname(os.path.abspath(csv_path))

        total = processed = 0

        failed_keys = []

        for log_file in glob.glob(os.path.join(log_dir, "field_updater_log_*.csv")):

            try:

                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:

                    reader = csv.DictReader(f)

                    for row in reader:

                        total += 1

                        if (row.get('Status') or '').strip().upper() == 'SUCCESS':

                            processed += 1

                        else:

                            failed_keys.append(row.get('RowKey', '?'))

            except Exception as e:

                _log('warning', f"Could not read log {log_file}: {e}")

        return {'total': total, 'processed': processed, 'failed': failed_keys}

