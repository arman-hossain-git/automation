"""
ui/base_tool_frame.py
=====================
Reusable base component frame for tool automation panels.
Provides header, configuration inputs, full Live KPI statistics panel,
progress bar, action buttons, live console log output widget, and
a multi-task sequential execution queue with inter-task verification modal.
Antigravity IDE Design System styling.
"""

import os
import threading
import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from ui.theme import (
    BG_MAIN, BG_CARD, BG_CARD_ALT, BG_LOG, BORDER_COLOR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_ACCENT, TEXT_MUTED,
    ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER,
    ACCENT_DANGER, ACCENT_DANGER_HOVER,
    ACCENT_INFO, ACCENT_INFO_HOVER,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD, FONT_SMALL_BOLD, FONT_SMALL, FONT_LOG
)
from core.utils import format_timedelta, format_bytes, format_speed_bps, format_time_remaining
from core.settings_manager import settings
from ui.time_series_graph import TimeSeriesTelemetryGraph


class QueueTask:
    """Represents a single queued task configuration item."""

    def __init__(self, task_id: str, label: str, inputs: dict):
        self.id = task_id
        self.label = label
        self.inputs = inputs
        self.status = "Pending"  # Pending, In Progress, Completed, Failed, Discarded, Aborted
        self.verified = False
        self.result = {}


