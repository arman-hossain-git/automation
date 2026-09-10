"""
ui/grade_ui.py
==============
UI frame for Combined Grade Submission tool.
"""

import os
import threading
import tkinter as tk
from tkinter import messagebox

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import BG_CARD, FONT_BODY_BOLD, FONT_SMALL, TEXT_PRIMARY, TEXT_MUTED
from core.utils import load_credentials
from core.settings_manager import settings
from modules.grade_submission import GradeSubmitter


class GradeUI(BaseToolFrame):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="Student Grade Submission",
            description="Automates entering student final grades and submitting scores with dynamic field detection (Adjust Score or Grade dropdown)."
        )

        self.cred_path = ""
        self.csv_path = ""
        # Defaults from the global Settings tab. The automation can still auto-enable
        # this at run time if a site has proven (from past runs) that it needs
        # DevTools even with this box unchecked.
        self.devtools_var = tk.BooleanVar(value=settings.get("auto_open_devtools", False))

        self.submitter = GradeSubmitter()
        self._populate_inputs()

    def _populate_inputs(self):
        self.create_file_picker_row(
            self.inputs_container, 0, "Credentials File (.txt):",
            lambda p: setattr(self, 'cred_path', p),
            file_types=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )

        self.create_file_picker_row(
            self.inputs_container, 1, "Student Links CSV (.csv):",
            lambda p: setattr(self, 'csv_path', p),
            file_types=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )

        devtools_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        devtools_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        tk.Checkbutton(
            devtools_frame, text="Auto-open DevTools",
            variable=self.devtools_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).pack(side="left")
        tk.Label(
            self.inputs_container,
            text="Defaults from Settings. If this site has previously failed with this off, the "
                 "system auto-enables it and notes it in the log.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w", justify="left"
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def get_task_from_inputs(self, silent: bool = False) -> QueueTask:
        if not self.cred_path or not self.csv_path:
            if not silent:
                messagebox.showerror("Missing Fields", "Please select Credentials and Links CSV file.")
            return None

        config = load_credentials(self.cred_path)
        if not config:
            if not silent:
                messagebox.showerror("Invalid Credentials", "Failed to parse credentials file.")
            return None

        label = f"Creds: {os.path.basename(self.cred_path)} | CSV: {os.path.basename(self.csv_path)}"

        inputs = {
            "config": config,
            "csv_path": self.csv_path,
            "devtools": self.devtools_var.get(),
            "cred_path": self.cred_path,
        }

        import uuid
        return QueueTask(task_id=str(uuid.uuid4())[:8], label=label, inputs=inputs)

    def run_single_task(self, task: QueueTask) -> bool:
        inputs = task.inputs
        self.log("info", f"Running Grade Submission task: {task.label}")

        try:
            self.submitter.run(
                config=inputs["config"],
                csv_path=inputs["csv_path"],
                devtools=inputs["devtools"],
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log
            )
            return not self.abort_requested
        except Exception as e:
            import traceback
            self.log("error", f"Error executing task {task.label}: {e}")
            self.log("debug", traceback.format_exc())
            return False

    def verify_single_task(self, task: QueueTask):
        csv_path = task.inputs.get("csv_path", "")
        if not csv_path:
            return

        res = self.submitter.verify_logs(csv_path, log=self.log)
        self.log("info", f"[VERIFY] Task: {task.label} -> Total Rows: {res['total']}, Processed: {res['processed']}, Missing/Failed: {len(res['missing'])}")

    def _handle_progress(self, data: dict):
        t = data.get('type')
        if t == 'total':
            self.set_progress(0, data['total'])
            self.update_kpis(total_students=data['total'])
        elif t == 'item_start':
            self.update_status(f"Processing row {data['idx']+1}/{self.total_students} (Student {data['student_id']})...")
            self.update_kpis(
                students_traversed=data['idx'],
                current_student_id=data['student_id']
            )
        elif t == 'category':
            self.update_kpis(current_category=data['name'])
        elif t == 'item_done':
            self.set_progress(data['completed'])
            self.update_kpis(
                students_traversed=data['completed'],
                files_done=data['completed']
            )

    def on_verify_clicked(self):
        if not self.csv_path:
            messagebox.showwarning("Warning", "Select Links CSV first.")
            return

        res = self.submitter.verify_logs(self.csv_path, log=self.log)
        msg = f"Total Input Rows: {res['total']}\nSuccessfully Processed: {res['processed']}\nMissing/Failed: {len(res['missing'])}"
        if res['missing']:
            msg += f"\n\nMissing IDs: {', '.join(res['missing'][:10])}"
        messagebox.showinfo("Verification Report", msg)
