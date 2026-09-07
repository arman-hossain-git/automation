"""
modules/file_exporter.py
========================
Universal, site-agnostic File Exporter module.

Always auto-detects what kind of page it's looking at, per record:

  1. 'mypath_documents'    — MyPath-style Documents tab: h2-sectioned MUI
                             tables (Admissions / Graduation / Other ...),
                             each row's filename is a dialog-trigger button
                             that opens a preview modal or triggers a direct
                             download icon button.

  2. 'generate_documents'  — MyPath-style: dropdown selects a program, Generate
                             buttons create PDFs in a modal. Ported faithfully from
                             dynamic_export_work.py.

  3. 'static_links'        — Scrapes existing download links from any structural
                             container on the page. Understands four container types:
                               • RadDock widgets  (PCR Educator)
                               • Tab panels        (nav-tabs / tab-content / role=tabpanel)
                               • Section headings  (<h2>/<h3> followed by a table)
                               • Generic tables    (fallback whole-page scrape)

There is no user/credentials-configurable "interaction mode" any more -- the
exporter decides for itself, per record, which of the above applies. Any
'interaction_mode=' line left over in an old credentials.txt is ignored.

Learning integration:
  • Per-domain structural signature: which container types exist, what their
    names/selectors are, how many files each yielded last time.
  • Per-domain segment map: {segment_name -> css/xpath hint} so the system knows
    EXACTLY where to look on the next run without re-scanning the whole page.
  • Tab-click tracking: remembers which tabs need to be clicked and in what order.
  • Per-student file-count history: used for ETA estimation.
  • Interaction-mode memory: the auto-detected mode is recorded per domain and
    tried FIRST on the next run as a fast path; if it ever finds 0 files (e.g.
    the site's layout changed), the exporter automatically falls back to full
    re-detection for that record rather than giving up.
"""

import os
import re
import csv
import time
import base64
import hashlib
import logging
import datetime
import urllib.parse

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from core.chrome_driver import launch_chrome_driver, build_download_options
from core.selenium_helpers import (
    set_download_behavior,
    wait_for_downloads_to_finish,
    force_download_via_fetch,
    wait_for_page_ready,
)
from core.utils import sanitize_filename, filter_valid_students, build_output_path
from core.learning_system import learning_engine

_log_mod = logging.getLogger(__name__)

GENERATE_PREFIX_RE = re.compile(r'^generate\s+', re.IGNORECASE)

KNOWN_FILE_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'txt', 'rtf',
    'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'heic',
    'zip', 'rar', '7z', 'mp3', 'mp4', 'mov', 'avi', 'wav'
}

MIME_TO_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
    "image/tiff": ".tiff", "application/pdf": ".pdf",
    "text/plain": ".txt", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
}

DEFAULT_EXCLUDE_TEXT = {
    'edit', 'delete', 'remove', 'next', 'previous', 'prev', 'first', 'last',
    'logout', 'log out', 'home', 'profile', 'print', 'close', 'cancel',
    'view profile', 'back', 'back to top', 'settings', 'menu', '...', '»', '«',
    'documents', 'document', 'academics', 'programs', 'dashboard', 'overview'
}

DOWNLOAD_PATH_HINTS = ('/download', '/attachment', '/export', 'download.php', 'getfile', 'file.ashx', 'file.aspx', 'download=')

GENERATE_PREFIX_RE = re.compile(r'^generate\s+', re.IGNORECASE)

# Broad heuristic for standalone "produces a document when clicked" buttons
# that don't fit either known structured pattern (a MyPath Documents table
# row, or a dropdown+Generate-buttons page) -- e.g. a bare 'Ledger PDF'
# button on a Finances tab. Matched against a button's OWN short text only
# (never an aggregated container's text -- see the earlier lesson about
# that causing false matches on unrelated menu items).
GENERIC_GENERATE_TEXT_RE = re.compile(
    r'\b(pdf|ledger|statement|transcript|receipt|invoice|report|packet|1098|w-?2|w-?9|'
    r'agreement|contract|certificate|diploma|award\s*letter|verification|generate|download)\b',
    re.IGNORECASE
)
GENERIC_GENERATE_EXCLUDE_RE = re.compile(
    r'\b(upload|close|cancel|save|submit|edit|delete|remove|back|next|previous|logout|'
    r'sign\s*out|change|search|filter|sort|menu|expand|collapse)\b',
    re.IGNORECASE
)

# Stable XPath selectors for the MyPath "Ledger PDF" button (and any similar
# MuiLoadingButton whose label lives inside a nested <span>).
# Listed in priority order — tried in sequence until one matches.
# Rationale for each:
#   1. Text in MuiLoadingButton-label <span>  — most stable; immune to class/ID churn.
#      The button's own .text is often '' in Selenium because MUI renders the
#      label inside a nested span, not as a direct text node of the button.
#   2. MuiButton-outlined + PictureAsPdfIcon  — semantic; MuiButton-outlined is MUI
#      stable API; data-testid values are intentionally stable across releases.
#   3. Any button with PictureAsPdfIcon       — broadest fallback if variant class changes.
# What must NEVER be used:
#   • id=":r4:" etc.  — React regenerates colon-prefixed IDs on every render.
#   • css-stk9rk, css-oz8hdd etc. — MUI regenerates these hashes on every build.
#   • button[1], button[2] etc. — order changes when MUI inserts loading spinners.
LEDGER_BTN_XPATHS = [
    "//button[.//span[normalize-space(text())='Ledger PDF']]",
    "//button[contains(@class,'MuiButton-outlined')][.//*[@data-testid='PictureAsPdfIcon']]",
    "//button[.//*[@data-testid='PictureAsPdfIcon']]",
]


def _normalize_href(href: str) -> str:
    """Normalise href for dedup comparison — strips trailing slash, sorts query params."""
    if not href:
        return ""
    parsed = urllib.parse.urlparse(href)
    path = parsed.path.rstrip('/')
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query)))
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, parsed.fragment))


def _sanitize(name: str) -> str:
    """Clean folder/file names: remove invalid characters, collapse whitespace."""
    if not name:
        return "Uncategorized"
    name = name.replace('\n', ' ').replace('\r', ' ')
    for bad in '<>:"/\\|?*':
        name = name.replace(bad, '_')
    name = ' '.join(name.split())
    if len(name) > 100:
        name = name[:100]
    return name.strip() or "Uncategorized"


def _route_key(url: str) -> str:
    """Extract a stable route-type key from a URL's path, e.g.
    '.../students/12345/academics' -> 'academics'.

    Used to scope per-domain interaction-mode learning by ROUTE TYPE, not
    just domain. Different routes on the same domain can be structurally
    different page types -- confirmed on my-path.online, where an
    'academics' page is a 'generate_documents' page but a 'documents' page
    on the SAME domain is a 'mypath_documents' page. Learning a single
    domain-wide mode caused the wrong fast-path to be tried first on the
    wrong route every time.
    """
    try:
        path = urllib.parse.urlparse(url).path.strip('/')
        segments = [s for s in path.split('/') if s]
        # Prefer the last segment that doesn't look like an ID (long hex/numeric string)
        for seg in reversed(segments):
            if not re.fullmatch(r'[0-9a-fA-F]{6,}', seg):
                return seg.lower()
        return segments[-1].lower() if segments else 'root'
    except Exception:
        return 'root'


