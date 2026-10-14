"""
ui_qt/views/file_exporter_view.py
=================================
Modern PySide6 File Exporter automation panel.
"""

import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QGroupBox, QFrame, QMessageBox, QSpinBox
)
from ui_qt.theme import (
    BG_CARD, BG_CARD_ALT, BG_INPUT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY
)
from ui_qt.components.base_tool_widget import BaseToolWidget
from core.settings_manager import settings
from core.utils import load_credentials, read_links_csv
from modules.file_exporter import FileExporter

DEFAULT_TEMPLATE = "{studentId}/{segment}/{category}"


class FileExporterView(BaseToolWidget):
    """File Exporter UI view in PySide6."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            title="File Exporter",
            description="Dynamically extracts and downloads files from web portals. The system adapts to site structure on every run."
        )

        self.cred_path = ""
        self.links_path = ""
        self.download_dir = ""
        self.exporter = FileExporter()
        self.tool_mode = "download"

        self._populate_inputs()

    def _populate_inputs(self):
        ic_layout = self.inputs_layout

        # ── 1. File pickers ────────────────────────────────────────────
        self.create_file_picker_row(
            ic_layout, "Credentials File (.txt):",
            lambda p: setattr(self, "cred_path", p),
            is_directory=False, file_types="Text Files (*.txt);;All Files (*.*)"
        )
        self.create_file_picker_row(
            ic_layout, "Record Links CSV (.csv):",
            lambda p: setattr(self, "links_path", p),
            is_directory=False, file_types="CSV Files (*.csv);;All Files (*.*)"
        )
        self.create_file_picker_row(
            ic_layout, "Save Files Directory:",
            lambda p: setattr(self, "download_dir", p),
            is_directory=True
        )

        # ── 2. Folder Hierarchy Template Card ──────────────────────────
        tmpl_group = QGroupBox("📁  Folder Hierarchy Template")
        tmpl_layout = QVBoxLayout(tmpl_group)
        tmpl_layout.setSpacing(8)

        row_tmpl = QHBoxLayout()
        lbl_tmpl = QLabel("Template:")
        lbl_tmpl.setStyleSheet(f"font-weight: 600; color: {TEXT_PRIMARY}; min-width: 100px;")
        self.txt_template = QLineEdit(DEFAULT_TEMPLATE)
        row_tmpl.addWidget(lbl_tmpl)
        row_tmpl.addWidget(self.txt_template)
        tmpl_layout.addLayout(row_tmpl)

        lbl_tokens = QLabel("Available Tokens: {studentId}, {studentName}, {segment}, {category}, {year}")
        lbl_tokens.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        tmpl_layout.addWidget(lbl_tokens)

        ic_layout.insertWidget(ic_layout.count() - 1, tmpl_group)

        # ── 3. Filters & Advanced Overrides Card ───────────────────────
        filter_group = QGroupBox("⚙️  Filters & Overrides")
        f_grid = QGridLayout(filter_group)
        f_grid.setSpacing(10)

        # Segment names
        f_grid.addWidget(QLabel("Segment Overrides:"), 0, 0)
        self.txt_segments = QLineEdit()
        self.txt_segments.setPlaceholderText("Optional comma-separated names")
        f_grid.addWidget(self.txt_segments, 0, 1)

        # Category Column
        f_grid.addWidget(QLabel("Category Col Index:"), 0, 2)
        self.txt_category_col = QLineEdit("2")
        self.txt_category_col.setMaximumWidth(80)
        f_grid.addWidget(self.txt_category_col, 0, 3)

        # Extension Filter
        f_grid.addWidget(QLabel("Filter Extensions:"), 1, 0)
        self.txt_extensions = QLineEdit()
        self.txt_extensions.setPlaceholderText("e.g. .pdf, .docx (leave blank for all)")
        f_grid.addWidget(self.txt_extensions, 1, 1)

        # Checkboxes
        self.chk_allow_dups = QCheckBox("Allow Duplicate Downloads")
        self.chk_allow_dups.setChecked(True)
        f_grid.addWidget(self.chk_allow_dups, 1, 2, 1, 2)

        self.chk_devtools = QCheckBox("Auto-open Chrome DevTools")
        self.chk_devtools.setChecked(settings.get("auto_open_devtools", False))
        f_grid.addWidget(self.chk_devtools, 2, 0, 1, 2)

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

        f_grid.addWidget(workers_frame, 3, 0, 1, 4)

        ic_layout.insertWidget(ic_layout.count() - 1, filter_group)

    def get_current_inputs(self) -> dict:
        if not self.cred_path or not os.path.isfile(self.cred_path):
            QMessageBox.warning(self, "Missing Credentials", "Please select a valid credentials file.")
            return {}
        if not self.links_path or not os.path.isfile(self.links_path):
            QMessageBox.warning(self, "Missing Links", "Please select a valid record links CSV file.")
            return {}
        if not self.download_dir or not os.path.isdir(self.download_dir):
            QMessageBox.warning(self, "Missing Directory", "Please select a valid destination save directory.")
            return {}

        return {
            "cred_path": self.cred_path,
            "links_path": self.links_path,
            "download_dir": self.download_dir,
            "folder_template": self.txt_template.text().strip(),
            "segments": self.txt_segments.text().strip(),
            "category_col": self.txt_category_col.text().strip(),
            "extensions": self.txt_extensions.text().strip(),
            "allow_dups": self.chk_allow_dups.isChecked(),
            "auto_devtools": self.chk_devtools.isChecked(),
            "n_workers": self.spin_workers.value(),
        }

    def describe_task_inputs(self, inputs: dict) -> str:
        cred = os.path.basename(inputs.get("cred_path", ""))
        links = os.path.basename(inputs.get("links_path", ""))
        n = inputs.get("n_workers", 1)
        workers_str = f" | {n} workers" if n > 1 else ""
        return f"Exporter | Cred: {cred} | Links: {links}{workers_str}"

    def execute_task_backend(self, inputs: dict, **kwargs):
        log_cb = kwargs.get("log_callback", print)
        prog_cb = kwargs.get("progress_callback", lambda c, t: None)
        kpi_cb = kwargs.get("kpi_callback", lambda d: None)
        is_aborted = kwargs.get("is_aborted", lambda: False)

        n_workers = max(1, inputs.get("n_workers", 1))

        log_cb("Loading credentials...", "INFO")
        config = load_credentials(
            inputs["cred_path"],
            required_keys=["username", "password", "login_url"]
        )
        if not config:
            log_cb("Invalid credentials file — missing required keys.", "ERROR")
            return

        log_cb("Parsing student links CSV...", "INFO")
        students = read_links_csv(inputs["links_path"])
        if not students:
            log_cb("Could not parse any valid records from the CSV file.", "ERROR")
            return

        raw_ext = inputs.get("extensions", "").strip()
        filters = {}
        if raw_ext:
            filters["type_include"] = [e.strip().lower() for e in raw_ext.split(",") if e.strip()]

        def on_progress(data: dict):
            completed = data.get("completed", data.get("students_traversed", data.get("idx", 0)))
            total = data.get("total_students", data.get("total", len(students)))
            if total > 0:
                prog_cb(completed, total)
            kpi_cb(data)

        log_cb(f"Initializing File Exporter engine "
               f"({'parallel ×' + str(n_workers) if n_workers > 1 else 'sequential'})...", "INFO")
        common_kwargs = dict(
            config=config,
            students=students,
            download_dir=inputs["download_dir"],
            folder_hierarchy_template=inputs.get("folder_template", DEFAULT_TEMPLATE),
            allow_duplicates=inputs.get("allow_dups", True),
            segment_names_str=inputs.get("segments", ""),
            category_column_index=int(inputs.get("category_col", 2) or 2),
            filters=filters if filters else None,
            devtools=inputs.get("auto_devtools", False),
            on_progress=on_progress,
            abort_flag=is_aborted,
            log=lambda lvl, msg: log_cb(msg, lvl.upper()),
        )

        if n_workers > 1:
            self.exporter.run_parallel(n_workers=n_workers, **common_kwargs)
        else:
            self.exporter.run(**common_kwargs)
