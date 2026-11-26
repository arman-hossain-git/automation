"""
ui_qt/views/settings_view.py
=============================
Modern PySide6 System Settings view.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QComboBox, QGroupBox, QFrame, QScrollArea, QPushButton,
    QMessageBox, QFileDialog, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSizePolicy
)
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING
)
from core.settings_manager import settings
from core.utils import ORBUND_ALLOWED_FILE_TYPES

THEME_MAP = {
    "Same as Local PC (System)": "system",
    "Dark Mode": "dark",
    "Light Mode": "light",
}
REV_THEME_MAP = {v: k for k, v in THEME_MAP.items()}

INACTIVITY_ACTION_MAP = {
    "Close Application (Safely exit)": "close_app",
    "Pause Queue (Wait for user to resume)": "pause_queue",
    "Proceed with Next Task (Continue automatically)": "proceed_next",
    "Stop Automation (Abort remaining tasks)": "stop_automation",
}
REV_INACTIVITY_ACTION_MAP = {v: k for k, v in INACTIVITY_ACTION_MAP.items()}


class SettingsView(QWidget):
    """System Settings View Panel in PySide6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 12px;")
        h_layout = QVBoxLayout(hdr)
        h_layout.setContentsMargins(14, 8, 14, 8)
        lbl_title = QLabel("⚙️  System Settings & Preferences")
        lbl_title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        lbl_sub = QLabel("Configure Chrome driver execution, themes, timeouts, logging, and directories.")
        lbl_sub.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; border: none;")
        h_layout.addWidget(lbl_title)
        h_layout.addWidget(lbl_sub)
        root.addWidget(hdr)

        # Scrollable preferences area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        # --- Group 1: Browser Execution ---
        grp_browser = QGroupBox("🌐  Browser Execution")
        g1 = QGridLayout(grp_browser)
        g1.setSpacing(12)

        self.chk_headless = QCheckBox("Headless Mode (hidden browser)")
        self.chk_headless.setChecked(settings.get("headless", False))
        g1.addWidget(self.chk_headless, 0, 0)

        self.chk_devtools = QCheckBox("Auto-open Chrome DevTools on Launch")
        self.chk_devtools.setChecked(settings.get("auto_open_devtools", False))
        g1.addWidget(self.chk_devtools, 0, 1)

        self.chk_auto_devtools = QCheckBox("Auto-apply learned DevTools requirement per site")
        self.chk_auto_devtools.setChecked(settings.get("auto_apply_learned_devtools", True))
        g1.addWidget(self.chk_auto_devtools, 1, 0, 1, 2)

        scroll_layout.addWidget(grp_browser)

        # --- Group 2: Timeouts & Retries ---
        grp_timeouts = QGroupBox("⏱️  Timeouts & Retries")
        g2 = QGridLayout(grp_timeouts)
        g2.setSpacing(12)

        g2.addWidget(QLabel("Download Timeout (s):"), 0, 0)
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(10, 600)
        self.spin_timeout.setValue(int(settings.get("download_timeout", 120)))
        g2.addWidget(self.spin_timeout, 0, 1)

        g2.addWidget(QLabel("Max Action Wait (s):"), 0, 2)
        self.spin_max_wait = QSpinBox()
        self.spin_max_wait.setRange(5, 300)
        self.spin_max_wait.setValue(int(settings.get("max_action_wait_seconds", 30)))
        g2.addWidget(self.spin_max_wait, 0, 3)

        g2.addWidget(QLabel("Max Retries:"), 1, 0)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(0, 20)
        self.spin_retries.setValue(int(settings.get("max_retries", 3)))
        g2.addWidget(self.spin_retries, 1, 1)

        g2.addWidget(QLabel("Retry Wait (s):"), 1, 2)
        self.spin_retry_wait = QSpinBox()
        self.spin_retry_wait.setRange(1, 120)
        self.spin_retry_wait.setValue(int(settings.get("retry_wait_seconds", 5)))
        g2.addWidget(self.spin_retry_wait, 1, 3)

        g2.addWidget(QLabel("Inter-Task Timeout (min):"), 2, 0)
        self.spin_inter_task = QSpinBox()
        self.spin_inter_task.setRange(1, 120)
        self.spin_inter_task.setValue(int(settings.get("inter_task_timeout_minutes", 15)))
        g2.addWidget(self.spin_inter_task, 2, 1)

        g2.addWidget(QLabel("On Inactivity Timeout:"), 2, 2)
        self.combo_inactivity_action = QComboBox()
        self.combo_inactivity_action.addItems(list(INACTIVITY_ACTION_MAP.keys()))
        cur_action = settings.get("inter_task_inactivity_action", "close_app")
        self.combo_inactivity_action.setCurrentText(
            REV_INACTIVITY_ACTION_MAP.get(cur_action, "Close Application (Safely exit)")
        )
        g2.addWidget(self.combo_inactivity_action, 2, 3)

        scroll_layout.addWidget(grp_timeouts)

        # --- Group 3: Logging & Behavior ---
        grp_logging = QGroupBox("📋  Logging & Behavior")
        g3 = QGridLayout(grp_logging)
        g3.setSpacing(12)

        g3.addWidget(QLabel("Log Level:"), 0, 0)
        self.combo_log_level = QComboBox()
        self.combo_log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.combo_log_level.setCurrentText(settings.get("log_level", "INFO"))
        g3.addWidget(self.combo_log_level, 0, 1)

        self.chk_confirm_abort = QCheckBox("Confirm before aborting automation")
        self.chk_confirm_abort.setChecked(settings.get("confirm_on_abort", True))
        g3.addWidget(self.chk_confirm_abort, 0, 2)

        scroll_layout.addWidget(grp_logging)

        # --- Group 4: Default Directories ---
        grp_dirs = QGroupBox("📂  Default Directories")
        g4 = QVBoxLayout(grp_dirs)
        g4.setSpacing(8)

        dir_row = QHBoxLayout()
        lbl_dir = QLabel("Default Download Directory:")
        lbl_dir.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY}; min-width: 180px;")
        self.txt_default_dir = QLineEdit(settings.get("default_download_dir", ""))
        self.txt_default_dir.setPlaceholderText("Leave blank to use system default")
        btn_browse_dir = QPushButton("Browse...")
        btn_browse_dir.setCursor(Qt.PointingHandCursor)
        btn_browse_dir.clicked.connect(self._browse_default_dir)
        dir_row.addWidget(lbl_dir)
        dir_row.addWidget(self.txt_default_dir, stretch=1)
        dir_row.addWidget(btn_browse_dir)
        g4.addLayout(dir_row)

        scroll_layout.addWidget(grp_dirs)

        # --- Group 5: Orbund Allowed File Types ---
        grp_orbund = QGroupBox("🗂️  Orbund Allowed File Types")
        g5_outer = QVBoxLayout(grp_orbund)
        g5_outer.setSpacing(8)

        lbl_orbund_info = QLabel(
            "Orbund only accepts specific file types. The list below shows all currently allowed types.\n"
            "Built-in types are enforced by default. Use the toggle to disable one temporarily.\n"
            "Add new types as Orbund expands support — they are saved to your preferences."
        )
        lbl_orbund_info.setWordWrap(True)
        lbl_orbund_info.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        g5_outer.addWidget(lbl_orbund_info)

        # Table: Extension | MIME Type | Source | Enabled
        self._orbund_table = QTableWidget()
        self._orbund_table.setColumnCount(4)
        self._orbund_table.setHorizontalHeaderLabels(["Extension", "MIME Type", "Source", "Enabled"])
        self._orbund_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._orbund_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._orbund_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._orbund_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._orbund_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._orbund_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._orbund_table.setAlternatingRowColors(True)
        self._orbund_table.verticalHeader().setVisible(False)
        self._orbund_table.setMinimumHeight(220)
        self._orbund_table.setMaximumHeight(360)
        self._orbund_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        g5_outer.addWidget(self._orbund_table)

        # Row count label
        self._lbl_orbund_count = QLabel()
        self._lbl_orbund_count.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        g5_outer.addWidget(self._lbl_orbund_count)

        # Add-new-type row
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self._txt_new_ext = QLineEdit()
        self._txt_new_ext.setPlaceholderText("ext  (e.g. webp)")
        self._txt_new_ext.setMaximumWidth(90)
        self._txt_new_mime = QLineEdit()
        self._txt_new_mime.setPlaceholderText("MIME type  (e.g. image/webp)  — optional")
        btn_add_ext = QPushButton("➕  Add Extension")
        btn_add_ext.setCursor(Qt.PointingHandCursor)
        btn_add_ext.setObjectName("btn_success")
        btn_add_ext.clicked.connect(self._add_orbund_extension)
        add_row.addWidget(QLabel("New extension:"))
        add_row.addWidget(self._txt_new_ext)
        add_row.addWidget(QLabel("MIME:"))
        add_row.addWidget(self._txt_new_mime, stretch=1)
        add_row.addWidget(btn_add_ext)
        g5_outer.addLayout(add_row)

        scroll_layout.addWidget(grp_orbund)

        # Populate the table now
        self._populate_orbund_table()

        # --- Group: Local AI & Adaptive Automation (Ollama) ---
        grp_ai = QGroupBox("🧠  Local AI & Adaptive Automation (Ollama)")
        g_ai = QVBoxLayout(grp_ai)
        g_ai.setSpacing(10)

        ai_status_row = QHBoxLayout()
        self.lbl_ai_status = QLabel("Checking Local AI status...")
        self.lbl_ai_status.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY};")
        btn_test_ai = QPushButton("🔍 Test Connection")
        btn_test_ai.setCursor(Qt.PointingHandCursor)
        btn_test_ai.clicked.connect(lambda: self._test_ollama_connection(show_popup=True))
        ai_status_row.addWidget(self.lbl_ai_status)
        ai_status_row.addStretch()
        ai_status_row.addWidget(btn_test_ai)
        g_ai.addLayout(ai_status_row)

        ai_grid = QGridLayout()
        ai_grid.setSpacing(10)

        self.chk_ai_self_healing = QCheckBox("Autonomous Browser Self-Healing (Auto-repair broken buttons/XPaths)")
        self.chk_ai_self_healing.setChecked(settings.get("enable_ai_self_healing", True))
        ai_grid.addWidget(self.chk_ai_self_healing, 0, 0)

        self.chk_ai_harmonizer = QCheckBox("Adaptive Schema AI (Auto-match cryptic or non-standard file columns)")
        self.chk_ai_harmonizer.setChecked(settings.get("enable_ai_harmonizer", True))
        ai_grid.addWidget(self.chk_ai_harmonizer, 0, 1)

        ai_grid.addWidget(QLabel("Local Model:"), 1, 0)
        self.combo_ai_model = QComboBox()
        self.combo_ai_model.addItems(["qwen2.5:7b", "llama3.1:8b", "phi3:mini"])
        self.combo_ai_model.setCurrentText(settings.get("ollama_model", "qwen2.5:7b"))
        ai_grid.addWidget(self.combo_ai_model, 1, 1)

        g_ai.addLayout(ai_grid)
        scroll_layout.addWidget(grp_ai)

        # Trigger initial check on show
        QTimer.singleShot(500, self._test_ollama_connection)

        # --- Group 6: Appearance ---
        grp_theme = QGroupBox("🎨  Appearance")
        g5 = QHBoxLayout(grp_theme)
        g5.setSpacing(12)
        g5.addWidget(QLabel("Color Theme:"))
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(list(THEME_MAP.keys()))
        cur = settings.get("theme", "system")
        self.combo_theme.setCurrentText(REV_THEME_MAP.get(cur, "Same as Local PC (System)"))
        g5.addWidget(self.combo_theme)
        g5.addStretch()

        scroll_layout.addWidget(grp_theme)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        root.addWidget(scroll, stretch=1)

        # Pinned bottom action bar
        btn_bar = QFrame()
        btn_bar.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px;")
        b_layout = QHBoxLayout(btn_bar)
        b_layout.setContentsMargins(14, 8, 14, 8)
        b_layout.setSpacing(10)

        btn_save = QPushButton("💾  Save Preferences")
        btn_save.setObjectName("btn_success")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_settings)
        b_layout.addWidget(btn_save)

        btn_reset = QPushButton("🔄  Reset to Defaults")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset_settings)
        b_layout.addWidget(btn_reset)
        b_layout.addStretch()

        root.addWidget(btn_bar)

    def _browse_default_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Default Download Directory")
        if path:
            self.txt_default_dir.setText(path)

    def _save_settings(self):
        settings.set("headless", self.chk_headless.isChecked())
        settings.set("auto_open_devtools", self.chk_devtools.isChecked())
        settings.set("auto_apply_learned_devtools", self.chk_auto_devtools.isChecked())
        settings.set("download_timeout", self.spin_timeout.value())
        settings.set("max_action_wait_seconds", self.spin_max_wait.value())
        settings.set("max_retries", self.spin_retries.value())
        settings.set("retry_wait_seconds", self.spin_retry_wait.value())
        settings.set("inter_task_timeout_minutes", self.spin_inter_task.value())
        settings.set("inter_task_inactivity_action", INACTIVITY_ACTION_MAP.get(
            self.combo_inactivity_action.currentText(), "close_app"
        ))
        settings.set("log_level", self.combo_log_level.currentText())
        settings.set("confirm_on_abort", self.chk_confirm_abort.isChecked())
        settings.set("default_download_dir", self.txt_default_dir.text().strip())
        settings.set("theme", THEME_MAP.get(self.combo_theme.currentText(), "system"))

        # Local AI Settings
        settings.set("enable_ai_self_healing", self.chk_ai_self_healing.isChecked())
        settings.set("enable_ai_harmonizer", self.chk_ai_harmonizer.isChecked())
        settings.set("ollama_model", self.combo_ai_model.currentText())

        # Orbund file type table → persist disabled built-ins and user-added extras
        self._collect_orbund_table_state()

        settings.save()
        QMessageBox.information(self, "Settings Saved", "Preferences have been saved successfully.")

    def _reset_settings(self):
        result = QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to factory defaults?",
            QMessageBox.Yes | QMessageBox.No
        )
        if result == QMessageBox.Yes:
            settings.reset_to_defaults()
            self._refresh_from_settings()

    def _refresh_from_settings(self):
        self.chk_headless.setChecked(settings.get("headless", False))
        self.chk_devtools.setChecked(settings.get("auto_open_devtools", False))
        self.chk_auto_devtools.setChecked(settings.get("auto_apply_learned_devtools", True))
        self.spin_timeout.setValue(int(settings.get("download_timeout", 120)))
        self.spin_max_wait.setValue(int(settings.get("max_action_wait_seconds", 30)))
        self.spin_retries.setValue(int(settings.get("max_retries", 3)))
        self.spin_retry_wait.setValue(int(settings.get("retry_wait_seconds", 5)))
        self.spin_inter_task.setValue(int(settings.get("inter_task_timeout_minutes", 15)))
        cur_action = settings.get("inter_task_inactivity_action", "close_app")
        self.combo_inactivity_action.setCurrentText(
            REV_INACTIVITY_ACTION_MAP.get(cur_action, "Close Application (Safely exit)")
        )
        self.combo_log_level.setCurrentText(settings.get("log_level", "INFO"))
        self.chk_confirm_abort.setChecked(settings.get("confirm_on_abort", True))
        self.txt_default_dir.setText(settings.get("default_download_dir", ""))
        self.chk_ai_self_healing.setChecked(settings.get("enable_ai_self_healing", True))
        self.chk_ai_harmonizer.setChecked(settings.get("enable_ai_harmonizer", True))
        self.combo_ai_model.setCurrentText(settings.get("ollama_model", "qwen2.5:7b"))
        self._test_ollama_connection()
        # Repopulate Orbund table from current settings
        self._populate_orbund_table()

    def _test_ollama_connection(self, show_popup: bool = False):
        """Checks if local Ollama daemon is reachable (auto-starting if installed) and updates status pill."""
        from core.ollama_engine import ollama_engine
        if ollama_engine.ensure_running():
            ollama_engine.warm_up_async()
            ver = ollama_engine.get_version() or "Active"
            models = ollama_engine.list_installed_models()
            model_info = f" ({len(models)} model{'s' if len(models) != 1 else ''} available)" if models else ""
            self.lbl_ai_status.setText(f"🟢 Connected to Local Ollama v{ver}{model_info}")
            self.lbl_ai_status.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ACCENT_SUCCESS};")
            if show_popup:
                installed_str = ", ".join(models) if models else "None"
                QMessageBox.information(
                    self,
                    "Local AI Connected",
                    f"Local Ollama v{ver} is running and ready.\n\n"
                    f"Selected model: {self.combo_ai_model.currentText()}\n"
                    f"Installed models: {installed_str}",
                )
        else:
            self.lbl_ai_status.setText("⚪ Local Ollama Offline (Rule-based deterministic fallback active)")
            self.lbl_ai_status.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_MUTED};")
            if show_popup:
                QMessageBox.warning(
                    self,
                    "Local AI Offline",
                    "Could not reach Local Ollama on this computer.\n\n"
                    "The software will continue working normally using standard rule-based matching.\n"
                    "To enable AI features, please start Ollama and click Test Connection again.",
                )

    # ------------------------------------------------------------------
    # Orbund file-type table helpers
    # ------------------------------------------------------------------

    def _populate_orbund_table(self):
        """Fill the Orbund file-type table from ORBUND_ALLOWED_FILE_TYPES + user additions."""
        disabled: list = settings.get("orbund_disabled_extensions", []) or []
        extra: dict = settings.get("orbund_extra_extensions", {}) or {}

        self._orbund_table.setRowCount(0)

        # 1. Built-in extensions (sorted)
        for ext in sorted(ORBUND_ALLOWED_FILE_TYPES.keys()):
            mime = ORBUND_ALLOWED_FILE_TYPES[ext]
            self._add_orbund_table_row(ext, mime, is_builtin=True,
                                       enabled=(ext not in disabled))

        # 2. User-added extensions (sorted)
        for ext in sorted(extra.keys()):
            mime = extra[ext] or "application/octet-stream"
            self._add_orbund_table_row(ext, mime, is_builtin=False, enabled=True)

        total = self._orbund_table.rowCount()
        builtin_count = len(ORBUND_ALLOWED_FILE_TYPES)
        user_count = len(extra)
        self._lbl_orbund_count.setText(
            f"{total} types total  ({builtin_count} built-in, {user_count} user-added)"
        )

    def _add_orbund_table_row(self, ext: str, mime: str, is_builtin: bool, enabled: bool):
        """Append one row to the Orbund file-type table."""
        row = self._orbund_table.rowCount()
        self._orbund_table.insertRow(row)

        # Col 0: extension
        item_ext = QTableWidgetItem(f".{ext}")
        item_ext.setTextAlignment(Qt.AlignCenter)
        self._orbund_table.setItem(row, 0, item_ext)

        # Col 1: MIME type
        self._orbund_table.setItem(row, 1, QTableWidgetItem(mime))

        # Col 2: source label
        source_lbl = "Built-in" if is_builtin else "User-added"
        item_src = QTableWidgetItem(source_lbl)
        item_src.setTextAlignment(Qt.AlignCenter)
        if not is_builtin:
            item_src.setForeground(Qt.darkGreen)
        self._orbund_table.setItem(row, 2, item_src)

        # Col 3: checkbox (enable/disable) OR remove button for user-added
        if is_builtin:
            chk = QCheckBox()
            chk.setChecked(enabled)
            chk.setToolTip("Uncheck to temporarily block this built-in type from uploading")
            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.addWidget(chk)
            cell_layout.setAlignment(Qt.AlignCenter)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            self._orbund_table.setCellWidget(row, 3, cell_widget)
        else:
            btn_remove = QPushButton("✕  Remove")
            btn_remove.setToolTip(f"Remove user-added extension .{ext}")
            btn_remove.setCursor(Qt.PointingHandCursor)
            btn_remove.setStyleSheet("color: #c0392b; font-size: 11px;")
            # Capture ext in closure
            btn_remove.clicked.connect(lambda checked=False, e=ext: self._remove_orbund_extension(e))
            self._orbund_table.setCellWidget(row, 3, btn_remove)

    def _collect_orbund_table_state(self):
        """Read table checkboxes and persist disabled built-ins to settings."""
        disabled = []
        for row in range(self._orbund_table.rowCount()):
            src_item = self._orbund_table.item(row, 2)
            if src_item and src_item.text() == "Built-in":
                ext_item = self._orbund_table.item(row, 0)
                cell_w = self._orbund_table.cellWidget(row, 3)
                if ext_item and cell_w:
                    chk = cell_w.findChild(QCheckBox)
                    if chk and not chk.isChecked():
                        ext = ext_item.text().lstrip('.')
                        if ext:
                            disabled.append(ext)
        settings.set("orbund_disabled_extensions", sorted(disabled))

    def _add_orbund_extension(self):
        """Validate and add a new user extension to settings + table."""
        import re
        raw_ext = self._txt_new_ext.text().strip().lower().lstrip('.')
        raw_mime = self._txt_new_mime.text().strip()

        if not raw_ext:
            QMessageBox.warning(self, "Invalid Extension", "Please enter an extension (e.g. 'webp').")
            return

        if not re.fullmatch(r'[a-z0-9]{1,20}', raw_ext):
            QMessageBox.warning(
                self, "Invalid Extension",
                f"'{raw_ext}' is not a valid extension.\n"
                "Use only lowercase letters and digits (1–20 chars), no dot or spaces."
            )
            return

        if raw_ext in ORBUND_ALLOWED_FILE_TYPES:
            QMessageBox.information(
                self, "Already Built-in",
                f".{raw_ext} is already in the built-in Orbund list.\n"
                "To re-enable it, check its checkbox in the table."
            )
            return

        extra: dict = dict(settings.get("orbund_extra_extensions", {}) or {})
        if raw_ext in extra:
            QMessageBox.information(
                self, "Already Added",
                f".{raw_ext} is already in your user-added list."
            )
            return

        mime = raw_mime if raw_mime else "application/octet-stream"
        extra[raw_ext] = mime
        settings.set("orbund_extra_extensions", extra)
        # Repopulate table to reflect the new entry
        self._populate_orbund_table()
        self._txt_new_ext.clear()
        self._txt_new_mime.clear()

    def _remove_orbund_extension(self, ext: str):
        """Remove a user-added extension from settings and refresh table."""
        extra: dict = dict(settings.get("orbund_extra_extensions", {}) or {})
        if ext in extra:
            del extra[ext]
            settings.set("orbund_extra_extensions", extra)
        self._populate_orbund_table()
