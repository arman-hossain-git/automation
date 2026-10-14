"""
ui_qt/views/documentation_view.py
==================================
Modern PySide6 Documentation & User Guide view.
"""

import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTabWidget, QPlainTextEdit, QScrollArea, QMessageBox
)
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BG_CONSOLE, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_INFO
)

OVERVIEW_TEXT = """\
================================================================================
 🚀 UNIFIED SYSTEM AUTOMATION SUITE - OVERVIEW & QUICK START GUIDE
================================================================================

Welcome to the Unified System Automation Suite. This software consolidates all
your web automation workflows into 3 powerful, modular automation engines.

────────────────────────────────────────────────────────────────────────────────
1. THE 3 CORE AUTOMATION MODULES
────────────────────────────────────────────────────────────────────────────────

📥  1. FILE EXPORTER
    • Exports & downloads files from PCR Educator, Orbund, or any web portal.
    • The system LEARNS site structure after every run — no manual preset required.
    • Supports file extension filters, category filtering, and folder hierarchy
      templates using tokens: {studentId}, {segment}, {category}, {year}.

📤  2. FILE UPLOADER
    • Uploads local student folders & files directly into PCR Files & Documents.
    • Target Folder Options: Root, Admissions Inquiry, Students Inquiry, Custom.
    • Preserves local folder taxonomy and logs all uploads with timestamps.

🎓  3. GRADE SUBMISSION
    • Submits final grade status ('Yes') and date/score per student in batch.
    • Dynamic Field Detection: auto-detects 'Adjust Score' field vs 'Grade' menu.
    • Un-submit mode reverts grades to 'No' and clears submission status.

────────────────────────────────────────────────────────────────────────────────
2. QUICK START (All Modules)
────────────────────────────────────────────────────────────────────────────────

  Step 1. Prepare a credentials.txt file (see Credentials tab for format).
  Step 2. Prepare a Record Links CSV (see CSV Formats tab).
  Step 3. Open the relevant tool panel and select your credential + CSV files.
  Step 4. (Optional) Add additional configurations to the Task Queue for batching.
  Step 5. Click ▶ Start Automation.

The system logs all activity in the Live Activity console and saves execution
logs to the configured log directory automatically.
"""

CREDENTIALS_TEMPLATE = """\
# credentials.txt — Template
# ============================================================
# Instructions:
#   Line 1: Full URL of the login page (https://...)
#   Line 2: Username / email
#   Line 3: Password
#   Line 4: (Optional) CSS selector for username field
#   Line 5: (Optional) CSS selector for password field
#   Line 6: (Optional) CSS selector for submit button
# ============================================================
https://portal.example.com/login
your_username@institution.edu
YourSecurePassword123
# username_field_selector (optional)
# password_field_selector (optional)
# submit_button_selector  (optional)
"""

CSV_FORMATS_TEXT = """\
================================================================================
 CSV FILE FORMATS — Reference Guide
================================================================================

────────────────────────────────────────────────────────────────────────────────
FILE EXPORTER — record_links.csv
────────────────────────────────────────────────────────────────────────────────

Required Columns (exact names, case-insensitive):
  • student_id   — Unique student identifier
  • record_url   — Direct URL to the student's documents/files page
  • (Optional) Any additional metadata columns are preserved in logs

Example:
  student_id,record_url
  12345,https://portal.example.com/students/12345/files
  12346,https://portal.example.com/students/12346/files

────────────────────────────────────────────────────────────────────────────────
FILE UPLOADER — record_links.csv
────────────────────────────────────────────────────────────────────────────────

Required Columns:
  • student_id   — Unique student identifier
  • upload_url   — Direct URL to the student's upload/files page

The system maps local folder names to student_id to find and upload files.

────────────────────────────────────────────────────────────────────────────────
GRADE SUBMISSION — grade_links.csv
────────────────────────────────────────────────────────────────────────────────

Required Columns:
  • student_id     — Student ID (must match portal record)
  • grade_url      — Direct URL to the student's grade record page
  • completion_date — Date in MM/DD/YYYY format (e.g. 05/15/2025)
  • score          — Numeric grade or letter grade (e.g. 85, A, Pass)

Optional Columns:
  • adjust_score   — Override score for 'Adjust Score' field if different
"""


