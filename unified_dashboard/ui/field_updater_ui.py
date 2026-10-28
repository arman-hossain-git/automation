"""
ui/field_updater_ui.py
======================
UI frame for the Field Updater tool.

The user provides:
  - A credentials file (.txt)  — login_url, username, password, login_btn_xpath
  - A CSV file whose columns are:
      link, field_1, value_1, field_2, value_2, ...  (unlimited field/value pairs)
  - Number of parallel Chrome workers

The automation logs into the site and then, for every row, navigates to the
page URL and updates the specified fields (text inputs, dropdowns, checkboxes,
radio buttons — all handled automatically by field type detection).
"""

import os
import uuid
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import BG_CARD, BG_CARD_ALT, FONT_BODY_BOLD, FONT_SMALL, TEXT_PRIMARY, TEXT_MUTED
from core.utils import load_credentials
from core.settings_manager import settings
from modules.field_updater import FieldUpdater, parse_field_updater_csv, MODULE_ID


class FieldUpdaterUI(BaseToolFrame):
    """UI frame for the Field Updater automation module."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title="Field Updater",
            description=(
                "Navigates to each page URL in the CSV and updates the specified fields. "
                "Supports text inputs, dropdowns, checkboxes, and radio buttons. "
                "CSV columns: link, field_1, value_1, field_2, value_2, …"
            ),
        )

        self.cred_path = ""
        self.csv_path  = ""

        # Restore last-used paths from settings
        remembered_cred = settings.get("field_updater_last_cred_path", "")
        remembered_csv  = settings.get("field_updater_last_csv_path",  "")

        if remembered_cred and os.path.isfile(remembered_cred):
            self.cred_path = remembered_cred
        if remembered_csv and os.path.isfile(remembered_csv):
            self.csv_path = remembered_csv

        self.devtools_var = tk.BooleanVar(value=settings.get("auto_open_devtools", False))
        self.workers_var  = tk.IntVar(value=1)

        self.updater = FieldUpdater()
        self._populate_inputs()
        self._apply_remembered_labels()

    # ------------------------------------------------------------------
    # Input layout
    # ------------------------------------------------------------------

    def _populate_inputs(self):
        # Row 0 — Credentials file
        self.lbl_cred = self.create_file_picker_row(
            self.inputs_container, 0,
            "Credentials File (.txt):",
            self._set_cred_path,
            file_types=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )

        # Row 1 — CSV file
        self.lbl_csv = self.create_file_picker_row(
            self.inputs_container, 1,
            "Field Update CSV (.csv):",
            self._set_csv_path,
            file_types=[("CSV Files", "*.csv"), ("All Files", "*.*")],
        )

        # Row 2 — Info banner describing the CSV format
        banner = tk.Label(
            self.inputs_container,
            text=(
                "CSV must have a 'link' column plus any number of 'field_N' / 'value_N' column pairs "
                "(e.g. field_1, value_1, field_2, value_2 …). "
                "'field_N' must contain the XPath of the element to update; "
                "'value_N' is the new value (text for inputs, option text for dropdowns, "
                "'true'/'false' for checkboxes)."
            ),
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD,
            anchor="w", justify="left", wraplength=680,
        )
        banner.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 6))

        # Row 3 — DevTools checkbox
        opts_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        opts_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 2))

        tk.Checkbutton(
            opts_frame,
            text="Auto-open DevTools",
            variable=self.devtools_var,
            font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD,
            selectcolor=BG_CARD, activebackground=BG_CARD,
        ).pack(side="left")

        tk.Label(
            self.inputs_container,
            text="Defaults from Settings. Auto-enabled for sites that require it.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 4))

        # Row 5 — Workers spinbox
        workers_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        workers_frame.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tk.Label(
            workers_frame, text="Parallel Chrome Workers:",
            font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD,
        ).pack(side="left", padx=(0, 10))

        tk.Spinbox(
            workers_frame, from_=1, to=50, width=6,
            textvariable=self.workers_var,
            bg="#2b2b2b", fg="#ffffff", buttonbackground="#3c3c3c", bd=1, relief="solid",
        ).pack(side="left")

        tk.Label(
            workers_frame,
            text="(1 = sequential, 2–50 = parallel Chrome instances)",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD,
        ).pack(side="left", padx=(10, 0))

    def _apply_remembered_labels(self):
        if self.cred_path:
            self.lbl_cred.config(text=os.path.basename(self.cred_path), fg=TEXT_PRIMARY)
        if self.csv_path:
            self.lbl_csv.config(text=os.path.basename(self.csv_path), fg=TEXT_PRIMARY)

    # ------------------------------------------------------------------
    # Path setters
    # ------------------------------------------------------------------

    def _set_cred_path(self, p: str):
        self.cred_path = p
        settings.set("field_updater_last_cred_path", p)
        settings.save()

    def _set_csv_path(self, p: str):
        self.csv_path = p
        settings.set("field_updater_last_csv_path", p)
        settings.save()

    # ------------------------------------------------------------------
    # Queue task creation
    # ------------------------------------------------------------------

    def get_task_from_inputs(self, silent: bool = False) -> QueueTask | None:
        if not self.cred_path or not self.csv_path:
            if not silent:
                messagebox.showerror(
                    "Missing Fields",
                    "Please select a Credentials File and a Field Update CSV."
                )
            return None

        config = load_credentials(
            self.cred_path,
            required_keys=["username", "password", "login_url", "login_btn_xpath"],
        )
        if not config:
            if not silent:
                messagebox.showerror(
                    "Invalid Credentials File",
                    "Credentials file must include username, password, login_url, and login_btn_xpath.",
                )
            return None

        rows = parse_field_updater_csv(self.csv_path)
        if not rows:
            if not silent:
                messagebox.showerror(
                    "Invalid CSV",
                    "Could not parse any valid rows from the CSV.\n\n"
                    "Required column: link\n"
                    "Field columns: field_1 / value_1, field_2 / value_2, …",
                )
            return None

        n_workers = max(1, self.workers_var.get())
        workers_tag = f" | {n_workers} workers" if n_workers > 1 else ""
        label = (
            f"Creds: {os.path.basename(self.cred_path)} | "
            f"CSV: {os.path.basename(self.csv_path)} ({len(rows)} rows){workers_tag}"
        )

        inputs = {
            "config":    config,
            "csv_path":  self.csv_path,
            "devtools":  self.devtools_var.get(),
            "n_workers": n_workers,
            "cred_path": self.cred_path,
        }

        return QueueTask(task_id=str(uuid.uuid4())[:8], label=label, inputs=inputs)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    def run_single_task(self, task: QueueTask) -> bool:
        inputs = task.inputs
        n_workers = max(1, inputs.get("n_workers", 1))
        self.log(
            "info",
            f"Running Field Updater task "
            f"({'parallel ×' + str(n_workers) if n_workers > 1 else 'sequential'}): "
            f"{task.label}"
        )

        try:
            common_kwargs = dict(
                config=inputs["config"],
                csv_path=inputs["csv_path"],
                devtools=inputs["devtools"],
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log,
            )
            if n_workers > 1:
                self.updater.run_parallel(n_workers=n_workers, **common_kwargs)
            else:
                self.updater.run(**common_kwargs)
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
        res = self.updater.verify_logs(csv_path, log=self.log)
        self.log(
            "info",
            f"[VERIFY] Task: {task.label} → "
            f"Total: {res['total']}, Success: {res['processed']}, "
            f"Failed: {len(res['failed'])}"
        )

        def _show():
            msg = (
                f"Total Log Entries: {res['total']}\n"
                f"Successful: {res['processed']}\n"
                f"Failed: {len(res['failed'])}"
            )
            if res["failed"]:
                msg += f"\n\nFailed Row Keys: {', '.join(res['failed'][:15])}"
            messagebox.showinfo("Field Updater Verification", msg)

        self.after(0, _show)

    # ------------------------------------------------------------------
    # Progress events
    # ------------------------------------------------------------------

    def _handle_progress(self, data: dict):
        t = data.get("type")

        if t == "total":
            total = data.get("total", 0)
            self.set_progress(0, max(1, total))
            self.update_kpis(total_students=total)

        elif t == "item_start":
            idx   = data.get("idx", 0)
            total = data.get("total", self.total_students)
            rid   = data.get("student_id", str(idx + 1))
            link  = data.get("link", "")
            self.update_status(f"Updating row {idx + 1}/{total} (Row {rid})…")
            self.set_progress(idx + 1, max(1, total))
            self.update_kpis(
                students_traversed=idx,
                current_student_id=rid,
                current_category=link[:60] if link else "",
            )

        elif t == "item_done":
            done = data.get("completed", self.students_traversed + 1)
            self.update_kpis(students_traversed=done, files_done=done)

    # ------------------------------------------------------------------
    # Verify button fallback
    # ------------------------------------------------------------------

    def on_verify_clicked(self):
        tasks_to_verify = [t for t in self.queue_tasks if t.status in ("Completed", "Pending")]
        if tasks_to_verify or self.get_task_from_inputs(silent=True):
            super().on_verify_clicked()
            return

        if self.csv_path:
            res = self.updater.verify_logs(self.csv_path, log=self.log)
            messagebox.showinfo(
                "Field Updater Verification",
                f"Total Log Entries: {res['total']}\n"
                f"Successful: {res['processed']}\n"
                f"Failed: {len(res['failed'])}"
            )
        else:
            messagebox.showwarning("Warning", "Select a CSV file first.")
