"""
ui_qt/views/field_updater_view.py
==================================
Modern PySide6 Field Updater panel.
Accepts a CSV with columns: link, field_1, value_1, field_2, value_2, ...
Supports all field types (text, dropdowns, checkboxes, radio) and parallel workers.
"""

import os
from PySide6.QtCore import Qt  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QFrame, QMessageBox, QSpinBox
)
from ui_qt.theme import (
    BG_CARD_ALT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INFO, BG_INPUT
)
from ui_qt.components.base_tool_widget import BaseToolWidget
from core.settings_manager import settings
from core.utils import load_credentials
from modules.field_updater import FieldUpdater, parse_field_updater_csv


class FieldUpdaterView(BaseToolWidget):
    """Bulk field-update automation: navigates to each page URL and sets fields from CSV."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            title="Field Updater",
            description=(
                "Navigate to each page URL in the CSV and update one or more fields "
                "in batch. Supports text inputs, dropdowns, checkboxes, and radio buttons."
            ),
        )

        self.cred_path = ""
        self.csv_path = ""
        self._updater = FieldUpdater()

        self._populate_inputs()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _populate_inputs(self):
        ic_layout = self.inputs_layout

        # ── CSV Format Info Banner ─────────────────────────────────────
        banner = QFrame()
        banner.setStyleSheet(
            f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 6px; padding: 4px;"
        )
        b_layout = QVBoxLayout(banner)
        b_layout.setContentsMargins(10, 8, 10, 8)
        b_layout.setSpacing(4)

        lbl_banner_title = QLabel("📋  CSV Format")
        lbl_banner_title.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ACCENT_INFO}; border: none;"
        )
        b_layout.addWidget(lbl_banner_title)

        lbl_fmt = QLabel(
            "Required columns:  link,  field_1,  value_1  [ ,  field_2,  value_2  … ]\n"
            "• link — full page URL to navigate to\n"
            "• field_N — label text or partial XPath that identifies the field\n"
            "• value_N — the value to set (leave blank to clear the field)\n"
            "Supports: text inputs, <select> dropdowns, checkboxes, radio buttons."
        )
        lbl_fmt.setStyleSheet(
            f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;"
        )
        lbl_fmt.setWordWrap(True)
        b_layout.addWidget(lbl_fmt)

        ic_layout.insertWidget(ic_layout.count() - 1, banner)

        # ── File Pickers ───────────────────────────────────────────────
        self.create_file_picker_row(
            ic_layout, "Credentials File (.txt):",
            lambda p: setattr(self, "cred_path", p),
            is_directory=False,
            file_types="Text Files (*.txt);;All Files (*.*)",
        )

        def _on_csv_picked(p: str):
            self.csv_path = p
            self._log_dir = os.path.dirname(os.path.abspath(p)) or "."

        self.create_file_picker_row(
            ic_layout, "Field Updater CSV (.csv):",
            _on_csv_picked,
            is_directory=False,
            file_types="CSV Files (*.csv);;All Files (*.*)",
        )

        # ── Options ────────────────────────────────────────────────────
        from PySide6.QtWidgets import QGroupBox  # type: ignore
        opts_group = QGroupBox("⚙️  Execution Options")
        o_layout = QVBoxLayout(opts_group)
        o_layout.setSpacing(10)
        o_layout.setContentsMargins(10, 10, 10, 10)

        self.chk_devtools = QCheckBox("Auto-open Chrome DevTools")
        self.chk_devtools.setChecked(bool(settings.get("auto_open_devtools", False)))
        o_layout.addWidget(self.chk_devtools)

        # Workers row
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
            "Start low (1–5) and increase as the target site allows."
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

        ic_layout.insertWidget(ic_layout.count() - 1, opts_group)

    # ------------------------------------------------------------------
    # BaseToolWidget interface
    # ------------------------------------------------------------------
    def get_current_inputs(self) -> dict:
        if not self.cred_path or not os.path.isfile(self.cred_path):
            QMessageBox.warning(self, "Missing Credentials", "Please select a valid credentials file.")
            return {}
        if not self.csv_path or not os.path.isfile(self.csv_path):
            QMessageBox.warning(self, "Missing CSV", "Please select a valid field updater CSV file.")
            return {}
        return {
            "cred_path":    self.cred_path,
            "csv_path":     self.csv_path,
            "auto_devtools": self.chk_devtools.isChecked(),
            "n_workers":    self.spin_workers.value(),
        }

    def describe_task_inputs(self, inputs: dict) -> str:
        csv = os.path.basename(inputs.get("csv_path", ""))
        n = inputs.get("n_workers", 1)
        workers_str = f" | {n} workers" if n > 1 else ""
        return f"Field Updater | CSV: {csv}{workers_str}"

    def execute_task_backend(self, inputs: dict, **kwargs):
        log_cb    = kwargs.get("log_callback",    print)
        prog_cb   = kwargs.get("progress_callback", lambda c, t: None)
        kpi_cb    = kwargs.get("kpi_callback",    lambda d: None)
        is_aborted = kwargs.get("is_aborted",     lambda: False)

        n_workers = max(1, inputs.get("n_workers", 1))

        log_cb("Loading credentials...", "INFO")
        config = load_credentials(inputs["cred_path"])
        if not config:
            log_cb("Invalid credentials file — could not load config.", "ERROR")
            return

        log_cb(
            f"Starting Field Updater "
            f"({'parallel ×' + str(n_workers) if n_workers > 1 else 'sequential'})...",
            "INFO",
        )

        def on_progress(data: dict):
            completed = data.get("completed", data.get("idx", 0))
            total     = data.get("total", 0)
            if total > 0:
                prog_cb(completed, total)
            kpi_cb(data)

        def _log_adapter(lvl: str, msg: str):
            log_cb(msg, str(lvl).upper())

        if n_workers > 1:
            self._updater.run_parallel(
                config=config,
                csv_path=inputs["csv_path"],
                n_workers=n_workers,
                devtools=inputs.get("auto_devtools", False),
                on_progress=on_progress,
                abort_flag=is_aborted,
                log=_log_adapter,
            )
        else:
            self._updater.run(
                config=config,
                csv_path=inputs["csv_path"],
                devtools=inputs.get("auto_devtools", False),
                on_progress=on_progress,
                abort_flag=is_aborted,
                log=_log_adapter,
            )