class DocumentationView(QWidget):
    """Documentation & User Guide view in PySide6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 12px;")
        h_layout = QVBoxLayout(hdr)
        h_layout.setContentsMargins(14, 8, 14, 8)
        lbl_title = QLabel("📖  Documentation & User Guide")
        lbl_title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        lbl_sub = QLabel("Technical guide, credentials templates, CSV formats, and troubleshooting reference.")
        lbl_sub.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; border: none;")
        h_layout.addWidget(lbl_title)
        h_layout.addWidget(lbl_sub)
        root.addWidget(hdr)

        # Tab widget
        tabs = QTabWidget()
        root.addWidget(tabs, stretch=1)

        # ── Tab 1: Overview ────────────────────────────────────────────
        tab_overview = QWidget()
        o_layout = QVBoxLayout(tab_overview)
        o_layout.setContentsMargins(0, 0, 0, 0)
        txt_overview = QPlainTextEdit(OVERVIEW_TEXT)
        txt_overview.setReadOnly(True)
        txt_overview.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: none;
                padding: 14px;
            }}
        """)
        o_layout.addWidget(txt_overview)
        tabs.addTab(tab_overview, " 🚀 Overview ")

        # ── Tab 2: Credentials Template ────────────────────────────────
        tab_creds = QWidget()
        c_layout = QVBoxLayout(tab_creds)
        c_layout.setContentsMargins(12, 12, 12, 12)
        c_layout.setSpacing(8)

        c_btn_row = QHBoxLayout()
        lbl_c = QLabel("credentials.txt Template — Copy and edit this to prepare your login file:")
        lbl_c.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY};")
        c_btn_row.addWidget(lbl_c)
        c_btn_row.addStretch()

        btn_copy_cred = QPushButton("📋  Copy to Clipboard")
        btn_copy_cred.setObjectName("btn_primary")
        btn_copy_cred.setCursor(Qt.PointingHandCursor)
        btn_copy_cred.clicked.connect(lambda: self._copy_to_clipboard(CREDENTIALS_TEMPLATE, "Credentials template"))
        c_btn_row.addWidget(btn_copy_cred)
        c_layout.addLayout(c_btn_row)

        txt_creds = QPlainTextEdit(CREDENTIALS_TEMPLATE)
        txt_creds.setReadOnly(True)
        txt_creds.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: 1px solid {BORDER_MUTED};
                border-radius: 6px;
                padding: 12px;
            }}
        """)
        c_layout.addWidget(txt_creds, stretch=1)
        tabs.addTab(tab_creds, " 🔐 Credentials ")

        # ── Tab 3: CSV Formats ─────────────────────────────────────────
        tab_csv = QWidget()
        csv_layout = QVBoxLayout(tab_csv)
        csv_layout.setContentsMargins(0, 0, 0, 0)
        txt_csv = QPlainTextEdit(CSV_FORMATS_TEXT)
        txt_csv.setReadOnly(True)
        txt_csv.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: none;
                padding: 14px;
            }}
        """)
        csv_layout.addWidget(txt_csv)
        tabs.addTab(tab_csv, " 📊 CSV Formats ")

    def _copy_to_clipboard(self, text: str, label: str = "Content"):
        try:
            import subprocess
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
        except Exception:
            try:
                import pyperclip
                pyperclip.copy(text)
            except Exception:
                QMessageBox.warning(self, "Copy Failed", f"Could not copy {label} to clipboard.")
                return
        QMessageBox.information(self, "Copied", f"{label} copied to clipboard successfully.")
