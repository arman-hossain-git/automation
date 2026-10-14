import os
import re
import time
import csv
import hashlib
import logging
import threading
import urllib.parse
import datetime
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ======================================================================
# Site-agnostic detection defaults
# ======================================================================

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
    'view profile', 'back', 'back to top', 'settings', 'menu', '...', '»', '«'
}

DOWNLOAD_PATH_HINTS = ('download', 'file', 'attachment', 'document', 'export', 'media', 'export')

CREDENTIALS_TEMPLATE = """\
# ==================================================================
# Universal File Downloader - credentials / site configuration file
# Lines starting with # are comments. key=value, one per line.
# ==================================================================

# ---- REQUIRED ----
username=
password=
login_url=

# ---- OPTIONAL: login field overrides ----
username_xpath=
password_xpath=
login_btn_xpath=

# ---- OPTIONAL: file-detection overrides ----
# Use these if the numeric column index doesn't work.
row_container_xpath=
category_xpath=
link_selector_xpath=
min_columns=2
extra_extensions=
exclude_link_text=

# ---- OPTIONAL: interaction mode ----
# 'static_links'      (default) - scrape file links already sitting on the
#                        page, e.g. PCR-style grids.
# 'generate_documents' - the page has a program/record dropdown; for each
#                        option, one or more "Generate ___" buttons create
#                        a document on demand and preview it in a modal
#                        (MyPath-style). The tool selects every dropdown
#                        option in turn, clicks every Generate button, and
#                        captures the resulting PDF.
interaction_mode=static_links

# ---- OPTIONAL: generate_documents mode settings ----
# (only read when interaction_mode=generate_documents; leave blank to use
# the built-in MUI-dropdown defaults, override only if detection fails)
#
# Button that opens the program/record dropdown.
dropdown_button_xpath=
# Each selectable option once the dropdown is open.
dropdown_option_xpath=
# Each "Generate ___" button on the page for the currently selected option.
generate_buttons_xpath=
# The modal/dialog that appears after clicking Generate, containing the
# rendered PDF (used to locate the embedded PDF's real source URL).
modal_xpath=
"""


def _remove_empty_dirs(root_dir):
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


def _sanitize(name):
    """Clean up folder/file names: remove invalid characters, newlines, and trim."""
    if not name:
        return "Uncategorized"
    # Replace newlines and carriage returns with a space
    name = name.replace('\n', ' ').replace('\r', ' ')
    # Replace other invalid characters
    for bad_char in '<>:"/\\|?*':
        name = name.replace(bad_char, '_')
    # Collapse multiple spaces
    name = ' '.join(name.split())
    # Trim to avoid overly long folder names (Windows limit ~255, but we'll keep safe)
    if len(name) > 100:
        name = name[:100]
    return name.strip() or "Uncategorized"


class DarkStyle:
    @staticmethod
    def apply():
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background='#2b2b2b', foreground='#f0f0f0', fieldbackground='#3c3c3c')
        style.configure('TLabel', background='#2b2b2b', foreground='#f0f0f0')
        style.configure('TButton', background='#3c3c3c', foreground='#f0f0f0', borderwidth=1, focusthickness=3)
        style.map('TButton', background=[('active', '#5a5a5a')])
        style.configure('TEntry', fieldbackground='#3c3c3c', foreground='#f0f0f0')
        style.configure('TLabelframe', background='#2b2b2b', foreground='#f0f0f0', borderwidth=1, relief='solid')
        style.configure('TLabelframe.Label', background='#2b2b2b', foreground='#f0f0f0')
        style.configure('TProgressbar', background='#4CAF50', troughcolor='#3c3c3c')
        style.configure('TNotebook', background='#2b2b2b')
        style.configure('TNotebook.Tab', background='#3c3c3c', foreground='#f0f0f0', padding=[10, 2])
        style.map('TNotebook.Tab', background=[('selected', '#4CAF50')])
        return style


class LogTextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setLevel(logging.DEBUG)

    def emit(self, record):
        msg = self.format(record)
        tag = record.levelname
        def append():
            self.text_widget.insert(tk.END, msg + "\n", tag)
            self.text_widget.see(tk.END)
        self.text_widget.after(0, append)


class FileDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Universal Web File Downloader")
        self.root.geometry("1000x800")
        self.root.minsize(800, 600)
        self.root.configure(bg='#2b2b2b')

        self.style = DarkStyle.apply()

        self.config_file = ""
        self.links_file = ""
        self.download_dir = ""
        self.logger = None
        self.test_url = tk.StringVar(value="")
        self.allow_duplicates = tk.BooleanVar(value=True)

        # Filters
        self.filter_cat_include = tk.StringVar(value="")
        self.filter_cat_exclude = tk.StringVar(value="")
        self.filter_type_include = tk.StringVar(value="")
        self.filter_name_include = tk.StringVar(value="")
        self.filter_name_exclude = tk.StringVar(value="")
        self.filter_min_kb = tk.StringVar(value="")
        self.filter_max_kb = tk.StringVar(value="")

        # Segment & Category settings
        self.segment_names = tk.StringVar(value="")
        self.default_segment = tk.StringVar(value="Uncategorized")
        self.category_column_index = tk.StringVar(value="2")

        # Progress
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

        self.log_text = None
        self.progress = None
        self.btn_start = None
        self.btn_verify = None
        self.btn_abort = None
        self.lbl_status = None

        self.create_widgets()
        self.tick_clock()
        self.setup_logging_ui()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def setup_text_logger(self):
        if not self.download_dir:
            return
        log_path = os.path.join(self.download_dir, "automation_debug.log")
        logger = logging.getLogger("universal_downloader")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        if self.log_text:
            self.log_handler = LogTextHandler(self.log_text)
            self.log_handler.setLevel(logging.DEBUG)
            self.log_handler.setFormatter(formatter)
            logger.addHandler(self.log_handler)

        self.logger = logger
        self.logger.info("=" * 70)
        self.logger.info("New automation run started.")
        return log_path

    def log(self, level, message):
        if self.logger:
            getattr(self.logger, level)(message)

    def safe_messagebox(self, kind, title, message):
        func = {'info': messagebox.showinfo, 'error': messagebox.showerror, 'warning': messagebox.showwarning}[kind]
        self.root.after(0, lambda: func(title, message))

    def setup_logging_ui(self):
        pass

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def create_widgets(self):
        main_canvas = tk.Canvas(self.root, bg='#2b2b2b', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=main_canvas.yview)
        scrollable_frame = ttk.Frame(main_canvas, style='TFrame')
        scrollable_frame.bind(
            "<Configure>",
            lambda e: main_canvas.configure(scrollregion=main_canvas.bbox("all"))
        )
        main_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        main_canvas.configure(yscrollcommand=scrollbar.set)

        main_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._build_ui(scrollable_frame)

    def _build_ui(self, parent):
        # Header
        tk.Label(parent, text="Universal Web File Downloader", font=("Segoe UI", 18, "bold"), bg='#2b2b2b', fg='#4CAF50').pack(pady=(10, 0))
        tk.Label(parent, text="Dark Edition • Intelligent Web Scraper", font=("Segoe UI", 10), bg='#2b2b2b', fg='#888888').pack(pady=(0, 10))

        # ---------- Credentials ----------
        cred_frame = self._make_section(parent, "Credentials & Input")
        self.lbl_config = tk.Label(cred_frame, text="No credentials file loaded", fg="#888888", bg='#2b2b2b',
                                   font=("Segoe UI", 9), wraplength=350, justify="left")
        self.lbl_links = tk.Label(cred_frame, text="No CSV file selected", fg="#888888", bg='#2b2b2b',
                                  font=("Segoe UI", 9), wraplength=350, justify="left")
        self.lbl_dir = tk.Label(cred_frame, text="No folder selected", fg="#888888", bg='#2b2b2b',
                                font=("Segoe UI", 9), wraplength=350, justify="left")

        self._add_file_row(cred_frame, "Config File (.txt):", self.lbl_config, self.browse_config, self.create_template_config, 0)
        self._add_file_row(cred_frame, "Record Links CSV:", self.lbl_links, self.browse_links, None, 1)
        self._add_file_row(cred_frame, "Save Files To:", self.lbl_dir, self.browse_directory, None, 2)
        self._add_entry_row(cred_frame, "Test URL (optional):", self.test_url, 3)
        tk.Button(cred_frame, text="Preview Detection", bg="#FF9800", fg="white", font=("Segoe UI", 9, "bold"),
                  command=lambda: threading.Thread(target=self.preview_detection, daemon=True).start()).grid(row=4, column=2, sticky="e", padx=5, pady=5)

        # ---------- Segments & Categories ----------
        seg_frame = self._make_section(parent, "Segments & Categories")
        tk.Label(seg_frame, text="Segment Names (comma‑separated):", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=2)
        tk.Entry(seg_frame, textvariable=self.segment_names, width=60, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=0, column=1, padx=10, pady=2, sticky="w")
        tk.Label(seg_frame, text="e.g. Action Files, Another Widget", bg='#2b2b2b', fg='#888888', font=("Segoe UI", 8)).grid(row=1, column=1, sticky="w", padx=10)

        tk.Label(seg_frame, text="Default Segment (if no names):", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=2)
        tk.Entry(seg_frame, textvariable=self.default_segment, width=40, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=2, column=1, padx=10, pady=2, sticky="w")
        tk.Label(seg_frame, text="folder name when no segment names given", bg='#2b2b2b', fg='#888888', font=("Segoe UI", 8)).grid(row=3, column=1, sticky="w", padx=10)

        tk.Label(seg_frame, text="Category Column Index (1‑based):", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=4, column=0, sticky="w", pady=2)
        sp = tk.Spinbox(seg_frame, from_=1, to=20, width=5, textvariable=self.category_column_index,
                        bg='#3c3c3c', fg='#f0f0f0', buttonbackground='#3c3c3c')
        sp.grid(row=4, column=1, padx=10, pady=2, sticky="w")
        tk.Label(seg_frame, text="which column contains the category/folder name", bg='#2b2b2b', fg='#888888', font=("Segoe UI", 8)).grid(row=5, column=1, sticky="w", padx=10)

        # ---------- File Filters ----------
        filt_frame = self._make_section(parent, "File Filters (optional)")
        row = 0
        tk.Label(filt_frame, text="Categories to include:", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(filt_frame, textvariable=self.filter_cat_include, width=50, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=row, column=1, padx=10, pady=2, sticky="w")
        row += 1
        tk.Label(filt_frame, text="Categories to exclude:", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(filt_frame, textvariable=self.filter_cat_exclude, width=50, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=row, column=1, padx=10, pady=2, sticky="w")
        row += 1
        tk.Label(filt_frame, text="File types (extensions):", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(filt_frame, textvariable=self.filter_type_include, width=50, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=row, column=1, padx=10, pady=2, sticky="w")
        row += 1
        tk.Label(filt_frame, text="Filename must contain:", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(filt_frame, textvariable=self.filter_name_include, width=50, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=row, column=1, padx=10, pady=2, sticky="w")
        row += 1
        tk.Label(filt_frame, text="Filename must NOT contain:", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(filt_frame, textvariable=self.filter_name_exclude, width=50, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=row, column=1, padx=10, pady=2, sticky="w")
        row += 1
        tk.Label(filt_frame, text="File size (KB):", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=2)
        size_frame = tk.Frame(filt_frame, bg='#2b2b2b')
        size_frame.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        tk.Label(size_frame, text="Min:", bg='#2b2b2b', fg='#f0f0f0').pack(side="left")
        tk.Entry(size_frame, textvariable=self.filter_min_kb, width=10, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').pack(side="left", padx=(4, 14))
        tk.Label(size_frame, text="Max:", bg='#2b2b2b', fg='#f0f0f0').pack(side="left")
        tk.Entry(size_frame, textvariable=self.filter_max_kb, width=10, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').pack(side="left", padx=(4, 0))

        # ---------- Options ----------
        opt_frame = self._make_section(parent, "Options")
        tk.Checkbutton(opt_frame, text='Allow duplicate filenames (add __2, __3...)', variable=self.allow_duplicates,
                       bg='#2b2b2b', fg='#f0f0f0', selectcolor='#2b2b2b', font=("Segoe UI", 9)).pack(anchor="w", pady=2)

        # ---------- Status & Progress ----------
        self.lbl_status = tk.Label(parent, text="Status: Idle", font=("Segoe UI", 10, "bold"), bg='#2b2b2b', fg='#4CAF50')
        self.lbl_status.pack(pady=10)
        self.progress = ttk.Progressbar(parent, orient="horizontal", length=800, mode="determinate")
        self.progress.pack(pady=5)

        # ---------- Stats ----------
        stats_frame = tk.LabelFrame(parent, text=" Live Progress ", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 10, "bold"))
        stats_frame.pack(pady=10, fill="x", padx=10)
        left = tk.Frame(stats_frame, bg='#2b2b2b')
        left.pack(side="left", padx=10)
        right = tk.Frame(stats_frame, bg='#2b2b2b')
        right.pack(side="right", padx=10)

        self.lbl_students_progress = self._add_stat(left, "Records Traversed:", 0)
        self.lbl_current_student = self._add_stat(left, "Current Record:", 1)
        self.lbl_files_progress = self._add_stat(left, "Files Downloaded (Current):", 2)
        self.lbl_files_approx = self._add_stat(left, "Done/Approx. Total:", 3)
        self.lbl_start_time = self._add_stat(left, "Automation Start:", 4)

        self.lbl_now_time = self._add_stat(right, "Current Date & Time:", 0)
        self.lbl_elapsed = self._add_stat(right, "Elapsed Duration:", 1)
        self.lbl_eta = self._add_stat(right, "Est. Completion Time:", 2)
        self.lbl_avg_duration_file = self._add_stat(right, "Avg Duration/File:", 3)
        self.lbl_avg_duration_student = self._add_stat(right, "Avg Duration/Record:", 4)

        # ---------- Buttons ----------
        btn_frame = tk.Frame(parent, bg='#2b2b2b')
        btn_frame.pack(pady=15)
        self.btn_start = tk.Button(btn_frame, text="Start Download", bg="#4CAF50", fg="white", font=("Segoe UI", 11, "bold"),
                                   command=lambda: threading.Thread(target=self.start_download, daemon=True).start())
        self.btn_start.pack(side="left", padx=5)

        self.btn_verify = tk.Button(btn_frame, text="Verify Downloads", bg="#2196F3", fg="white", font=("Segoe UI", 11, "bold"),
                                    command=lambda: threading.Thread(target=self.verify_downloads, daemon=True).start())
        self.btn_verify.pack(side="left", padx=5)

        self.btn_abort = tk.Button(btn_frame, text="Abort", bg="#F44336", fg="white", font=("Segoe UI", 11, "bold"),
                                   command=self.request_abort, state="disabled")
        self.btn_abort.pack(side="left", padx=5)

        # ---------- Log Output ----------
        log_frame = tk.LabelFrame(parent, text=" Log Output ", bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 10, "bold"))
        log_frame.pack(pady=10, fill="both", expand=True, padx=10)
        self.log_text = scrolledtext.ScrolledText(log_frame, bg='#1e1e1e', fg='#d4d4d4', font=("Consolas", 9),
                                                  insertbackground='white', wrap="word", height=10)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.tag_config("DEBUG", foreground="#888888")
        self.log_text.tag_config("INFO", foreground="#4CAF50")
        self.log_text.tag_config("WARNING", foreground="#FF9800")
        self.log_text.tag_config("ERROR", foreground="#F44336")

    # Helpers
    def _make_section(self, parent, title):
        frame = tk.LabelFrame(parent, text=title, bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 10, "bold"), padx=10, pady=5)
        frame.pack(pady=5, fill="x", padx=10)
        return frame

    def _add_file_row(self, parent, label, label_widget, browse_cmd, extra_cmd, row):
        tk.Label(parent, text=label, bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=4)
        label_widget.grid(row=row, column=1, sticky="w", padx=10)
        btn_frame = tk.Frame(parent, bg='#2b2b2b')
        btn_frame.grid(row=row, column=2, sticky="e", padx=5)
        tk.Button(btn_frame, text="Browse", bg='#3c3c3c', fg='#f0f0f0', command=browse_cmd).pack(side="left")
        if extra_cmd:
            tk.Button(btn_frame, text="Create Template", bg='#3c3c3c', fg='#f0f0f0', command=extra_cmd).pack(side="left", padx=5)

    def _add_entry_row(self, parent, label, var, row):
        tk.Label(parent, text=label, bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=4)
        tk.Entry(parent, textvariable=var, width=60, bg='#3c3c3c', fg='#f0f0f0', insertbackground='white').grid(row=row, column=1, padx=10, sticky="w")

    def _add_stat(self, parent, label, row):
        tk.Label(parent, text=label, bg='#2b2b2b', fg='#f0f0f0', font=("Segoe UI", 9, "bold"), anchor="w").grid(row=row, column=0, sticky="w", pady=2)
        lbl = tk.Label(parent, text="-", bg='#2b2b2b', fg='#4CAF50', font=("Segoe UI", 9), anchor="w")
        lbl.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        return lbl
    def _normalize_href(self, href):
        if not href:
            return ""
        parsed = urllib.parse.urlparse(href)
        # Remove trailing slash from path
        path = parsed.path.rstrip('/')
        # Sort query parameters to ensure order doesn't matter
        query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query)))
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, parsed.fragment))
    # ------------------------------------------------------------------
    # Stats & helpers
    # ------------------------------------------------------------------
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
        return self.format_timedelta(datetime.timedelta(seconds=avg_seconds)) + "/record"

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
            self.lbl_files_approx.config(text=f"{self.total_files_downloaded} / ~{approx_grand_total:.0f} (avg {avg_files:.1f}/record)")
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
            self.lbl_config.config(text=os.path.basename(self.config_file), fg="#4CAF50")

    def browse_links(self):
        self.links_file = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if self.links_file:
            self.lbl_links.config(text=os.path.basename(self.links_file), fg="#4CAF50")

    def browse_directory(self):
        self.download_dir = filedialog.askdirectory()
        if self.download_dir:
            self.lbl_dir.config(text=self.download_dir, fg="#4CAF50")

    def create_template_config(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="credentials.txt",
                                             filetypes=[("Text Files", "*.txt")])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(CREDENTIALS_TEMPLATE)
            self.config_file = path
            self.lbl_config.config(text=os.path.basename(path), fg="#4CAF50")
            self.safe_messagebox('info', "Template Created",
                                  f"Template saved to:\n{path}\n\nFill in the required fields, then load it.")
        except Exception as e:
            self.safe_messagebox('error', "Error", f"Could not create template: {e}")

    # ------------------------------------------------------------------
    # Core detection methods
    # ------------------------------------------------------------------
    def load_credentials(self):
        raw = {}
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        raw[key.strip().lower()] = val.strip()
        except Exception:
            return None
        if not all(k in raw and raw[k] for k in ['username', 'password', 'login_url']):
            return None
        def csv_set(key):
            val = raw.get(key, '')
            return {v.strip().lower() for v in val.split(',') if v.strip()}
        config = dict(raw)
        config['extra_extensions'] = csv_set('extra_extensions')
        config['exclude_link_text_list'] = csv_set('exclude_link_text')
        try:
            config['min_columns'] = int(raw.get('min_columns') or 2)
        except ValueError:
            config['min_columns'] = 2
        return config

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
                    if h in ['studentid', 'id', 'student', 'recordid', 'name', 'record']:
                        id_idx = idx
                    if h in ['link', 'url', 'studentlink', 'recordlink', 'pageurl']:
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
            self.log('error', f"Failed writing log CSV: {e}")

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
            self.log('error', f"Failed updating validation: {e}")

    def find_segments_by_names(self, driver, names):
        """
        Find each segment container (RadDock) by its exact title text.
        Returns a list of (container_element, title) tuples.
        """
        segments = []
        for name in names:
            name = name.strip()
            if not name:
                continue
            # Try to find a RadDock whose title span contains the exact text (case-insensitive)
            xpath = f"//div[contains(@class, 'RadDock')]//span[contains(@class, 'title') and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{name.lower()}')]"
            title_els = driver.find_elements(By.XPATH, xpath)
            if title_els:
                # For each title element, climb up to the RadDock container
                for title_el in title_els:
                    container = title_el
                    while container and container.tag_name != 'body':
                        classes = container.get_attribute('class') or ''
                        if 'RadDock' in classes:
                            # Avoid duplicates
                            if not any(c[1] == name for c in segments):
                                segments.append((container, name))
                            break
                        container = container.find_element(By.XPATH, '..')
            else:
                # Fallback: search for any element containing the text, then climb
                fallback_xpath = f".//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{name.lower()}')]"
                fallback_els = driver.find_elements(By.XPATH, fallback_xpath)
                for el in fallback_els:
                    container = el
                    while container and container.tag_name != 'body':
                        classes = container.get_attribute('class') or ''
                        if 'RadDock' in classes:
                            if not any(c[1] == name for c in segments):
                                segments.append((container, name))
                            break
                        try:
                            container = container.find_element(By.XPATH, '..')
                        except:
                            break
        return segments

    def find_login_elements(self, driver, wait, config):
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
        return user_input, pass_input, login_btn

    def is_probable_file_link(self, el, config):
        try:
            text = el.text.strip()
            href = (el.get_attribute("href") or "").strip()
            onclick = (el.get_attribute("onclick") or "").strip()
            download_attr = el.get_attribute("download")
            exclude_words = DEFAULT_EXCLUDE_TEXT | config.get('exclude_link_text_list', set())
            if text.lower() in exclude_words:
                return False
            if not text and not download_attr:
                return False
            href_lower = href.lower()
            ext_from_href = os.path.splitext(urllib.parse.urlparse(href).path)[1].lower().lstrip('.')
            ext_from_text = os.path.splitext(text)[1].lower().lstrip('.')
            known_exts = KNOWN_FILE_EXTENSIONS | config.get('extra_extensions', set())
            looks_like_file = (ext_from_href in known_exts) or (ext_from_text in known_exts) or bool(download_attr)
            if looks_like_file:
                return True
            has_real_href = href_lower not in ('', '#', 'javascript:void(0)', 'javascript:void(0);')
            if has_real_href:
                path = urllib.parse.urlparse(href).path.lower()
                if any(hint in path for hint in DOWNLOAD_PATH_HINTS):
                    return True
                return False
            if onclick and any(k in onclick.lower() for k in ('download', 'file', 'open', 'view')):
                return True
            return False
        except Exception:
            return False

    def get_rows(self, driver, config, context=None):
        """Returns list of 'row' elements. If context is given, search only inside it."""
        if config.get('row_container_xpath'):
            xpath = config['row_container_xpath']
            # If we have a context, make the XPath relative to it
            if context and xpath.startswith('//'):
                xpath = '.' + xpath   # prepend '.'
            try:
                if context:
                    rows = context.find_elements(By.XPATH, xpath)
                else:
                    rows = driver.find_elements(By.XPATH, xpath)
                if rows:
                    return rows
            except Exception:
                pass
        min_columns = config.get('min_columns', 2)
        # Use a relative XPath that works both with and without a context
        # When context is given, we use './' prefix; otherwise '//' is fine.
        # NOTE: we exclude rows that themselves contain a nested qualifying <tr>,
        # so a wrapper row (nested tables) doesn't get matched *in addition to*
        # the real inner row - that double-match was causing the same file to be
        # collected twice (once correctly categorized via the inner row, once as
        # "Uncategorized" via the outer wrapper row, which has no category text
        # of its own).
        no_nested = f"not(.//tr[td and count(td) >= {min_columns} and .//a])"
        if context:
            xpath = f".//tr[td and count(td) >= {min_columns} and .//a and {no_nested}]"
        else:
            xpath = f"//tr[td and count(td) >= {min_columns} and .//a and {no_nested}]"
        try:
            if context:
                return context.find_elements(By.XPATH, xpath)
            else:
                return driver.find_elements(By.XPATH, xpath)
        except Exception:
            return []

    def get_category_for_row(self, row, config):
        category = ""
        # Try category_xpath or column index first
        if config.get('category_xpath'):
            try:
                text = row.find_element(By.XPATH, config['category_xpath']).text.strip()
                if text:
                    # Take first line if multi-line
                    category = text.split('\n')[0].strip()
            except Exception:
                pass
        if not category:
            col_index = config.get('category_column_index', 0)
            if col_index:
                try:
                    xpath = f"./td[{col_index}]"
                    text = row.find_element(By.XPATH, xpath).text.strip()
                    if text:
                        category = text.split('\n')[0].strip()
                except Exception:
                    pass
        # Fallback auto-detection
        if not category:
            try:
                tds = row.find_elements(By.XPATH, "./td")
                for td in tds:
                    text = td.text.strip()
                    if text and not td.find_elements(By.XPATH, ".//a"):
                        category = text.split('\n')[0].strip()
                        break
            except Exception:
                pass
        if not category:
            category = "Uncategorized"
        # Sanitize the category before returning
        return _sanitize(category)

    def get_links_for_row(self, row, config):
        try:
            if config.get('link_selector_xpath'):
                links = row.find_elements(By.XPATH, config['link_selector_xpath'])
            else:
                links = row.find_elements(By.XPATH, ".//a")
        except Exception:
            return []
        return [l for l in links if self.is_probable_file_link(l, config)]

    @staticmethod
    def _parse_csv_field(text):
        return [x.strip().lower() for x in (text or "").split(',') if x.strip()]

    def get_active_filters(self):
        def parse_kb(var):
            raw = var.get().strip()
            if not raw:
                return None
            try:
                return int(float(raw) * 1024)
            except ValueError:
                return None
        return {
            'cat_include': self._parse_csv_field(self.filter_cat_include.get()),
            'cat_exclude': self._parse_csv_field(self.filter_cat_exclude.get()),
            'type_include': self._parse_csv_field(self.filter_type_include.get()),
            'name_include': self._parse_csv_field(self.filter_name_include.get()),
            'name_exclude': self._parse_csv_field(self.filter_name_exclude.get()),
            'min_bytes': parse_kb(self.filter_min_kb),
            'max_bytes': parse_kb(self.filter_max_kb),
        }

    def item_passes_prefilters(self, item, filters):
        if not filters:
            return True
        category = (item.get('category') or "").lower()
        name = item.get('link_text') or ""
        name_lower = name.lower()
        href = item.get('href') or ""
        if filters['cat_include'] and not any(k in category for k in filters['cat_include']):
            return False
        if filters['cat_exclude'] and any(k in category for k in filters['cat_exclude']):
            return False
        if filters['type_include']:
            ext = os.path.splitext(name)[1].lower().lstrip('.')
            if not ext:
                ext = os.path.splitext(urllib.parse.urlparse(href).path)[1].lower().lstrip('.')
            if ext not in filters['type_include']:
                return False
        if filters['name_include'] and not any(k in name_lower for k in filters['name_include']):
            return False
        if filters['name_exclude'] and any(k in name_lower for k in filters['name_exclude']):
            return False
        return True

    # ------------------------------------------------------------------
    # Core collection logic (no fallback when segments given)
    # ------------------------------------------------------------------
    def get_all_items(self, driver, config, filters):
        segment_names_raw = config.get('segment_names', '')
        segment_names = [s.strip() for s in segment_names_raw.split(',') if s.strip()] if segment_names_raw else []
        default_segment = config.get('default_segment', 'Uncategorized')
        try:
            config['category_column_index'] = int(self.category_column_index.get())
        except:
            config['category_column_index'] = 2

        items = []
        found_segments = []
        missing_exists = False

        if segment_names:
            for seg_name in segment_names:
                containers = self.find_segments_by_names(driver, [seg_name])
                if containers:
                    container, seg_title = containers[0]
                    seg_items = self.collect_file_items(
                        driver, config, filters,
                        context=container,
                        category_prefix=seg_title
                    )
                    if seg_items:
                        items.extend(seg_items)
                        found_segments.append(seg_title)
                        self.log('info', f"Found {len(seg_items)} file(s) in segment '{seg_title}'")
                    else:
                        self.log('warning', f"Segment '{seg_title}' found but has no file rows – skipping.")
                else:
                    missing_exists = True
                    self.log('warning', f"Segment '{seg_name}' not found – will collect whole page without segment folder.")

            # If any missing segment, collect whole page, excluding duplicates
            if missing_exists:
                self.log('info', "Collecting files from whole page for missing segments, excluding duplicates.")
                all_page_items = self.collect_file_items(
                    driver, config, filters,
                    context=None,
                    category_prefix=""   # no segment prefix → saved under studentId/category
                )
                # Build sets of identifiers from existing items
                existing_hrefs = {self._normalize_href(item['href']) for item in items if item.get('href')}
                existing_keys = {(item['subcategory'], item['link_text']) for item in items}
                for item in all_page_items:
                    norm_href = self._normalize_href(item['href'])
                    key = (item['subcategory'], item['link_text'])
                    # Skip if href or filename+subcategory already exists
                    if norm_href not in existing_hrefs and key not in existing_keys:
                        item['segment'] = ""   # empty → no segment folder
                        items.append(item)
                        existing_hrefs.add(norm_href)
                        existing_keys.add(key)
                    else:
                        self.log('debug', f"Skipping duplicate: {item['link_text']} in {item['subcategory']}")
                self.log('info', f"Added {len(all_page_items) - (len(all_page_items) - len(items) + len(found_segments))} new files from whole page.")
        else:
            # No segment names: scan whole page with default segment
            self.log('info', f"No segment names – scanning whole page, using default segment '{default_segment}'.")
            items = self.collect_file_items(
                driver, config, filters,
                context=None,
                category_prefix=default_segment
            )

        return items

    def collect_file_items(self, driver, config, filters=None, context=None, category_prefix=""):
        items = []
        rows = self.get_rows(driver, config, context)
        if rows:
            for row in rows:
                category = self.get_category_for_row(row, config)
                combined = f"{category_prefix} - {category}" if category_prefix and category != "Uncategorized" else (category_prefix if category_prefix else category)
                for link_el in self.get_links_for_row(row, config):
                    items.append({
                        'category': combined,
                        'segment': category_prefix,
                        'subcategory': category,
                        'link_text': link_el.text.strip() or "file",
                        'href': link_el.get_attribute("href"),
                        'element': link_el,
                    })
        # Fallback only when no context (whole‑page mode)
        if not items and context is None:
            try:
                all_links = driver.find_elements(By.XPATH, "//a")
            except Exception:
                all_links = []
            for link_el in all_links:
                if self.is_probable_file_link(link_el, config):
                    category = "Uncategorized"
                    combined = category_prefix if category_prefix else category
                    items.append({
                        'category': combined,
                        'segment': category_prefix,
                        'subcategory': category,
                        'link_text': link_el.text.strip() or "file",
                        'href': link_el.get_attribute("href"),
                        'element': link_el,
                    })

        # Defensive dedup: if the same href got matched more than once within
        # this same segment/context (e.g. from unusual/nested markup a site
        # might use), keep only one copy - preferring whichever copy has a
        # real (non-"Uncategorized") subcategory, since that's the accurate one.
        if items:
            by_href = {}
            for it in items:
                key = self._normalize_href(it['href']) if it.get('href') else None
                if key is None:
                    # No href to key on (shouldn't normally happen) - keep as-is.
                    by_href.setdefault(id(it), it)
                    continue
                existing = by_href.get(key)
                if existing is None:
                    by_href[key] = it
                elif existing['subcategory'] == 'Uncategorized' and it['subcategory'] != 'Uncategorized':
                    # Replace the Uncategorized duplicate with the properly categorized one.
                    by_href[key] = it
                # else: keep the first one we found; drop this duplicate.
            items = list(by_href.values())

        if filters:
            items = [i for i in items if self.item_passes_prefilters(i, filters)]
        return items

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
        return False

    def set_download_behavior(self, driver, folder_path):
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": folder_path})
        except Exception:
            pass
        try:
            driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": folder_path, "eventsEnabled": True})
        except Exception:
            pass

    def force_download_via_fetch(self, driver, file_url, folder_path, file_name, min_bytes=None, max_bytes=None):
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
            if not result or result.startswith('ERROR'):
                return 'failed'
            header, encoded = result.split(',', 1)
            import base64
            file_bytes = base64.b64decode(encoded)
            size = len(file_bytes)
            if min_bytes is not None and size < min_bytes:
                return 'skipped_size'
            if max_bytes is not None and size > max_bytes:
                return 'skipped_size'
            mime_type = "application/octet-stream"
            if header.startswith("data:") and ";" in header:
                mime_type = header[5:header.index(";")]
            safe_name = _sanitize(file_name.strip())
            if not os.path.splitext(safe_name)[1]:
                ext = MIME_TO_EXT.get(mime_type.lower(), "")
                safe_name += ext
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
        except Exception:
            return 'failed'

    def close_extra_windows(self, driver, keep_handle):
        for handle in list(driver.window_handles):
            if handle != keep_handle:
                try:
                    driver.switch_to.window(handle)
                    driver.close()
                except Exception:
                    pass
        driver.switch_to.window(keep_handle)

    def launch_chrome_driver(self, chrome_options):
        try:
            return webdriver.Chrome(options=chrome_options)
        except Exception:
            path = ChromeDriverManager().install()
            return webdriver.Chrome(service=Service(path), options=chrome_options)

    def build_chrome_options(self, download_dir_abs):
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
        return chrome_options

    def login(self, driver, wait, config):
        driver.get(config['login_url'])
        user_input, pass_input, login_btn = self.find_login_elements(driver, wait, config)
        user_input.clear()
        user_input.send_keys(config['username'])
        pass_input.clear()
        pass_input.send_keys(config['password'])
        login_btn.click()
        time.sleep(3)

    # ------------------------------------------------------------------
    # generate_documents mode: click-to-generate dropdown + doc buttons
    # ------------------------------------------------------------------
    GENERATE_PREFIX_RE = re.compile(r'^generate\s+', re.IGNORECASE)

    def get_dropdown_button(self, driver, wait, config):
        """Returns the multi-program switcher button, or None if it doesn't
        exist on this page (this switcher only renders when a student has
        more than one program -- single-program students won't have it, and
        that's not an error)."""
        xpath = config.get('dropdown_button_xpath') or (
            "//button[.//span[contains(@class,'MuiLoadingButton-label')]]"
            "[.//*[@data-testid='ArrowDropDownRoundedIcon']]"
        )
        try:
            return WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        except Exception:
            return None

    def _wait_for_no_backdrop(self, driver, timeout=3):
        """MUI popovers/menus leave a backdrop element that intercepts clicks
        while it's still animating closed. Wait for it to actually go away
        before clicking anything underneath it -- fixes
        ElementClickInterceptedException when re-opening the dropdown right
        after closing it."""
        try:
            WebDriverWait(driver, timeout).until(
                EC.invisibility_of_element_located(
                    (By.XPATH, "//div[contains(@class,'MuiBackdrop-root') and contains(@class,'MuiModal-backdrop')]")
                )
            )
        except Exception:
            pass
            
    def get_current_program_name(self, driver, config):
        """Reads the program name straight off the page (the heading that
        links to /academics/programs/...), independent of whether the
        multi-program switcher exists. Used as the fallback for
        single-program students."""
        xpath = config.get('current_program_xpath') or (
            "//a[contains(@href,'/academics/programs/')]/h2"
        )
        try:
            for el in driver.find_elements(By.XPATH, xpath):
                text = (el.get_attribute('textContent') or '').strip()
                if text:
                    return text
        except Exception:
            pass
        return None

    def _wait_for_no_backdrop(self, driver, timeout=3):
        """MUI popovers/menus leave a backdrop element that intercepts clicks
        while it's still animating closed. Wait for it to actually go away
        before clicking anything underneath it -- fixes
        ElementClickInterceptedException when re-opening the dropdown right
        after closing it."""
        try:
            WebDriverWait(driver, timeout).until(
                EC.invisibility_of_element_located(
                    (By.XPATH, "//div[contains(@class,'MuiBackdrop-root') and contains(@class,'MuiModal-backdrop')]")
                )
            )
        except Exception:
            pass
        
    def get_dropdown_option_elements(self, driver, config):
        xpath = config.get('dropdown_option_xpath') or (
            "//li[@role='option' or @role='menuitem' or contains(@class,'MuiMenuItem-root') "
            "or contains(@class,'MuiAutocomplete-option')]"
        )
        try:
            return driver.find_elements(By.XPATH, xpath)
        except Exception:
            return []

    def get_dropdown_program_list(self, driver, wait, config):
        """Opens the multi-program switcher (if present) and reads every
        option's text, in order. If there's no switcher -- i.e. the student
        only has one program -- falls back to reading that single program's
        name directly off the page. Filters out hidden/ghost DOM nodes (MUI
        sometimes leaves an extra invisible copy mid-animation) by checking
        is_displayed() rather than deduping by text, since two genuinely
        different enrollments can share the exact same program name."""
        btn = self.get_dropdown_button(driver, wait, config)
        if btn is None:
            current = self.get_current_program_name(driver, config)
            return [current] if current else []

        btn.click()
        time.sleep(0.8)
        names = []
        for opt in self.get_dropdown_option_elements(driver, config):
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
            current = self.get_current_program_name(driver, config)
            return [current] if current else []
        return names

    def select_dropdown_program(self, driver, wait, program_text, config, occurrence=1):
        """Selects the Nth (1-indexed) option whose text matches program_text.
        Needed because two different program enrollments can share the exact
        same displayed name -- matching by text alone would always click the
        first one."""
        btn = self.get_dropdown_button(driver, wait, config)
        if btn is None:
            current = self.get_current_program_name(driver, config)
            return current is not None

        self._wait_for_no_backdrop(driver)
        try:
            btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.8)

        match_count = 0
        target = None
        for opt in self.get_dropdown_option_elements(driver, config):
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

    def get_generate_buttons(self, driver, config):
        """Finds the 'Generate ___' buttons. Waits briefly for at least one
        to appear first, since this can run right after a fresh navigation
        or a program switch, before React has finished rendering the
        Generate Documents section."""
        xpath = config.get('generate_buttons_xpath') or (
            "//button[.//span[starts-with(normalize-space(.),'Generate')]]"
        )
        try:
            WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.XPATH, xpath)))
        except Exception:
            pass
        try:
            return driver.find_elements(By.XPATH, xpath)
        except Exception:
            return []

    def generate_button_label(self, btn):
        text = (btn.get_attribute('textContent') or '').strip()
        return self.GENERATE_PREFIX_RE.sub('', text).strip() or "Document"

    @staticmethod
    def program_folder_name(program_text):
        """'EMT - Basic Training Program-MARCH 8WK AM' ->
        'EMT - Basic Training Program' (first two '-'-separated segments)."""
        parts = [p.strip() for p in (program_text or "").split('-') if p.strip()]
        if len(parts) >= 2:
            return f"{parts[0]} - {parts[1]}"
        return parts[0] if parts else (program_text or "Program").strip()

    def find_modal(self, driver, config):
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

    def extract_pdf_source_url(self, driver, modal):
        """Strategy A: the PDF is rendered via <embed>/<iframe>/<object> --
        read its src/data attribute directly rather than trying to click
        the browser's own native PDF-viewer toolbar (which isn't a real,
        clickable page element as far as Selenium is concerned)."""
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
        # Escape doesn't always close MUI dialogs (some disable it) -- fall
        # back to clicking a visible "Close" button if one is still open.
        try:
            for b in driver.find_elements(By.XPATH, "//button[contains(translate(., 'CLOSE', 'close'), 'close')]"):
                if b.is_displayed():
                    driver.execute_script("arguments[0].click();", b)
                    break
        except Exception:
            pass
        time.sleep(0.4)

    def generate_and_capture_document(self, driver, config, btn, dest_folder, doc_label):
        """Clicks a 'Generate ___' button, waits for its preview modal (or a
        new tab, if the site opens one instead), locates the embedded PDF's
        real URL, fetches its bytes through the browser's own authenticated
        context, and closes the modal/tab afterward.
        Returns (status, comment) where status is 'downloaded' / 'duplicate' / 'failed'."""
        main_window = driver.current_window_handle
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
            modal_wait = WebDriverWait(driver, 20)
            modal = modal_wait.until(lambda d: self.find_modal(d, config))
            pdf_url = self.extract_pdf_source_url(driver, modal)
        except Exception as e:
            self.log('warning', f"No modal/embedded PDF detected after clicking '{doc_label}': {e}")

        if not pdf_url:
            try:
                current_url = driver.current_url
                if current_url and ('.pdf' in current_url.lower() or current_url.lower().startswith('blob:')):
                    pdf_url = current_url
            except Exception:
                pass

        if not pdf_url:
            self.log('error', f"Could not locate a PDF source for '{doc_label}'. The modal/page "
                               f"structure may differ from what this tool expects -- try setting "
                               f"'modal_xpath' in the credentials file, or check automation_debug.log.")
            try:
                body_len = driver.execute_script("return document.body.innerHTML.length;")
                self.log('debug', f"[{doc_label}] page body HTML length at failure time: {body_len}")
            except Exception:
                pass
            self._close_modal_or_window(driver, main_window, switched_window)
            return 'failed', 'Could not locate generated PDF (embed/iframe src not found)'

        file_name = _sanitize(doc_label) + '.pdf'
        result = self.force_download_via_fetch(driver, pdf_url, dest_folder, file_name)

        self._close_modal_or_window(driver, main_window, switched_window)

        comment = {
            'downloaded': 'Success',
            'duplicate': 'Duplicate content already on disk',
            'failed': 'Could not fetch generated PDF bytes',
        }.get(result, 'Unknown result')
        return result, comment

    def process_generate_documents_for_student(self, driver, wait, config, filters, student_id, student_root_folder):
        """MyPath-style flow: enumerate every option in the program dropdown,
        then for each one click every 'Generate ___' button and capture the
        resulting PDF. All documents for a student land flat under:
        {student_root_folder}/Profile Docs/{program name} - {doc label}[ N].pdf
        -- there is no per-program subfolder, since a student's programs
        frequently share the exact same display name and a subfolder per
        program would just recreate the same collision problem one level up.
        The program name is always part of the filename so documents from
        different programs never collide; a trailing " N" is added only
        when the program name itself repeats across occurrences.
        Any single document that fails or isn't available (button missing,
        PDF never renders, etc.) is logged and skipped -- it never stops the
        rest of that student's documents from being attempted.
        Returns the list of execution-log rows for this student."""
        current_batch_logs = []
        try:
            programs = self.get_dropdown_program_list(driver, wait, config)
        except Exception as e:
            self.log('error', f"Could not read program dropdown for {student_id}: {e}")
            return current_batch_logs

        self.log('info', f"Found {len(programs)} program(s) for {student_id}: {programs}")

        name_counts = {}
        for p in programs:
            name_counts[p] = name_counts.get(p, 0) + 1
        seen_occurrence = {}

        dest_folder = os.path.join(student_root_folder, "Profile Docs")

        first_pass_buttons = self.get_generate_buttons(driver, config)
        self.current_student_files_total = len(programs) * max(len(first_pass_buttons), 1)
        self.refresh_stats_panel()

        for program_text in programs:
            if self.abort_requested:
                break

            seen_occurrence[program_text] = seen_occurrence.get(program_text, 0) + 1
            occurrence = seen_occurrence[program_text]
            needs_suffix = name_counts[program_text] > 1

            if len(programs) > 1:
                if not self.select_dropdown_program(driver, wait, program_text, config, occurrence=occurrence):
                    self.log('warning', f"Could not select program '{program_text}' "
                                         f"(occurrence {occurrence}) for {student_id}; skipping.")
                    continue
                time.sleep(1.0)

            folder_name = self.program_folder_name(program_text)

            num_buttons = len(self.get_generate_buttons(driver, config))
            for b_idx in range(num_buttons):
                if self.abort_requested:
                    break
                try:
                    current_buttons = self.get_generate_buttons(driver, config)
                    if b_idx >= len(current_buttons):
                        msg = f"Generate button [{b_idx}] not available for '{program_text}' ({student_id})."
                        self.log('warning', msg + " Continuing to next document.")
                        current_batch_logs.append({
                            'studentId': student_id,
                            'fileName': f"{folder_name} - (missing button {b_idx})",
                            'fileType': 'N/A', 'directory': dest_folder, 'action': 'skipped',
                            'actionComment': msg, 'validation': 'not available',
                        })
                        continue
                    btn = current_buttons[b_idx]
                    base_label = self.generate_button_label(btn)
                    doc_label = f"{folder_name} - {base_label}"
                    if needs_suffix:
                        doc_label = f"{doc_label} {occurrence}"

                    # Filters are checked BEFORE generating, since generating
                    # is the expensive/slow step -- no point creating a
                    # document the filters would reject anyway.
                    pseudo_item = {'category': folder_name, 'link_text': doc_label + '.pdf', 'href': ''}
                    if filters and not self.item_passes_prefilters(pseudo_item, filters):
                        self.log('info', f"Skipping '{doc_label}' for '{program_text}' (filtered out).")
                        continue

                    self.lbl_status.config(text=f"Generating '{doc_label}' for {student_id}...")
                    self.root.update()

                    result, comment = self.generate_and_capture_document(driver, config, btn, dest_folder, doc_label)

                    if result in ('downloaded', 'duplicate'):
                        self.current_student_files_done += 1
                        self.total_files_downloaded += 1
                    else:
                        self.log('warning', f"'{doc_label}' unavailable for {student_id}: {comment}. Continuing.")
                    self.refresh_stats_panel()

                    status_map = {'downloaded': 'downloaded', 'duplicate': 'downloaded', 'failed': 'failed'}
                    action_status = status_map.get(result, 'failed')
                    current_batch_logs.append({
                        'studentId': student_id, 'fileName': f"{doc_label}.pdf", 'fileType': 'PDF',
                        'directory': dest_folder, 'action': action_status,
                        'actionComment': comment,
                        'validation': 'file downloaded' if action_status == 'downloaded' else action_status,
                    })
                except Exception as e:
                    self.log('error', f"Error generating document [{b_idx}] for program '{program_text}', "
                                       f"student {student_id}: {e}. Continuing to next document.")
                    current_batch_logs.append({
                        'studentId': student_id, 'fileName': f"{folder_name} - Error_Item_{b_idx}", 'fileType': 'N/A',
                        'directory': dest_folder, 'action': 'error',
                        'actionComment': str(e)[:200], 'validation': 'traversed'
                    })

        return current_batch_logs

    # ------------------------------------------------------------------
    # Preview Detection
    # ------------------------------------------------------------------
    def preview_detection(self):
        if self.is_running:
            return
        if not self.config_file or not self.test_url.get().strip():
            self.safe_messagebox('error', "Preview Error", "Load a credentials file and enter a Test URL first.")
            return

        config = self.load_credentials()
        if not config:
            self.safe_messagebox('error', "Preview Error", "Credentials file is missing required fields.")
            return

        gui_segment_names = self.segment_names.get().strip()
        if gui_segment_names:
            config['segment_names'] = gui_segment_names
        config['default_segment'] = self.default_segment.get().strip() or "Uncategorized"
        try:
            config['category_column_index'] = int(self.category_column_index.get())
        except:
            config['category_column_index'] = 2

        self.root.after(0, lambda: self.set_buttons_running(True))
        self.lbl_status.config(text="Status: Running preview detection...")
        driver = None
        try:
            chrome_options = self.build_chrome_options(os.path.abspath(self.download_dir or "."))
            driver = self.launch_chrome_driver(chrome_options)
            driver.set_script_timeout(60)
            wait = WebDriverWait(driver, 15)
            self.login(driver, wait, config)

            driver.get(self.test_url.get().strip())
            time.sleep(2)

            interaction_mode = (config.get('interaction_mode') or 'static_links').strip().lower()

            if interaction_mode == 'generate_documents':
                try:
                    programs = self.get_dropdown_program_list(driver, wait, config)
                except Exception as e:
                    programs = []
                    self.log('error', f"Could not read program dropdown: {e}")
                gen_buttons = self.get_generate_buttons(driver, config)
                doc_labels = [self.generate_button_label(b) for b in gen_buttons]

                if not programs:
                    msg = ("No programs were found in the dropdown.\n\nCheck 'dropdown_button_xpath' / "
                           "'dropdown_option_xpath' overrides in the credentials file, or the debug log.")
                else:
                    sample = "\n".join(
                        f"  • {p}  →  Profile Docs/{self.program_folder_name(p)}/" for p in programs[:15]
                    )
                    more = f"\n  ... and {len(programs) - 15} more" if len(programs) > 15 else ""
                    docs_line = ", ".join(doc_labels) if doc_labels else "(none found -- check 'generate_buttons_xpath')"
                    total_est = len(programs) * len(doc_labels)
                    msg = (f"Found {len(programs)} program(s), and {len(doc_labels)} 'Generate ___' "
                           f"document type(s) per program: {docs_line}\n\n"
                           f"Estimated total documents for this student: ~{total_est}\n\n"
                           f"Programs (destination folder):\n{sample}{more}\n\n"
                           f"Note: preview does not actually generate documents, to avoid creating "
                           f"unnecessary files on the target system.")
                self.safe_messagebox('info', "Preview Detection Result", msg)
                self.lbl_status.config(text="Status: Preview complete.")
                return

            filters = self.get_active_filters()
            all_items = self.get_all_items(driver, config, None)
            matched_items = [i for i in all_items if self.item_passes_prefilters(i, filters)]

            if not all_items:
                msg = "No file links were detected on this page at all.\n\nTry adjusting the Category Column Index or XPath overrides."
            else:
                sample_lines = []
                for item in matched_items[:15]:
                    seg = item.get('segment', '')
                    sub = item.get('subcategory', 'Uncategorized')
                    if seg:
                        folder = f"{seg}/{sub}"
                    else:
                        folder = sub
                    sample_lines.append(f"  • [{item['category']}] → {folder}/{item['link_text']}")
                sample = "\n".join(sample_lines)
                more = f"\n  ... and {len(matched_items) - 15} more" if len(matched_items) > 15 else ""
                msg = f"Detected {len(all_items)} file(s).\n\n"
                if filters:
                    msg += f"Filters applied: {len(matched_items)} files match.\n\n"
                msg += f"Sample (folder structure):\n{sample}{more}"

            self.safe_messagebox('info', "Preview Detection Result", msg)
            self.lbl_status.config(text="Status: Preview complete.")
        except Exception as e:
            tb = traceback.format_exc()
            self.log('error', f"Preview detection failed:\n{tb}")
            self.safe_messagebox('error', "Preview Error", str(e))
            self.lbl_status.config(text="Status: Preview failed.")
        finally:
            if driver:
                driver.quit()
            self.root.after(0, lambda: self.set_buttons_running(False))

    # ------------------------------------------------------------------
    # Main download routine
    # ------------------------------------------------------------------
    def start_download(self):
        if self.is_running:
            return
        if not self.config_file or not self.links_file or not self.download_dir:
            self.safe_messagebox('error', "Selection Error", "Please fill in all paths.")
            return

        self.setup_text_logger()
        config = self.load_credentials()
        students = self.read_links_csv()

        if not config or not students:
            self.safe_messagebox('error', "Error", "Invalid configuration or empty/invalid links CSV.")
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
        chrome_options = self.build_chrome_options(download_dir_abs)
        filters = self.get_active_filters()
        interaction_mode = (config.get('interaction_mode') or 'static_links').strip().lower()

        gui_segment_names = self.segment_names.get().strip()
        if gui_segment_names:
            config['segment_names'] = gui_segment_names
        config['default_segment'] = self.default_segment.get().strip() or "Uncategorized"
        try:
            config['category_column_index'] = int(self.category_column_index.get())
        except:
            config['category_column_index'] = 2

        driver = None
        aborted = False
        try:
            driver = self.launch_chrome_driver(chrome_options)
            driver.set_script_timeout(60)
            wait = WebDriverWait(driver, 15)

            self.lbl_status.config(text="Status: Logging in...")
            self.root.update()
            self.login(driver, wait, config)

            self.progress["maximum"] = len(students)

            for idx, student in enumerate(students):
                if self.abort_requested:
                    aborted = True
                    break

                raw_id = student['studentId'].strip()
                student_id = raw_id.split('.')[0] if '.' in raw_id else raw_id

                if (not student_id or len(student_id) < 1 or
                    student_id.lower() in ['studentid', 'id', 'student', 'recordid', 'record']):
                    self.log('info', f"Skipping row index/header artifact: '{raw_id}'")
                    continue

                link = student['link']
                student_root_folder = os.path.join(download_dir_abs, student_id)

                self.current_student_id = student_id
                self.current_student_files_done = 0
                self.current_student_files_total = 0
                self.refresh_stats_panel()

                self.lbl_status.config(text=f"Scanning: {student_id}...")
                self.progress["value"] = idx + 1
                self.root.update()

                driver.get(link)
                time.sleep(1.5)

                if interaction_mode == 'generate_documents':
                    current_batch_logs = self.process_generate_documents_for_student(
                        driver, wait, config, filters, student_id, student_root_folder
                    )
                    if current_batch_logs:
                        self.write_log_csv(current_batch_logs)
                    self.completed_students_file_counts.append(self.current_student_files_done)
                    self.students_traversed += 1
                    self.refresh_stats_panel()
                    continue

                items_to_download = self.get_all_items(driver, config, filters)

                self.current_student_files_total = len(items_to_download)
                self.refresh_stats_panel()
                current_batch_logs = []
                seen_names = {}

                for f_idx, item in enumerate(items_to_download):
                    if self.abort_requested:
                        aborted = True
                        break

                    self.lbl_status.config(text=f"Downloading [{f_idx + 1}/{len(items_to_download)}] for {student_id}...")
                    self.root.update()

                    seg = item.get('segment', '')
                    sub = item.get('subcategory', 'Uncategorized')
                    safe_seg = _sanitize(seg) if seg else ''
                    safe_sub = _sanitize(sub)
                    if safe_seg:
                        student_folder = os.path.join(student_root_folder, safe_seg, safe_sub)
                    else:
                        student_folder = os.path.join(student_root_folder, safe_sub)
                    os.makedirs(student_folder, exist_ok=True)

                    file_name = item['link_text']
                    file_ext = os.path.splitext(file_name)[1].replace('.', '').upper() or "UNKNOWN"

                    safe_check = _sanitize(file_name)
                    seen_names[file_name] = seen_names.get(file_name, 0) + 1
                    occurrence = seen_names[file_name]

                    if self.allow_duplicates.get() and occurrence > 1:
                        _base, _ext = os.path.splitext(safe_check)
                        effective_disk_name = f"{_base}__{occurrence}{_ext}"
                    else:
                        effective_disk_name = safe_check

                    result = self.force_download_via_fetch(
                        driver, item['href'], student_folder, effective_disk_name,
                        min_bytes=filters['min_bytes'], max_bytes=filters['max_bytes']
                    )

                    if result in ('downloaded', 'duplicate'):
                        self.current_student_files_done += 1
                        self.total_files_downloaded += 1
                    elif result == 'skipped_size':
                        self.log('info', f"Skipped '{file_name}' (outside configured size filter).")
                    else:
                        self.log('error', f"File extraction failed for {file_name} via context URL.")

                    self.refresh_stats_panel()
                    status_map = {
                        'downloaded': ('downloaded', 'Success'),
                        'duplicate': ('downloaded', 'Duplicate content already on disk'),
                        'skipped_size': ('skipped', 'Outside configured min/max size filter'),
                        'failed': ('failed', 'Extraction failed'),
                    }
                    action_status, action_comment = status_map[result]
                    current_batch_logs.append({
                        'studentId': student_id, 'fileName': file_name, 'fileType': file_ext,
                        'directory': student_folder, 'action': action_status,
                        'actionComment': action_comment,
                        'validation': 'file downloaded' if action_status == 'downloaded' else action_status
                    })

                if current_batch_logs:
                    self.write_log_csv(current_batch_logs)

                self.completed_students_file_counts.append(self.current_student_files_done)
                self.students_traversed += 1
                self.refresh_stats_panel()

            self.lbl_status.config(text="Status: Process Completed!" if not aborted else "Status: Aborted")

        except Exception as e:
            tb = traceback.format_exc()
            self.log('error', f"Download process failed:\n{tb}")
            self.safe_messagebox('error', "Critical Error", str(e))
        finally:
            if driver:
                driver.quit()
            _remove_empty_dirs(download_dir_abs)
            self.is_running = False
            self.current_phase = None
            self.download_end_time = datetime.datetime.now()
            self.root.after(0, self.refresh_stats_panel)
            self.root.after(0, lambda: self.set_buttons_running(False))

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def verify_downloads(self):
        if self.is_running:
            return
        log_file_path = os.path.join(self.download_dir, "automation_execution_log.csv")
        if not os.path.isfile(log_file_path):
            return

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
        except Exception as e:
            self.log('error', f"Verification read error: {e}")

        verified = 0
        for path, records in expected_records.items():
            for sid, files in records.items():
                if os.path.exists(path):
                    actual = os.listdir(path)
                    for f in files:
                        sf = _sanitize(f)
                        if sf in actual:
                            verified += 1
                            self.update_log_validation(sid, f, "Verified Match")

        self.is_running = False
        self.current_phase = None
        self.verify_end_time = datetime.datetime.now()
        self.root.after(0, self.refresh_stats_panel)
        self.root.after(0, lambda: self.set_buttons_running(False))
        self.safe_messagebox('info', "Verification Complete", f"Verified matches: {verified}")
        self.log('info', f"Verification complete: {verified} matches found.")


if __name__ == "__main__":
    root = tk.Tk()
    app = FileDownloaderApp(root)
    root.mainloop()
