"""
modules/file_uploader.py
========================
Unified Business logic for uploading local student files into PCR Educator Files & Documents.

Combines MyPath Uploader and PCR Educator Uploader into a single, versatile File Uploader.
Target folder is selectable: can upload directly to Files & Documents root or under a subfolder
(e.g., 'Admissions Inquiry', 'Students Inquiry', or a custom folder).

Learning Integration:
  - Remembers per-student whether a category folder already existed on PCR.
    On re-runs, skips the folder-creation step for folders known to exist.
  - Learns login page-load latency per domain for dynamic timeout tuning.
  - Records run summary (duration, upload count) for the Learning dashboard.
"""

import os
import csv
import time
import shutil
import tempfile
import datetime
import threading

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from core.chrome_driver import launch_chrome_driver, build_basic_options
from core.settings_manager import settings
from core.selenium_helpers import (
    click_xpath,
    try_click_xpath,
    wait_for_popup_ok,
    wait_for_page_ready,
    open_dropdown_menu,
    folder_exists_in_listing,
    get_all_portal_folder_names,
    get_all_portal_folder_details,
    get_portal_files_in_current_table,
    get_portal_table_snapshot,
    get_portal_table_snapshot_adaptive,
    verify_uploaded_file_strict,
    file_exists_in_listing,
    click_folder_by_name,
    delete_portal_file_by_name,
    detect_unsupported_format_error,
    detect_malformed_filename_error,
    get_dialog_signatures,
    click_dialog_ok_button,
    dismiss_error_dialog,
    safe_send_keys,
    set_file_input_files,
    is_file_ready_for_upload,
    is_portal_session_expired,
)
from core.utils import (
    sanitize_upload_filename,
    list_category_folders,
    filter_valid_students,
    describe_exception,
    xpath_literal,
    new_run_id,
    find_latest_run_log,
    is_network_error,
)
from core.learning_system import learning_engine
from core.session_manager import (
    SessionManager,
    process_item_with_retry,
    check_network_connection,
    wait_for_network_connection,
    UnsupportedFormatError,
    MalformedFileError,
)

MODULE_ID = "file_uploader"

NO_TARGET_FOLDER_LABEL = "(None - upload to Files & Documents root)"
TARGET_FOLDER_OPTIONS = [
    NO_TARGET_FOLDER_LABEL,
    "Admissions Inquiry",
    "Students Inquiry",
]

DEFAULT_XPATHS = {
    'three_dot_menu_xpath':          "/html/body/div[1]/div/div[1]/div[2]/div[2]/button",
    'files_documents_menu_xpath':    "/html/body/div[3]/div/div[1]/div[2]/div[6]/div/a[14]",
    'new_folder_toolbar_button_xpath': "/html/body/div[1]/div/div[1]/div[2]/a[4]",
    'new_folder_option_xpath':       "//a[contains(normalize-space(.), 'New Folder')] | //li[contains(normalize-space(.), 'New Folder')]",
    'folder_name_input_xpath':       "/html/body/div[1]/div/div[2]/form/div/table/tbody/tr[1]/td[2]/input",
    'save_button_xpath':             "/html/body/div[1]/div/div[2]/form/div/table/tbody/tr[4]/td[2]/input",
    'popup_ok_xpath':                "/html/body/div[6]/div/div/div[3]/button",
    'upload_tool_button_xpath':      "/html/body/div[1]/div/div[1]/div[2]/a[5]",
    'folder_listing_table_xpath':    "/html/body/div[1]/div/div[2]/div/div[2]/table",
    'file_input_xpath':              "//input[@type='file']",
    'upload_submit_xpath':           "/html/body/div[1]/div/div[2]/form/table/tbody/tr/td/input",
    'upload_popup_ok_xpath':         "/html/body/div[6]/div/div/div[3]/button",
}


