"""
modules/student_removal.py
===========================
Business logic for Student Removal from Classes in PCR Educator / portal.

Reads a CSV file with columns:
  - studentId : Student identification string / number
  - link      : URL to the class / student list page

For each student:
  1. Navigates to the class page (reuses page navigation if consecutive rows share the same link).
  2. Ensures Students tab is active (if applicable).
  3. Locates the student across Regular, Waiting, or Withdrawn list (checks learned list category first).
  4. Selects the student row.
  5. Clicks the Remove / Delete Student button (credentials XPath -> learned XPath -> semantic search -> JS fallback).
  6. Automatically confirms any browser alert (window.confirm) or modal confirmation dialog.
  7. Saves the class form (credentials XPath -> learned XPath -> semantic Save button -> JS fallback) to persist changes.
  8. Logs execution results to timestamped run CSV and master execution_results.csv.

Efficiency & Optimization:
  - Class Link Grouping: groups consecutive pending rows by class link to avoid redundant full-page reloads.
  - Parallel Workers: run_parallel() spawns N independent Chrome instances, partitioning rows across workers.
  - Learning System Integration: remembers student list categories, login XPaths, save XPaths, and page latency.
  - Session Resume & Persistence: skips already-completed removals from persistent registry and past execution logs.
  - Auto-reconnect & Session Guard: detects expired sessions or crashed drivers and re-authenticates automatically.
"""

import os
import csv
import time
import math
import glob
import json
import hashlib
import threading
from datetime import datetime
import pandas as pd

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

from core.chrome_driver import launch_chrome_driver, build_basic_options, safe_quit_driver
from core.settings_manager import settings
from core.selenium_helpers import (
    safe_click,
    select_student_row,
    is_portal_session_expired,
)
from core.learning_system import learning_engine
from core.session_manager import (
    SessionManager,
    process_item_with_retry,
    BrowserSessionError,
    is_dead_session_error,
    ensure_sessions_dir,
)
from core.utils import new_run_id, find_latest_run_log

MODULE_ID = "student_removal"

# Shared thread locks
_csv_write_lock = threading.Lock()
_registry_lock = threading.Lock()


def make_removal_key(student_id: str, link: str = "") -> str:
    """Canonical unique key for a student removal row.
    Combines student_id and link so students taking multiple classes
    are tracked accurately per class.
    """
    sid = str(student_id).strip()
    lnk = str(link).strip().rstrip("/")
    if lnk:
        return f"{sid}::{lnk}"
    return sid


def _get_registry_path(csv_path: str) -> str:
    norm_path = os.path.normcase(os.path.abspath(csv_path))
    h = hashlib.sha256(norm_path.encode("utf-8")).hexdigest()[:12]
    base = os.path.splitext(os.path.basename(csv_path))[0][:25]
    safe_base = "".join(c for c in base if c.isalnum() or c in ("-", "_"))
    return os.path.join(ensure_sessions_dir(), f"completed_removal_{safe_base}_{h}.json")


def get_completed_removal_items(csv_path: str, log_func=None) -> set[str]:
    """Return a set of canonical keys (f"{student_id}::{link}") that have
    already been successfully processed in prior runs.
    """
    _log = log_func or (lambda l, m: None)
    completed_keys: set[str] = set()
    reg_path = _get_registry_path(csv_path)

    # 1. Read persistent registry if exists
    with _registry_lock:
        if os.path.isfile(reg_path):
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        completed_keys.update(data)
                    elif isinstance(data, dict):
                        completed_keys.update(data.get("completed_keys", []))
            except Exception as e:
                _log('warning', f"Could not read completed removal registry {reg_path}: {e}")

    # 2. Scan execution_results_*.csv files in the CSV directory
    csv_dir = os.path.dirname(os.path.abspath(csv_path))
    if os.path.isdir(csv_dir):
        log_entries = []
        for res_file in glob.glob(os.path.join(csv_dir, "execution_results_*.csv")):
            try:
                file_mtime = os.path.getmtime(res_file)
                with open(res_file, "r", encoding="utf-8", errors="ignore") as fp:
                    reader = csv.DictReader(fp)
                    for row in reader:
                        status = str(row.get("Status", "")).strip().upper()
                        if status not in ("SUCCESS", "FAILED"):
                            continue
                        details = str(row.get("Details", "")).lower()
                        # Only match removal rows or general success
                        if "remove" not in details and "removal" not in details and status != "SUCCESS":
                            continue
                        sid = str(row.get("StudentId", "")).strip()
                        lnk = str(row.get("Link", "")).strip().rstrip("/")
                        if not sid:
                            continue
                        ts_str = str(row.get("Timestamp", "")).strip()
                        try:
                            ts_val = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
                        except Exception:
                            ts_val = file_mtime
                        log_entries.append((ts_val, sid, lnk, status))
            except Exception as e:
                _log('warning', f"Could not scan execution results {res_file}: {e}")

        log_entries.sort(key=lambda x: x[0])
        latest_status_by_key = {}
        for _, sid, lnk, status in log_entries:
            key = make_removal_key(sid, lnk)
            latest_status_by_key[key] = (status, sid)

        found_from_csvs = 0
        failed_retries = 0
        for key, (status, sid) in latest_status_by_key.items():
            if status == "SUCCESS":
                if key not in completed_keys:
                    completed_keys.add(key)
                    found_from_csvs += 1
            elif status == "FAILED":
                if key in completed_keys or sid in completed_keys:
                    completed_keys.discard(key)
                    completed_keys.discard(sid)
                    failed_retries += 1

        if found_from_csvs > 0:
            _log('info', f"[HISTORY] Discovered {found_from_csvs} previously completed removal items from logs.")
        if failed_retries > 0:
            _log('info', f"[HISTORY] Identified {failed_retries} removal items marked FAILED — scheduled for re-attempt.")

        with _registry_lock:
            try:
                with open(reg_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(list(completed_keys)), f, indent=2)
            except Exception:
                pass

    # 3. Check SessionManager
    try:
        session_data = SessionManager.get_unfinished_session(MODULE_ID)
        if session_data:
            for item in session_data.get("completed_item_ids", []):
                completed_keys.add(str(item).strip())
            for item in session_data.get("failed_item_ids", []):
                completed_keys.discard(str(item).strip())
    except Exception:
        pass

    return completed_keys


