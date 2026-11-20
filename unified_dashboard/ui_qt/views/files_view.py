"""
ui_qt/views/files_view.py
=========================
Modern PySide6 Unified 3-Way File Management Automation Panel.
Supports:
  1. File Exporter (Download documents from portal)
  2. File Uploader (Upload local documents into portal folders)
  3. Export & Upload Pipeline (Automated two-stage download + upload sync)

Parallel workers (1 to 50) are available and active across all 3 modes!
"""

import os
import shutil
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QRadioButton, QButtonGroup, QGroupBox, QFrame,
    QMessageBox, QSpinBox
)
from ui_qt.theme import (
    BG_CARD, BG_CARD_ALT, BG_INPUT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_INFO, ACCENT_SUCCESS, ACCENT_WARNING
)
from ui_qt.components.base_tool_widget import BaseToolWidget
from core.settings_manager import settings
from core.utils import load_credentials, read_links_csv
from modules.file_exporter import FileExporter
from modules.file_uploader import FileUploader

DEFAULT_TEMPLATE = "{studentId}/{segment}/{category}"
TARGET_FOLDER_OPTIONS = [
    "Root (Files & Documents)",
    "Admissions Inquiry",
    "Students Inquiry",
    "Custom Folder..."
]


class FilesView(BaseToolWidget):
    """Unified 3-Way File Management (Export, Upload, and Combined Pipeline) Panel."""

    def __init__(self, parent=None, default_mode: str = "export"):
        super().__init__(
            parent,
            title="Student File Management",
            description="Export, upload, or pipeline student files and documents to/from web portals."
        )

        self.mode = default_mode  # "export", "upload", or "pipeline"
        self.cred_path = ""
        self.links_path = ""
        self.download_dir = ""
        self.source_dir = ""
        self.staging_dir = ""
        self.target_cred_path = ""
        self.target_links_path = ""

        self.exporter = FileExporter()
        self.uploader = FileUploader()

        self._populate_inputs()
        self.set_mode(self.mode)

    def _populate_inputs(self):
        ic_layout = self.inputs_layout

        # ── 1. Mode Switcher Group (3 Radio Buttons) ───────────────────
        mode_group = QGroupBox("📁  Select File Operation Mode")
        m_layout = QVBoxLayout(mode_group)
        m_layout.setSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(24)
        self.rb_export = QRadioButton("File Exporter (Download Only)")
        self.rb_upload = QRadioButton("File Uploader (Upload Only)")
        self.rb_pipeline = QRadioButton("Export & Upload Pipeline (Download then Upload)")

        if self.mode == "upload":
            self.rb_upload.setChecked(True)
        elif self.mode == "pipeline":
            self.rb_pipeline.setChecked(True)
        else:
            self.rb_export.setChecked(True)

        self.btn_group = QButtonGroup(self)
        self.btn_group.addButton(self.rb_export)
        self.btn_group.addButton(self.rb_upload)
        self.btn_group.addButton(self.rb_pipeline)

        btn_row.addWidget(self.rb_export)
        btn_row.addWidget(self.rb_upload)
        btn_row.addWidget(self.rb_pipeline)
        btn_row.addStretch()
        m_layout.addLayout(btn_row)

        # Mode banner
        self.banner_mode = QFrame()
        self.banner_mode.setStyleSheet(
            f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px; padding: 8px;"
        )
        b_layout = QVBoxLayout(self.banner_mode)
        b_layout.setContentsMargins(10, 6, 10, 6)
        self.lbl_banner_title = QLabel("📥 Mode: File Exporter")
        self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_PRIMARY}; border: none;")
        self.lbl_banner_desc = QLabel("Scrapes and downloads student documents from web portals into organized local directories.")
        self.lbl_banner_desc.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")
        b_layout.addWidget(self.lbl_banner_title)
        b_layout.addWidget(self.lbl_banner_desc)
        m_layout.addWidget(self.banner_mode)

        def on_radio_toggled():
            if self.rb_export.isChecked():
                self.set_mode("export")
            elif self.rb_upload.isChecked():
                self.set_mode("upload")
            elif self.rb_pipeline.isChecked():
                self.set_mode("pipeline")

        self.rb_export.toggled.connect(on_radio_toggled)
        self.rb_upload.toggled.connect(on_radio_toggled)
        self.rb_pipeline.toggled.connect(on_radio_toggled)

        ic_layout.insertWidget(ic_layout.count() - 1, mode_group)

        # ── 2. Primary File & Directory Pickers ────────────────────────
        self.row_cred = self.create_file_picker_row(
            ic_layout, "Credentials File (.txt):",
            lambda p: setattr(self, "cred_path", p),
            is_directory=False, file_types="Text Files (*.txt);;All Files (*.*)"
        )

        self.row_target_cred = self.create_file_picker_row(
            ic_layout, "Target Portal Credentials (.txt):",
            lambda p: setattr(self, "target_cred_path", p),
            is_directory=False, file_types="Text Files (*.txt);;All Files (*.*)"
        )
        self.row_target_cred.setVisible(False)

        self.row_links = self.create_file_picker_row(
            ic_layout, "Record Links CSV (.csv):",
            lambda p: setattr(self, "links_path", p),
            is_directory=False, file_types="CSV Files (*.csv);;All Files (*.*)"
        )

        self.row_target_links = self.create_file_picker_row(
            ic_layout, "Target Links CSV (.csv):",
            lambda p: setattr(self, "target_links_path", p),
            is_directory=False, file_types="CSV Files (*.csv);;All Files (*.*)"
        )
        self.row_target_links.setVisible(False)

        self.row_download_dir = self.create_file_picker_row(
            ic_layout, "Save Files Directory:",
            lambda p: setattr(self, "download_dir", p),
            is_directory=True
        )

        self.row_source_dir = self.create_file_picker_row(
            ic_layout, "Source Files Directory:",
            lambda p: setattr(self, "source_dir", p),
            is_directory=True
        )

        self.row_staging_dir = self.create_file_picker_row(
            ic_layout, "Staging / Working Directory:",
            lambda p: setattr(self, "staging_dir", p),
            is_directory=True
        )

        # ── 3. Folder Hierarchy & Scraper Options Card (Export & Pipeline) ───
        self.card_export_options = QGroupBox("📁  Folder Hierarchy & Scraper Options")
        tmpl_layout = QVBoxLayout(self.card_export_options)
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

        f_grid = QGridLayout()
        f_grid.setSpacing(10)

        f_grid.addWidget(QLabel("Segment Overrides:"), 0, 0)
        self.txt_segments = QLineEdit()
        self.txt_segments.setPlaceholderText("Optional comma-separated names")
        f_grid.addWidget(self.txt_segments, 0, 1)

        f_grid.addWidget(QLabel("Category Col Index:"), 0, 2)
        self.txt_category_col = QLineEdit("2")
        self.txt_category_col.setMaximumWidth(80)
        f_grid.addWidget(self.txt_category_col, 0, 3)

        f_grid.addWidget(QLabel("Filter Extensions:"), 1, 0)
        self.txt_extensions = QLineEdit()
        self.txt_extensions.setPlaceholderText("e.g. .pdf, .docx (leave blank for all)")
        f_grid.addWidget(self.txt_extensions, 1, 1)

        self.chk_allow_dups = QCheckBox("Allow Duplicate Downloads")
        self.chk_allow_dups.setChecked(True)
        f_grid.addWidget(self.chk_allow_dups, 1, 2, 1, 2)

        tmpl_layout.addLayout(f_grid)
        ic_layout.insertWidget(ic_layout.count() - 1, self.card_export_options)

        # ── 4. Target Portal Directory Options Card (Upload & Pipeline) ─────
        self.card_upload_options = QGroupBox("📁  Target Portal Directory Configuration")
        d_layout = QVBoxLayout(self.card_upload_options)
        d_layout.setSpacing(8)

        row_opt = QHBoxLayout()
        lbl_folder = QLabel("Target Folder:")
        lbl_folder.setStyleSheet(f"font-weight: 600; color: {TEXT_PRIMARY}; min-width: 120px;")

        self.combo_folder = QComboBox()
        self.combo_folder.addItems(TARGET_FOLDER_OPTIONS)
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

        self.chk_skip_dups = QCheckBox("Skip Duplicate Files (Check Destination Page)")
        self.chk_skip_dups.setChecked(True)
        self.chk_skip_dups.setToolTip(
            "When checked (default): inspect the destination folder before uploading each file.\n"
            "Files already present on the portal are skipped and logged as already uploaded.\n"
            "Uncheck ONLY to force re-upload / overwrite all files on the portal."
        )
        d_layout.addWidget(self.chk_skip_dups)

        ic_layout.insertWidget(ic_layout.count() - 1, self.card_upload_options)

        # ── 5. Pipeline Sync Specific Settings Card (Pipeline Only) ────────
        self.card_pipeline_options = QGroupBox("🔄  Combined Pipeline Settings")
        pipe_layout = QVBoxLayout(self.card_pipeline_options)
        pipe_layout.setSpacing(8)

        self.chk_same_cred = QCheckBox("Use same portal credentials for both Export and Upload")
        self.chk_same_cred.setChecked(True)
        pipe_layout.addWidget(self.chk_same_cred)

        self.chk_same_links = QCheckBox("Use same Links CSV for both Export and Upload")
        self.chk_same_links.setChecked(True)
        pipe_layout.addWidget(self.chk_same_links)

        self.chk_cleanup_staging = QCheckBox("Delete local staging files after successful upload")
        self.chk_cleanup_staging.setChecked(False)
        self.chk_cleanup_staging.setToolTip(
            "When checked, downloaded files in the staging directory are deleted after successful upload.\n"
            "Leave unchecked (default) to keep a local archive of all exported files."
        )
        pipe_layout.addWidget(self.chk_cleanup_staging)

        def on_pipe_checks_changed():
            is_pipe = (self.mode == "pipeline")
            self.row_target_cred.setVisible(is_pipe and not self.chk_same_cred.isChecked())
            self.row_target_links.setVisible(is_pipe and not self.chk_same_links.isChecked())

        self.chk_same_cred.toggled.connect(on_pipe_checks_changed)
        self.chk_same_links.toggled.connect(on_pipe_checks_changed)

        ic_layout.insertWidget(ic_layout.count() - 1, self.card_pipeline_options)

        # ── 6. Execution Options Card (Shared by All 3 Modes) ───────────────
        opts_group = QGroupBox("⚙️  Execution Options")
        o_grid = QGridLayout(opts_group)
        o_grid.setSpacing(10)

        self.chk_devtools = QCheckBox("Auto-open Chrome DevTools")
        self.chk_devtools.setChecked(settings.get("auto_open_devtools", False))
        o_grid.addWidget(self.chk_devtools, 0, 0, 1, 2)

        # Parallel Chrome Workers (1-50) — available for all modes
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
            "In Combined Pipeline mode, parallel workers accelerate both the Export phase\n"
            "and the Upload phase sequentially.\n"
            "Max supported: 50 — start lower (5–10) and scale up as the site allows."
        )
        self.spin_workers.setStyleSheet(
            f"QSpinBox {{ background: {BG_INPUT}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER_SUBTLE}; border-radius: 4px; padding: 2px 6px; }}"
        )

        lbl_workers_hint = QLabel("(1 = sequential, 2–50 = parallel Chrome instances)")
        lbl_workers_hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; border: none;")

        workers_h.addWidget(lbl_workers)
        workers_h.addWidget(self.spin_workers)
        workers_h.addWidget(lbl_workers_hint)
        workers_h.addStretch()

        o_grid.addWidget(workers_frame, 1, 0, 1, 2)
        ic_layout.insertWidget(ic_layout.count() - 1, opts_group)

    # ------------------------------------------------------------------
    # Reactive Mode Switching & Navigation Hooks
    # ------------------------------------------------------------------
    def set_sub_feature(self, sub_feature_id: str):
        """Called by MainWindow sidebar sub-navigation clicks."""
        if "export" in sub_feature_id:
            self.set_mode("export")
        elif "upload" in sub_feature_id:
            self.set_mode("upload")
        elif "pipeline" in sub_feature_id or "both" in sub_feature_id or "sync" in sub_feature_id:
            self.set_mode("pipeline")

    def set_mode(self, mode: str):
        """Switch active operation mode between 'export', 'upload', and 'pipeline'."""
        self.mode = mode

        # Reflect in radio buttons
        if mode == "export" and not self.rb_export.isChecked():
            self.rb_export.setChecked(True)
        elif mode == "upload" and not self.rb_upload.isChecked():
            self.rb_upload.setChecked(True)
        elif mode == "pipeline" and not self.rb_pipeline.isChecked():
            self.rb_pipeline.setChecked(True)

        is_export = (mode == "export")
        is_upload = (mode == "upload")
        is_pipe = (mode == "pipeline")

        # Directory pickers visibility
        self.row_download_dir.setVisible(is_export)
        self.row_source_dir.setVisible(is_upload)
        self.row_staging_dir.setVisible(is_pipe)

        # Cards visibility
        self.card_export_options.setVisible(is_export or is_pipe)
        self.card_upload_options.setVisible(is_upload or is_pipe)
        self.card_pipeline_options.setVisible(is_pipe)

        # Cross-portal picker rows
        self.row_target_cred.setVisible(is_pipe and not self.chk_same_cred.isChecked())
        self.row_target_links.setVisible(is_pipe and not self.chk_same_links.isChecked())

        # Update Banner & Telemetry Tool Mode
        if is_export:
            self.lbl_banner_title.setText("📥 Mode: File Exporter")
            self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_PRIMARY}; border: none;")
            self.lbl_banner_desc.setText("Scrapes and downloads student documents from web portals into organized local directories.")
            self.tool_mode = "download"
        elif is_upload:
            self.lbl_banner_title.setText("📤 Mode: File Uploader")
            self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_INFO}; border: none;")
            self.lbl_banner_desc.setText("Batch-uploads local student directories into portal document categories, preserving folder structures.")
            self.tool_mode = "upload"
        else:
            self.lbl_banner_title.setText("🔄 Mode: Export & Upload Pipeline (End-to-End Sync)")
            self.lbl_banner_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_SUCCESS}; border: none;")
            self.lbl_banner_desc.setText("Automated two-stage sync: Downloads documents into staging folder, then batch-uploads them to target portal.")
            self.tool_mode = "download"

    # ------------------------------------------------------------------
    # Input Serialization & Task Description
    # ------------------------------------------------------------------
    def get_current_inputs(self) -> dict:
        mode = self.mode

        # Common checks
        if not self.cred_path or not os.path.isfile(self.cred_path):
            QMessageBox.warning(self, "Missing Credentials", "Please select a valid credentials file.")
            return {}
        if not self.links_path or not os.path.isfile(self.links_path):
            QMessageBox.warning(self, "Missing Links", "Please select a valid record links CSV file.")
            return {}

        n_workers = self.spin_workers.value()
        auto_devtools = self.chk_devtools.isChecked()

        if mode == "export":
            if not self.download_dir or not os.path.isdir(self.download_dir):
                QMessageBox.warning(self, "Missing Directory", "Please select a valid destination save directory.")
                return {}
            return {
                "mode": "export",
                "cred_path": self.cred_path,
                "links_path": self.links_path,
                "download_dir": self.download_dir,
                "folder_template": self.txt_template.text().strip(),
                "segments": self.txt_segments.text().strip(),
                "category_col": self.txt_category_col.text().strip(),
                "extensions": self.txt_extensions.text().strip(),
                "allow_dups": self.chk_allow_dups.isChecked(),
                "auto_devtools": auto_devtools,
                "n_workers": n_workers,
            }

        elif mode == "upload":
            if not self.source_dir or not os.path.isdir(self.source_dir):
                QMessageBox.warning(self, "Missing Directory", "Please select a valid source files directory.")
                return {}
            target_folder = self.combo_folder.currentText()
            if self.combo_folder.currentIndex() == 3:
                target_folder = self.txt_custom_folder.text().strip() or "Custom"
            return {
                "mode": "upload",
                "cred_path": self.cred_path,
                "source_dir": self.source_dir,
                "links_path": self.links_path,
                "target_folder": target_folder,
                "skip_dups": self.chk_skip_dups.isChecked(),
                "auto_devtools": auto_devtools,
                "n_workers": n_workers,
            }

        else:  # "pipeline"
            effective_staging = self.staging_dir or self.download_dir or self.source_dir
            if not effective_staging or not os.path.isdir(effective_staging):
                QMessageBox.warning(self, "Missing Directory", "Please select a valid staging/working directory.")
                return {}

            target_cred = self.cred_path if self.chk_same_cred.isChecked() else self.target_cred_path
            if not target_cred or not os.path.isfile(target_cred):
                QMessageBox.warning(self, "Missing Target Credentials", "Please select a valid target credentials file.")
                return {}

            target_links = self.links_path if self.chk_same_links.isChecked() else self.target_links_path
            if not target_links or not os.path.isfile(target_links):
                QMessageBox.warning(self, "Missing Target Links", "Please select a valid target links CSV file.")
                return {}

            target_folder = self.combo_folder.currentText()
            if self.combo_folder.currentIndex() == 3:
                target_folder = self.txt_custom_folder.text().strip() or "Custom"

            return {
                "mode": "pipeline",
                "cred_path": self.cred_path,
                "target_cred_path": target_cred,
                "links_path": self.links_path,
                "target_links_path": target_links,
                "staging_dir": effective_staging,
                "folder_template": self.txt_template.text().strip() or DEFAULT_TEMPLATE,
                "segments": self.txt_segments.text().strip(),
                "category_col": self.txt_category_col.text().strip(),
                "extensions": self.txt_extensions.text().strip(),
                "allow_dups": self.chk_allow_dups.isChecked(),
                "target_folder": target_folder,
                "skip_dups": self.chk_skip_dups.isChecked(),
                "cleanup_staging": self.chk_cleanup_staging.isChecked(),
                "auto_devtools": auto_devtools,
                "n_workers": n_workers,
            }

    def describe_task_inputs(self, inputs: dict) -> str:
        mode = inputs.get("mode", self.mode)
        n = inputs.get("n_workers", 1)
        workers_str = f" | {n} workers" if n > 1 else ""

        if mode == "export":
            cred = os.path.basename(inputs.get("cred_path", ""))
            links = os.path.basename(inputs.get("links_path", ""))
            return f"Exporter | Cred: {cred} | Links: {links}{workers_str}"
        elif mode == "upload":
            cred = os.path.basename(inputs.get("cred_path", ""))
            src = os.path.basename(inputs.get("source_dir", ""))
            return f"Uploader | Cred: {cred} | Folder: {src}{workers_str}"
        else:
            cred = os.path.basename(inputs.get("cred_path", ""))
            stg = os.path.basename(inputs.get("staging_dir", ""))
            return f"Pipeline | Cred: {cred} | Staging: {stg}{workers_str}"

    # ------------------------------------------------------------------
    # Backend Task Execution
    # ------------------------------------------------------------------
    def execute_task_backend(self, inputs: dict, **kwargs):
        log_cb = kwargs.get("log_callback", print)
        prog_cb = kwargs.get("progress_callback", lambda c, t: None)
        kpi_cb = kwargs.get("kpi_callback", lambda d: None)
        is_aborted = kwargs.get("is_aborted", lambda: False)

        mode = inputs.get("mode", self.mode)
        n_workers = max(1, inputs.get("n_workers", 1))
        workers_tag = f"parallel ×{n_workers}" if n_workers > 1 else "sequential"

        # ── 1. EXPORT ONLY MODE ───────────────────────────────────────
        if mode == "export":
            self.tool_mode = "download"
            self._log_dir = inputs["download_dir"]

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

            log_cb(f"Initializing File Exporter engine ({workers_tag})...", "INFO")
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

        # ── 2. UPLOAD ONLY MODE ───────────────────────────────────────
        elif mode == "upload":
            self.tool_mode = "upload"
            self._log_dir = inputs["source_dir"]

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
                kpi_cb(data)

            skip_dups = inputs.get("skip_dups", True)
            allow_dups = not skip_dups
            dup_str = "enabled (skip if already uploaded)" if skip_dups else "disabled (force re-upload)"
            log_cb(f"Initializing File Uploader engine ({workers_tag}) | Duplicate check: {dup_str}...", "INFO")
            log_cb("Orbund file type policy active: 42 allowed types enforced, auto-sanitizing filenames.", "INFO")

            common_kwargs = dict(
                config=config,
                students=students,
                source_dir=inputs["source_dir"],
                target_folder_name=inputs.get("target_folder", ""),
                devtools=inputs.get("auto_devtools", False),
                allow_duplicates=allow_dups,
                on_progress=on_progress,
                abort_flag=is_aborted,
                log=lambda lvl, msg: log_cb(msg, lvl.upper()),
            )

            if n_workers > 1:
                self.uploader.run_parallel(n_workers=n_workers, **common_kwargs)
            else:
                self.uploader.run(**common_kwargs)

        # ── 3. COMBINED PIPELINE MODE ─────────────────────────────────
        else:
            staging_dir = inputs["staging_dir"]
            os.makedirs(staging_dir, exist_ok=True)
            self._log_dir = staging_dir

            # ── 3.1 Validate Credentials ──
            log_cb("[PIPELINE] Validating source portal credentials...", "INFO")
            source_config = load_credentials(
                inputs["cred_path"],
                required_keys=["username", "password", "login_url"]
            )
            if not source_config:
                log_cb("[PIPELINE ERROR] Invalid source credentials file.", "ERROR")
                return

            target_cred = inputs.get("target_cred_path") or inputs["cred_path"]
            log_cb("[PIPELINE] Validating target portal credentials...", "INFO")
            target_config = load_credentials(
                target_cred,
                required_keys=["username", "password", "login_url", "login_btn_xpath"]
            )
            if not target_config:
                log_cb("[PIPELINE ERROR] Invalid target credentials file.", "ERROR")
                return

            # ── 3.2 Validate Student Links ──
            log_cb("[PIPELINE] Parsing source links CSV...", "INFO")
            source_students = read_links_csv(inputs["links_path"])
            if not source_students:
                log_cb("[PIPELINE ERROR] Could not parse records from source CSV.", "ERROR")
                return

            target_links = inputs.get("target_links_path") or inputs["links_path"]
            target_students = read_links_csv(target_links)
            if not target_students:
                log_cb("[PIPELINE ERROR] Could not parse records from target CSV.", "ERROR")
                return

            raw_ext = inputs.get("extensions", "").strip()
            filters = {}
            if raw_ext:
                filters["type_include"] = [e.strip().lower() for e in raw_ext.split(",") if e.strip()]

            # ── 3.3 STAGE 1: EXPORT (Parallel) ──
            self.tool_mode = "download"
            log_cb(f"════════════════════════════════════════════════════════════════", "INFO")
            log_cb(f"  [PIPELINE STAGE 1/2] Launching File Exporter ({workers_tag})", "INFO")
            log_cb(f"  Destination Staging Directory: {staging_dir}", "INFO")
            log_cb(f"════════════════════════════════════════════════════════════════", "INFO")

            def on_export_progress(data: dict):
                completed = data.get("completed", data.get("students_traversed", data.get("idx", 0)))
                total = data.get("total_students", data.get("total", len(source_students)))
                if total > 0:
                    prog_cb(completed, total)
                kpi_cb(data)

            export_kwargs = dict(
                config=source_config,
                students=source_students,
                download_dir=staging_dir,
                folder_hierarchy_template=inputs.get("folder_template", DEFAULT_TEMPLATE),
                allow_duplicates=inputs.get("allow_dups", True),
                segment_names_str=inputs.get("segments", ""),
                category_column_index=int(inputs.get("category_col", 2) or 2),
                filters=filters if filters else None,
                devtools=inputs.get("auto_devtools", False),
                on_progress=on_export_progress,
                abort_flag=is_aborted,
                log=lambda lvl, msg: log_cb(f"[EXPORT] {msg}", lvl.upper()),
            )

            try:
                if n_workers > 1:
                    self.exporter.run_parallel(n_workers=n_workers, **export_kwargs)
                else:
                    self.exporter.run(**export_kwargs)
            except Exception as e:
                log_cb(f"[PIPELINE ERROR] Export phase failed with exception: {e}", "ERROR")
                return

            if is_aborted():
                log_cb("[PIPELINE] Execution aborted by user during Export phase.", "WARNING")
                return

            log_cb("[PIPELINE STAGE 1/2] File Exporter finished successfully.", "SUCCESS")

            # ── 3.4 STAGE 2: UPLOAD (Parallel) ──
            self.tool_mode = "upload"
            skip_dups = inputs.get("skip_dups", True)
            allow_dups = not skip_dups
            dup_str = "enabled (skip if already on portal)" if skip_dups else "disabled (force re-upload)"

            log_cb(f"════════════════════════════════════════════════════════════════", "INFO")
            log_cb(f"  [PIPELINE STAGE 2/2] Launching File Uploader ({workers_tag})", "INFO")
            log_cb(f"  Source Staging Directory: {staging_dir}", "INFO")
            log_cb(f"  Duplicate Check: {dup_str}", "INFO")
            log_cb(f"════════════════════════════════════════════════════════════════", "INFO")

            def on_upload_progress(data: dict):
                kpi_cb(data)

            upload_kwargs = dict(
                config=target_config,
                students=target_students,
                source_dir=staging_dir,
                target_folder_name=inputs.get("target_folder", ""),
                devtools=inputs.get("auto_devtools", False),
                allow_duplicates=allow_dups,
                on_progress=on_upload_progress,
                abort_flag=is_aborted,
                log=lambda lvl, msg: log_cb(f"[UPLOAD] {msg}", lvl.upper()),
            )

            try:
                if n_workers > 1:
                    self.uploader.run_parallel(n_workers=n_workers, **upload_kwargs)
                else:
                    self.uploader.run(**upload_kwargs)
            except Exception as e:
                log_cb(f"[PIPELINE ERROR] Upload phase failed with exception: {e}", "ERROR")
                return

            if is_aborted():
                log_cb("[PIPELINE] Execution aborted by user during Upload phase.", "WARNING")
                return

            log_cb("[PIPELINE STAGE 2/2] File Uploader finished successfully.", "SUCCESS")

            # ── 3.5 Optional Staging Directory Cleanup ──
            if inputs.get("cleanup_staging", False) and not is_aborted():
                log_cb("[PIPELINE] Cleaning up temporary staging directory...", "INFO")
                try:
                    for item in os.listdir(staging_dir):
                        item_path = os.path.join(staging_dir, item)
                        if os.path.isfile(item_path):
                            os.remove(item_path)
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path, ignore_errors=True)
                    log_cb("[PIPELINE] Staging directory cleaned successfully.", "INFO")
                except Exception as e:
                    log_cb(f"[PIPELINE WARNING] Could not clean staging files: {e}", "WARNING")

            log_cb("🎉 [PIPELINE] Entire Export & Upload pipeline completed successfully!", "SUCCESS")
