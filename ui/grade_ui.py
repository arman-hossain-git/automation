"""
ui/grade_ui.py
==============
Unified UI frame for Grades module.
Driven by sub-feature selection from the left navigation panel:
  - Submit Grades (enters final grades, dates, and scores)
  - Un-submit Grades (reverts final grades by marking 'No' and submitting)
"""

import os
import tkinter as tk
from tkinter import messagebox

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import (
    BG_CARD, BG_CARD_ALT, FONT_TITLE, FONT_BODY_BOLD, FONT_BODY,
    FONT_SMALL, FONT_SMALL_BOLD, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_INFO, BORDER_COLOR
)
from core.utils import load_credentials
from core.settings_manager import settings
from modules.grade_submission import GradeSubmitter


class GradeUI(BaseToolFrame):
    """Unified UI frame for Grade Submission and Un-submitting Grades."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title="Student Grade Management",
            description="Manage student grades in PCR Educator. Select a sub-feature in the left panel (Submit Grades or Un-submit Grades) to configure and run."
        )

        self.cred_path = ""
        self.csv_path = ""
        self.devtools_var = tk.BooleanVar(value=settings.get("auto_open_devtools", False))
        self.sub_feature = "submit"  # "submit" or "unsubmit"
        self.total_students = 0

        self.submitter = GradeSubmitter()
        self._populate_inputs()

    def _populate_inputs(self):
        # ------------------------------------------------------------------
        # 1. Active Sub-feature Badge & Information Banner
        # ------------------------------------------------------------------
        self.guide_frame = tk.Frame(self.inputs_container, bg=BG_CARD_ALT, bd=1, relief="solid")
        self.guide_frame.grid(row=0, column=0, columnspan=3, sticky="ew", padx=2, pady=(0, 12))

        self.guide_header = tk.Label(
            self.guide_frame,
            text="",
            font=FONT_SMALL_BOLD,
            fg=ACCENT_INFO,
            bg=BG_CARD_ALT,
            anchor="w"
        )
        self.guide_header.pack(fill="x", padx=10, pady=(8, 3))

        self.guide_body = tk.Label(
            self.guide_frame,
            text="",
            font=FONT_SMALL,
            fg=TEXT_SECONDARY,
            bg=BG_CARD_ALT,
            justify="left",
            anchor="w"
        )
        self.guide_body.pack(fill="x", padx=10, pady=(0, 8))

        # ------------------------------------------------------------------
        # 2. File Picker Rows
        # ------------------------------------------------------------------
        self.create_file_picker_row(
            self.inputs_container, 1, "Credentials File (.txt):",
            lambda p: setattr(self, 'cred_path', p),
            file_types=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )

        self.csv_row_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        self.csv_row_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.lbl_csv_picker = tk.Label(
            self.csv_row_frame,
            text="Grade Links CSV (.csv):",
            font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY,
            bg=BG_CARD,
            width=22,
            anchor="w"
        )
        self.lbl_csv_picker.pack(side="left", pady=4)

        self.lbl_csv_path_display = tk.Label(
            self.csv_row_frame,
            text="No file selected",
            font=FONT_SMALL,
            fg=TEXT_MUTED,
            bg=BG_CARD_ALT,
            anchor="w",
            padx=8,
            pady=4,
            relief="flat"
        )
        self.lbl_csv_path_display.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=4)

        btn_browse_csv = tk.Button(
            self.csv_row_frame,
            text="Browse...",
            font=FONT_SMALL,
            bg=BG_CARD_ALT,
            fg=TEXT_PRIMARY,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._browse_csv
        )
        btn_browse_csv.pack(side="right", pady=4)

        # ------------------------------------------------------------------
        # 3. DevTools Checkbox & Settings note
        # ------------------------------------------------------------------
        devtools_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        devtools_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
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
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # Initial display styling
        self._update_sub_feature_display()

    def _browse_csv(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="Select CSV File",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if p:
            self.csv_path = p
            self.lbl_csv_path_display.config(text=p, fg=TEXT_PRIMARY)

    def set_sub_feature(self, mode: str):
        """Called by the left navigation panel when a sub-feature is clicked."""
        if self.sub_feature == mode:
            return
        self.sub_feature = mode
        self._update_sub_feature_display()

    def _update_sub_feature_display(self):
        """Update instructions and labels based on active sub-feature."""
        if self.sub_feature == "submit":
            self.guide_header.config(text="🎓  ACTIVE SUB-FEATURE: SUBMIT FINAL GRADES & SCORES", fg=ACCENT_INFO)
            self.guide_body.config(
                text="• CSV Requirements: Must include studentId and link. finalGradeReceivedDate, score, and grade are all optional.\n"
                     "• Action: Locates student record, opens Edit Status modal, sets Final Grade Received to 'Yes'\n"
                     "  (when date is provided), inputs date, fills Adjust Score / Grade dropdown (when score/grade is provided), and submits.\n"
                     "• No grade or score? Only the Edit Status modal is opened and submitted — nothing else."
            )
            self.lbl_csv_picker.config(text="Grade Links CSV (.csv):")
        else:
            self.guide_header.config(text="↩️  ACTIVE SUB-FEATURE: UN-SUBMIT FINAL GRADES", fg=ACCENT_INFO)
            self.guide_body.config(
                text="• CSV Requirements: Only student (or studentId) and link (or url) columns needed.\n"
                     "• No Scores or Dates: Do NOT include finalGradeReceivedDate, score, or grade in this CSV.\n"
                     "• Action: Locates student record, opens Edit Status modal, sets Final Grade Received to 'No',\n"
                     "  clears lingering date, and submits the status update."
            )
            self.lbl_csv_picker.config(text="Student Links CSV (.csv):")

    def get_task_from_inputs(self, silent: bool = False) -> QueueTask | None:
        if not self.cred_path or not self.csv_path:
            if not silent:
                messagebox.showerror("Missing Fields", "Please select Credentials and Links CSV file.")
            return None

        config = load_credentials(self.cred_path)
        if not config:
            if not silent:
                messagebox.showerror("Invalid Credentials", "Failed to parse credentials file.")
            return None

        mode = self.sub_feature
        mode_tag = "Submit" if mode == "submit" else "Un-submit"
        label = f"[{mode_tag}] Creds: {os.path.basename(self.cred_path)} | CSV: {os.path.basename(self.csv_path)}"

        inputs = {
            "config": config,
            "csv_path": self.csv_path,
            "devtools": self.devtools_var.get(),
            "cred_path": self.cred_path,
            "mode": mode,
        }

        import uuid
        return QueueTask(task_id=str(uuid.uuid4())[:8], label=label, inputs=inputs)

    def run_single_task(self, task: QueueTask) -> bool:
        inputs = task.inputs
        mode = inputs.get("mode", "submit")
        self.log("info", f"Running Grades task ({mode.upper()}): {task.label}")

        try:
            self.submitter.run(
                config=inputs["config"],
                csv_path=inputs["csv_path"],
                devtools=inputs["devtools"],
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log,
                mode=mode
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
            self.total_students = data.get('total', 0)
            self.set_progress(0, self.total_students)
            self.update_kpis(total_students=self.total_students)
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
