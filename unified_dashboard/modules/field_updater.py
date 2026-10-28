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
                self._log('info', "Login successful.")

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

                    # Session guard
                    if driver is not None and is_portal_session_expired(driver):
                        self._log('warning', "Session expired — re-logging in...")
                        if login_url:
                            driver.get(login_url)
                            self._perform_login(driver, wait, config, login_url)

                    # Navigate to target page
                    self._log('info', f"[ROW {_item_key}] Navigating to: {_link}")
                    if driver is None:
                        if not _restart_driver():
                            raise RuntimeError("Driver could not be restarted")
                    assert driver is not None
                    driver.get(_link)
                    time.sleep(0.8)  # Let page settle

                    # Apply each field update
                    for field_spec in _fields:
                        if self._abort():
                            break
                        self._apply_field_update(driver, wait, field_spec['xpath'], field_spec['value'], _item_key)

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
                    wlog('info', "Login successful.")

                for row in partition:
                    if self._abort():
                        break

                    item_key = str(row.get('row_num', '?'))
                    link = row['link']
                    fields = row['fields']

                    ok = False
                    err_detail = ''
                    try:
                        if is_portal_session_expired(driver):
                            wlog('warning', "Session expired — re-logging in...")
                            if login_url:
                                driver.get(login_url)
                                self._perform_login(driver, wait, config, login_url, log=wlog)

                        wlog('info', f"[ROW {item_key}] → {link}")
                        driver.get(link)
                        time.sleep(0.8)

                        for field_spec in fields:
                            if self._abort():
                                break
                            self._apply_field_update(
                                driver, wait, field_spec['xpath'], field_spec['value'],
                                item_key, log=wlog
                            )

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
        self, driver, wait, xpath: str, value: str, item_key: str, log=None
    ) -> None:
        """Detect the field type at *xpath* and set it to *value*."""
        _log = log or self._log

        try:
            elem = WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
        except TimeoutException:
            raise TimeoutException(f"Field not found at XPath: {xpath}")

        tag = (elem.tag_name or '').lower()
        input_type = (elem.get_attribute('type') or '').lower()

        _log('info', f"[ROW {item_key}] Updating <{tag} type='{input_type}'> → '{value}'")

        # Scroll into view
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center',inline:'center'});", elem
            )
            time.sleep(0.15)
        except Exception:
            pass

        if tag == 'select':
            # Dropdown (<select>)
            safe_select_dropdown(
                driver, xpath, value, f"Row {item_key} dropdown", log_func=_log
            )

        elif tag in ('input', 'textarea'):
            if input_type == 'checkbox':
                # Checkbox: interpret truthy strings as "checked"
                desired_checked = value.strip().lower() in ('true', 'yes', '1', 'checked', 'on')
                is_checked = elem.is_selected()
                if desired_checked != is_checked:
                    try:
                        elem.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", elem)

            elif input_type == 'radio':
                # Radio button: click if not already selected
                if not elem.is_selected():
                    try:
                        elem.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", elem)

            else:
                # text / email / number / date / textarea …
                safe_send_keys(
                    driver, wait, xpath, value,
                    f"Row {item_key} text field", log_func=_log
                )

        else:
            # Generic element — try JS value injection, then click
            try:
                driver.execute_script(
                    "arguments[0].value = arguments[1];"
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                    elem, value
                )
                _log('debug', f"[ROW {item_key}] Set value via JS on <{tag}>.")
            except Exception as je:
                _log('warning', f"[ROW {item_key}] JS value set failed ({je}); trying click.")
                try:
                    elem.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", elem)

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _perform_login(self, driver, wait, config: dict, login_url: str, log=None) -> None:
        _log = log or self._log
        username  = config.get('username', '')
        password  = config.get('password', '')
        user_xpath = config.get(
            'username_xpath',
            config.get('user_xpath', '//input[@name="username" or @id="username" or @type="email"]')
        )
        pass_xpath = config.get(
            'password_xpath',
            config.get('pass_xpath', '//input[@type="password"]')
        )
        btn_xpath = config.get(
            'login_btn_xpath',
            config.get('submit_xpath', '//input[@type="submit"] | //button[@type="submit"]')
        )

        if username:
            safe_send_keys(driver, wait, user_xpath, username, "username field", log_func=_log)
        if password:
            safe_send_keys(driver, wait, pass_xpath, password, "password field", log_func=_log)
        if btn_xpath:
            try:
                btn = wait.until(EC.element_to_be_clickable((By.XPATH, btn_xpath)))
                btn.click()
                time.sleep(1.5)
            except Exception as be:
                _log('warning', f"Login button click note: {be}")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

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
