"""
ui_qt/views/settings_view.py
=============================
Modern PySide6 System Settings view.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QComboBox, QGroupBox, QFrame, QScrollArea, QPushButton,
    QMessageBox, QFileDialog, QSpinBox
)
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING
)
from core.settings_manager import settings

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

        # --- Group 5: Appearance ---
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
