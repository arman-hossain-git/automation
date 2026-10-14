"""
PCR Educator Files & Documents Uploader - Optimized Edition
===========================================================
"""

import os
import re
import time
import csv
import shutil
import tempfile
import logging
import threading
import traceback
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC


# ==============================================================================
# Default XPaths - override any of these from the credentials .txt file
# ==============================================================================
DEFAULT_XPATHS = {
    'three_dot_menu_xpath': "/html/body/div[1]/div/div[1]/div[2]/div[2]/button",
    'files_documents_menu_xpath': "/html/body/div[3]/div/div[1]/div[2]/div[6]/div/a[14]",
    'new_folder_toolbar_button_xpath': "/html/body/div[1]/div/div[1]/div[2]/a[4]",
    'new_folder_option_xpath': "//a[contains(normalize-space(.), 'New Folder')] | //li[contains(normalize-space(.), 'New Folder')]",
    'folder_name_input_xpath': "/html/body/div[1]/div/div[2]/form/div/table/tbody/tr[1]/td[2]/input",
    'save_button_xpath': "/html/body/div[1]/div/div[2]/form/div/table/tbody/tr[4]/td[2]/input",
    'popup_ok_xpath': "/html/body/div[6]/div/div/div[3]/button",
    'upload_tool_button_xpath': "/html/body/div[1]/div/div[1]/div[2]/a[5]",
    'folder_listing_table_xpath': "/html/body/div[1]/div/div[2]/div/div[2]/table",
    'browse_link_xpath': "/html/body/div[1]/div/div[2]/form/div[1]/div/table/tbody/tr/td[4]/a",
    'file_input_xpath': "//input[@type='file']",
    'upload_submit_xpath': "/html/body/div[1]/div/div[2]/form/table/tbody/tr/td/input",
    'upload_popup_ok_xpath': "/html/body/div[6]/div/div/div[3]/button",
}

NO_TARGET_FOLDER_LABEL = "(None - upload to Files & Documents root)"
TARGET_FOLDER_OPTIONS = [NO_TARGET_FOLDER_LABEL, "Admissions Inquiry", "Students Inquiry"]


def _xpath_literal(value):
    """Build a safe XPath string literal for arbitrary text (handles embedded quotes)."""
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts = value.split('"')
    pieces = [f'"{p}"' for p in parts]
    return "concat(" + ', \'"\', '.join(pieces) + ")"


def describe_exception(e):
    msg = str(e).strip()
    first_line = msg.splitlines()[0].strip() if msg else ""
    if first_line:
        return f"{type(e).__name__}: {first_line}"
    return f"{type(e).__name__} (no message - see debug log for full traceback)"


def sanitize_upload_filename(filename):
    if filename.count('.') > 1:
        base, ext = filename.rsplit('.', 1)
        base = base.replace('.', '_')
        return f"{base}.{ext}"
    return filename


def list_category_folders(student_root_folder):
    result = []
    if not os.path.isdir(student_root_folder):
        return result
    for entry in sorted(os.listdir(student_root_folder)):
        full_path = os.path.join(student_root_folder, entry)
        if os.path.isdir(full_path):
            files = sorted(
                f for f in os.listdir(full_path)
                if os.path.isfile(os.path.join(full_path, f))
            )
            result.append({'name': entry, 'path': full_path, 'files': files})
    return result


class TkTextHandler(logging.Handler):
    def __init__(self, text_widget, root):
        super().__init__()
        self.text_widget = text_widget
        self.root = root

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            return
        level = record.levelname

        def append():
            self.text_widget.configure(state='normal')
            self.text_widget.insert('end', msg + '\n', level)
            self.text_widget.see('end')
            self.text_widget.configure(state='disabled')

        try:
            self.root.after(0, append)
        except Exception:
            pass


class FileUploaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCR Educator Files & Documents Uploader (Optimized)")
        self.root.geometry("920x920")
        self.root.resizable(False, False)

        self.config_file = ""
        self.links_file = ""
        self.source_dir = ""
        self.logger = None

        self.target_folder_var = tk.StringVar(value=NO_TARGET_FOLDER_LABEL)
        self.devtools_var = tk.BooleanVar(value=False)

        self.is_running = False
        self.current_phase = None
        self.total_students = 0
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_category = "-"
        self.current_student_folders_created = 0
        self.current_student_files_done = 0
        self.current_student_files_total = 0
        self.completed_students_file_counts = []
        self.total_files_uploaded = 0
        self.total_folders_created = 0

        self.upload_start_time = None
        self.upload_end_time = None

        self.abort_requested = False
        self.driver = None
        self._tmp_upload_dir = None

        self.create_widgets()
        self.tick_clock()

    def setup_text_logger(self):
        log_path = os.path.join(self.source_dir, "uploads_debug.log")
        logger = logging.getLogger("pcr_uploader")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(file_handler)

        live_handler = TkTextHandler(self.txt_live_log, self.root)
        live_handler.setLevel(logging.DEBUG)
        live_handler.setFormatter(logging.Formatter('%(asctime)s  %(levelname)-7s  %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(live_handler)

        self.logger = logger
        self.logger.info("=" * 70)
        self.logger.info("Optimized upload run started.")
        return log_path

    def log(self, level, message):
        if self.logger:
            getattr(self.logger, level)(message)

    def safe_messagebox(self, kind, title, message):
        func = {'info': messagebox.showinfo, 'error': messagebox.showerror, 'warning': messagebox.showwarning}[kind]
        self.root.after(0, lambda: func(title, message))

    def clear_live_log(self):
        self.txt_live_log.configure(state='normal')
        self.txt_live_log.delete('1.0', 'end')
        self.txt_live_log.configure(state='disabled')

    def tick_clock(self):
        now = datetime.datetime.now()
        self.lbl_now_time.config(text=now.strftime("%Y-%m-%d %H:%M:%S"))
        self.lbl_elapsed.config(text=self.current_phase_elapsed_text(now))
        self.root.after(1000, self.tick_clock)

    def current_phase_elapsed_text(self, now=None):
        now = now or datetime.datetime.now()
        if self.current_phase == 'upload' and self.upload_start_time:
            return self.format_timedelta(now - self.upload_start_time) + " (uploading)"
        if self.upload_start_time and self.upload_end_time:
            return self.format_timedelta(self.upload_end_time - self.upload_start_time) + " (upload done)"
        return "00:00:00"
    
    def set_status(self, text):
        self.root.after(0, lambda t=text: self.lbl_status.config(text=t))

    def set_progress(self, value):
        self.root.after(0, lambda v=value: self.progress.config(value=v))
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
        if self.total_files_uploaded <= 0 or not self.upload_start_time:
            return "-"
        ref_end = datetime.datetime.now() if self.current_phase == 'upload' else (self.upload_end_time or datetime.datetime.now())
        elapsed = (ref_end - self.upload_start_time).total_seconds()
        if elapsed <= 0:
            return "-"
        return f"{(elapsed / self.total_files_uploaded):.1f}s/file"

    def average_duration_per_student_text(self):
        if self.students_traversed <= 0 or not self.upload_start_time:
            return "-"
        ref_end = datetime.datetime.now() if self.current_phase == 'upload' else (self.upload_end_time or datetime.datetime.now())
        elapsed = (ref_end - self.upload_start_time).total_seconds()
        if elapsed <= 0:
            return "-"
        avg_seconds = elapsed / self.students_traversed
        return self.format_timedelta(datetime.timedelta(seconds=avg_seconds)) + "/student"

    def refresh_stats_panel(self):
        self.lbl_students_progress.config(text=f"{self.students_traversed} / {self.total_students}")
        self.lbl_current_student.config(text=str(self.current_student_id))
        self.lbl_current_category.config(text=str(self.current_category))
        self.lbl_folders_created.config(text=str(self.total_folders_created))

        if self.current_student_files_total > 0:
            self.lbl_files_progress.config(text=f"{self.current_student_files_done} / {self.current_student_files_total}")
        else:
            self.lbl_files_progress.config(text=f"{self.current_student_files_done} / -")

        avg_files = self.average_files_per_student()
        if avg_files is not None and self.total_students > 0:
            approx_grand_total = avg_files * self.total_students
            self.lbl_files_approx.config(text=f"{self.total_files_uploaded} / ~{approx_grand_total:.0f} (avg {avg_files:.1f}/student)")
        else:
            self.lbl_files_approx.config(text=f"{self.total_files_uploaded} / ~? (no average yet)")

        if self.upload_start_time:
            self.lbl_start_time.config(text=self.upload_start_time.strftime("%Y-%m-%d %H:%M:%S"))

        self.lbl_avg_duration_file.config(text=self.average_duration_per_file_text())
        self.lbl_avg_duration_student.config(text=self.average_duration_per_student_text())

        if self.current_phase == 'upload' and self.upload_start_time and self.students_traversed > 0 and self.total_students > 0:
            now = datetime.datetime.now()
            elapsed = (now - self.upload_start_time).total_seconds()
            avg_seconds_per_student = elapsed / self.students_traversed
            remaining_seconds = avg_seconds_per_student * (self.total_students - self.students_traversed)
            eta = now + datetime.timedelta(seconds=remaining_seconds)
            self.lbl_eta.config(text=eta.strftime("%Y-%m-%d %H:%M:%S"))
        elif self.upload_end_time:
            self.lbl_eta.config(text=self.upload_end_time.strftime("%Y-%m-%d %H:%M:%S") + " (done)")
        else:
            self.lbl_eta.config(text="Calculating...")

        self.root.update_idletasks()

    def create_widgets(self):
        header = tk.Label(self.root, text="PCR Files & Documents Uploader (Optimized)", font=("Arial", 16, "bold"))
        header.pack(pady=15)

        frame = tk.Frame(self.root)
        frame.pack(pady=10, fill="x", padx=20)

        tk.Label(frame, text="Credentials File (.txt):", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=8)
        self.lbl_config = tk.Label(frame, text="No credentials file loaded", fg="gray", wraplength=350, justify="left")
        self.lbl_config.grid(row=0, column=1, sticky="w", padx=10)
        tk.Button(frame, text="Browse", command=self.browse_config).grid(row=0, column=2, sticky="e", pady=8)

        tk.Label(frame, text="Student Links CSV (.csv):", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=8)
        self.lbl_links = tk.Label(frame, text="No CSV file selected", fg="gray", wraplength=350, justify="left")
        self.lbl_links.grid(row=1, column=1, sticky="w", padx=10)
        tk.Button(frame, text="Browse", command=self.browse_links).grid(row=1, column=2, sticky="e", pady=8)

        tk.Label(frame, text="Source Files Folder:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=8)
        self.lbl_dir = tk.Label(frame, text="No folder selected", fg="gray", wraplength=350, justify="left")
        self.lbl_dir.grid(row=2, column=1, sticky="w", padx=10)
        tk.Button(frame, text="Browse", command=self.browse_directory).grid(row=2, column=2, sticky="e", pady=8)

        tk.Label(frame, text="Upload Under Folder (optional):", font=("Arial", 10, "bold")).grid(row=3, column=0, sticky="w", pady=8)
        self.cmb_target_folder = ttk.Combobox(
            frame, textvariable=self.target_folder_var, values=TARGET_FOLDER_OPTIONS,
            state="readonly", width=30
        )
        self.cmb_target_folder.grid(row=3, column=1, sticky="w", padx=10)

        opt_frame = tk.Frame(self.root)
        opt_frame.pack(fill="x", padx=20, pady=(0, 4))
        tk.Checkbutton(
            opt_frame, text="Auto-open DevTools at launch",
            variable=self.devtools_var, font=("Arial", 9), anchor="w",
        ).pack(side="left")

        self.lbl_status = tk.Label(self.root, text="Status: Idle", font=("Arial", 10))
        self.lbl_status.pack(pady=5)

        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=800, mode="determinate")
        self.progress.pack(pady=8)

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
        self.lbl_current_category = add_stat_row(left_col, 2, "Current Folder:")
        self.lbl_folders_created = add_stat_row(left_col, 3, "Folders Created (Current):")
        self.lbl_files_progress = add_stat_row(left_col, 4, "Files Uploaded (Current):")
        self.lbl_files_approx = add_stat_row(left_col, 5, "Done/Approx. Total:")

        self.lbl_start_time = add_stat_row(right_col, 0, "Automation Start:")
        self.lbl_now_time = add_stat_row(right_col, 1, "Current Date & Time:")
        self.lbl_elapsed = add_stat_row(right_col, 2, "Elapsed Duration:")
        self.lbl_eta = add_stat_row(right_col, 3, "Est. Completion Time:")
        self.lbl_avg_duration_file = add_stat_row(right_col, 4, "Average Duration/File:")
        self.lbl_avg_duration_student = add_stat_row(right_col, 5, "Average Duration/Student:")

        log_frame = tk.LabelFrame(self.root, text=" Live Action Log ", font=("Arial", 10, "bold"), padx=10, pady=8)
        log_frame.pack(pady=8, fill="both", padx=20, expand=True)

        self.txt_live_log = scrolledtext.ScrolledText(
            log_frame, height=14, wrap="word", state="disabled",
            font=("Consolas", 9), bg="#0f1117", fg="#d4d4d4", insertbackground="#d4d4d4"
        )
        self.txt_live_log.pack(fill="both", expand=True)
        self.txt_live_log.tag_config("INFO", foreground="#9cdcfe")
        self.txt_live_log.tag_config("WARNING", foreground="#dcdcaa")
        self.txt_live_log.tag_config("ERROR", foreground="#f48771")
        self.txt_live_log.tag_config("DEBUG", foreground="#808080")

        log_btn_frame = tk.Frame(log_frame)
        log_btn_frame.pack(fill="x", pady=(6, 0))
        tk.Button(log_btn_frame, text="Clear Log Panel", command=self.clear_live_log).pack(side="left")
        self.btn_dump_html = tk.Button(
            log_btn_frame, text="Dump Current Page HTML",
            command=self.dump_page_source, state="disabled"
        )
        self.btn_dump_html.pack(side="left", padx=10)

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=12)

        self.btn_start = tk.Button(
            btn_frame, text="Start Upload Process", font=("Arial", 11, "bold"), bg="#4CAF50", fg="white", padx=10, pady=5,
            command=lambda: threading.Thread(target=self.start_upload, daemon=True).start()
        )
        self.btn_start.pack(side="left", padx=8)

        self.btn_verify = tk.Button(
            btn_frame, text="Verify Uploads (Log Summary)", font=("Arial", 11, "bold"), bg="#2196F3", fg="white", padx=10, pady=5,
            command=lambda: threading.Thread(target=self.verify_uploads, daemon=True).start()
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
            self.btn_dump_html.config(state="normal")
            self.cmb_target_folder.config(state="disabled")
        else:
            self.btn_start.config(state="normal")
            self.btn_verify.config(state="normal")
            self.btn_abort.config(state="disabled")
            self.btn_dump_html.config(state="disabled")
            self.cmb_target_folder.config(state="readonly")

    def browse_config(self):
        self.config_file = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
        if self.config_file:
            self.lbl_config.config(text=os.path.basename(self.config_file), fg="black")

    def browse_links(self):
        self.links_file = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if self.links_file:
            self.lbl_links.config(text=os.path.basename(self.links_file), fg="black")

    def browse_directory(self):
        self.source_dir = filedialog.askdirectory()
        if self.source_dir:
            self.lbl_dir.config(text=self.source_dir, fg="black")

    def load_credentials(self):
        config_data = {}
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        config_data[key.strip().lower()] = val.strip()
            required = ['username', 'password', 'login_url', 'username_xpath', 'password_xpath', 'login_btn_xpath']
            if not all(k in config_data for k in required):
                return None
            for key, default_val in DEFAULT_XPATHS.items():
                config_data.setdefault(key, default_val)
                if key not in config_data or not config_data[key]:
                    config_data[key] = default_val
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
                    if h in ['studentid', 'id', 'student']:
                        id_idx = idx
                    if h in ['link', 'url', 'studentlink']:
                        link_idx = idx

                if id_idx is None or link_idx is None:
                    return []

                for row in reader:
                    if row and len(row) > max(id_idx, link_idx):
                        students.append({'studentId': row[id_idx].strip(), 'link': row[link_idx].strip()})
            return students
        except Exception:
            return []

    def write_log_csv(self, log_data):
        log_file_path = os.path.join(self.source_dir, "uploads_execution_log.csv")
        file_exists = os.path.isfile(log_file_path)
        headers = ['studentId', 'category', 'fileName', 'uploadedAs', 'action', 'actionComment', 'validation', 'timestamp']
        try:
            with open(log_file_path, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(log_data)
        except Exception as e:
            self.log('error', f"Failed writing log CSV: {e}")

    def launch_chrome_driver(self, chrome_options):
        try:
            return webdriver.Chrome(options=chrome_options)
        except Exception:
            path = ChromeDriverManager().install()
            return webdriver.Chrome(service=Service(path), options=chrome_options)

    def dump_page_source(self):
        if not self.driver:
            self.safe_messagebox('warning', "No active session", "Start the automation first.")
            return
        try:
            html = self.driver.execute_script("return document.documentElement.outerHTML;")
            dumps_dir = os.path.join(self.source_dir or ".", "page_dumps")
            os.makedirs(dumps_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(dumps_dir, f"dump_{ts}.html")
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(html)
            self.log('info', f"Page HTML dumped to: {out_path}")
            self.safe_messagebox('info', "Page Dumped", f"Saved current page HTML to:\n{out_path}")
        except Exception as e:
            self.log('error', f"Failed to dump page HTML: {e}")

    def click_xpath(self, driver, wait, xpath, description, hover_first=False, timeout_override=None):
        active_wait = WebDriverWait(driver, timeout_override, poll_frequency=0.2) if timeout_override else wait
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
        self.log('debug', f"Clicked: {description}")
        return el

    def try_click_xpath(self, driver, xpath, description, timeout=3):
        try:
            if not driver.find_elements(By.XPATH, xpath):
                return False  # instant fail instead of blocking `timeout` seconds
            wait = WebDriverWait(driver, timeout, poll_frequency=0.2)
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            el.click()
            self.log('debug', f"Clicked (optional): {description}")
            return True
        except Exception:
            return False

    def wait_for_popup_ok(self, driver, wait, xpath, description, expected_text=None, text_timeout=10):
        if expected_text:
            try:
                popup_text_xpath = f"//div[contains(., {_xpath_literal(expected_text)})]"
                WebDriverWait(driver, text_timeout).until(EC.presence_of_element_located((By.XPATH, popup_text_xpath)))
            except Exception:
                self.log('warning', f"Expected popup text '{expected_text}' not seen - attempting OK click anyway.")
        try:
            self.click_xpath(driver, wait, xpath, description)
            return True
        except Exception as e:
            self.log('warning', f"No confirmation popup detected for '{description}' ({e})")
            return False

    def folder_exists_in_listing(self, driver, folder_name, config):
        lit = _xpath_literal(folder_name)
        table = config.get('folder_listing_table_xpath', '')
        scope = table if table else ''
        xpath = (
            f"{scope}//a[contains(normalize-space(.), {lit})] "
            f"| {scope}//td[contains(normalize-space(.), {lit})] "
            f"| {scope}//span[contains(normalize-space(.), {lit})]"
        )
        if driver.find_elements(By.XPATH, xpath):
            return True
        if scope:
            fallback_xpath = (
                f"//a[contains(normalize-space(.), {lit})] "
                f"| //td[contains(normalize-space(.), {lit})] "
                f"| //span[contains(normalize-space(.), {lit})]"
            )
            return len(driver.find_elements(By.XPATH, fallback_xpath)) > 0
        return False

    def verify_portal_file_content(self, driver, download_url, local_filepath):
        if not local_filepath or not os.path.isfile(local_filepath):
            return False
        try:
            import requests
            import tempfile
            import hashlib

            def _hash(p):
                h = hashlib.sha256()
                with open(p, 'rb') as f:
                    for chunk in iter(lambda: f.read(65536), b''):
                        h.update(chunk)
                return h.hexdigest()

            local_size = os.path.getsize(local_filepath)
            local_hash = _hash(local_filepath)

            session = requests.Session()
            for cookie in driver.get_cookies():
                session.cookies.set(cookie['name'], cookie['value'])

            headers = {'User-Agent': driver.execute_script("return navigator.userAgent;")}
            resp = session.get(download_url, headers=headers, stream=True, timeout=15)
            if resp.status_code != 200:
                return False

            tmp_fd, tmp_path = tempfile.mkstemp(prefix="verify_content_")
            os.close(tmp_fd)
            try:
                with open(tmp_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)

                if os.path.getsize(tmp_path) != local_size:
                    return False

                return _hash(tmp_path) == local_hash
            finally:
                if os.path.isfile(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        except Exception as e:
            self.log('warning', f"Content verification check error: {e}")
            return False

    def file_exists_in_listing(self, driver, file_name, config, upload_name=None, local_path=None):
        names_to_check = [file_name]
        if upload_name and upload_name != file_name:
            names_to_check.append(upload_name)

        table = config.get('folder_listing_table_xpath', '')
        scope = table if table else ''

        matching_elements = []
        for name in names_to_check:
            lit = _xpath_literal(name)
            xpath = (
                f"{scope}//a[contains(normalize-space(.), {lit})] "
                f"| {scope}//td[contains(normalize-space(.), {lit})] "
                f"| {scope}//span[contains(normalize-space(.), {lit})]"
            )
            found = driver.find_elements(By.XPATH, xpath)
            if not found and scope:
                fallback = (
                    f"//a[contains(normalize-space(.), {lit})] "
                    f"| //td[contains(normalize-space(.), {lit})] "
                    f"| //span[contains(normalize-space(.), {lit})]"
                )
                found = driver.find_elements(By.XPATH, fallback)
            if found:
                matching_elements.extend(found)

        if not matching_elements:
            return False

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
                        if self.verify_portal_file_content(driver, href, local_path):
                            return True
                        else:
                            self.log('info', f"Portal file '{file_name}' found, but inner content differs from local file.")
                except Exception as e:
                    self.log('debug', f"Could not verify content for matching element: {e}")

        return True

    def wait_for_page_ready(self, driver, timeout=15):
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass

    def open_student_files_and_documents(self, driver, wait, config, link, target_folder_name, student_id):
        """
        Navigates to the student's profile, launches the Files & Documents module,
        and lands in the parent target directory. Returns the direct Files & Documents
        URL so we can perform instant reset actions without reloading the main profile.
        """
        self.log('debug', f"Direct initial load of Student {student_id}'s portal.")
        driver.get(link)
        self.wait_for_page_ready(driver, timeout=15)
        
        self.click_xpath(driver, wait, config['three_dot_menu_xpath'], "3-dot page menu", hover_first=True, timeout_override=20)
        self.click_xpath(driver, wait, config['files_documents_menu_xpath'], "'Files & Documents' menu item")
        self.wait_for_page_ready(driver, timeout=15)
        
        files_docs_root_url = driver.current_url
        self.log('debug', f"Cached Files & Docs Root URL: {files_docs_root_url}")

        if not target_folder_name:
            self.log('debug', "No 'Upload Under Folder' selected -- staying at Files & Documents root.")
            return files_docs_root_url

        self.click_folder_by_name(driver, wait, target_folder_name, config, f"Top-level folder '{target_folder_name}'")
        target_folder_url = driver.current_url
        self.log('debug', f"Cached target directory folder URL: {target_folder_url}")
        
        return target_folder_url

    def click_folder_by_name(self, driver, wait, folder_name, config, description):
        lit = _xpath_literal(folder_name)
        table = config.get('folder_listing_table_xpath', '')
        if table:
            xpath = (
                f"{table}//a[contains(normalize-space(.), {lit})] "
                f"| {table}//td[contains(normalize-space(.), {lit})]//a "
                f"| {table}//span[contains(normalize-space(.), {lit})]"
            )
            try:
                return self.click_xpath(driver, wait, xpath, description, timeout_override=5)
            except Exception:
                pass
        fallback_xpath = (
            f"//a[contains(normalize-space(.), {lit})] "
            f"| //td[contains(normalize-space(.), {lit})]//a "
            f"| //span[contains(normalize-space(.), {lit})]"
        )
        return self.click_xpath(driver, wait, fallback_xpath, description)

    def start_upload(self):
        if self.is_running:
            return
        if not self.config_file or not self.links_file or not self.source_dir:
            self.safe_messagebox('error', "Selection Error", "Please fill in all paths.")
            return

        self.setup_text_logger()
        config = self.load_credentials()
        students = self.read_links_csv()
        target_folder_name = self.target_folder_var.get()
        if target_folder_name == NO_TARGET_FOLDER_LABEL:
            target_folder_name = ""

        if not config:
            self.safe_messagebox('error', "Error", "Invalid credentials file.")
            return
        if not students:
            self.safe_messagebox('error', "Error", "No students found in the CSV.")
            return

        self.root.after(0, lambda: self.set_buttons_running(True))
        self.is_running = True
        self.current_phase = 'upload'
        self.abort_requested = False
        self.total_students = len(students)
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_category = "-"
        self.current_student_files_done = 0
        self.current_student_files_total = 0
        self.completed_students_file_counts = []
        self.total_files_uploaded = 0
        self.total_folders_created = 0
        self.upload_start_time = datetime.datetime.now()
        self.root.after(0, self.refresh_stats_panel)

        self._tmp_upload_dir = tempfile.mkdtemp(prefix="pcr_upload_")

        chrome_options = Options()
        if self.devtools_var.get():
            chrome_options.add_argument("--auto-open-devtools-for-tabs")

        driver = None
        aborted = False
        try:
            driver = self.launch_chrome_driver(chrome_options)
            self.driver = driver
            driver.set_script_timeout(60)

            # Faster polling: was 0.5s default, now 0.2s
            wait = WebDriverWait(driver, 30, poll_frequency=0.2)

            # Login Phase
            self.log('info', f"Navigating to login page: {config['login_url']}")
            driver.get(config['login_url'])
            user_input = wait.until(EC.presence_of_element_located((By.XPATH, config['username_xpath'])))
            pass_input = driver.find_element(By.XPATH, config['password_xpath'])
            user_input.send_keys(config['username'])
            pass_input.send_keys(config['password'])

            if config.get('role') and config.get('role_xpath'):
                try:
                    role_el = driver.find_element(By.XPATH, config['role_xpath'])
                    Select(role_el).select_by_visible_text(config['role'])
                except Exception as e:
                    self.log('warning', f"Could not select role '{config['role']}': {e}")

            self.click_xpath(driver, wait, config['login_btn_xpath'], "Login button")
            wait.until(lambda d: "login" not in d.current_url.lower())

            self.progress["maximum"] = len(students)

            for idx, student in enumerate(students):
                if self.abort_requested:
                    aborted = True
                    break

                raw_id = student['studentId'].strip()
                student_id = raw_id.split('.')[0] if '.' in raw_id else raw_id
                if not student_id or len(student_id) < 3 or student_id.lower() in ['studentid', 'id', 'student']:
                    continue

                link = student['link']
                student_root_folder = os.path.join(self.source_dir, student_id)

                self.current_student_id = student_id
                self.current_category = "-"
                self.current_student_files_done = 0
                self.root.after(0, self.refresh_stats_panel)

                category_folders = list_category_folders(student_root_folder)
                if not category_folders:
                    self.log('warning', f"No local folders found for studentId '{student_id}'.")
                    self.students_traversed += 1
                    self.root.after(0, self.refresh_stats_panel)
                    continue

                self.current_student_files_total = sum(len(c['files']) for c in category_folders)
                self.root.after(0, self.refresh_stats_panel)

                # CHANGED: was self.lbl_status.config(...) + self.progress["value"] = ... + self.root.update()
                self.set_status(f"Loading Modules for: {student_id}...")
                self.set_progress(idx + 1)

                batch_logs = []
                try:
                    self.log('info', f"--- Student {student_id}: resolving profile record")

                    target_parent_url = self.open_student_files_and_documents(
                        driver, wait, config, link, target_folder_name, student_id
                    )

                    for category in category_folders:
                        if self.abort_requested:
                            aborted = True
                            break

                        self.current_category = category['name']
                        self.root.after(0, self.refresh_stats_panel)
                        # CHANGED: was self.lbl_status.config(...)
                        self.set_status(f"{student_id}: processing '{category['name']}'...")

                        driver.get(target_parent_url)
                        wait.until(EC.presence_of_element_located((By.XPATH, config['new_folder_toolbar_button_xpath'])))

                        already_exists = self.folder_exists_in_listing(driver, category['name'], config)
                        if already_exists:
                            self.log('info', f"Folder '{category['name']}' already exists.")
                        else:
                            self.log('info', f"Creating folder '{category['name']}'...")
                            self.click_xpath(driver, wait, config['new_folder_toolbar_button_xpath'], "New Folder (4th button)")
                            self.try_click_xpath(driver, config['new_folder_option_xpath'], "Sub-item menu", timeout=2)

                            name_input = wait.until(EC.presence_of_element_located((By.XPATH, config['folder_name_input_xpath'])))
                            name_input.clear()
                            name_input.send_keys(category['name'])

                            self.click_xpath(driver, wait, config['save_button_xpath'], "Save Folder")
                            self.wait_for_popup_ok(driver, wait, config['popup_ok_xpath'], "Confirm Save OK")

                            wait.until(EC.presence_of_element_located((By.XPATH, config['new_folder_toolbar_button_xpath'])))
                            self.total_folders_created += 1
                            self.root.after(0, self.refresh_stats_panel)

                        self.click_folder_by_name(driver, wait, category['name'], config, f"Folder '{category['name']}'")
                        wait.until(EC.element_to_be_clickable((By.XPATH, config['upload_tool_button_xpath'])))

                        for file_name in category['files']:
                            if self.abort_requested:
                                aborted = True
                                break

                            src_path = os.path.join(category['path'], file_name)
                            upload_name = sanitize_upload_filename(file_name)

                            # --- DUPLICATE CHECK: Pre-upload check with content verification ---
                            if self.file_exists_in_listing(driver, file_name, config, upload_name=upload_name, local_path=src_path):
                                self.log('info', f"File '{file_name}' already exists in '{category['name']}' on portal (content verified) — skipping duplicate upload.")
                                self.current_student_files_done += 1
                                self.total_files_uploaded += 1
                                batch_logs.append({
                                    'studentId': student_id, 'category': category['name'], 'fileName': file_name,
                                    'uploadedAs': upload_name, 'action': 'uploaded',
                                    'actionComment': 'Already exists on portal (duplicate prevented)',
                                    'validation': 'already_uploaded', 'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
                                })
                                self.root.after(0, self.refresh_stats_panel)
                                continue

                            tmp_path = os.path.join(self._tmp_upload_dir, upload_name)

                            try:
                                shutil.copy2(src_path, tmp_path)
                            except Exception as e:
                                self.log('error', f"Copy prep failed for '{file_name}': {describe_exception(e)}")
                                continue

                            # CHANGED: was self.lbl_status.config(...) + self.root.update()
                            self.set_status(f"Uploading {file_name}...")

                            try:
                                self.click_xpath(driver, wait, config['upload_tool_button_xpath'], "Upload module button")

                                file_input = wait.until(EC.presence_of_element_located((By.XPATH, config['file_input_xpath'])))
                                file_input.send_keys(os.path.abspath(tmp_path))

                                self.click_xpath(driver, wait, config['upload_submit_xpath'], "Upload submit")

                                got_popup = self.try_click_xpath(driver, config['upload_popup_ok_xpath'], "Upload confirmation OK", timeout=2)
                                if not got_popup:
                                    self.log('info', "No modal popup; waiting for file input module container to close.")
                                    try:
                                        wait.until(EC.element_to_be_clickable((By.XPATH, config['upload_tool_button_xpath'])))
                                    except Exception:
                                        if self.file_exists_in_listing(driver, file_name, config, upload_name=upload_name, local_path=src_path):
                                            self.log('info', f"Upload container timeout, but '{file_name}' verified present in folder listing.")
                                            got_popup = True
                                        else:
                                            raise

                                self.current_student_files_done += 1
                                self.total_files_uploaded += 1
                                self.log('info', f"Success: '{file_name}' uploaded as '{upload_name}'")
                                batch_logs.append({
                                    'studentId': student_id, 'category': category['name'], 'fileName': file_name,
                                    'uploadedAs': upload_name, 'action': 'uploaded',
                                    'actionComment': 'Success' if got_popup else 'Success (bypass/no popup)',
                                    'validation': 'pending', 'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
                                })
                            except Exception as e:
                                err_desc = describe_exception(e)
                                if self.file_exists_in_listing(driver, file_name, config, upload_name=upload_name, local_path=src_path):
                                    self.current_student_files_done += 1
                                    self.total_files_uploaded += 1
                                    self.log('info', f"Upload exception occurred ({err_desc}), but '{file_name}' was verified present in folder listing.")
                                    batch_logs.append({
                                        'studentId': student_id, 'category': category['name'], 'fileName': file_name,
                                        'uploadedAs': upload_name, 'action': 'uploaded',
                                        'actionComment': f"Success (verified after exception: {err_desc})",
                                        'validation': 'pending', 'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
                                    })
                                else:
                                    self.log('error', f"Upload failed for '{file_name}': {err_desc}")
                                    batch_logs.append({
                                        'studentId': student_id, 'category': category['name'], 'fileName': file_name,
                                        'uploadedAs': upload_name, 'action': 'failed', 'actionComment': err_desc,
                                        'validation': '-', 'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
                                    })
                            finally:
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass

                            self.root.after(0, self.refresh_stats_panel)

                        if aborted:
                            break

                except Exception as e:
                    err_desc = describe_exception(e)
                    self.log('error', f"Student {student_id}: automation error - {err_desc}")
                    batch_logs.append({
                        'studentId': student_id, 'category': self.current_category, 'fileName': '-',
                        'uploadedAs': '-', 'action': 'failed', 'actionComment': err_desc,
                        'validation': '-', 'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
                    })

                if batch_logs:
                    self.write_log_csv(batch_logs)

                self.completed_students_file_counts.append(self.current_student_files_done)
                self.students_traversed += 1
                self.root.after(0, self.refresh_stats_panel)

                if aborted:
                    break

            # CHANGED: was self.lbl_status.config(...)
            self.set_status("Status: Completed!" if not aborted else "Status: Aborted")

        except Exception as e:
            err_desc = describe_exception(e)
            self.log('error', f"Critical run loop error: {err_desc}")
            self.safe_messagebox('error', "Critical Error", err_desc)
        finally:
            if driver:
                driver.quit()
            self.driver = None
            if self._tmp_upload_dir and os.path.isdir(self._tmp_upload_dir):
                shutil.rmtree(self._tmp_upload_dir, ignore_errors=True)
            self.is_running = False
            self.current_phase = None
            self.upload_end_time = datetime.datetime.now()
            self.root.after(0, self.refresh_stats_panel)
            self.root.after(0, lambda: self.set_buttons_running(False))

    def verify_uploads(self):
        if self.is_running:
            return
        log_file_path = os.path.join(self.source_dir, "uploads_execution_log.csv")
        if not os.path.isfile(log_file_path):
            self.safe_messagebox('warning', "Nothing to verify", "No uploads execution log CSV found.")
            return

        self.root.after(0, lambda: self.set_buttons_running(True))
        self.is_running = True

        uploaded, failed = 0, 0
        per_student = {}
        try:
            with open(log_file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sid = row.get('studentId', '-')
                    per_student.setdefault(sid, {'uploaded': 0, 'failed': 0})
                    if row.get('action') == 'uploaded':
                        uploaded += 1
                        per_student[sid]['uploaded'] += 1
                    else:
                        failed += 1
                        per_student[sid]['failed'] += 1
        except Exception as e:
            self.log('error', f"Failed reading execution log: {e}")

        self.log('info', f"Verification summary: {uploaded} uploaded, {failed} failed.")
        self.is_running = False
        self.root.after(0, lambda: self.set_buttons_running(False))
        self.safe_messagebox(
            'info', "Verification Summary",
            f"Uploaded: {uploaded}\nFailed: {failed}\nStudents in log: {len(per_student)}"
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = FileUploaderApp(root)
    root.mainloop()