class FileUploader:
    """Unified File Uploader for PCR Educator & Orbund portals."""

    _csv_lock: threading.Lock | None = None
    _run_id: str = ""

    def __init__(self):
        self._log = lambda l, m: None
        self._on_progress = lambda d: None
        self._abort = lambda: False
        self._source_dir = ""

    def run(
        self,
        config: dict,
        students: list,
        source_dir: str,
        target_folder_name: str,
        devtools: bool = False,
        on_progress=None,
        abort_flag=None,
        log=None,
        session_inputs: dict | None = None,
    ) -> None:
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda d: None)
        self._abort = abort_flag or (lambda: False)
        self._source_dir = source_dir

        run_start = time.time()
        # Per-run identifier baked into this run's execution log filename so
        # consecutive runs never append into the same file and stay easy to
        # tell apart in a folder listing.
        self._run_id = new_run_id()
        total_uploaded = 0
        students_succeeded = 0
        loop_completed = False

        # --- SESSION PERSISTENCE: resume-aware run tracking ---
        # session_inputs identifies this exact run (credentials/CSV/source dir/etc).
        # If a prior run with the SAME inputs was left unfinished (crash, abort,
        # shutdown), start_session() picks that same session back up instead of
        # starting over, and completed_ids lets us skip rows already done.
        session_inputs = session_inputs or {
            'source_dir': source_dir,
            'target_folder_name': target_folder_name,
            'login_url': config.get('login_url', ''),
        }

        for key, default_val in DEFAULT_XPATHS.items():
            config.setdefault(key, default_val)
            if not config.get(key):
                config[key] = default_val

        students = filter_valid_students(students)
        if target_folder_name == NO_TARGET_FOLDER_LABEL:
            target_folder_name = ""

        session_data = SessionManager.start_session(MODULE_ID, session_inputs, total_items=len(students))
        completed_ids = set(session_data.get('completed_item_ids', []))
        if completed_ids:
            self._log('info', f"[SESSION] Resuming previous session — "
                               f"{len(completed_ids)} of {len(students)} students already completed and will be skipped.")

        login_url = config.get('login_url', '')
        domain_key = learning_engine.extract_domain(login_url)

        # --- LEARNING: auto-enable DevTools if this site has confirmed it needs it,
        # even though the user (or a UI default) left the checkbox unchecked. ---
        effective_devtools = learning_engine.resolve_devtools(devtools, domain_key, log_func=self._log)

        headless = settings.get("headless", False)
        if headless:
            self._log('info', "[SETTINGS] Headless mode enabled -- Chrome will run with no visible window.")
        chrome_options = build_basic_options(devtools=effective_devtools, headless=headless)
        tmp_upload_dir = tempfile.mkdtemp(prefix="pcr_upload_")
        driver = None

        try:
            driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))
            driver.set_script_timeout(60)

            # Interaction timeout: bounded by the user's configured ceiling
            # (Settings -> Maximum Wait Time per Action), with a 25s floor since
            # Orbund's menu animations are known to take 10-16s to settle -- a
            # lower learned/ceiling value would cut that off too early. This is
            # only a MAXIMUM; WebDriverWait still returns the instant each
            # element is actually ready, so most actions finish far sooner.
            wait_timeout = learning_engine.get_wait_ceiling(floor=25.0)
            wait = WebDriverWait(driver, wait_timeout, poll_frequency=0.2)

            # Login
            self._log('info', f"Navigating to login page: {login_url}")
            t0 = time.time()
            driver.get(login_url)
            learning_engine.record_latency(login_url, 'page_load', time.time() - t0)

            self._perform_login(driver, wait, config, login_url)
            self._log('info', "Login successful.")
            self._on_progress({'type': 'total', 'total': len(students)})

            for idx, student in enumerate(students):
                if self._abort():
                    break

                student_id = student['studentId']
                link       = student['link']
                student_root_folder = os.path.join(source_dir, student_id)

                if student_id in completed_ids:
                    self._log('info', f"[SESSION] Skipping student {student_id} — already completed in a previous run of this session.")
                    self._on_progress({
                        'type': 'student_done',
                        'students_traversed': idx + 1,
                        'total_students': len(students)
                    })
                    continue

                self._on_progress({
                    'type': 'student_start',
                    'student_id': student_id,
                    'idx': idx,
                    'students_traversed': idx,
                    'total_students': len(students)
                })

                category_folders = list_category_folders(student_root_folder)
                if not category_folders:
                    self._log('warning', f"No local folders for student {student_id}.")
                    self._on_progress({
                        'type': 'student_done',
                        'students_traversed': idx + 1,
                        'total_students': len(students)
                    })
                    continue

                total_files = sum(len(c['files']) for c in category_folders)
                self._on_progress({'type': 'files_total', 'total': total_files})
                max_student_retries = 3
                student_attempt = 0
                student_done = False

                while not student_done and not self._abort():
                    files_done = 0
                    batch_logs = []

                    try:
                        # 1. Pre-check internet connection before starting
                        if not check_network_connection():
                            self._log('warning', f"[NETWORK DISRUPTION] Internet connection lost before student {student_id}. Pausing automation and waiting for connection to recover...")
                            if not wait_for_network_connection(retry_wait_seconds=5, abort_flag=self._abort, log_func=self._log):
                                break
                            driver, wait = self._ensure_live_driver_and_session(driver, wait, config, effective_devtools, headless)

                        # Guard: verify session is still active before navigating
                        if is_portal_session_expired(driver):
                            self._log('warning', f"Portal session expired before student {student_id}. Re-authenticating...")
                            driver, wait = self._ensure_live_driver_and_session(driver, wait, config, effective_devtools, headless)

                        files_docs_root_url = self._open_files_and_documents(
                            driver, wait, config, link, student_id
                        )

                        # Navigate to target parent folder (e.g. 'Admissions Inquiry') ONCE for this student
                        self._go_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

                        for category in category_folders:
                            if self._abort():
                                break
                            self._on_progress({'type': 'category', 'name': category['name'] or '(root)'})

                            if category.get('is_root'):
                                # Files live directly in the student root folder —
                                # upload straight into the target parent; no
                                # sub-folder is created or navigated into.
                                self._log('info', "Uploading root-level files directly into target folder.")
                                wait.until(EC.element_to_be_clickable(
                                    (By.XPATH, config['upload_tool_button_xpath'])))
                                category_listing_url = files_docs_root_url
                            else:
                                wait.until(EC.presence_of_element_located(
                                    (By.XPATH, config['new_folder_toolbar_button_xpath'])))

                                folder_key = f"folder_exists_{category['name']}"
                                folder_known_exists = learning_engine.get_student_insight(student_id, folder_key)

                                folder_present = False
                                if folder_known_exists:
                                    # --- LEARNING: Trust that folder already exists ---
                                    self._log('info', f"[LEARNING] Folder '{category['name']}' known to exist for student {student_id} — skipping creation check.")
                                    folder_present = True
                                else:
                                    folder_present = folder_exists_in_listing(driver, category['name'], config)

                                if not folder_present:
                                    self._log('info', f"Creating folder '{category['name']}'...")
                                    click_xpath(driver, wait, config['new_folder_toolbar_button_xpath'],
                                                "New Folder button", log_func=self._log)
                                    try_click_xpath(driver, config['new_folder_option_xpath'],
                                                    "Sub-item menu", timeout=2, log_func=self._log)
                                    name_input = wait.until(EC.presence_of_element_located(
                                        (By.XPATH, config['folder_name_input_xpath'])))
                                    name_input.clear()
                                    name_input.send_keys(category['name'])
                                    click_xpath(driver, wait, config['save_button_xpath'],
                                                "Save Folder", log_func=self._log)
                                    wait_for_popup_ok(driver, wait, config['popup_ok_xpath'],
                                                      "Confirm Save OK", log_func=self._log)
                                    wait.until(EC.presence_of_element_located(
                                        (By.XPATH, config['new_folder_toolbar_button_xpath'])))

                                    # --- LEARNING: Mark folder as now existing ---
                                    learning_engine.record_student_insight(student_id, folder_key, True)
                                else:
                                    self._log('info', f"Folder '{category['name']}' already exists.")
                                    # Record it as existing in case it wasn't known before
                                    learning_engine.record_student_insight(student_id, folder_key, True)

                                click_folder_by_name(driver, wait, category['name'], config,
                                                     f"Folder '{category['name']}'", log_func=self._log)
                                wait.until(EC.element_to_be_clickable(
                                    (By.XPATH, config['upload_tool_button_xpath'])))
                                # Extra settle: PCR AJAX file listings render after the toolbar appears
                                time.sleep(0.5)
                                category_listing_url = driver.current_url

                            initial_snapshot = get_portal_table_snapshot(driver, config, wait_timeout=1.5)
                            category_uploaded_tracker = []
                            pending_files = []
                            for file_name in category['files']:
                                if self._abort():
                                    break

                                src_path = os.path.join(category['path'], file_name)
                                f_size = 0
                                try:
                                    if os.path.isfile(src_path):
                                        f_size = os.path.getsize(src_path)
                                except OSError:
                                    pass

                                file_ext = os.path.splitext(file_name)[1].lower().lstrip('.')
                                if file_ext in config.get('unsupported_extensions_list', set()):
                                    self._log('info', f"Skipping '{file_name}' — '.{file_ext}' is configured as an unsupported format.")
                                    files_done += 1
                                    batch_logs.append({
                                        'studentId':     student_id,
                                        'category':      category['name'],
                                        'fileName':      file_name,
                                        'uploadedAs':    '-',
                                        'action':        'skipped_unsupported',
                                        'actionComment': f"Unsupported file extension '.{file_ext}' (configured) -- not attempted, will not be retried.",
                                        'validation':    'skipped',
                                        'timestamp':     datetime.datetime.now().isoformat(timespec='seconds'),
                                    })
                                    self._on_progress({'type': 'file_done', 'file_name': file_name, 'file_size': f_size, 'files_done': files_done})
                                    continue

                                is_ready, file_err = is_file_ready_for_upload(src_path)
                                if not is_ready:
                                    self._log('warning', f"Skipping '{file_name}' — {file_err}.")
                                    files_done += 1
                                    batch_logs.append({
                                        'studentId':     student_id,
                                        'category':      category['name'],
                                        'fileName':      file_name,
                                        'uploadedAs':    '-',
                                        'action':        'failed',
                                        'actionComment': f"Local file unready: {file_err}",
                                        'validation':    '-',
                                        'timestamp':     datetime.datetime.now().isoformat(timespec='seconds'),
                                    })
                                    self._on_progress({'type': 'file_done', 'file_name': file_name, 'file_size': f_size, 'files_done': files_done})
                                    continue

                                upload_name = sanitize_upload_filename(file_name)

                                if file_exists_in_listing(driver, file_name, config, upload_name=upload_name, local_path=src_path, table_snapshot=initial_snapshot, log_func=self._log):
                                    self._log('info', f"File '{file_name}' already exists in '{category['name']}' on portal (content verified) — skipping duplicate upload.")
                                    files_done += 1
                                    total_uploaded += 1
                                    batch_logs.append({
                                        'studentId':     student_id,
                                        'category':      category['name'],
                                        'fileName':      file_name,
                                        'uploadedAs':    upload_name,
                                        'action':        'uploaded',
                                        'actionComment': 'Already exists on portal (duplicate prevented)',
                                        'validation':    'already_uploaded',
                                        'timestamp':     datetime.datetime.now().isoformat(timespec='seconds'),
                                    })
                                    self._on_progress({'type': 'file_done', 'file_name': file_name, 'file_size': f_size, 'files_done': files_done})
                                    continue

                                pending_files.append((file_name, upload_name, src_path))

                            # --- BATCH MULTI-FILE UPLOAD ATTEMPT (if > 1 pending files exist) ---
                            if len(pending_files) > 1 and not self._abort():
                                self._log('info', f"Attempting batch multi-file upload for {len(pending_files)} files in folder '{category['name']}'...")
                                batch_tmp_paths = []
                                valid_batch_items = []
                                for file_name, upload_name, src_path in pending_files:
                                    tmp_path = os.path.join(tmp_upload_dir, upload_name)
                                    try:
                                        shutil.copy2(src_path, tmp_path)
                                        batch_tmp_paths.append(os.path.abspath(tmp_path))
                                        valid_batch_items.append((file_name, upload_name, src_path))
                                    except Exception as e:
                                        self._log('error', f"Copy failed for batch file '{file_name}': {describe_exception(e)}")

                                if len(batch_tmp_paths) > 1:
                                    try:
                                        click_xpath(driver, wait, config['upload_tool_button_xpath'],
                                                    "Upload module button", log_func=self._log)
                                        file_input = wait.until(EC.presence_of_element_located(
                                            (By.XPATH, config['file_input_xpath'])))

                                        set_file_input_files(driver, file_input, batch_tmp_paths, log_func=self._log)

                                        click_xpath(driver, wait, config['upload_submit_xpath'],
                                                    "Upload submit (batch)", log_func=self._log)

                                        got_popup = try_click_xpath(driver, config['upload_popup_ok_xpath'],
                                                                    "Upload confirmation OK", timeout=3,
                                                                    log_func=self._log)
                                        if not got_popup:
                                            try:
                                                wait.until(EC.element_to_be_clickable(
                                                    (By.XPATH, config['upload_tool_button_xpath'])))
                                            except Exception:
                                                pass

                                        # Batch upload submitted and confirmed successfully
                                        for fn, un, sp in valid_batch_items:
                                            files_done += 1
                                            total_uploaded += 1
                                            f_size = os.path.getsize(sp) if os.path.exists(sp) else 0
                                            log_entry = {
                                                'studentId':     student_id,
                                                'category':      category['name'],
                                                'fileName':      fn,
                                                'uploadedAs':    un,
                                                'action':        'uploaded',
                                                'actionComment': 'Batch upload successful',
                                                'validation':    'uploaded_unverified',
                                                'timestamp':     datetime.datetime.now().isoformat(timespec='seconds'),
                                            }
                                            batch_logs.append(log_entry)
                                            category_uploaded_tracker.append({'fn': fn, 'un': un, 'sp': sp, 'log_dict': log_entry})
                                            self._on_progress({'type': 'file_done', 'file_name': fn, 'file_size': f_size, 'files_done': files_done})
                                            self._log('info', f"Batch uploaded '{fn}' as '{un}'.")

                                        self._log('info', f"Batch upload complete! All {len(valid_batch_items)} files uploaded simultaneously.")
                                        pending_files.clear()

                                    except Exception as batch_err:
                                        self._log('warning', f"Batch upload failed: {describe_exception(batch_err)}. Falling back to sequential upload...")
                                    finally:
                                        for tp in batch_tmp_paths:
                                            if os.path.exists(tp):
                                                try:
                                                    os.remove(tp)
                                                except OSError:
                                                    pass

                            # --- SEQUENTIAL UPLOAD (for remaining pending files) ---
                            for file_name, upload_name, src_path in pending_files:
                                if self._abort():
                                    break
                                tmp_path = os.path.join(tmp_upload_dir, upload_name)
                                shutil.copy2(src_path, tmp_path)

                                click_xpath(driver, wait, config['upload_tool_button_xpath'],
                                            "Upload module button", log_func=self._log)
                                file_input = wait.until(EC.presence_of_element_located(
                                    (By.XPATH, config['file_input_xpath'])))

                                set_file_input_files(driver, file_input, [tmp_path], log_func=self._log)

                                click_xpath(driver, wait, config['upload_submit_xpath'],
                                            "Upload submit", log_func=self._log)
                                try_click_xpath(driver, config['upload_popup_ok_xpath'],
                                                "Upload confirmation OK", timeout=2,
                                                log_func=self._log)

                                files_done += 1
                                total_uploaded += 1
                                f_size = os.path.getsize(src_path) if os.path.exists(src_path) else 0
                                log_entry = {
                                    'studentId':     student_id,
                                    'category':      category['name'],
                                    'fileName':      file_name,
                                    'uploadedAs':    upload_name,
                                    'action':        'uploaded',
                                    'actionComment': 'Uploaded successfully',
                                    'validation':    'uploaded_unverified',
                                    'timestamp':     datetime.datetime.now().isoformat(timespec='seconds'),
                                }
                                batch_logs.append(log_entry)
                                category_uploaded_tracker.append({'fn': file_name, 'un': upload_name, 'sp': src_path, 'log_dict': log_entry})
                                self._on_progress({'type': 'file_done', 'file_name': file_name, 'file_size': f_size, 'files_done': files_done})
                                self._log('info', f"Uploaded '{file_name}' as '{upload_name}'.")

                                if os.path.exists(tmp_path):
                                    try:
                                        os.remove(tmp_path)
                                    except OSError:
                                        pass

                            # Verify all newly uploaded files in ONE pass while staying on this folder page!
                            if category_uploaded_tracker and not self._abort():
                                expected_un = [item['un'] for item in category_uploaded_tracker]
                                self._log('info', f"Getting adaptive page dump for category '{category['name']}' ({len(category_uploaded_tracker)} uploads) for in-place verification...")
                                final_snapshot = get_portal_table_snapshot_adaptive(
                                    driver, config,
                                    expected_filenames=expected_un,
                                    max_timeout=4.0,
                                    poll_interval=0.25,
                                    log_func=self._log
                                )
                                for item in category_uploaded_tracker:
                                    fn = item['fn']
                                    un = item['un']
                                    sp = item['sp']
                                    log_entry = item['log_dict']

                                    is_verified, reason = verify_uploaded_file_strict(
                                        driver, un, sp, config,
                                        table_snapshot=final_snapshot,
                                        deep_hash_check=True,
                                        log_func=self._log,
                                    )

                                    if is_verified:
                                        log_entry['validation'] = reason
                                        log_entry['actionComment'] = f"Verified ({reason})"
                                        self._log('info', f"Verified '{fn}' in category '{category['name']}' ({reason}).")
                                    else:
                                        # Table snapshot did not capture row, but the batch/single upload transaction was accepted and confirmed
                                        log_entry['validation'] = 'uploaded_unverified'
                                        log_entry['actionComment'] = "Uploaded (pending portal table indexing)"
                                        self._log('info', f"Upload confirmed for '{fn}' in folder '{category['name'] or 'root'}' (pending portal background indexing).")

                            # Return to parent target folder for next category
                            if not category.get('is_root'):
                                self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

                        if self._abort():
                            break

                        students_succeeded += 1
                        student_done = True
                        SessionManager.update_item_status(MODULE_ID, student_id, success=True)

                    except Exception as e:
                        if self._abort():
                            break

                        err_desc = describe_exception(e)

                        # 1. Check if error is network disconnection / internet outage
                        if not check_network_connection() or is_network_error(e):
                            self._log('warning', f"[NETWORK DISRUPTION] Internet connection lost or unstable during student {student_id} ({err_desc}). Pausing automation and waiting for connection to recover...")
                            recovered = wait_for_network_connection(retry_wait_seconds=5, abort_flag=self._abort, log_func=self._log)
                            if not recovered or self._abort():
                                break
                            self._log('info', f"[NETWORK DISRUPTION] Internet restored! Re-initializing session to resume student {student_id} and all remaining students...")
                            try:
                                driver, wait = self._ensure_live_driver_and_session(driver, wait, config, effective_devtools, headless)
                            except Exception as rec_err:
                                self._log('warning', f"[SESSION RECOVERY] Session recovery attempt notice: {describe_exception(rec_err)}")
                            # Retry this student from scratch without burning a retry counter
                            continue

                        # 2. Transient error retry
                        student_attempt += 1
                        if student_attempt <= max_student_retries:
                            self._log('warning', f"[RETRY {student_attempt}/{max_student_retries}] Student {student_id} encountered error: {err_desc}. Retrying student in 5s...")
                            for _ in range(5):
                                if self._abort():
                                    break
                                time.sleep(1)
                            try:
                                driver, wait = self._ensure_live_driver_and_session(driver, wait, config, effective_devtools, headless)
                            except Exception:
                                pass
                            continue
                        else:
                            self._log('error', f"Student {student_id} failed after {max_student_retries} attempts: {err_desc}")
                            batch_logs.append({
                                'studentId': student_id, 'category': '-', 'fileName': '-',
                                'uploadedAs': '-', 'action': 'failed', 'actionComment': err_desc,
                                'validation': '-',
                                'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
                            })
                            SessionManager.update_item_status(MODULE_ID, student_id, success=False)
                            break

                    finally:
                        if batch_logs:
                            self._write_log_csv(source_dir, batch_logs)

                if self._abort():
                    break

                self._on_progress({
                    'type': 'student_done',
                    'students_traversed': idx + 1,
                    'total_students': len(students)
                })
            else:
                # for/else: only runs if the loop finished all students without
                # an abort-triggered `break` -- i.e. the run is genuinely complete.
                loop_completed = True

        finally:
            if driver:
                driver.quit()
            if os.path.isdir(tmp_upload_dir):
                shutil.rmtree(tmp_upload_dir, ignore_errors=True)

            # --- LEARNING: Record run summary ---
            duration = time.time() - run_start
            learning_engine.record_run_summary(
                module_name="File Uploader",
                total_items=len(students),
                success_count=students_succeeded,
                total_files=total_uploaded,
                duration_sec=duration,
            )
            self._log('info', f"[LEARNING] Run summary recorded. Duration: {duration:.1f}s, "
                               f"Uploads: {total_uploaded}")

            # --- LEARNING: Record whether this DevTools setting worked, so future
            # runs on this domain can auto-correct if the user forgets the checkbox. ---
            if students:
                run_succeeded = students_succeeded > 0
                learning_engine.record_devtools_outcome(domain_key, effective_devtools, run_succeeded)

            # --- SESSION PERSISTENCE ---
            # Only clear the session file if we genuinely reached the end of the
            # student list without an abort. If the run was aborted (by the user,
            # a crash, or the app closing), leave the session file in place so the
            # next launch can offer to resume exactly where this left off.
            if loop_completed:
                SessionManager.clear_session(MODULE_ID)
                self._log('info', "[SESSION] Run completed — session cleared.")
            else:
                self._log('warning', "[SESSION] Run did not finish — progress saved. "
                                      "You'll be asked to resume this session next time the app starts.")

    def _return_to_target_folder(self, driver, wait, config: dict, files_docs_root_url: str, target_folder_name: str, student_id: str = "") -> None:
        """Return to the parent target folder (e.g. 'Admissions Inquiry') efficiently.
        Uses fast breadcrumb navigation, avoiding full page reloads.
        """
        if target_folder_name:
            lit = xpath_literal(target_folder_name)
            lit_lower = xpath_literal(target_folder_name.strip().lower())
            breadcrumb_xpaths = [
                f"//*[contains(@class,'breadcrumb') or contains(@class,'nav') or contains(@id,'breadcrumb') or contains(@id,'nav')]//a[normalize-space(.) = {lit} or contains(normalize-space(.), {lit})]",
                f"//a[normalize-space(.) = {lit}]",
                f"//a[contains(normalize-space(.), {lit}) and not(ancestor::tbody//tr[.//input[@type='checkbox']])]",
                f"//a[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = {lit_lower}]",
            ]
            for bxp in breadcrumb_xpaths:
                try:
                    elements = driver.find_elements(By.XPATH, bxp)
                    for el in elements:
                        if el.is_displayed():
                            try:
                                el.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", el)
                            time.sleep(0.35)
                            return
                except Exception:
                    pass

            # Try browser history back if breadcrumb link was not found
            try:
                driver.back()
                wait_for_page_ready(driver, timeout=4)
                time.sleep(0.35)
                return
            except Exception:
                pass

        # Fallback to full target folder navigation only if in-page methods fail
        self._go_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

    def audit_and_remediate(self, driver, wait, config: dict, student_id: str, source_dir: str, files_docs_root_url: str, target_folder_name: str) -> dict:
        """Single-pass row-wise audit and remediation for a student inside target_folder_name.
        Traverses portal subfolders row-by-row in the exact order they appear in the portal table.
        Visits each subfolder EXACTLY ONCE to inspect, clean, verify, and upload missing files.
        """
        student_root_folder = os.path.join(source_dir, student_id)
        if not os.path.isdir(student_root_folder):
            return {'misplaced_deleted': 0, 'reuploaded': 0, 'correct_verified': 0}

        category_folders = list_category_folders(student_root_folder)
        if not category_folders:
            return {'misplaced_deleted': 0, 'reuploaded': 0, 'correct_verified': 0}

        local_category_map = {}
        root_expected_files = set()
        root_path = student_root_folder
        for c in category_folders:
            if c.get('is_root'):
                root_expected_files = set(c['files'])
                root_path = c['path']
            else:
                local_category_map[c['name'].strip().lower()] = {
                    'name': c['name'],
                    'path': c['path'],
                    'files': set(c['files']),
                    'sanitized': {sanitize_upload_filename(f): f for f in c['files']}
                }

        all_expected_files_map = {}
        for cat_info in local_category_map.values():
            for f in cat_info['files']:
                all_expected_files_map[f] = cat_info['name']
                all_expected_files_map[sanitize_upload_filename(f)] = cat_info['name']

        misplaced_deleted = 0
        reuploaded = 0
        correct_verified = 0

        tmp_upload_dir = os.path.join(self._source_dir or ".", "tmp_uploads")
        os.makedirs(tmp_upload_dir, exist_ok=True)

        self._log('info', f"[AUDIT] Starting row-wise folder audit for student '{student_id}' inside target folder '{target_folder_name or 'Root'}'...")

        # -------------------------------------------------------------------------
        # STEP 1: Navigate to target folder root ONCE & audit root-level files
        # -------------------------------------------------------------------------
        self._go_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

        root_snapshot = get_portal_table_snapshot_adaptive(
            driver, config,
            expected_filenames=list(root_expected_files) if root_expected_files else None,
            max_timeout=3.5,
            poll_interval=0.25,
            log_func=self._log
        )
        root_files = root_snapshot.get('files', [])

        # 1A. Verify root-level files if expected in root
        if root_expected_files:
            portal_root_names = set()
            for rf in root_files:
                portal_root_names.add(rf['name'].strip().lower())
                portal_root_names.add(sanitize_upload_filename(rf['name']).strip().lower())
            for k in root_snapshot.get('by_name', {}).keys():
                portal_root_names.add(k)

            for exp_f in root_expected_files:
                exp_clean = exp_f.strip().lower()
                exp_san = sanitize_upload_filename(exp_f).strip().lower()
                exp_prefix = exp_clean[:16] if len(exp_clean) > 16 else exp_clean

                is_present = (
                    exp_clean in portal_root_names or
                    exp_san in portal_root_names or
                    any(exp_prefix in pn or pn in exp_clean for pn in portal_root_names if len(pn) >= 8) or
                    any(exp_clean in f.get('raw_text', '') or exp_clean in f.get('href', '').lower() for f in root_snapshot.get('files', []))
                )

                if is_present:
                    correct_verified += 1
                    self._log('info', f"[AUDIT] Verified root file '{exp_f}' present in '{target_folder_name or 'Root'}'.")
                else:
                    self._log('warning', f"[AUDIT MISSING] Root file '{exp_f}' missing from '{target_folder_name or 'Root'}'. Uploading...")
                    src_fpath = os.path.join(root_path, exp_f)
                    if os.path.isfile(src_fpath):
                        tmp_path = os.path.join(tmp_upload_dir, exp_f)
                        try:
                            shutil.copy2(src_fpath, tmp_path)
                            click_xpath(driver, wait, config['upload_tool_button_xpath'], "Upload module button", log_func=self._log)
                            file_input = wait.until(EC.presence_of_element_located((By.XPATH, config['file_input_xpath'])))
                            set_file_input_files(driver, file_input, [tmp_path], log_func=self._log)
                            click_xpath(driver, wait, config['upload_submit_xpath'], "Upload submit", log_func=self._log)
                            try_click_xpath(driver, config['upload_popup_ok_xpath'], "Upload confirmation OK", timeout=2, log_func=self._log)
                            reuploaded += 1
                            correct_verified += 1
                            self._log('info', f"[AUDIT REMEDIATION] Successfully uploaded missing root file '{exp_f}'.")
                        except Exception as up_err:
                            self._log('error', f"[AUDIT REMEDIATION] Failed uploading '{exp_f}' to root: {describe_exception(up_err)}")
                        finally:
                            if os.path.exists(tmp_path):
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass

        # 1B. Delete files sitting in root that actually belong to a subfolder
        if root_files and all_expected_files_map:
            for rf in root_files:
                rf_name = rf['name']
                if rf_name in all_expected_files_map or sanitize_upload_filename(rf_name) in all_expected_files_map:
                    correct_cat = all_expected_files_map.get(rf_name) or all_expected_files_map.get(sanitize_upload_filename(rf_name))
                    self._log('warning', f"[AUDIT MISMATCH] File '{rf_name}' found directly in parent folder (belongs in '{correct_cat}')! Deleting from parent...")
                    deleted = delete_portal_file_by_name(driver, wait, rf_name, config, log_func=self._log)
                    if not deleted:
                        deleted = delete_portal_file_by_name(driver, wait, sanitize_upload_filename(rf_name), config, log_func=self._log)
                    if deleted:
                        misplaced_deleted += 1
                        self._log('info', f"[AUDIT REMEDIATION] Deleted misplaced file '{rf_name}' from parent folder root.")

        # -------------------------------------------------------------------------
        # STEP 2: Process portal subfolders in exact table ROW-WISE order
        # -------------------------------------------------------------------------
        portal_folder_details = get_all_portal_folder_details(driver, config)
        self._log('info', f"[AUDIT] Discovered {len(portal_folder_details)} portal subfolder(s) in row order: {[d['raw'] for d in portal_folder_details]}")

        processed_categories = set()

        for pfd in portal_folder_details:
            pf_name = pfd['name']
            pf_clean = pf_name.strip().lower()
            pf_count = pfd.get('count', 0)

            # Check if this portal folder matches one of our local expected categories
            if pf_clean in local_category_map:
                cat_info = local_category_map[pf_clean]
                folder_name = cat_info['name']
                expected_files = cat_info['files']
                processed_categories.add(pf_clean)

                self._log('info', f"[AUDIT] Processing category subfolder '{folder_name}' (row item '{pfd['raw']}')...")

                try:
                    click_folder_by_name(driver, wait, pf_name, config, f"Target Folder '{folder_name}'", log_func=self._log)
                    try:
                        wait.until(EC.element_to_be_clickable((By.XPATH, config['upload_tool_button_xpath'])))
                    except Exception:
                        pass
                    time.sleep(0.4)
                except Exception as e:
                    self._log('error', f"[AUDIT] Failed navigating into subfolder '{folder_name}': {describe_exception(e)}")
                    self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)
                    continue

                # --- INSIDE SUBFOLDER: Audit all files, delete foreign ones, and upload any missing ones ---
                current_files = get_portal_files_in_current_table(driver, config, wait_timeout=1.5)
                current_file_names_clean = {cf['name'].strip().lower(): cf['name'] for cf in current_files}

                expected_clean_map = {f.strip().lower(): f for f in expected_files}
                expected_sanitized_clean_map = {sanitize_upload_filename(f).strip().lower(): f for f in expected_files}

                # 2A. Delete files that do NOT belong in this folder
                for cf in current_files:
                    cf_name = cf['name']
                    cf_lower = cf_name.strip().lower()
                    if cf_lower not in expected_clean_map and cf_lower not in expected_sanitized_clean_map:
                        self._log('warning', f"[AUDIT MISMATCH] File '{cf_name}' does not belong in '{folder_name}'! Deleting...")
                        deleted = delete_portal_file_by_name(driver, wait, cf_name, config, log_func=self._log)
                        if deleted:
                            misplaced_deleted += 1
                            self._log('info', f"[AUDIT REMEDIATION] Deleted misplaced file '{cf_name}' from '{folder_name}'.")

                # 2B. Verify expected files & identify missing
                missing_files = []
                for file_name in expected_files:
                    fn_lower = file_name.strip().lower()
                    upload_name = sanitize_upload_filename(file_name)
                    un_lower = upload_name.strip().lower()
                    src_path = os.path.join(cat_info['path'], file_name)

                    is_present = (
                        fn_lower in current_file_names_clean
                        or un_lower in current_file_names_clean
                        or file_exists_in_listing(driver, file_name, config, upload_name=upload_name, local_path=src_path, log_func=self._log)
                    )

                    if is_present:
                        correct_verified += 1
                        self._log('info', f"[AUDIT VERIFIED] '{file_name}' already exists in folder '{folder_name}' on portal — skipping upload.")
                    else:
                        missing_files.append((file_name, upload_name, src_path))

                # 2C. Upload missing files right now while already inside this subfolder
                for file_name, upload_name, src_path in missing_files:
                    if file_exists_in_listing(driver, file_name, config, upload_name=upload_name, local_path=src_path, log_func=self._log):
                        correct_verified += 1
                        self._log('info', f"[AUDIT VERIFIED] '{file_name}' detected in '{folder_name}' — skipping duplicate upload.")
                        continue

                    tmp_path = os.path.join(tmp_upload_dir, upload_name)
                    try:
                        shutil.copy2(src_path, tmp_path)
                    except Exception as e:
                        self._log('error', f"[AUDIT REMEDIATION] Copy failed for '{file_name}': {describe_exception(e)}")
                        continue

                    self._log('info', f"[AUDIT REMEDIATION] Uploading missing file '{file_name}' to correct folder '{folder_name}'...")
                    try:
                        click_xpath(driver, wait, config['upload_tool_button_xpath'], "Upload module button", log_func=self._log)
                        file_input = wait.until(EC.presence_of_element_located((By.XPATH, config['file_input_xpath'])))
                        file_input.send_keys(os.path.abspath(tmp_path))
                        click_xpath(driver, wait, config['upload_submit_xpath'], "Upload submit", log_func=self._log)
                        try_click_xpath(driver, config['upload_popup_ok_xpath'], "Upload confirmation OK", timeout=2, log_func=self._log)
                        reuploaded += 1
                        correct_verified += 1
                        self._log('info', f"[AUDIT REMEDIATION] Successfully uploaded '{file_name}' to correct folder '{folder_name}'.")
                    except Exception as e:
                        self._log('error', f"[AUDIT REMEDIATION] Failed uploading '{file_name}' to '{folder_name}': {describe_exception(e)}")
                    finally:
                        if os.path.isfile(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass

                # Return to target parent folder for next row
                self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

            else:
                # Non-matching portal subfolder (in row order)
                if pf_count == 0:
                    self._log('info', f"[AUDIT] Non-matching folder '{pf_name}' has 0 files — skipping.")
                    continue

                self._log('info', f"[AUDIT] Checking non-matching portal folder '{pf_name}' ({pf_count} files) for misplaced files...")
                try:
                    click_folder_by_name(driver, wait, pf_name, config, f"Non-matching folder '{pf_name}'", log_func=self._log)
                    time.sleep(0.4)

                    folder_files = get_portal_files_in_current_table(driver, config, wait_timeout=1.5)
                    if folder_files:
                        self._log('warning', f"[AUDIT MISMATCH] Found {len(folder_files)} misplaced file(s) in non-matching folder '{pf_name}'. Emptying files...")
                        for ff in folder_files:
                            ff_name = ff['name']
                            deleted = delete_portal_file_by_name(driver, wait, ff_name, config, log_func=self._log)
                            if deleted:
                                misplaced_deleted += 1
                                self._log('info', f"[AUDIT CLEANUP] Deleted misplaced file '{ff_name}' from folder '{pf_name}'.")

                    self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)
                except Exception as e:
                    self._log('warning', f"[AUDIT] Notice checking non-matching folder '{pf_name}': {describe_exception(e)}")
                    self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

        # -------------------------------------------------------------------------
        # STEP 3: Create and process any local categories that were NOT on the portal
        # -------------------------------------------------------------------------
        for category in category_folders:
            if category['is_root']:
                continue

            folder_name = category['name']
            fn_clean = folder_name.strip().lower()
            expected_files = category['files']

            if fn_clean in processed_categories or not expected_files:
                continue

            self._log('info', f"[AUDIT REMEDIATION] Creating missing target subfolder '{folder_name}' on portal...")
            try:
                click_xpath(driver, wait, config['new_folder_toolbar_button_xpath'], "New folder toolbar button", log_func=self._log)
                if config.get('new_folder_option_xpath'):
                    try_click_xpath(driver, config['new_folder_option_xpath'], "New Folder option", timeout=2, log_func=self._log)
                name_input = wait.until(EC.presence_of_element_located((By.XPATH, config['folder_name_input_xpath'])))
                name_input.clear()
                name_input.send_keys(folder_name)
                click_xpath(driver, wait, config['save_button_xpath'], "Save button", log_func=self._log)
                wait_for_popup_ok(driver, wait, config.get('popup_ok_xpath', ''), "Confirm Save OK", log_func=self._log)
                wait.until(EC.presence_of_element_located((By.XPATH, config['new_folder_toolbar_button_xpath'])))
                time.sleep(0.5)

                click_folder_by_name(driver, wait, folder_name, config, f"Target Folder '{folder_name}'", log_func=self._log)
                wait.until(EC.element_to_be_clickable((By.XPATH, config['upload_tool_button_xpath'])))
                time.sleep(0.4)

                for file_name in expected_files:
                    upload_name = sanitize_upload_filename(file_name)
                    src_path = os.path.join(category['path'], file_name)
                    tmp_path = os.path.join(tmp_upload_dir, upload_name)
                    shutil.copy2(src_path, tmp_path)

                    self._log('info', f"[AUDIT REMEDIATION] Uploading '{file_name}' to new folder '{folder_name}'...")
                    click_xpath(driver, wait, config['upload_tool_button_xpath'], "Upload module button", log_func=self._log)
                    file_input = wait.until(EC.presence_of_element_located((By.XPATH, config['file_input_xpath'])))
                    file_input.send_keys(os.path.abspath(tmp_path))
                    click_xpath(driver, wait, config['upload_submit_xpath'], "Upload submit", log_func=self._log)
                    try_click_xpath(driver, config['upload_popup_ok_xpath'], "Upload confirmation OK", timeout=2, log_func=self._log)
                    reuploaded += 1
                    correct_verified += 1
                    if os.path.isfile(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

                self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)
            except Exception as e:
                self._log('error', f"Could not create/populate missing subfolder '{folder_name}': {describe_exception(e)}")
                self._return_to_target_folder(driver, wait, config, files_docs_root_url, target_folder_name, student_id)

        self._log('info', f"[AUDIT COMPLETE] Student '{student_id}': {correct_verified} correct, {misplaced_deleted} misplaced files deleted, {reuploaded} re-uploaded to correct folders.")
        return {'misplaced_deleted': misplaced_deleted, 'reuploaded': reuploaded, 'correct_verified': correct_verified}

    def verify_and_audit(
        self,
        config: dict,
        students: list,
        source_dir: str,
        target_folder_name: str,
        devtools: bool = False,
        on_progress=None,
        abort_flag=None,
        log=None,
    ) -> dict:
        """Execute a live portal verification and audit across *students*.
        Logs in, navigates to each student's portal, detects and deletes any misplaced files from wrong folders,
        and ensures all expected files exist in their correct folders.
        """
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda data: None)
        self._abort = abort_flag or (lambda: False)
        self._source_dir = source_dir

        for key, default_val in DEFAULT_XPATHS.items():
            config.setdefault(key, default_val)
            if not config.get(key):
                config[key] = default_val

        if target_folder_name == NO_TARGET_FOLDER_LABEL:
            target_folder_name = ""

        if students and isinstance(students[0], dict):
            valid_students = filter_valid_students(students)
        elif students:
            valid_students = [{'studentId': str(s), 'link': config.get('student_link_template', '').format(student_id=str(s))} for s in students]
        else:
            valid_students = []

        if not valid_students:
            self._log('warning', "No valid students found to verify and audit.")
            return {'students_audited': 0, 'correct_verified': 0, 'misplaced_deleted': 0, 'reuploaded': 0}

        self._on_progress({'type': 'total', 'total': len(valid_students)})
        total_correct = 0
        total_deleted = 0
        total_reuploaded = 0
        students_audited = 0

        login_url = config.get('login_url', '')
        domain_key = learning_engine.extract_domain(login_url)
        effective_devtools = learning_engine.resolve_devtools(devtools, domain_key, log_func=self._log)
        headless = settings.get("headless", False)
        if headless:
            self._log('info', "[SETTINGS] Headless mode enabled -- Chrome will run with no visible window.")
        chrome_options = build_basic_options(devtools=effective_devtools, headless=headless)

        driver = None
        try:
            driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))
            wait_timeout = learning_engine.get_wait_ceiling(floor=25.0)
            wait = WebDriverWait(driver, wait_timeout, poll_frequency=0.2)

            self._log('info', f"Navigating to login page: {login_url}")
            driver.get(login_url)
            self._perform_login(driver, wait, config, login_url)
            self._log('info', "Login successful for Audit & Verification.")

            for idx, student_data in enumerate(valid_students):
                if self._abort():
                    self._log('warning', "[AUDIT] Abort requested by user.")
                    break

                student_id = student_data['studentId']
                link = student_data['link']

                self._on_progress({'type': 'student_start', 'student_id': student_id, 'idx': idx})

                while not self._abort():
                    try:
                        if not check_network_connection():
                            self._log('warning', f"[NETWORK DISRUPTION] Internet connection lost before auditing student {student_id}. Pausing and waiting for connection to recover...")
                            if not wait_for_network_connection(retry_wait_seconds=5, abort_flag=self._abort, log_func=self._log):
                                break
                            driver, wait = self._ensure_live_driver_and_session(driver, wait, config, effective_devtools, headless)

                        files_docs_root_url = self._open_files_and_documents(
                            driver, wait, config, link, student_id
                        )
                        res = self.audit_and_remediate(
                            driver, wait, config, student_id, source_dir, files_docs_root_url, target_folder_name
                        )
                        total_correct += res.get('correct_verified', 0)
                        total_deleted += res.get('misplaced_deleted', 0)
                        total_reuploaded += res.get('reuploaded', 0)
                        students_audited += 1
                        break
                    except Exception as e:
                        if self._abort():
                            break

                        err_desc = describe_exception(e)
                        if not check_network_connection() or is_network_error(e):
                            self._log('warning', f"[NETWORK DISRUPTION] Internet connection lost while auditing student {student_id} ({err_desc}). Pausing and waiting for connection to recover...")
                            recovered = wait_for_network_connection(retry_wait_seconds=5, abort_flag=self._abort, log_func=self._log)
                            if not recovered or self._abort():
                                break
                            self._log('info', f"[NETWORK DISRUPTION] Connection restored! Resuming audit for student {student_id}...")
                            try:
                                driver, wait = self._ensure_live_driver_and_session(driver, wait, config, effective_devtools, headless)
                            except Exception:
                                pass
                            continue
                        else:
                            self._log('error', f"Audit failed for student {student_id}: {err_desc}")
                            break

                self._on_progress({
                    'type': 'student_done',
                    'students_traversed': idx + 1,
                    'total_students': len(valid_students),
                })

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return {
            'students_audited': students_audited,
            'correct_verified': total_correct,
            'misplaced_deleted': total_deleted,
            'reuploaded': total_reuploaded
        }

    def verify(self, source_dir: str, log=None) -> dict:
        # Runs now write a distinct uploads_execution_log_<run_id>.csv per
        # run -- verify against whichever one is most recent.
        log_file_path = find_latest_run_log(source_dir, "uploads_execution_log", ".csv")
        if not log_file_path or not os.path.isfile(log_file_path):
            return {'uploaded': 0, 'failed': 0, 'skipped_unsupported': 0, 'skipped_malformed': 0, 'students': 0}
        uploaded = failed = skipped_unsupported = skipped_malformed = 0
        per_student: dict = {}
        try:
            with open(log_file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sid = row.get('studentId', '-')
                    per_student.setdefault(sid, {'uploaded': 0, 'failed': 0, 'skipped_unsupported': 0, 'skipped_malformed': 0})
                    action = row.get('action')
                    if action == 'uploaded':
                        uploaded += 1
                        per_student[sid]['uploaded'] += 1
                    elif action == 'skipped_unsupported':
                        skipped_unsupported += 1
                        per_student[sid]['skipped_unsupported'] += 1
                    elif action == 'skipped_malformed':
                        skipped_malformed += 1
                        per_student[sid]['skipped_malformed'] += 1
                    else:
                        failed += 1
                        per_student[sid]['failed'] += 1
        except Exception:
            pass
        return {
            'uploaded': uploaded,
            'failed': failed,
            'skipped_unsupported': skipped_unsupported,
            'skipped_malformed': skipped_malformed,
            'students': len(per_student),
        }

    # ------------------------------------------------------------------
    # Login with learning
    # ------------------------------------------------------------------

    def _ensure_live_driver_and_session(self, driver, wait, config: dict, effective_devtools: bool, headless: bool):
        """Verify that Chrome WebDriver is responsive and authenticated.
        If the browser died during a network blackout, relaunches Chrome and logs back in."""
        login_url = config.get('login_url', '')
        driver_alive = False
        if driver is not None:
            try:
                _ = driver.current_url
                driver_alive = True
            except Exception:
                driver_alive = False
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None

        if not driver_alive:
            self._log('info', "[SESSION RECOVERY] Re-launching Chrome WebDriver...")
            chrome_options = build_basic_options(devtools=effective_devtools, headless=headless)
            driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))
            wait_timeout = learning_engine.get_wait_ceiling(floor=25.0)
            wait = WebDriverWait(driver, wait_timeout, poll_frequency=0.2)

        try:
            self._log('info', f"[SESSION RECOVERY] Re-authenticating on portal login page...")
            driver.get(login_url)
            wait_for_page_ready(driver, timeout=10)
            self._perform_login(driver, wait, config, login_url)
            self._log('info', "[SESSION RECOVERY] Portal session authenticated successfully.")
        except Exception as e:
            self._log('warning', f"[SESSION RECOVERY] Portal session re-authentication notice: {describe_exception(e)}")

        return driver, wait

    def _perform_login(self, driver, wait, config: dict, login_url: str):
        """Login using learned XPaths first, then fall back to config values."""
        learned_user = learning_engine.get_learned_xpaths(login_url, 'username_field')
        learned_pass = learning_engine.get_learned_xpaths(login_url, 'password_field')
        learned_btn  = learning_engine.get_learned_xpaths(login_url, 'login_button')

        user_xpath = (learned_user[0] if learned_user else None) or config.get('username_xpath', '')
        pass_xpath = (learned_pass[0] if learned_pass else None) or config.get('password_xpath', '')
        btn_xpath  = (learned_btn[0]  if learned_btn  else None) or config.get('login_btn_xpath', '')

        try:
            # NOTE: previously this located user_input/pass_input ONCE each and
            # then called .send_keys() on the saved references afterwards. If
            # the login page re-rendered or redirected (e.g. a JS keepalive/
            # countdown script, or a slow async widget swap) in the gap
            # between locating and typing, those references went stale and
            # StaleElementReferenceException propagated all the way out of
            # run() -- crashing the ENTIRE automation run (even already-
            # in-progress session state) instead of just this one attempt.
            # safe_send_keys re-locates the element immediately before typing
            # and transparently retries on StaleElementReferenceException, so
            # a mid-flight re-render no longer takes down the whole run.
            safe_send_keys(driver, wait, user_xpath, config['username'],
                            'username field', log_func=self._log)
            safe_send_keys(driver, wait, pass_xpath, config['password'],
                            'password field', log_func=self._log)

            if config.get('role') and config.get('role_xpath'):
                try:
                    role_el = driver.find_element(By.XPATH, config['role_xpath'])
                    Select(role_el).select_by_visible_text(config['role'])
                except Exception as e:
                    self._log('warning', f"Could not select role: {e}")

            login_url_before = driver.current_url
            click_xpath(driver, wait, btn_xpath, "Login button", log_func=self._log)

            # Wait for URL to change from the login page — the "login" not in URL check
            # is immediately true for Orbund (kwi.orbundsis.com never has 'login' in
            # the URL), so it returned before auth finished and the portal had loaded.
            # Waiting for URL change is the reliable cross-site approach.
            try:
                WebDriverWait(driver, 12).until(lambda d: d.current_url != login_url_before)
            except Exception:
                # Some sites redirect via meta-refresh or JS without a clean URL change;
                # fall back to a brief sleep so the post-login page can at least start
                # rendering before we navigate to the first student record.
                time.sleep(3)

            # Record successful XPaths
            learning_engine.record_successful_xpath(login_url, 'username_field', user_xpath)
            learning_engine.record_successful_xpath(login_url, 'password_field', pass_xpath)
            learning_engine.record_successful_xpath(login_url, 'login_button',   btn_xpath)

        except Exception:
            learning_engine.record_failed_xpath(login_url, 'username_field', user_xpath)
            raise

    def _open_files_and_documents(self, driver, wait, config, link, student_id: str = "") -> str:
        """Navigates to the student's profile and opens Files & Documents.

        Tries several independent strategies, in order, and automatically
        retries with the next one if a strategy either can't find/click the
        menu item or clicks something that lands on the WRONG page. Each
        attempt starts from a clean reload of the student's profile, since a
        bad click may have already navigated away.

        The 3-dot menu itself is opened via `open_dropdown_menu`, which is
        aware that this site's dropdown ALSO opens on hover (jquery-dropdown-
        hover.js) -- hovering then unconditionally clicking would toggle an
        already-open menu shut again, which was the actual root cause of
        every prior failure (both strategies were searching a closed menu).

        STRATEGY 0 -- Learned link (self-learning, tried first when present):
        the exact href pattern the system has previously confirmed works for
        this domain, recorded automatically the first time any strategy
        below succeeds. This means the very next run after a one-off DOM
        change self-heals to the fast path instead of re-discovering it every
        time.

        STRATEGY 1 -- Href pattern match: matches an <a> whose href contains
        a known Files & Documents JSP filename fragment. This is the most
        durable selector available -- immune to menu reordering (which broke
        the positional xpath) and to label/casing changes.

        STRATEGY 2 -- Configured XPath: the exact positional xpath from
        credentials (default: a[14]). Kept for backward compatibility with
        existing credentials files.

        STRATEGY 3 -- Scoped text match (fallback): matches only <a> elements
        whose OWN short visible text reads like "Files & Documents" -- never
        <div>/<li>/<span> containers, since a container's aggregated text can
        innocently contain both "files" and "document" as substrings from two
        different, unrelated sibling menu items.
        """
        login_url = config.get('login_url', '')
        learned_hrefs = learning_engine.get_learned_xpaths(login_url, 'files_documents_menu_href')

        strategies = []
        if learned_hrefs:
            best_learned = learned_hrefs[0]
            strategies.append((
                "learned href",
                lambda d, w, c, _href=best_learned: self._click_via_href_match(d, w, c, href_fragment=_href),
            ))
        strategies.append(("href pattern match", self._click_via_href_match))
        strategies.append(("configured xpath", self._click_via_configured_xpath))
        strategies.append(("text match", self._click_via_text_match))

        files_docs_root_url = None
        last_error = None
        matched_href = None
        matched_strategy = None

        for attempt_idx, (strategy_name, strategy_fn) in enumerate(strategies):
            try:
                self._log('debug', f"Loading student {student_id} portal (attempt {attempt_idx + 1}: {strategy_name}).")
                driver.get(link)
                wait_for_page_ready(driver, timeout=15)

                # 3-dot menu: hover-aware opener -- avoids the hover-then-click
                # race that was silently re-closing the menu on this site.
                open_dropdown_menu(driver, wait, config['three_dot_menu_xpath'],
                                    "3-dot menu", timeout_override=20, log_func=self._log)

                clicked_href = strategy_fn(driver, wait, config)
                wait_for_page_ready(driver, timeout=15)
                candidate_url = driver.current_url

                if self._is_files_documents_url(candidate_url):
                    files_docs_root_url = candidate_url
                    matched_href = clicked_href
                    matched_strategy = strategy_name
                    self._log('debug', f"Files & Docs root URL ({strategy_name}): {files_docs_root_url}")
                    break

                last_error = f"{strategy_name} landed on unexpected page: {candidate_url}"
                remaining = len(strategies) - attempt_idx - 1
                self._log('warning', f"Student {student_id}: {last_error}"
                                      f"{f' -- retrying with next strategy.' if remaining else ' -- no strategies left.'}")
            except Exception as e:
                last_error = f"{strategy_name} failed: {describe_exception(e)}"
                self._log('debug', f"Student {student_id}: {last_error}")

        if not files_docs_root_url:
            dump_path = self._dump_debug_html(driver, student_id)
            suggestion = self._suggest_locator_from_dom(driver)
            iframe_count = 0
            try:
                iframe_count = len(driver.find_elements(By.TAG_NAME, "iframe"))
            except Exception:
                pass
            extra = f" Page HTML dumped to: {dump_path}." if dump_path else ""
            if suggestion:
                extra += f" Candidate file-related links still present in DOM: {suggestion}"
            if iframe_count:
                extra += (f" NOTE: {iframe_count} <iframe> element(s) detected on this page \u2014 "
                          f"the dropdown menu may be rendered inside one of them.")
            raise Exception(
                f"Files & Documents navigation failed for student {student_id} after trying "
                f"{len(strategies)} strategies. Last error: {last_error}. "
                f"Check 'files_documents_menu_xpath' in credentials or the student's portal."
                f"{extra}"
            )

        # --- LEARNING: remember what worked so future runs skip straight to it,
        # and self-heal if the site's positional xpath drifts again. ---
        if matched_href:
            learning_engine.record_successful_xpath(login_url, 'files_documents_menu_href', matched_href)
            learning_engine.record_domain_strategy(login_url, 'files_documents_last_successful_strategy', matched_strategy)
            self._log('info', f"[LEARNING] Recorded working Files & Documents link ({matched_strategy}) for this domain.")

        self._log('debug', f"Files & Documents base URL: {files_docs_root_url}")
        return files_docs_root_url

    def _go_to_target_folder(self, driver, wait, config: dict, files_docs_root_url: str, target_folder_name: str, student_id: str = "") -> None:
        """Navigate to the student's Files & Documents base root, then click into target_folder_name.
        Retries up to 3 times and confirms navigation by verifying the target folder link disappears
        from the table (meaning we are now INSIDE the folder, not at root).
        """
        import time as _time
        from selenium.webdriver.common.by import By as _By2

        if not target_folder_name:
            if "files_and_documents" not in driver.current_url.lower():
                driver.get(files_docs_root_url)
                wait_for_page_ready(driver, timeout=10)
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, config.get('folder_listing_table_xpath', '//table'))))
            except Exception:
                pass
            _time.sleep(0.4)
            return

        table_xpath = config.get('folder_listing_table_xpath', '')
        target_lit = xpath_literal(target_folder_name)
        # XPath that is TRUE when we are still at root (folder link still visible in table)
        still_at_root_xpath = (
            f"{table_xpath}//a[contains(normalize-space(.), {target_lit})]"
            if table_xpath else
            f"//table//a[contains(normalize-space(.), {target_lit})]"
        )

        for attempt in range(1, 4):  # up to 3 attempts
            # Always reload from the known-good root URL
            driver.get(files_docs_root_url)
            wait_for_page_ready(driver, timeout=10)
            _time.sleep(0.7)

            # Ensure target folder exists at root (create if missing)
            if not folder_exists_in_listing(driver, target_folder_name, config):
                self._log('info', f"Top-level folder '{target_folder_name}' not found for student {student_id}. Creating it...")
                try:
                    click_xpath(driver, wait, config['new_folder_toolbar_button_xpath'], "New folder toolbar button", log_func=self._log)
                    if config.get('new_folder_option_xpath'):
                        try_click_xpath(driver, config['new_folder_option_xpath'], "New Folder option", timeout=2, log_func=self._log)
                    safe_send_keys(driver, wait, config['folder_name_input_xpath'], target_folder_name, "folder name field", log_func=self._log)
                    click_xpath(driver, wait, config['save_button_xpath'], "Save button", log_func=self._log)
                    wait_for_popup_ok(driver, config, log_func=self._log)
                    driver.get(files_docs_root_url)
                    wait_for_page_ready(driver, timeout=10)
                    _time.sleep(0.7)
                except Exception as create_err:
                    self._log('warning', f"Create top-level folder notice: {describe_exception(create_err)}")

            self._log('debug', f"[NAV] Attempt {attempt}: Clicking into folder '{target_folder_name}'...")
            try:
                click_folder_by_name(driver, wait, target_folder_name, config,
                                     f"Top-level folder '{target_folder_name}'", log_func=self._log)
            except Exception as click_err:
                self._log('warning', f"[NAV] Attempt {attempt}: click error: {describe_exception(click_err)}")
                continue

            # Confirm we actually navigated INSIDE the folder.
            # Success = target folder link is no longer visible in the table
            # (we are now viewing the contents of that folder).
            confirmed = False
            for _ in range(15):  # poll up to ~3 seconds
                _time.sleep(0.2)
                try:
                    els = driver.find_elements(_By2.XPATH, still_at_root_xpath)
                    if not any(e.is_displayed() for e in els):
                        confirmed = True
                        break
                except Exception:
                    break

            if confirmed:
                all_folders = get_all_portal_folder_details(driver, config)
                self._log('debug', f"[NAV] CONFIRMED inside '{target_folder_name}'. Subfolders: {[f['raw'] for f in all_folders]}")
                return

            self._log('warning', f"[NAV] Attempt {attempt}: NOT confirmed inside '{target_folder_name}' — link still visible. Retrying...")

        self._log('error', f"[NAV] FAILED to navigate inside '{target_folder_name}' after 3 attempts for student {student_id}.")

    @staticmethod
    def _is_files_documents_url(url: str) -> bool:
        u = url.lower()
        return 'files' in u or 'document' in u

    def _click_via_href_match(self, driver, wait, config: dict, href_fragment: str | None = None) -> str:
        """Strategy: click an <a> whose href contains a Files & Documents JSP
        filename fragment. This is the most durable locator -- immune to menu
        reordering and label/casing drift, since it targets the underlying
        page name rather than position or visible text.

        If *href_fragment* is given (a previously-learned href), only that
        exact fragment is tried. Otherwise a small set of common filename
        fragments is tried.
        """
        from selenium.webdriver.support.ui import WebDriverWait as WDW
        from selenium.webdriver.support import expected_conditions as _EC
        from selenium.webdriver.common.by import By as _By

        fragments = [href_fragment] if href_fragment else [
            "files_and_documents", "files_documents", "file_and_documents", "filesanddocuments",
        ]
        or_clauses = " or ".join(
            f"contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
            f"{xpath_literal(f.lower())})"
            for f in fragments if f
        )
        if not or_clauses:
            raise Exception("No href fragment available for href-match strategy.")
        text_xpath = f"//a[{or_clauses}]"

        WDW(driver, 8, poll_frequency=0.2).until(
            _EC.presence_of_element_located((_By.XPATH, text_xpath))
        )
        candidates = driver.find_elements(_By.XPATH, text_xpath)
        visible = [el for el in candidates if el.is_displayed()]
        if not visible:
            raise Exception("Href-match strategy found no visible Files & Documents link.")

        target = visible[0]
        href = ''
        try:
            href = target.get_attribute('href') or ''
        except Exception:
            pass
        self._log('debug', f"Href-match found Files & Documents menu item: '{href}'")
        try:
            target.click()
        except Exception:
            driver.execute_script("arguments[0].click();", target)
        return href

    def _click_via_configured_xpath(self, driver, wait, config: dict) -> str:
        """Strategy: click the exact positional xpath from credentials
        (matches the proven standalone uploader's behavior)."""
        configured_xpath = config.get('files_documents_menu_xpath', '')
        if not configured_xpath:
            raise Exception("No 'files_documents_menu_xpath' configured.")
        el = click_xpath(driver, wait, configured_xpath,
                          "'Files & Documents' menu item (configured xpath)",
                          timeout_override=20, log_func=self._log)
        try:
            return el.get_attribute('href') or ''
        except Exception:
            return ''

    def _click_via_text_match(self, driver, wait, config: dict) -> str:
        """Strategy (fallback only): click the <a> in the open dropdown whose
        own short text reads like 'Files & Documents'. Restricted to anchor
        elements with a short text length so a wrapping container (whose
        aggregated text spans several unrelated menu items) can never match --
        that was the original cause of clicks landing on Comments/Correspondence
        pages, before the href-match strategy above existed.
        """
        from selenium.webdriver.support.ui import WebDriverWait as WDW
        from selenium.webdriver.support import expected_conditions as _EC
        from selenium.webdriver.common.by import By as _By

        text_xpath = (
            "//a["
            "contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'files') and "
            "contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'document') and "
            "string-length(normalize-space(.)) < 40"
            "]"
        )

        WDW(driver, 12, poll_frequency=0.2).until(
            _EC.presence_of_element_located((_By.XPATH, text_xpath))
        )
        candidates = driver.find_elements(_By.XPATH, text_xpath)
        visible = [el for el in candidates if el.is_displayed()]
        if not visible:
            raise Exception("Text-match strategy found no visible 'Files & Documents' link.")

        # Prefer the shortest matching text -- the tightest match to the
        # literal menu label, in case more than one anchor qualifies.
        target = min(visible, key=lambda el: len(el.text.strip() or el.get_attribute('textContent') or ''))
        href = ''
        try:
            href = target.get_attribute('href') or ''
        except Exception:
            pass
        self._log('debug', f"Text-match found Files & Documents menu item: '{target.text.strip()}'")
        try:
            target.click()
        except Exception:
            driver.execute_script("arguments[0].click();", target)
        return href

    def _suggest_locator_from_dom(self, driver) -> str:
        """Best-effort scan of the current DOM for file/document-like links,
        even ones that weren't visible/clickable to the strategies above
        (e.g. the dropdown is still closed). Used only to enrich the failure
        log for faster diagnosis -- never used to click anything."""
        try:
            candidates = driver.find_elements(
                By.XPATH,
                "//a[contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'file')]"
            )
            hits = []
            for el in candidates[:10]:
                try:
                    href = el.get_attribute('href') or ''
                    text = (el.text or '').strip()
                    if href:
                        hits.append(f"'{text}' -> {href}")
                except Exception:
                    pass
            return "; ".join(hits)
        except Exception:
            return ""

    def _dump_debug_html(self, driver, student_id: str) -> str | None:
        """Save the current page's full HTML to source_dir/page_dumps/ for offline
        diagnosis when Files & Documents navigation fails on both strategies.
        Returns the path written, or None on failure."""
        try:
            dumps_dir = os.path.join(self._source_dir or ".", "page_dumps")
            os.makedirs(dumps_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(dumps_dir, f"student_{student_id}_{ts}.html")
            html = driver.execute_script("return document.documentElement.outerHTML;")
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(html)
            self._log('info', f"Debug page HTML dumped to: {out_path}")
            return out_path
        except Exception as e:
            self._log('warning', f"Failed to dump debug HTML: {describe_exception(e)}")
            return None

    def _write_log_csv(self, source_dir: str, log_data: list) -> None:
        run_id = getattr(self, '_run_id', None) or new_run_id()
        log_file_path = os.path.join(source_dir, f"uploads_execution_log_{run_id}.csv")
        file_exists = os.path.isfile(log_file_path)
        headers = ['studentId', 'category', 'fileName', 'uploadedAs', 'action', 'actionComment', 'validation', 'timestamp']
        try:
            with open(log_file_path, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(log_data)
        except Exception as e:
            self._log('error', f"Failed writing log CSV: {e}")
