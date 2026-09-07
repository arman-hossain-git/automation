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
import pandas as pd
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from core.chrome_driver import launch_chrome_driver, build_basic_options
from core.settings_manager import settings
from core.selenium_helpers import (
    safe_click,
    safe_send_keys,
    select_student_row,
    safe_select_dropdown,
)
from core.learning_system import learning_engine
from core.session_manager import SessionManager, process_item_with_retry
from core.utils import new_run_id, find_latest_run_log

MODULE_ID = "grade_submission"


class GradeSubmitter:
    """Submits student final grades with automatic detection of Adjust Score vs Grade dropdown field."""

    def run(
        self,
        config: dict,
        csv_path: str,
        devtools: bool = False,
        on_progress=None,
        abort_flag=None,
        log=None,
        session_inputs: dict | None = None,
    ) -> None:
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda d: None)
        self._abort = abort_flag or (lambda: False)

        run_start = time.time()
        # Per-run identifier baked into this run's execution results filename
        # so consecutive runs never append into the same file and stay easy
        # to tell apart in a folder listing.
        self._run_id = new_run_id()
        success_count = 0

        df = pd.read_csv(csv_path)
        total_count = len(df)
        completed_count = 0

        self._on_progress({'type': 'total', 'total': total_count})

        # --- SESSION PERSISTENCE ---
        session_inputs = session_inputs or {
            'csv_path': csv_path,
            'login_url': config.get('url', ''),
        }
        session_data = SessionManager.start_session(MODULE_ID, session_inputs, total_items=total_count)
        completed_ids = set(session_data.get('completed_item_ids', []))
        if completed_ids:
            self._log('info', f"[SESSION] Resuming — {len(completed_ids)} of {total_count} rows already done, will be skipped.")

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
            driver = launch_chrome_driver(options, log_func=lambda m: self._log('info', m))

            # --- LEARNING: Use learned page-load timeout for this domain ---
            learned_timeout = learning_engine.get_optimal_timeout(login_url, 'page_load', default_timeout=15.0)
            wait = WebDriverWait(driver, learned_timeout)

            # Step 1: Login
            self._log('info', f"Navigating to login page: {login_url}")
            t0 = time.time()
            driver.get(login_url)
            learning_engine.record_latency(login_url, 'page_load', time.time() - t0)

            self._log('info', "Entering login credentials...")
            self._perform_login(driver, wait, config, login_url)
            self._log('info', "Login submitted successfully.")

            # Step 2: Process Rows
            for index, row in df.iterrows():
                if self._abort():
                    self._log('warning', "Automation aborted before completing all entries.")
                    break

                student_id = str(row["studentId"]).strip()
                link = str(row["link"]).strip()
                date_val = str(row["finalGradeReceivedDate"]).strip() if "finalGradeReceivedDate" in df.columns and pd.notna(row.get("finalGradeReceivedDate")) else ""
                score_val = str(row["score"]).strip() if "score" in df.columns and pd.notna(row.get("score")) else ""

                # Extract grade value from 'grade', 'Grade', or 'GRADE' column
                grade_val = ""
                for g_col in ["grade", "Grade", "GRADE"]:
                    if g_col in df.columns and pd.notna(row.get(g_col)):
                        grade_val = str(row.get(g_col)).strip()
                        break

                enrollment_date_val = str(row["enrollmentDate"]).strip() if "enrollmentDate" in df.columns and pd.notna(row.get("enrollmentDate")) else ""
                completion_date_val = str(row["completionDate"]).strip() if "completionDate" in df.columns and pd.notna(row.get("completionDate")) else ""

                # --- SESSION: skip already-completed rows ---
                if student_id in completed_ids:
                    self._log('info', f"[SESSION] Skipping {student_id} — already completed in a prior run.")
                    completed_count += 1
                    self._on_progress({
                        'type': 'item_done',
                        'completed': completed_count,
                        'students_traversed': completed_count,
                        'total_students': total_count
                    })
                    continue

                self._on_progress({
                    'type': 'item_start',
                    'idx': index,
                    'student_id': student_id,
                    'students_traversed': index,
                    'total_students': total_count
                })
                self._log('info', f"--- [Row {index+1}/{total_count}] Student ID: {student_id} ---")

                def _process_student():
                    t_nav = time.time()
                    driver.get(link)
                    learning_engine.record_latency(link, 'page_load', time.time() - t_nav)

                    # Click Students Tab
                    safe_click(driver, wait, config["student_xpath"], "Students tab", log_func=self._log)

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

                    poll_end = time.time() + 6
                    while time.time() < poll_end and not category_found:
                        for cat, list_xpath, edit_xpath in ordered_lists:
                            try:
                                elem = driver.find_element(By.XPATH, list_xpath)
                                if student_id in elem.text:
                                    category_found = cat
                                    target_edit_xpath = edit_xpath
                                    target_list_xpath = list_xpath
                                    break
                            except NoSuchElementException:
                                continue
                        if not category_found:
                            time.sleep(0.15)

                    if not category_found:
                        raise Exception(f"Student ID {student_id} not located in Regular, Waiting, or Withdrawn list.")

                    learning_engine.record_student_insight(student_id, 'list_category', category_found)
                    self._on_progress({'type': 'category', 'name': category_found})
                    self._log('info', f"Student ID {student_id} identified under '{category_found}' list.")

                    select_student_row(driver, wait, target_list_xpath, student_id, category_found, log_func=self._log)
                    safe_click(driver, wait, target_edit_xpath, f"Edit button ({category_found})", log_func=self._log)
                    self._log('info', "Opened 'Edit Status' modal.")

                    # --- enrollmentDate (optional) ---
                    if enrollment_date_val and config.get("enrollment_date_xpath"):
                        safe_send_keys(driver, wait, config["enrollment_date_xpath"], enrollment_date_val,
                                       "Enrollment Date field", log_func=self._log)

                    # --- completionDate (optional) ---
                    if completion_date_val and config.get("completion_date_xpath"):
                        safe_send_keys(driver, wait, config["completion_date_xpath"], completion_date_val,
                                       "Completion Date field", log_func=self._log)

                    # --- Final Grade dropdown + date (optional, requires date_val) ---
                    has_grade_date = bool(date_val)
                    if has_grade_date:
                        fg_dropdown = Select(wait.until(EC.presence_of_element_located((By.XPATH, config["final_grade_xpath"]))))
                        fg_dropdown.select_by_visible_text("Yes")

                        safe_send_keys(driver, wait, config["final_grade_received_date_xpath"], date_val,
                                       "Final Grade Received Date field", log_func=self._log)
                    else:
                        self._log('info', f"Student {student_id}: finalGradeReceivedDate missing — skipping grade/date fields.")

                    safe_click(driver, wait, config["update_xpath"], "Update button", log_func=self._log)
                    self._log('info', "Submitted updated grade status.")

                    # --- Step A: Select Grade from dropdown at //*[@id="GRADE"] (or grade_xpath) ---
                    grade_xpath = config.get("grade_xpath", '//*[@id="GRADE"]')
                    if grade_val:
                        safe_select_dropdown(driver, grade_xpath, grade_val, "Grade dropdown", log_func=self._log)
                        self._log('info', f"Selected Grade '{grade_val}' from dropdown at {grade_xpath}.")

                    # --- Step B: Input Score into Adjust Score field ---
                    adjust_score_xpath = config.get("adjust_score_xpath", "")
                    field_submit_xpath = config.get("submit_xpath") or config.get("score_submit_xpath") or config.get("grade_submit_xpath", "")

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

                    # --- Step C: Safety check — re-confirm Grade selection in dropdown before submitting ---
                    if grade_val:
                        safe_select_dropdown(driver, grade_xpath, grade_val, "Grade dropdown (safety re-select)", log_func=self._log)
                        self._log('info', f"Safety re-check: Confirmed Grade '{grade_val}' is selected in dropdown at {grade_xpath} before submit.")

                    # --- Step D: Submit final changes ---
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
                    student_id, _process_student, MODULE_ID,
                    log_func=self._log, abort_flag=self._abort, track_session=False,
                )
                if row_ok:
                    success_count += 1
                    SessionManager.update_item_status(MODULE_ID, student_id, success=True)
                else:
                    self._log('error', f"ERROR: Failed processing Student ID {student_id} after retries: {row_err}")
                    self._log_csv_status(csv_path, student_id, link, "FAILED", str(row_err))
                    SessionManager.update_item_status(MODULE_ID, student_id, success=False)

                completed_count += 1
                self._on_progress({
                    'type': 'item_done',
                    'completed': completed_count,
                    'students_traversed': completed_count,
                    'total_students': total_count
                })
            else:
                # for/else: loop finished without a break (no abort) — clear session.
                SessionManager.clear_session(MODULE_ID)
                self._log('info', "[SESSION] All rows processed — session cleared.")

        finally:
            if driver:
                driver.quit()

            # --- LEARNING: Record run summary ---
            duration = time.time() - run_start
            learning_engine.record_run_summary(
                module_name="Grade Submission",
                total_items=total_count,
                success_count=success_count,
                total_files=0,
                duration_sec=duration,
            )
            self._log('info', f"[LEARNING] Run summary recorded. Duration: {duration:.1f}s, "
                               f"Successes: {success_count}/{total_count}")

            # --- LEARNING: Record whether this DevTools setting worked, so future
            # runs on this domain can auto-correct if the user forgets the checkbox. ---
            if total_count:
                learning_engine.record_devtools_outcome(domain_key, effective_devtools, success_count > 0)

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

            df_input["studentId"] = df_input["studentId"].astype(str).str.strip()
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
            safe_click(driver, wait, btn_xpath, "Login button", log_func=self._log)
            try:
                WebDriverWait(driver, 8).until(lambda d: d.current_url != login_url_before)
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
        base_dir = os.path.dirname(csv_path)
        run_id = getattr(self, '_run_id', None) or new_run_id()
        csv_log_file = os.path.join(base_dir, f"execution_results_{run_id}.csv")
        file_exists = os.path.isfile(csv_log_file)
        try:
            with open(csv_log_file, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "StudentId", "Link", "Status", "Details"])
                writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), student_id, link, status, details])
        except Exception as e:
            self._log('error', f"Error appending to CSV log: {e}")
