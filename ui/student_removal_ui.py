"""
ui/student_removal_ui.py
========================
Tkinter UI frame for the Student Removal tool.

The user provides:
  - Credentials file (.txt)  — login URL, username, password, role, XPaths
  - A CSV file with columns: studentId, link
  - Number of parallel Chrome workers

Automation navigates to class pages, finds the student, clicks Remove,
handles confirmation dialogs, and saves the class to commit changes.
"""

import os
import uuid
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import (
    BG_CARD, BG_CARD_ALT, FONT_BODY_BOLD, FONT_SMALL, FONT_SMALL_BOLD,
    TEXT_PRIMARY, TEXT_MUTED, ACCENT_INFO, ACCENT_DANGER
)
from core.utils import load_credentials
from core.settings_manager import settings
from modules.student_removal import StudentRemover, MODULE_ID


class StudentRemovalUI(BaseToolFrame):
    """UI frame for the Student Removal automation module."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title="Student Removal",
            description=(
                "Removes students from class lists in PCR Educator and saves each class. "
                "CSV columns: studentId, link."
            ),
        )

        self.cred_path = ""
        self.csv_path = ""

        remembered_cred = settings.get("student_removal_last_cred_path", "")
        remembered_csv = settings.get("student_removal_last_csv_path", "")

        if remembered_cred and os.path.isfile(remembered_cred):
            self.cred_path = remembered_cred
        if remembered_csv and os.path.isfile(remembered_csv):
            self.csv_path = remembered_csv

        self.devtools_var = tk.BooleanVar(value=settings.get("auto_open_devtools", False))
        self.workers_var = tk.IntVar(value=1)
        self.skip_completed_var = tk.BooleanVar(value=True)

        self.remover = StudentRemover()
        self._populate_inputs()
        self._apply_remembered_labels()

    def _populate_inputs(self):
        # Row 0 — Credentials file
        self.lbl_cred = self.create_file_picker_row(
            self.inputs_container, 0,
            "Credentials File (.txt):",
            self._set_cred_path,
            file_types=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )

        # Row 1 — Removal CSV
        self.lbl_csv = self.create_file_picker_row(
            self.inputs_container, 1,
            "Removal Links CSV (.csv):",
            self._set_csv_path,
            file_types=[("CSV Files", "*.csv"), ("All Files", "*.*")],
        )

        # Row 2 — Banner
        banner = tk.Label(
            self.inputs_container,
            text=(
                "• Required CSV Columns: studentId, link\n"
                "• Action: Locates student in class (Regular/Waiting/Withdrawn), clicks Remove, "
                "confirms alerts/dialogs, and saves the class to commit changes.\n"
                "• Efficiency: Automatically batches consecutive students sharing the same class URL "
                "to eliminate redundant page reloads, and supports multi-worker parallel execution."
            ),
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD,
            anchor="w", justify="left", wraplength=680,
        )
        banner.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 6))

        # Row 3 — Execution Options
        opts_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        opts_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 6))

        chk_dev = tk.Checkbutton(
            opts_frame,
            text="Auto-open Chrome DevTools",
            variable=self.devtools_var,
            font=FONT_SMALL,
            fg=TEXT_PRIMARY,
            bg=BG_CARD,
            activebackground=BG_CARD,
            activeforeground=TEXT_PRIMARY,
            selectcolor=BG_CARD_ALT,
        )
        chk_dev.pack(side="left", padx=(0, 16))

        chk_skip = tk.Checkbutton(
            opts_frame,
            text="Skip previously completed removals",
            variable=self.skip_completed_var,
            font=FONT_SMALL,
            fg=TEXT_PRIMARY,
            bg=BG_CARD,
            activebackground=BG_CARD,
            activeforeground=TEXT_PRIMARY,
            selectcolor=BG_CARD_ALT,
        )
        chk_skip.pack(side="left", padx=(0, 16))

        lbl_w = tk.Label(
            opts_frame,
            text="Parallel Workers:",
            font=FONT_SMALL,
            fg=TEXT_PRIMARY,
            bg=BG_CARD,
        )
        lbl_w.pack(side="left", padx=(0, 4))

        spn_w = tk.Spinbox(
            opts_frame,
            from_=1, to=50,
            textvariable=self.workers_var,
            width=4,
            font=FONT_SMALL,
        )
        spn_w.pack(side="left", padx=(0, 4))

        lbl_hint = tk.Label(
            opts_frame,
            text="(1 = sequential, 2–50 = parallel)",
            font=FONT_SMALL,
            fg=TEXT_MUTED,
            bg=BG_CARD,
        )
        lbl_hint.pack(side="left")

    def _set_cred_path(self, path: str):
        self.cred_path = path
        settings.set("student_removal_last_cred_path", path)
        if hasattr(self, "lbl_cred"):
            self.lbl_cred.config(text=os.path.basename(path) if path else "No file selected")

    def _set_csv_path(self, path: str):
        self.csv_path = path
        settings.set("student_removal_last_csv_path", path)
        if hasattr(self, "lbl_csv"):
            self.lbl_csv.config(text=os.path.basename(path) if path else "No file selected")

    def _apply_remembered_labels(self):
        if self.cred_path and hasattr(self, "lbl_cred"):
            self.lbl_cred.config(text=os.path.basename(self.cred_path))
        if self.csv_path and hasattr(self, "lbl_csv"):
            self.lbl_csv.config(text=os.path.basename(self.csv_path))

    def validate_inputs(self) -> bool:
        if not self.cred_path or not os.path.isfile(self.cred_path):
            messagebox.showerror("Missing Credentials", "Please select a valid credentials file.")
            return False
        if not self.csv_path or not os.path.isfile(self.csv_path):
            messagebox.showerror("Missing CSV", "Please select a valid student removal CSV file.")
            return False
        return True

    def build_queue_task(self) -> QueueTask | None:
        if not self.validate_inputs():
            return None
        n = max(1, self.workers_var.get())
        csv_name = os.path.basename(self.csv_path)
        workers_str = f" | {n} workers" if n > 1 else ""
        label = f"Student Removal | CSV: {csv_name}{workers_str}"

        inputs = {
            "cred_path": self.cred_path,
            "csv_path": self.csv_path,
            "devtools": self.devtools_var.get(),
            "n_workers": n,
            "skip_completed": self.skip_completed_var.get(),
        }
        return QueueTask(
            task_id=str(uuid.uuid4())[:8],
            label=label,
            inputs=inputs,
        )

    def run_task(self, task: QueueTask):
        inputs = task.inputs
        config = load_credentials(inputs["cred_path"])
        if not config:
            self.log("error", "Invalid credentials file — could not load config.")
            return

        n_workers = max(1, inputs.get("n_workers", 1))

        def _prog(data: dict):
            p_type = data.get("type")
            if p_type == "total":
                self.set_progress(0, data.get("total", 0))
            elif p_type == "item_done":
                self.set_progress(
                    data.get("completed", 0),
                    data.get("total", 0),
                )

        workers_label = f"parallel ×{n_workers}" if n_workers > 1 else "sequential"
        self.log("info", f"Starting Student Removal ({workers_label})...")

        if n_workers > 1:
            self.remover.run_parallel(
                config=config,
                csv_path=inputs["csv_path"],
                n_workers=n_workers,
                devtools=inputs.get("devtools", False),
                on_progress=_prog,
                abort_flag=lambda: self.abort_requested,
                log=lambda lvl, msg: self.log(lvl, msg),
                skip_completed=inputs.get("skip_completed", True),
            )
        else:
            self.remover.run(
                config=config,
                csv_path=inputs["csv_path"],
                devtools=inputs.get("devtools", False),
                on_progress=_prog,
                abort_flag=lambda: self.abort_requested,
                log=lambda lvl, msg: self.log(lvl, msg),
                skip_completed=inputs.get("skip_completed", True),
            )
