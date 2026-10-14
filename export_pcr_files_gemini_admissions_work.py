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


def _remove_empty_dirs(root_dir):
    """Recursively remove any empty directories under root_dir (bottom-up).
    This is a safety net that cleans up stray empty folders (e.g. a folder
    named "1") left behind by skipped rows, failed downloads, or old runs.
    """
    if not root_dir or not os.path.isdir(root_dir):
        return
    for current_dir, subdirs, files in os.walk(root_dir, topdown=False):
        if current_dir == root_dir:
            continue
        try:
            if not os.listdir(current_dir):
                os.rmdir(current_dir)
        except OSError:
            pass


def _file_content_hash(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


class FileDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCR Educator Action Files Downloader")
        self.root.geometry("900x780")
        self.root.resizable(False, False)

        self.config_file = ""
        self.links_file = ""
        self.download_dir = ""
        self.logger = None

        self.allow_duplicates = tk.BooleanVar(value=True)

        # --- Progress tracking state ---
        self.is_running = False
        self.current_phase = None
        self.total_students = 0
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_student_files_done = 0
        self.current_student_files_total = 0
        self.completed_students_file_counts = []
        self.total_files_downloaded = 0

        self.download_start_time = None
        self.download_end_time = None
        self.verify_start_time = None
        self.verify_end_time = None

        self.abort_requested = False

        self.create_widgets()
        self.tick_clock()

    def setup_text_logger(self):
        log_path = os.path.join(self.download_dir, "automation_debug.log")
        logger = logging.getLogger("pcr_downloader")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        self.logger = logger
        self.logger.info("=" * 70)
        self.logger.info("New Action Files automation run started.")
        return log_path

    def log(self, level, message):
        if self.logger:
            getattr(self.logger, level)(message)

    def safe_messagebox(self, kind, title, message):
        func = {'info': messagebox.showinfo, 'error': messagebox.showerror, 'warning': messagebox.showwarning}[kind]
        self.root.after(0, lambda: func(title, message))

    def tick_clock(self):
        now = datetime.datetime.now()
        self.lbl_now_time.config(text=now.strftime("%Y-%m-%d %H:%M:%S"))
        self.lbl_elapsed.config(text=self.current_phase_elapsed_text(now))
        self.root.after(1000, self.tick_clock)

    def current_phase_elapsed_text(self, now=None):
        now = now or datetime.datetime.now()
        if self.current_phase == 'download':
            if self.download_start_time:
                return self.format_timedelta(now - self.download_start_time) + " (downloading)"
        elif self.current_phase == 'verify':
            if self.verify_start_time:
                return self.format_timedelta(now - self.verify_start_time) + " (verifying)"

        if self.verify_start_time and self.verify_end_time:
            return self.format_timedelta(self.verify_end_time - self.verify_start_time) + " (verify done)"
        if self.download_start_time and self.download_end_time:
            return self.format_timedelta(self.download_end_time - self.download_start_time) + " (download done)"
        return "00:00:00"

    @staticmethod
    def format_timedelta(td):
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def average_files_per_student(self):
        if not self.completed_students_file_counts:
            return None
        return sum(self.completed_students_file_counts) / len(self.completed_students_file_counts)

    def average_duration_per_file_text(self):
        if self.total_files_downloaded <= 0 or not self.download_start_time:
            return "-"
        ref_end = datetime.datetime.now() if self.current_phase == 'download' else (self.download_end_time or datetime.datetime.now())
        elapsed_seconds = (ref_end - self.download_start_time).total_seconds()
        if elapsed_seconds <= 0:
            return "-"
        return f"{(elapsed_seconds / self.total_files_downloaded):.1f}s/file"

    def average_duration_per_student_text(self):
        if self.students_traversed <= 0 or not self.download_start_time:
            return "-"
        ref_end = datetime.datetime.now() if self.current_phase == 'download' else (self.download_end_time or datetime.datetime.now())
        elapsed_seconds = (ref_end - self.download_start_time).total_seconds()
        if elapsed_seconds <= 0:
            return "-"
        avg_seconds = elapsed_seconds / self.students_traversed
        return self.format_timedelta(datetime.timedelta(seconds=avg_seconds)) + "/student"

    def refresh_stats_panel(self):
        self.lbl_students_progress.config(text=f"{self.students_traversed} / {self.total_students}")
        self.lbl_current_student.config(text=str(self.current_student_id))

        if self.current_student_files_total > 0:
            self.lbl_files_progress.config(text=f"{self.current_student_files_done} / {self.current_student_files_total}")
        else:
            self.lbl_files_progress.config(text=f"{self.current_student_files_done} / -")

        avg_files = self.average_files_per_student()
        if avg_files is not None and self.total_students > 0:
            approx_grand_total = avg_files * self.total_students
            self.lbl_files_approx.config(text=f"{self.total_files_downloaded} / ~{approx_grand_total:.0f} (avg {avg_files:.1f}/student)")
        else:
            self.lbl_files_approx.config(text=f"{self.total_files_downloaded} / ~? (no average yet)")

        if self.download_start_time:
            self.lbl_start_time.config(text=self.download_start_time.strftime("%Y-%m-%d %H:%M:%S"))

        self.lbl_avg_duration_file.config(text=self.average_duration_per_file_text())
        self.lbl_avg_duration_student.config(text=self.average_duration_per_student_text())

        if self.current_phase == 'download' and self.download_start_time and self.students_traversed > 0 and self.total_students > 0:
            now = datetime.datetime.now()
            elapsed = (now - self.download_start_time).total_seconds()
            avg_seconds_per_student = elapsed / self.students_traversed
            remaining_seconds = avg_seconds_per_student * (self.total_students - self.students_traversed)
            eta = now + datetime.timedelta(seconds=remaining_seconds)
            self.lbl_eta.config(text=eta.strftime("%Y-%m-%d %H:%M:%S"))
        elif self.download_end_time:
            self.lbl_eta.config(text=self.download_end_time.strftime("%Y-%m-%d %H:%M:%S") + " (done)")
        else:
            self.lbl_eta.config(text="Calculating...")

        self.root.update_idletasks()

    def create_widgets(self):
        header = tk.Label(self.root, text="PCR Action Files Downloader", font=("Arial", 16, "bold"))
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

        dup_frame = tk.Frame(self.root)
        dup_frame.pack(fill="x", padx=20, pady=(0, 4))
        self.chk_allow_duplicates = tk.Checkbutton(
            dup_frame, text='Download files with duplicate titles (filename__2, filename__3...)',
            variable=self.allow_duplicates, font=("Arial", 9), anchor="w",
        )
        self.chk_allow_duplicates.pack(side="left")

        self.lbl_status = tk.Label(self.root, text="Status: Idle", font=("Arial", 10))
        self.lbl_status.pack(pady=5)

        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=800, mode="determinate")
        self.progress.pack(pady=10)

        stats_frame = tk.LabelFrame(self.root, text=" Live Progress ", font=("Arial", 10, "bold"), padx=15, pady=10)
        stats_frame.pack(pady=5, fill="x", padx=20)

        left_col = tk.Frame(stats_frame)
        left_col.grid(row=0, column=0, sticky="nw", padx=(0, 30))
        right_col = tk.Frame(stats_frame)
        right_col.grid(row=0, column=1, sticky="nw")

        LABEL_WIDTH, VALUE_WIDTH = 28, 32

        def add_stat_row(parent, row, label_text):
            tk.Label(parent, text=label_text, font=("Arial", 9, "bold"), anchor="w", width=LABEL_WIDTH, justify="left").grid(row=row, column=0, sticky="w", pady=4)
            value_lbl = tk.Label(parent, text="-", font=("Arial", 9), anchor="w", fg="#1565C0", width=VALUE_WIDTH, justify="left")
            value_lbl.grid(row=row, column=1, sticky="w", padx=10, pady=4)
            return value_lbl

        self.lbl_students_progress = add_stat_row(left_col, 0, "Students Traversed:")
        self.lbl_current_student = add_stat_row(left_col, 1, "Current Student:")
        self.lbl_files_progress = add_stat_row(left_col, 2, "Files Downloaded (Current):")
        self.lbl_files_approx = add_stat_row(left_col, 3, "Done/Approx. Total:")
        self.lbl_start_time = add_stat_row(left_col, 4, "Automation Start:")

        self.lbl_now_time = add_stat_row(right_col, 0, "Current Date & Time:")
        self.lbl_elapsed = add_stat_row(right_col, 1, "Elapsed Duration:")
        self.lbl_eta = add_stat_row(right_col, 2, "Est. Completion Time:")
        self.lbl_avg_duration_file = add_stat_row(right_col, 3, "Average Duration/File:")
        self.lbl_avg_duration_student = add_stat_row(right_col, 4, "Average Duration/Student:")

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=15)

        self.btn_start = tk.Button(
            btn_frame, text="Start Download Process", font=("Arial", 11, "bold"), bg="#4CAF50", fg="white", padx=10, pady=5,
            command=lambda: threading.Thread(target=self.start_download, daemon=True).start()
        )
        self.btn_start.pack(side="left", padx=8)

        self.btn_verify = tk.Button(
            btn_frame, text="Verify Downloads", font=("Arial", 11, "bold"), bg="#2196F3", fg="white", padx=10, pady=5,
            command=lambda: threading.Thread(target=self.verify_downloads, daemon=True).start()
        )
        self.btn_verify.pack(side="left", padx=8)

        self.btn_abort = tk.Button(
            btn_frame, text="Abort", font=("Arial", 11, "bold"), bg="#F44336", fg="white", padx=10, pady=5,
            command=self.request_abort, state="disabled"
        )
        self.btn_abort.pack(side="left", padx=8)

    def request_abort(self):
        if not self.is_running:
            return
        self.abort_requested = True
        self.btn_abort.config(state="disabled")
        self.lbl_status.config(text="Status: Abort requested -- stopping soon...")
        self.log('warning', "Abort requested by user.")

    def set_buttons_running(self, running):
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

    def load_credentials(self):
        config_data = {}
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        config_data[key.strip().lower()] = val.strip()
            if not all(k in config_data for k in ['username', 'password', 'login_url', 'login_btn_xpath']):
                return None
            return config_data
        except Exception:
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
                    if h in ['studentid', 'id', 'student']: id_idx = idx
                    if h in ['link', 'url', 'studentlink']: link_idx = idx

                if id_idx is None or link_idx is None: return []

                for row in reader:
                    if row and len(row) > max(id_idx, link_idx):
                        students.append({'studentId': row[id_idx].strip(), 'link': row[link_idx].strip()})
            return students
        except Exception:
            return []

    def write_log_csv(self, log_data):
        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")
        file_exists = os.path.isfile(log_file_path)
        headers = ['studentId', 'fileName', 'fileType', 'directory', 'action', 'actionComment', 'validation']
        try:
            with open(log_file_path, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists: writer.writeheader()
                writer.writerows(log_data)
        except Exception as e:
            self.log('error', f"Failed writing log CSV: {e}")

    def update_log_validation(self, student_id, file_name, validation_status):
        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")
        if not os.path.isfile(log_file_path): return
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
            self.log('error', f"Failed updating validation: {e}")

    def wait_for_downloads_to_finish(self, folder_path, timeout=40):
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                pending = [f for f in os.listdir(folder_path) if f.endswith('.crdownload') or f.endswith('.tmp')]
            except FileNotFoundError:
                pending = []
            if not pending: return True
            time.sleep(0.5)
        return False

    def set_download_behavior(self, driver, folder_path):
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": folder_path})
        except Exception: pass
        try:
            driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": folder_path, "eventsEnabled": True})
        except Exception: pass

    def force_download_via_fetch(self, driver, file_url, folder_path, file_name):
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
                    reader.readAsDataURL(blob);
                }).catch(() => callback('ERROR'));
            """
            result = driver.execute_async_script(script, file_url)
            if not result or result.startswith('ERROR'): return False

            header, encoded = result.split(',', 1)
            import base64
            file_bytes = base64.b64decode(encoded)

            mime_type = "application/octet-stream"
            if header.startswith("data:") and ";" in header:
                mime_type = header[5:header.index(";")]

            mime_to_ext = {
                "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
                "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
                "application/pdf": ".pdf", "text/plain": ".txt", "application/msword": ".doc",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"
            }

            safe_name = file_name.strip()
            for bad_char in '<>:"/\\|?*': safe_name = safe_name.replace(bad_char, '_')

            if not os.path.splitext(safe_name)[1]:
                ext = mime_to_ext.get(mime_type.lower(), "")
                safe_name += ext

            os.makedirs(folder_path, exist_ok=True)
            out_path = os.path.join(folder_path, safe_name)

            if os.path.exists(out_path):
                if _file_content_hash(out_path) == hashlib.md5(file_bytes).hexdigest():
                    return True
                base, ext = os.path.splitext(safe_name)
                counter = 1
                while os.path.exists(out_path):
                    out_path = os.path.join(folder_path, f"{base}__{counter + 1}{ext}")
                    counter += 1

            with open(out_path, 'wb') as out_f:
                out_f.write(file_bytes)
            return True
        except Exception:
            return False

    def close_extra_windows(self, driver, keep_handle):
        for handle in list(driver.window_handles):
            if handle != keep_handle:
                try:
                    driver.switch_to.window(handle)
                    driver.close()
                except Exception: pass
        driver.switch_to.window(keep_handle)

    def launch_chrome_driver(self, chrome_options):
        try:
            return webdriver.Chrome(options=chrome_options)
        except Exception:
            path = ChromeDriverManager().install()
            return webdriver.Chrome(service=Service(path), options=chrome_options)

    # ------------------------------------------------------------------
    # Target-Specific Action Files Routine
    # ------------------------------------------------------------------
    def start_download(self):
        if self.is_running: return
        if not self.config_file or not self.links_file or not self.download_dir:
            self.safe_messagebox('error', "Selection Error", "Please fill in all paths.")
            return

        self.setup_text_logger()
        config = self.load_credentials()
        students = self.read_links_csv()

        if not config or not students:
            self.safe_messagebox('error', "Error", "Invalid configuration files.")
            return

        self.root.after(0, lambda: self.set_buttons_running(True))
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
        self.root.after(0, self.refresh_stats_panel)

        download_dir_abs = os.path.abspath(self.download_dir)
        chrome_options = Options()
        chrome_options.add_experimental_option("prefs", {
            "download.default_directory": download_dir_abs,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "plugins.always_open_pdf_externally": True,
            "profile.default_content_settings.popups": 0,
            "profile.content_settings.exceptions.automatic_downloads.*.setting": 1,
            "download_restrictions": 0
        })
        chrome_options.add_argument("--disable-features=ChromePDFViewer")

        driver = None
        aborted = False
        try:
            driver = self.launch_chrome_driver(chrome_options)
            driver.set_script_timeout(60)
            wait = WebDriverWait(driver, 15)
            main_window = driver.current_window_handle

            driver.get(config['login_url'])
            user_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[contains(@id, 'txtUsername') or @type='text']")))
            pass_input = driver.find_element(By.XPATH, "//input[@type='password']")
            login_btn = driver.find_element(By.XPATH, config['login_btn_xpath'])

            user_input.send_keys(config['username'])
            pass_input.send_keys(config['password'])
            login_btn.click()
            time.sleep(3)

            self.progress["maximum"] = len(students)

            for idx, student in enumerate(students):
                if self.abort_requested:
                    aborted = True
                    break

                # Clean up any spaces or accidental decimal points (e.g., "1.0")
                raw_id = student['studentId'].strip()
                student_id = raw_id.split('.')[0] if '.' in raw_id else raw_id
                
                # --- IRON-CLAD FILTER: Skip row indices, artifacts, or headers ---
                if (
                    not student_id 
                    or len(student_id) < 3  # Skips '1', '2', '01' row numbering
                    or student_id.lower() in ['studentid', 'id', 'student']
                    or student_id == '1'
                ):
                    self.log('info', f"Skipping row index/header artifact: '{raw_id}'")
                    continue
                # ------------------------------------------------------------------

                link = student['link']
                student_root_folder = os.path.join(download_dir_abs, student_id)

                self.current_student_id = student_id
                self.current_student_files_done = 0
                self.current_student_files_total = 0
                self.refresh_stats_panel()

                self.lbl_status.config(text=f"Scanning Action Files: {student_id}...")
                self.progress["value"] = idx + 1
                self.root.update()

                driver.get(link)
                
                # --- FIXED ACCURATE GRID XPATH EXCLUSION ---
                # Specifically searches only within table rows containing data columns belonging exclusively 
                # inside the targeted Action Files interface component.
                action_rows_xpath = "//div[contains(., 'Action Files') and .//table]//table//tr[td and count(td) >= 3]"
                try:
                    wait.until(EC.presence_of_element_located((By.XPATH, action_rows_xpath)))
                except Exception:
                    self.log('warning', f"No Action Files tab found for student {student_id}.")
                    self.completed_students_file_counts.append(0)
                    self.students_traversed += 1
                    self.refresh_stats_panel()
                    continue

                items_to_download = []
                rows = driver.find_elements(By.XPATH, action_rows_xpath)
                
                for row in rows:
                    try:
                        # Column 2 holds the Category Name
                        category_name = row.find_element(By.XPATH, "./td[2]").text.strip()
                        if not category_name: category_name = "Uncategorized"
                        
                        # Gather all link parameters cleanly inside the target row to bypass staleness crashes
                        links = row.find_elements(By.XPATH, "./td[3]//a")
                        for link_el in links:
                            text = link_el.text.strip()
                            href = link_el.get_attribute("href")
                            # Defensive filter to avoid loading layout counter stats like '8424' as file data
                            if text and href and href.strip() != "#" and not text.isdigit():
                                items_to_download.append({
                                    'category': category_name,
                                    'link_text': text,
                                    'href': href
                                })
                    except Exception:
                        pass

                self.current_student_files_total = len(items_to_download)
                self.refresh_stats_panel()
                current_batch_logs = []
                seen_names = {}

                for f_idx, item in enumerate(items_to_download):
                    if self.abort_requested:
                        aborted = True
                        break

                    self.lbl_status.config(text=f"Downloading [{f_idx+1}/{len(items_to_download)}] for {student_id}...")
                    self.root.update()

                    safe_category = item['category']
                    for bad_char in '<>:"/\\|?*': safe_category = safe_category.replace(bad_char, '_')
                    student_folder = os.path.join(student_root_folder, safe_category)

                    file_name = item['link_text']
                    file_ext = os.path.splitext(file_name)[1].replace('.', '').upper() or "UNKNOWN"

                    safe_check = file_name
                    for bad_char in '<>:"/\\|?*': safe_check = safe_check.replace(bad_char, '_')

                    seen_names[file_name] = seen_names.get(file_name, 0) + 1
                    occurrence = seen_names[file_name]

                    if self.allow_duplicates.get() and occurrence > 1:
                        _base, _ext = os.path.splitext(safe_check)
                        effective_disk_name = f"{_base}__{occurrence}{_ext}"
                    else:
                        effective_disk_name = safe_check

                    # High-reliability primary pull method via secure authenticated stream fetching
                    download_confirmed = self.force_download_via_fetch(driver, item['href'], student_folder, effective_disk_name)

                    if download_confirmed:
                        self.current_student_files_done += 1
                        self.total_files_downloaded += 1
                    else:
                        self.log('error', f"File extraction failed for {file_name} via context URL.")

                    self.refresh_stats_panel()
                    current_batch_logs.append({
                        'studentId': student_id, 'fileName': file_name, 'fileType': file_ext,
                        'directory': student_folder, 'action': 'downloaded' if download_confirmed else 'failed',
                        'actionComment': 'Success' if download_confirmed else 'Extraction failed',
                        'validation': 'file downloaded' if download_confirmed else 'failed'
                    })

                if current_batch_logs:
                    self.write_log_csv(current_batch_logs)

                self.completed_students_file_counts.append(self.current_student_files_done)
                self.students_traversed += 1
                self.refresh_stats_panel()

            self.lbl_status.config(text="Status: Process Completed!" if not aborted else "Status: Aborted")

        except Exception as e:
            self.safe_messagebox('error', "Critical Error", str(e))
        finally:
            if driver: driver.quit()
            _remove_empty_dirs(download_dir_abs)
            self.is_running = False
            self.current_phase = None
            self.download_end_time = datetime.datetime.now()
            self.root.after(0, self.refresh_stats_panel)
            self.root.after(0, lambda: self.set_buttons_running(False))

    def verify_downloads(self):
        if self.is_running: return
        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")
        if not os.path.isfile(log_file_path): return

        self.root.after(0, lambda: self.set_buttons_running(True))
        self.is_running = True
        self.current_phase = 'verify'
        self.verify_start_time = datetime.datetime.now()
        
        expected_records = {}
        try:
            with open(log_file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['action'] == 'downloaded':
                        expected_records.setdefault(row['directory'], {}).setdefault(row['studentId'], []).append(row['fileName'])
        except Exception:
            pass

        # --- FIX: Increments matched items completely for every file checked ---
        verified = 0
        for path, records in expected_records.items():
            for sid, files in records.items():
                if os.path.exists(path):
                    actual = os.listdir(path)
                    for f in files:
                        sf = f
                        for bad_char in '<>:"/\\|?*': sf = sf.replace(bad_char, '_')
                        if sf in actual:
                            verified += 1
                            self.update_log_validation(sid, f, "Verified Match")

        self.is_running = False
        self.current_phase = None
        self.verify_end_time = datetime.datetime.now()
        self.root.after(0, self.refresh_stats_panel)
        self.root.after(0, lambda: self.set_buttons_running(False))
        self.safe_messagebox('info', "Verification Complete", f"Verified matches: {verified}")


if __name__ == "__main__":
    root = tk.Tk()
    app = FileDownloaderApp(root)
    root.mainloop()