def mark_removal_item_completed(csv_path: str, student_id: str, link: str = ""):
    """Atomically record an item as successfully completed in the persistent registry."""
    key = make_removal_key(student_id, link)
    reg_path = _get_registry_path(csv_path)
    with _registry_lock:
        current = set()
        if os.path.isfile(reg_path):
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        current.update(data)
                    elif isinstance(data, dict):
                        current.update(data.get("completed_keys", []))
            except Exception:
                pass
        if key not in current:
            current.add(key)
            try:
                with open(reg_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(list(current)), f, indent=2)
            except Exception:
                pass


def remove_removal_item_completed(csv_path: str, student_id: str, link: str = ""):
    """Atomically remove an item from the persistent registry if it failed."""
    key = make_removal_key(student_id, link)
    reg_path = _get_registry_path(csv_path)
    with _registry_lock:
        if os.path.isfile(reg_path):
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                current = set(data if isinstance(data, list) else data.get("completed_keys", []))
                modified = False
                if key in current:
                    current.discard(key)
                    modified = True
                if student_id in current:
                    current.discard(student_id)
                    modified = True
                if modified:
                    with open(reg_path, "w", encoding="utf-8") as f:
                        json.dump(sorted(list(current)), f, indent=2)
            except Exception:
                pass


def _update_master_csv(master_path: str, header: list, student_id: str, link: str, new_row: list):
    """Upsert *new_row* into the cumulative master execution_results.csv."""
    sid_col = header.index("StudentId")
    lnk_col = header.index("Link")
    rows: list[list] = []
    if os.path.isfile(master_path):
        try:
            with open(master_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                file_header = next(reader, None)
                if file_header == header:
                    rows = list(reader)
        except Exception:
            rows = []

    updated = False
    for i, r in enumerate(rows):
        try:
            if r[sid_col] == str(student_id) and r[lnk_col] == str(link):
                rows[i] = new_row
                updated = True
                break
        except IndexError:
            continue

    if not updated:
        rows.append(new_row)

    try:
        with open(master_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
    except Exception:
        pass


class StudentRemover:
    """Automates removal of students from class lists in PCR Educator / portals
    and persists changes by saving the class form.
    """

    _csv_lock: threading.Lock = _csv_write_lock
    _run_id: str = ""

    def __init__(self, log=None, on_progress=None, abort_flag=None):
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda d: None)
        self._abort = abort_flag or (lambda: False)
        self._csv_lock = _csv_write_lock

    def run(
        self,
        config: dict,
        csv_path: str,
        devtools: bool = False,
        _module_id_override: str | None = None,
        on_progress=None,
        abort_flag=None,
        log=None,
        session_inputs: dict | None = None,
        skip_completed: bool = True,
        canonical_csv_path: str | None = None,
    ) -> None:
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda d: None)
        self._abort = abort_flag or (lambda: False)

        run_start = time.time()
        self._run_id = getattr(self, '_run_id', None) or new_run_id()
        success_count = 0

        target_csv = canonical_csv_path or csv_path
        df = pd.read_csv(csv_path)
        total_count = len(df)
        completed_count = 0

        self._on_progress({'type': 'total', 'total': total_count})

        # --- SESSION PERSISTENCE & COMPLETED CHECK ---
        active_module_id = _module_id_override or MODULE_ID
        session_inputs = session_inputs or {
            'csv_path': os.path.normpath(target_csv).replace("\\", "/"),
            'login_url': config.get('url', ''),
        }
        session_data = SessionManager.start_session(active_module_id, session_inputs, total_items=total_count)

        completed_keys = set()
        if skip_completed:
            completed_keys = get_completed_removal_items(target_csv, log_func=self._log)
            for item in session_data.get('completed_item_ids', []):
                completed_keys.add(str(item).strip())
            for item in session_data.get('failed_item_ids', []):
                completed_keys.discard(str(item).strip())
            if completed_keys:
                self._log('info', f"[SESSION] Resuming with {len(completed_keys)} previously completed entries.")

        login_url = config.get("url", "")
        if not login_url.startswith("http"):
            login_url = "https://" + login_url
        domain_key = learning_engine.extract_domain(login_url)

        effective_devtools = learning_engine.resolve_devtools(devtools, domain_key, log_func=self._log)
        headless = settings.get("headless", False)
        if headless:
            self._log('info', "[SETTINGS] Headless mode enabled -- Chrome will run with no visible window.")

        options = build_basic_options(devtools=effective_devtools, headless=headless)
        driver = None

        try:
            learned_timeout = learning_engine.get_optimal_timeout(login_url, 'page_load', default_timeout=15.0)
            wait = None

            def _nav_with_retry(target_url, max_attempts=3, delay=3.0):
                for attempt in range(1, max_attempts + 1):
                    try:
                        driver.get(target_url)
                        return
                    except TimeoutException as te:
                        try:
                            driver.execute_script("window.stop();")
                        except Exception:
                            pass
                        try:
                            curr = driver.current_url or ""
                            if curr and "about:blank" not in curr:
                                self._log('info', f"Page load hit timeout, but DOM interactive ({curr}). Proceeding.")
                                return
                        except Exception:
                            pass
                        if attempt < max_attempts and not self._abort():
                            self._log('warning', f"Navigation to {target_url} timed out (attempt {attempt}/{max_attempts}): {te}. Retrying...")
                            time.sleep(delay)
                        else:
                            raise
                    except Exception as e:
                        if is_dead_session_error(e):
                            raise BrowserSessionError(str(e)) from e
                        err_str = str(e).lower()
                        is_transient = any(k in err_str for k in (
                            "err_name_not_resolved", "net::err_", "timeout", "timed out",
                            "connection reset", "connection refused", "read timeout"
                        ))
                        if is_transient and attempt < max_attempts and not self._abort():
                            self._log('warning', f"Navigation transient error (attempt {attempt}/{max_attempts}): {e}. Retrying...")
                            time.sleep(delay)
                        else:
                            raise

            def _restart_driver(max_attempts=3):
                nonlocal driver, wait
                self._log('warning', "[RECOVERY] Chrome session lost or crashed. Relaunching browser...")
                for attempt in range(1, max_attempts + 1):
                    if self._abort():
                        return False
                    safe_quit_driver(driver, log_func=lambda m: self._log('debug', m))
                    driver = None
                    backoff = attempt * 2.5
                    time.sleep(backoff)
                    try:
                        driver = launch_chrome_driver(options, log_func=lambda m: self._log('info', m))
                        wait = WebDriverWait(driver, learned_timeout)
                        self._log('info', f"[RECOVERY] (Attempt {attempt}/{max_attempts}) Re-authenticating at {login_url}...")
                        _nav_with_retry(login_url, max_attempts=3, delay=3.0)
                        self._perform_login(driver, wait, config, login_url)
                        self._log('info', f"[RECOVERY] Browser restarted and logged in successfully.")
                        return True
                    except Exception as ex:
                        self._log('warning', f"[RECOVERY] Attempt {attempt}/{max_attempts} failed: {ex}")
                        if attempt == max_attempts:
                            self._log('error', f"[RECOVERY] All attempts to restore browser failed: {ex}")
                            return False
                return False

            # Launch driver
            for launch_attempt in range(1, 4):
                try:
                    driver = launch_chrome_driver(options, log_func=lambda m: self._log('info', m))
                    wait = WebDriverWait(driver, learned_timeout)
                    break
                except Exception as le:
                    if launch_attempt < 3 and not self._abort():
                        self._log('warning', f"Chrome driver launch failed (attempt {launch_attempt}/3): {le}. Retrying...")
                        time.sleep(launch_attempt * 2)
                    else:
                        raise

            # Step 1: Login
            login_success = False
            for login_attempt in range(1, 4):
                if self._abort():
                    return
                try:
                    if login_attempt > 1:
                        self._log('info', f"[RETRY] Re-attempting browser launch & login (attempt {login_attempt}/3)...")
                        if not _restart_driver():
                            continue
                    else:
                        self._log('info', f"Navigating to login page: {login_url}")
                        t0 = time.time()
                        _nav_with_retry(login_url, max_attempts=3, delay=3.0)
                        learning_engine.record_latency(login_url, 'page_load', time.time() - t0)

                        self._log('info', "Entering login credentials...")
                        self._perform_login(driver, wait, config, login_url)

                    self._log('info', "Login submitted successfully.")
                    login_success = True
                    break
                except Exception as login_err:
                    self._log('warning', f"Login attempt {login_attempt}/3 hit error: {login_err}")
                    if is_dead_session_error(login_err):
                        safe_quit_driver(driver)
                        driver = None
                    if login_attempt == 3 or self._abort():
                        raise
                    time.sleep(2.5)

            if not login_success:
                raise RuntimeError("Failed to log in after 3 attempts.")

            # Identify Student ID and Link column headers flexibly
            student_col = None
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("studentid", "id", "student", "recordid"):
                    student_col = col
                    break
            if not student_col:
                student_col = "studentId" if "studentId" in df.columns else df.columns[0]

            link_col = None
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("link", "url", "studentlink", "classlink", "pageurl"):
                    link_col = col
                    break
            if not link_col:
                link_col = "link" if "link" in df.columns else (df.columns[1] if len(df.columns) > 1 else df.columns[0])

            self._log('info', f"Using columns: Student ID -> '{student_col}', Link -> '{link_col}'")

            # Optimization: Group/sort rows by link so students sharing the same class
            # are processed together without reloading the class page every time!
            # We preserve an original index tracker so progress and logging stay consistent.
            df_with_meta = df.copy()
            df_with_meta['_orig_idx'] = df.index

            # Preserve relative order while grouping by link
            grouped_indices = []
            for _, grp in df_with_meta.groupby(link_col, sort=False):
                grouped_indices.extend(grp['_orig_idx'].tolist())

            df_ordered = df_with_meta.iloc[grouped_indices].reset_index(drop=True)

            # Step 2: Process Rows
            for _, row in df_ordered.iterrows():
                orig_index = int(row['_orig_idx'])
                if self._abort():
                    self._log('warning', "Automation aborted before completing all entries.")
                    break

                student_id = str(row[student_col]).strip()
                link = str(row[link_col]).strip().rstrip("/")
                item_key = make_removal_key(student_id, link)

                # Skip already completed
                if skip_completed and (item_key in completed_keys or (not link and student_id in completed_keys)):
                    self._log('info', f"[SESSION] Skipping {student_id} — already completed in a prior run.")
                    completed_count += 1
                    self._log_csv_status(
                        target_csv, student_id, link,
                        "SKIPPED", "Already completed in a prior run."
                    )
                    self._on_progress({
                        'type': 'item_done',
                        'completed': completed_count,
                        'students_traversed': completed_count,
                        'total_students': total_count,
                        'total': total_count,
                        'student_id': student_id,
                        'success': True,
                        'skipped': True,
                    })
                    continue

                self._on_progress({
                    'type': 'item_start',
                    'idx': orig_index,
                    'student_id': student_id,
                    'students_traversed': completed_count,
                    'total_students': total_count,
                    'total': total_count,
                })
                self._log('info', f"--- [Student {completed_count + 1}/{total_count}] Student ID: {student_id} ---")

                def _process_student_removal():
                    # Optimization: Skip full page navigation if already on this class link
                    need_nav = True
                    try:
                        curr_url = (driver.current_url or "").rstrip('#').rstrip('/')
                        target_url = link.rstrip('#').rstrip('/')
                        if curr_url == target_url or curr_url.startswith(target_url):
                            need_nav = False
                    except Exception:
                        need_nav = True

                    if need_nav:
                        t_nav = time.time()
                        _nav_with_retry(link, max_attempts=3, delay=2.0)
                        learning_engine.record_latency(link, 'page_load', time.time() - t_nav)

                    # Session Expiry Guard
                    if is_portal_session_expired(driver):
                        raise BrowserSessionError(
                            f"Portal session expired (redirected to login) while navigating to student {student_id}."
                        )

                    # Learning list insight
                    known_list = learning_engine.get_student_insight(student_id, 'list_category')
                    all_lists = [
                        ("Regular",   config.get("regular_student_list_xpath", ""),   config.get("remove_regular_xpath", "")),
                        ("Waiting",   config.get("waiting_student_list_xpath", ""),   config.get("remove_waiting_xpath", "")),
                        ("Withdrawn", config.get("withdrawn_student_list_xpath", ""), config.get("remove_withdrawn_xpath", "")),
                    ]

                    if known_list:
                        self._log('info', f"[LEARNING] Student {student_id} known to be in '{known_list}' list — checking there first.")
                        ordered_lists = sorted(all_lists, key=lambda x: 0 if x[0] == known_list else 1)
                    else:
                        ordered_lists = all_lists

                    target_list_xpath = None
                    target_remove_xpath = None
                    category_found = None

                    # Click Students tab if defined or present
                    student_tab_xpath = config.get("student_xpath", "")
                    if student_tab_xpath:
                        safe_click(driver, wait, student_tab_xpath, "Students tab", log_func=self._log)
                    else:
                        # Fallback heuristic: check if a Students tab exists
                        try:
                            st_elems = driver.find_elements(By.XPATH, "//a[contains(translate(., 'STUDENT', 'student'), 'student')] | //button[contains(translate(., 'STUDENT', 'student'), 'student')]")
                            for el in st_elems:
                                if el.is_displayed() and "student" in (el.text or "").lower():
                                    el.click()
                                    time.sleep(0.3)
                                    break
                        except Exception:
                            pass

                    # Poll for student ID in list categories
                    sid_str = str(student_id).strip()
                    poll_timeout = max(10.0, float(settings.get("max_action_wait_seconds", 20)))
                    _tab_poll_end = time.time() + poll_timeout
                    _tab_reclicked = False

                    while time.time() < _tab_poll_end and not category_found:
                        for cat, list_xpath, rem_xp in ordered_lists:
                            if not list_xpath:
                                continue
                            try:
                                _lx_elems = driver.find_elements(By.XPATH, list_xpath)
                                if not _lx_elems:
                                    continue

                                # 1. Fast text check
                                _combined_text = ' '.join(
                                    (el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or '')
                                    for el in _lx_elems
                                )
                                if sid_str in _combined_text:
                                    category_found = cat
                                    target_list_xpath = list_xpath
                                    target_remove_xpath = rem_xp
                                    break

                                # 2. Scoped XPath check
                                _row_matches = driver.find_elements(
                                    By.XPATH,
                                    f"{list_xpath}//*[contains(normalize-space(.), '{sid_str}')]"
                                )
                                if _row_matches:
                                    category_found = cat
                                    target_list_xpath = list_xpath
                                    target_remove_xpath = rem_xp
                                    break
                            except Exception:
                                pass

                        if not category_found:
                            if not _tab_reclicked and time.time() > (_tab_poll_end - poll_timeout + 3.5) and student_tab_xpath:
                                try:
                                    safe_click(driver, wait, student_tab_xpath, "Students tab (retry)", log_func=self._log)
                                    _tab_reclicked = True
                                except Exception:
                                    pass
                            time.sleep(0.15)

                    # 3. Page-wide fallback
                    if not category_found:
                        try:
                            _page_matches = driver.find_elements(
                                By.XPATH,
                                f"//*[contains(normalize-space(.), '{sid_str}') and not(.//*[contains(normalize-space(.), '{sid_str}')])]"
                            )
                            if _page_matches:
                                self._log('info', f"[FALLBACK] Student {sid_str} found on page — proceeding with 'Regular' list.")
                                category_found = "Regular"
                                target_list_xpath = config.get("regular_student_list_xpath", "")
                                target_remove_xpath = config.get("remove_regular_xpath", "")
                        except Exception:
                            pass

                    # If student is not found anywhere in the class
                    if not category_found:
                        # Check if class page actually loaded
                        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
                        if "error" in body_text and "not authorized" in body_text:
                            raise Exception(f"Failed to access class page {link}: Not authorized or error page.")
                        
                        # Student is already absent from the class lists
                        self._log('info', f"Student ID {student_id} is not present in class lists (already removed or not enrolled).")
                        self._log_csv_status(target_csv, student_id, link, "SUCCESS", "Student not found in list (already removed or not enrolled).")
                        mark_removal_item_completed(target_csv, student_id, link)
                        return

                    self._log('info', f"Located student {student_id} in '{category_found}' list.")
                    row_elem = select_student_row(driver, wait, target_list_xpath, student_id, category_found, log_func=self._log)
                    learning_engine.record_student_insight(student_id, 'list_category', category_found)

                    # Click Remove button
                    remove_success = self._click_remove_student(
                        driver, wait, config, student_id, category_found,
                        target_remove_xpath=target_remove_xpath, row_elem=row_elem
                    )

                    if not remove_success:
                        raise Exception(f"Failed to trigger Remove button for student {student_id}.")

                    self._log('info', f"Triggered Remove action for Student ID {student_id}.")

                    # Save the class form to commit removal
                    save_ok = self._save_class(driver, wait, config, student_id=student_id)
                    if not save_ok:
                        self._log('warning', f"Save Class button not confirmed for Student ID {student_id}. Changes might not persist.")

                    # Post-save removal verification: ensure student was genuinely removed
                    time.sleep(1.0)
                    student_still_present = False
                    for check_cat, chk_list_xp, _ in all_lists:
                        if not chk_list_xp:
                            continue
                        try:
                            found_elems = driver.find_elements(By.XPATH, chk_list_xp)
                            text_dump = ' '.join(
                                (el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or '')
                                for el in found_elems
                            )
                            if sid_str in text_dump:
                                detail_elems = driver.find_elements(
                                    By.XPATH,
                                    f"{chk_list_xp}//*[contains(normalize-space(.), '{sid_str}') and not(.//*[contains(normalize-space(.), '{sid_str}')])]"
                                )
                                for de in detail_elems:
                                    if de.tag_name.lower() == 'option' or de.is_displayed():
                                        student_still_present = True
                                        self._log('error', f"Removal verification failed: Student ID {student_id} was still detected in '{check_cat}' list after save!")
                                        break
                                if student_still_present:
                                    break
                        except Exception:
                            pass

                    if student_still_present:
                        raise Exception(f"Removal verification failed: Student ID {student_id} was not removed from class (still visible in portal after Save).")

                    self._log('info', f"Successfully removed student {student_id} from '{category_found}' list and verified removal.")
                    self._log_csv_status(
                        target_csv, student_id, link, "SUCCESS",
                        f"Removed student from '{category_found}' list and verified removal."
                    )
                    mark_removal_item_completed(target_csv, student_id, link)

                # Retry logic for student processing
                row_ok, _, row_err = process_item_with_retry(
                    student_id, _process_student_removal, active_module_id,
                    log_func=self._log, abort_flag=self._abort, track_session=False,
                )

                if not row_ok and isinstance(row_err, (BrowserSessionError, TimeoutException)):
                    if not self._abort():
                        self._log('warning', f"[RECOVERY] Session error during student {student_id}: {row_err}. Restarting browser...")
                        if _restart_driver():
                            row_ok, _, row_err = process_item_with_retry(
                                student_id, _process_student_removal, active_module_id,
                                log_func=self._log, abort_flag=self._abort, track_session=False,
                            )

                completed_count += 1
                if row_ok:
                    success_count += 1
                    self._on_progress({
                        'type': 'item_done',
                        'completed': completed_count,
                        'students_traversed': completed_count,
                        'total_students': total_count,
                        'total': total_count,
                        'student_id': student_id,
                        'success': True,
                    })
                else:
                    self._log('error', f"Failed to remove student {student_id}: {row_err}")
                    self._log_csv_status(target_csv, student_id, link, "FAILED", str(row_err))
                    remove_removal_item_completed(target_csv, student_id, link)
                    self._on_progress({
                        'type': 'item_done',
                        'completed': completed_count,
                        'students_traversed': completed_count,
                        'total_students': total_count,
                        'total': total_count,
                        'student_id': student_id,
                        'success': False,
                    })

        finally:
            safe_quit_driver(driver, log_func=lambda m: self._log('debug', m))

        duration = time.time() - run_start
        self._log('info', f"Student Removal completed in {duration:.1f}s. Success: {success_count}/{total_count}")
        learning_engine.record_run_summary(
            module_name="Student Removal",
            total_items=total_count,
            success_count=success_count,
            total_files=0,
            duration_sec=duration,
        )

    def run_parallel(
        self,
        config: dict,
        csv_path: str,
        n_workers: int = 1,
        devtools: bool = False,
        on_progress=None,
        abort_flag=None,
        log=None,
        session_inputs: dict | None = None,
        skip_completed: bool = True,
    ) -> None:
        """Run student removal with *n_workers* independent Chrome instances
        working in parallel, each handling a separate chunk of the CSV.
        """
        _log  = log          or (lambda l, m: None)
        _prog = on_progress  or (lambda d: None)
        _abort = abort_flag  or (lambda: False)

        n_workers = max(1, n_workers)
        run_start = time.time()

        df_full = pd.read_csv(csv_path)
        total_count = len(df_full)

        if total_count == 0:
            _log('warning', "CSV is empty — nothing to process.")
            return

        student_col = None
        for col in df_full.columns:
            clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
            if clean_c in ("studentid", "id", "student", "recordid"):
                student_col = col
                break
        if not student_col:
            student_col = "studentId" if "studentId" in df_full.columns else df_full.columns[0]

        link_col = None
        for col in df_full.columns:
            clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
            if clean_c in ("link", "url", "studentlink", "classlink", "pageurl"):
                link_col = col
                break
        if not link_col:
            link_col = "link" if "link" in df_full.columns else (df_full.columns[1] if len(df_full.columns) > 1 else df_full.columns[0])

        already_completed_count = 0
        df_pending = df_full

        if skip_completed:
            completed_keys = get_completed_removal_items(csv_path, log_func=_log)
            if completed_keys:
                def _row_is_done(r):
                    sid = str(r[student_col]).strip()
                    lnk = str(r[link_col]).strip().rstrip("/")
                    k = make_removal_key(sid, lnk)
                    return k in completed_keys or (not lnk and sid in completed_keys)

                mask_done = df_full.apply(_row_is_done, axis=1)
                already_completed_count = int(mask_done.sum())

                if already_completed_count >= total_count:
                    _log('info', f"[SESSION] All {total_count} rows in this CSV have already been successfully processed! Nothing to do.")
                    _prog({'type': 'total', 'total': total_count})
                    _prog({
                        'type': 'item_done',
                        'completed': total_count,
                        'students_traversed': total_count,
                        'total_students': total_count,
                        'total': total_count,
                        'success': True,
                        'skipped': True,
                    })
                    return

                if already_completed_count > 0:
                    df_pending = df_full[~mask_done].reset_index(drop=True)
                    _log('info', f"[SESSION] Found {already_completed_count} of {total_count} rows already completed. "
                                 f"Distributing remaining {len(df_pending)} unsubmitted rows across workers...")

        pending_count = len(df_pending)
        n_workers = min(n_workers, pending_count)
        chunk_size = math.ceil(pending_count / n_workers)
        chunks = [
            df_pending.iloc[i : i + chunk_size].reset_index(drop=True)
            for i in range(0, pending_count, chunk_size)
        ]
        actual_workers = len(chunks)

        _log('info',
             f"[PARALLEL] Starting {actual_workers} worker(s) — "
             f"{pending_count} pending rows split into chunks of ~{chunk_size} (Total CSV: {total_count}).")
        _prog({'type': 'total', 'total': total_count})
        if already_completed_count > 0:
            _prog({
                'type': 'item_done',
                'completed': already_completed_count,
                'students_traversed': already_completed_count,
                'total_students': total_count,
                'total': total_count,
                'success': True,
                'skipped': True,
            })

        _counter_lock = threading.Lock()
        _shared = {'completed': already_completed_count, 'success': already_completed_count}
        shared_run_id = new_run_id()

        def _worker(worker_idx: int, chunk_df: pd.DataFrame):
            worker_module_id = f"{MODULE_ID}_w{worker_idx}"
            remover = StudentRemover()
            remover._run_id = shared_run_id
            remover._csv_lock = _csv_write_lock

            def worker_log(lvl, msg):
                _log(lvl, f"[W{worker_idx}] {msg}")

            def worker_progress(data: dict):
                p_type = data.get('type')
                if p_type == 'item_done':
                    with _counter_lock:
                        _shared['completed'] += 1
                        _shared['success'] += int(data.get('success', 1))
                        merged = dict(data)
                        merged['completed'] = _shared['completed']
                        merged['students_traversed'] = _shared['completed']
                        merged['total_students'] = total_count
                        merged['total'] = total_count
                    _prog(merged)
                elif p_type == 'item_start':
                    merged = dict(data)
                    with _counter_lock:
                        merged['students_traversed'] = _shared['completed']
                        merged['total_students'] = total_count
                        merged['total'] = total_count
                    _prog(merged)
                else:
                    _prog(data)

            chunk_csv = csv_path.replace('.csv', f'_chunk_w{worker_idx}_{shared_run_id}.csv')
            try:
                chunk_df.to_csv(chunk_csv, index=False)
                remover.run(
                    config=config,
                    csv_path=chunk_csv,
                    devtools=devtools,
                    on_progress=worker_progress,
                    abort_flag=_abort,
                    log=worker_log,
                    session_inputs=session_inputs,
                    skip_completed=False,
                    canonical_csv_path=csv_path,
                    _module_id_override=worker_module_id,
                )
            except Exception as w_err:
                worker_log('error', f"Worker encountered an unhandled error: {w_err}")
            finally:
                try:
                    if os.path.isfile(chunk_csv):
                        os.remove(chunk_csv)
                except Exception:
                    pass

        threads = [
            threading.Thread(
                target=_worker,
                args=(idx, chunk),
                name=f"StudentRemovalWorker-{idx}",
                daemon=True,
            )
            for idx, chunk in enumerate(chunks)
        ]

        for idx, t in enumerate(threads):
            if _abort():
                break
            t.start()
            if idx < len(threads) - 1:
                time.sleep(0.4)

        for t in threads:
            t.join()

        duration = time.time() - run_start
        total_success = _shared['success']
        _log('info',
             f"[PARALLEL] All {actual_workers} worker(s) finished. "
             f"Duration: {duration:.1f}s | "
             f"Success: {total_success}/{total_count}")

        learning_engine.record_run_summary(
            module_name="Student Removal (Parallel)",
            total_items=total_count,
            success_count=total_success,
            total_files=0,
            duration_sec=duration,
        )

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _perform_login(self, driver, wait, config: dict, login_url: str):
        learned_user = learning_engine.get_learned_xpaths(login_url, 'username_field')
        learned_pass = learning_engine.get_learned_xpaths(login_url, 'password_field')
        learned_btn  = learning_engine.get_learned_xpaths(login_url, 'login_button')

        user_xpath = (learned_user[0] if learned_user else None) or config.get("user_xpath", "")
        pass_xpath = (learned_pass[0] if learned_pass else None) or config.get("password_xpath", "")
        btn_xpath  = (learned_btn[0]  if learned_btn  else None) or config.get("login_xpath", "") or config.get("login_btn_xpath", "")

        try:
            wait.until(EC.element_to_be_clickable((By.XPATH, user_xpath))).send_keys(config["user"])
            wait.until(EC.element_to_be_clickable((By.XPATH, pass_xpath))).send_keys(config["password"])

            if config.get("role_xpath") and config.get("role"):
                select_elem = Select(wait.until(EC.presence_of_element_located((By.XPATH, config["role_xpath"]))))
                select_elem.select_by_visible_text(config["role"])

            login_url_before = driver.current_url
            try:
                safe_click(driver, wait, btn_xpath, "Login button", log_func=self._log)
            except TimeoutException:
                try:
                    driver.execute_script("window.stop();")
                except Exception:
                    pass
            try:
                WebDriverWait(driver, 10).until(lambda d: d.current_url != login_url_before)
            except TimeoutException:
                pass

            learning_engine.record_successful_xpath(login_url, 'username_field', user_xpath)
            learning_engine.record_successful_xpath(login_url, 'password_field', pass_xpath)
            learning_engine.record_successful_xpath(login_url, 'login_button',   btn_xpath)
        except Exception:
            learning_engine.record_failed_xpath(login_url, 'username_field', user_xpath)
            raise

    # ------------------------------------------------------------------
    # Remove Student Action
    # ------------------------------------------------------------------

    def _click_remove_student(
        self,
        driver,
        wait,
        config: dict,
        student_id: str,
        category: str = "Regular",
        target_remove_xpath: str | None = None,
        row_elem=None,
    ) -> bool:
        """Trigger the Remove / Delete action for the selected student.

        Order of attempts:
          1. Category-specific XPath from config or target_remove_xpath (e.g. remove_regular_xpath).
          2. Explicit general remove XPath from credentials (remove_student_xpath / remove_xpath / delete_xpath).
          3. Learned XPath for domain from learning engine.
          4. In-row actions (e.g. trash icon, delete link/button inside row_elem).
          5. Semantic candidate search for 'Remove', 'Delete', 'Drop' buttons or inputs.
          6. In-browser JS query to locate and trigger the Remove element.
          7. Automatic confirmation of browser alert / modal dialog.
        """
        domain_key = ""
        try:
            curr_url = driver.current_url or config.get("url", "")
            domain_key = learning_engine.extract_domain(curr_url)
        except Exception:
            pass

        learned_xpaths = learning_engine.get_learned_xpaths(domain_key, 'remove_student_button') if domain_key else []

        candidate_xpaths = []
        if target_remove_xpath:
            candidate_xpaths.append(target_remove_xpath)

        config_remove = (
            config.get("remove_student_xpath")
            or config.get("remove_xpath")
            or config.get("delete_student_xpath")
            or config.get("delete_xpath")
            or config.get(f"remove_{category.lower()}_xpath")
        )
        if config_remove:
            candidate_xpaths.append(config_remove)

        candidate_xpaths.extend(learned_xpaths)

        # 4. In-row remove/delete icons or buttons if row_elem is supplied
        if row_elem:
            try:
                in_row_elems = row_elem.find_elements(
                    By.XPATH,
                    ".//a[contains(translate(@title, 'REMOVE', 'remove'), 'remove') or contains(translate(@title, 'DELETE', 'delete'), 'delete') or contains(@class, 'delete') or contains(@class, 'remove') or contains(@class, 'trash')] "
                    "| .//button[contains(translate(@title, 'REMOVE', 'remove'), 'remove') or contains(translate(@title, 'DELETE', 'delete'), 'delete') or contains(translate(., 'REMOVE', 'remove'), 'remove') or contains(translate(., 'DELETE', 'delete'), 'delete') or contains(@class, 'trash')] "
                    "| .//input[(@type='button' or @type='submit') and (contains(translate(@value, 'REMOVE', 'remove'), 'remove') or contains(translate(@value, 'DELETE', 'delete'), 'delete'))] "
                    "| .//*[contains(@class, 'fa-trash') or contains(@class, 'icon-trash') or contains(@class, 'btn-delete') or contains(@class, 'btn-remove')]"
                )
                for el in in_row_elems:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.1)
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        self._log('info', f"Clicked in-row remove button for student {student_id}.")
                        self._handle_confirmation_dialog(driver)
                        return True
            except Exception:
                pass

        # 5. Semantic candidate XPaths on toolbar / page
        candidate_xpaths.extend([
            "//input[(@type='button' or @type='submit') and (translate(normalize-space(@value), 'REMOVE', 'remove')='remove' or contains(translate(@value, 'REMOVE', 'remove'), 'remove student') or translate(normalize-space(@value), 'DELETE', 'delete')='delete' or contains(translate(@value, 'DELETE', 'delete'), 'delete student') or contains(translate(@value, 'DROP', 'drop'), 'drop'))]",
            "//button[translate(normalize-space(.), 'REMOVE', 'remove')='remove' or contains(translate(normalize-space(.), 'REMOVE', 'remove'), 'remove student') or translate(normalize-space(.), 'DELETE', 'delete')='delete' or contains(translate(normalize-space(.), 'DELETE', 'delete'), 'delete student') or contains(translate(normalize-space(.), 'DROP', 'drop'), 'drop')]",
            "//a[(contains(@class, 'btn') or contains(@class, 'button') or @role='button') and (contains(translate(normalize-space(.), 'REMOVE', 'remove'), 'remove') or contains(translate(normalize-space(.), 'DELETE', 'delete'), 'delete') or contains(translate(normalize-space(.), 'DROP', 'drop'), 'drop'))]",
            "//input[contains(translate(@value, 'REMOVE', 'remove'), 'remove') and not(@type='hidden')]",
            "//button[contains(translate(normalize-space(.), 'REMOVE', 'remove'), 'remove')]",
            "//*[@role='button'][contains(translate(normalize-space(.), 'REMOVE', 'remove'), 'remove')]",
        ])

        seen = set()
        unique_candidates = [xp for xp in candidate_xpaths if xp and not (xp in seen or seen.add(xp))]

        _end_time = time.time() + 6.0
        while time.time() < _end_time:
            for xp in unique_candidates:
                try:
                    elems = driver.find_elements(By.XPATH, xp)
                    for el in elems:
                        tag_name = (el.tag_name or '').lower()
                        val = el.get_attribute("value") or el.text or el.get_attribute("title") or el.get_attribute("aria-label") or ""
                        btn_type = el.get_attribute("type") or ""
                        is_disabled = (not el.is_enabled()) or (el.get_attribute("disabled") is not None)

                        if not el.is_displayed():
                            try:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                                time.sleep(0.1)
                            except Exception:
                                pass

                        if el.is_displayed() or el.is_enabled():
                            if is_disabled:
                                self._log('warning', f"Remove button ({tag_name} value='{val}' type='{btn_type}') is DISABLED. Waiting for selection state...")
                                time.sleep(0.6)
                                is_disabled = (not el.is_enabled()) or (el.get_attribute("disabled") is not None)

                            try:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                                time.sleep(0.1)
                            except Exception:
                                pass

                            clicked = False
                            try:
                                el.click()
                                clicked = True
                            except Exception:
                                pass

                            if not clicked or is_disabled:
                                try:
                                    driver.execute_script("""
                                        arguments[0].disabled = false;
                                        arguments[0].click();
                                        if (arguments[0].onclick) { try { arguments[0].onclick(); } catch(e){} }
                                    """, el)
                                    clicked = True
                                except Exception:
                                    pass

                            self._log('info', f"Clicked Remove button ({tag_name} value='{val}' type='{btn_type}') using '{xp}' for Student ID {student_id}.")
                            if domain_key:
                                learning_engine.record_successful_xpath(domain_key, 'remove_student_button', xp)
                            self._handle_confirmation_dialog(driver)
                            return True
                except Exception:
                    continue

            # 6. JavaScript DOM search fallback
            try:
                js_click_ok = driver.execute_script("""
                    var candidates = document.querySelectorAll('input[type="submit"], input[type="button"], button, a.btn, a[role="button"]');
                    for (var i = 0; i < candidates.length; i++) {
                        var el = candidates[i];
                        var val = (el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().toLowerCase();
                        if (val === 'remove' || val.indexOf('remove student') !== -1 || val === 'delete' || val.indexOf('delete student') !== -1 || val.indexOf('drop') !== -1) {
                            el.scrollIntoView({block: 'center'});
                            try { el.click(); } catch(e) { if (el.onclick) el.onclick(); }
                            return true;
                        }
                    }
                    return false;
                """)
                if js_click_ok:
                    self._log('info', f"Clicked Remove button via dynamic DOM inspection for Student ID {student_id}.")
                    self._handle_confirmation_dialog(driver)
                    return True
            except Exception:
                pass

            time.sleep(0.3)

        self._log('warning', f"Could not locate Remove button for Student ID {student_id}.")
        return False

    def _handle_confirmation_dialog(self, driver):
        """Accept any confirmation alert or dialog popup after clicking Remove."""
        for _ in range(8):
            time.sleep(0.3)
            # 1. Browser native alert (window.confirm / window.alert)
            try:
                alert = driver.switch_to.alert
                a_text = (alert.text or "").strip()
                if any(kw in a_text.lower() for kw in ("select a student", "no student selected", "must select", "please select")):
                    self._log('warning', f"Portal alert indicated selection issue: '{a_text}'")
                    alert.accept()
                    raise Exception(f"Portal rejected removal: '{a_text}' — student was not recognized as selected.")
                alert.accept()
                self._log('info', f"Accepted browser confirmation alert: '{a_text}'")
                time.sleep(0.5)
                return
            except Exception as ae:
                if "Portal rejected removal" in str(ae):
                    raise
                pass

        # 2. DOM modal dialog confirmation (e.g. Bootstrap modal 'Yes' / 'OK' / 'Confirm')
        try:
            confirm_btns = driver.find_elements(
                By.XPATH,
                "//div[contains(@class, 'modal') or contains(@class, 'dialog') or @role='dialog' or contains(@class, 'popup') or contains(@id, 'dialog') or contains(@id, 'modal')]//button[translate(normalize-space(.), 'YES', 'yes')='yes' or translate(normalize-space(.), 'OK', 'ok')='ok' or contains(translate(., 'CONFIRM', 'confirm'), 'confirm') or contains(translate(., 'REMOVE', 'remove'), 'remove') or contains(translate(., 'DELETE', 'delete'), 'delete')] "
                "| //div[contains(@class, 'modal') or contains(@class, 'dialog') or @role='dialog' or contains(@class, 'popup') or contains(@id, 'dialog') or contains(@id, 'modal')]//input[(@type='button' or @type='submit') and (translate(normalize-space(@value), 'YES', 'yes')='yes' or translate(normalize-space(@value), 'OK', 'ok')='ok' or contains(translate(@value, 'CONFIRM', 'confirm'), 'confirm') or contains(translate(@value, 'REMOVE', 'remove'), 'remove') or contains(translate(@value, 'DELETE', 'delete'), 'delete'))] "
                "| //button[translate(normalize-space(.), 'OK', 'ok')='ok']"
            )
            for btn in confirm_btns:
                if btn.is_displayed():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    self._log('info', "Confirmed removal in modal dialog.")
                    time.sleep(0.5)
                    return
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Save Class Action
    # ------------------------------------------------------------------

    def _get_save_class_xpath(self, config: dict) -> str:
        return (
            config.get("save_class_xpath")
            or config.get("class_save_xpath")
            or config.get("save_xpath")
            or ""
        )

    def _save_class(self, driver, wait, config: dict, student_id: str = "") -> bool:
        """Save the class form so student removals persist."""
        user_xpath = self._get_save_class_xpath(config)

        domain_key = ""
        try:
            curr_url = driver.current_url or config.get("url", "")
            domain_key = learning_engine.extract_domain(curr_url)
        except Exception:
            pass

        learned_xpaths = learning_engine.get_learned_xpaths(domain_key, 'save_class_button') if domain_key else []

        candidate_xpaths = []
        if user_xpath:
            candidate_xpaths.append(user_xpath)
        candidate_xpaths.extend(learned_xpaths)

        candidate_xpaths.extend([
            "//input[(@type='submit' or @type='button') and (translate(normalize-space(@value), 'SAVE', 'save')='save' or contains(translate(@value, 'SAVE', 'save'), 'save class') or contains(translate(@value, 'SAVE', 'save'), 'save grade'))]",
            "//button[translate(normalize-space(.), 'SAVE', 'save')='save' or contains(translate(normalize-space(.), 'SAVE', 'save'), 'save class') or contains(translate(normalize-space(.), 'SAVE', 'save'), 'save grade')]",
            "//input[translate(normalize-space(@value), 'SAVE', 'save')='save' and not(@type='hidden')]",
            "//input[contains(translate(@value, 'SAVE', 'save'), 'save') and not(@type='hidden')]",
            "//button[contains(translate(normalize-space(.), 'SAVE', 'save'), 'save')]",
            "//a[(contains(@class, 'btn') or contains(@class, 'button') or @role='button') and contains(translate(normalize-space(.), 'SAVE', 'save'), 'save')]",
            "//*[@role='button'][translate(normalize-space(.), 'SAVE', 'save')='save']",
        ])

        seen = set()
        unique_candidates = [xp for xp in candidate_xpaths if xp and not (xp in seen or seen.add(xp))]

        _end_time = time.time() + 7.0
        while time.time() < _end_time:
            for xp in unique_candidates:
                try:
                    elems = driver.find_elements(By.XPATH, xp)
                    for el in elems:
                        if el.is_displayed() or el.is_enabled():
                            try:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                                time.sleep(0.1)
                            except Exception:
                                pass
                            try:
                                el.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", el)
                            self._log('info', f"Clicked Save Class button for Student ID {student_id or 'current'}.")
                            if domain_key:
                                learning_engine.record_successful_xpath(domain_key, 'save_class_button', xp)
                            time.sleep(0.8)
                            try:
                                alert = driver.switch_to.alert
                                a_text = alert.text
                                alert.accept()
                                self._log('info', f"Accepted browser alert after Save Class: '{a_text}'")
                            except Exception:
                                pass
                            return True
                except Exception:
                    continue

            # JavaScript fallback
            try:
                js_click_ok = driver.execute_script("""
                    var candidates = document.querySelectorAll('input[type="submit"], input[type="button"], button, a.btn, a[role="button"]');
                    for (var i = 0; i < candidates.length; i++) {
                        var el = candidates[i];
                        var val = (el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().toLowerCase();
                        if (val === 'save' || val.indexOf('save class') !== -1 || val.indexOf('save grade') !== -1 || val.indexOf('save changes') !== -1) {
                            el.scrollIntoView({block: 'center'});
                            try { el.click(); } catch(e) { if (el.onclick) el.onclick(); }
                            return true;
                        }
                    }
                    return false;
                """)
                if js_click_ok:
                    self._log('info', f"Clicked Save Class button via dynamic DOM inspection for Student ID {student_id or 'current'}.")
                    time.sleep(0.8)
                    try:
                        alert = driver.switch_to.alert
                        a_text = alert.text
                        alert.accept()
                        self._log('info', f"Accepted browser alert after Save Class: '{a_text}'")
                    except Exception:
                        pass
                    return True
            except Exception:
                pass

            time.sleep(0.3)

        self._log('warning', "Could not locate Save Class button dynamically. Changes may not persist if unsaved.")
        return False

    def _log_csv_status(self, csv_path: str, student_id: str, link: str, status: str, details: str):
        base_dir = os.path.dirname(csv_path)
        run_id = getattr(self, '_run_id', None) or new_run_id()
        csv_log_file = os.path.join(base_dir, f"execution_results_{run_id}.csv")
        master_log_file = os.path.join(base_dir, "execution_results.csv")
        lock = getattr(self, '_csv_lock', None) or _csv_write_lock
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [ts, student_id, link, status, details]
        header = ["Timestamp", "StudentId", "Link", "Status", "Details"]
        try:
            if lock:
                with lock:
                    file_exists = os.path.isfile(csv_log_file)
                    with open(csv_log_file, "a", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow(header)
                        writer.writerow(row)
                    _update_master_csv(master_log_file, header, student_id, link, row)
            else:
                file_exists = os.path.isfile(csv_log_file)
                with open(csv_log_file, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(header)
                    writer.writerow(row)
                _update_master_csv(master_log_file, header, student_id, link, row)
        except Exception as e:
            self._log('error', f"Error appending to CSV log: {e}")

    def verify_logs(self, csv_path: str, log=None) -> dict:
        _log = log or (lambda l, m: None)
        base_dir = os.path.dirname(csv_path)
        csv_log_file = find_latest_run_log(base_dir, "execution_results", ".csv")

        if not csv_log_file or not os.path.isfile(csv_log_file):
            return {'total': 0, 'processed': 0, 'missing': []}

        try:
            df_input = pd.read_csv(csv_path)
            df_logs  = pd.read_csv(csv_log_file)

            student_col = None
            for col in df_input.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("studentid", "id", "student", "recordid"):
                    student_col = col
                    break
            if not student_col:
                student_col = "studentId" if "studentId" in df_input.columns else df_input.columns[0]

            df_input["studentId"] = df_input[student_col].astype(str).str.strip()
            df_logs["StudentId"]  = df_logs["StudentId"].astype(str).str.strip()

            processed_ids = set(df_logs[df_logs["Status"] == "SUCCESS"]["StudentId"])
            all_ids       = set(df_input["studentId"])
            matched       = all_ids.intersection(processed_ids)
            missing       = list(all_ids - processed_ids)

            return {'total': len(all_ids), 'processed': len(matched), 'missing': missing}
        except Exception as e:
            _log('error', f"Failed to verify logs: {e}")
            return {'total': 0, 'processed': 0, 'missing': []}
