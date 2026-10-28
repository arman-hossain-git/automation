"""
ui_qt/views/grade_view.py
=========================
Modern PySide6 Student Grade Management panel.
Supports:
  - Submit Grades (enters final grades, dates, and scores)
  - Un-submit Grades (reverts final grades by marking 'No' and submitting)
"""

import os
from PySide6.QtCore import Qt  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QRadioButton, QButtonGroup, QGroupBox, QFrame, QMessageBox,
    QSpinBox
)
from ui_qt.theme import (
    BG_CARD, BG_CARD_ALT, BG_INPUT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_INFO, ACCENT_WARNING
)
from ui_qt.components.base_tool_widget import BaseToolWidget
from core.settings_manager import settings
from core.utils import load_credentials
from modules.grade_submission import GradeSubmitter


class GradeView(BaseToolWidget):
    """Unified Grade Submission & Un-submission UI in PySide6."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            title="Student Grade Management",
            description="Manage student grades in PCR Educator. Choose to submit or un-submit grades in batch."
        )

        self.cred_path = ""
        self.csv_path = ""
        self.sub_feature = "submit"  # "submit" or "unsubmit"
        self.submitter = GradeSubmitter()

        self._populate_inputs()

    def _populate_inputs(self):
        ic_layout = self.inputs_layout

        # ── 1. Sub-feature Switcher (Submit vs Un-submit) ───────────────
        mode_group = QGroupBox("🎓  Select Grade Operation Mode")
        m_layout = QVBoxLayout(mode_group)
        m_layout.setSpacing(8)

        btn_row = QHBoxLayout()
        self.rb_submit = QRadioButton("Submit Grades (Post final grades, dates & scores)")
        self.rb_unsubmit = QRadioButton("Un-submit Grades (Revert status, Mark 'No')")
        self.rb_submit.setChecked(True)

        self.btn_group = QButtonGroup(self)
        self.btn_group.addButton(self.rb_submit)
        self.btn_group.addButton(self.rb_unsubmit)

        btn_row.addWidget(self.rb_submit)
        btn_row.addWidget(self.rb_unsubmit)
        btn_row.addStretch()
        m_layout.addLayout(btn_row)

        # Mode banner
        self.banner_mode = QFrame()
        self.banner_mode.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px; padding: 8px;")
        b_layout = QVBoxLayout(self.banner_mode)
        b_layout.setContentsMargins(10, 6, 10, 6)
        self.lbl_banner_title = QLabel("📝 Mode: Submit Final Grades")
        self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_INFO}; border: none;")
        self.lbl_banner_desc = QLabel("Opens Edit Status modal per student. Fills grade/date/score when provided; submits modal-only when not.")
        self.lbl_banner_desc.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")
        b_layout.addWidget(self.lbl_banner_title)
        b_layout.addWidget(self.lbl_banner_desc)
        m_layout.addWidget(self.banner_mode)

        def on_mode_changed():
            if self.rb_submit.isChecked():
                self.sub_feature = "submit"
                self.lbl_banner_title.setText("📝 Mode: Submit Final Grades")
                self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_INFO}; border: none;")
                self.lbl_banner_desc.setText("Opens Edit Status modal per student. Fills grade/date/score when provided; submits modal-only when not.")
            else:
                self.sub_feature = "unsubmit"
                self.lbl_banner_title.setText("↩️ Mode: Un-submit Grades (Revert)")
                self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_WARNING}; border: none;")
                self.lbl_banner_desc.setText("Locates submitted student records, unchecks/marks 'No' for final grade status, and saves.")

        self.rb_submit.toggled.connect(on_mode_changed)
        ic_layout.insertWidget(ic_layout.count() - 1, mode_group)

        # ── 2. File Pickers ────────────────────────────────────────────
        self.create_file_picker_row(
            ic_layout, "Credentials File (.txt):",
            lambda p: setattr(self, "cred_path", p),
            is_directory=False, file_types="Text Files (*.txt);;All Files (*.*)"
        )
        def _on_csv_picked(p: str):
            self.csv_path = p
            self._log_dir = os.path.dirname(os.path.abspath(p)) or "."

        self.create_file_picker_row(
            ic_layout, "Grade Links CSV (.csv):",
            _on_csv_picked,
            is_directory=False, file_types="CSV Files (*.csv);;All Files (*.*)"
        )

        # ── 3. Options ─────────────────────────────────────────────────
        opts_group = QGroupBox("⚙️  Execution Options")
        o_layout = QVBoxLayout(opts_group)
        o_layout.setSpacing(10)
        o_layout.setContentsMargins(10, 10, 10, 10)

        self.chk_devtools = QCheckBox("Auto-open Chrome DevTools")
        self.chk_devtools.setChecked(bool(settings.get("auto_open_devtools", False)))
        o_layout.addWidget(self.chk_devtools)

        # ── Parallel workers row ──────────────────────────────────────
        workers_frame = QFrame()
        workers_frame.setStyleSheet("border: none;")
        workers_h = QHBoxLayout(workers_frame)
        workers_h.setContentsMargins(0, 2, 0, 2)
        workers_h.setSpacing(8)

        lbl_workers = QLabel("Parallel Chrome Workers:")
        lbl_workers.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")

        self.spin_workers = QSpinBox()
        self.spin_workers.setMinimum(1)
        self.spin_workers.setMaximum(50)
        self.spin_workers.setValue(1)
        self.spin_workers.setFixedWidth(64)
        self.spin_workers.setToolTip(
            "Number of simultaneous Chrome windows to open.\n"
            "Each worker handles an independent slice of the CSV.\n"
            "Max supported: 50 — start lower (5–10) and scale up as the site allows."
        )
        self.spin_workers.setStyleSheet(
            f"QSpinBox {{ background: {BG_INPUT}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER_SUBTLE}; border-radius: 4px; padding: 2px 6px; }}"
        )

        lbl_workers_hint = QLabel("(1 = sequential, 2–50 = parallel)")
        lbl_workers_hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; border: none;")

        workers_h.addWidget(lbl_workers)
        workers_h.addWidget(self.spin_workers)
        workers_h.addWidget(lbl_workers_hint)
        workers_h.addStretch()

        o_layout.addWidget(workers_frame)

        self.chk_skip_completed = QCheckBox("Skip previously submitted grades (resumes from prior runs & logs)")
        self.chk_skip_completed.setChecked(True)
        self.chk_skip_completed.setToolTip(
            "When checked, rows that were already successfully processed in previous runs\n"
            "or execution audit logs are detected and skipped automatically."
        )
        o_layout.addWidget(self.chk_skip_completed)

        ic_layout.insertWidget(ic_layout.count() - 1, opts_group)

    def get_current_inputs(self) -> dict:
        if not self.cred_path or not os.path.isfile(self.cred_path):
            QMessageBox.warning(self, "Missing Credentials", "Please select a valid credentials file.")
            return {}
        if not self.csv_path or not os.path.isfile(self.csv_path):
            QMessageBox.warning(self, "Missing CSV", "Please select a valid grade links CSV file.")
            return {}

        return {
            "cred_path": self.cred_path,
            "csv_path": self.csv_path,
            "sub_feature": self.sub_feature,
            "auto_devtools": self.chk_devtools.isChecked(),
            "n_workers": self.spin_workers.value(),
            "skip_completed": self.chk_skip_completed.isChecked(),
        }

    def describe_task_inputs(self, inputs: dict) -> str:
        mode = "Submit" if inputs.get("sub_feature") == "submit" else "Un-submit"
        csv = os.path.basename(inputs.get("csv_path", ""))
        n = inputs.get("n_workers", 1)
        workers_str = f" | {n} workers" if n > 1 else ""
        return f"Grades ({mode}) | CSV: {csv}{workers_str}"

    def execute_task_backend(self, inputs: dict, **kwargs):
        log_cb = kwargs.get("log_callback", print)
        prog_cb = kwargs.get("progress_callback", lambda c, t: None)
        kpi_cb = kwargs.get("kpi_callback", lambda d: None)
        is_aborted = kwargs.get("is_aborted", lambda: False)

        mode = inputs.get("sub_feature", "submit")
        n_workers = max(1, inputs.get("n_workers", 1))

        log_cb("Loading credentials...", "INFO")
        config = load_credentials(inputs["cred_path"])
        if not config:
            log_cb("Invalid credentials file — could not load config.", "ERROR")
            return

        log_cb(f"Starting Grade Operation: {mode.upper()} "
               f"({'parallel ×' + str(n_workers) if n_workers > 1 else 'sequential'})...", "INFO")

        def on_progress(data: dict):
            completed = data.get("completed", data.get("students_traversed", data.get("idx", 0)))
            total = data.get("total_students", data.get("total", 0))
            if total > 0:
                prog_cb(completed, total)
            kpi_cb(data)

        def _log_adapter(lvl: str, msg: str):
            log_cb(msg, str(lvl).upper())

        if n_workers > 1:
            self.submitter.run_parallel(
                config=config,
                csv_path=inputs["csv_path"],
                n_workers=n_workers,
                devtools=inputs.get("auto_devtools", False),
                on_progress=on_progress,
                abort_flag=is_aborted,
                log=_log_adapter,
                mode=mode,
                skip_completed=inputs.get("skip_completed", True),
            )
        else:
            self.submitter.run(
                config=config,
                csv_path=inputs["csv_path"],
                devtools=inputs.get("auto_devtools", False),
                on_progress=on_progress,
                abort_flag=is_aborted,
                log=_log_adapter,
                mode=mode,
                skip_completed=inputs.get("skip_completed", True),
            )