class FileExporter:
    """Universal File Exporter — RadDock, tabs, headings, plain tables, MyPath Documents, and MyPath generate mode."""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        config: dict,
        students: list,
        download_dir: str,
        folder_hierarchy_template: str = "{studentId}/{segment}/{category}",
        allow_duplicates: bool = True,
        segment_names_str: str = "",
        default_segment: str = "Uncategorized",
        category_column_index: int = 2,
        interaction_mode: str = "auto",
        filters: dict | None = None,
        devtools: bool = False,
        on_progress=None,
        abort_flag=None,
        log=None,
    ) -> None:
        self._log = log or (lambda l, m: None)
        self._on_progress = on_progress or (lambda d: None)
        self._abort = abort_flag or (lambda: False)

        run_start = time.time()
        success_count = 0
        total_files_downloaded = 0
        domain_key = None

        students = filter_valid_students(students)
        download_dir_abs = os.path.abspath(download_dir)
        os.makedirs(download_dir_abs, exist_ok=True)

        self._setup_file_logger(download_dir_abs)

        segment_names = [s.strip() for s in segment_names_str.split(',') if s.strip()] \
            if segment_names_str else []

        # Interaction mode is no longer user/credentials-configurable. The
        # exporter always auto-detects what kind of page it's looking at
        # (MyPath Documents table, MyPath generate-documents dropdown, or a
        # static file grid/tabs/headings) fresh on every record, using
        # per-domain learning purely to skip straight to the right handler
        # once a domain's pattern is known -- never to force a mode. A
        # leftover 'interaction_mode=' line in an old credentials.txt is
        # ignored so it can never again lock the whole run onto the wrong
        # page type (this was the root cause of the MyPath export bug).
        effective_mode = 'auto'
        if config.get('interaction_mode'):
            self._log('debug', "Ignoring legacy 'interaction_mode' credentials setting -- "
                                "the exporter always auto-detects per page now.")

        # Cross-tab scanning is OPT-IN via credentials.txt ('scan_all_tabs=true').
        # Default is OFF: only the link given in the CSV is visited. Scanning
        # every profile tab (General/Academics/Graduation/Career Services/
        # Finances/Documents/...) for every student multiplies run time and
        # CDP/download-behavior churn considerably, so it should be something
        # the user turns on deliberately, not a silent default.
        scan_all_tabs = str(config.get('scan_all_tabs', '')).strip().lower() in ('1', 'true', 'yes', 'on')
        self._armed_download_dir = None

        config['segment_names']          = segment_names
        config['default_segment']        = default_segment
        config['category_column_index']  = category_column_index
        config['interaction_mode']       = effective_mode
        config['folder_template']        = folder_hierarchy_template or "{studentId}/{segment}/{category}"
        try:
            config['min_columns'] = int(config.get('min_columns') or 2)
        except (ValueError, TypeError):
            config['min_columns'] = 2

        filters    = filters or {}
        login_url  = config.get('login_url', '')
        domain_key = learning_engine.extract_domain(login_url)

        effective_devtools = learning_engine.resolve_devtools(devtools, domain_key, log_func=self._log)
        chrome_options = build_download_options(download_dir_abs, devtools=effective_devtools)

        driver = None
        try:
            driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))
            driver.set_script_timeout(60)
            wait   = WebDriverWait(driver, 30, poll_frequency=0.2)
            main_window = driver.current_window_handle

            set_download_behavior(driver, download_dir_abs, log_func=self._log)
            self._armed_download_dir = download_dir_abs

            self._log('info', f"Cross-tab scanning: {'ON' if scan_all_tabs else 'OFF'} "
                               f"(set scan_all_tabs=true in credentials.txt to also check every "
                               f"profile tab, not just each record's own link).")

            if login_url:
                self._log('info', f"Navigating to login URL: {login_url}")
                t0 = time.time()
                driver.get(login_url)
                learning_engine.record_latency(login_url, 'page_load', time.time() - t0)
                self._perform_login(driver, wait, config, login_url)
                wait_for_page_ready(driver, timeout=15)
                if self._is_on_login_page(driver, config, settle_timeout=2.0):
                    self._log_login_page_diagnostics(driver, config, '<none>', 'login_failed')
                    self._log('error',
                              "Login did not actually succeed (password field still visible after "
                              "submitting) -- every record will likely fail. Check credentials.txt "
                              "username/password and the login form xpaths for this site.")
                else:
                    self._log('info', "Login successful.")
                self._log_login_page_diagnostics(driver, config, '<none>', 'baseline_after_login')

            self._on_progress({'type': 'total', 'total': len(students)})

            for idx, student in enumerate(students):
                if self._abort():
                    self._log('warning', "Abort requested — halting export.")
                    break

                student_id          = student['studentId']
                link                = student['link']
                student_root_folder = os.path.join(download_dir_abs, student_id)

                self._on_progress({
                    'type': 'student_start', 'student_id': student_id,
                    'idx': idx, 'students_traversed': idx, 'total_students': len(students),
                })
                self._log('info', f"--- Record {student_id} ({idx+1}/{len(students)}) ---")

                t_nav = time.time()
                try:
                    driver.get(link)
                    wait_for_page_ready(driver, timeout=15)
                except Exception as nav_err:
                    if not self._session_alive(driver):
                        self._log('error',
                                  f"Browser session crashed before reaching {student_id}: {nav_err}. "
                                  f"Stopping the run -- please re-run to pick up where this left off.")
                        break
                    self._log('error', f"Could not load {student_id}'s page: {nav_err} -- skipping.")
                    continue

                if self._is_on_login_page(driver, config, settle_timeout=4.0):
                    self._log_login_page_diagnostics(driver, config, student_id, 'initial')
                    self._log('warning',
                              f"Session expired before reaching {student_id}'s record -- "
                              f"site bounced back to login. Re-authenticating and retrying...")
                    try:
                        self._perform_login(driver, wait, config, login_url)
                        driver.get(link)
                        wait_for_page_ready(driver, timeout=15)
                        if self._is_on_login_page(driver, config, settle_timeout=4.0):
                            self._log_login_page_diagnostics(driver, config, student_id, 'after_relogin')
                            self._log('error',
                                      f"Still on login page for {student_id} after re-authenticating -- "
                                      f"skipping this record. Check credentials / the link itself.")
                            self._write_log_csv(download_dir_abs, [{
                                'studentId': student_id, 'fileName': 'N/A', 'fileType': 'N/A',
                                'directory': student_root_folder, 'action': 'not downloaded',
                                'actionComment': 'Session kept expiring / re-login failed for this record',
                                'validation': 'traversed',
                            }])
                            self._on_progress({
                                'type': 'student_done',
                                'students_traversed': idx + 1,
                                'total_students': len(students),
                            })
                            continue
                    except Exception as relogin_err:
                        self._log('error', f"Re-login failed for {student_id}: {relogin_err}")
                        continue

                learning_engine.record_latency(link, 'page_load', time.time() - t_nav)

                # MyPath tabs are real <a href> navigation links. Some routes
                # (confirmed: 'finances' for at least one student) render their
                # panel, pass an immediate check, and only THEN -- a few
                # seconds later, once an async data-availability check inside
                # that panel resolves -- get silently redirected away by the
                # SPA's router (finances -> general, observed even cascading
                # general -> academics in one run). _reach_route_reliably
                # settles for a few seconds watching for exactly that before
                # giving the green light to scan, and skips this record's own
                # link entirely (rather than scanning the wrong page under a
                # misleading 'route=finances' label) if the site keeps
                # bouncing away from it no matter what.
                intended_route = _route_key(link)
                route_reachable = True
                try:
                    if _route_key(driver.current_url) != intended_route:
                        route_reachable = self._reach_route_reliably(
                            driver, intended_route, link, student_id, download_dir_abs)
                    else:
                        # Already on the right route right now -- still settle-
                        # check once, since the redirect that matters here is
                        # the DELAYED kind that a same-instant check can't see.
                        route_reachable = self._reach_route_reliably(
                            driver, intended_route, link, student_id, download_dir_abs,
                            max_attempts=1)
                except Exception as settle_err:
                    self._log('debug', f"Route settle-check failed for {student_id}: {settle_err}")
                    route_reachable = True  # don't block the record on a settle-check bug

                files_got = 0
                if route_reachable:
                    try:
                        files_got = self._process_current_page(
                            driver, wait, config, student_id, student_root_folder,
                            download_dir_abs, allow_duplicates, filters, main_window, link
                        )
                    except Exception as proc_err:
                        if not self._session_alive(driver):
                            self._log('error',
                                      f"Browser session crashed while processing {student_id}: {proc_err}. "
                                      f"Stopping the run -- please re-run to pick up where this left off.")
                            break
                        self._log('error', f"Unexpected error processing {student_id}: {proc_err}")

                # --- Automatic tab traversal -----------------------------------
                # OPT-IN (see 'scan_all_tabs' above). A student's profile is a
                # tab bar of real navigation links (General / Academics /
                # Graduation / Career Services / Finances / Documents / Tasks /
                # Messages). The CSV only gives us ONE link, but files worth
                # capturing (e.g. a 'Ledger PDF' button) can live on a DIFFERENT
                # tab than whichever one that link happens to point at. When
                # enabled, this visits every other discoverable tab too and runs
                # the same unified detection on each one.
                other_tabs = []
                if scan_all_tabs:
                    try:
                        other_tabs = self._discover_profile_tabs(driver, exclude_route=_route_key(link))
                    except Exception as e:
                        self._log('debug', f"Tab discovery failed for {student_id}: {e}")

                session_died = False
                for tab_route, tab_href in other_tabs:
                    if self._abort():
                        break
                    try:
                        self._log('info', f"  [TAB] Checking '{tab_route}' tab for {student_id}...")
                        clicked = self._click_profile_tab(driver, tab_route, tab_href)
                        if clicked:
                            wait_for_page_ready(driver, timeout=12)
                        if not clicked or _route_key(driver.current_url) != tab_route:
                            if clicked:
                                self._log('debug',
                                    f"Clicking '{tab_route}' tab landed on route "
                                    f"'{_route_key(driver.current_url)}' instead -- falling back to "
                                    f"direct navigation.")
                            driver.get(tab_href)
                            wait_for_page_ready(driver, timeout=12)
                        self._wait_for_record_content(driver)
                        if _route_key(driver.current_url) != tab_route:
                            self._log('warning',
                                f"'{tab_route}' tab for {student_id} redirected to "
                                f"'{_route_key(driver.current_url)}' via both click and direct nav -- "
                                f"this route may not be reachable as a standalone page for this student.")
                            self._dump_debug_html(driver, f"{student_id}_{tab_route}_redirect", download_dir_abs)
                            continue
                        tab_files_got = self._process_current_page(
                            driver, wait, config, student_id, student_root_folder,
                            download_dir_abs, allow_duplicates, filters, main_window, tab_href
                        )
                        if tab_files_got:
                            self._log('info', f"  [TAB] '{tab_route}': {tab_files_got} file(s) captured.")
                        files_got += tab_files_got
                    except Exception as tab_err:
                        if not self._session_alive(driver):
                            self._log('error',
                                      f"Browser session crashed while checking '{tab_route}' tab for "
                                      f"{student_id}: {tab_err}. Stopping the run.")
                            session_died = True
                            break
                        self._log('debug', f"Tab '{tab_route}' failed for {student_id}: {tab_err}")
                        continue

                if session_died:
                    break

                learning_engine.record_student_insight(student_id, 'has_files', files_got > 0)
                if files_got == 0:
                    dump_path = self._dump_debug_html(driver, student_id, download_dir_abs)
                    diag = self._diagnose_page_structure(driver, link)
                    self._log('warning',
                              f"[DIAG] Record {student_id} yielded 0 files (route='{_route_key(link)}'). "
                              f"{diag} Page HTML dumped to: {dump_path}")
                if files_got > 0:
                    success_count += 1
                total_files_downloaded += files_got

                self._on_progress({
                    'type': 'student_done',
                    'students_traversed': idx + 1,
                    'total_students': len(students),
                })

        finally:
            if driver:
                driver.quit()
            from core.utils import remove_empty_dirs
            remove_empty_dirs(download_dir_abs)

            duration = time.time() - run_start
            learning_engine.record_run_summary(
                module_name="File Exporter",
                total_items=len(students),
                success_count=success_count,
                total_files=total_files_downloaded,
                duration_sec=duration,
            )
            self._log('info', f"[LEARNING] Run complete. {duration:.1f}s, "
                               f"{success_count}/{len(students)} records, {total_files_downloaded} files.")
            if students:
                learning_engine.record_devtools_outcome(domain_key, effective_devtools, success_count > 0)

    # ------------------------------------------------------------------ #
    # File logger
    # ------------------------------------------------------------------ #

    def _setup_file_logger(self, download_dir_abs: str):
        log_path = os.path.join(download_dir_abs, "automation_debug.log")
        file_logger = logging.getLogger("file_exporter_debug")
        file_logger.setLevel(logging.DEBUG)
        file_logger.handlers.clear()
        fh = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        file_logger.addHandler(fh)
        self._file_logger = file_logger
        original_log = self._log
        def dual_log(level, msg):
            original_log(level, msg)
            try:
                getattr(self._file_logger, level.lower())(msg)
            except Exception:
                self._file_logger.info(msg)
        self._log = dual_log
        self._file_logger.info("=" * 70)
        self._file_logger.info(f"New automation run started at {datetime.datetime.now()}")

    # ------------------------------------------------------------------ #
    # Verify
    # ------------------------------------------------------------------ #

    def verify(self, download_dir: str, log=None) -> dict:
        _log = log or (lambda l, m: None)
        log_file_path = os.path.join(download_dir, "automation_execution_log.csv")
        result = {'verified': 0, 'missing': [], 'folders_missing': 0}
        if not os.path.isfile(log_file_path):
            _log('warning', "No execution log CSV found to verify against.")
            return result

        expected: dict = {}
        try:
            with open(log_file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('action') == 'downloaded':
                        expected.setdefault(row['directory'], {}).setdefault(
                            row['studentId'], []).append(row['fileName'])
        except Exception as e:
            _log('error', f"Failed reading execution log: {e}")
            return result

        for path, records in expected.items():
            if not os.path.isdir(path):
                result['folders_missing'] += 1
                for sid, files in records.items():
                    for fname in files:
                        result['missing'].append(f"{sid} | {path} | {fname} (folder missing)")
                continue
            actual = set(os.listdir(path))
            for sid, files in records.items():
                for fname in files:
                    if _sanitize(fname) in actual:
                        result['verified'] += 1
                    else:
                        result['missing'].append(f"{sid} | {path} | {fname}")
        return result

    # ------------------------------------------------------------------ #
    # Login
    # ------------------------------------------------------------------ #

    def _perform_login(self, driver, wait, config: dict, login_url: str):
        username_xpath = config.get('username_xpath') or (
            "//input[@type='email'] | //input[contains(translate(@id,'USERNAM','usernam'),'user')] "
            "| //input[contains(translate(@name,'USERNAM','usernam'),'user')] | //input[@type='text']"
        )
        password_xpath = config.get('password_xpath') or "//input[@type='password']"
        login_btn_xpath = config.get('login_btn_xpath') or (
            "//button[@type='submit'] | //input[@type='submit'] "
            "| //button[contains(translate(.,'LOGIN','login'),'log in')] "
            "| //button[contains(translate(.,'LOGIN','login'),'login')] "
            "| //button[contains(translate(.,'SIGNIN','signin'),'sign in')]"
        )
        user_input = wait.until(EC.presence_of_element_located((By.XPATH, username_xpath)))
        pass_input = driver.find_element(By.XPATH, password_xpath)
        login_btn = driver.find_element(By.XPATH, login_btn_xpath)
        user_input.clear()
        user_input.send_keys(config['username'])
        pass_input.clear()
        pass_input.send_keys(config['password'])
        login_btn.click()

        # Wait for the ACTUAL success signal -- the password field disappearing --
        # instead of a URL check. For SPAs that don't navigate on login (e.g.
        # my-path.online, which stays on the bare root URL the whole time),
        # 'login' not in current_url is trivially true before the async login
        # request has even completed, giving zero real wait time and causing
        # every subsequent step to run against a still-logged-out page. This
        # was confirmed via diagnostics: the login form was still fully present,
        # with no session cookie or auth token anywhere, immediately after the
        # old check reported success.
        def _password_gone(d):
            try:
                return not any(e.is_displayed() for e in d.find_elements(By.XPATH, password_xpath))
            except Exception:
                return False

        try:
            WebDriverWait(driver, 15).until(_password_gone)
        except Exception:
            # Fallback for sites where login legitimately does navigate to/from
            # a /login path -- keep the old heuristic as a secondary check.
            try:
                WebDriverWait(driver, 5).until(lambda d: 'login' not in d.current_url.lower())
            except Exception:
                pass
        wait_for_page_ready(driver, timeout=8)

    def _is_on_login_page(self, driver, config: dict, settle_timeout: float = 0.0) -> bool:
        """True if the current page is showing a visible password field.

        NOTE: two earlier attempts at refining this (a settle/debounce window,
        then an additional URL-must-say-'login' requirement) were both blind
        guesses made without seeing the actual page, and both turned out wrong
        -- confirmed by the user that my-path.online genuinely does need
        re-login handling, so gating on the URL would have silently disabled
        detection there entirely. Rather than guess a third time, this reverts
        to the simple, original check and instead logs rich diagnostics
        (via _log_login_page_diagnostics) whenever it fires, so the next run's
        log shows exactly what's on screen instead of us hypothesizing blind.
        """
        password_xpath = config.get('password_xpath') or "//input[@type='password']"

        def _password_visible() -> bool:
            try:
                for el in driver.find_elements(By.XPATH, password_xpath):
                    if el.is_displayed():
                        return True
            except Exception:
                pass
            return False

        if not _password_visible():
            return False
        if settle_timeout <= 0:
            return True

        deadline = time.time() + settle_timeout
        while time.time() < deadline:
            time.sleep(0.3)
            if not _password_visible():
                return False
        return _password_visible()

    def _log_login_page_diagnostics(self, driver, config: dict, student_id: str, label: str):
        """Dump everything we can see about the suspected login page so the next
        log tells us the actual cause instead of us guessing again."""
        password_xpath = config.get('password_xpath') or "//input[@type='password']"
        username_xpath = config.get('username_xpath') or (
            "//input[@type='email'] | //input[contains(translate(@id,'USERNAM','usernam'),'user')] "
            "| //input[contains(translate(@name,'USERNAM','usernam'),'user')] | //input[@type='text']"
        )
        try:
            current_url = driver.current_url
        except Exception:
            current_url = '<unavailable>'
        try:
            title = driver.title
        except Exception:
            title = '<unavailable>'
        try:
            pw_all = driver.find_elements(By.XPATH, password_xpath)
            pw_visible = [e for e in pw_all if e.is_displayed()]
        except Exception:
            pw_all, pw_visible = [], []
        try:
            user_all = driver.find_elements(By.XPATH, username_xpath)
            user_visible = [e for e in user_all if e.is_displayed()]
        except Exception:
            user_all, user_visible = [], []
        try:
            body_text = driver.find_element(By.TAG_NAME, 'body').text[:300].replace('\n', ' | ')
        except Exception:
            body_text = '<unavailable>'
        try:
            cookies = driver.get_cookies()
            cookie_summary = ', '.join(f"{c.get('name')}={'<val>' if c.get('value') else '<empty>'}" for c in cookies) or '<none>'
        except Exception:
            cookie_summary = '<unavailable>'
        try:
            storage_keys = driver.execute_script(
                "return {local: Object.keys(window.localStorage||{}), "
                "session: Object.keys(window.sessionStorage||{})};"
            )
        except Exception:
            storage_keys = '<unavailable>'
        self._log('debug',
                   f"[DIAG:{label}] student={student_id} url={current_url} title={title!r} "
                   f"password_fields={len(pw_all)} (visible={len(pw_visible)}) "
                   f"username_fields={len(user_all)} (visible={len(user_visible)}) "
                   f"cookies=[{cookie_summary}] storage_keys={storage_keys} "
                   f"body_preview={body_text!r}")

    # ------------------------------------------------------------------ #
    # STATIC LINKS mode (RadDock / tabs / headings / generic tables)
    # ------------------------------------------------------------------ #

    def _wait_for_record_content(self, driver, timeout: float = 10) -> None:
        """Wait for the actual content of the navigated route to render.

        MyPath is a Next.js SPA where every student page shares a tab bar.
        The tabs are real <a href> navigation links, not JS tab-switches --
        each tab loads a completely different URL and page. The key problem:
        the 'academics' tab content renders immediately as a placeholder shell
        (even when the current route is /finances or /documents) because it's
        the SPA's default shell. This caused _wait_for_record_content to fire
        the moment the academics placeholder appeared, before the actual
        finances/documents content had loaded at all.

        The fix: detect the ACTIVE tab from the URL route, then wait for that
        specific tab's panel to have real content -- not just any panel.
        For routes whose panel ID we know (documents, finances, academics,
        graduation, etc.), we wait for that panel specifically. For unknown
        routes, we fall back to waiting for any panel with content.
        """
        # Determine which tab-panel we should be waiting for based on the URL
        try:
            current_url = driver.current_url
            route = _route_key(current_url)
        except Exception:
            route = ''

        # MyPath panel ids follow the pattern: {route}-panel
        # e.g. /finances -> finances-panel, /documents -> documents-panel
        target_panel_id = f"{route}-panel" if route else None

        def _ready(d):
            try:
                # If we know the target panel, wait for IT specifically
                if target_panel_id:
                    panels = d.find_elements(By.ID, target_panel_id)
                    if panels:
                        p = panels[0]
                        # Panel must have actual content -- h2, table, or
                        # a dialog-trigger button (ledger/receipts pages)
                        if (p.find_elements(By.TAG_NAME, 'h2') or
                                p.find_elements(By.TAG_NAME, 'table') or
                                p.find_elements(By.XPATH, ".//button[@name='dialog-trigger']")):
                            return True
                        # Panel exists but is empty -- content still loading
                        return False
            except Exception:
                pass
            # Fallback: any tab-panel with real content
            try:
                for panel in d.find_elements(By.XPATH, "//*[@role='tab-panel']"):
                    if (panel.find_elements(By.TAG_NAME, 'h2') or
                            panel.find_elements(By.TAG_NAME, 'table')):
                        return True
            except Exception:
                pass
            # Generate-mode signals
            try:
                if d.find_elements(
                    By.XPATH,
                    "//button[.//span[contains(@class,'MuiLoadingButton-label')]]"
                    "[.//*[@data-testid='ArrowDropDownRoundedIcon']]"
                    " | //a[contains(@href,'/academics/programs/')]/h2"
                    " | //button[.//span[starts-with(normalize-space(.),'Generate')]]"
                ):
                    return True
            except Exception:
                pass
            try:
                if d.find_elements(By.XPATH, "//div[contains(@class,'RadDock')]"):
                    return True
            except Exception:
                pass
            return False

        try:
            WebDriverWait(driver, timeout, poll_frequency=0.2).until(_ready)
        except Exception:
            pass

    def _session_alive(self, driver) -> bool:
        """Cheap check for whether the WebDriver session/browser is still up.
        Used after any exception in the main record loop to tell a genuine
        session/browser crash (attempt relaunch-and-resume) apart from an
        ordinary per-record error (skip this record, keep going)."""
        try:
            _ = driver.window_handles
            return True
        except Exception:
            return False

    def _relaunch_driver(self, chrome_options, download_dir_abs: str, login_url: str, config: dict):
        """Launch a fresh Chrome instance and log back in, to recover from a
        browser/session crash mid-run. Raises on failure (caller treats that
        as unrecoverable); returns (driver, wait, main_window) on success.

        This exists because the browser has been observed to die spontaneously
        partway through a run -- not tied to any specific risky command (both
        set_download_behavior and wait_for_downloads_to_finish were checked
        directly and neither does anything that should kill the session) --
        so simply stopping the whole run on the first crash throws away every
        remaining record. One relaunch lets the run continue past a crash
        instead.
        """
        new_driver = launch_chrome_driver(chrome_options, log_func=lambda m: self._log('info', m))
        new_driver.set_script_timeout(60)
        new_wait = WebDriverWait(new_driver, 30, poll_frequency=0.2)
        new_main_window = new_driver.current_window_handle
        set_download_behavior(new_driver, download_dir_abs, log_func=self._log)
        self._armed_download_dir = download_dir_abs
        if login_url:
            new_driver.get(login_url)
            self._perform_login(new_driver, new_wait, config, login_url)
            wait_for_page_ready(new_driver, timeout=15)
        return new_driver, new_wait, new_main_window

    def _try_recover_session(self, err, chrome_options, download_dir_abs: str, login_url: str,
                              config: dict, relaunch_budget: int):
        """On a dead browser session, attempt one relaunch if budget remains.
        Returns (driver, wait, main_window, new_budget) on success, or None
        if recovery isn't possible / budget exhausted (caller should stop the
        run)."""
        if relaunch_budget <= 0:
            self._log('error', f"Browser session crashed: {err}. No relaunches left this run -- stopping.")
            return None
        try:
            driver, wait, main_window = self._relaunch_driver(
                chrome_options, download_dir_abs, login_url, config)
            self._log('error',
                      f"Browser session crashed: {err}. Recovered by relaunching Chrome -- "
                      f"skipping the record in progress and continuing with the next one "
                      f"({relaunch_budget - 1} relaunch(es) left this run).")
            return driver, wait, main_window, relaunch_budget - 1
        except Exception as relaunch_err:
            self._log('error', f"Relaunch attempt failed: {relaunch_err}. Stopping the run.")
            return None

    def _dump_debug_html(self, driver, student_id: str, download_dir_abs: str) -> str | None:
        """Save the current page's full HTML to download_dir/page_dumps/ whenever a
        record yields 0 files, so a real DOM snapshot is available for diagnosis
        instead of guessing selectors again. Returns the path written, or None."""
        try:
            dumps_dir = os.path.join(download_dir_abs, "page_dumps")
            os.makedirs(dumps_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(dumps_dir, f"student_{student_id}_{ts}.html")
            html = driver.execute_script("return document.documentElement.outerHTML;")
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(html)
            return out_path
        except Exception as e:
            self._log('warning', f"Failed to dump debug HTML: {e}")
            return None

    def _diagnose_page_structure(self, driver, page_url: str) -> str:
        """Count key structural signals on the current page without clicking or
        waiting on anything, so a 0-file record's log line says exactly what WAS
        and WASN'T present -- e.g. whether the generate-mode signals (dropdown
        button / current-program element / Generate buttons) are matching a
        persistent site-wide nav element even on a documents page, which would
        explain a false-positive generate-mode detection."""
        checks = {
            'h2': (By.TAG_NAME, 'h2'),
            'dialog_trigger_btns': (By.XPATH, "//button[@name='dialog-trigger']"),
            'download_icon_btns': (By.XPATH, "//button[.//*[@data-testid='FileDownloadIcon']]"),
            'tab_panels_total': (By.XPATH, "//*[@role='tab-panel']"),
            'tab_panels_with_doc_id': (By.XPATH,
                "//*[@role='tab-panel' and (contains(@id,'document') or contains(@aria-labelledby,'document'))]"),
            'raddock_containers': (By.XPATH, "//div[contains(@class,'RadDock')]"),
            'nav_tabs': (By.XPATH, "//*[@role='tab']"),
            'dropdown_btn_matches': (By.XPATH,
                "//button[.//span[contains(@class,'MuiLoadingButton-label')]]"
                "[.//*[@data-testid='ArrowDropDownRoundedIcon']]"),
            'program_name_matches': (By.XPATH, "//a[contains(@href,'/academics/programs/')]/h2"),
            'generate_btn_matches': (By.XPATH, "//button[.//span[starts-with(normalize-space(.),'Generate')]]"),
        }
        parts = []
        for key, (by, sel) in checks.items():
            try:
                parts.append(f"{key}={len(driver.find_elements(by, sel))}")
            except Exception:
                parts.append(f"{key}=err")
        try:
            panel_ids = [
                (p.get_attribute('id') or '', p.get_attribute('aria-labelledby') or '')
                for p in driver.find_elements(By.XPATH, "//*[@role='tab-panel']")
            ][:6]
        except Exception:
            panel_ids = []
        try:
            current_url = driver.current_url
        except Exception:
            current_url = '<unavailable>'
        return f"url={current_url} " + " ".join(parts) + f" tab_panel_ids={panel_ids}"

    # ------------------------------------------------------------------ #
    # Unified per-page processing + tab discovery + generic generate buttons
    # ------------------------------------------------------------------ #

    def _process_current_page(
        self, driver, wait, config, student_id, student_root_folder,
        download_dir_abs, allow_duplicates, filters, main_window, page_url
    ) -> int:
        """Runs full detection on whatever page is CURRENTLY loaded and
        returns how many files were captured. This is the single place that
        decides what's on a page and how to get it -- called once for the
        record's own link, and again for every other tab discovered on the
        student's profile (see _discover_profile_tabs).

        Two independent passes run every time, NOT mutually exclusive,
        because a single page can genuinely have both at once (confirmed:
        a Documents tab has static file rows, while a Finances tab can have
        a standalone 'Ledger PDF' button with no table/dropdown around it at
        all):

          1. Structured detection (existing, unchanged) -- mypath_documents
             table rows, dropdown+Generate buttons, or RadDock/tabs/headings/
             table static links. Per-domain-route learning is consulted as a
             fast path exactly as before.

          2. Generic generate-button scan (new) -- ANY standalone visible
             button on the page whose own text reads like it produces a
             document (e.g. 'Ledger PDF', 'Download Statement', 'Generate
             1098T') gets clicked and captured via the same robust
             multi-strategy pipeline already built for MyPath's dialog-
             trigger buttons. This is what catches one-off buttons that
             don't fit either of the two known structured patterns.
        """
        route = _route_key(page_url)
        learned_mode = learning_engine.get_domain_strategy(
            page_url, f'interaction_mode::{route}', 'auto')

        self._log('info', f"[MODE] route='{route}' learned='{learned_mode}' (auto-detect always active)")

        files_got = 0
        detected_via_fastpath = False
        generic_already_ran = False

        if learned_mode == 'mypath_documents':
            files_got = self._process_mypath_documents_mode(
                driver, wait, config, student_id, student_root_folder,
                download_dir_abs, allow_duplicates, filters, main_window, page_url
            )
            detected_via_fastpath = True
        elif learned_mode == 'generate_documents':
            files_got = self._process_generate_documents_mode(
                driver, wait, config, student_id, student_root_folder,
                download_dir_abs, filters, main_window
            )
            detected_via_fastpath = True
        elif learned_mode == 'static_links':
            files_got = self._process_static_links_mode(
                driver, wait, config, student_id, student_root_folder,
                download_dir_abs, allow_duplicates, filters, main_window, page_url
            )
            detected_via_fastpath = True
        elif learned_mode == 'generic_generate':
            # Fast path for routes where NEITHER structured pattern applies and
            # only the generic standalone-button scan ever finds anything (e.g.
            # a bare 'Ledger PDF' button on a Finances tab with no table/dropdown
            # around it). Skips straight past the ~10s of structured-detection
            # waits (dropdown-button wait 4s + generate-buttons wait 6s) that are
            # already known to find nothing on this route.
            files_got = self._process_generic_generate_buttons(
                driver, config, student_id, student_root_folder, route,
                filters, main_window, download_dir_abs, allow_duplicates
            )
            generic_already_ran = True
            detected_via_fastpath = True

        if detected_via_fastpath and files_got == 0:
            self._log('warning', f"[MODE] Learned mode '{learned_mode}' found nothing for "
                                  f"{student_id} on route '{route}' -- re-detecting from scratch.")
            generic_already_ran = False

        if not detected_via_fastpath or files_got == 0:
            # --- Full structured auto-detect --------------------------------
            # Priority order:
            # 1. MyPath documents page (h2-sectioned MUI tables, dialog-trigger buttons)
            # 2. Generate-documents page (MUI dropdown + Generate buttons)
            # 3. Static links (RadDock / tabs / headings / table rows)

            if self._is_mypath_documents_page(driver, page_url):
                self._log('info', "[AUTO-DETECT] MyPath Documents page detected (h2-sectioned MUI tables).")
                files_got = self._process_mypath_documents_mode(
                    driver, wait, config, student_id, student_root_folder,
                    download_dir_abs, allow_duplicates, filters, main_window, page_url
                )
                if files_got > 0:
                    learning_engine.record_domain_strategy(
                        page_url, f'interaction_mode::{route}', 'mypath_documents')

            else:
                dropdown_btn = self._get_dropdown_button(driver, wait, config)
                program_name = self._get_current_program_name(driver, config)
                gen_btns     = self._get_generate_buttons(driver, config)
                is_generate  = (dropdown_btn is not None) or (program_name is not None) or bool(gen_btns)

                if is_generate:
                    self._log('info', "[AUTO-DETECT] MyPath-style generate page detected.")
                    files_got = self._process_generate_documents_mode(
                        driver, wait, config, student_id, student_root_folder,
                        download_dir_abs, filters, main_window
                    )
                    if files_got > 0:
                        learning_engine.record_domain_strategy(
                            page_url, f'interaction_mode::{route}', 'generate_documents')
                else:
                    self._log('info', "[AUTO-DETECT] Scanning page structure for static files.")
                    files_got = self._process_static_links_mode(
                        driver, wait, config, student_id, student_root_folder,
                        download_dir_abs, allow_duplicates, filters, main_window, page_url
                    )
                    if files_got > 0:
                        learning_engine.record_domain_strategy(
                            page_url, f'interaction_mode::{route}', 'static_links')

        # --- Generic generate-button pass ---------------------------------
        # Only runs when structured detection found NOTHING (files_got == 0),
        # not "on top of" a successful structured result. Confirmed twice via
        # a real run's log: when structured detection (mypath_documents or
        # generate_documents) already succeeded, the broad text heuristic
        # re-matched the SAME buttons/rows a second time under a looser
        # match (e.g. every per-row 'Download' icon on a Documents table, or
        # every 'Generate X' button already handled by the structured
        # per-program loop) -- producing duplicate downloads into a wrong
        # duplicate folder, wasting ~30-45s per route, and in one case a
        # stale-element crash from clicking a row button a second time after
        # the structured pass had already re-rendered the table. The generic
        # pass exists to catch buttons NEITHER structured mode covers (e.g. a
        # bare 'Ledger PDF' button on a page with no table/dropdown at all,
        # where structured detection legitimately finds 0) -- it was never
        # meant to double-check a page that already succeeded.
        if not generic_already_ran and files_got == 0:
            generic_got = self._process_generic_generate_buttons(
                driver, config, student_id, student_root_folder, route,
                filters, main_window, download_dir_abs, allow_duplicates
            )
            if generic_got:
                self._log('info', f"[GENERIC] '{route}': {generic_got} standalone generate-button "
                                   f"file(s) captured.")
            files_got += generic_got

            # Learn this route as 'generic_generate' so future runs skip
            # straight to the generic scan instead of redoing ~10s of doomed
            # structured detection first (confirmed pattern: a Finances tab
            # with a bare 'Ledger PDF' button and no table/dropdown around it).
            if generic_got > 0:
                learning_engine.record_domain_strategy(
                    page_url, f'interaction_mode::{route}', 'generic_generate')

        return files_got

    def _reach_route_reliably(
        self, driver, route: str, href: str, student_id: str, download_dir_abs: str,
        max_attempts: int = 2, settle_checks: int = 3, settle_interval: float = 1.2
    ) -> bool:
        """Navigates to *route* and verifies the browser actually STAYS there,
        not just that it briefly loaded there.

        Confirmed via a real run's log: at least one route ('finances' for a
        specific student) renders its panel, passes an immediate route check,
        and THEN -- several seconds later, apparently once an async
        data-availability check inside that panel resolves -- the SPA's
        router silently redirects away (finances -> general, and in one run
        even cascaded general -> academics). A single check taken right after
        navigating completely misses this: the exporter would go on to scan
        whatever page it drifted to while still logging and learning under
        the ORIGINAL route's name, which is actively misleading (a log line
        can say "route='finances'" while the actual DOM being scanned is the
        Academics page).

        This settles for a few seconds after each navigation attempt,
        watching for that drift, before giving the caller a green light to
        scan. Returns True only if the route still matches after settling.
        On failure, dumps the page HTML and logs a message that explicitly
        distinguishes 'never got there' from 'got there, then got redirected
        away' -- so it's possible to tell a real automation bug apart from
        the site itself denying/redirecting away from this route for this
        particular student (e.g. no accessible data there), which is not
        something re-trying can ever fix.
        """
        last_seen_route = None
        for attempt in range(1, max_attempts + 1):
            clicked = self._click_profile_tab(driver, route, href)
            if clicked:
                wait_for_page_ready(driver, timeout=10)
            if not clicked or _route_key(driver.current_url) != route:
                try:
                    driver.get(href)
                    wait_for_page_ready(driver, timeout=10)
                except Exception:
                    pass
            self._wait_for_record_content(driver)

            # Settle window: catch a delayed async redirect before committing
            # to scanning under this route's name.
            drifted = False
            for _ in range(settle_checks):
                time.sleep(settle_interval)
                try:
                    current_route = _route_key(driver.current_url)
                except Exception:
                    current_route = last_seen_route
                last_seen_route = current_route
                if current_route != route:
                    drifted = True
                    break

            if not drifted and last_seen_route == route:
                return True

            self._log('debug', f"'{route}' for {student_id} drifted to '{last_seen_route}' "
                                f"during the settle window (attempt {attempt}/{max_attempts}).")

        self._log('warning',
            f"Could not keep '{route}' loaded for {student_id} -- it settles on "
            f"'{last_seen_route}' instead every time ({max_attempts} attempt(s) tried). "
            f"This usually means the site itself redirects away from this route for "
            f"this student (e.g. no accessible data there), not an automation bug. "
            f"Skipping this route rather than scanning the wrong page under a misleading label.")
        self._dump_debug_html(driver, f"{student_id}_{route}_unreachable", download_dir_abs)
        return False

    def _click_profile_tab(self, driver, route: str, href: str) -> bool:
        """Click the actual <a role="tab"> element for *route* on the CURRENT
        page (real SPA client-side navigation) instead of driver.get(href).

        Confirmed from real runs: direct URL navigation to '/finances' gets
        silently redirected back to '/general' by the app's router, every
        time, even via the existing redirect-recovery block on the record's
        own link -- so a fresh full-page load of that URL isn't a route this
        SPA supports as a deep link. Clicking the tab from an already-loaded
        page is real client-side navigation and is what a user would actually
        do; this is the one approach not yet tried for that route.
        Returns True if a tab element was found and clicked.
        """
        try:
            els = driver.find_elements(By.XPATH, f"//a[@role='tab' and @href={_xpath_literal(href)}]")
            if not els:
                els = driver.find_elements(By.XPATH, f"//a[@role='tab' and contains(@href,'/{route}')]")
            if not els:
                return False
            el = els[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def _discover_profile_tabs(self, driver, exclude_route: str = '') -> list:
        """Finds every profile tab link on the CURRENT page (MyPath tabs are
        real <a role="tab" href="..."> navigation links -- confirmed via a
        real page dump: General / Academics / Graduation / Career Services /
        Finances / Documents / Tasks / Messages). Returns a deduplicated list
        of (route_key, href) tuples, excluding the route already processed
        and a couple of routes that are essentially never going to contain
        downloadable documents (kept short and skippable -- if a site DOES
        put files under one of these, they just won't be auto-discovered
        this way; nothing else is affected).
        """
        SKIP_ROUTES = {'tasks', 'messages'}
        try:
            tab_els = driver.find_elements(By.XPATH, "//a[@role='tab' and @href]")
        except Exception:
            return []

        seen_routes = {exclude_route} if exclude_route else set()
        results = []
        for el in tab_els:
            try:
                href = el.get_attribute('href')
                if not href:
                    continue
                route = _route_key(href)
                if route in seen_routes or route in SKIP_ROUTES:
                    continue
                seen_routes.add(route)
                results.append((route, href))
            except Exception:
                continue
        return results

    def _expand_hidden_content(self, driver, max_clicks: int = 6) -> int:
        """Click any visible 'Show More' / 'Load More' / 'View More' style
        button repeatedly (a fresh one can appear after each click, e.g.
        paginated transaction/receipt lists) so content that's collapsed by
        default -- and any download buttons inside it -- actually renders
        before the page gets scanned. Confirmed directly from a real page
        diagnostic: a Finances tab's ONLY visible button was 'Show More',
        and the button-text scan (correctly) doesn't treat 'Show More' itself
        as a generate/download button, so whatever was behind it (a 'Ledger
        PDF' button, per the user) was never given a chance to appear.
        Returns how many times something was actually expanded.
        """
        expand_xpath = (
            "//button[not(@disabled)]["
            "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'show more') "
            "or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'load more') "
            "or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'view more') "
            "or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'view all')"
            "]"
        )
        expanded = 0
        for _ in range(max_clicks):
            try:
                candidates = [b for b in driver.find_elements(By.XPATH, expand_xpath) if b.is_displayed()]
            except Exception:
                break
            if not candidates:
                break
            btn = candidates[0]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.1)
                try:
                    btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                expanded += 1
                time.sleep(0.5)
            except Exception:
                break
        if expanded:
            self._log('debug', f"Expanded {expanded} 'Show More'/'Load More' section(s) before scanning.")
        return expanded

    def _find_generic_generate_buttons(self, driver) -> list:
        """Finds any visible, enabled button ANYWHERE on the current page
        whose own short text suggests clicking it produces a downloadable
        document -- e.g. 'Ledger PDF', 'Download Statement', 'Generate
        1098T'.

        Two-pass strategy:

        Pass 1 — known stable XPaths (LEDGER_BTN_XPATHS): tries the
        selectors derived directly from the finance.html DOM analysis.
        These are immune to MUI class/ID churn and cover the confirmed
        'Ledger PDF' button structure definitively.

        Pass 2 — broad heuristic: reads every visible, enabled button's
        label text (extracted from the MuiLoadingButton-label <span> if
        present, otherwise from textContent) and matches against
        GENERIC_GENERATE_TEXT_RE. This is the catch-all for any other
        generate/download buttons not covered by Pass 1.

        The key fix vs the old implementation: MUI's MuiLoadingButton
        renders its label inside a nested <span class="MuiLoadingButton-
        label">, NOT as a direct text node of the <button>. Selenium's
        button.text / textContent therefore returns the full concatenated
        text of ALL child nodes -- including the invisible loading spinner
        text -- which can be empty-string or longer than 60 chars after
        concatenation, causing the old len(text)>60 guard to filter out
        perfectly valid buttons like 'Ledger PDF'.
        """
        matches = []
        seen_ids = set()

        # ── Pass 1: known stable XPaths for this site's button structure ──
        for xpath in LEDGER_BTN_XPATHS:
            try:
                candidates = driver.find_elements(By.XPATH, xpath)
                for b in candidates:
                    try:
                        if not b.is_displayed() or not b.is_enabled():
                            continue
                        bid = id(b)  # object identity dedup within this pass
                        if bid in seen_ids:
                            continue
                        seen_ids.add(bid)
                        matches.append(b)
                        self._log('debug',
                            f"[GENERIC-PASS1] Ledger/PDF button located via: {xpath}")
                    except Exception:
                        continue
            except Exception:
                continue

        if matches:
            return matches

        # ── Pass 2: broad heuristic scan over all visible buttons ─────────
        # Excludes buttons inside an already-structured MyPath table row
        # (tr[@role='row']) -- those are exhaustively handled by
        # _process_mypath_documents_mode already; matching them here again
        # produced duplicate re-downloads under the wrong folder in a real run.
        try:
            buttons = driver.find_elements(
                By.XPATH, "//button[not(@disabled) and not(ancestor::tr[@role='row'])]")
        except Exception:
            return []

        for b in buttons:
            try:
                if not b.is_displayed():
                    continue

                # Prefer the label text from the MuiLoadingButton-label <span>
                # if one exists -- it contains only the human-readable label,
                # not the invisible spinner text that inflates textContent.
                label_spans = b.find_elements(
                    By.XPATH,
                    ".//span[contains(@class,'MuiLoadingButton-label')]"
                    " | .//span[contains(@class,'MuiButton-label')]"
                )
                if label_spans:
                    text = (label_spans[0].get_attribute('textContent') or '').strip()
                else:
                    text = (b.get_attribute('textContent') or '').strip()

                # Tighter length guard now that we extract only the label span
                if not text or len(text) > 80:
                    continue
                if GENERIC_GENERATE_EXCLUDE_RE.search(text):
                    continue
                if GENERIC_GENERATE_TEXT_RE.search(text):
                    bid = id(b)
                    if bid not in seen_ids:
                        seen_ids.add(bid)
                        matches.append(b)
            except Exception:
                continue

        return matches

    def _log_generic_scan_diagnostics(self, driver, student_id: str, route: str):
        """Dump what the generic-button scan actually saw when it finds zero
        matches, so a silent 'nothing here' can be told apart from 'the button
        exists but something filtered it out' without guessing. Lists every
        visible, enabled button's raw text (truncated) plus whether each was
        excluded by GENERIC_GENERATE_EXCLUDE_RE or simply didn't match
        GENERIC_GENERATE_TEXT_RE, so the exact reason is visible in the log.
        """
        try:
            all_buttons = driver.find_elements(By.XPATH, "//button[not(@disabled)]")
        except Exception:
            all_buttons = []
        rows = []
        for b in all_buttons[:40]:
            try:
                visible = b.is_displayed()
                # Use label-span text (same logic as _find_generic_generate_buttons)
                label_spans = b.find_elements(
                    By.XPATH,
                    ".//span[contains(@class,'MuiLoadingButton-label')]"
                    " | .//span[contains(@class,'MuiButton-label')]"
                )
                if label_spans:
                    text = (label_spans[0].get_attribute('textContent') or '').strip()
                else:
                    text = (b.get_attribute('textContent') or '').strip()
            except Exception:
                continue
            if not visible or not text:
                continue
            short = text[:40]
            if len(text) > 80:
                reason = 'too_long'
            elif GENERIC_GENERATE_EXCLUDE_RE.search(text):
                reason = 'excluded'
            elif GENERIC_GENERATE_TEXT_RE.search(text):
                reason = 'SHOULD_HAVE_MATCHED'  # would only appear if a race/logic bug exists
            else:
                reason = 'no_keyword_match'
            rows.append(f"{short!r}:{reason}")
        try:
            current_url = driver.current_url
        except Exception:
            current_url = '<unavailable>'
        self._log('debug',
                   f"[DIAG:generic_scan] student={student_id} route='{route}' url={current_url} "
                   f"visible_buttons_with_text={len(rows)} sample={rows}")

    def _process_generic_generate_buttons(
        self, driver, config, student_id, student_root_folder, route,
        filters, main_window, download_dir_abs, allow_duplicates
    ) -> int:
        self._expand_hidden_content(driver)
        buttons = self._find_generic_generate_buttons(driver)
        if not buttons:
            self._log_generic_scan_diagnostics(driver, student_id, route)
            return 0

        self._log('info', f"[GENERIC] Found {len(buttons)} standalone generate/download "
                           f"button(s) on '{route}' for {student_id}.")
        folder_template = config.get('folder_template', '{studentId}/{segment}/{category}')
        seen_names: dict = {}
        files_done = 0
        batch_logs = []

        for btn in buttons:
            if self._abort():
                break
            try:
                label = (btn.get_attribute('textContent') or '').strip() or 'Document'
            except Exception:
                label = 'Document'

            pseudo_item = {'category': route, 'link_text': label + '.pdf', 'href': ''}
            if filters and not self._item_passes_filters(pseudo_item, filters):
                continue

            safe_label = _sanitize(label)
            seen_names[safe_label] = seen_names.get(safe_label, 0) + 1
            occurrence = seen_names[safe_label]
            if allow_duplicates and occurrence > 1:
                base, ext = os.path.splitext(safe_label)
                effective_name = f"{base}__{occurrence}{ext}"
            else:
                effective_name = safe_label

            category_folder = build_output_path(
                folder_template, download_dir_abs, student_id, route, route, effective_name
            )

            self._log('info', f"    [GENERIC] Capturing '{label}' for {student_id} ({route})...")
            result = self._download_mypath_file(driver, btn, category_folder, effective_name, main_window)
            download_ok = result in ('downloaded', 'duplicate')
            if download_ok:
                files_done += 1

            status, comment = {
                'downloaded': ('downloaded', 'Success'),
                'duplicate':  ('downloaded', 'Duplicate content already on disk'),
                'failed':     ('failed',     'Could not retrieve generated file'),
            }.get(result, ('failed', 'Unknown result'))

            batch_logs.append({
                'studentId': student_id, 'fileName': label, 'fileType': 'PDF',
                'directory': category_folder, 'action': status, 'actionComment': comment,
                'validation': 'verified' if download_ok else result,
            })
            self._on_progress({'type': 'file_done', 'files_done': files_done})

        if batch_logs:
            self._write_log_csv(download_dir_abs, batch_logs)

        return files_done

    def _process_static_links_mode(
        self, driver, wait, config, student_id, student_root_folder,
        download_dir_abs, allow_duplicates, filters, main_window, page_url
    ) -> int:
        items = self._collect_all_items(driver, wait, config, filters, page_url)
        self._on_progress({'type': 'files_total', 'total': len(items)})

        if not items:
            self._log('warning', f"No files found on static page for {student_id}.")
            self._write_log_csv(download_dir_abs, [{
                'studentId': student_id, 'fileName': 'N/A', 'fileType': 'N/A',
                'directory': student_root_folder, 'action': 'not downloaded',
                'actionComment': 'No file links detected on page (RadDock/tabs/headings/tables)',
                'validation': 'traversed',
            }])
            return 0

        self._log('info', f"Found {len(items)} file(s) for {student_id}.")
        folder_template = config.get('folder_template', '{studentId}/{segment}/{category}')
        seen_names: dict = {}
        files_done = 0
        batch_logs = []

        for item in items:
            if self._abort():
                break

            seg = item.get('segment', '') or config.get('default_segment', 'Uncategorized')
            sub = item.get('subcategory', 'Uncategorized')
            file_name = item['link_text']
            href = item.get('href')

            category_folder = build_output_path(
                folder_template, download_dir_abs, student_id, seg, sub, file_name
            )

            safe_check = _sanitize(file_name)
            seen_names[safe_check] = seen_names.get(safe_check, 0) + 1
            occurrence = seen_names[safe_check]
            if allow_duplicates and occurrence > 1:
                base, ext = os.path.splitext(safe_check)
                effective_disk_name = f"{base}__{occurrence}{ext}"
            else:
                effective_disk_name = safe_check

            file_ext = os.path.splitext(file_name)[1].replace('.', '').upper() or 'UNKNOWN'

            result = self._force_download_via_fetch(driver, href, category_folder, effective_disk_name)
            download_ok = result in ('downloaded', 'duplicate')
            if download_ok:
                files_done += 1

            status, comment = {
                'downloaded': ('downloaded', 'Success'),
                'duplicate':  ('downloaded', 'Duplicate content already on disk'),
                'failed':     ('failed',     'Extraction failed'),
            }.get(result, ('failed', 'Unknown result'))

            batch_logs.append({
                'studentId': student_id, 'fileName': file_name, 'fileType': file_ext,
                'directory': category_folder, 'action': status, 'actionComment': comment,
                'validation': 'verified' if download_ok else result,
            })
            self._on_progress({'type': 'file_done', 'files_done': files_done})

        if batch_logs:
            self._write_log_csv(download_dir_abs, batch_logs)

        learning_engine.record_student_insight(student_id, 'file_count', files_done)
        return files_done

    def _collect_all_items(self, driver, wait, config: dict, filters: dict, page_url: str) -> list:
        segment_names = config.get('segment_names') or []
        if segment_names:
            items = []
            for seg_name in segment_names:
                container = self._find_named_container(driver, wait, seg_name)
                if container is not None:
                    seg_items = self._collect_items_from_container(driver, container, config, filters, seg_name)
                    items.extend(seg_items)
                    self._record_segment_signature(page_url, seg_name, container)
            if items:
                return _dedup(items)
        return self._discover_containers(driver, wait, config, filters)

    def _discover_containers(self, driver, wait, config: dict, filters: dict) -> list:
        items = []
        # 1. RadDock
        for container in self._find_all_raddock_containers(driver):
            items.extend(self._collect_items_from_container(driver, container, config, filters, None))
        if items:
            return _dedup(items)
        # 2. Tabs
        tab_items = self._collect_from_tabs(driver, wait, config, filters)
        if tab_items:
            return _dedup(tab_items)
        # 3. Headings
        heading_items = self._collect_from_headings(driver, config, filters)
        if heading_items:
            return _dedup(heading_items)
        # 4. Whole-page fallback
        return _dedup(self._collect_items_from_container(driver, None, config, filters, None))

    def _find_all_raddock_containers(self, driver) -> list:
        try:
            titles = driver.find_elements(
                By.XPATH, "//div[contains(@class,'RadDock')]//span[contains(@class,'title')]")
        except Exception:
            return []
        containers = []
        for t in titles:
            c = _climb_to_class(t, 'RadDock')
            if c is not None:
                containers.append(c)
        return containers

    def _collect_from_tabs(self, driver, wait, config: dict, filters: dict) -> list:
        try:
            tabs = driver.find_elements(
                By.XPATH,
                "//*[@role='tab'] | //ul[contains(@class,'nav-tabs')]//a"
            )
        except Exception:
            tabs = []
        if not tabs:
            return []

        items = []
        for tab in tabs:
            try:
                if not tab.is_displayed():
                    continue
                tab_name = tab.text.strip()
                if not tab_name:
                    continue
                try:
                    tab.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", tab)
                time.sleep(0.4)
                panel_id = tab.get_attribute('aria-controls')
                panel = None
                if panel_id:
                    try:
                        panel = driver.find_element(By.ID, panel_id)
                    except Exception:
                        panel = None
                if panel is None:
                    panel = driver
                seg_items = self._collect_items_from_container(driver, panel, config, filters, tab_name)
                items.extend(seg_items)
            except Exception:
                continue
        return items

    def _collect_from_headings(self, driver, config: dict, filters: dict,
                                heading_tags=('h2', 'h3')) -> list:
        items = []
        for tag in heading_tags:
            try:
                headings = driver.find_elements(By.TAG_NAME, tag)
            except Exception:
                headings = []
            for h in headings:
                try:
                    name = h.text.strip()
                    if not name:
                        continue
                    container = driver.execute_script(
                        "return arguments[0].closest('div') ? "
                        "(arguments[0].closest('div').parentElement || arguments[0].closest('div')) : null;",
                        h
                    )
                    if container is None:
                        continue
                    seg_items = self._collect_items_from_container(driver, container, config, filters, name)
                    items.extend(seg_items)
                except Exception:
                    continue
        return items

    def _record_segment_signature(self, page_url: str, seg_name: str, container) -> None:
        try:
            tag = container.tag_name
            cls = container.get_attribute('class') or ''
            learning_engine.record_domain_strategy(
                page_url, f'segment_sig::{seg_name}', {'tag': tag, 'class': cls[:120]})
        except Exception:
            pass

    def _find_container_by_signature(self, driver, wait, sig: dict, seg_name: str):
        return None

    def _find_named_container(self, driver, wait, name: str):
        # RadDock
        try:
            xpath = (
                "//div[contains(@class,'RadDock')]//span[contains(@class,'title') and "
                f"contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                f"'{name.lower()}')]"
            )
            for t in driver.find_elements(By.XPATH, xpath):
                c = _climb_to_class(t, 'RadDock')
                if c is not None:
                    return c
        except Exception:
            pass
        # Heading
        try:
            xpath = f"//h2[normalize-space(.)={_xpath_literal(name)}] | //h3[normalize-space(.)={_xpath_literal(name)}]"
            for h in driver.find_elements(By.XPATH, xpath):
                container = driver.execute_script(
                    "return arguments[0].closest('div') ? "
                    "(arguments[0].closest('div').parentElement || arguments[0].closest('div')) : null;",
                    h
                )
                if container is not None:
                    return container
        except Exception:
            pass
        # Tab
        try:
            xpath = f"//*[@role='tab' and contains(normalize-space(.), {_xpath_literal(name)})]"
            tabs = driver.find_elements(By.XPATH, xpath)
            if tabs:
                tab = tabs[0]
                try:
                    tab.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", tab)
                time.sleep(0.4)
                panel_id = tab.get_attribute('aria-controls')
                if panel_id:
                    try:
                        return driver.find_element(By.ID, panel_id)
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def _collect_items_from_container(
        self, driver, context, config: dict, filters: dict, category_prefix
    ) -> list:
        items = []
        rows = self._get_rows(driver, config, context)
        for row in rows:
            category = self._get_category_for_row(row, config)
            for link_el in self._get_links_for_row(row, config):
                item = self._make_item(link_el, category, category_prefix, category)
                if item and self._item_passes_filters(item, filters):
                    items.append(item)
        return items

    def _make_item(self, link_el, category: str, segment: str, subcategory: str) -> dict | None:
        try:
            text = link_el.text.strip() or link_el.get_attribute('title') or 'file'
            href = link_el.get_attribute('href')
            if not href:
                return None
            return {
                'category': category, 'segment': segment or '', 'subcategory': subcategory,
                'link_text': text, 'href': href,
            }
        except Exception:
            return None

    def _get_rows(self, driver, config: dict, context=None) -> list:
        min_columns = config.get('min_columns', 2)
        no_nested = f"not(.//tr[td and count(td) >= {min_columns} and .//a])"
        xpath = f".//tr[td and count(td) >= {min_columns} and .//a and {no_nested}]"
        try:
            if context is not None and context != driver:
                rows = context.find_elements(By.XPATH, xpath)
                if rows:
                    return rows
            return driver.find_elements(By.XPATH, f"/{xpath}" if not xpath.startswith('//') else xpath.replace('.//', '//', 1))
        except Exception:
            return []

    def _get_category_for_row(self, row, config: dict) -> str:
        col_index = config.get('category_column_index', 2)
        try:
            text = row.find_element(By.XPATH, f"./td[{col_index}]").text.strip()
            if text:
                return _sanitize(text.split('\n')[0].strip())
        except Exception:
            pass
        try:
            for td in row.find_elements(By.XPATH, "./td"):
                text = td.text.strip()
                if text and not td.find_elements(By.XPATH, ".//a"):
                    return _sanitize(text.split('\n')[0].strip())
        except Exception:
            pass
        return "Uncategorized"

    def _get_links_for_row(self, row, config: dict) -> list:
        try:
            links = row.find_elements(By.XPATH, config.get('link_selector_xpath') or ".//a")
        except Exception:
            return []
        return [l for l in links if self._is_probable_file_link(l, config)]

    def _is_probable_file_link(self, el, config: dict) -> bool:
        try:
            text = el.text.strip()
            href = (el.get_attribute("href") or "").strip()
            if not text or text.lower() in DEFAULT_EXCLUDE_TEXT:
                return False
            href_lower = href.lower()
            if href_lower in ("", "#", "javascript:void(0)", "javascript:void(0);"):
                return False
            ext_from_href = os.path.splitext(urllib.parse.urlparse(href).path)[1].lower().lstrip('.')
            ext_from_text = os.path.splitext(text)[1].lower().lstrip('.')
            known_exts = KNOWN_FILE_EXTENSIONS | config.get('extra_extensions', set())
            if ext_from_href in known_exts or ext_from_text in known_exts:
                return True
            path = urllib.parse.urlparse(href).path.lower()
            return any(hint in path or hint in href_lower for hint in DOWNLOAD_PATH_HINTS)
        except Exception:
            return False

    def _item_passes_filters(self, item: dict, filters: dict) -> bool:
        if not filters:
            return True
        name = (item.get('link_text') or '').lower()
        href = item.get('href') or ''
        if filters.get('type_include'):
            ext = os.path.splitext(name)[1].lower().lstrip('.')
            if not ext:
                ext = os.path.splitext(urllib.parse.urlparse(href).path)[1].lower().lstrip('.')
            if ext not in filters['type_include']:
                return False
        if filters.get('name_include') and not any(k in name for k in filters['name_include']):
            return False
        if filters.get('name_exclude') and     any(k in name for k in filters['name_exclude']):
            return False
        return True

    # ------------------------------------------------------------------ #
    # MYPATH DOCUMENTS mode (h2-sectioned MUI tables, dialog-trigger buttons)
    # ------------------------------------------------------------------ #

    def _is_mypath_documents_page(self, driver, page_url: str | None = None) -> bool:
        """Detect whether the current page is a MyPath Documents page.

        Two independent signals, either one is sufficient:

        SIGNAL A (original): a role="tab-panel" element whose id or
        aria-labelledby literally contains the substring 'document', with at
        least one <h2> heading inside it. This assumes MUI generates a
        semantically-named panel id, which it does not always do -- some
        renders use generic ids like 'tabpanel-1' with no 'document'
        substring anywhere, which made this signal miss real documents pages
        entirely (root cause of '.../documents' links producing 0 files).

        SIGNAL B (route + content based, new): the URL's route segment is
        literally 'documents' (e.g. '.../kwi/{studentId}/documents') AND the
        page actually contains at least one <h2> heading and at least one
        MyPath file-row control (a dialog-trigger button or a per-row
        download-icon button). This doesn't depend on any particular id
        naming scheme -- it recognizes the page by what's actually on it.
        """
        try:
            panels = driver.find_elements(
                By.XPATH,
                "//*[@role='tab-panel' and (contains(@id,'document') or contains(@aria-labelledby,'document'))]"
            )
            for panel in panels:
                if panel.find_elements(By.TAG_NAME, 'h2'):
                    return True
        except Exception:
            pass

        try:
            route_is_documents = bool(page_url) and _route_key(page_url) == 'documents'
            if route_is_documents:
                has_h2 = bool(driver.find_elements(By.TAG_NAME, 'h2'))
                has_file_controls = bool(driver.find_elements(
                    By.XPATH,
                    "//button[@name='dialog-trigger'] | //button[.//*[@data-testid='FileDownloadIcon']]"
                ))
                if has_h2 and has_file_controls:
                    self._log('debug', "[AUTO-DETECT] MyPath Documents page recognized via route+content "
                                        "signal (panel id lacked 'document' substring).")
                    return True
        except Exception:
            pass

        return False

    def _process_mypath_documents_mode(
        self, driver, wait, config, student_id, student_root_folder,
        download_dir_abs, allow_duplicates, filters, main_window, page_url
    ) -> int:
        """
        MyPath Documents page flow:
        1. Find the documents tab-panel.
        2. For each <h2> section heading (Admissions, Graduation, Other, ...):
           a. Collect all <button name="dialog-trigger"> rows from the MUI table below it.
           b. Each button's text = filename, the adjacent chip = file type/category.
           c. Click the button -> wait for download dialog -> grab file URL -> fetch bytes.
        3. Save files under {studentId}/{section}/{filename}.
        4. Record the section names in learning so next run is instant.
        """
        # Find the documents panel
        panel = None
        try:
            panels = driver.find_elements(
                By.XPATH,
                "//*[@role='tab-panel' and (contains(@id,'document') or contains(@aria-labelledby,'document'))]"
            )
            for p in panels:
                if p.find_elements(By.TAG_NAME, 'h2'):
                    panel = p
                    break
        except Exception as e:
            self._log('error', f"Could not find documents panel for {student_id}: {e}")
            return 0

        if panel is None:
            # Fallback: the strict tab-panel-id lookup found nothing (this is
            # what used to happen on '.../documents' pages whose MUI panel id
            # doesn't literally contain the word 'document'). Since
            # _collect_mypath_sections only needs a container that holds all
            # the target <h2> sections, the page body works just as well as
            # scope -- it doesn't need to be the exact tab-panel element.
            try:
                if driver.find_elements(By.TAG_NAME, 'h2') and driver.find_elements(
                    By.XPATH, "//button[@name='dialog-trigger'] | //button[.//*[@data-testid='FileDownloadIcon']]"
                ):
                    panel = driver.find_element(By.TAG_NAME, 'body')
                    self._log('debug', f"Documents panel for {student_id} not found via strict id lookup; "
                                        f"falling back to whole-page scan (panel id lacked 'document' substring).")
            except Exception:
                pass

        if panel is None:
            self._log('warning', f"No documents panel found for {student_id}.")
            return 0

        # Collect all h2 sections and their file rows
        sections = self._collect_mypath_sections(driver, panel, config, filters)

        if not sections:
            self._log('warning', f"No document sections with files found for {student_id}.")
            self._log_mypath_section_diagnostics(driver, panel, student_id)
            self._write_log_csv(download_dir_abs, [{
                'studentId': student_id, 'fileName': 'N/A', 'fileType': 'N/A',
                'directory': student_root_folder, 'action': 'not downloaded',
                'actionComment': 'No file rows found in MyPath Documents sections',
                'validation': 'traversed',
            }])
            return 0

        total_files = sum(len(s['files']) for s in sections)
        self._log('info', f"Found {len(sections)} section(s), {total_files} file(s) for {student_id}.")
        self._on_progress({'type': 'files_total', 'total': total_files})

        # Record the section structure for learning
        section_names = [s['name'] for s in sections]
        learning_engine.record_domain_strategy(page_url, 'mypath_section_names', section_names)

        folder_template = config.get('folder_template', '{studentId}/{segment}/{category}')
        seen_names: dict = {}
        files_done = 0
        batch_logs = []

        for section in sections:
            seg_name = section['name']   # e.g. 'Admissions'
            self._log('info', f"  Section '{seg_name}': {len(section['files'])} file(s).")

            for file_info in section['files']:
                if self._abort():
                    break

                file_name  = file_info['name']        # e.g. 'Orientation Packet 2024 (3).pdf'
                file_type  = file_info['type']        # e.g. 'External Application'
                btn_element = file_info['element']    # the dialog-trigger button element

                # Use file_type as the sub-category for folder organisation
                category_for_folder = _sanitize(file_type) if file_type and file_type != 'N/A' else seg_name
                category_folder = build_output_path(
                    folder_template, download_dir_abs,
                    student_id, seg_name, category_for_folder, file_name
                )

                safe_check = _sanitize(file_name)
                seen_names[safe_check] = seen_names.get(safe_check, 0) + 1
                occurrence = seen_names[safe_check]
                if allow_duplicates and occurrence > 1:
                    base, ext = os.path.splitext(safe_check)
                    effective_disk_name = f"{base}__{occurrence}{ext}"
                else:
                    effective_disk_name = safe_check

                file_ext = os.path.splitext(file_name)[1].replace('.', '').upper() or 'UNKNOWN'
                self._log('info', f"    [{files_done+1}/{total_files}] '{file_name}' [{file_type}]")

                # Download: click dialog-trigger -> capture URL from modal -> fetch bytes
                result = self._download_mypath_file(
                    driver, btn_element, category_folder, effective_disk_name, main_window
                )
                download_ok = result in ('downloaded', 'duplicate')
                if download_ok:
                    files_done += 1

                status, comment = {
                    'downloaded': ('downloaded', 'Success'),
                    'duplicate':  ('downloaded', 'Duplicate content already on disk'),
                    'failed':     ('failed',     'Could not retrieve file from dialog'),
                }.get(result, ('failed', 'Unknown result'))

                batch_logs.append({
                    'studentId':     student_id,
                    'fileName':      file_name,
                    'fileType':      file_ext,
                    'directory':     category_folder,
                    'action':        status,
                    'actionComment': comment,
                    'validation':    'verified' if download_ok else result,
                })
                self._on_progress({'type': 'file_done', 'files_done': files_done})

        if batch_logs:
            self._write_log_csv(download_dir_abs, batch_logs)

        learning_engine.record_student_insight(student_id, 'file_count', files_done)
        return files_done

    def _log_mypath_section_diagnostics(self, driver, panel, student_id: str):
        """Dump structural info about the documents panel when section detection
        comes up empty, so we can see WHY instead of guessing: how many h2
        headings exist, how many rows total, and whether dialog-trigger buttons
        are present anywhere on the page (in case they're outside the expected
        h2-sibling container, or the page genuinely has zero files for this
        student).
        """
        try:
            h2_count = len(panel.find_elements(By.TAG_NAME, 'h2'))
        except Exception:
            h2_count = -1
        try:
            row_count_in_panel = len(panel.find_elements(By.XPATH, ".//tr[@role='row']"))
        except Exception:
            row_count_in_panel = -1
        try:
            dialog_triggers_anywhere = len(driver.find_elements(By.XPATH, "//button[@name='dialog-trigger']"))
        except Exception:
            dialog_triggers_anywhere = -1
        try:
            h2_texts = [h.text.strip() for h in panel.find_elements(By.TAG_NAME, 'h2')][:10]
        except Exception:
            h2_texts = []
        try:
            panel_text_preview = panel.text[:300].replace('\n', ' | ')
        except Exception:
            panel_text_preview = '<unavailable>'
        self._log('debug',
                   f"[DIAG:mypath_sections] student={student_id} h2_count={h2_count} "
                   f"h2_texts={h2_texts} rows_in_panel={row_count_in_panel} "
                   f"dialog_triggers_anywhere_on_page={dialog_triggers_anywhere} "
                   f"panel_text_preview={panel_text_preview!r}")

    def _collect_mypath_sections(self, driver, panel, config: dict, filters: dict) -> list:
        """
        Walk the documents panel and return a list of dicts:
          {'name': str, 'files': [{'name': str, 'type': str, 'element': WebElement}]}

        Structure in DOM:
          <div>  (section wrapper)
            <div>  (header row with h2 + buttons)
              <h2>Admissions</h2>
              <button>Generate 1098T</button>   (optional — ignored here)
              <button>Upload File(s)</button>    (ignored)
              <button>Download All (.zip)</button> (ignored)
            </div>
            <div class="MuiTableContainer-root">  (file table)
              <table>
                <tbody>
                  <tr role="row">
                    <td>icon</td>
                    <td><button name="dialog-trigger">filename.pdf</button></td>
                    <td><span class="MuiChip-label">Type chip text</span></td>
                    <td>description</td>
                    <td>date</td>
                    <td>download icon button</td>  ← this is the per-file download button
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        """
        sections = []
        try:
            h2_elements = panel.find_elements(By.TAG_NAME, 'h2')
        except Exception:
            return sections

        for h2 in h2_elements:
            try:
                section_name = h2.text.strip()
                if not section_name:
                    continue

                # The section container must hold BOTH the h2 header and its
                # sibling <table> -- NOT just the immediate parent. MUI applies
                # 'MuiBox-root' to many nested elements, so h2.closest('.MuiBox-root')
                # matches the header row (h2 + toolbar buttons) FIRST, one level
                # too shallow -- the actual <table> is a SIBLING of that header
                # row, not a descendant, so it was never found (confirmed via a
                # real page dump: 'rows_in_panel=6' at the panel level, but 0
                # rows found per-section). Walk up from the h2 until an ancestor
                # that actually contains a <table> is found, which is robust to
                # the exact class names/depth MUI happens to use.
                section_container = driver.execute_script(
                    "var el = arguments[0].parentElement; "
                    "while (el && el.tagName !== 'BODY') { "
                    "  if (el.querySelector('table')) return el; "
                    "  el = el.parentElement; "
                    "} "
                    "return arguments[0].parentElement ? arguments[0].parentElement.parentElement : null;",
                    h2
                )
                if section_container is None:
                    continue

                # Find the MUI table within this section container
                file_rows = []
                try:
                    table_rows = section_container.find_elements(
                        By.XPATH, ".//tr[@role='row']"
                    )
                except Exception:
                    table_rows = []

                for row in table_rows:
                    try:
                        # The filename button: <button name="dialog-trigger"> in td[2]
                        file_btn = None
                        try:
                            file_btn = row.find_element(
                                By.XPATH, ".//button[@name='dialog-trigger']"
                            )
                        except Exception:
                            pass

                        if file_btn is None:
                            continue

                        fname = file_btn.text.strip()
                        if not fname or 'Upload' in fname:
                            continue

                        # Get the file type from the MuiChip in td[3]
                        ftype = 'N/A'
                        try:
                            chip = row.find_element(
                                By.XPATH,
                                ".//span[contains(@class,'MuiChip-label')]"
                            )
                            ftype = chip.text.strip() or 'N/A'
                        except Exception:
                            pass

                        # The per-file download icon button (last td) — this is more reliable
                        # than the dialog-trigger because it directly downloads the file
                        # without opening a preview modal. It's the small icon button at the
                        # right end of each row.
                        dl_btn = None
                        try:
                            # Last button in the row that has a FileDownloadIcon svg
                            dl_btn = row.find_element(
                                By.XPATH,
                                ".//button[.//*[@data-testid='FileDownloadIcon']]"
                            )
                        except Exception:
                            pass

                        # Use per-file download button if available, else dialog-trigger
                        effective_btn = dl_btn if dl_btn else file_btn

                        item = {
                            'name':    fname,
                            'type':    ftype,
                            'element': effective_btn,
                            'use_direct_download': dl_btn is not None,
                        }

                        # Apply filters
                        pseudo = {
                            'category':  section_name,
                            'link_text': fname,
                            'href':      '',
                        }
                        if not self._item_passes_filters(pseudo, filters):
                            continue

                        file_rows.append(item)
                    except Exception as row_err:
                        self._log('debug', f"Error reading row in section '{section_name}': {row_err}")
                        continue

                if file_rows:
                    sections.append({'name': _sanitize(section_name), 'files': file_rows})

            except Exception as sec_err:
                self._log('debug', f"Error processing section: {sec_err}")
                continue

        return sections

    def _download_mypath_file(
        self, driver, btn_element, folder_path: str, file_name: str, main_window: str
    ) -> str:
        """
        Download a file from the MyPath Documents page by clicking its
        dialog-trigger button (the filename text button in each row).

        Three outcomes after the click:

        A) A new tab/window opens  →  image/file opened directly in browser.
           We fetch the URL from the new tab. If the tab self-closes before
           we can read it (Chrome native-download trigger), we recover the
           file that Chrome's download manager already wrote to folder_path.

        B) A MUI dialog opens on the same page  →  PDF or other preview.
           We poll the dialog's <iframe>/<embed> src attribute until React's
           async API call populates it (up to 15 s), then fetch the bytes.
           The previous implementation read the src instantly, always got
           empty-string/about:blank, and failed. Polling fixes that.

        C) Nothing visible changes  →  silent direct download by Chrome.
           Handled by _resolve_native_download which looks for new files on
           disk that weren't there before the click.
        """
        # Resync to the main window before touching anything -- if a previous
        # file's popup/native-download interaction left chromedriver's notion
        # of the 'current' window desynced (observed: CDP calls starting to
        # fail with 'target window already closed' partway through a batch of
        # otherwise-successful native-download files, eventually killing the
        # whole browser session), this re-anchors every file to a known-good
        # starting point instead of carrying that drift into the next click.
        try:
            driver.switch_to.window(main_window)
        except Exception:
            pass

        # Only re-arm Chrome's CDP download behavior when the destination
        # folder actually changed. Re-issuing Page.setDownloadBehavior /
        # Browser.setDownloadBehavior before EVERY single file -- including
        # runs of files that all silently native-download with no tab/modal
        # ever opening -- is unnecessary CDP churn, and was the immediate
        # precursor to a full browser-session crash in a real run (four
        # files native-downloaded fine, then the fifth file's arm call
        # itself failed with 'target window already closed', followed by the
        # whole session dying on the next navigation).
        if getattr(self, '_armed_download_dir', None) != folder_path:
            try:
                set_download_behavior(driver, folder_path, log_func=self._log)
                self._armed_download_dir = folder_path
            except Exception:
                pass
        os.makedirs(folder_path, exist_ok=True)
        try:
            files_before_click = set(os.listdir(folder_path))
        except FileNotFoundError:
            files_before_click = set()

        windows_before = set(driver.window_handles)
        dialogs_before = self._get_open_dialog_signatures(driver)

        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn_element)
            time.sleep(0.15)
            try:
                btn_element.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn_element)
        except Exception as e:
            self._log('error', f"Could not click download button for '{file_name}': {e}")
            return 'failed'

        # Give the page up to 2 s to open either a new tab OR a dialog.
        # We poll both simultaneously so we don't commit to one path too
        # early -- especially important for the tab case, where Chrome can
        # open AND close a tab within ~200 ms for a native-download link.
        new_win = None
        new_modal = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            candidates = set(driver.window_handles) - windows_before
            if candidates:
                new_win = next(iter(candidates))
                break
            fresh = self._find_new_mypath_modal(driver, dialogs_before)
            if fresh is not None:
                new_modal = fresh
                break
            time.sleep(0.1)

        # ── Path A: new tab / window ──────────────────────────────────────
        if new_win:
            try:
                driver.switch_to.window(new_win)
                time.sleep(0.5)
                file_url = driver.current_url
            except Exception as tab_err:
                # Tab already auto-closed — Chrome completed a native
                # download and dismissed the transient tab itself. The file
                # is on disk; recover it.
                self._log('debug',
                    f"Tab for '{file_name}' closed itself (native download): {tab_err}")
                try:
                    driver.switch_to.window(main_window)
                except Exception:
                    pass
                return self._resolve_native_download(folder_path, file_name, files_before_click)

            # We are now inside the new tab. Fetch the file directly from
            # this same-origin context (avoids CORS issues with GCS signed
            # URLs that block cross-origin fetch from the main page). Always
            # close the tab and return to main_window regardless of outcome.
            self._log('debug', f"New tab URL for '{file_name}': {file_url}")
            try:
                result = self._force_download_via_fetch(driver, file_url, folder_path, file_name)
            except Exception:
                result = 'failed'
            finally:
                try:
                    driver.close()
                except Exception:
                    pass
                try:
                    driver.switch_to.window(main_window)
                except Exception:
                    pass
            if result == 'failed':
                native = self._resolve_native_download(folder_path, file_name, files_before_click)
                if native != 'failed':
                    return native
            return result

        # ── Path B: MUI dialog opened ─────────────────────────────────────
        # If _find_new_mypath_modal already found it during the polling loop
        # above, use it directly; otherwise wait a bit longer.
        if new_modal is None:
            try:
                new_modal = WebDriverWait(driver, 3, poll_frequency=0.2).until(
                    lambda d: self._find_new_mypath_modal(d, dialogs_before)
                )
            except Exception:
                pass

        if new_modal is not None:
            # Poll the dialog's embedded media element until React's async
            # fetch populates its src (up to 15 s). The previous code read
            # the src ONCE immediately after the dialog appeared -- but MUI
            # renders the dialog shell first (src='') and only fills the src
            # after an API round-trip, so the instant read always got empty
            # string or about:blank and fell through to "strategy exhausted".
            file_url = None
            try:
                file_url = WebDriverWait(driver, 15, poll_frequency=0.4).until(
                    lambda d: self._extract_mypath_file_url(d, new_modal) or None
                )
            except Exception:
                pass

            if file_url:
                self._log('debug', f"Modal iframe/embed URL for '{file_name}': {file_url}")
                # Force the GCS signed URL to download as a file rather than
                # render inline. The URL has response-content-disposition=inline
                # which tells Chrome to display it -- replace that with
                # 'attachment' so Chrome saves it directly without trying to
                # open a PDF viewer tab (which produces a downloads.htm error
                # page instead of the actual file).
                download_url = re.sub(
                    r'response-content-disposition=[^&]*',
                    'response-content-disposition=attachment',
                    file_url
                )
                result = self._force_download_via_fetch(driver, download_url, folder_path, file_name)
                if result == 'failed':
                    # fetch() failed (CORS or auth) -- open the URL in a new
                    # tab where the GCS origin context allows it, but set
                    # download behavior first so Chrome saves it rather than
                    # trying to render it.
                    set_download_behavior(driver, folder_path, log_func=self._log)
                    wins_before_fetch = set(driver.window_handles)
                    driver.execute_script(f"window.open({repr(download_url)}, '_blank');")
                    time.sleep(0.8)
                    fetch_tab = next(iter(set(driver.window_handles) - wins_before_fetch), None)
                    if fetch_tab:
                        try:
                            driver.switch_to.window(fetch_tab)
                            time.sleep(0.3)
                            result = self._force_download_via_fetch(driver, download_url, folder_path, file_name)
                        except Exception:
                            result = 'failed'
                        finally:
                            try:
                                driver.close()
                            except Exception:
                                pass
                            try:
                                driver.switch_to.window(main_window)
                            except Exception:
                                pass
                    if result == 'failed':
                        result = self._resolve_native_download(folder_path, file_name, files_before_click)
                self._close_mypath_modal(driver, new_modal)
                return result

            # The dialog opened but we couldn't get a URL from its content.
            # Try clicking any Download button INSIDE the dialog.
            try:
                dl_btn = new_modal.find_element(
                    By.XPATH,
                    ".//*[@data-testid='FileDownloadIcon']/ancestor::button "
                    "| .//button[contains(translate(.,'DOWNLOAD','download'),'download')]"
                )
                if dl_btn.is_displayed():
                    wins_before2 = set(driver.window_handles)
                    try:
                        dl_btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", dl_btn)
                    time.sleep(1.5)
                    new_wins2 = set(driver.window_handles) - wins_before2
                    if new_wins2:
                        w2 = next(iter(new_wins2))
                        try:
                            driver.switch_to.window(w2)
                            time.sleep(0.5)
                            file_url = driver.current_url
                            result = self._force_download_via_fetch(
                                driver, file_url, folder_path, file_name)
                            driver.close()
                            driver.switch_to.window(main_window)
                        except Exception:
                            try:
                                driver.switch_to.window(main_window)
                            except Exception:
                                pass
                            result = self._resolve_native_download(
                                folder_path, file_name, files_before_click)
                        self._close_mypath_modal(driver, new_modal)
                        return result
            except Exception:
                pass

            self._close_mypath_modal(driver, new_modal)

        # ── Path C: neither tab nor dialog appeared ───────────────────────
        # Chrome may have silently downloaded the file directly.
        native = self._resolve_native_download(folder_path, file_name, files_before_click)
        if native != 'failed':
            return native

        self._log('error',
            f"Could not obtain download URL for '{file_name}' "
            f"\u2014 modal/tab strategy exhausted.")
        return 'failed'

    def _resolve_native_download(self, folder_path: str, file_name: str, files_before: set) -> str:
        """Check whether Chrome's own download mechanism already saved a file
        into folder_path. Waits for any in-progress download to finish, then
        looks for whichever file is new and renames it to the expected
        file_name. Ignores Chrome error pages (downloads.htm / .html files
        that are obviously not the intended file type).
        """
        try:
            wait_for_downloads_to_finish(folder_path, timeout=15)
            files_after = set(os.listdir(folder_path))
        except FileNotFoundError:
            return 'failed'

        new_files = [f for f in (files_after - files_before)
                     if not f.endswith(('.crdownload', '.tmp'))]

        # Filter out Chrome error pages -- 'downloads.htm' / 'downloads.html'
        # are generated when Chrome can't open/render a URL rather than
        # downloading the actual file. Treating them as downloads produces
        # mis-named files (e.g. renaming downloads.htm to Foo.pdf).
        intended_ext = os.path.splitext(file_name)[1].lower()
        html_exts = {'.htm', '.html'}
        non_html = [f for f in new_files if os.path.splitext(f)[1].lower() not in html_exts]
        # Use non-html files if any exist; only fall back to html if that's
        # all that landed AND the intended file is itself html.
        if non_html:
            new_files = non_html
        elif intended_ext not in html_exts:
            # Only html files landed but we expected something else -- Chrome
            # saved an error page instead of the real file. Reject them.
            for f in new_files:
                try:
                    os.remove(os.path.join(folder_path, f))
                except Exception:
                    pass
            self._log('debug', f"No native download found on disk for '{file_name}' "
                               f"(only Chrome error page(s) landed; removed them).")
            return 'failed'

        if not new_files:
            self._log('debug', f"No native download found on disk for '{file_name}' either.")
            return 'failed'

        # Take the most recently modified of the remaining candidates.
        new_files.sort(
            key=lambda f: os.path.getmtime(os.path.join(folder_path, f)), reverse=True)
        downloaded_path = os.path.join(folder_path, new_files[0])

        # Wait until the file is no longer locked by Chrome before renaming.
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                with open(downloaded_path, 'rb'):
                    pass
                break  # file is readable / not locked
            except OSError:
                time.sleep(0.3)

        safe_name = _sanitize(file_name)
        real_ext = os.path.splitext(downloaded_path)[1]
        if not os.path.splitext(safe_name)[1] and real_ext:
            safe_name += real_ext
        target_path = os.path.join(folder_path, safe_name)

        if os.path.abspath(downloaded_path) == os.path.abspath(target_path):
            self._log('info', f"Recovered native download for '{file_name}' -> {target_path}")
            return 'downloaded'

        try:
            if os.path.exists(target_path):
                if _file_content_hash(target_path) == _file_content_hash(downloaded_path):
                    os.remove(downloaded_path)
                    self._log('info', f"Native download for '{file_name}' matched existing file; "
                                       f"removed duplicate.")
                    return 'duplicate'
                base, ext = os.path.splitext(safe_name)
                counter = 1
                while os.path.exists(target_path):
                    target_path = os.path.join(folder_path, f"{base}__{counter + 1}{ext}")
                    counter += 1
            os.rename(downloaded_path, target_path)
        except Exception as e:
            self._log('warning', f"Could not rename native download '{downloaded_path}' to "
                                  f"'{target_path}': {e} -- file is on disk under its original name.")
            return 'downloaded'

        self._log('info', f"Recovered native download for '{file_name}' -> {target_path}")
        return 'downloaded'

    def _get_open_dialog_signatures(self, driver) -> set:
        """Returns a frozenset of (aria-labelledby|id, display-state) tuples
        for every role='dialog' on the page right now.

        We track BOTH the id/label AND a crude display snapshot so that a
        dialog which React merely hides (display:none) rather than removes
        is still distinguishable from a freshly opened one. offsetParent is
        unreliable in MUI -- a dialog with 'visibility:hidden' or inside a
        Portal with transform can have offsetParent=null while still being
        what the user sees. Using getBoundingClientRect().width > 0 is more
        robust and doesn't depend on the CSS visibility model.
        """
        try:
            return set(driver.execute_script(
                """
                return Array.from(document.querySelectorAll('div[role="dialog"]'))
                  .map(d => {
                    var r = d.getBoundingClientRect();
                    var visible = r.width > 0 && r.height > 0;
                    return (d.getAttribute('aria-labelledby') || d.id || '') + ':' + visible;
                  });
                """
            ))
        except Exception:
            return set()

    def _find_new_mypath_modal(self, driver, before_signatures: set):
        """Find a dialog that is currently visible AND whose signature
        (id/label + visible=True) was not in before_signatures.

        Returns None (not raises) if no such fresh dialog exists yet, so
        WebDriverWait keeps polling rather than giving up.
        """
        try:
            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
        except Exception:
            return None
        fresh = []
        for d in dialogs:
            try:
                r = driver.execute_script(
                    "var r=arguments[0].getBoundingClientRect();"
                    "return {w:r.width,h:r.height};", d)
                if not r or r['w'] <= 0 or r['h'] <= 0:
                    continue
                sig = (d.get_attribute('aria-labelledby') or
                       d.get_attribute('id') or '') + ':True'
                if sig not in before_signatures:
                    fresh.append(d)
            except Exception:
                continue
        return fresh[-1] if fresh else None

    def _find_mypath_modal(self, driver):
        """Find any open MUI dialog that contains a file preview or download
        trigger. Used only as a last-resort fallback -- prefer
        _find_new_mypath_modal wherever a 'before' signature set is
        available, since this method can't distinguish a fresh dialog from
        a stale leftover one."""
        try:
            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
            for d in dialogs:
                if d.is_displayed():
                    return d
        except Exception:
            pass
        return None

    def _extract_mypath_file_url(self, driver, modal) -> str | None:
        """
        Extract a non-empty, non-placeholder file URL from an open dialog.

        Called repeatedly by WebDriverWait(15s) in _download_mypath_file,
        so it must return None (not raise) when the src is not ready yet.
        React renders the dialog shell first and populates the iframe/embed
        src only after an async API call completes -- that's why an
        immediate single read always returned '' or 'about:blank' before.
        """
        PLACEHOLDERS = {'', 'about:blank', 'about:srcdoc'}
        for tag, attr in (('embed', 'src'), ('embed', 'data'),
                          ('iframe', 'src'), ('object', 'data')):
            try:
                els = modal.find_elements(By.TAG_NAME, tag)
            except Exception:
                els = []
            for el in els:
                try:
                    url = (el.get_attribute(attr) or '').strip()
                    if url and url not in PLACEHOLDERS:
                        return url
                except Exception:
                    pass
        # Images served via API path (e.g. /api/files/…)
        try:
            for img in modal.find_elements(By.XPATH, ".//img"):
                src = (img.get_attribute('src') or '').strip()
                if src and src not in PLACEHOLDERS and '/api/' in src:
                    return src
        except Exception:
            pass
        # Anchor with explicit download attribute or API/download path
        try:
            a = modal.find_element(
                By.XPATH,
                ".//a[@download and @href] "
                "| .//a[contains(@href,'/api/files')] "
                "| .//a[contains(@href,'/download')]"
            )
            href = (a.get_attribute('href') or '').strip()
            if href and href not in PLACEHOLDERS:
                return href
        except Exception:
            pass
        return None

    def _close_mypath_modal(self, driver, modal=None):
        """Closes the given dialog and verifies it's actually gone/hidden
        before returning, instead of firing ESC/click-and-hope. A dialog
        that's still technically open (even though the code assumed it had
        closed) was the root cause of every subsequent file's modal search
        matching the wrong, stale dialog -- so this now (a) prefers closing
        the SPECIFIC dialog via its own scoped Close button rather than a
        global search that could hit a different dialog's button, and (b)
        polls to confirm that specific dialog's signature has actually
        disappeared from the visible set before moving on.
        """
        signature = None
        if modal is not None:
            try:
                signature = modal.get_attribute('aria-labelledby') or modal.get_attribute('id')
            except Exception:
                modal = None

        closed_via_button = False
        if modal is not None:
            try:
                for b in modal.find_elements(
                    By.XPATH,
                    ".//button[contains(translate(.,'CLOSE','close'),'close')] "
                    "| .//button[@aria-label='close' or @aria-label='Close']"
                ):
                    if b.is_displayed():
                        driver.execute_script("arguments[0].click();", b)
                        closed_via_button = True
                        break
            except Exception:
                pass

        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        except Exception:
            pass

        if not closed_via_button:
            try:
                for b in driver.find_elements(
                    By.XPATH,
                    "//div[@role='dialog']//button[contains(translate(.,'CLOSE','close'),'close')] "
                    "| //div[@role='dialog']//button[@aria-label='close' or @aria-label='Close']"
                ):
                    if b.is_displayed():
                        driver.execute_script("arguments[0].click();", b)
                        break
            except Exception:
                pass

        if signature:
            # Use the same getBoundingClientRect visibility check as
            # _get_open_dialog_signatures so the two methods agree on what
            # "visible" means. The old _gone() used offsetParent which is
            # unreliable in MUI Portals and disagreed with the signature
            # format (':True'/':False'), causing the 4s timeout to fire on
            # every single file even when the dialog had actually closed.
            sig_visible = signature + ':True'
            def _gone(d):
                try:
                    current_sigs = set(d.execute_script(
                        """
                        return Array.from(document.querySelectorAll('div[role="dialog"]'))
                          .map(function(x) {
                            var r = x.getBoundingClientRect();
                            var vis = r.width > 0 && r.height > 0;
                            return (x.getAttribute('aria-labelledby') || x.id || '') + ':' + vis;
                          });
                        """
                    ))
                    return sig_visible not in current_sigs
                except Exception:
                    return True
            try:
                WebDriverWait(driver, 3, poll_frequency=0.15).until(_gone)
            except Exception:
                self._log('debug', f"Dialog '{signature}' did not confirm closed within timeout; "
                                    f"continuing anyway.")
        time.sleep(0.2)

    # ------------------------------------------------------------------ #
    # GENERATE DOCUMENTS mode (MyPath dropdown + Generate buttons)
    # ------------------------------------------------------------------ #

    def _get_dropdown_button(self, driver, wait, config: dict):
        xpath = config.get('dropdown_button_xpath') or (
            "//button[.//span[contains(@class,'MuiLoadingButton-label')]]"
            "[.//*[@data-testid='ArrowDropDownRoundedIcon']]"
        )
        try:
            return WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        except Exception:
            return None

    def _wait_for_no_backdrop(self, driver, timeout=3):
        try:
            WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located(
                (By.XPATH, "//div[contains(@class,'MuiBackdrop-root') and contains(@class,'MuiModal-backdrop')]")))
        except Exception:
            pass

    def _get_current_program_name(self, driver, config: dict):
        xpath = config.get('current_program_xpath') or "//a[contains(@href,'/academics/programs/')]/h2"
        try:
            for el in driver.find_elements(By.XPATH, xpath):
                text = (el.get_attribute('textContent') or '').strip()
                if text:
                    return text
        except Exception:
            pass
        return None

    def _get_dropdown_option_elements(self, driver, config: dict):
        xpath = config.get('dropdown_option_xpath') or (
            "//li[@role='option' or @role='menuitem' or contains(@class,'MuiMenuItem-root') "
            "or contains(@class,'MuiAutocomplete-option')]"
        )
        try:
            return driver.find_elements(By.XPATH, xpath)
        except Exception:
            return []

    def _get_dropdown_program_list(self, driver, wait, config: dict):
        btn = self._get_dropdown_button(driver, wait, config)
        if btn is None:
            current = self._get_current_program_name(driver, config)
            return [current] if current else []
        btn.click()
        time.sleep(0.8)
        names = []
        for opt in self._get_dropdown_option_elements(driver, config):
            try:
                if not opt.is_displayed():
                    continue
            except Exception:
                continue
            text = (opt.get_attribute('textContent') or '').strip()
            if text:
                names.append(text)
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        except Exception:
            pass
        self._wait_for_no_backdrop(driver)
        if not names:
            current = self._get_current_program_name(driver, config)
            return [current] if current else []
        return names

    def _select_dropdown_program(self, driver, wait, program_text: str, config: dict, occurrence=1):
        btn = self._get_dropdown_button(driver, wait, config)
        if btn is None:
            return self._get_current_program_name(driver, config) is not None
        self._wait_for_no_backdrop(driver)
        try:
            btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.8)
        match_count, target = 0, None
        for opt in self._get_dropdown_option_elements(driver, config):
            try:
                if not opt.is_displayed():
                    continue
            except Exception:
                continue
            text = (opt.get_attribute('textContent') or '').strip()
            if text == program_text:
                match_count += 1
                if match_count == occurrence:
                    target = opt
                    break
        if target is not None:
            try:
                target.click()
            except Exception:
                driver.execute_script("arguments[0].click();", target)
            time.sleep(1.2)
            self._wait_for_no_backdrop(driver)
            return True
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        except Exception:
            pass
        self._wait_for_no_backdrop(driver)
        return False

    def _get_generate_buttons(self, driver, config: dict) -> list:
        xpath = config.get('generate_buttons_xpath') or "//button[.//span[starts-with(normalize-space(.),'Generate')]]"
        try:
            WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.XPATH, xpath)))
        except Exception:
            pass
        try:
            return driver.find_elements(By.XPATH, xpath)
        except Exception:
            return []

    def _generate_button_label(self, btn) -> str:
        text = (btn.get_attribute('textContent') or '').strip()
        return GENERATE_PREFIX_RE.sub('', text).strip() or "Document"

    @staticmethod
    def _program_folder_name(program_text: str) -> str:
        parts = [p.strip() for p in (program_text or "").split('-') if p.strip()]
        if len(parts) >= 2:
            return f"{parts[0]} - {parts[1]}"
        return parts[0] if parts else (program_text or "Program").strip()

    def _find_modal(self, driver, config: dict):
        modal_xpath = config.get('modal_xpath') or (
            "//div[@role='dialog'][.//embed or .//iframe or .//object "
            "or contains(translate(., 'DOWNLOAD', 'download'), 'download')]"
        )
        try:
            modals = driver.find_elements(By.XPATH, modal_xpath)
        except Exception:
            modals = []
        if not modals:
            return None
        visible = [m for m in modals if m.is_displayed()]
        return visible[-1] if visible else modals[-1]

    def _extract_pdf_source_url(self, driver, modal):
        if modal is None:
            return None
        for tag, attr in (('embed', 'src'), ('embed', 'data'), ('iframe', 'src'), ('object', 'data')):
            try:
                els = modal.find_elements(By.TAG_NAME, tag)
            except Exception:
                els = []
            for el in els:
                url = el.get_attribute(attr)
                if url and url not in ('about:blank', ''):
                    return url
        return None

    def _close_modal_or_window(self, driver, main_window, switched_window):
        try:
            if switched_window and switched_window in driver.window_handles:
                driver.close()
            driver.switch_to.window(main_window)
        except Exception:
            pass
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
            time.sleep(0.5)
        except Exception:
            pass
        try:
            for b in driver.find_elements(By.XPATH, "//button[contains(translate(., 'CLOSE', 'close'), 'close')]"):
                if b.is_displayed():
                    driver.execute_script("arguments[0].click();", b)
                    break
        except Exception:
            pass
        time.sleep(0.4)

    def _generate_and_capture_document(
        self, driver, config: dict, btn, dest_folder: str, doc_label: str, main_window: str
    ):
        windows_before = set(driver.window_handles)
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            driver.execute_script("arguments[0].click();", btn)
        except Exception as e:
            return 'failed', f"Could not click Generate button: {e}"

        time.sleep(1.5)

        windows_after = set(driver.window_handles)
        new_windows = windows_after - windows_before
        switched_window = None
        if new_windows:
            switched_window = next(iter(new_windows))
            driver.switch_to.window(switched_window)
            time.sleep(1.0)

        pdf_url = None
        try:
            modal = WebDriverWait(driver, 20).until(lambda d: self._find_modal(d, config))
            pdf_url = self._extract_pdf_source_url(driver, modal)
        except Exception as e:
            self._log('warning', f"No modal/embedded PDF detected after clicking '{doc_label}': {e}")

        if not pdf_url:
            try:
                current_url = driver.current_url
                if current_url and ('.pdf' in current_url.lower() or current_url.lower().startswith('blob:')):
                    pdf_url = current_url
            except Exception:
                pass

        if not pdf_url:
            self._log('error', f"Could not locate a PDF source for '{doc_label}'.")
            self._close_modal_or_window(driver, main_window, switched_window)
            return 'failed', 'Could not locate generated PDF (embed/iframe src not found)'

        file_name = _sanitize(doc_label) + '.pdf'
        result = self._force_download_via_fetch(driver, pdf_url, dest_folder, file_name)
        self._close_modal_or_window(driver, main_window, switched_window)

        comment = {
            'downloaded': 'Success',
            'duplicate': 'Duplicate content already on disk',
            'failed': 'Could not fetch generated PDF bytes',
        }.get(result, 'Unknown result')
        return result, comment

    def _force_download_via_fetch(self, driver, file_url: str, folder_path: str, file_name: str) -> str:
        try:
            script = """
            const url = arguments[0];
            const callback = arguments[arguments.length - 1];
            fetch(url, { credentials: 'include' })
                .then(r => r.ok ? r.blob() : null)
                .then(blob => {
                    if (!blob || blob.size === 0) { callback('ERROR'); return; }
                    const reader = new FileReader();
                    reader.onloadend = () => callback(reader.result);
                    reader.onerror = () => callback('ERROR');
                    reader.readAsDataURL(blob);
                }).catch(() => callback('ERROR'));
            """
            result = driver.execute_async_script(script, file_url)
            if not result or result.startswith('ERROR'):
                return 'failed'
            header, encoded = result.split(',', 1)
            file_bytes = base64.b64decode(encoded)
            mime_type = "application/octet-stream"
            if header.startswith("data:") and ";" in header:
                mime_type = header[5:header.index(";")]

            safe_name = _sanitize(file_name.strip())
            if not os.path.splitext(safe_name)[1]:
                safe_name += MIME_TO_EXT.get(mime_type.lower(), "")

            os.makedirs(folder_path, exist_ok=True)
            out_path = os.path.join(folder_path, safe_name)

            if os.path.exists(out_path):
                if _file_content_hash(out_path) == hashlib.md5(file_bytes).hexdigest():
                    return 'duplicate'
                base, ext = os.path.splitext(safe_name)
                counter = 1
                while os.path.exists(out_path):
                    out_path = os.path.join(folder_path, f"{base}__{counter + 1}{ext}")
                    counter += 1

            with open(out_path, 'wb') as out_f:
                out_f.write(file_bytes)
            return 'downloaded'
        except Exception as e:
            self._log('debug', f"force_download_via_fetch failed for '{file_name}': {e}")
            return 'failed'

    def _click_and_download(self, driver, element, folder_path: str, file_name: str, main_window: str) -> bool:
        result = self._force_download_via_fetch(driver, element.get_attribute('href') or '', folder_path, file_name)
        return result in ('downloaded', 'duplicate')

    def _process_generate_documents_mode(
        self, driver, wait, config, student_id, student_root_folder,
        download_dir_abs, filters, main_window
    ) -> int:
        try:
            programs = self._get_dropdown_program_list(driver, wait, config)
        except Exception as e:
            self._log('error', f"Could not read program dropdown for {student_id}: {e}")
            return 0

        self._log('info', f"Found {len(programs)} program(s) for {student_id}: {programs}")
        if not programs:
            self._write_log_csv(download_dir_abs, [{
                'studentId': student_id, 'fileName': 'N/A', 'fileType': 'N/A',
                'directory': student_root_folder, 'action': 'not downloaded',
                'actionComment': 'No programs found in dropdown / no Generate buttons on page',
                'validation': 'traversed',
            }])
            return 0

        name_counts = {}
        for p in programs:
            name_counts[p] = name_counts.get(p, 0) + 1
        seen_occurrence = {}
        dest_folder = os.path.join(student_root_folder, "Profile Docs")

        first_pass_buttons = self._get_generate_buttons(driver, config)
        total_est = len(programs) * max(len(first_pass_buttons), 1)
        self._on_progress({'type': 'files_total', 'total': total_est})

        files_done = 0
        batch_logs = []

        for program_text in programs:
            if self._abort():
                break

            seen_occurrence[program_text] = seen_occurrence.get(program_text, 0) + 1
            occurrence = seen_occurrence[program_text]
            needs_suffix = name_counts[program_text] > 1

            if len(programs) > 1:
                if not self._select_dropdown_program(driver, wait, program_text, config, occurrence=occurrence):
                    self._log('warning', f"Could not select program '{program_text}' for {student_id}; skipping.")
                    continue
                time.sleep(1.0)

            folder_name = self._program_folder_name(program_text)
            num_buttons = len(self._get_generate_buttons(driver, config))

            for b_idx in range(num_buttons):
                if self._abort():
                    break
                try:
                    current_buttons = self._get_generate_buttons(driver, config)
                    if b_idx >= len(current_buttons):
                        continue
                    btn = current_buttons[b_idx]
                    base_label = self._generate_button_label(btn)
                    doc_label = f"{folder_name} - {base_label}"
                    if needs_suffix:
                        doc_label = f"{doc_label} {occurrence}"

                    pseudo_item = {'category': folder_name, 'link_text': doc_label + '.pdf', 'href': ''}
                    if filters and not self._item_passes_filters(pseudo_item, filters):
                        continue

                    result, comment = self._generate_and_capture_document(
                        driver, config, btn, dest_folder, doc_label, main_window)

                    if result in ('downloaded', 'duplicate'):
                        files_done += 1
                    else:
                        self._log('warning', f"'{doc_label}' unavailable for {student_id}: {comment}.")

                    self._on_progress({'type': 'file_done', 'files_done': files_done})
                    status_map = {'downloaded': 'downloaded', 'duplicate': 'downloaded', 'failed': 'failed'}
                    action_status = status_map.get(result, 'failed')
                    batch_logs.append({
                        'studentId': student_id, 'fileName': f"{doc_label}.pdf", 'fileType': 'PDF',
                        'directory': dest_folder, 'action': action_status, 'actionComment': comment,
                        'validation': 'file downloaded' if action_status == 'downloaded' else action_status,
                    })
                except Exception as e:
                    self._log('error', f"Error generating document [{b_idx}] for program '{program_text}', "
                                        f"student {student_id}: {e}.")
                    batch_logs.append({
                        'studentId': student_id, 'fileName': f"{folder_name} - Error_Item_{b_idx}", 'fileType': 'N/A',
                        'directory': dest_folder, 'action': 'error',
                        'actionComment': str(e)[:200], 'validation': 'traversed'
                    })

        if batch_logs:
            self._write_log_csv(download_dir_abs, batch_logs)

        learning_engine.record_student_insight(student_id, 'file_count', files_done)
        return files_done

    # ------------------------------------------------------------------
    # CSV Log
    # ------------------------------------------------------------------

    def _write_log_csv(self, download_dir: str, log_data: list) -> None:
        log_file_path = os.path.join(download_dir, "automation_execution_log.csv")
        file_exists = os.path.isfile(log_file_path)
        headers = ['studentId', 'fileName', 'fileType', 'directory', 'action', 'actionComment', 'validation']
        try:
            with open(log_file_path, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(log_data)
        except Exception as e:
            self._log('error', f"Failed writing log CSV: {e}")


# ================================================================== #
# Module-level helpers
# ================================================================== #

def _xpath_literal(value: str) -> str:
    """Build a safe XPath string literal for arbitrary text."""
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts  = value.split('"')
    pieces = [f'"{p}"' for p in parts]
    return "concat(" + ", '\"', ".join(pieces) + ")"


def _climb_to_class(element, class_fragment: str):
    """Walk up the DOM from *element* until we find an ancestor whose @class
    contains *class_fragment*, or return None if we reach <body>."""
    try:
        container = element
        while container and container.tag_name != 'body':
            classes = container.get_attribute('class') or ''
            if class_fragment in classes:
                return container
            container = container.find_element(By.XPATH, '..')
    except Exception:
        pass
    return None


def _dedup(items: list) -> list:
    """Deduplicate items by normalised href, preferring categorised over Uncategorized."""
    by_href: dict = {}
    no_href = []
    for it in items:
        key = _normalize_href(it.get('href', '')) if it.get('href') else None
        if key is None:
            no_href.append(it)
            continue
        existing = by_href.get(key)
        if existing is None:
            by_href[key] = it
        elif existing.get('subcategory') == 'Uncategorized' and it.get('subcategory') != 'Uncategorized':
            by_href[key] = it
    return list(by_href.values()) + no_href


def _file_content_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()
