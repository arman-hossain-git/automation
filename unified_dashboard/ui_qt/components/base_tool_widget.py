"""
ui_qt/components/base_tool_widget.py
====================================
Reusable PySide6 base component widget for tool automation panels.
Provides:
  - Header with title, description, and dynamic status badge
  - Tab 1: Configuration inputs with scroll area + task queue management table
  - Tab 2: Live activity panel with full 12-item live KPI grid, telemetry graph,
           and high-performance console log
  - Pinned control bar with Start / Pause / Stop / Discard buttons and progress bar
  - Multi-task sequential execution with thread-safe Qt Worker signals and InterTaskDialog
  - Live 1-second QTimer clock updating Elapsed, Current Time, and ETA
"""

import os
import sys
import glob
import time
import datetime
import subprocess
from typing import Dict, Any, List, Optional, Callable

from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QScrollArea, QFrame, QFileDialog, QMessageBox, QSplitter, QGridLayout
)
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat, QFont

from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BG_INPUT, BG_CONSOLE, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_DANGER, ACCENT_INFO
)
from ui_qt.components.badge import StatusBadge, MetricCard
from ui_qt.components.time_series_graph import TimeSeriesTelemetryGraph
from ui_qt.components.inter_task_dialog import InterTaskDialog
from core.utils import format_timedelta, format_bytes, format_speed_bps, format_time_remaining
from core.settings_manager import settings
from core.activity_logger import ActivityLogger


class QueueTask:
    """Represents a single queued task item."""
    def __init__(self, task_id: str, label: str, inputs: dict):
        self.id = task_id
        self.label = label
        self.inputs = inputs
        self.status = "Pending"  # Pending, In Progress, Completed, Failed, Discarded, Aborted
        self.verified = False
        self.result = {}


class AutomationWorker(QThread):
    """Background worker thread bridging Python automation logic to Qt UI via Signals."""
    sig_log = Signal(str, str)             # message, level
    sig_progress = Signal(int, int)        # current, total
    sig_status = Signal(str)               # status text
    sig_kpi = Signal(dict)                 # kpi dict
    sig_telemetry = Signal(float, int, str)# speed_bps, cumulative_bytes, file_name
    sig_finished = Signal(bool, str)       # success, error_message

    def __init__(self, target_callable: Callable, task_inputs: dict, **kwargs):
        super().__init__()
        self.target_callable = target_callable
        self.task_inputs = task_inputs
        self.kwargs = kwargs
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True

    def run(self):
        try:
            # We inject thread-safe emitter callbacks into kwargs
            def log_callback(msg, level="INFO"):
                self.sig_log.emit(str(msg), str(level))

            def progress_callback(cur, tot):
                self.sig_progress.emit(int(cur), int(tot))

            def telemetry_callback(speed, cum_bytes, fn=""):
                self.sig_telemetry.emit(float(speed), int(cum_bytes), str(fn))

            def kpi_callback(kpi_dict):
                self.sig_kpi.emit(kpi_dict)

            extra = {
                "log_callback": log_callback,
                "progress_callback": progress_callback,
                "telemetry_callback": telemetry_callback,
                "kpi_callback": kpi_callback,
                "is_aborted": lambda: self._is_aborted,
            }
            extra.update(self.kwargs)

            # Call the user function
            res = self.target_callable(self.task_inputs, **extra)
            self.sig_finished.emit(True, "")
        except Exception as e:
            import traceback
            # Python 3.14: traceback.format_exc() can itself raise
            # AttributeError('NoneType' has no attribute 'partition') when
            # the frame source cannot be resolved. Use a layered fallback.
            try:
                err = traceback.format_exc()
            except Exception:
                try:
                    err = f"{type(e).__name__}: {e}"
                except Exception:
                    err = "<unknown error — traceback formatter failed>"
            self.sig_log.emit(f"Critical Exception: {e}", "ERROR")
            self.sig_finished.emit(False, str(e))


