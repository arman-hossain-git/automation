"""
ui_qt/views/file_uploader_view.py
=================================
Modern PySide6 File Uploader automation panel.
"""

import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QGroupBox, QFrame, QMessageBox, QSpinBox
)
from ui_qt.theme import (
    BG_CARD, BG_INPUT, BORDER_SUBTLE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED
)
from ui_qt.components.base_tool_widget import BaseToolWidget
from core.settings_manager import settings
from core.utils import load_credentials, read_links_csv
from modules.file_uploader import FileUploader


class FileUploaderView(BaseToolWidget):
    """File Uploader UI view in PySide6."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            title="File Uploader",
            description="Uploads local student files and folders directly to web portal directories, preserving folder structures."
        )

        self.cred_path = ""
        self.source_dir = ""
        self.links_path = ""
        self.uploader = FileUploader()
        self.tool_mode = "upload"

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
            ic_layout, "Source Files Directory:",
            lambda p: setattr(self, "source_dir", p),
            is_directory=True
        )
        self.create_file_picker_row(
            ic_layout, "Record Links CSV (.csv):",
            lambda p: setattr(self, "links_path", p),
            is_directory=False, file_types="CSV Files (*.csv);;All Files (*.*)"
        )

        # ── 2. Destination Folder Config ───────────────────────────────
        dest_group = QGroupBox("📁  Target Portal Directory Configuration")
        d_layout = QVBoxLayout(dest_group)
        d_layout.setSpacing(8)

        row_opt = QHBoxLayout()
        lbl_folder = QLabel("Target Folder:")
        lbl_folder.setStyleSheet(f"font-weight: 600; color: {TEXT_PRIMARY}; min-width: 120px;")

        self.combo_folder = QComboBox()
        self.combo_folder.addItems([
            "Root (Files & Documents)",
            "Admissions Inquiry",
            "Students Inquiry",
            "Custom Folder..."
        ])
        row_opt.addWidget(lbl_folder)
        row_opt.addWidget(self.combo_folder, stretch=1)
        d_layout.addLayout(row_opt)

        self.txt_custom_folder = QLineEdit()
        self.txt_custom_folder.setPlaceholderText("Enter custom folder name")
        self.txt_custom_folder.setVisible(False)
        d_layout.addWidget(self.txt_custom_folder)

        def on_folder_change(idx):
            self.txt_custom_folder.setVisible(idx == 3)

        self.combo_folder.currentIndexChanged.connect(on_folder_change)

        ic_layout.insertWidget(ic_layout.count() - 1, dest_group)

        # ── 3. Options ─────────────────────────────────────────────────
        opts_group = QGroupBox("⚙️  Upload Options")
        o_grid = QGridLayout(opts_group)
        o_grid.setSpacing(10)

        self.chk_allow_dups = QCheckBox("Allow Duplicate Uploads")
        self.chk_allow_dups.setChecked(False)
        o_grid.addWidget(self.chk_allow_dups, 0, 0)

        self.chk_devtools = QCheckBox("Auto-open Chrome DevTools")
        self.chk_devtools.setChecked(settings.get("auto_open_devtools", False))
        o_grid.addWidget(self.chk_devtools, 0, 1)

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

        o_grid.addWidget(workers_frame, 1, 0, 1, 2)

        ic_layout.insertWidget(ic_layout.count() - 1, opts_group)

    def get_current_inputs(self) -> dict:
        if not self.cred_path or not os.path.isfile(self.cred_path):
            QMessageBox.warning(self, "Missing Credentials", "Please select a valid credentials file.")
            return {}
        if not self.source_dir or not os.path.isdir(self.source_dir):
            QMessageBox.warning(self, "Missing Directory", "Please select a valid source files directory.")
            return {}
        if not self.links_path or not os.path.isfile(self.links_path):
            QMessageBox.warning(self, "Missing Links", "Please select a valid record links CSV file.")
            return {}

        target_folder = self.combo_folder.currentText()
        if self.combo_folder.currentIndex() == 3:
            target_folder = self.txt_custom_folder.text().strip() or "Custom"

        return {
            "cred_path": self.cred_path,
            "source_dir": self.source_dir,
            "links_path": self.links_path,
            "target_folder": target_folder,
            "allow_dups": self.chk_allow_dups.isChecked(),
            "auto_devtools": self.chk_devtools.isChecked(),
            "n_workers": self.spin_workers.value(),
        }

    def describe_task_inputs(self, inputs: dict) -> str:
        cred = os.path.basename(inputs.get("cred_path", ""))
        src = os.path.basename(inputs.get("source_dir", ""))
        n = inputs.get("n_workers", 1)
        workers_str = f" | {n} workers" if n > 1 else ""
        return f"Uploader | Cred: {cred} | Folder: {src}{workers_str}"

    def execute_task_backend(self, inputs: dict, **kwargs):
        log_cb = kwargs.get("log_callback", print)
        prog_cb = kwargs.get("progress_callback", lambda c, t: None)
        kpi_cb = kwargs.get("kpi_callback", lambda d: None)
        is_aborted = kwargs.get("is_aborted", lambda: False)

        n_workers = max(1, inputs.get("n_workers", 1))

        log_cb("Loading credentials...", "INFO")
        config = load_credentials(
            inputs["cred_path"],
            required_keys=["username", "password", "login_url", "login_btn_xpath"]
        )
        if not config:
            log_cb("Invalid credentials file — missing required keys.", "ERROR")
            return

        log_cb("Parsing student links CSV...", "INFO")
        students = read_links_csv(inputs["links_path"])
        if not students:
            log_cb("Could not parse any valid student links from the CSV file.", "ERROR")
            return

        def on_progress(data: dict):
            t = data.get("type")
            if t == "file_done":
                prog_cb(data.get("files_done", 0), data.get("files_total", 0))
            elif t in ("student_start", "student_done"):
                prog_cb(data.get("students_traversed", data.get("idx", 0)), data.get("total_students", len(students)))
            kpi_cb(data)

        log_cb(f"Initializing File Uploader engine "
               f"({'parallel ×' + str(n_workers) if n_workers > 1 else 'sequential'})...", "INFO")
        common_kwargs = dict(
            config=config,
            students=students,
            source_dir=inputs["source_dir"],
            target_folder_name=inputs.get("target_folder", ""),
            devtools=inputs.get("auto_devtools", False),
            on_progress=on_progress,
            abort_flag=is_aborted,
            log=lambda lvl, msg: log_cb(msg, lvl.upper()),
        )

        if n_workers > 1:
            self.uploader.run_parallel(n_workers=n_workers, **common_kwargs)
        else:
            self.uploader.run(**common_kwargs)
