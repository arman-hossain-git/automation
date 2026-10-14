import os
import time
import csv
import hashlib
import logging
import threading
import urllib.parse
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def _file_content_hash(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


class FileDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCR Educator Dashboard File Downloader")
        self.root.geometry("900x780")
        self.root.resizable(False, False)

        self.config_file = ""
        self.links_file = ""
        self.download_dir = ""
        self.logger = None  # Set up once a download dir is known

        # User preference: save duplicate-named files with a __2, __3 … suffix
        # rather than skipping them. Controlled by the checkbox in the UI.
        self.allow_duplicates = tk.BooleanVar(value=True)

        # --- Real-time progress tracking state ---
        self.is_running = False  # True while EITHER the download or verify phase is active
        self.current_phase = None  # 'download' | 'verify' | None
        self.total_students = 0
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_student_files_done = 0
        self.current_student_files_total = 0
        self.completed_students_file_counts = []  # File counts per fully-processed student, used for the running average
        self.total_files_downloaded = 0  # Running count across the whole run, used for Average Duration/File

        # Phase-specific start/end times. Elapsed Duration is computed from
        # whichever phase is currently active (or the last one that ran),
        # so the clock pauses between phases and resumes when the next one
        # starts, instead of counting straight through.
        self.download_start_time = None
        self.download_end_time = None
        self.verify_start_time = None
        self.verify_end_time = None

        # Cooperative abort flag. Checked at safe checkpoints (between
        # files, between students, between verification records) so a
        # click stops things after the current item finishes rather than
        # killing mid-action and leaving things in a half-done state.
        self.abort_requested = False

        self.create_widgets()
        self.tick_clock()  # Starts the self-repeating live clock/elapsed-time updater

    # ------------------------------------------------------------------
    # Text logger setup
    # ------------------------------------------------------------------
    def setup_text_logger(self):
        """Creates a per-run text log file with every step, click, and
        error written to it, saved inside the chosen download directory."""
        log_path = os.path.join(self.download_dir, "automation_debug.log")

        logger = logging.getLogger("pcr_downloader")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()  # Avoid duplicate handlers across runs

        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        self.logger = logger
        self.logger.info("=" * 70)
        self.logger.info("New automation run started.")
        return log_path

    def log(self, level, message):
        """Safe wrapper in case logger isn't initialized yet."""
        if self.logger:
            getattr(self.logger, level)(message)

    def safe_messagebox(self, kind, title, message):
        """Shows a messagebox safely from any thread.

        Tkinter is not thread-safe: calling messagebox.* directly from the
        background automation thread (start_download runs in one) can fail
        to display, or raise its own thread-related exception that gets
        swallowed -- which is exactly how a real error can look like
        "nothing happened, no popup, no crash." Routing the call through
        root.after(0, ...) hands it back to the main/UI thread, where it's
        safe to show.
        """
        func = {'info': messagebox.showinfo, 'error': messagebox.showerror, 'warning': messagebox.showwarning}[kind]
        self.root.after(0, lambda: func(title, message))

    # ------------------------------------------------------------------
    # Real-time stats panel
    # ------------------------------------------------------------------
    def tick_clock(self):
        """Runs every second via root.after to keep the current date/time
        and elapsed duration live, independent of what the automation
        loop is doing (Selenium calls can block for seconds at a time).

        Elapsed Duration reflects whichever phase is CURRENTLY running
        (download or verify). Between phases -- e.g. after download
        finishes and before verify is clicked -- the clock holds steady
        at that phase's final duration rather than continuing to climb,
        and starts fresh from zero when the next phase begins."""
        now = datetime.datetime.now()
        self.lbl_now_time.config(text=now.strftime("%Y-%m-%d %H:%M:%S"))
        self.lbl_elapsed.config(text=self.current_phase_elapsed_text(now))
        self.root.after(1000, self.tick_clock)

    def current_phase_elapsed_text(self, now=None):
        """Returns the Elapsed Duration label text for whichever phase is
        active or was most recently active."""
        now = now or datetime.datetime.now()

        if self.current_phase == 'download':
            if self.download_start_time:
                elapsed = now - self.download_start_time
                return self.format_timedelta(elapsed) + " (downloading)"
        elif self.current_phase == 'verify':
            if self.verify_start_time:
                elapsed = now - self.verify_start_time
                return self.format_timedelta(elapsed) + " (verifying)"

        # Not currently running -- show whichever phase finished last.
        if self.verify_start_time and self.verify_end_time:
            elapsed = self.verify_end_time - self.verify_start_time
            return self.format_timedelta(elapsed) + " (verify done)"
        if self.download_start_time and self.download_end_time:
            elapsed = self.download_end_time - self.download_start_time
            return self.format_timedelta(elapsed) + " (download done)"
        return "00:00:00"

    @staticmethod
    def format_timedelta(td):
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def average_files_per_student(self):
        """Running average of files-per-student across students that have
        been FULLY completed so far. Falls back to None if no student has
        completed yet (nothing to average from)."""
        if not self.completed_students_file_counts:
            return None
        return sum(self.completed_students_file_counts) / len(self.completed_students_file_counts)

    def average_duration_per_file_text(self):
        """Live running average of wall-clock time per downloaded file
        during the download phase: (elapsed so far) / (files downloaded so
        far). Updates continuously while running, since both the elapsed
        time and the file count are changing in real time."""
        if self.total_files_downloaded <= 0 or not self.download_start_time:
            return "-"
        reference_end = datetime.datetime.now() if self.current_phase == 'download' else (self.download_end_time or datetime.datetime.now())
        elapsed_seconds = (reference_end - self.download_start_time).total_seconds()
        if elapsed_seconds <= 0:
            return "-"
        avg_seconds = elapsed_seconds / self.total_files_downloaded
        return f"{avg_seconds:.1f}s/file"

    def average_duration_per_student_text(self):
        """Live running average of wall-clock time per traversed student
        during the download phase: (elapsed so far) / (students traversed
        so far)."""
        if self.students_traversed <= 0 or not self.download_start_time:
            return "-"
        reference_end = datetime.datetime.now() if self.current_phase == 'download' else (self.download_end_time or datetime.datetime.now())
        elapsed_seconds = (reference_end - self.download_start_time).total_seconds()
        if elapsed_seconds <= 0:
            return "-"
        avg_seconds = elapsed_seconds / self.students_traversed
        return self.format_timedelta(datetime.timedelta(seconds=avg_seconds)) + "/student"

    def refresh_stats_panel(self):
        """Recomputes and redraws every live statistic. Called at each
        meaningful checkpoint (student start, file downloaded, student
        finished) rather than purely on the 1-second timer, so numbers
        update immediately rather than waiting for the next tick."""
        self.lbl_students_progress.config(text=f"{self.students_traversed} / {self.total_students}")
        self.lbl_current_student.config(text=str(self.current_student_id))

        if self.current_student_files_total > 0:
            self.lbl_files_progress.config(text=f"{self.current_student_files_done} / {self.current_student_files_total}")
        else:
            self.lbl_files_progress.config(text=f"{self.current_student_files_done} / -")

        avg_files = self.average_files_per_student()
        if avg_files is not None and self.total_students > 0:
            # Projected grand total across ALL students, based on the
            # average files-per-student seen so far among fully-completed
            # students. This is an ESTIMATE of the final total, not a sum
            # of already-counted files (that was the bug: summing
            # completed-student counts AND the in-progress student's count
            # double-counted the student who had just finished).
            approx_grand_total = avg_files * self.total_students
            self.lbl_files_approx.config(
                text=f"{self.total_files_downloaded} / ~{approx_grand_total:.0f} (avg {avg_files:.1f}/student)"
            )
        else:
            self.lbl_files_approx.config(text=f"{self.total_files_downloaded} / ~? (no average yet)")

        if self.download_start_time:
            self.lbl_start_time.config(text=self.download_start_time.strftime("%Y-%m-%d %H:%M:%S"))

        self.lbl_avg_duration_file.config(text=self.average_duration_per_file_text())
        self.lbl_avg_duration_student.config(text=self.average_duration_per_student_text())

        # ETA: based on average time-per-student observed so far during the
        # download phase (more reliable than time-per-file, since per-file
        # click/wait overhead is roughly constant but per-student file
        # counts vary). Only meaningful while the download phase is active.
        if self.current_phase == 'download' and self.download_start_time and self.students_traversed > 0 and self.total_students > 0:
            now = datetime.datetime.now()
            elapsed = (now - self.download_start_time).total_seconds()
            avg_seconds_per_student = elapsed / self.students_traversed
            remaining_students = self.total_students - self.students_traversed
            remaining_seconds = avg_seconds_per_student * remaining_students
            eta = now + datetime.timedelta(seconds=remaining_seconds)
            self.lbl_eta.config(text=eta.strftime("%Y-%m-%d %H:%M:%S"))
        elif self.download_end_time:
            self.lbl_eta.config(text=self.download_end_time.strftime("%Y-%m-%d %H:%M:%S") + " (done)")
        else:
            self.lbl_eta.config(text="Calculating...")

        self.root.update_idletasks()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def create_widgets(self):
        header = tk.Label(self.root, text="PCR Dashboard File Downloader", font=("Arial", 16, "bold"))
        header.pack(pady=15)

        frame = tk.Frame(self.root)
        frame.pack(pady=10, fill="x", padx=20)

        tk.Label(frame, text="Credentials File (.txt):", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=10)
        self.lbl_config = tk.Label(frame, text="No credentials file loaded", fg="gray", wraplength=350, justify="left")
        self.lbl_config.grid(row=0, column=1, sticky="w", padx=10)
        btn_browse_config = tk.Button(frame, text="Browse", command=self.browse_config)
        btn_browse_config.grid(row=0, column=2, sticky="e", pady=10)

        tk.Label(frame, text="Student Links CSV (.csv):", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=10)
        self.lbl_links = tk.Label(frame, text="No CSV file selected", fg="gray", wraplength=350, justify="left")
        self.lbl_links.grid(row=1, column=1, sticky="w", padx=10)
        btn_browse_links = tk.Button(frame, text="Browse", command=self.browse_links)
        btn_browse_links.grid(row=1, column=2, sticky="e", pady=10)

        tk.Label(frame, text="Save Files To:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=10)
        self.lbl_dir = tk.Label(frame, text="No folder selected", fg="gray", wraplength=350, justify="left")
        self.lbl_dir.grid(row=2, column=1, sticky="w", padx=10)
        btn_browse_dir = tk.Button(frame, text="Browse", command=self.browse_directory)
        btn_browse_dir.grid(row=2, column=2, sticky="e", pady=10)

        # --- Duplicate-file handling option ---
        dup_frame = tk.Frame(self.root)
        dup_frame.pack(fill="x", padx=20, pady=(0, 4))
        self.chk_allow_duplicates = tk.Checkbutton(
            dup_frame,
            text='Download files with duplicate titles (saves as filename__2, filename__3 … )',
            variable=self.allow_duplicates,
            font=("Arial", 9),
            anchor="w",
        )
        self.chk_allow_duplicates.pack(side="left")

        self.lbl_status = tk.Label(self.root, text="Status: Idle", font=("Arial", 10))
        self.lbl_status.pack(pady=5)

        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=800, mode="determinate")
        self.progress.pack(pady=10)

        # --- Real-time statistics panel ---
        # 10 stats total, arranged into two columns of 5 each:
        #   Left column  = progress-oriented stats (what's been done)
        #   Right column = timing-oriented stats (how long things take)
        # Each label/value sub-column gets an explicit fixed width (via a
        # spacer label with `width=`) so the two stat columns size
        # consistently and don't shrink-to-fit in a way that clips text.
        stats_frame = tk.LabelFrame(self.root, text=" Live Progress ", font=("Arial", 10, "bold"), padx=15, pady=10)
        stats_frame.pack(pady=5, fill="x", padx=20)

        left_col = tk.Frame(stats_frame)
        left_col.grid(row=0, column=0, sticky="nw", padx=(0, 30))
        right_col = tk.Frame(stats_frame)
        right_col.grid(row=0, column=1, sticky="nw")

        LABEL_WIDTH = 28   # chars -- fits "Files Downloaded (Current):" etc without truncation
        VALUE_WIDTH = 32   # chars -- fits full datetimes and longer value strings on one line

        def add_stat_row(parent, row, label_text):
            tk.Label(parent, text=label_text, font=("Arial", 9, "bold"), anchor="w", width=LABEL_WIDTH, justify="left").grid(row=row, column=0, sticky="w", pady=4)
            value_lbl = tk.Label(parent, text="-", font=("Arial", 9), anchor="w", fg="#1565C0", width=VALUE_WIDTH, justify="left")
            value_lbl.grid(row=row, column=1, sticky="w", padx=10, pady=4)
            return value_lbl

        # Left column: progress-oriented
        self.lbl_students_progress = add_stat_row(left_col, 0, "Students Traversed:")
        self.lbl_current_student = add_stat_row(left_col, 1, "Current Student:")
        self.lbl_files_progress = add_stat_row(left_col, 2, "Files Downloaded (Current):")
        self.lbl_files_approx = add_stat_row(left_col, 3, "Done/Approx. Total:")
        self.lbl_start_time = add_stat_row(left_col, 4, "Automation Start:")

        # Right column: timing-oriented
        self.lbl_now_time = add_stat_row(right_col, 0, "Current Date & Time:")
        self.lbl_elapsed = add_stat_row(right_col, 1, "Elapsed Duration:")
        self.lbl_eta = add_stat_row(right_col, 2, "Est. Completion Time:")
        self.lbl_avg_duration_file = add_stat_row(right_col, 3, "Average Duration/File:")
        self.lbl_avg_duration_student = add_stat_row(right_col, 4, "Average Duration/Student:")

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=15)

        self.btn_start = tk.Button(
            btn_frame, text="Start Download Process", font=("Arial", 11, "bold"),
            bg="#4CAF50", fg="white", padx=10, pady=5,
            command=lambda: threading.Thread(target=self.start_download, daemon=True).start()
        )
        self.btn_start.pack(side="left", padx=8)

        self.btn_verify = tk.Button(
            btn_frame, text="Verify Downloads", font=("Arial", 11, "bold"),
            bg="#2196F3", fg="white", padx=10, pady=5,
            command=lambda: threading.Thread(target=self.verify_downloads, daemon=True).start()
        )
        self.btn_verify.pack(side="left", padx=8)

        self.btn_abort = tk.Button(
            btn_frame, text="Abort", font=("Arial", 11, "bold"),
            bg="#F44336", fg="white", padx=10, pady=5,
            command=self.request_abort, state="disabled"
        )
        self.btn_abort.pack(side="left", padx=8)

    def request_abort(self):
        """Called when the user clicks Abort. Sets a cooperative flag that
        both start_download and verify_downloads check at safe checkpoints
        (between files / between students / between verification records),
        so the current item finishes cleanly rather than being killed
        mid-action."""
        if not self.is_running:
            return
        self.abort_requested = True
        self.btn_abort.config(state="disabled")
        self.lbl_status.config(text="Status: Abort requested -- finishing current item, then stopping...")
        self.log('warning', "Abort requested by user. Will stop at the next safe checkpoint.")

    def set_buttons_running(self, running):
        """Centralizes the Start/Verify/Abort enable-disable logic so the
        three buttons can't get out of sync with each other. Always call
        this through root.after if invoked from a background thread."""
        if running:
            self.btn_start.config(state="disabled")
            self.btn_verify.config(state="disabled")
            self.btn_abort.config(state="normal")
        else:
            self.btn_start.config(state="normal")
            self.btn_verify.config(state="normal")
            self.btn_abort.config(state="disabled")

    def browse_config(self):
        self.config_file = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
        if self.config_file:
            self.lbl_config.config(text=os.path.basename(self.config_file), fg="black")

    def browse_links(self):
        self.links_file = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if self.links_file:
            self.lbl_links.config(text=os.path.basename(self.links_file), fg="black")

    def browse_directory(self):
        self.download_dir = filedialog.askdirectory()
        if self.download_dir:
            self.lbl_dir.config(text=self.download_dir, fg="black")

    # ------------------------------------------------------------------
    # Config / CSV loading
    # ------------------------------------------------------------------
    def load_credentials(self):
        config_data = {}
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        config_data[key.strip().lower()] = val.strip()
            valid = all(k in config_data for k in ['username', 'password', 'login_url', 'login_btn_xpath'])
            if not valid:
                self.log('error', f"Credentials file missing required keys. Found keys: {list(config_data.keys())}")
                return None
            self.log('info', "Credentials file loaded and validated successfully.")
            return config_data
        except Exception as e:
            self.log('error', f"Failed to read credentials file: {e}")
            return None

    def read_links_csv(self):
        students = []
        try:
            with open(self.links_file, mode='r', encoding='utf-8-sig') as f:
                sample = f.read(2048)
                f.seek(0)
                delimiter = ';' if ';' in sample and ',' not in sample else ','

                reader = csv.reader(f, delimiter=delimiter)
                headers = [h.strip().lower().replace(" ", "").replace("_", "") for h in next(reader, [])]

                id_idx, link_idx = None, None
                for idx, h in enumerate(headers):
                    if h in ['studentid', 'id', 'student']:
                        id_idx = idx
                    if h in ['link', 'url', 'studentlink']:
                        link_idx = idx

                if id_idx is None or link_idx is None:
                    self.log('error', f"CSV headers did not contain recognizable id/link columns. Headers found: {headers}")
                    return []

                for row in reader:
                    if row and len(row) > max(id_idx, link_idx):
                        students.append({
                            'studentId': row[id_idx].strip(),
                            'link': row[link_idx].strip()
                        })
            self.log('info', f"Loaded {len(students)} student records from links CSV.")
            return students
        except Exception as e:
            self.log('error', f"Failed to read links CSV: {e}")
            return []

    # ------------------------------------------------------------------
    # CSV execution log (machine-readable summary)
    # ------------------------------------------------------------------
    def write_log_csv(self, log_data):
        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")
        file_exists = os.path.isfile(log_file_path)
        headers = ['studentId', 'fileName', 'fileType', 'directory', 'action', 'actionComment', 'validation']
        try:
            with open(log_file_path, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(log_data)
        except Exception as e:
            self.log('error', f"Failed writing to execution log CSV: {e}")

    def update_log_validation(self, student_id, file_name, validation_status):
        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")
        if not os.path.isfile(log_file_path):
            return

        rows = []
        try:
            with open(log_file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                for row in reader:
                    if row['studentId'] == student_id and row['fileName'] == file_name:
                        row['validation'] = validation_status
                    rows.append(row)

            with open(log_file_path, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except Exception as e:
            self.log('error', f"Failed updating validation status for {student_id}/{file_name}: {e}")

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------
    def wait_for_downloads_to_finish(self, folder_path, timeout=40):
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                pending = [f for f in os.listdir(folder_path) if f.endswith('.crdownload') or f.endswith('.tmp')]
            except FileNotFoundError:
                pending = []
            if not pending:
                return True
            time.sleep(0.5)
        self.log('warning', f"Timed out waiting for in-progress downloads to finish in: {folder_path}")
        return False

    def set_download_behavior(self, driver, folder_path):
        """Forces ALL file types -- including images that Chrome would
        otherwise render inline instead of downloading -- to save to disk.

        Page.setDownloadBehavior alone is not enough: Chrome treats
        navigable/viewable mime types (image/*, text/plain, application/pdf)
        as 'display' targets unless download is forced at the Browser level
        with download_restrictions disabled too."""
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": folder_path
            })
        except Exception as e:
            self.log('warning', f"Page.setDownloadBehavior failed: {e}")

        try:
            # Browser-level behavior overrides Chrome's "open viewable types
            # inline" default, which is the actual cause of images opening
            # in a new tab/render view instead of saving as files.
            driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": folder_path,
                "eventsEnabled": True
            })
        except Exception as e:
            self.log('warning', f"Browser.setDownloadBehavior failed (older Chrome may not support this): {e}")

    def force_download_via_fetch(self, driver, file_url, folder_path, file_name):
        """Fallback for images/files that still open in a new tab/viewer
        instead of downloading: re-fetches the resource using the browser's
        authenticated session via JS fetch + Blob, then saves bytes directly
        to disk through CDP, bypassing the renderer entirely."""
        try:
            script = """
            const url = arguments[0];
            const callback = arguments[arguments.length - 1];
            fetch(url, { credentials: 'include' })
                .then(r => {
                    if (!r.ok) {
                        callback('ERROR:HTTP_' + r.status + '_' + r.statusText);
                        return null;
                    }
                    return r.blob();
                })
                .then(blob => {
                    if (!blob) return;
                    if (blob.size === 0) {
                        callback('ERROR:EMPTY_BLOB');
                        return;
                    }
                    const reader = new FileReader();
                    reader.onloadend = () => callback(reader.result);
                    reader.onerror = () => callback('ERROR:BLOB_READ_FAILED');
                    reader.readAsDataURL(blob);
                })
                .catch(e => callback('ERROR:FETCH_EXCEPTION_' + e.toString()));
            """
            result = driver.execute_async_script(script, file_url)
            if not result or result.startswith('ERROR:'):
                self.log('error', f"Fetch-fallback failed for {file_name} (url: {file_url}): {result}")
                return False

            header, encoded = result.split(',', 1)
            import base64
            file_bytes = base64.b64decode(encoded)

            # header looks like "data:image/jpeg;base64" -- pull the real
            # mime type out so we can attach the correct extension even
            # when the dashboard's link text has no extension at all
            # (e.g. a label like "View Photo" instead of "photo.jpg").
            mime_type = "application/octet-stream"
            if header.startswith("data:") and ";" in header:
                mime_type = header[5:header.index(";")]

            mime_to_ext = {
                "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
                "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
                "image/tiff": ".tiff", "application/pdf": ".pdf",
                "text/plain": ".txt", "application/msword": ".doc",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            }

            safe_name = file_name.strip() if file_name and file_name.strip() else ""
            if not safe_name:
                safe_name = os.path.basename(urllib.parse.urlparse(file_url).path) or "downloaded_file"

            # Strip characters that are invalid in Windows filenames, since
            # dashboard link text sometimes includes them (e.g. "Photo: ID Card").
            for bad_char in '<>:"/\\|?*':
                safe_name = safe_name.replace(bad_char, '_')

            has_ext = os.path.splitext(safe_name)[1] != ""
            if not has_ext:
                ext = mime_to_ext.get(mime_type.lower(), "")
                if ext:
                    safe_name += ext
                self.log('info', f"Link text had no file extension; inferred '{ext or 'none'}' from MIME type '{mime_type}'.")

            # Double check folder directory path exists before writing
            if not os.path.exists(folder_path):
                os.makedirs(folder_path, exist_ok=True)

            out_path = os.path.join(folder_path, safe_name)

            # --- FIX 3: Content-hash deduplication ---
            # If a file with the same name already exists, compare content.
            # Identical bytes → skip the write entirely (not a new file).
            # Different bytes → append a counter suffix (genuinely different file).
            if os.path.exists(out_path):
                existing_hash = _file_content_hash(out_path)
                new_hash = hashlib.md5(file_bytes).hexdigest()
                if existing_hash == new_hash:
                    self.log('info', f"Fetch-fallback: identical file already on disk, skipping write: {out_path}")
                    return True  # File is already there — counts as success
                else:
                    base, ext = os.path.splitext(safe_name)
                    counter = 1
                    while os.path.exists(out_path):
                        out_path = os.path.join(folder_path, f"{base}__{counter + 1}{ext}")
                        counter += 1

            with open(out_path, 'wb') as out_f:
                out_f.write(file_bytes)

            self.log('info', f"Fetch-fallback succeeded ({mime_type}), wrote file directly to disk: {out_path}")
            return True
        except Exception as e:
            self.log('error', f"Fetch-fallback exception for {file_name}: {e}")
            return False

    def enable_downloads_in_all_windows(self, driver, folder_path):
        main_handle = driver.current_window_handle
        for handle in driver.window_handles:
            try:
                driver.switch_to.window(handle)
                self.set_download_behavior(driver, folder_path)
            except Exception as e:
                self.log('warning', f"Could not set download behavior on window {handle}: {e}")
        driver.switch_to.window(main_handle)

    def close_extra_windows(self, driver, keep_handle):
        for handle in list(driver.window_handles):
            if handle != keep_handle:
                try:
                    driver.switch_to.window(handle)
                    current_url = driver.current_url
                    self.log('info', f"Closing extra window/tab opened by file click: {current_url}")
                    driver.close()
                except Exception as e:
                    self.log('warning', f"Error closing extra window: {e}")
        driver.switch_to.window(keep_handle)

    # ------------------------------------------------------------------
    # Driver startup
    # ------------------------------------------------------------------
    def launch_chrome_driver(self, chrome_options, manager_timeout=20):
        """Starts Chrome reliably.

        Why this exists: the previous version always called
        webdriver.Chrome(service=Service(ChromeDriverManager().install()), ...).
        ChromeDriverManager().install() makes a network call EVERY run to
        figure out which driver build matches your installed Chrome, with
        no timeout. Two ways that silently breaks things with zero code
        changes on your end:
          1) Chrome auto-updates itself in the background. The next run,
             webdriver-manager (or a previously cached driver) no longer
             matches the new Chrome build, so Selenium fails to start.
          2) webdriver-manager's metadata endpoint is briefly unreachable
             or rate-limited, so .install() hangs or throws.
        Because this whole method historically ran inside a background
        thread, an exception here didn't always surface as a visible error
        (see the threading note in start_download), so it looked like
        "nothing happened."

        Fix: try Selenium's built-in Selenium Manager first (bundled with
        Selenium >= 4.6, no webdriver-manager dependency, resolves the
        matching driver locally/quickly). Only fall back to
        webdriver-manager if that fails, and bound it with a timeout so a
        network hiccup can't hang the run forever.
        """
        self.log('info', "Launching Chrome WebDriver (attempt 1: Selenium Manager).")
        try:
            driver = webdriver.Chrome(options=chrome_options)
            self.log('info', "Chrome launched successfully via Selenium Manager.")
            return driver
        except Exception as e:
            self.log('warning', f"Selenium Manager launch failed, will try webdriver-manager fallback: {e}")

        self.log('info', "Launching Chrome WebDriver (attempt 2: webdriver-manager fallback).")
        result = {}

        def _install():
            try:
                result['path'] = ChromeDriverManager().install()
            except Exception as inner_e:
                result['error'] = inner_e

        installer_thread = threading.Thread(target=_install, daemon=True)
        installer_thread.start()
        installer_thread.join(manager_timeout)

        if installer_thread.is_alive():
            raise RuntimeError(
                f"webdriver-manager did not respond within {manager_timeout}s "
                "(likely a network/proxy issue reaching its driver-metadata "
                "server). Check your internet connection, or that Chrome "
                "itself is installed and up to date, then try again."
            )
        if 'error' in result:
            raise RuntimeError(
                f"Both Selenium Manager and webdriver-manager failed to "
                f"provide a working ChromeDriver. webdriver-manager error: "
                f"{result['error']}"
            )

        driver = webdriver.Chrome(service=Service(result['path']), options=chrome_options)
        self.log('info', "Chrome launched successfully via webdriver-manager fallback.")
        return driver

    # ------------------------------------------------------------------
    # Main download routine
    # ------------------------------------------------------------------
    def start_download(self):
        if self.is_running:
            return  # Guard against double-start (shouldn't happen since Start is disabled while running, but cheap to check)

        if not self.config_file or not self.links_file or not self.download_dir:
            self.safe_messagebox('error', "Selection Error", "Please fill in all directory and file paths.")
            return

        self.setup_text_logger()

        config = self.load_credentials()
        students = self.read_links_csv()

        if not config or not students:
            self.safe_messagebox('error', "Error", "Configuration error or invalid/empty Links CSV file.")
            self.log('critical', "Aborting run: invalid configuration or empty/invalid student CSV.")
            return

        self.root.after(0, lambda: self.set_buttons_running(True))

        # Reset/initialize all live tracking state for this run
        self.is_running = True
        self.current_phase = 'download'
        self.abort_requested = False
        self.total_students = len(students)
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_student_files_done = 0
        self.current_student_files_total = 0
        self.completed_students_file_counts = []
        self.total_files_downloaded = 0
        self.download_start_time = datetime.datetime.now()
        self.download_end_time = None
        self.root.after(0, self.refresh_stats_panel)

        download_dir_abs = os.path.abspath(self.download_dir)
        chrome_options = Options()

        prefs = {
            "download.default_directory": download_dir_abs,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            # Forces image/text/pdf mime types to save instead of render inline.
            "plugins.always_open_pdf_externally": True,
            "profile.default_content_settings.popups": 0,
            "profile.content_settings.exceptions.automatic_downloads.*.setting": 1,
            "download_restrictions": 0,
            "download.extensions_to_open": ""
        }
        chrome_options.add_experimental_option("prefs", prefs)
        # Disables Chrome's built-in image/PDF viewers so files are streamed
        # to disk rather than rendered in a viewer tab.
        chrome_options.add_argument("--disable-features=ChromePDFViewer")

        driver = None
        aborted = False
        try:
            driver = self.launch_chrome_driver(chrome_options)
            driver.set_script_timeout(60)  # Default async-script timeout is too short for fetching large images/files
            wait = WebDriverWait(driver, 15)
            main_window = driver.current_window_handle

            self.set_download_behavior(driver, download_dir_abs)

            self.lbl_status.config(text="Status: Logging in...")
            self.root.update()
            self.log('info', f"Navigating to login URL: {config['login_url']}")
            driver.get(config['login_url'])

            try:
                user_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[contains(@id, 'txtUsername') or @type='text']")))
                pass_input = driver.find_element(By.XPATH, "//input[@type='password']")
                login_btn = driver.find_element(By.XPATH, config['login_btn_xpath'])

                user_input.clear()
                user_input.send_keys(config['username'])
                pass_input.clear()
                pass_input.send_keys(config['password'])
                login_btn.click()
                self.log('info', "Login form submitted.")
            except Exception as e:
                self.log('critical', f"Login failed -- could not locate login fields/button: {e}")
                raise

            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)
            self.log('info', "Login successful, post-login page loaded.")

            total_students = len(students)
            self.progress["maximum"] = total_students

            for idx, student in enumerate(students):
                if self.abort_requested:
                    self.log('warning', f"Abort checkpoint hit before student {idx + 1}/{total_students} -- stopping download phase.")
                    aborted = True
                    break

                student_id = student['studentId']
                link = student['link']
                student_root_folder = os.path.join(download_dir_abs, student_id)

                self.log('info', f"--- Processing student {student_id} ({idx + 1}/{total_students}) ---")

                self.current_student_id = student_id
                self.current_student_files_done = 0
                self.current_student_files_total = 0
                self.refresh_stats_panel()

                self.lbl_status.config(text=f"Processing student {student_id} ({idx + 1}/{total_students})...")
                self.progress["value"] = idx + 1
                self.root.update()

                self.log('info', f"Navigating to student link: {link}")
                driver.get(link)
                file_link_xpath = "//div[contains(., 'Actions') and .//table]//table//tr/td[6]//a"

                try:
                    wait.until(EC.presence_of_element_located((By.XPATH, file_link_xpath)))
                except Exception:
                    self.log('warning', f"No file links found on dashboard for student {student_id}.")
                    self.write_log_csv([{
                        'studentId': student_id, 'fileName': 'N/A', 'fileType': 'N/A',
                        'directory': student_root_folder, 'action': 'not downloaded',
                        'actionComment': 'No records or files found on dashboard matching current criteria',
                        'validation': 'traversed'
                    }])
                    self.completed_students_file_counts.append(0)
                    self.students_traversed += 1
                    self.refresh_stats_panel()
                    continue

                file_links_raw = driver.find_elements(By.XPATH, file_link_xpath)

                def is_real_file_link(el):
                    try:
                        text = el.text.strip()
                        if not text:
                            return False
                        href = (el.get_attribute("href") or "").strip().lower()
                        onclick = (el.get_attribute("onclick") or "").strip()
                        has_real_href = href not in ("", "javascript:void(0)", "javascript:void(0);", "#")
                        has_onclick = len(onclick) > 0
                        return has_real_href or has_onclick
                    except Exception:
                        return False

                file_links = [el for el in file_links_raw if is_real_file_link(el)]
                self.log('info', f"Found {len(file_links_raw)} raw grid link(s), {len(file_links)} confirmed real file link(s) for student {student_id}.")
                self.current_student_files_total = len(file_links)
                self.refresh_stats_panel()
                current_batch_logs = []

                # Tracks how many times each sanitized base-name has been seen
                # within this student's batch so we can assign __2, __3 … suffixes
                # to same-titled files when the "allow duplicates" option is on.
                seen_names: dict[str, int] = {}

                for file_idx in range(len(file_links)):
                    if self.abort_requested:
                        self.log('warning', f"Abort checkpoint hit before file [{file_idx}] for student {student_id} -- stopping current student's batch.")
                        aborted = True
                        break

                    try:
                        current_links_raw = driver.find_elements(By.XPATH, file_link_xpath)
                        current_links = [el for el in current_links_raw if is_real_file_link(el)]
                        if file_idx >= len(current_links):
                            self.log('warning', f"File link index {file_idx} no longer present after re-render; skipping.")
                            break
                        file_link = current_links[file_idx]

                        # --- NEW: Extract Category from column 3 (td[3]) of the parent row ---
                        try:
                            # Navigates up to the <tr> containing this link, then locates the 3rd <td> cell
                            category_text = file_link.find_element(By.XPATH, "./ancestor::tr/td[3]").text.strip()
                        except Exception:
                            category_text = ""
                        
                        if not category_text:
                            category_text = "Uncategorized"
                        
                        # Sanitize the category folder name to replace unsafe path characters
                        safe_category = category_text
                        for bad_char in '<>:"/\\|?*':
                            safe_category = safe_category.replace(bad_char, '_')
                        
                        # Establish individual specific sub-folder for this file context
                        student_folder = os.path.join(student_root_folder, safe_category)
                        if not os.path.exists(student_folder):
                            os.makedirs(student_folder)
                            self.log('info', f"Created folder structure: {student_folder}")

                        file_name = file_link.text.strip()
                        if not file_name:
                            self.log('info', f"Skipping placeholder link at index {file_idx} (empty name).")
                            continue

                        file_ext = os.path.splitext(file_name)[1].replace('.', '').upper() or "UNKNOWN"

                        # Build the sanitized base name (same logic as force_download_via_fetch).
                        safe_check = file_name.strip()
                        for bad_char in '<>:"/\\|?*':
                            safe_check = safe_check.replace(bad_char, '_')

                        _INFER_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.pdf', '.txt', '.doc', '.docx')

                        allow_dup = self.allow_duplicates.get()
                        seen_names[safe_check] = seen_names.get(safe_check, 0) + 1
                        occurrence = seen_names[safe_check]

                        if allow_dup and occurrence > 1:
                            _base, _ext = os.path.splitext(safe_check)
                            effective_disk_name = f"{_base}__{occurrence}{_ext}"
                            self.log('info', f"Duplicate title detected (occurrence {occurrence}): '{file_name}' → will save as '{effective_disk_name}'")
                        else:
                            effective_disk_name = safe_check

                        _already_exists = os.path.exists(os.path.join(student_folder, effective_disk_name))
                        if not _already_exists and not os.path.splitext(effective_disk_name)[1]:
                            for _ext in _INFER_EXTS:
                                if os.path.exists(os.path.join(student_folder, effective_disk_name + _ext)):
                                    _already_exists = True
                                    break

                        if _already_exists and not self.allow_duplicates.get():
                            self.log('info', f"Skipping '{effective_disk_name}' — already exists on disk from a previous run.")
                            self.current_student_files_done += 1
                            self.total_files_downloaded += 1
                            self.refresh_stats_panel()
                            current_batch_logs.append({
                                'studentId': student_id, 'fileName': file_name, 'fileType': file_ext,
                                'directory': student_folder, 'action': 'skipped',
                                'actionComment': f'File already existed on disk as {effective_disk_name}; skipped to avoid duplicate.',
                                'validation': 'file downloaded'
                            })
                            continue

                        file_href = file_link.get_attribute("href")

                        self.log('info', f"Attempting download [{file_idx + 1}/{len(file_links)}]: {file_name} (type: {file_ext}) under category: {safe_category}")

                        before_listing = set(os.listdir(student_folder)) if os.path.exists(student_folder) else set()

                        windows_before = set(driver.window_handles)
                        self.set_download_behavior(driver, student_folder)
                        driver.execute_script("arguments[0].click();", file_link)
                        time.sleep(1.5)

                        windows_after = set(driver.window_handles)
                        new_windows = windows_after - windows_before

                        download_confirmed = False

                        if new_windows:
                            self.log('info', f"Click opened {len(new_windows)} new window(s) -- likely an image/viewer tab.")
                            self.enable_downloads_in_all_windows(driver, student_folder)

                            popup_url = None
                            for h in driver.window_handles:
                                if h != main_window:
                                    try:
                                        driver.switch_to.window(h)
                                        popup_url = driver.current_url
                                        self.log('info', f"Captured popup URL before switching back: {popup_url}")
                                    except Exception as pu_err:
                                        self.log('warning', f"Could not read popup URL: {pu_err}")
                                    break
                            driver.switch_to.window(main_window)

                            self.wait_for_downloads_to_finish(student_folder, timeout=15)
                            after_listing = set(os.listdir(student_folder)) if os.path.exists(student_folder) else set()
                            new_files = after_listing - before_listing
                            download_confirmed = len(new_files) > 0 and not any(f.endswith('.crdownload') for f in new_files)

                            if not download_confirmed:
                                self.log('warning', f"'{file_name}' opened a new tab but no file landed on disk -- using fetch-fallback.")
                                fallback_url = popup_url or file_href
                                if fallback_url:
                                    fetch_window = None
                                    for h in driver.window_handles:
                                        if h != main_window:
                                            fetch_window = h
                                            break
                                    if fetch_window:
                                        driver.switch_to.window(fetch_window)
                                        download_confirmed = self.force_download_via_fetch(driver, fallback_url, student_folder, effective_disk_name)
                                        driver.switch_to.window(main_window)
                                    else:
                                        download_confirmed = self.force_download_via_fetch(driver, fallback_url, student_folder, effective_disk_name)
                                else:
                                    self.log('error', f"No usable URL found to retry '{file_name}' via fetch-fallback (no popup URL and no href).")

                            self.close_extra_windows(driver, main_window)
                            self.set_download_behavior(driver, student_folder)
                        else:
                            self.wait_for_downloads_to_finish(student_folder, timeout=15)
                            after_listing = set(os.listdir(student_folder)) if os.path.exists(student_folder) else set()
                            new_files = after_listing - before_listing
                            download_confirmed = len(new_files) > 0 and not any(f.endswith('.crdownload') for f in new_files)

                            if not download_confirmed:
                                current_url = driver.current_url
                                navigated_away = current_url != link
                                self.log('warning', f"'{file_name}' click produced no popup and no confirmed file (navigated_away={navigated_away}). Using fetch-fallback.")

                                retry_url = current_url if navigated_away else file_href

                                if not retry_url:
                                    try:
                                        img_src = driver.execute_script("""
                                            const imgs = Array.from(document.querySelectorAll('img'));
                                            const visible = imgs.filter(img => img.offsetWidth > 50 && img.offsetHeight > 50 && img.src);
                                            if (visible.length === 0) return null;
                                            visible.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
                                            return visible[0].src;
                                        """)
                                        if img_src:
                                            self.log('info', f"Located visible <img> element for '{file_name}': {img_src}")
                                            retry_url = img_src
                                    except Exception as img_err:
                                        self.log('warning', f"Could not search for in-page <img> fallback: {img_err}")

                                if retry_url:
                                    download_confirmed = self.force_download_via_fetch(driver, retry_url, student_folder, effective_disk_name)
                                else:
                                    self.log('error', f"No usable URL found to retry '{file_name}' via fetch-fallback (no href, no navigation, no visible image found).")

                                if not navigated_away:
                                    try:
                                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                                        time.sleep(0.5)
                                    except Exception:
                                        pass

                                if navigated_away:
                                    self.log('info', f"Navigating back to student dashboard after inline navigation: {link}")
                                    driver.get(link)
                                    try:
                                        wait.until(EC.presence_of_element_located((By.XPATH, file_link_xpath)))
                                    except Exception:
                                        self.log('warning', f"Dashboard grid did not reappear after returning for student {student_id}.")
                                    self.set_download_behavior(driver, student_folder)

                        action_comment = "Download transaction processed successfully" if download_confirmed else "Download could not be confirmed; file may have rendered instead of saving"
                        action_status = "downloaded" if download_confirmed else "not downloaded"

                        self.log('info' if download_confirmed else 'warning', f"Result for '{file_name}': {action_status}")

                        if download_confirmed:
                            self.current_student_files_done += 1
                            self.total_files_downloaded += 1
                        self.refresh_stats_panel()

                        current_batch_logs.append({
                            'studentId': student_id, 'fileName': file_name, 'fileType': file_ext,
                            'directory': student_folder, 'action': action_status,
                            'actionComment': action_comment,
                            'validation': 'file downloaded' if download_confirmed else 'discrepancy: not confirmed'
                        })

                    except Exception as click_err:
                        self.log('error', f"Failed processing file click at index [{file_idx}] for student {student_id}: {click_err}")
                        self.refresh_stats_panel()
                        current_batch_logs.append({
                            'studentId': student_id, 'fileName': 'Error_Item', 'fileType': 'N/A',
                            'directory': os.path.join(student_root_folder, "Errors"), 'action': 'error',
                            'actionComment': str(click_err)[:200], 'validation': 'traversed'
                        })

                if current_batch_logs:
                    # Collect all distinctive sub-folders that received files in this batch to verify them completely
                    target_dirs = set(log_entry['directory'] for log_entry in current_batch_logs if log_entry['fileName'] != 'Error_Item')
                    for folder in target_dirs:
                        self.wait_for_downloads_to_finish(folder)
                    self.log('info', f"All pending downloads finished for student {student_id}.")
                    self.write_log_csv(current_batch_logs)

                self.completed_students_file_counts.append(self.current_student_files_done)
                self.students_traversed += 1
                self.refresh_stats_panel()

                if aborted:
                    break

            if aborted:
                self.lbl_status.config(text="Status: Download phase aborted by user.")
                self.log('warning', "Download phase stopped early due to user abort request.")
                self.safe_messagebox('warning', "Aborted", "Download process was stopped by the user. Partial results were saved and logged.")
            else:
                self.lbl_status.config(text="Status: Process Completed!")
                self.log('info', "Automation run completed successfully.")
                self.safe_messagebox('info', "Success", "All records traversed. Log generated in target folder.")

        except Exception as e:
            self.log('critical', f"Fatal operation exception: {e}")
            self.safe_messagebox('error', "Critical Error", str(e))
        finally:
            if driver:
                driver.quit()
                self.log('info', "WebDriver closed.")
            self.is_running = False
            self.current_phase = None
            self.abort_requested = False
            self.download_end_time = datetime.datetime.now()
            self.root.after(0, self.refresh_stats_panel)
            self.root.after(0, lambda: self.set_buttons_running(False))
            self.root.after(0, lambda: self.progress.config(value=0))

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def verify_downloads(self):
        if self.is_running:
            return  # Guard against double-start; Verify is disabled while running anyway

        if not self.download_dir:
            self.safe_messagebox('error', "Verification Error", "Please select the download target folder first.")
            return

        if not self.logger:
            self.setup_text_logger()

        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")

        if not os.path.isfile(log_file_path):
            self.safe_messagebox('error', "Verification Error", "Execution log not found in target folder. Please run a download sequence first.")
            self.log('error', "Verification aborted: automation_execution_log.csv not found.")
            return

        self.log('info', "Starting verification of downloaded files against execution log.")

        self.root.after(0, lambda: self.set_buttons_running(True))
        self.is_running = True
        self.current_phase = 'verify'
        self.abort_requested = False
        self.verify_start_time = datetime.datetime.now()
        self.verify_end_time = None
        self.root.after(0, lambda: self.lbl_status.config(text="Status: Verifying downloads..."))
        self.root.after(0, self.refresh_stats_panel)

        aborted = False
        try:
            expected_records = {}
            try:
                with open(log_file_path, mode='r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        sid = row['studentId']
                        fname = row['fileName']
                        action = row['action']
                        stored_dir = row['directory']

                        if action in ('downloaded', 'skipped') and fname != 'N/A':
                            # Track using the full recorded directory folder mapping instead of just structural roots
                            expected_records.setdefault(stored_dir, {}).setdefault(sid, []).append(fname)
            except Exception as e:
                self.log('error', f"Failed reading log data for verification: {e}")
                self.safe_messagebox('error', "Error", f"Failed reading log data for verification: {e}")
                return

            missing_files_report = []
            verified_files_count = 0
            failed_folders_count = 0

            for student_path, records in expected_records.items():
                for student_id, files in records.items():
                    if self.abort_requested:
                        self.log('warning', f"Abort checkpoint hit during verification at student {student_id} -- stopping verify phase.")
                        aborted = True
                        break

                    if not os.path.exists(student_path):
                        failed_folders_count += 1
                        self.log('warning', f"Verification: folder missing entirely: {student_path}")
                        missing_files_report.append(f"Student {student_id} ({os.path.basename(student_path)}): Folder missing.")
                        for missing_file in files:
                            self.update_log_validation(student_id, missing_file, "Discrepancy Warning: Folder Missing")
                        continue

                    actual_files = os.listdir(student_path)
                    actual_files = [f for f in actual_files if not f.endswith(('.crdownload', '.tmp'))]

                    for expected_file in files:
                        safe_expected = expected_file
                        for bad_char in '<>:"/\\|?*':
                            safe_expected = safe_expected.replace(bad_char, '_')

                        if safe_expected in actual_files:
                            verified_files_count += 1
                            self.update_log_validation(student_id, expected_file, "Verified Match")
                        else:
                            self.log('warning', f"Verification: file missing for student {student_id} in {student_path}: {expected_file}")
                            missing_files_report.append(f"Student {student_id} [{os.path.basename(student_path)}]: Missing '{expected_file}'")
                            self.update_log_validation(student_id, expected_file, "Discrepancy Warning: File Missing")

                if aborted:
                    break

            self.log('info', f"Verification complete. Verified: {verified_files_count}, Missing: {len(missing_files_report)}, Folders missing: {failed_folders_count}")

            if aborted:
                self.root.after(0, lambda: self.lbl_status.config(text="Status: Verification aborted by user."))
                self.safe_messagebox('warning', "Aborted", f"Verification was stopped by the user.\n\nVerified so far: {verified_files_count} files.")
            elif not missing_files_report:
                self.root.after(0, lambda: self.lbl_status.config(text="Status: Verification Completed!"))
                self.safe_messagebox('info', "Verification Success", f"Integrity check complete!\n\nChecked and matched {verified_files_count} files directly inside their designated student category directories.")
            else:
                self.root.after(0, lambda: self.lbl_status.config(text="Status: Verification Completed (discrepancies found)."))
                report_text = f"Integrity Check Failed!\nVerified Files: {verified_files_count}\n\nIssues Identified:\n"
                report_text += "\n".join(missing_files_report[:8])
                if len(missing_files_report) > 8:
                    report_text += f"\n...and {len(missing_files_report) - 8} more discrepancies."
                self.safe_messagebox('warning', "Discrepancy Warning", report_text)

        except Exception as e:
            self.log('critical', f"Fatal verification exception: {e}")
            self.safe_messagebox('error', "Critical Error", str(e))
        finally:
            self.is_running = False
            self.current_phase = None
            self.abort_requested = False
            self.verify_end_time = datetime.datetime.now()
            self.root.after(0, self.refresh_stats_panel)
            self.root.after(0, lambda: self.set_buttons_running(False))


if __name__ == "__main__":
    root = tk.Tk()
    app = FileDownloaderApp(root)
    root.mainloop()