class BaseToolWidget(QWidget):
    """
    Reusable Base Widget for automation panels.

    Activity Log tab includes a full 12-item live KPI grid matching the legacy
    Tkinter BaseToolFrame's 'Live Progress & Performance KPIs' panel, plus a
    1-second QTimer clock and the standard telemetry graph + console.
    """

    def __init__(self, parent=None, title: str = "Tool Automation", description: str = ""):
        super().__init__(parent)
        self.title_text = title
        self.description_text = description

        # ── State ──────────────────────────────────────────────────────
        self.queue: List[QueueTask] = []
        self.current_task_idx = -1
        self.worker: Optional[AutomationWorker] = None
        self.is_running = False
        self.is_paused = False

        # ── Metric State (mirrors BaseToolFrame) ───────────────────────
        self.total_students = 0
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_category = "-"
        self.files_done = 0
        self.files_total = 0
        self.total_bytes = 0
        self.bytes_done = 0
        self.current_file_name = "-"
        self.current_file_size = 0
        self.transfer_rate_bps = 0.0
        self.speed_history: List[tuple] = []   # (timestamp, cumulative_bytes)
        self.rolling_speed_bps = 0.0
        self.item_speed_history: List[tuple] = []  # (timestamp, cumulative_items)
        self.rolling_item_rate_per_min = 0.0
        self.students_base_offset: Optional[int] = None
        self.files_base_offset: Optional[int] = None
        self.bytes_base_offset: Optional[int] = None
        self.active_work_start_time: Optional[datetime.datetime] = None
        self.errors_count = 0

        # Per-student file tracking (for Files/Tasks Processed KPI)
        self.student_files_done = 0    # files done for the current student
        self.student_files_total = 0   # total files for the current student

        # Upload mode: global cumulative files uploaded & total files in storage
        self.total_files_uploaded = 0  # global count of files uploaded so far
        # tool_mode: "upload" when total_bytes > 0 (upload pre-scans), else "download"
        self.tool_mode: str = ""       # set to "upload" or "download" when first totals arrive

        # Simple progress counters (kept for backward-compat with update_progress)
        self.items_processed = 0
        self.total_items = 0

        self.start_time: Optional[datetime.datetime] = None
        self.end_time: Optional[datetime.datetime] = None

        # Log/output directory (subclasses should set self._log_dir)
        self._log_dir: str = ""

        # Activity log — mirrors the Live Console to a text file each run
        self._activity_logger: Optional[ActivityLogger] = None

        self._build_ui()

        # ── Live 1-second clock ────────────────────────────────────────
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 16, 18, 14)
        root_layout.setSpacing(12)

        # ── 1. Header Row ──────────────────────────────────────────────
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 10px;")
        h_layout = QHBoxLayout(hdr_frame)
        h_layout.setContentsMargins(12, 6, 12, 6)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        lbl_title = QLabel(self.title_text)
        lbl_title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        self.lbl_desc = QLabel(self.description_text)
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; border: none;")
        title_vbox.addWidget(lbl_title)
        title_vbox.addWidget(self.lbl_desc)

        h_layout.addLayout(title_vbox, stretch=1)

        self.badge_status = StatusBadge("Ready", "neutral")
        h_layout.addWidget(self.badge_status, alignment=Qt.AlignVCenter)
        root_layout.addWidget(hdr_frame)

        # ── 2. Notebook / Tabs ──────────────────────────────────────────
        self.notebook = QTabWidget()
        root_layout.addWidget(self.notebook, stretch=1)

        # Tab 1: Configuration & Queue
        self.tab_config = QWidget()
        self._build_config_tab()
        self.notebook.addTab(self.tab_config, " ⚙️ 1. Configuration & Queue ")

        # Tab 2: Activity & Logs
        self.tab_execution = QWidget()
        self._build_execution_tab()
        self.notebook.addTab(self.tab_execution, " 📊 Live Activity & Logs ")

        # ── 3. Bottom Pinned Action Bar ────────────────────────────────
        bot_bar = QFrame()
        bot_bar.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 8px;")
        b_layout = QHBoxLayout(bot_bar)
        b_layout.setContentsMargins(12, 8, 12, 8)
        b_layout.setSpacing(10)

        self.btn_start = QPushButton("▶  Start Automation")
        self.btn_start.setObjectName("btn_success")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_automation)
        b_layout.addWidget(self.btn_start)

        self.btn_pause = QPushButton("⏸  Pause")
        self.btn_pause.setObjectName("btn_warning")
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        b_layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹  Stop")
        self.btn_stop.setObjectName("btn_danger")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_automation)
        b_layout.addWidget(self.btn_stop)

        b_layout.addSpacing(10)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        b_layout.addWidget(self.progress_bar, stretch=1)

        self.lbl_progress_percent = QLabel("0%")
        self.lbl_progress_percent.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {TEXT_PRIMARY}; border: none; min-width: 45px;")
        b_layout.addWidget(self.lbl_progress_percent)

        root_layout.addWidget(bot_bar)

    # ------------------------------------------------------------------
    # Configuration Tab Construction
    # ------------------------------------------------------------------
    def _build_config_tab(self):
        layout = QVBoxLayout(self.tab_config)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        splitter = QSplitter(Qt.Vertical)

        # Top: Scrollable Inputs Container
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self.inputs_container = QWidget()
        self.inputs_layout = QVBoxLayout(self.inputs_container)
        self.inputs_layout.setContentsMargins(6, 6, 6, 6)
        self.inputs_layout.setSpacing(8)
        self.inputs_layout.addStretch()

        scroll.setWidget(self.inputs_container)
        splitter.addWidget(scroll)

        # Bottom: Queue Management Card
        queue_card = QFrame()
        queue_card.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px;")
        q_layout = QVBoxLayout(queue_card)
        q_layout.setContentsMargins(12, 10, 12, 10)
        q_layout.setSpacing(6)

        q_hdr = QHBoxLayout()
        lbl_q_title = QLabel("📋  Multi-Task Sequential Execution Queue")
        lbl_q_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        q_hdr.addWidget(lbl_q_title)
        q_hdr.addStretch()

        self.lbl_queue_status = QLabel("Queue is empty (Single-task run if started without queueing)")
        self.lbl_queue_status.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        q_hdr.addWidget(self.lbl_queue_status)
        q_layout.addLayout(q_hdr)

        self.table_queue = QTableWidget(0, 4)
        self.table_queue.setHorizontalHeaderLabels(["#", "Task Credentials & Input Details", "Status", "Verified"])
        self.table_queue.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_queue.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_queue.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_queue.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_queue.setAlternatingRowColors(False)
        self.table_queue.setMaximumHeight(110)
        q_layout.addWidget(self.table_queue)

        q_btns = QHBoxLayout()
        q_btns.setContentsMargins(0, 2, 0, 0)
        btn_add_q = QPushButton("➕ Add Current Inputs to Queue")
        btn_add_q.setObjectName("btn_success")
        btn_add_q.setCursor(Qt.PointingHandCursor)
        btn_add_q.clicked.connect(self.add_inputs_to_queue)
        q_btns.addWidget(btn_add_q)

        btn_rem_q = QPushButton("❌ Remove Selected")
        btn_rem_q.setCursor(Qt.PointingHandCursor)
        btn_rem_q.clicked.connect(self.remove_selected_queue_task)
        q_btns.addWidget(btn_rem_q)

        btn_clr_q = QPushButton("🧹 Clear Queue")
        btn_clr_q.setCursor(Qt.PointingHandCursor)
        btn_clr_q.clicked.connect(self.clear_queue)
        q_btns.addWidget(btn_clr_q)
        q_btns.addStretch()

        q_layout.addLayout(q_btns)
        splitter.addWidget(queue_card)

        splitter.setSizes([380, 160])
        layout.addWidget(splitter)

    # ------------------------------------------------------------------
    # Execution Tab Construction  (full 12-item Live KPI panel)
    # ------------------------------------------------------------------
    def _build_execution_tab(self):
        layout = QVBoxLayout(self.tab_execution)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── Section 1: Live Progress & Performance KPIs ────────────────
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px;"
        )
        kpi_outer = QVBoxLayout(kpi_frame)
        kpi_outer.setContentsMargins(12, 10, 12, 10)
        kpi_outer.setSpacing(6)

        kpi_title = QLabel("📈  Live Progress & Performance KPIs")
        kpi_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        kpi_outer.addWidget(kpi_title)

        # 2-column grid
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(0)
        kpi_grid.setContentsMargins(0, 4, 0, 4)

        LABEL_STYLE = f"font-size: 11px; font-weight: 600; color: {TEXT_SECONDARY}; border: none; padding: 2px 8px 2px 0;"
        VALUE_STYLE = f"font-size: 11px; color: {ACCENT_PRIMARY}; border: none; padding: 2px 20px 2px 0;"

        def _make_kpi_row(grid, row, col_offset, label_text):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(LABEL_STYLE)
            val = QLabel("-")
            val.setStyleSheet(VALUE_STYLE)
            grid.addWidget(lbl, row, col_offset * 2,     Qt.AlignTop)
            grid.addWidget(val, row, col_offset * 2 + 1, Qt.AlignTop)
            return val

        # Left column – Workload & Volume Metrics
        self.kpi_students         = _make_kpi_row(kpi_grid, 0, 0, "Students / Items Traversed:")
        self.kpi_files_done       = _make_kpi_row(kpi_grid, 1, 0, "Files / Tasks Processed:")
        self.kpi_data_volume      = _make_kpi_row(kpi_grid, 2, 0, "Data Volume Processed:")
        self.kpi_current_student  = _make_kpi_row(kpi_grid, 3, 0, "Current Student ID:")
        self.kpi_current_category = _make_kpi_row(kpi_grid, 4, 0, "Current Stage / Folder:")
        self.kpi_current_file     = _make_kpi_row(kpi_grid, 5, 0, "In-Flight File & Size:")

        # Right column – Performance & Time KPIs
        self.kpi_start_time      = _make_kpi_row(kpi_grid, 0, 1, "Automation Start:")
        self.kpi_now_time        = _make_kpi_row(kpi_grid, 1, 1, "Current Date & Time:")
        self.kpi_elapsed         = _make_kpi_row(kpi_grid, 2, 1, "Elapsed Duration:")
        self.kpi_transfer_speed  = _make_kpi_row(kpi_grid, 3, 1, "Transfer Throughput:")
        self.kpi_avg_speed       = _make_kpi_row(kpi_grid, 4, 1, "Item Processing Speed:")
        self.kpi_eta             = _make_kpi_row(kpi_grid, 5, 1, "Est. Remaining & ETA:")

        # Column sizing
        kpi_grid.setColumnMinimumWidth(0, 160)
        kpi_grid.setColumnMinimumWidth(1, 240)
        kpi_grid.setColumnMinimumWidth(2, 160)
        kpi_grid.setColumnMinimumWidth(3, 240)
        kpi_grid.setColumnStretch(1, 1)
        kpi_grid.setColumnStretch(3, 1)

        kpi_outer.addLayout(kpi_grid)
        layout.addWidget(kpi_frame)

        # ── Backward-compat stubs (hidden) – keep attributes so subclass
        # code that calls card_*.set_value() doesn't crash. Not shown in UI.
        self.card_elapsed = MetricCard("Elapsed",   "00:00:00", "", "⏱️", ACCENT_PRIMARY)
        self.card_eta     = MetricCard("Remaining", "--:--",    "", "⏳", ACCENT_INFO)
        self.card_items   = MetricCard("Processed", "0 / 0",    "", "📋", ACCENT_SUCCESS)
        self.card_speed   = MetricCard("Speed",     "0 B/s",    "", "⚡", ACCENT_WARNING)
        self.card_errors  = MetricCard("Errors",    "0",        "", "⚠️", ACCENT_DANGER)
        # (cards intentionally NOT added to layout – removed per user request)

        # ── Section 2: Telemetry Graph ─────────────────────────────────
        self.telemetry_graph = TimeSeriesTelemetryGraph(self)
        layout.addWidget(self.telemetry_graph)

        # ── Section 3: Live Console Log ────────────────────────────────
        console_frame = QFrame()
        console_frame.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px;")
        c_layout = QVBoxLayout(console_frame)
        c_layout.setContentsMargins(12, 10, 12, 10)
        c_layout.setSpacing(6)

        c_hdr = QHBoxLayout()
        lbl_c_title = QLabel("💻  Live Activity Console")
        lbl_c_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        c_hdr.addWidget(lbl_c_title)
        c_hdr.addStretch()

        btn_clear_log = QPushButton("Clear Console")
        btn_clear_log.setCursor(Qt.PointingHandCursor)
        btn_clear_log.clicked.connect(self.clear_console)
        c_hdr.addWidget(btn_clear_log)

        btn_open_log = QPushButton("📄 Open Log File")
        btn_open_log.setCursor(Qt.PointingHandCursor)
        btn_open_log.clicked.connect(self.open_log_file)
        c_hdr.addWidget(btn_open_log)

        btn_open_output = QPushButton("📂 Open Output Folder")
        btn_open_output.setCursor(Qt.PointingHandCursor)
        btn_open_output.clicked.connect(self.open_output_folder)
        c_hdr.addWidget(btn_open_output)

        c_layout.addLayout(c_hdr)

        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Consolas, Cascadia Code, Courier New, monospace;
                font-size: 12px;
                border: 1px solid {BORDER_MUTED};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        c_layout.addWidget(self.txt_console, stretch=1)
        layout.addWidget(console_frame, stretch=1)

    # ------------------------------------------------------------------
    # Form Input Helpers
    # ------------------------------------------------------------------
    def create_file_picker_row(self, parent_layout, label_text: str, callback: Callable[[str], None], is_directory: bool = False, file_types: str = "All Files (*.*)") -> QFrame:
        """Helper to create a sleek file/folder selector row."""
        frame = QFrame()
        frame.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY}; min-width: 170px; border: none;")
        layout.addWidget(lbl)

        lbl_path = QLabel("No file selected" if not is_directory else "No directory selected")
        lbl_path.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED}; border: none;")
        layout.addWidget(lbl_path, stretch=1)

        btn_browse = QPushButton("Browse...")
        btn_browse.setCursor(Qt.PointingHandCursor)

        def on_browse():
            if is_directory:
                path = QFileDialog.getExistingDirectory(self, f"Select {label_text}")
            else:
                path, _ = QFileDialog.getOpenFileName(self, f"Select {label_text}", filter=file_types)
            if path:
                lbl_path.setText(os.path.normpath(path))
                lbl_path.setStyleSheet(f"font-size: 12px; color: {TEXT_PRIMARY}; border: none;")
                callback(path)

        btn_browse.clicked.connect(on_browse)
        layout.addWidget(btn_browse)

        if parent_layout is not None:
            if hasattr(parent_layout, "insertWidget"):
                parent_layout.insertWidget(parent_layout.count() - 1, frame)
            else:
                parent_layout.addWidget(frame)

        return frame

    # ------------------------------------------------------------------
    # Console Logging & Signals
    # ------------------------------------------------------------------
    @Slot(str, str)
    def log(self, message: str, level: str = "INFO"):
        """Append color-formatted message to the live console and activity log file."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO":    ACCENT_PRIMARY,
            "SUCCESS": ACCENT_SUCCESS,
            "WARNING": ACCENT_WARNING,
            "ERROR":   ACCENT_DANGER,
            "DEBUG":   TEXT_MUTED,
        }
        color = color_map.get(level.upper(), TEXT_PRIMARY)

        # --- Mirror to text log file (same line, no colour codes) ---
        if self._activity_logger is not None:
            self._activity_logger.write(level, message)

        # Move cursor to end
        self.txt_console.moveCursor(QTextCursor.End)
        tf = QTextCharFormat()

        # Timestamp
        tf.setForeground(QColor(TEXT_MUTED))
        self.txt_console.setCurrentCharFormat(tf)
        self.txt_console.insertPlainText(f"[{timestamp}] ")

        # Tag
        tf.setForeground(QColor(color))
        tf.setFontWeight(QFont.Bold)
        self.txt_console.setCurrentCharFormat(tf)
        self.txt_console.insertPlainText(f"[{level.upper()}] ")

        # Message
        tf.setForeground(QColor("#D4D4D4"))
        tf.setFontWeight(QFont.Normal)
        self.txt_console.setCurrentCharFormat(tf)
        self.txt_console.insertPlainText(f"{message}\n")

        self.txt_console.moveCursor(QTextCursor.End)

    def clear_console(self):
        self.txt_console.clear()

    def open_log_file(self):
        """Open the most recent debug log file in Notepad."""
        log_path = ""
        if self._log_dir:
            candidates = glob.glob(os.path.join(self._log_dir, "*_debug_*.log"))
            if candidates:
                log_path = max(candidates, key=os.path.getmtime)
            else:
                legacy = os.path.join(self._log_dir, "automation_debug.log")
                if os.path.isfile(legacy):
                    log_path = legacy
        if log_path and os.path.isfile(log_path):
            subprocess.Popen(["notepad", log_path])
        else:
            QMessageBox.information(self, "Log File", "No log file found yet. Run an automation first.")

    def open_output_folder(self):
        """Open the output/log folder in Windows Explorer."""
        folder = self._log_dir
        if folder and os.path.isdir(folder):
            subprocess.Popen(["explorer", os.path.normpath(folder)])
        else:
            QMessageBox.information(self, "Output Folder", "No output folder selected yet.")

    # ------------------------------------------------------------------
    # Live KPI Updates – update_kpis(**kwargs)
    # ------------------------------------------------------------------
    def update_kpis(self, **kwargs):
        """
        Update metric values, calculate rolling throughput (bytes/sec or items/min),
        and refresh the 12-item KPI grid, progress bar, top badge, and telemetry graph.
        """
        now_ts = time.time()
        is_skipped = bool(kwargs.get("skipped", False))

        if kwargs.get("total_students") is not None:
            self.total_students = int(kwargs["total_students"])
        if kwargs.get("students_traversed") is not None:
            prev_traversed = self.students_traversed
            self.students_traversed = int(kwargs["students_traversed"])

            if is_skipped:
                # All items up to this count were completed in prior runs
                self.students_base_offset = self.students_traversed
            elif self.students_base_offset is None:
                # First event of session: if starting from > 1 without an explicit skip event
                self.students_base_offset = max(0, self.students_traversed - 1)

            session_items_done = max(0, self.students_traversed - (self.students_base_offset or 0))

            # When non-skipped active work begins, mark active work start time
            if not is_skipped and session_items_done > 0 and self.active_work_start_time is None:
                self.active_work_start_time = datetime.datetime.now()

            # Track rolling throughput for item/record operations (only active work)
            if not is_skipped and session_items_done > 0:
                prev_session_items = max(0, prev_traversed - (self.students_base_offset or 0))
                if session_items_done > prev_session_items or not self.item_speed_history:
                    self.item_speed_history.append((now_ts, session_items_done))
                    # 15-second rolling window
                    self.item_speed_history = [(ts, c) for ts, c in self.item_speed_history if now_ts - ts <= 15.0]
                    if len(self.item_speed_history) >= 2:
                        dt = max(0.1, self.item_speed_history[-1][0] - self.item_speed_history[0][0])
                        dc = max(0, self.item_speed_history[-1][1] - self.item_speed_history[0][1])
                        self.rolling_item_rate_per_min = (dc / dt) * 60.0

        if "current_student_id" in kwargs and kwargs["current_student_id"] is not None:
            self.current_student_id = str(kwargs["current_student_id"])
        if "current_category" in kwargs and kwargs["current_category"] is not None:
            self.current_category = str(kwargs["current_category"])

        if kwargs.get("files_done") is not None:
            prev_files = self.files_done
            self.files_done = int(kwargs["files_done"])
            if is_skipped:
                self.files_base_offset = self.files_done
            elif self.files_base_offset is None:
                self.files_base_offset = max(0, self.files_done - 1)

            session_files_done = max(0, self.files_done - (self.files_base_offset or 0))
            if not is_skipped and session_files_done > 0 and self.active_work_start_time is None:
                self.active_work_start_time = datetime.datetime.now()

        if kwargs.get("files_total") is not None:
            self.files_total = int(kwargs["files_total"])
        if kwargs.get("total_bytes") is not None:
            self.total_bytes = int(kwargs["total_bytes"])
        if kwargs.get("bytes_done") is not None:
            self.bytes_done = int(kwargs["bytes_done"])
            if is_skipped:
                self.bytes_base_offset = self.bytes_done
            elif self.bytes_base_offset is None:
                self.bytes_base_offset = 0

            session_bytes_done = max(0, self.bytes_done - (self.bytes_base_offset or 0))
            if not is_skipped and session_bytes_done > 0 and self.active_work_start_time is None:
                self.active_work_start_time = datetime.datetime.now()

            if not is_skipped and session_bytes_done > 0:
                self.speed_history.append((now_ts, session_bytes_done))
                # Rolling 10-second window
                self.speed_history = [(ts, b) for ts, b in self.speed_history if now_ts - ts <= 10.0]
                if len(self.speed_history) >= 2:
                    dt = max(0.1, self.speed_history[-1][0] - self.speed_history[0][0])
                    db = max(0, self.speed_history[-1][1] - self.speed_history[0][1])
                    self.rolling_speed_bps = db / dt

        if "current_file_name" in kwargs and kwargs["current_file_name"] is not None:
            self.current_file_name = str(kwargs["current_file_name"])
        if kwargs.get("current_file_size") is not None:
            self.current_file_size = int(kwargs["current_file_size"])
        if kwargs.get("transfer_rate_bps") is not None:
            self.transfer_rate_bps = float(kwargs["transfer_rate_bps"])

        # Feed data point to telemetry graph
        is_upload = (self.tool_mode == "upload") or (self.total_bytes > 0)
        if is_upload and (self.bytes_done > 0 or self.total_bytes > 0):
            fn = kwargs.get("current_file_name") or self.current_file_name
            spd = self.rolling_speed_bps if self.rolling_speed_bps > 0 else self.transfer_rate_bps
            session_bytes = max(0, self.bytes_done - (self.bytes_base_offset or 0))
            tot_lbl = (
                f"Total: {format_bytes(self.bytes_done)} / {format_bytes(self.total_bytes)}"
                if self.total_bytes > 0 else f"Total: {format_bytes(self.bytes_done)}"
            )
            if not is_skipped and (session_bytes > 0 or spd > 0):
                self.telemetry_graph.record_datapoint(
                    speed=spd,
                    cumulative=self.bytes_done,
                    label=fn if fn != "-" else "",
                    unit_type="bytes",
                    total_label=tot_lbl
                )
            elif is_skipped:
                self.telemetry_graph.lbl_total_progress.setText(tot_lbl)
        elif not is_upload and (self.total_students > 0 or self.students_traversed > 0):
            session_items = max(0, self.students_traversed - (self.students_base_offset or 0))
            tot_lbl = (
                f"Total: {self.students_traversed}/{self.total_students}"
                if self.total_students > 0 else f"Total: {self.students_traversed} items"
            )
            if not is_skipped and session_items > 0:
                active_ref = self.active_work_start_time or self.start_time
                elapsed_sec = max(0.1, (now_ts - active_ref.timestamp()) if active_ref else 1.0)
                avg_rate_m = (session_items / elapsed_sec) * 60.0 if elapsed_sec > 0 else 0.0
                spd = self.rolling_item_rate_per_min if self.rolling_item_rate_per_min > 0 else avg_rate_m
                lbl = f"ID: {self.current_student_id}" if self.current_student_id != "-" else ""
                self.telemetry_graph.record_datapoint(
                    speed=spd,
                    cumulative=self.students_traversed,
                    label=lbl,
                    unit_type="items",
                    total_label=tot_lbl
                )
            elif is_skipped:
                self.telemetry_graph.lbl_total_progress.setText(tot_lbl)

        # Progress bar logic:
        #   upload mode (tool_mode=="upload" or total_bytes>0): byte-based
        #   download mode: student-based
        is_upload = (self.tool_mode == "upload") or (self.total_bytes > 0)
        if is_upload and self.total_bytes > 0:
            # Byte-based progress for upload
            pb_max = 1000  # scale to avoid int overflow on large files
            pb_val = int(min(self.bytes_done / self.total_bytes, 1.0) * pb_max) if self.total_bytes > 0 else 0
            pct = min(100, max(0, int(pb_val / pb_max * 100))) if pb_max > 0 else 0
            new_max = pb_max
            current_val = pb_val
            badge_cur = format_bytes(self.bytes_done)
            badge_tot = format_bytes(self.total_bytes)
        elif self.total_students > 0:
            new_max = self.total_students
            current_val = self.students_traversed
            pct = min(100, max(0, int(current_val / new_max * 100))) if new_max > 0 else 0
            badge_cur = str(current_val)
            badge_tot = str(new_max)
        elif self.files_total > 0:
            new_max = self.files_total
            current_val = self.files_done
            pct = min(100, max(0, int(current_val / new_max * 100))) if new_max > 0 else 0
            badge_cur = str(current_val)
            badge_tot = str(new_max)
        else:
            new_max = self.total_items if self.total_items > 0 else 0
            current_val = self.items_processed
            pct = min(100, max(0, int(current_val / new_max * 100))) if new_max > 0 else 0
            badge_cur = str(current_val)
            badge_tot = str(new_max)

        if new_max > 0 and self.progress_bar.maximum() != new_max:
            self.progress_bar.setMaximum(new_max)

        if new_max > 0:
            self.progress_bar.setValue(min(current_val, new_max))
            self.lbl_progress_percent.setText(f"{pct}%")

            # Update top-right status badge
            if self.is_running:
                if current_val >= new_max and new_max > 0:
                    self.badge_status.set_variant("success", f"Completed {badge_cur}/{badge_tot} (100%)")
                else:
                    self.badge_status.set_variant("success", f"Processing {badge_cur}/{badge_tot} ({pct}%)")

        # Refresh grid labels on the Qt thread
        self._refresh_kpi_display()

    def _refresh_kpi_display(self):
        """Synchronously update all 12 KPI grid labels from current metric state."""
        try:
            is_upload = (self.tool_mode == "upload") or (self.total_bytes > 0)

            # 1. Students / Items Traversed
            session_items = max(0, self.students_traversed - (self.students_base_offset or 0))
            if self.total_students > 0:
                pct = (self.students_traversed / self.total_students) * 100
                st_part = f"{self.students_traversed}/{self.total_students} ({pct:.1f}%)"
                if (self.students_base_offset or 0) > 0 and session_items > 0:
                    st_part += f" [+{session_items}]"
                if is_upload:
                    # Upload: {students done}/{Total Students} and {files uploaded}/{total files in storage}
                    if self.files_total > 0:
                        files_pct = (self.total_files_uploaded / self.files_total) * 100
                        self.kpi_students.setText(
                            f"{st_part} and {self.total_files_uploaded}/{self.files_total} files ({files_pct:.1f}%)"
                        )
                    else:
                        self.kpi_students.setText(f"{st_part} and {self.total_files_uploaded} files")
                else:
                    # Download: {students done}/{Total Students}
                    self.kpi_students.setText(st_part)
            elif self.students_traversed > 0:
                if (self.students_base_offset or 0) > 0 and session_items > 0:
                    self.kpi_students.setText(f"{self.students_traversed} [+{session_items}]")
                else:
                    self.kpi_students.setText(str(self.students_traversed))
            else:
                self.kpi_students.setText("0")

            # 2. Files / Tasks Processed:
            # Upload: {files for a student uploaded}/{Total files for that Student}
            # Download: {files for a student downloaded}/{Total files for that Student}
            if self.student_files_total > 0:
                f_pct = (self.student_files_done / self.student_files_total) * 100
                self.kpi_files_done.setText(
                    f"{self.student_files_done}/{self.student_files_total} ({f_pct:.1f}%)"
                )
            elif self.student_files_done > 0:
                self.kpi_files_done.setText(str(self.student_files_done))
            elif is_upload or self.tool_mode == "download":
                self.kpi_files_done.setText("0/0")
            else:
                self.kpi_files_done.setText("-")

            # 3. Data Volume
            if self.total_bytes > 0:
                v_pct = (self.bytes_done / self.total_bytes) * 100
                self.kpi_data_volume.setText(
                    f"{format_bytes(self.bytes_done)} / {format_bytes(self.total_bytes)} ({v_pct:.1f}%)"
                )
            elif self.bytes_done > 0:
                self.kpi_data_volume.setText(format_bytes(self.bytes_done))
            else:
                self.kpi_data_volume.setText("- (N/A for form submission)")

            # 4. Current labels
            self.kpi_current_student.setText(self.current_student_id)
            self.kpi_current_category.setText(self.current_category)

            if self.current_file_name and self.current_file_name != "-":
                if self.current_file_size > 0:
                    self.kpi_current_file.setText(
                        f"{self.current_file_name} ({format_bytes(self.current_file_size)})"
                    )
                else:
                    self.kpi_current_file.setText(self.current_file_name)
            else:
                self.kpi_current_file.setText("- (Web form automation)")

            # 5. Start time
            if self.start_time:
                self.kpi_start_time.setText(self.start_time.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                self.kpi_start_time.setText("-")

            # 6. Speed calculations (only when running)
            if self.start_time and self.is_running:
                now = datetime.datetime.now()
                active_ref = self.active_work_start_time or self.start_time
                elapsed_sec = max(0.1, (now - active_ref).total_seconds())

                if self.bytes_done > 0:
                    session_bytes = max(0, self.bytes_done - (self.bytes_base_offset or 0))
                    if session_bytes > 0:
                        overall_speed = session_bytes / elapsed_sec
                        mb_done = session_bytes / (1024 * 1024)
                        sec_per_mb = (elapsed_sec / mb_done) if mb_done > 0.01 else 0
                        display_speed = self.rolling_speed_bps if self.rolling_speed_bps > 0 else overall_speed
                        if sec_per_mb > 0:
                            self.kpi_transfer_speed.setText(
                                f"{format_speed_bps(display_speed)} ({sec_per_mb:.2f}s/MB)"
                            )
                        else:
                            self.kpi_transfer_speed.setText(format_speed_bps(display_speed))
                    else:
                        self.kpi_transfer_speed.setText("Calculating...")
                else:
                    self.kpi_transfer_speed.setText("- (Form automation)")

                session_files = max(0, self.files_done - (self.files_base_offset or 0))
                if session_items > 0:
                    avg_s = elapsed_sec / session_items
                    rate_m = (session_items / elapsed_sec) * 60.0
                    rolling_m = self.rolling_item_rate_per_min if self.rolling_item_rate_per_min > 0 else rate_m
                    self.kpi_avg_speed.setText(f"{rolling_m:.1f} items/min ({avg_s:.1f}s/item)")
                elif self.students_traversed > 0:
                    self.kpi_avg_speed.setText("Calculating... (Skipping completed)")
                elif session_files > 0:
                    s_per_file = elapsed_sec / session_files
                    self.kpi_avg_speed.setText(f"{s_per_file:.2f}s/file")
                elif self.files_done > 0:
                    self.kpi_avg_speed.setText("Calculating... (Skipping completed)")
                else:
                    self.kpi_avg_speed.setText("-")
            elif self.end_time:
                # Finished run
                pass
            else:
                self.kpi_transfer_speed.setText("-")
                self.kpi_avg_speed.setText("-")

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Live 1-second clock: updates Current Time, Elapsed, and ETA
    # ------------------------------------------------------------------
    def _tick_clock(self):
        """Called every second by the QTimer to update time-dependent KPIs."""
        try:
            now = datetime.datetime.now()
            self.kpi_now_time.setText(now.strftime("%Y-%m-%d %H:%M:%S"))

            if self.is_running and self.start_time:
                elapsed = now - self.start_time
                self.kpi_elapsed.setText(format_timedelta(elapsed) + " (running)")

                active_ref = self.active_work_start_time or self.start_time
                active_elapsed_sec = max(0.1, (now - active_ref).total_seconds())

                eta_dt: Optional[datetime.datetime] = None
                rem_sec: float = 0.0

                session_items = max(0, self.students_traversed - (self.students_base_offset or 0))
                session_files = max(0, self.files_done - (self.files_base_offset or 0))
                session_bytes = max(0, self.bytes_done - (self.bytes_base_offset or 0))

                is_upload_eta = (self.tool_mode == "upload") or (self.total_bytes > 0)

                if is_upload_eta and self.total_bytes > 0:
                    # Upload: ETA based on bytes
                    rem_bytes = max(0, self.total_bytes - self.bytes_done)
                    if rem_bytes == 0:
                        self.kpi_eta.setText("Completed")
                    elif session_bytes > 0:
                        overall_speed = session_bytes / active_elapsed_sec
                        effective_speed = (
                            (overall_speed * 0.4 + self.rolling_speed_bps * 0.6)
                            if self.rolling_speed_bps > 0 else overall_speed
                        )
                        if effective_speed > 0:
                            rem_sec = rem_bytes / effective_speed
                            eta_dt = now + datetime.timedelta(seconds=rem_sec)
                    else:
                        self.kpi_eta.setText("Calculating...")

                elif self.total_students > 0:
                    # Download (or student-based): ETA based on students
                    rem_items = max(0, self.total_students - self.students_traversed)
                    if rem_items == 0:
                        self.kpi_eta.setText("Completed")
                    elif session_items > 0:
                        overall_rate_sec = session_items / active_elapsed_sec
                        if self.rolling_item_rate_per_min > 0:
                            rolling_rate_sec = self.rolling_item_rate_per_min / 60.0
                            effective_rate_sec = (overall_rate_sec * 0.3) + (rolling_rate_sec * 0.7)
                        else:
                            effective_rate_sec = overall_rate_sec

                        if effective_rate_sec > 0:
                            rem_sec = rem_items / effective_rate_sec
                            eta_dt = now + datetime.timedelta(seconds=rem_sec)
                    elif (self.students_base_offset or 0) > 0:
                        self.kpi_eta.setText("Calculating... (Skipping completed)")
                    else:
                        self.kpi_eta.setText("Calculating...")

                elif self.files_total > 0:
                    rem_files = max(0, self.files_total - self.files_done)
                    if rem_files == 0:
                        self.kpi_eta.setText("Completed")
                    elif session_files > 0:
                        effective_rate_sec = session_files / active_elapsed_sec
                        if effective_rate_sec > 0:
                            rem_sec = rem_files / effective_rate_sec
                            eta_dt = now + datetime.timedelta(seconds=rem_sec)
                    elif (self.files_base_offset or 0) > 0:
                        self.kpi_eta.setText("Calculating... (Skipping completed)")
                    else:
                        self.kpi_eta.setText("Calculating...")

                if eta_dt is not None:
                    today = now.date()
                    eta_date = eta_dt.date()
                    if eta_date == today:
                        time_str = eta_dt.strftime("Today at %I:%M:%S %p")
                    elif eta_date == today + datetime.timedelta(days=1):
                        time_str = eta_dt.strftime("Tomorrow at %I:%M:%S %p")
                    else:
                        time_str = eta_dt.strftime("%b %d, %Y at %I:%M %p")

                    dur_str = format_time_remaining(rem_sec)
                    self.kpi_eta.setText(f"~{dur_str} (ETA: {time_str})")

                # Keep speed and other KPIs fresh every second
                self._refresh_kpi_display()

            elif self.start_time and self.end_time:
                elapsed = self.end_time - self.start_time
                self.kpi_elapsed.setText(format_timedelta(elapsed) + " (done)")
                self.kpi_eta.setText(self.end_time.strftime("%Y-%m-%d %H:%M:%S") + " (done)")

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Progress & Telemetry slot handlers (backward-compat)
    # ------------------------------------------------------------------
    @Slot(int, int)
    def update_progress(self, current: int, total: int):
        """Generic progress slot – keeps bar in sync with absolute counts."""
        self.items_processed = current
        if total > 0:
            if total > self.total_items or self.total_items <= 1:
                self.total_items = total

        # If student task, update students_traversed
        if self.total_students > 0 or (self.files_total <= 0 and total > 1):
            if total > 1 and (self.total_students <= 1 or total > self.total_students):
                self.total_students = total
            self.students_traversed = current
            effective_max = self.total_students
            effective_cur = self.students_traversed
        elif self.files_total > 0:
            effective_max = self.files_total
            effective_cur = self.files_done
        else:
            effective_max = max(self.total_items, total, 1)
            effective_cur = current

        if effective_max > 0 and self.progress_bar.maximum() != effective_max:
            self.progress_bar.setMaximum(effective_max)
        self.progress_bar.setValue(min(effective_cur, effective_max))
        pct = min(100, max(0, int(effective_cur / effective_max * 100))) if effective_max > 0 else 0
        self.lbl_progress_percent.setText(f"{pct}%")
        if self.is_running and effective_max > 0:
            self.badge_status.set_variant("success", f"Processing {effective_cur}/{effective_max} ({pct}%)")

    @Slot(float, int, str)
    def update_telemetry(self, speed_bps: float, cum_bytes: int, file_name: str = ""):
        self.telemetry_graph.record_datapoint(
            speed=speed_bps,
            cumulative=cum_bytes,
            label=file_name,
            unit_type="bytes"
        )

    @Slot(dict)
    def _handle_kpi_event(self, kpi_dict: dict):
        """
        Slot connected to AutomationWorker.sig_kpi.
        Dispatch a progress-event dict and update all KPIs accordingly.
        """
        t = kpi_dict.get("type")
        if t == "init_totals":
            tot_st = kpi_dict.get("total_students", self.total_students)
            tot_fl = kpi_dict.get("total_files", self.files_total)
            tot_bytes = kpi_dict.get("total_bytes", 0)
            # Detect upload mode: uploader pre-scans and provides total_bytes > 0
            if tot_bytes > 0:
                self.tool_mode = "upload"
            self.update_kpis(
                total_students=tot_st,
                files_total=tot_fl,
                total_bytes=tot_bytes,
            )
            total = tot_fl if tot_fl > 0 else (tot_st if tot_st > 0 else 1)
            self.total_items = total
            # Set progress bar range based on mode
            if self.tool_mode == "upload" and tot_bytes > 0:
                self.progress_bar.setMaximum(1000)
            else:
                self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(0)
            self.lbl_progress_percent.setText("0%")
            self.badge_status.set_variant("success", f"Processing 0/{total} (0%)")
        elif t == "total":
            tot = max(1, kpi_dict.get("total", self.total_students))
            self.total_items = tot
            if kpi_dict.get("is_file_task"):
                self.update_kpis(files_total=tot)
            else:
                # Download mode — no total_bytes upfront
                if not self.tool_mode:
                    self.tool_mode = "download"
                self.update_kpis(total_students=tot)
            self.progress_bar.setMaximum(tot)
            self.progress_bar.setValue(0)
            self.lbl_progress_percent.setText("0%")
            self.badge_status.set_variant("success", f"Processing 0/{tot} (0%)")
        elif t in ("student_start", "item_start"):
            sid = kpi_dict.get("student_id", "-")
            tot_st = self.total_students if self.total_students > 0 else kpi_dict.get("total_students", self.total_items)
            cur_done = self.students_traversed
            # Reset per-student counts for the newly starting student
            self.student_files_done = 0
            self.student_files_total = 0
            kargs = {
                "current_student_id": sid,
                "current_category": kpi_dict.get("category", self.current_category),
                "bytes_done": kpi_dict.get("bytes_done", self.bytes_done),
                "total_bytes": kpi_dict.get("total_bytes", self.total_bytes),
            }
            if tot_st > 0:
                kargs["total_students"] = tot_st
            if "students_traversed" in kpi_dict and kpi_dict["students_traversed"] > self.students_traversed:
                kargs["students_traversed"] = kpi_dict["students_traversed"]
                cur_done = kpi_dict["students_traversed"]
            self.update_kpis(**kargs)
        elif t == "category":
            self.update_kpis(current_category=kpi_dict.get("name", "-"))
        elif t == "files_total":
            tot = kpi_dict.get("total", self.files_total)
            # student_files: per-student file total (sent alongside global total)
            stud_tot = kpi_dict.get("student_files", tot)
            self.student_files_total = stud_tot
            self.student_files_done = 0   # reset per-student counter for new student
            self.total_items = max(self.total_items, tot)
            self.update_kpis(
                files_total=tot,
                bytes_done=kpi_dict.get("bytes_done", self.bytes_done),
                total_bytes=kpi_dict.get("total_bytes", self.total_bytes),
            )
            # Only update progress bar max if not in byte-based upload mode
            if self.tool_mode != "upload" and tot > 0 and self.progress_bar.maximum() != tot:
                self.progress_bar.setMaximum(tot)
        elif t == "file_start":
            self.update_kpis(
                current_file_name=kpi_dict.get("file_name", "-"),
                current_file_size=kpi_dict.get("file_size", 0),
                bytes_done=kpi_dict.get("bytes_done", self.bytes_done),
                total_bytes=kpi_dict.get("total_bytes", self.total_bytes),
            )
        elif t in ("file_done", "item_done"):
            is_file_task = bool(kpi_dict.get("is_file_task") or (self.files_total > 0 and self.total_students <= 0))

            # Update per-student file counters
            # Upload: student_files_done comes from explicit field; fallback to files_done (per-student local)
            # Download: files_done in the event is already per-student cumulative
            per_student_done = kpi_dict.get("student_files_done",
                kpi_dict.get("files_done", self.student_files_done))
            self.student_files_done = int(per_student_done) if per_student_done is not None else self.student_files_done
            if "student_files_total" in kpi_dict:
                self.student_files_total = int(kpi_dict["student_files_total"])
            elif "files_total" in kpi_dict and self.tool_mode != "upload":
                # For download, files_total in file_done is the per-student total
                self.student_files_total = int(kpi_dict["files_total"])

            # Update global files uploaded counter for upload mode
            if self.tool_mode == "upload" and "files_done" in kpi_dict:
                self.total_files_uploaded = int(kpi_dict["files_done"])

            kargs = {
                "current_file_name": kpi_dict.get("file_name", self.current_file_name),
                "current_file_size": kpi_dict.get("file_size", 0),
                "bytes_done": kpi_dict.get("bytes_done", self.bytes_done),
                "total_bytes": kpi_dict.get("total_bytes", self.total_bytes),
                "skipped": bool(kpi_dict.get("skipped", False)),
            }
            if "students_traversed" in kpi_dict:
                kargs["students_traversed"] = kpi_dict["students_traversed"]
            elif t == "item_done":
                # item_done increments completed items for pure batch table/item automations
                kargs["students_traversed"] = kpi_dict.get("completed", self.students_traversed + 1)

            if is_file_task:
                kargs["files_done"] = kpi_dict.get("completed", self.files_done + 1)
                if "files_total" in kpi_dict:
                    kargs["files_total"] = kpi_dict["files_total"]
            elif "files_done" in kpi_dict:
                kargs["files_done"] = kpi_dict["files_done"]
            if "files_total" in kpi_dict:
                kargs["files_total"] = kpi_dict["files_total"]

            if "student_id" in kpi_dict:
                kargs["current_student_id"] = kpi_dict["student_id"]

            self.update_kpis(**kargs)
        elif t == "student_done":
            st_done = kpi_dict.get("students_traversed", self.students_traversed + 1)
            tot_st = self.total_students if self.total_students > 0 else kpi_dict.get("total_students", self.total_items)

            # Update global files_uploaded for upload mode
            if self.tool_mode == "upload" and "files_done" in kpi_dict:
                self.total_files_uploaded = int(kpi_dict["files_done"])
            if "files_total" in kpi_dict and int(kpi_dict["files_total"]) > self.files_total:
                self.files_total = int(kpi_dict["files_total"])

            kpi_args = dict(
                students_traversed=st_done,
                total_students=tot_st if tot_st > 0 else self.total_students,
                bytes_done=kpi_dict.get("bytes_done", self.bytes_done),
                total_bytes=kpi_dict.get("total_bytes", self.total_bytes),
                skipped=bool(kpi_dict.get("skipped", False)),
            )
            self.update_kpis(**kpi_args)

    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------
    def add_inputs_to_queue(self):
        inputs = self.get_current_inputs()
        if not inputs:
            return
        task_id = f"task_{len(self.queue) + 1}_{int(time.time())}"
        label = self.describe_task_inputs(inputs)
        task = QueueTask(task_id, label, inputs)
        self.queue.append(task)
        self._refresh_queue_table()

    def remove_selected_queue_task(self):
        row = self.table_queue.currentRow()
        if 0 <= row < len(self.queue):
            del self.queue[row]
            self._refresh_queue_table()

    def clear_queue(self):
        self.queue.clear()
        self._refresh_queue_table()

    def _refresh_queue_table(self):
        self.table_queue.setRowCount(len(self.queue))
        for i, task in enumerate(self.queue):
            item_num = QTableWidgetItem(str(i + 1))
            item_num.setTextAlignment(Qt.AlignCenter)
            self.table_queue.setItem(i, 0, item_num)

            item_desc = QTableWidgetItem(task.label)
            self.table_queue.setItem(i, 1, item_desc)

            item_stat = QTableWidgetItem(task.status)
            item_stat.setTextAlignment(Qt.AlignCenter)
            self.table_queue.setItem(i, 2, item_stat)

            item_ver = QTableWidgetItem("✓ Yes" if task.verified else "No")
            item_ver.setTextAlignment(Qt.AlignCenter)
            self.table_queue.setItem(i, 3, item_ver)

        if not self.queue:
            self.lbl_queue_status.setText("Queue is empty (Single-task run if started without queueing)")
        else:
            self.lbl_queue_status.setText(f"{len(self.queue)} task(s) queued")

    # ------------------------------------------------------------------
    # Automation Execution Lifecycle
    # ------------------------------------------------------------------
    def get_current_inputs(self) -> Optional[dict]:
        """Subclasses return current form inputs dict."""
        return {}

    def describe_task_inputs(self, inputs: dict) -> str:
        """Subclasses return a short one-line summary string of inputs."""
        return str(inputs)

    def execute_task_backend(self, inputs: dict, **kwargs):
        """Subclasses execute the underlying automation engine."""
        raise NotImplementedError

    def _reset_metrics(self):
        """Reset all live KPI metric state for a fresh run."""
        self.total_students = 0
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_category = "-"
        self.files_done = 0
        self.files_total = 0
        self.total_bytes = 0
        self.bytes_done = 0
        self.current_file_name = "-"
        self.current_file_size = 0
        self.transfer_rate_bps = 0.0
        self.speed_history.clear()
        self.rolling_speed_bps = 0.0
        self.item_speed_history.clear()
        self.rolling_item_rate_per_min = 0.0
        self.students_base_offset = None
        self.files_base_offset = None
        self.bytes_base_offset = None
        self.active_work_start_time = None
        self.items_processed = 0
        self.total_items = 0
        self.errors_count = 0
        self.student_files_done = 0
        self.student_files_total = 0
        self.total_files_uploaded = 0

    def start_automation(self):
        """Launches single or queued tasks."""
        if self.is_running:
            return

        # If queue is empty, build single task from current inputs
        if not self.queue:
            inputs = self.get_current_inputs()
            if not inputs:
                return
            label = self.describe_task_inputs(inputs)
            self.queue.append(QueueTask("single_1", label, inputs))
            self._refresh_queue_table()

        self.is_running = True
        self.is_paused = False
        self.current_task_idx = 0

        self.start_time = datetime.datetime.now()
        self.end_time = None
        self._reset_metrics()

        # Open a fresh activity log for this run
        if self._activity_logger is not None:
            self._activity_logger.close()
        log_dir = self._log_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
        run_id = self.start_time.strftime("%Y%m%d_%H%M%S") if self.start_time else datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._activity_logger = ActivityLogger(log_dir, run_id)
        _al_path = self._activity_logger.path
        if _al_path:
            self.log(f"[LOG] Activity log: {_al_path}", "INFO")

        self.card_errors.set_value("0")
        self.progress_bar.setValue(0)
        self.lbl_progress_percent.setText("0%")

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.badge_status.set_variant("success", "Running")

        self.kpi_start_time.setText(self.start_time.strftime("%Y-%m-%d %H:%M:%S"))
        self.kpi_elapsed.setText("00:00:00 (running)")
        self.kpi_eta.setText("Calculating...")

        self.telemetry_graph.reset_graph()

        self.notebook.setCurrentWidget(self.tab_execution)
        self._run_next_queued_task()

    def _run_next_queued_task(self):
        if self.current_task_idx >= len(self.queue):
            self.finish_all_tasks()
            return

        task = self.queue[self.current_task_idx]
        task.status = "In Progress"
        self._refresh_queue_table()
        self.log(f"Starting Task {self.current_task_idx + 1} of {len(self.queue)}: {task.label}", "INFO")

        # Launch QThread worker
        self.worker = AutomationWorker(self.execute_task_backend, task.inputs)
        self.worker.sig_log.connect(self.log)
        self.worker.sig_progress.connect(self.update_progress)
        self.worker.sig_telemetry.connect(self.update_telemetry)
        self.worker.sig_kpi.connect(self._handle_kpi_event)
        self.worker.sig_finished.connect(self._on_task_finished)
        self.worker.start()

    @Slot(bool, str)
    def _on_task_finished(self, success: bool, error_msg: str):
        if self.current_task_idx < len(self.queue):
            task = self.queue[self.current_task_idx]
            task.status = "Completed" if success else "Failed"
            if not success:
                self.errors_count += 1
                self.card_errors.set_value(str(self.errors_count))
            self._refresh_queue_table()

        # If there are more tasks in the queue, show the InterTaskDialog
        has_more = (self.current_task_idx + 1) < len(self.queue)
        if has_more and not self.is_paused:
            dlg = InterTaskDialog(
                self,
                task_label=task.label,
                completed_idx=self.current_task_idx + 1,
                total_count=len(self.queue),
                timeout_minutes=int(settings.get("inter_task_timeout_minutes", 15))
            )
            dlg.exec()
            choice = dlg.choice

            if choice == 1:  # Verify and discard next
                task.verified = True
                self.log("Task verified by user. Discarding remaining queued tasks.", "INFO")
                for remaining in self.queue[self.current_task_idx + 1:]:
                    remaining.status = "Discarded"
                self.finish_all_tasks()
                return
            elif choice == 2:  # Pause queue
                self.is_paused = True
                self.badge_status.set_variant("warning", "Paused")
                self.btn_pause.setText("▶ Resume")
                self.log("Queue paused by user. Click Resume to continue.", "WARNING")
                return
            elif choice == 4:  # Abort all
                self.log("Execution aborted by user or timeout.", "DANGER")
                self.stop_automation()
                return

        # Advance
        self.current_task_idx += 1
        if self.is_running and not self.is_paused:
            self._run_next_queued_task()

    def toggle_pause(self):
        if not self.is_running:
            return
        if self.is_paused:
            self.is_paused = False
            self.btn_pause.setText("⏸ Pause")
            self.badge_status.set_variant("success", "Running")
            self.log("Automation resumed.", "INFO")
            self._run_next_queued_task()
        else:
            self.is_paused = True
            self.btn_pause.setText("▶ Resume")
            self.badge_status.set_variant("warning", "Paused")
            self.log("Automation paused by user.", "WARNING")

    def stop_automation(self):
        if self.worker and self.worker.isRunning():
            self.worker.abort()
            self.log("Stopping background automation worker...", "WARNING")
        self.is_running = False
        self.is_paused = False
        self.end_time = datetime.datetime.now()
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.badge_status.set_variant("danger", "Stopped")
        if self._activity_logger is not None:
            self._activity_logger.close()
            self._activity_logger = None

    def finish_all_tasks(self):
        self.is_running = False
        self.is_paused = False
        self.end_time = datetime.datetime.now()
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.badge_status.set_variant("success", "Completed")
        self.log("All queued tasks have finished.", "SUCCESS")
        # Show completion in progress bar
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.lbl_progress_percent.setText("100%")
        if self._activity_logger is not None:
            self._activity_logger.close()
            self._activity_logger = None
