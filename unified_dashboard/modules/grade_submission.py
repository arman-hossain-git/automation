"""
modules/grade_submission.py
============================
Unified Business logic for Student Grade Submission.
Combines Adjust Score entry with dynamic detection of Adjust Score vs Grade field.

Learning Integration:
  - Remembers per-student which list they were in (Regular/Waiting/Withdrawn)
    so next run jumps straight to the right list without polling others.
  - Remembers per-student which score field type (Adjust Score vs Grade)
    so the resolution step is instant on subsequent runs.
  - Learns page-load latency per domain for dynamic wait timeouts.
  - Records run summary (duration, success count) for the Learning dashboard.
"""

import os
import csv
import time
import threading
import math
import glob
import json
import hashlib
import pandas as pd
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from core.chrome_driver import launch_chrome_driver, build_basic_options, safe_quit_driver
from core.settings_manager import settings
from core.selenium_helpers import (
    safe_click,
    safe_send_keys,
    select_student_row,
    safe_select_dropdown,
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

MODULE_ID = "grade_submission"

# Shared lock so multiple parallel workers can safely append to the same
# execution_results CSV without interleaving partial writes.
_csv_write_lock = threading.Lock()
_registry_lock = threading.Lock()


def make_submission_key(student_id: str, link: str = "") -> str:
    """Canonical unique key for a grade submission row.
    Combines student_id and link/classid so that a student
    taking multiple classes is tracked accurately per class.
    """
    sid = str(student_id).strip()
    lnk = str(link).strip().rstrip("/")
    if lnk:
        return f"{sid}::{lnk}"
    return sid


def _get_registry_path(csv_path: str, mode: str = "submit") -> str:
    norm_path = os.path.normcase(os.path.abspath(csv_path))
    h = hashlib.sha256(norm_path.encode("utf-8")).hexdigest()[:12]
    base = os.path.splitext(os.path.basename(csv_path))[0][:25]
    safe_base = "".join(c for c in base if c.isalnum() or c in ("-", "_"))
    return os.path.join(ensure_sessions_dir(), f"completed_{mode}_{safe_base}_{h}.json")


def get_completed_grade_items(csv_path: str, mode: str = "submit", log_func=None) -> set[str]:
    """Return a set of canonical keys (f"{student_id}::{link}") that have
    already been successfully submitted (or un-submitted) in prior runs.

    Aggregates from:
      1. Persistent JSON registry for this CSV and mode.
      2. All execution_results_*.csv files in os.path.dirname(csv_path).
         - Rows with latest status == 'SUCCESS' are added to completed keys.
         - Rows with latest status == 'FAILED' are EXPLICITLY REMOVED from
           completed keys and persistent registry so the program tries them again.
      3. Active SessionManager session for this CSV (if any), excluding failed items.
    """
    _log = log_func or (lambda l, m: None)
    completed_keys: set[str] = set()
    reg_path = _get_registry_path(csv_path, mode)

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
                _log('warning', f"Could not read completed registry {reg_path}: {e}")

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
                        is_unsubmit_row = ("un-submit" in details or "unsubmitted" in details)
                        if mode == "unsubmit" and not is_unsubmit_row:
                            continue
                        if mode == "submit" and is_unsubmit_row:
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

        # Chronologically sort all execution log entries
        log_entries.sort(key=lambda x: x[0])

        # Find the latest status for each item
        latest_status_by_key = {}
        for _, sid, lnk, status in log_entries:
            key = make_submission_key(sid, lnk)
            latest_status_by_key[key] = (status, sid)

        found_from_csvs = 0
        failed_retries = 0
        for key, (status, sid) in latest_status_by_key.items():
            if status == "SUCCESS":
                if key not in completed_keys:
                    completed_keys.add(key)
                    found_from_csvs += 1
            elif status == "FAILED":
                # Crucial: if latest execution status is FAILED, do NOT skip!
                if key in completed_keys or sid in completed_keys:
                    completed_keys.discard(key)
                    completed_keys.discard(sid)
                    failed_retries += 1

        if found_from_csvs > 0:
            _log('info', f"[HISTORY] Discovered {found_from_csvs} previously completed items from execution logs.")
        if failed_retries > 0:
            _log('info', f"[HISTORY] Identified {failed_retries} items marked FAILED in execution logs — scheduled for re-attempt.")

        with _registry_lock:
            try:
                with open(reg_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(list(completed_keys)), f, indent=2)
            except Exception:
                pass

    # 3. Also check SessionManager
    try:
        active_module = "unsubmit_grades" if mode == "unsubmit" else MODULE_ID
        session_data = SessionManager.get_unfinished_session(active_module)
        if session_data:
            for item in session_data.get("completed_item_ids", []):
                completed_keys.add(str(item).strip())
            # Ensure failed items from session are removed so they are tried again
            for item in session_data.get("failed_item_ids", []):
                completed_keys.discard(str(item).strip())
    except Exception:
        pass

    return completed_keys


def mark_grade_item_completed(csv_path: str, student_id: str, link: str = "", mode: str = "submit"):
    """Atomically record an item as successfully completed in the persistent registry."""
    key = make_submission_key(student_id, link)
    reg_path = _get_registry_path(csv_path, mode)
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


def remove_grade_item_completed(csv_path: str, student_id: str, link: str = "", mode: str = "submit"):
    """Atomically remove an item from the persistent registry if it failed."""
    key = make_submission_key(student_id, link)
    reg_path = _get_registry_path(csv_path, mode)
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
    """Upsert *new_row* into the cumulative master execution_results.csv.

    If a row for the same (StudentId, Link) pair already exists it is replaced
    with the latest result; otherwise the row is appended.  This means the
    master file always contains exactly one entry per student per class, always
    reflecting the most recent outcome.

    Called from within ``_log_csv_status`` which already holds the write lock.
    """
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
                # If the header doesn't match (e.g. old format) start fresh
        except Exception:
            rows = []

    # Replace existing row for this (StudentId, Link) or append
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


class GradeSubmitter:
    """Submits student final grades with automatic detection of Adjust Score vs Grade dropdown field."""

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
        mode: str = "submit",
        skip_completed: bool = True,
        canonical_csv_path: str | None = None,
    ) -> None:
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda d: None)
        self._abort = abort_flag or (lambda: False)

        run_start = time.time()
        # Per-run identifier baked into this run's execution results filename
        # so consecutive runs never append into the same file and stay easy
        # to tell apart in a folder listing.
        self._run_id = getattr(self, '_run_id', None) or new_run_id()
        success_count = 0

        target_csv = canonical_csv_path or csv_path
        df = pd.read_csv(csv_path)
        total_count = len(df)
        completed_count = 0

        self._on_progress({'type': 'total', 'total': total_count})

        # --- SESSION PERSISTENCE & COMPLETED CHECK ---
        if _module_id_override:
            active_module_id = _module_id_override
        else:
            active_module_id = "unsubmit_grades" if mode == "unsubmit" else MODULE_ID
        session_inputs = session_inputs or {
            'csv_path': os.path.normpath(target_csv).replace("\\", "/"),
            'login_url': config.get('url', ''),
            'mode': mode,
        }
        session_data = SessionManager.start_session(active_module_id, session_inputs, total_items=total_count)
        
        completed_keys = set()
        if skip_completed:
            completed_keys = get_completed_grade_items(target_csv, mode=mode, log_func=self._log)
            # Also merge SessionManager's stored items, excluding any that failed
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

        # --- LEARNING: auto-enable DevTools if this domain has confirmed it needs it,
        # even though the caller (Settings default) left it off. ---
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
                        # Page DOM might have loaded even though background assets are still loading
                        try:
                            driver.execute_script("window.stop();")
                        except Exception:
                            pass
                        try:
                            curr = driver.current_url or ""
                            if curr and "about:blank" not in curr:
                                self._log('info', f"Page load hit timeout waiting for all assets, but DOM is interactive ({curr}). Proceeding.")
                                return
                        except Exception:
                            pass
                        if attempt < max_attempts and not self._abort():
                            self._log('warning', f"Navigation to {target_url} timed out (attempt {attempt}/{max_attempts}): {te}. Retrying in {delay}s...")
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
                            self._log('warning', f"Navigation to {target_url} hit transient network error (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay}s...")
                            time.sleep(delay)
                        else:
                            raise

            def _restart_driver(max_attempts=3):
                nonlocal driver, wait
                self._log('warning', "[RECOVERY] Chrome session was lost or crashed. Relaunching browser...")
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
                        self._log('info', f"[RECOVERY] Browser restarted and logged in successfully on attempt {attempt}.")
                        return True
                    except Exception as ex:
                        self._log('warning', f"[RECOVERY] Attempt {attempt}/{max_attempts} failed to relaunch/login: {ex}")
                        if attempt == max_attempts:
                            self._log('error', f"[RECOVERY] All {max_attempts} attempts to restore browser session failed: {ex}")
                            return False
                return False

            # Launch driver with retry to absorb temporary host or port pressure
            for launch_attempt in range(1, 4):
                try:
                    driver = launch_chrome_driver(options, log_func=lambda m: self._log('info', m))
                    wait = WebDriverWait(driver, learned_timeout)
                    break
                except Exception as le:
                    if launch_attempt < 3 and not self._abort():
                        self._log('warning', f"Chrome driver launch failed (attempt {launch_attempt}/3): {le}. Retrying in {launch_attempt * 2}s...")
                        time.sleep(launch_attempt * 2)
                    else:
                        raise

            # Step 1: Login with retry if initial navigation or authentication times out
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
                if clean_c in ("studentid", "id", "student"):
                    student_col = col
                    break
            if not student_col:
                student_col = "studentId" if "studentId" in df.columns else df.columns[0]

            link_col = None
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("link", "url", "studentlink"):
                    link_col = col
                    break
            if not link_col:
                link_col = "link" if "link" in df.columns else (df.columns[1] if len(df.columns) > 1 else df.columns[0])

            self._log('info', f"Using columns: Student ID -> '{student_col}', Link -> '{link_col}' (Mode: {mode.upper()})")

            # Step 2: Process Rows
            for index, row in df.iterrows():
                if self._abort():
                    self._log('warning', "Automation aborted before completing all entries.")
                    break

                student_id = str(row[student_col]).strip()
                link = str(row[link_col]).strip().rstrip("/")

                # Extract finalGradeReceivedDate
                date_val = ""
                for d_col in ["finalGradeReceivedDate", "FinalGradeReceivedDate", "final_grade_received_date"]:
                    if d_col in df.columns and pd.notna(row.get(d_col)):
                        date_val = str(row.get(d_col)).strip()
                        break

                # Extract score value
                score_val = ""
                for s_col in ["score", "Score", "SCORE"]:
                    if s_col in df.columns and pd.notna(row.get(s_col)):
                        score_val = str(row.get(s_col)).strip()
                        break

                # Extract grade value
                grade_val = ""
                for g_col in ["grade", "Grade", "GRADE"]:
                    if g_col in df.columns and pd.notna(row.get(g_col)):
                        grade_val = str(row.get(g_col)).strip()
                        break

                enrollment_date_val = str(row["enrollmentDate"]).strip() if "enrollmentDate" in df.columns and pd.notna(row.get("enrollmentDate")) else ""
                completion_date_val = str(row["completionDate"]).strip() if "completionDate" in df.columns and pd.notna(row.get("completionDate")) else ""

                item_key = make_submission_key(student_id, link)


                # --- SESSION: skip already-completed rows ---
                if skip_completed and (item_key in completed_keys or (not link and student_id in completed_keys)):
                    self._log('info', f"[SESSION] Skipping {student_id} — already completed in a prior run.")
                    completed_count += 1
                    # Record skipped rows so run CSV row count matches source CSV
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
                    'idx': index,
                    'student_id': student_id,
                    'students_traversed': completed_count,
                    'total_students': total_count,
                    'total': total_count,
                })
                self._log('info', f"--- [Row {index+1}/{total_count}] Student ID: {student_id} ---")

                def _process_student():
                    # Optimization: Skip full page navigation if already on this link
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

                    # --- SESSION EXPIRY GUARD ---
                    # If the portal redirected us to the login page (session expired / timed out
                    # under parallel load), detect it immediately and raise BrowserSessionError so
                    # the caller's recovery block can restart the driver and re-authenticate.
                    # Without this check the worker would spend 20 s per student timing out on
                    # every subsequent wait.until() before eventually failing the row.
                    if is_portal_session_expired(driver):
                        raise BrowserSessionError(
                            f"Portal session expired (redirected to login) while navigating to student {student_id}.")

                    # --- LEARNING: Check if we already know which list this student is in ---
                    known_list = learning_engine.get_student_insight(student_id, 'list_category')

                    all_lists = [
                        ("Regular",   config["regular_student_list_xpath"],   config["edit_regular_xpath"]),
                        ("Waiting",   config["waiting_student_list_xpath"],   config["edit_waiting_xpath"]),
                        ("Withdrawn", config["withdrawn_student_list_xpath"], config["edit_withdrawn_xpath"]),
                    ]

                    if known_list:
                        self._log('info', f"[LEARNING] Student {student_id} known to be in '{known_list}' list — checking there first.")
                        ordered_lists = sorted(all_lists, key=lambda x: 0 if x[0] == known_list else 1)
                    else:
                        ordered_lists = all_lists

                    target_edit_xpath = None
                    target_list_xpath = None
                    category_found = None

                    # Click Students tab via safe_click — waits for it to be clickable,
                    # ensuring the AJAX for the student list has started.
                    student_tab_xpath = config.get("student_xpath", "")
                    if student_tab_xpath:
                        safe_click(driver, wait, student_tab_xpath, "Students tab", log_func=self._log)

                    # Poll for the student ID to appear in a list category.
                    # Under heavy parallel worker load, AJAX responses can take several seconds to render.
                    # We check textContent/innerText (immune to scroll/hidden state), direct XPath matching,
                    # and allow sufficient time (derived from max_action_wait_seconds, min 10s).
                    sid_str = str(student_id).strip()
                    poll_timeout = max(10.0, float(settings.get("max_action_wait_seconds", 20)))
                    _tab_poll_end = time.time() + poll_timeout
                    _tab_reclicked = False

                    while time.time() < _tab_poll_end and not category_found:
                        for cat, list_xpath, edit_xpath in ordered_lists:
                            try:
                                _lx_elems = driver.find_elements(By.XPATH, list_xpath)
                                if not _lx_elems:
                                    continue

                                # 1. Fast text check: inspect textContent & innerText across all matching elements
                                _combined_text = ' '.join(
                                    (el.get_attribute("textContent") or el.get_attribute("innerText") or el.text or '')
                                    for el in _lx_elems
                                )
                                if sid_str in _combined_text:
                                    category_found = cat
                                    target_list_xpath = list_xpath
                                    target_edit_xpath = edit_xpath
                                    break

                                # 2. Scoped XPath check: directly match candidate elements inside list container
                                _row_matches = driver.find_elements(
                                    By.XPATH,
                                    f"{list_xpath}//*[contains(normalize-space(.), '{sid_str}')]"
                                )
                                if _row_matches:
                                    category_found = cat
                                    target_list_xpath = list_xpath
                                    target_edit_xpath = edit_xpath
                                    break
                            except Exception:
                                pass

                        if not category_found:
                            # Re-click Students tab once after 3.5s if initial AJAX click was swallowed
                            if not _tab_reclicked and time.time() > (_tab_poll_end - poll_timeout + 3.5) and student_tab_xpath:
                                try:
                                    safe_click(driver, wait, student_tab_xpath, "Students tab (retry)", log_func=self._log)
                                    _tab_reclicked = True
                                except Exception:
                                    pass
                            time.sleep(0.15)

                    # 3. Page-wide fallback: if student ID is present on the page, proceed instead of failing
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
                                target_edit_xpath = config.get("edit_regular_xpath", "")
                        except Exception:
                            pass

                    if not category_found:
                        raise Exception(
                            f"Student ID {student_id} not located in Regular, Waiting, or Withdrawn list "
                            f"within {poll_timeout:.1f}s of clicking Students tab."
                        )

                    self._log('info', f"Located student {student_id} in '{category_found}' list.")
                    select_student_row(driver, wait, target_list_xpath, student_id, category_found, log_func=self._log)
                    safe_click(driver, wait, target_edit_xpath, f"Edit button ({category_found})", log_func=self._log)

                    learning_engine.record_student_insight(student_id, 'list_category', category_found)
                    self._on_progress({'type': 'category', 'name': category_found})
                    self._log('info', f"Student ID {student_id} identified under '{category_found}' list.")
                    self._log('info', "Opened 'Edit Status' modal.")

                    if mode == "unsubmit":
                        # ==============================================================
                        # UN-SUBMIT GRADE WORKFLOW
                        # Mark 'Final Grade Received' as 'No' and submit the status modal
                        # ==============================================================
                        modal_submit_xpath = config.get("update_xpath") or config.get("submit_xpath")
                        if not modal_submit_xpath:
                            raise Exception("Neither 'update_xpath' nor 'submit_xpath' defined in credentials for modal submission.")

                        # Try JS fast-path for unsubmit (non-blocking — no WebDriverWait)
                        js_unsub_ok = False
                        try:
                            # Check immediately; if button not yet in DOM fall through to Selenium
                            _unsub_matches = driver.find_elements(By.XPATH, modal_submit_xpath)
                            if _unsub_matches:
                                unsub_res = self._js_unsubmit_modal(
                                    driver,
                                    config.get("final_grade_xpath", ""),
                                    config.get("final_grade_received_date_xpath", ""),
                                    modal_submit_xpath
                                )
                                if unsub_res.get("success"):
                                    js_unsub_ok = True
                                    self._log('info', "[JS-SPEED] Un-submitted grade in modal via direct JS.")
                        except Exception as ue:
                            self._log('debug', f"JS unsubmit note: {ue}")

                        if not js_unsub_ok:
                            fg_elem = wait.until(EC.presence_of_element_located((By.XPATH, config["final_grade_xpath"])))
                            fg_dropdown = Select(fg_elem)
                            try:
                                fg_dropdown.select_by_visible_text("No")
                            except Exception:
                                no_selected = False
                                for opt in fg_dropdown.options:
                                    if opt.text.strip().lower() == "no" or opt.get_attribute("value").strip().lower() in ("no", "0", "n", "false"):
                                        opt.click()
                                        no_selected = True
                                        break
                                if not no_selected:
                                    raise Exception("Could not find 'No' option in Final Grade Received dropdown.")
                            self._log('info', "Marked 'Final Grade Received' dropdown as 'No'.")

                            # Safely clear final grade received date if field is present and active
                            date_xpath = config.get("final_grade_received_date_xpath", "")
                            if date_xpath:
                                try:
                                    date_elems = driver.find_elements(By.XPATH, date_xpath)
                                    if date_elems and date_elems[0].is_displayed() and date_elems[0].is_enabled():
                                        date_elems[0].clear()
                                        self._log('info', "Cleared 'Final Grade Received Date' field.")
                                except Exception:
                                    pass

                            safe_click(driver, wait, modal_submit_xpath, "Update/Submit button", log_func=self._log)

                        self._log('info', f"Successfully un-submitted grade for Student ID {student_id}.")
                        self._log_csv_status(csv_path, student_id, link, "SUCCESS",
                                             "Un-submitted grade (Final Grade Received marked 'No').")
                        return

                    # ==============================================================
                    # SUBMIT GRADE WORKFLOW (Standard)
                    # ==============================================================
                    has_grade_date = bool(date_val)
                    update_xpath = config.get("update_xpath", "")

                    # --- FAST-PATH 2: Direct In-Browser JS Modal Fill & Update (non-blocking) ---
                    js_modal_ok = False
                    if update_xpath:
                        try:
                            # Non-blocking: modal opened via Edit button so update btn should be
                            # present immediately. If not, fall through to Selenium in <1ms.
                            _upd_matches = driver.find_elements(By.XPATH, update_xpath)
                            if _upd_matches:
                                modal_res = self._js_fill_modal_and_update(
                                    driver,
                                    config.get("enrollment_date_xpath", ""), enrollment_date_val,
                                    config.get("completion_date_xpath", ""), completion_date_val,
                                    config.get("final_grade_xpath", ""), has_grade_date,
                                    config.get("final_grade_received_date_xpath", ""), date_val,
                                    update_xpath,
                                )
                                if modal_res.get("success"):
                                    js_modal_ok = True
                                    self._log('info', "[JS-SPEED] Injected modal dates/status and clicked Update.")
                        except Exception as me:
                            self._log('debug', f"JS modal fast-path note: {me}")

                    if not js_modal_ok:
                        # Standard Selenium Fallback
                        if enrollment_date_val and config.get("enrollment_date_xpath"):
                            safe_send_keys(driver, wait, config["enrollment_date_xpath"], enrollment_date_val,
                                           "Enrollment Date field", log_func=self._log)

                        if completion_date_val and config.get("completion_date_xpath"):
                            safe_send_keys(driver, wait, config["completion_date_xpath"], completion_date_val,
                                           "Completion Date field", log_func=self._log)

                        if has_grade_date:
                            fg_dropdown = Select(wait.until(EC.presence_of_element_located((By.XPATH, config["final_grade_xpath"]))))
                            fg_dropdown.select_by_visible_text("Yes")

                            safe_send_keys(driver, wait, config["final_grade_received_date_xpath"], date_val,
                                           "Final Grade Received Date field", log_func=self._log)
                        else:
                            self._log('info', f"Student {student_id}: finalGradeReceivedDate missing — skipping grade/date fields.")

                        safe_click(driver, wait, update_xpath, "Update button", log_func=self._log)
                        self._log('info', "Submitted updated grade status.")

                    # --- FAST-PATH 3: Direct In-Browser JS Grade, Adjust Score & Submit ---
                    grade_xpath = config.get("grade_xpath", '//*[@id="GRADE"]')
                    adjust_score_xpath = config.get("adjust_score_xpath", "")
                    field_submit_xpath = config.get("submit_xpath") or config.get("score_submit_xpath") or config.get("grade_submit_xpath", "")

                    js_submit_ok = False
                    if field_submit_xpath and (grade_val or score_val):
                        try:
                            # Non-blocking poll: after clicking Update the grade page may need
                            # a moment to render. Poll for up to 1.0s in 100ms steps — much
                            # cheaper than a 3s blocking WebDriverWait timeout that dumps a
                            # stacktrace on every miss.
                            _sub_btn_ready = False
                            _sub_poll_end = time.time() + 1.0
                            while time.time() < _sub_poll_end:
                                if driver.find_elements(By.XPATH, field_submit_xpath):
                                    _sub_btn_ready = True
                                    break
                                time.sleep(0.1)
                            if _sub_btn_ready:
                                sub_res = self._js_submit_grade_and_score(
                                    driver,
                                    grade_xpath, grade_val,
                                    adjust_score_xpath, score_val,
                                    field_submit_xpath
                                )
                                if sub_res.get("success"):
                                    js_submit_ok = True
                                    self._log('info', f"[JS-SPEED] Successfully posted grade ('{grade_val}') and score ('{score_val}') for Student ID {student_id}.")
                        except Exception as se:
                            self._log('debug', f"JS grade/score fast-path note: {se}")

                    if not js_submit_ok:
                        # Standard Selenium Fallback
                        # Step A: Select Grade from dropdown at //*[@id="GRADE"] (or grade_xpath)
                        if grade_val:
                            safe_select_dropdown(driver, grade_xpath, grade_val, "Grade dropdown", log_func=self._log)
                            self._log('info', f"Selected Grade '{grade_val}' from dropdown at {grade_xpath}.")

                        # Step B: Input Score into Adjust Score field
                        if score_val and adjust_score_xpath:
                            safe_send_keys(driver, wait, adjust_score_xpath, score_val, "Adjust Score field", log_func=self._log)
                            self._log('info', f"Entered Score '{score_val}' into Adjust Score field.")
                        elif score_val or not grade_val:
                            try:
                                field_xpath, dynamic_submit_xpath, field_label = self._resolve_score_or_grade_field(
                                    driver, config, student_id
                                )
                                if not field_submit_xpath:
                                    field_submit_xpath = dynamic_submit_xpath

                                if field_label == "Grade" and not grade_val:
                                    safe_select_dropdown(driver, field_xpath, score_val, f"{field_label} field", log_func=self._log)
                                elif field_label == "Adjust Score" and score_val:
                                    safe_send_keys(driver, wait, field_xpath, score_val, f"{field_label} field", log_func=self._log)
                            except Exception as res_err:
                                self._log('info', f"Field resolution note: {res_err}")

                        # Step C: Safety check — re-confirm Grade selection in dropdown before submitting
                        if grade_val:
                            safe_select_dropdown(driver, grade_xpath, grade_val, "Grade dropdown (safety re-select)", log_func=self._log)
                            self._log('info', f"Safety re-check: Confirmed Grade '{grade_val}' is selected in dropdown at {grade_xpath} before submit.")

                        # Step D: Submit final changes
                        if field_submit_xpath:
                            safe_click(driver, wait, field_submit_xpath, "Submit button", log_func=self._log)
                            self._log('info', f"Successfully posted grade ('{grade_val}') and score ('{score_val}') for Student ID {student_id}.")
                        else:
                            submit_xpath = config.get("submit_xpath", "")
                            if submit_xpath:
                                _submit_end = time.time() + 8
                                _clicked = False
                                while time.time() < _submit_end:
                                    _matches = driver.find_elements(By.XPATH, submit_xpath)
                                    if _matches:
                                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", _matches[0])
                                        time.sleep(0.15)
                                        try:
                                            _matches[0].click()
                                        except Exception:
                                            driver.execute_script("arguments[0].click();", _matches[0])
                                        self._log('info', "Clicked submit button.")
                                        _clicked = True
                                        break
                                    time.sleep(0.3)
                                if not _clicked:
                                    raise Exception(f"Submit button not found within 8s after Update (xpath: {submit_xpath})")

                    self._log_csv_status(csv_path, student_id, link, "SUCCESS",
                                         f"Updated grade ('{grade_val}') and score ('{score_val}') successfully.")

                row_ok, _, row_err = process_item_with_retry(
                    student_id, _process_student, active_module_id,
                    log_func=self._log, abort_flag=self._abort, track_session=False,
                )
                if not row_ok and (
                    isinstance(row_err, (BrowserSessionError, TimeoutException))
                    or is_dead_session_error(row_err)
                    or (row_err is not None and is_portal_session_expired(driver))
                ):
                    if not self._abort():
                        if _restart_driver():
                            self._log('info', f"[RECOVERY] Retrying student {student_id} with restored browser session...")
                            row_ok, _, row_err = process_item_with_retry(
                                student_id, _process_student, active_module_id,
                                log_func=self._log, abort_flag=self._abort, track_session=False,
                            )
                        else:
                            self._log('error', "[RECOVERY] Unable to restore browser session. Stopping worker.")
                            self._log_csv_status(target_csv, student_id, link, "FAILED", "Browser session died and could not be recovered")
                            remove_grade_item_completed(target_csv, student_id, link, mode=mode)
                            SessionManager.update_item_status(active_module_id, item_key, success=False)
                            break

                if row_ok:
                    success_count += 1
                    mark_grade_item_completed(target_csv, student_id, link, mode=mode)
                    SessionManager.update_item_status(active_module_id, item_key, success=True)
                else:
                    self._log('error', f"ERROR: Failed processing Student ID {student_id} after retries: {row_err}")
                    self._log_csv_status(target_csv, student_id, link, "FAILED", str(row_err))
                    remove_grade_item_completed(target_csv, student_id, link, mode=mode)
                    SessionManager.update_item_status(active_module_id, item_key, success=False)

                completed_count += 1
                self._on_progress({
                    'type': 'item_done',
                    'completed': completed_count,
                    'students_traversed': completed_count,
                    'total_students': total_count,
                    'total': total_count,
                    'student_id': student_id,
                    'success': row_ok,
                })
            else:
                # for/else: loop finished without a break (no abort) — clear session.
                SessionManager.clear_session(active_module_id)
                self._log('info', "[SESSION] All rows processed — session cleared.")

        finally:
            try:
                safe_quit_driver(driver, log_func=lambda m: self._log('debug', m))
            except Exception:
                pass
            driver = None

            # --- LEARNING: Record run summary ---
            try:
                duration = time.time() - run_start
                learning_engine.record_run_summary(
                    module_name="Un-submit Grades" if mode == "unsubmit" else "Grade Submission",
                    total_items=total_count,
                    success_count=success_count,
                    total_files=0,
                    duration_sec=duration,
                )
                self._log('info', f"[LEARNING] Run summary recorded. Duration: {duration:.1f}s, "
                                   f"Successes: {success_count}/{total_count}")

                # --- LEARNING: Record whether this DevTools setting worked ---
                if total_count:
                    learning_engine.record_devtools_outcome(domain_key, effective_devtools, success_count > 0)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Parallel execution
    # ------------------------------------------------------------------

    def run_parallel(
        self,
        config: dict,
        csv_path: str,
        n_workers: int,
        devtools: bool = False,
        on_progress=None,
        abort_flag=None,
        log=None,
        session_inputs: dict | None = None,
        mode: str = "submit",
        skip_completed: bool = True,
    ) -> None:
        """Run grade submission with *n_workers* independent Chrome instances
        working in parallel, each handling a separate chunk of the CSV.

        Progress events from all workers are merged and forwarded through the
        single *on_progress* / *log* callbacks supplied by the caller, so the
        existing UI progress bar and KPI grid work without any changes.

        Thread safety:
          - Each worker owns its own ``GradeSubmitter`` instance and Selenium
            driver, so there is no shared browser state.
          - The shared execution-results CSV is protected by ``_csv_write_lock``.
          - Session persistence uses per-worker module IDs
            (``grade_submission_w0``, ``grade_submission_w1``, …) so workers
            never clobber each other's progress checkpoints.
          - The *abort_flag* is checked by every worker; setting it once stops
            all of them at their next row boundary.
        """
        _log  = log          or (lambda l, m: None)
        _prog = on_progress  or (lambda d: None)
        _abort = abort_flag  or (lambda: False)

        n_workers = max(1, n_workers)
        run_start = time.time()

        # ── Load and partition the DataFrame ────────────────────────────────
        df_full = pd.read_csv(csv_path)
        total_count = len(df_full)

        if total_count == 0:
            _log('warning', "CSV is empty — nothing to process.")
            return

        # Identify student and link columns
        student_col = None
        for col in df_full.columns:
            clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
            if clean_c in ("studentid", "id", "student"):
                student_col = col
                break
        if not student_col:
            student_col = "studentId" if "studentId" in df_full.columns else df_full.columns[0]

        link_col = None
        for col in df_full.columns:
            clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
            if clean_c in ("link", "url", "studentlink"):
                link_col = col
                break
        if not link_col:
            link_col = "link" if "link" in df_full.columns else (df_full.columns[1] if len(df_full.columns) > 1 else df_full.columns[0])

        already_completed_count = 0
        df_pending = df_full

        if skip_completed:
            completed_keys = get_completed_grade_items(csv_path, mode=mode, log_func=_log)
            if completed_keys:
                def _row_is_done(r):
                    sid = str(r[student_col]).strip()
                    lnk = str(r[link_col]).strip().rstrip("/")
                    k = make_submission_key(sid, lnk)
                    return k in completed_keys or (not lnk and sid in completed_keys)

                mask_done = df_full.apply(_row_is_done, axis=1)
                already_completed_count = int(mask_done.sum())

                if already_completed_count >= total_count:
                    _log('info', f"[SESSION] All {total_count} rows in this CSV have already been successfully submitted! Nothing to process.")
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
                    _log('info', f"[SESSION] Found {already_completed_count} of {total_count} rows already submitted in prior runs. "
                                 f"Skipping them and distributing remaining {len(df_pending)} unsubmitted rows across workers...")

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

        # ── Shared counters (protected by a lock) ───────────────────────────
        _counter_lock = threading.Lock()
        _shared = {'completed': already_completed_count, 'success': already_completed_count}

        # Shared run_id so all workers append to the same results file
        shared_run_id = new_run_id()

        def _worker(worker_idx: int, chunk_df: pd.DataFrame):
            """Target function for each worker thread."""
            worker_module_id = f"{MODULE_ID}_w{worker_idx}"
            submitter = GradeSubmitter()
            submitter._run_id = shared_run_id   # point all workers at same log
            submitter._csv_lock = _csv_write_lock  # shared write lock

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

            # Write a temp CSV for this worker's chunk so run() can read it
            chunk_csv = csv_path.replace('.csv', f'_chunk_w{worker_idx}_{shared_run_id}.csv')
            try:
                chunk_df.to_csv(chunk_csv, index=False)
                submitter.run(
                    config=config,
                    csv_path=chunk_csv,
                    devtools=devtools,
                    on_progress=worker_progress,
                    abort_flag=_abort,
                    log=worker_log,
                    session_inputs=session_inputs,
                    mode=mode,
                    skip_completed=False,  # Already pre-filtered to pending rows
                    canonical_csv_path=csv_path,
                    _module_id_override=worker_module_id,
                )
            except Exception as w_err:
                worker_log('error', f"Worker encountered an unhandled error: {w_err}")
            finally:
                # Clean up the temp chunk CSV
                try:
                    if os.path.isfile(chunk_csv):
                        os.remove(chunk_csv)
                except Exception:
                    pass

        # ── Launch and join all worker threads ──────────────────────────────
        threads = [
            threading.Thread(
                target=_worker,
                args=(idx, chunk),
                name=f"GradeWorker-{idx}",
                daemon=True,
            )
            for idx, chunk in enumerate(chunks)
        ]

        for idx, t in enumerate(threads):
            if _abort():
                break
            t.start()
            if idx < len(threads) - 1:
                # Stagger worker launches to prevent OS process spawn storm,
                # port thrashing, and simultaneous DNS flood on initial logins.
                time.sleep(0.4)

        for t in threads:
            t.join()

        duration = time.time() - run_start
        total_success = _shared['success']
        _log('info',
             f"[PARALLEL] All {actual_workers} worker(s) finished. "
             f"Duration: {duration:.1f}s | "
             f"Success: {total_success}/{total_count}")

        # Record combined run summary in the learning system
        learning_engine.record_run_summary(
            module_name="Un-submit Grades (Parallel)" if mode == "unsubmit" else "Grade Submission (Parallel)",
            total_items=total_count,
            success_count=total_success,
            total_files=0,
            duration_sec=duration,
        )

    def verify_logs(self, csv_path: str, log=None) -> dict:
        _log = log or (lambda l, m: None)
        base_dir = os.path.dirname(csv_path)
        # Runs now write a distinct execution_results_<run_id>.csv per run --
        # verify against whichever one is most recent.
        csv_log_file = find_latest_run_log(base_dir, "execution_results", ".csv")

        if not csv_log_file or not os.path.isfile(csv_log_file):
            return {'total': 0, 'processed': 0, 'missing': []}

        try:
            df_input = pd.read_csv(csv_path)
            df_logs  = pd.read_csv(csv_log_file)

            student_col = None
            for col in df_input.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("studentid", "id", "student"):
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

    # ------------------------------------------------------------------
    # High-Performance In-Browser JavaScript Execution Pipelines
    # ------------------------------------------------------------------

    def _js_find_and_open_student(self, driver, student_tab_xpath: str, ordered_lists: list, student_id: str) -> dict:
        """Fast single-roundtrip JS scan to identify which list contains the student.

        INTENTIONALLY READ-ONLY — no clicks are performed in JS.
        PCR Educator onclick handlers reference page-scope closure variables that
        are inaccessible inside ``new Function()``’s isolated scope, causing Chrome’s
        renderer to crash.  All actual row selection and Edit-button clicks are done
        by the caller via safe Selenium APIs.
        """
        js_code = """
        var studentTabXpath = arguments[0];
        var lists = arguments[1];
        var studentId = arguments[2];

        function getByXpath(xpath) {
            if (!xpath) return null;
            try {
                var res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                return res.singleNodeValue;
            } catch(e) {
                return null;
            }
        }

        // Scan lists for studentId — read-only, no DOM mutations / clicks
        for (var i = 0; i < lists.length; i++) {
            var cat = lists[i][0];
            var listXpath = lists[i][1];

            var listNode = getByXpath(listXpath);
            if (!listNode) continue;
            var listText = listNode.textContent || '';
            if (listText.indexOf(studentId) !== -1) {
                return { success: true, category: cat };
            }
        }
        return { success: false, not_found: true };
        """
        try:
            res = driver.execute_script(js_code, student_tab_xpath, ordered_lists, student_id)
            return res if isinstance(res, dict) else {'success': False}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _js_fill_modal_and_update(
        self,
        driver,
        enrollment_xpath: str,
        enrollment_val: str,
        completion_xpath: str,
        completion_val: str,
        final_grade_xpath: str,
        has_grade_date: bool,
        final_grade_date_xpath: str,
        final_grade_date_val: str,
        update_xpath: str,
    ) -> dict:
        """Direct DOM injection of modal dates & status, then trigger Update button click."""
        js_code = """
        var enrollmentXpath = arguments[0];
        var enrollmentVal = arguments[1];
        var completionXpath = arguments[2];
        var completionVal = arguments[3];
        var finalGradeXpath = arguments[4];
        var hasGradeDate = arguments[5];
        var finalGradeDateXpath = arguments[6];
        var finalGradeDateVal = arguments[7];
        var updateXpath = arguments[8];

        function getByXpath(xpath) {
            if (!xpath) return null;
            try {
                var res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                return res.singleNodeValue;
            } catch(e) {
                return null;
            }
        }

        function setInput(xpath, val) {
            if (!xpath || !val) return false;
            var node = getByXpath(xpath);
            if (!node) return false;
            node.value = val;
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }

        function setSelect(xpath, textVal) {
            if (!xpath || !textVal) return false;
            var node = getByXpath(xpath);
            if (!node) return false;
            var target = ('' + textVal).trim().toLowerCase();
            for (var i = 0; i < node.options.length; i++) {
                var optText = (node.options[i].text || '').trim().toLowerCase();
                var optVal = (node.options[i].value || '').trim().toLowerCase();
                if (optText === target || optVal === target) {
                    node.selectedIndex = i;
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }

        if (enrollmentVal) setInput(enrollmentXpath, enrollmentVal);
        if (completionVal) setInput(completionXpath, completionVal);
        if (hasGradeDate) {
            setSelect(finalGradeXpath, "Yes");
            setInput(finalGradeDateXpath, finalGradeDateVal);
        }

        var updateBtn = getByXpath(updateXpath);
        if (updateBtn) {
            try { updateBtn.scrollIntoView({ block: 'center' }); } catch(e) {}
            try {
                updateBtn.click();
            } catch(e) {
                if (updateBtn.onclick) {
                    try { updateBtn.onclick(); } catch(e2) {}
                }
            }
            return { success: true };
        }
        return { success: false, error: "Update button not found" };
        """
        try:
            res = driver.execute_script(
                js_code,
                enrollment_xpath, enrollment_val,
                completion_xpath, completion_val,
                final_grade_xpath, has_grade_date,
                final_grade_date_xpath, final_grade_date_val,
                update_xpath
            )
            return res if isinstance(res, dict) else {'success': False}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _js_unsubmit_modal(self, driver, final_grade_xpath: str, date_xpath: str, modal_submit_xpath: str) -> dict:
        """Fast-path un-submit in modal: mark 'No' on final grade, clear date, click submit."""
        js_code = """
        var finalGradeXpath = arguments[0];
        var dateXpath = arguments[1];
        var modalSubmitXpath = arguments[2];

        function getByXpath(xpath) {
            if (!xpath) return null;
            try {
                var res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                return res.singleNodeValue;
            } catch(e) {
                return null;
            }
        }

        var fg = getByXpath(finalGradeXpath);
        if (fg) {
            for (var i = 0; i < fg.options.length; i++) {
                var optT = (fg.options[i].text || '').trim().toLowerCase();
                var optV = (fg.options[i].value || '').trim().toLowerCase();
                if (optT === 'no' || optV === 'no' || optV === '0' || optV === 'false') {
                    fg.selectedIndex = i;
                    fg.dispatchEvent(new Event('change', { bubbles: true }));
                    break;
                }
            }
        }

        if (dateXpath) {
            var dt = getByXpath(dateXpath);
            if (dt) {
                dt.value = '';
                dt.dispatchEvent(new Event('input', { bubbles: true }));
                dt.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }

        var sub = getByXpath(modalSubmitXpath);
        if (sub) {
            try { sub.scrollIntoView({ block: 'center' }); } catch(e) {}
            try {
                sub.click();
            } catch(e) {
                if (sub.onclick) {
                    try { sub.onclick(); } catch(e2) {}
                }
            }
            return { success: true };
        }
        return { success: false, error: "Modal submit button not found" };
        """
        try:
            res = driver.execute_script(js_code, final_grade_xpath, date_xpath, modal_submit_xpath)
            return res if isinstance(res, dict) else {'success': False}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _js_submit_grade_and_score(
        self,
        driver,
        grade_xpath: str,
        grade_val: str,
        adjust_score_xpath: str,
        score_val: str,
        submit_xpath: str,
    ) -> dict:
        """Direct DOM assignment of grade dropdown and adjust score input, followed by final submit click."""
        js_code = """
        var gradeXpath = arguments[0];
        var gradeVal = arguments[1];
        var adjustScoreXpath = arguments[2];
        var scoreVal = arguments[3];
        var submitXpath = arguments[4];

        function getByXpath(xpath) {
            if (!xpath) return null;
            try {
                var res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                return res.singleNodeValue;
            } catch(e) {
                return null;
            }
        }

        function setSelect(xpath, textVal) {
            if (!xpath || !textVal) return false;
            var node = getByXpath(xpath);
            if (!node) return false;
            var target = ('' + textVal).trim().toLowerCase();
            for (var i = 0; i < node.options.length; i++) {
                var optText = (node.options[i].text || '').trim().toLowerCase();
                var optVal = (node.options[i].value || '').trim().toLowerCase();
                if (optText === target || optVal === target) {
                    node.selectedIndex = i;
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }

        function setInput(xpath, val) {
            if (!xpath || !val) return false;
            var node = getByXpath(xpath);
            if (!node) return false;
            node.value = val;
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }

        var gradeOk = true;
        if (gradeVal) {
            gradeOk = setSelect(gradeXpath, gradeVal);
        }

        var scoreOk = true;
        if (scoreVal && adjustScoreXpath) {
            scoreOk = setInput(adjustScoreXpath, scoreVal);
        }

        // Safety re-confirm grade selection
        if (gradeVal) {
            setSelect(gradeXpath, gradeVal);
        }

        if (submitXpath) {
            var submitBtn = getByXpath(submitXpath);
            if (submitBtn) {
                try { submitBtn.scrollIntoView({ block: 'center' }); } catch(e) {}
                try {
                    submitBtn.click();
                } catch(e) {
                    if (submitBtn.onclick) {
                        try { submitBtn.onclick(); } catch(e2) {}
                    }
                }
                return { success: true, grade_set: gradeOk, score_set: scoreOk };
            }
        }
        return { success: false, grade_set: gradeOk, score_set: scoreOk, error: "Submit button not found" };
        """
        try:
            res = driver.execute_script(
                js_code,
                grade_xpath, grade_val,
                adjust_score_xpath, score_val,
                submit_xpath
            )
            return res if isinstance(res, dict) else {'success': False}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _perform_login(self, driver, wait, config: dict, login_url: str):
        """Login with learned XPath fallback → default XPath fallback."""
        learned_user = learning_engine.get_learned_xpaths(login_url, 'username_field')
        learned_pass = learning_engine.get_learned_xpaths(login_url, 'password_field')
        learned_btn  = learning_engine.get_learned_xpaths(login_url, 'login_button')

        user_xpath = (learned_user[0] if learned_user else None) or config.get("user_xpath", "")
        pass_xpath = (learned_pass[0] if learned_pass else None) or config.get("password_xpath", "")
        btn_xpath  = (learned_btn[0]  if learned_btn  else None) or config.get("login_xpath", "")

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

            # Record success
            learning_engine.record_successful_xpath(login_url, 'username_field', user_xpath)
            learning_engine.record_successful_xpath(login_url, 'password_field', pass_xpath)
            learning_engine.record_successful_xpath(login_url, 'login_button',   btn_xpath)

        except Exception:
            learning_engine.record_failed_xpath(login_url, 'username_field', user_xpath)
            # Re-raise so the caller knows login failed
            raise

    # ------------------------------------------------------------------
    # Score / Grade Field Detection with Learning
    # ------------------------------------------------------------------

    def _resolve_score_or_grade_field(self, driver, config: dict, student_id: str, timeout: float = 6):
        """
        Detect which score-entry field is visible.
        Uses learned field type for this student to check the likely candidate first.
        """
        adjust_score_xpath  = config.get("adjust_score_xpath", "")
        grade_xpath         = config.get("grade_xpath", '//*[@id="GRADE"]')
        score_submit_xpath  = config.get("score_submit_xpath", config.get("submit_xpath", ""))
        grade_submit_xpath  = config.get("grade_submit_xpath", "")

        # --- LEARNING: Check known field type for this student ---
        known_field = learning_engine.get_student_insight(student_id, 'grade_field_type')
        if known_field:
            self._log('info', f"[LEARNING] Student {student_id}: expected field type '{known_field}' from previous run.")

        # Build check order based on what was seen before
        if known_field == "Grade":
            check_order = [
                (grade_xpath,        grade_submit_xpath,  "Grade"),
                (adjust_score_xpath, score_submit_xpath,  "Adjust Score"),
            ]
        else:
            check_order = [
                (adjust_score_xpath, score_submit_xpath,  "Adjust Score"),
                (grade_xpath,        grade_submit_xpath,  "Grade"),
            ]

        end_time = time.time() + timeout
        while time.time() < end_time:
            for field_xpath, submit_xpath, label in check_order:
                if not field_xpath:
                    continue
                matches = driver.find_elements(By.XPATH, field_xpath)
                if any(e.is_displayed() for e in matches):
                    return field_xpath, submit_xpath, label
            time.sleep(0.2)

        raise Exception(
            "Could not find either an 'Adjust Score' field or a 'Grade' field after clicking Update."
        )

    def _log_csv_status(self, csv_path: str, student_id: str, link: str, status: str, details: str):
        """Append a result row to the per-run timestamped CSV and update the
        cumulative master ``execution_results.csv`` in the same directory.

        The master CSV always reflects the *latest* status for each
        (StudentId, Link) pair across all runs, so retried rows get updated
        in-place rather than duplicated.
        """
        # Use the original (non-chunk) base dir when running as a parallel worker
        base_dir = os.path.dirname(csv_path)
        run_id = getattr(self, '_run_id', None) or new_run_id()
        csv_log_file = os.path.join(base_dir, f"execution_results_{run_id}.csv")
        master_log_file = os.path.join(base_dir, "execution_results.csv")
        # Use per-instance lock if set by parallel coordinator, else the global lock
        lock = getattr(self, '_csv_lock', _csv_write_lock)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [ts, student_id, link, status, details]
        header = ["Timestamp", "StudentId", "Link", "Status", "Details"]
        try:
            with lock:
                # 1. Per-run timestamped log (append-only)
                file_exists = os.path.isfile(csv_log_file)
                with open(csv_log_file, "a", newline="") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(header)
                    writer.writerow(row)

                # 2. Cumulative master log (upsert by StudentId + Link)
                _update_master_csv(master_log_file, header, student_id, link, row)
        except Exception as e:
            self._log('error', f"Error appending to CSV log: {e}")