class InterTaskDialog(tk.Toplevel):
    """Modal popup shown between queued tasks with inactivity countdown timer."""

    def __init__(self, parent, task: QueueTask, completed_idx: int, total_count: int,
                 timeout_minutes: int = 15, inactivity_action: str = "close_app"):
        super().__init__(parent)
        self.title("Task Completed — Verification & Next Step")
        self.geometry("640x520")
        self.configure(bg=BG_MAIN)
        self.resizable(False, False)

        self.transient(parent)
        self.grab_set()

        self.choice = 3  # Default fallback option (Proceed without verifying)
        self.timeout_occurred = False
        self.seconds_remaining = int(timeout_minutes * 60)
        self.inactivity_action = inactivity_action
        self._timer_id = None

        self._build_ui(task, completed_idx, total_count)
        self._tick_timer()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self, task: QueueTask, completed_idx: int, total_count: int):
        hdr = tk.Frame(self, bg=BG_CARD_ALT, padx=18, pady=14)
        hdr.pack(fill="x")

        tk.Label(
            hdr, text=f"✓ Task {completed_idx} of {total_count} Completed Successfully",
            font=FONT_TITLE, fg=ACCENT_SUCCESS, bg=BG_CARD_ALT, anchor="w"
        ).pack(fill="x")

        tk.Label(
            hdr, text=f"Details: {task.label}",
            font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_CARD_ALT, anchor="w", wraplength=600, justify="left"
        ).pack(fill="x", pady=(4, 0))

        # Timer Card
        timer_card = tk.LabelFrame(
            self, text=" ⏱️ Inactivity Countdown Timer ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=14, pady=10
        )
        timer_card.pack(fill="x", padx=18, pady=12)

        self.lbl_timer = tk.Label(
            timer_card, text="", font=("Segoe UI", 15, "bold"),
            fg=TEXT_ACCENT, bg=BG_CARD, anchor="center"
        )
        self.lbl_timer.pack(fill="x")

        action_descriptions = {
            "close_app": "If no action is selected before the countdown reaches 0:00, the system safely assumes you are\naway from your desk, stops execution, and safely closes the application.",
            "pause_queue": "If no action is selected before the countdown reaches 0:00, the system safely\npauses execution and waits for you to resume.",
            "proceed_next": "If no action is selected before the countdown reaches 0:00, the system automatically\nproceeds to execute the next queued task.",
            "stop_automation": "If no action is selected before the countdown reaches 0:00, the system stops\nexecution and cancels remaining tasks while keeping the program open.",
        }
        timer_sub = action_descriptions.get(
            self.inactivity_action,
            "If no action is selected before the countdown reaches 0:00, the system assumes you are\naway from your desk, stops execution, and safely closes the automation."
        )

        tk.Label(
            timer_card,
            text=timer_sub,
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="center", justify="center"
        ).pack(fill="x", pady=(2, 0))

        # Action Options Card
        choices_card = tk.LabelFrame(
            self, text=" Select Next Action ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=14, pady=10
        )
        choices_card.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        def make_btn(parent_frame, title_text, desc_text, bg_color, choice_val):
            f = tk.Frame(parent_frame, bg=BG_CARD, pady=3)
            f.pack(fill="x")
            btn = tk.Button(
                f, text=title_text, font=FONT_BODY_BOLD, bg=bg_color, fg="white",
                bd=0, padx=12, pady=5, cursor="hand2", anchor="w",
                command=lambda: self._select_choice(choice_val)
            )
            btn.pack(fill="x")
            tk.Label(f, text=desc_text, font=FONT_SMALL, fg=TEXT_SECONDARY, bg=BG_CARD, anchor="w").pack(fill="x", padx=4, pady=(2, 0))

        make_btn(
            choices_card,
            "1. Verify current task and discard next tasks",
            "Verifies results for the current task now, then discards all remaining tasks in queue.",
            ACCENT_DANGER, 1
        )
        make_btn(
            choices_card,
            "2. Verify and proceed to next tasks",
            "Verifies results for current task now, then continues automatically to the next task.",
            ACCENT_SUCCESS, 2
        )
        make_btn(
            choices_card,
            "3. Proceed to next task without verifying",
            "Proceeds directly to the next task. Unverified tasks can be verified after completion.",
            ACCENT_INFO, 3
        )
        make_btn(
            choices_card,
            "4. Verify this task & Pause (Wait for Start Automation)",
            "Verifies current task now, then pauses queue until you click 'Start Automation' again.",
            "#8b5cf6", 4
        )

    def _tick_timer(self):
        mins = max(0, self.seconds_remaining // 60)
        secs = max(0, self.seconds_remaining % 60)
        self.lbl_timer.config(text=f"Inactivity Timeout: {mins:02d}:{secs:02d} remaining")

        if self.seconds_remaining <= 0:
            self.timeout_occurred = True
            self.choice = 3
            self._close_modal()
        else:
            self.seconds_remaining -= 1
            self._timer_id = self.after(1000, self._tick_timer)

    def _select_choice(self, val):
        self.choice = val
        self.timeout_occurred = False
        self._close_modal()

    def _on_close(self):
        self.choice = 3
        self.timeout_occurred = False
        self._close_modal()

    def _close_modal(self):
        if self._timer_id:
            self.after_cancel(self._timer_id)
            self._timer_id = None
        self.grab_release()
        self.destroy()


class BaseToolFrame(tk.Frame):
    """Base class for all automation tool view frames in the dashboard."""

    def __init__(self, parent, title: str, description: str, **kwargs):
        super().__init__(parent, bg=BG_MAIN, **kwargs)
        self.title_text = title
        self.desc_text = description

        self.is_running = False
        self.abort_requested = False
        self._log_dir = ""  # set by child frame so 'Open Log' button works

        # Queue tracking
        self.queue_tasks = []
        self.paused_queue_idx = None

        # Metrics for Live KPIs
        self.total_students = 0
        self.students_traversed = 0
        self.current_student_id = "-"
        self.current_category = "-"
        self.folders_created = 0
        self.files_done = 0
        self.files_total = 0
        self.total_bytes = 0
        self.bytes_done = 0
        self.current_file_name = "-"
        self.current_file_size = 0
        self.transfer_rate_bps = 0.0
        self.speed_history = []  # tuples of (timestamp, cumulative_bytes)
        self.rolling_speed_bps = 0.0

        self.start_time = None
        self.end_time = None

        self._build_ui()
        self._tick_clock()

    def _build_ui(self):
        # 1. Header banner
        header_frame = tk.Frame(self, bg=BG_MAIN)
        header_frame.pack(fill="x", padx=20, pady=(12, 6))

        title_lbl = tk.Label(
            header_frame, text=self.title_text, font=FONT_TITLE,
            fg=TEXT_PRIMARY, bg=BG_MAIN, anchor="w"
        )
        title_lbl.pack(fill="x")

        desc_lbl = tk.Label(
            header_frame, text=self.desc_text, font=FONT_BODY,
            fg=TEXT_SECONDARY, bg=BG_MAIN, anchor="w"
        )
        desc_lbl.pack(fill="x", pady=(2, 0))

        # 2. Controls & Progress Bar Header Card (Antigravity IDE sleek flat card design)
        action_card = tk.Frame(self, bg=BG_CARD, bd=1, relief="solid", padx=14, pady=8)
        action_card.pack(fill="x", padx=20, pady=(4, 8))

        # Status & Progress Row
        status_row = tk.Frame(action_card, bg=BG_CARD)
        status_row.pack(fill="x", pady=(0, 6))

        self.lbl_status = tk.Label(
            status_row, text="Status: Ready", font=FONT_BODY_BOLD,
            fg=TEXT_ACCENT, bg=BG_CARD, anchor="w"
        )
        self.lbl_status.pack(side="left")

        self.progress_bar = ttk.Progressbar(status_row, orient="horizontal", mode="determinate")
        self.progress_bar.pack(side="right", fill="x", expand=True, padx=(20, 0))

        # Action Buttons Row
        btn_frame = tk.Frame(action_card, bg=BG_CARD)
        btn_frame.pack(fill="x", pady=(2, 0))

        from ui.theme import bind_hover_effect

        self.btn_start = tk.Button(
            btn_frame, text="▶ Start Automation", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            activeforeground="white", bd=0, padx=16, pady=5, cursor="hand2",
            command=self.on_start_clicked
        )
        self.btn_start.pack(side="left", padx=(0, 10))
        bind_hover_effect(self.btn_start, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

        self.btn_verify = tk.Button(
            btn_frame, text="✓ Verify Results", font=FONT_BODY_BOLD,
            bg=ACCENT_INFO, fg="white", activebackground=ACCENT_INFO_HOVER,
            activeforeground="white", bd=0, padx=16, pady=5, cursor="hand2",
            command=self.on_verify_clicked
        )
        self.btn_verify.pack(side="left", padx=5)
        bind_hover_effect(self.btn_verify, ACCENT_INFO, ACCENT_INFO_HOVER)

        self.btn_abort = tk.Button(
            btn_frame, text="■ Abort Process", font=FONT_BODY_BOLD,
            bg=ACCENT_DANGER, fg="white", activebackground=ACCENT_DANGER_HOVER,
            activeforeground="white", bd=0, padx=16, pady=5, cursor="hand2",
            state="disabled", command=self.on_abort_clicked
        )
        self.btn_abort.pack(side="right")
        bind_hover_effect(self.btn_abort, ACCENT_DANGER, ACCENT_DANGER_HOVER)

        # 3. Main Tabbed Notebook View
        from ui.theme import apply_notebook_style
        apply_notebook_style()

        nb_wrapper = tk.Frame(self, bg=BG_MAIN)
        nb_wrapper.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        self.notebook = ttk.Notebook(nb_wrapper)
        self.notebook.pack(fill="both", expand=True)

        # --- TAB 1: Setup & Configuration ---
        self.tab_config = tk.Frame(self.notebook, bg=BG_CARD)
        self.notebook.add(self.tab_config, text=" ⚙️ Setup & Configuration ")

        # 1. Pinned Task Queue Panel at the BOTTOM of Tab 1 (Always visible, fixed position at tab bottom)
        self.queue_card = tk.LabelFrame(
            self.tab_config, text=" 📋 Task Queue (Sequential Execution) ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=12, pady=8
        )
        self.queue_card.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        q_tree_frame = tk.Frame(self.queue_card, bg=BG_CARD)
        q_tree_frame.pack(fill="x", pady=(0, 6))

        self.tv_queue = ttk.Treeview(
            q_tree_frame, columns=("idx", "desc", "status", "verified"), show="headings", height=3
        )
        self.tv_queue.heading("idx", text="#")
        self.tv_queue.heading("desc", text="Task Credentials & Input Details")
        self.tv_queue.heading("status", text="Status")
        self.tv_queue.heading("verified", text="Verified")

        self.tv_queue.column("idx", width=35, anchor="center")
        self.tv_queue.column("desc", width=420, anchor="w")
        self.tv_queue.column("status", width=100, anchor="center")
        self.tv_queue.column("verified", width=90, anchor="center")

        q_scroll = tk.Scrollbar(q_tree_frame, orient="vertical", command=self.tv_queue.yview)
        self.tv_queue.configure(yscrollcommand=q_scroll.set)
        q_scroll.pack(side="right", fill="y")
        self.tv_queue.pack(fill="x", expand=True)

        q_btn_frame = tk.Frame(self.queue_card, bg=BG_CARD)
        q_btn_frame.pack(fill="x")

        tk.Button(
            q_btn_frame, text="➕ Add Current Inputs to Queue", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", bd=0, padx=12, pady=4, cursor="hand2",
            command=self.add_inputs_to_queue
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            q_btn_frame, text="❌ Remove Selected", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=10, pady=4, cursor="hand2",
            command=self.remove_selected_queue_task
        ).pack(side="left", padx=4)

        tk.Button(
            q_btn_frame, text="🧹 Clear Queue", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=10, pady=4, cursor="hand2",
            command=self.clear_queue
        ).pack(side="left", padx=4)

        self.lbl_queue_status = tk.Label(
            q_btn_frame, text="Queue is empty (Single-task run if started without queueing).",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="e"
        )
        self.lbl_queue_status.pack(side="right", padx=4)

        # 2. Top Scrollable Container for Inputs (Fills remaining space above queue_card; scrollbar activates dynamically if inputs exceed height)
        inputs_wrapper = tk.Frame(self.tab_config, bg=BG_CARD)
        inputs_wrapper.pack(side="top", fill="both", expand=True, padx=14, pady=(12, 4))

        self.config_canvas = tk.Canvas(inputs_wrapper, bg=BG_CARD, highlightthickness=0, bd=0)
        self.config_scrollbar = ttk.Scrollbar(inputs_wrapper, orient="vertical", command=self.config_canvas.yview)

        self.inputs_container = tk.Frame(self.config_canvas, bg=BG_CARD)

        def _update_scroll_region(event=None):
            self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all"))
            self.inputs_container.update_idletasks()
            content_h = self.inputs_container.winfo_reqheight()
            canvas_h = self.config_canvas.winfo_height()
            if content_h > canvas_h and canvas_h > 1:
                if not self.config_scrollbar.winfo_ismapped():
                    self.config_scrollbar.pack(side="right", fill="y")
            else:
                if self.config_scrollbar.winfo_ismapped():
                    self.config_scrollbar.pack_forget()

        def _on_canvas_configure(event):
            self.config_canvas.itemconfig(canvas_window, width=event.width)
            _update_scroll_region()

        canvas_window = self.config_canvas.create_window((0, 0), window=self.inputs_container, anchor="nw")
        self.inputs_container.bind("<Configure>", _update_scroll_region)
        self.config_canvas.bind("<Configure>", _on_canvas_configure)
        self.config_canvas.configure(yscrollcommand=self.config_scrollbar.set)

        def _on_mousewheel(event):
            content_h = self.inputs_container.winfo_reqheight()
            canvas_h = self.config_canvas.winfo_height()
            if content_h > canvas_h:
                self.config_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mw(e):
            self.config_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mw(e):
            self.config_canvas.unbind_all("<MouseWheel>")

        self.config_canvas.bind("<Enter>", _bind_mw)
        self.config_canvas.bind("<Leave>", _unbind_mw)

        self.config_canvas.pack(side="left", fill="both", expand=True)

        # --- TAB 2: Live Execution & Activity Log ---
        self.tab_execution = tk.Frame(self.notebook, bg=BG_CARD, padx=14, pady=10)
        self.notebook.add(self.tab_execution, text=" 📊 Live Activity & Logs ")

        # Top section of Tab 2: Live Progress KPIs
        self.kpi_container = tk.LabelFrame(
            self.tab_execution, text=" Live Progress & Performance KPIs ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=14, pady=6
        )
        self.kpi_container.pack(fill="x", pady=(0, 8))
        self._build_kpi_panel()

        # Middle section of Tab 2: Real-time Time Series Telemetry Graph
        self.telemetry_graph = TimeSeriesTelemetryGraph(
            self.tab_execution,
            title="Live Upload Telemetry & Throughput Time-Series",
            height=160
        )
        self.telemetry_graph.pack(fill="x", pady=(0, 8))

        # Bottom section of Tab 2: Console Log
        log_container = tk.LabelFrame(
            self.tab_execution, text=" Live Activity Console ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=12, pady=6
        )
        log_container.pack(fill="both", expand=True, pady=(0, 0))

        self.txt_log = scrolledtext.ScrolledText(
            log_container, height=8, wrap="word", state="disabled",
            font=FONT_LOG, bg=BG_LOG, fg="#d4d4d4", insertbackground="#d4d4d4", bd=0
        )
        self.txt_log.pack(fill="both", expand=True)

        self.txt_log.tag_config("INFO", foreground="#58a6ff")
        self.txt_log.tag_config("WARNING", foreground="#d29922")
        self.txt_log.tag_config("ERROR", foreground="#f85149")
        self.txt_log.tag_config("DEBUG", foreground="#8b949e")

        log_controls = tk.Frame(log_container, bg=BG_CARD)
        log_controls.pack(fill="x", pady=(4, 0))

        self.btn_clear_console = tk.Button(
            log_controls, text="Clear Console", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=10, pady=3,
            command=self.clear_console, cursor="hand2"
        )
        self.btn_clear_console.pack(side="left")

        self.btn_open_log = tk.Button(
            log_controls, text="📄 Open Log File", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=10, pady=3,
            command=self.open_log_file, cursor="hand2"
        )
        self.btn_open_log.pack(side="left", padx=(8, 0))

        self.btn_open_output = tk.Button(
            log_controls, text="📂 Open Output Folder", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=10, pady=3,
            command=self.open_output_folder, cursor="hand2"
        )
        self.btn_open_output.pack(side="left", padx=(8, 0))

    def _build_kpi_panel(self):
        """Construct a 2-column live statistics grid with definitive size & time KPIs."""
        left_col = tk.Frame(self.kpi_container, bg=BG_CARD)
        left_col.grid(row=0, column=0, sticky="nw", padx=(0, 20))
        right_col = tk.Frame(self.kpi_container, bg=BG_CARD)
        right_col.grid(row=0, column=1, sticky="nw")

        LABEL_W, VAL_W = 24, 28

        def add_kpi_row(parent, row, label_text):
            tk.Label(parent, text=label_text, font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w", width=LABEL_W, justify="left").grid(row=row, column=0, sticky="w", pady=1)
            val_lbl = tk.Label(parent, text="-", font=FONT_BODY, fg=TEXT_ACCENT, bg=BG_CARD, anchor="w", width=VAL_W, justify="left")
            val_lbl.grid(row=row, column=1, sticky="w", padx=5, pady=1)
            return val_lbl

        # Left Column: Workload & Volume Metrics
        self.kpi_students = add_kpi_row(left_col, 0, "Students / Items Traversed:")
        self.kpi_files_done = add_kpi_row(left_col, 1, "Files / Tasks Processed:")
        self.kpi_data_volume = add_kpi_row(left_col, 2, "Data Volume Processed:")
        self.kpi_current_student = add_kpi_row(left_col, 3, "Current Student ID:")
        self.kpi_current_category = add_kpi_row(left_col, 4, "Current Stage / Folder:")
        self.kpi_current_file = add_kpi_row(left_col, 5, "In-Flight File & Size:")

        # Right Column: Performance & Time KPIs
        self.kpi_start_time = add_kpi_row(right_col, 0, "Automation Start:")
        self.kpi_now_time = add_kpi_row(right_col, 1, "Current Date & Time:")
        self.kpi_elapsed = add_kpi_row(right_col, 2, "Elapsed Duration:")
        self.kpi_transfer_speed = add_kpi_row(right_col, 3, "Transfer Throughput:")
        self.kpi_avg_speed = add_kpi_row(right_col, 4, "Item Processing Speed:")
        self.kpi_eta = add_kpi_row(right_col, 5, "Est. Remaining & ETA:")

        self.kpi_container.columnconfigure(0, weight=1)
        self.kpi_container.columnconfigure(1, weight=1)

    def update_kpis(self, **kwargs):
        """Update any metric values, calculate throughput, and refresh telemetry."""
        import time as _t
        now_ts = _t.time()

        if kwargs.get('total_students') is not None:
            self.total_students = int(kwargs['total_students'])
        if kwargs.get('students_traversed') is not None:
            self.students_traversed = int(kwargs['students_traversed'])
        if 'current_student_id' in kwargs and kwargs['current_student_id'] is not None:
            self.current_student_id = str(kwargs['current_student_id'])
        if 'current_category' in kwargs and kwargs['current_category'] is not None:
            self.current_category = str(kwargs['current_category'])
        if kwargs.get('files_done') is not None:
            self.files_done = int(kwargs['files_done'])
        if kwargs.get('files_total') is not None:
            self.files_total = int(kwargs['files_total'])
        if kwargs.get('total_bytes') is not None:
            self.total_bytes = int(kwargs['total_bytes'])
            if hasattr(self, 'telemetry_graph'):
                self.telemetry_graph.set_total_bytes(self.total_bytes)
        if kwargs.get('bytes_done') is not None:
            self.bytes_done = int(kwargs['bytes_done'])
            self.speed_history.append((now_ts, self.bytes_done))
            # Keep rolling window of last 10 seconds
            self.speed_history = [(ts, b) for ts, b in self.speed_history if now_ts - ts <= 10.0]
            if len(self.speed_history) >= 2:
                dt = max(0.1, self.speed_history[-1][0] - self.speed_history[0][0])
                db = max(0, self.speed_history[-1][1] - self.speed_history[0][1])
                self.rolling_speed_bps = db / dt
        if 'current_file_name' in kwargs and kwargs['current_file_name'] is not None:
            self.current_file_name = str(kwargs['current_file_name'])
        if kwargs.get('current_file_size') is not None:
            self.current_file_size = int(kwargs['current_file_size'])
        if kwargs.get('transfer_rate_bps') is not None:
            self.transfer_rate_bps = float(kwargs['transfer_rate_bps'])

        # Feed data point to telemetry graph if telemetry is active
        if hasattr(self, 'telemetry_graph') and ('bytes_done' in kwargs or 'files_done' in kwargs):
            file_name = kwargs.get('current_file_name')
            self.telemetry_graph.add_data_point(
                cumulative_bytes=self.bytes_done,
                speed_bps=self.rolling_speed_bps if self.rolling_speed_bps > 0 else None,
                file_name=file_name,
                total_bytes=self.total_bytes
            )

        self.after(0, self._refresh_kpi_display)

    def _refresh_kpi_display(self):
        try:
            # 1. Students / Items
            if self.total_students > 0:
                pct = (self.students_traversed / self.total_students) * 100
                self.kpi_students.config(text=f"{self.students_traversed} / {self.total_students} ({pct:.0f}%)")
            else:
                self.kpi_students.config(text=f"{self.students_traversed} / {self.total_students}")

            # 2. Files
            if self.files_total > 0:
                f_pct = (self.files_done / self.files_total) * 100
                self.kpi_files_done.config(text=f"{self.files_done} / {self.files_total} ({f_pct:.0f}%)")
            else:
                self.kpi_files_done.config(text=str(self.files_done))

            # 3. Data Volume
            if self.total_bytes > 0:
                v_pct = (self.bytes_done / self.total_bytes) * 100
                self.kpi_data_volume.config(text=f"{format_bytes(self.bytes_done)} / {format_bytes(self.total_bytes)} ({v_pct:.1f}%)")
            elif self.bytes_done > 0:
                self.kpi_data_volume.config(text=format_bytes(self.bytes_done))
            else:
                self.kpi_data_volume.config(text="0 B")

            # 4. Student & Category & In-flight File
            self.kpi_current_student.config(text=self.current_student_id)
            self.kpi_current_category.config(text=self.current_category)

            if self.current_file_name and self.current_file_name != "-":
                if self.current_file_size > 0:
                    self.kpi_current_file.config(text=f"{self.current_file_name} ({format_bytes(self.current_file_size)})")
                else:
                    self.kpi_current_file.config(text=self.current_file_name)
            else:
                self.kpi_current_file.config(text="-")

            # 5. Start Time
            if self.start_time:
                self.kpi_start_time.config(text=self.start_time.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                self.kpi_start_time.config(text="-")

            # 6. Performance & Speed Calculations
            if self.start_time:
                now = datetime.datetime.now()
                elapsed_sec = max(0.1, (now - self.start_time).total_seconds())

                # Data Transfer Speed
                if self.bytes_done > 0 and elapsed_sec > 0:
                    overall_speed = self.bytes_done / elapsed_sec
                    mb_done = self.bytes_done / (1024 * 1024)
                    sec_per_mb = (elapsed_sec / mb_done) if mb_done > 0.01 else 0
                    display_speed = self.rolling_speed_bps if self.rolling_speed_bps > 0 else overall_speed
                    if sec_per_mb > 0:
                        self.kpi_transfer_speed.config(text=f"{format_speed_bps(display_speed)} ({sec_per_mb:.2f}s/MB)")
                    else:
                        self.kpi_transfer_speed.config(text=format_speed_bps(display_speed))
                else:
                    self.kpi_transfer_speed.config(text="- B/s")

                # Item Processing Speed
                if self.files_done > 0:
                    s_per_file = elapsed_sec / self.files_done
                    self.kpi_avg_speed.config(text=f"{s_per_file:.2f}s/file")
                elif self.students_traversed > 0:
                    avg = elapsed_sec / self.students_traversed
                    self.kpi_avg_speed.config(text=f"{format_timedelta(datetime.timedelta(seconds=avg))}/record")
                else:
                    self.kpi_avg_speed.config(text="-")
            else:
                self.kpi_transfer_speed.config(text="- B/s")
                self.kpi_avg_speed.config(text="-")
        except Exception:
            pass

    def create_file_picker_row(self, parent, row: int, label: str, variable_setter, file_types=None, is_directory=False):
        tk.Label(parent, text=label, font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))

        val_lbl = tk.Label(
            parent, text="No path selected", font=FONT_BODY,
            fg=TEXT_SECONDARY, bg=BG_CARD_ALT, anchor="w", wraplength=400,
            justify="left", padx=10, pady=5, bd=0
        )
        val_lbl.grid(row=row, column=1, sticky="ew", padx=10, pady=4)

        def browse():
            if is_directory:
                path = tk.filedialog.askdirectory()
            else:
                path = tk.filedialog.askopenfilename(filetypes=file_types or [("All Files", "*.*")])
            if path:
                val_lbl.config(text=os.path.basename(path) if not is_directory else path, fg=TEXT_PRIMARY)
                variable_setter(path)

        btn = tk.Button(parent, text="Browse...", font=FONT_BODY_BOLD, bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=12, pady=4, cursor="hand2", command=browse)
        btn.grid(row=row, column=2, sticky="e", pady=4, padx=(10, 0))

        parent.columnconfigure(1, weight=1)
        return val_lbl

    def log(self, level: str, message: str):
        def append():
            self.txt_log.configure(state='normal')
            self.txt_log.insert('end', f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [{level.upper():<7}] {message}\n", level.upper())
            self.txt_log.see('end')
            self.txt_log.configure(state='disabled')

        self.after(0, append)

    def clear_console(self):
        self.txt_log.configure(state='normal')
        self.txt_log.delete('1.0', 'end')
        self.txt_log.configure(state='disabled')

    def open_log_file(self):
        import subprocess
        import glob
        # Each run now writes its own timestamped debug log (e.g.
        # 'automation_debug_20260814_153012.log') instead of every run
        # appending to one fixed 'automation_debug.log', so open whichever
        # one is most recent instead of a single hardcoded filename.
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
            messagebox.showinfo("Log File", "No log file found yet. Run an automation first.")

    def open_output_folder(self):
        import subprocess
        folder = self._log_dir
        if folder and os.path.isdir(folder):
            subprocess.Popen(["explorer", os.path.normpath(folder)])
        else:
            messagebox.showinfo("Output Folder", "No output folder selected yet.")

    def update_status(self, text: str, is_error: bool = False):
        color = ACCENT_DANGER if is_error else TEXT_ACCENT
        self.after(0, lambda: self.lbl_status.config(text=f"Status: {text}", fg=color))

    def set_progress(self, current: int, total: int | None = None):
        def _update():
            if total:
                self.progress_bar.config(maximum=total)
            self.progress_bar.config(value=current)
        self.after(0, _update)

    def set_running_state(self, running: bool):
        def _apply():
            self.is_running = running
            if running:
                self.btn_start.config(state="disabled")
                self.btn_verify.config(state="disabled")
                self.btn_abort.config(state="normal")
                self.start_time = datetime.datetime.now()
                self.end_time = None
                self.students_traversed = 0
                self.files_done = 0
                self.bytes_done = 0
                self.current_file_size = 0
                self.current_file_name = "-"
                self.speed_history.clear()
                self.rolling_speed_bps = 0.0
                if hasattr(self, 'telemetry_graph'):
                    self.telemetry_graph.reset(total_bytes=self.total_bytes)
                self._refresh_kpi_display()
                self.notebook.select(self.tab_execution)
            else:
                self.btn_start.config(state="normal")
                self.btn_verify.config(state="normal")
                self.btn_abort.config(state="disabled")
                self.end_time = datetime.datetime.now()
                self._refresh_kpi_display()
        self.after(0, _apply)

    def _tick_clock(self):
        try:
            now = datetime.datetime.now()
            self.kpi_now_time.config(text=now.strftime("%Y-%m-%d %H:%M:%S"))

            if self.is_running and self.start_time:
                elapsed = now - self.start_time
                elapsed_sec = max(0.1, elapsed.total_seconds())
                self.kpi_elapsed.config(text=format_timedelta(elapsed) + " (running)")

                # Definitive File-Size-Based ETA calculation
                if self.total_bytes > 0 and self.bytes_done > 0:
                    rem_bytes = max(0, self.total_bytes - self.bytes_done)
                    overall_speed = self.bytes_done / elapsed_sec
                    effective_speed = (overall_speed * 0.4 + self.rolling_speed_bps * 0.6) if self.rolling_speed_bps > 0 else overall_speed
                    if effective_speed > 0:
                        rem_sec = rem_bytes / effective_speed
                        eta = now + datetime.timedelta(seconds=rem_sec)
                        self.kpi_eta.config(text=f"~{format_time_remaining(rem_sec)} ({eta.strftime('%H:%M:%S')})")
                    else:
                        self.kpi_eta.config(text="Calculating...")
                elif self.students_traversed > 0 and self.total_students > 0:
                    avg_sec_per_student = elapsed_sec / self.students_traversed
                    rem_sec = avg_sec_per_student * (self.total_students - self.students_traversed)
                    eta = now + datetime.timedelta(seconds=rem_sec)
                    self.kpi_eta.config(text=f"~{format_time_remaining(rem_sec)} ({eta.strftime('%H:%M:%S')})")
                elif self.files_done > 0 and self.files_total > 0:
                    avg_sec_per_file = elapsed_sec / self.files_done
                    rem_sec = avg_sec_per_file * (self.files_total - self.files_done)
                    eta = now + datetime.timedelta(seconds=rem_sec)
                    self.kpi_eta.config(text=f"~{format_time_remaining(rem_sec)} ({eta.strftime('%H:%M:%S')})")
                else:
                    self.kpi_eta.config(text="Calculating...")
            elif self.start_time and self.end_time:
                elapsed = self.end_time - self.start_time
                self.kpi_elapsed.config(text=format_timedelta(elapsed) + " (done)")
                self.kpi_eta.config(text=self.end_time.strftime("%Y-%m-%d %H:%M:%S") + " (done)")
        except Exception:
            pass
        finally:
            try:
                self.after(1000, self._tick_clock)
            except Exception:
                pass


    # ------------------------------------------------------------------
    # Queue Management Methods & Hooks
    # ------------------------------------------------------------------

    def get_task_from_inputs(self, silent: bool = False) -> QueueTask | None:
        """Override in child frames to construct a QueueTask from form inputs."""
        return None

    def run_single_task(self, task: QueueTask) -> bool:
        """Override in child frames to execute a single task."""
        return True

    def verify_single_task(self, task: QueueTask):
        """Override in child frames to verify a single task."""
        pass

    def add_inputs_to_queue(self):
        """Builds a task from active input fields and adds it to the Queue."""
        task = self.get_task_from_inputs(silent=False)
        if task:
            self.queue_tasks.append(task)
            self.refresh_queue_display()
            self.log("info", f"[QUEUE] Added task to queue: {task.label}")

    def remove_selected_queue_task(self):
        """Removes the selected task from the queue."""
        selected = self.tv_queue.selection()
        if not selected:
            messagebox.showinfo("Queue", "Select a task in the queue table to remove.")
            return

        for item_id in selected:
            values = self.tv_queue.item(item_id, "values")
            if values:
                try:
                    idx = int(values[0]) - 1
                    if 0 <= idx < len(self.queue_tasks):
                        removed = self.queue_tasks.pop(idx)
                        self.log("info", f"[QUEUE] Removed task: {removed.label}")
                except Exception:
                    pass

        self.refresh_queue_display()

    def clear_queue(self):
        """Clears all tasks from the queue."""
        if self.queue_tasks and messagebox.askyesno("Clear Queue", "Clear all tasks from the queue?"):
            self.queue_tasks.clear()
            self.paused_queue_idx = None
            self.refresh_queue_display()
            self.log("info", "[QUEUE] Cleared task queue.")

    def refresh_queue_display(self):
        """Refreshes the Queue Treeview table and status label."""
        for item in self.tv_queue.get_children():
            self.tv_queue.delete(item)

        for i, task in enumerate(self.queue_tasks, start=1):
            ver_text = "✓ Yes" if task.verified else "No"
            self.tv_queue.insert("", "end", values=(i, task.label, task.status, ver_text))

        count = len(self.queue_tasks)
        if count == 0:
            self.lbl_queue_status.config(text="Queue is empty (Single-task run if started without queueing).")
        else:
            paused_str = f" | Paused at task {self.paused_queue_idx+1}" if self.paused_queue_idx is not None else ""
            self.lbl_queue_status.config(text=f"Total Tasks: {count}{paused_str}")

    # ------------------------------------------------------------------
    # Sequential Execution Engine
    # ------------------------------------------------------------------

    def on_start_clicked(self):
        """Handler when Start Automation button is clicked."""
        if self.is_running:
            return

        # If queue is empty, attempt to build task 1 from current form inputs
        if not self.queue_tasks:
            task = self.get_task_from_inputs(silent=False)
            if not task:
                return
            self.queue_tasks.append(task)
            self.refresh_queue_display()

        self.start_queue_execution()

    def start_queue_execution(self):
        """Launches sequential queue execution in a background thread."""
        self.set_running_state(True)
        self.abort_requested = False

        def runner():
            try:
                start_idx = self.paused_queue_idx if self.paused_queue_idx is not None else 0
                self.paused_queue_idx = None
                discarded = False

                for idx in range(start_idx, len(self.queue_tasks)):
                    task = self.queue_tasks[idx]
                    if self.abort_requested or discarded:
                        break

                    task.status = "In Progress"
                    self.after(0, self.refresh_queue_display)
                    self.update_status(f"Running Task {idx+1}/{len(self.queue_tasks)}: {task.label}")
                    self.log("info", f"[QUEUE] === Running Task {idx+1}/{len(self.queue_tasks)}: {task.label} ===")

                    success = self.run_single_task(task)

                    if self.abort_requested:
                        task.status = "Aborted"
                        self.after(0, self.refresh_queue_display)
                        break

                    if success:
                        task.status = "Completed"
                        self.log("info", f"[QUEUE] Task {idx+1} completed successfully.")
                    else:
                        task.status = "Failed"
                        self.log("error", f"[QUEUE] Task {idx+1} failed.")

                    self.after(0, self.refresh_queue_display)

                    # Inter-task verification prompt if more tasks remain
                    if idx < len(self.queue_tasks) - 1:
                        choice, is_timeout = self._show_inter_task_modal(task, idx + 1, len(self.queue_tasks))

                        if is_timeout:
                            action = settings.get("inter_task_inactivity_action", "close_app")
                            self.log("warning", f"[QUEUE] Inactivity timeout reached (User Away). Action: {action}")
                            if action == "close_app":
                                self.log("warning", "[QUEUE] Closing application safely due to inactivity session timeout.")
                                self.update_status("Closed due to inactivity timeout", is_error=True)
                                self.after(150, self.winfo_toplevel().destroy)
                                break
                            elif action == "pause_queue":
                                self.paused_queue_idx = idx + 1
                                self.after(0, self.refresh_queue_display)
                                self.log("info", f"[QUEUE] Queue paused due to inactivity timeout. Click 'Start Automation' to resume.")
                                self.update_status(f"Paused at Task {idx+2}/{len(self.queue_tasks)} (Inactivity). Click '▶ Start Automation' to continue.")
                                break
                            elif action == "stop_automation":
                                self.log("warning", "[QUEUE] Automation stopped due to inactivity timeout.")
                                self.update_status("Stopped due to inactivity timeout (User Away)", is_error=True)
                                break
                            elif action == "proceed_next":
                                self.log("info", "[QUEUE] Automatically proceeding to next task due to inactivity timeout.")
                                task.verified = False
                                # Continue to next queued task

                        if choice == 1:  # Verify current task & discard next tasks
                            self.log("info", "[QUEUE] Choice 1 selected: Verifying current task & discarding remaining tasks.")
                            self.verify_single_task(task)
                            task.verified = True
                            discarded = True
                            for rem_i in range(idx + 1, len(self.queue_tasks)):
                                self.queue_tasks[rem_i].status = "Discarded"
                            self.after(0, self.refresh_queue_display)
                            break

                        elif choice == 2:  # Verify and proceed to next task
                            self.log("info", "[QUEUE] Choice 2 selected: Verifying current task and proceeding to next task.")
                            self.verify_single_task(task)
                            task.verified = True
                            self.after(0, self.refresh_queue_display)

                        elif choice == 3:  # Proceed without verifying
                            self.log("info", "[QUEUE] Choice 3 selected: Proceeding to next task without verifying.")
                            task.verified = False

                        elif choice == 4:  # Verify this task & Pause (Wait for Start Automation)
                            self.log("info", "[QUEUE] Choice 4 selected: Verifying task and pausing queue.")
                            self.verify_single_task(task)
                            task.verified = True
                            self.paused_queue_idx = idx + 1
                            self.after(0, self.refresh_queue_display)
                            self.log("info", f"[QUEUE] Queue paused at task {idx+2}/{len(self.queue_tasks)}. Click 'Start Automation' to resume.")
                            self.update_status(f"Paused at Task {idx+2}/{len(self.queue_tasks)}. Click '▶ Start Automation' to continue.")
                            break

                # Post-queue final verification check
                if not self.abort_requested and not discarded and self.paused_queue_idx is None:
                    unverified_tasks = [t for t in self.queue_tasks if t.status == "Completed" and not t.verified]
                    if unverified_tasks:
                        do_verify = self._prompt_final_verification(len(unverified_tasks))
                        if do_verify:
                            self.log("info", f"[QUEUE] Verifying {len(unverified_tasks)} unverified completed task(s)...")
                            for u_task in unverified_tasks:
                                self.verify_single_task(u_task)
                                u_task.verified = True
                            self.after(0, self.refresh_queue_display)

                    self.update_status("Queue Execution Finished ✓")
                    self.log("info", "[QUEUE] All queue execution workflows completed.")

            except Exception as e:
                import traceback
                self.log("error", f"[QUEUE] Fatal error during queue execution: {e}")
                self.log("debug", traceback.format_exc())
                self.update_status(f"Queue Error: {e}", is_error=True)
            finally:
                self.set_running_state(False)

        threading.Thread(target=runner, daemon=True).start()

    def _show_inter_task_modal(self, task: QueueTask, completed_num: int, total_count: int):
        evt = threading.Event()
        res = {'choice': 3, 'timeout': False}
        timeout_mins = settings.get("inter_task_timeout_minutes", 15)
        inact_action = settings.get("inter_task_inactivity_action", "close_app")

        def _open():
            dlg = InterTaskDialog(self, task, completed_num, total_count, timeout_minutes=timeout_mins, inactivity_action=inact_action)
            res['choice'] = dlg.choice
            res['timeout'] = dlg.timeout_occurred
            evt.set()

        self.after(0, _open)
        evt.wait()
        return res['choice'], res['timeout']

    def _prompt_final_verification(self, count: int) -> bool:
        evt = threading.Event()
        res = {'answer': False}

        def _ask():
            try:
                res['answer'] = messagebox.askyesno(
                    "Verify Remaining Tasks",
                    f"All tasks in queue completed!\n\n"
                    f"There are {count} completed task(s) that were not verified earlier.\n\n"
                    "Would you like to run verification on these tasks now?"
                )
            except Exception:
                res['answer'] = False
            finally:
                evt.set()

        self.after(0, _ask)
        evt.wait(timeout=60)
        return res['answer']

    def on_verify_clicked(self):
        """Runs verification across all tasks in the queue asynchronously."""
        if self.is_running:
            return

        tasks_to_verify = [t for t in self.queue_tasks if t.status in ("Completed", "Pending")]
        if not tasks_to_verify:
            task = self.get_task_from_inputs(silent=True)
            if task:
                tasks_to_verify = [task]

        if not tasks_to_verify:
            messagebox.showinfo("Verify Results", "No queued tasks to verify. Add tasks or fill input fields first.")
            return

        def _verify_worker():
            self.set_running_state(True)
            self.abort_requested = False
            self.log("info", f"[VERIFY] Verifying {len(tasks_to_verify)} task(s)...")
            try:
                for task in tasks_to_verify:
                    if self.abort_requested:
                        self.log("warning", "[VERIFY] Verification aborted by user.")
                        break
                    self.verify_single_task(task)
                    task.verified = True
                self.after(0, self.refresh_queue_display)
            finally:
                self.set_running_state(False)
                self.update_status("Verification complete.")

        t = threading.Thread(target=_verify_worker, daemon=True)
        t.start()

    def on_abort_clicked(self):
        if self.is_running:
            self.abort_requested = True
            self.btn_abort.config(state="disabled")
            self.update_status("Aborting process...")
            self.log("warning", "Abort requested by user.")
