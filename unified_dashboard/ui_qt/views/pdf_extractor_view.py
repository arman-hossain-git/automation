"""
ui_qt/views/pdf_extractor_view.py
===================================
Comprehensive PySide6 PDF → Excel Extractor workspace.

Implements full feature parity with legacy pdf_extractor_ui.py:
  Tab 1: Setup & Presets
    - Source folder and output file pickers
    - Preset management (Load, Save As, Delete, and ML Auto-Detect via synthesize_preset_from_pdf)
    - Active Configuration Overview with quick-launch CTA to Field Mapper
  Tab 2: Visual Field Mapper
    - Top bar: Sample PDF selector, Pick PDF, Random Sample, Run Extraction Preview
    - Left Top: Line Viewer with color syntax highlighting (matched, separator, excluded, section)
      and drag-selection / click tracking
    - Left Bottom: Preview Panel with Extracted Data Table (QTableView) & Summary Log
    - Right Top: Action Center with 4 categorized sub-tabs (Labels, Rows, Exclude, Sections)
      wired to build_anchored_pattern, build_row_pattern, etc.
    - Right Bottom: Rules Panel with QTableView of active rules, Edit Rules modal dialog,
      and Remove Selected rule
  Tab 3: Advanced JSON Config Editor
    - Raw JSON editor with Validate, Reset to Blank Template, and Apply JSON
  Tab 4: Review & Self-Tune
    - Full PdfReviewView integration
  Tab 5: Live Activity & Logs
    - Task execution, KPIs, live console, and post-run auto-review
"""

import os
import re
import json
import random
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QGroupBox, QComboBox, QLineEdit, QSplitter, QDialog,
    QPlainTextEdit, QTextEdit, QTableView, QAbstractItemView, QHeaderView,
    QMessageBox, QFileDialog, QInputDialog, QTabWidget, QScrollArea
)
from PySide6.QtGui import (
    QColor, QBrush, QTextCharFormat, QTextCursor, QFont, QKeySequence
)

from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BG_CONSOLE, BG_INPUT, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, TEXT_ACCENT,
    ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER,
    ACCENT_WARNING, ACCENT_DANGER, ACCENT_INFO
)
from ui_qt.components.base_tool_widget import BaseToolWidget
from ui_qt.views.pdf_review_view import PdfReviewView

from modules.pdf_extractor import (
    BatchPDFToExcelExporter, ConfigurableExtractor, DEFAULT_CONFIG,
    list_presets, load_preset, save_preset, delete_preset, ensure_configs_dir,
    build_anchored_pattern, build_row_pattern, describe_field_map, remove_field_entry,
    load_extractor_state, save_extractor_state,
)
from modules.pdf_spatial_ml import synthesize_preset_from_pdf
from core.learning_system import learning_engine
from core.utils import describe_exception


# ---------------------------------------------------------------------------
# Models for Preview and Rules Tables
# ---------------------------------------------------------------------------
class RulesTableModel(QAbstractTableModel):
    """Table model for Configured Rules in Visual Field Mapper."""
    HEADERS = ["Field / Scope", "Pattern / Value"]

    def __init__(self):
        super().__init__()
        self._rows: List[dict] = []

    def set_rules(self, rows: List[dict]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return r.get("label", "")
            else:
                pat = r.get("pattern", "")
                return (pat[:70] + "...") if len(pat) > 70 else pat
        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 0:
                return QBrush(QColor(TEXT_PRIMARY))
            return QBrush(QColor(TEXT_SECONDARY))
        return None


class PreviewDataModel(QAbstractTableModel):
    """Dynamic table model for the Preview extracted tabular data."""

    def __init__(self):
        super().__init__()
        self._headers: List[str] = ["Status"]
        self._rows: List[List[str]] = [["No tabular data extracted. Check config patterns."]]

    def set_data(self, headers: List[str], rows: List[List[str]]):
        self.beginResetModel()
        self._headers = headers if headers else ["Status"]
        self._rows = rows if rows else [["No data extracted."]]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = index.row()
        c = index.column()
        row = self._rows[r]
        if role == Qt.ItemDataRole.DisplayRole:
            return row[c] if c < len(row) else ""
        return None


# ---------------------------------------------------------------------------
# Edit Rules Modal Dialog
# ---------------------------------------------------------------------------
class EditRulesDialog(QDialog):
    """Modal popup dialog to edit extraction rules in JSON."""

    def __init__(self, parent=None, initial_config=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Rules Specification")
        self.resize(720, 560)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        lbl = QLabel("✏️  Edit Extraction Rules Specification (JSON)")
        lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        layout.addWidget(lbl)

        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: 1px solid {BORDER_MUTED};
                border-radius: 6px;
                padding: 10px;
            }}
        """)
        raw = json.dumps(initial_config or DEFAULT_CONFIG, indent=2, ensure_ascii=False)
        self.editor.setPlainText(raw)
        layout.addWidget(self.editor, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_save = QPushButton("💾  Save & Apply Rules")
        btn_save.setObjectName("btn_success")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_and_close)
        btn_row.addWidget(btn_save)

        btn_val = QPushButton("✔  Validate")
        btn_val.setObjectName("btn_primary")
        btn_val.setCursor(Qt.PointingHandCursor)
        btn_val.clicked.connect(self._validate)
        btn_row.addWidget(btn_val)

        btn_rst = QPushButton("🔄  Reset Defaults")
        btn_rst.setCursor(Qt.PointingHandCursor)
        btn_rst.clicked.connect(self._reset_defaults)
        btn_row.addWidget(btn_rst)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)
        self.result_config = None

    def _save_and_close(self):
        try:
            cfg = json.loads(self.editor.toPlainText())
            self.result_config = cfg
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Invalid JSON", f"Syntax Error in JSON rules:\n{e}")

    def _validate(self):
        try:
            json.loads(self.editor.toPlainText())
            QMessageBox.information(self, "Valid JSON", "JSON rules syntax is valid!")
        except Exception as e:
            QMessageBox.critical(self, "Invalid JSON", f"Syntax Error:\n{e}")

    def _reset_defaults(self):
        r = QMessageBox.question(self, "Reset Rules", "Reset rules to blank default template?", QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes:
            self.editor.setPlainText(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Main PdfExtractorView
# ---------------------------------------------------------------------------
class PdfExtractorView(BaseToolWidget):
    """5-tab PDF → Excel Extractor workspace in PySide6 with full feature parity."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            title="PDF → Excel Extractor",
            description=(
                "Batch-extracts structured data from a folder of same-type PDF documents "
                "into a single Excel workbook using a fully editable extraction config."
            )
        )

        self.source_folder = ""
        self.output_xlsx = ""
        self.exporter = BatchPDFToExcelExporter()

        # Selection state in Visual Field Mapper
        self._verify_pdf_path: str = ""
        self._verify_lines: List[str] = []
        self._verify_selected_line: int = -1
        self._pending_label: Optional[dict] = None  # {"text": ..., "line_idx": ...}
        self._row_draft: Optional[dict] = None      # {"line_idx": ..., "cols": [...]}
        self._field_row_map: dict = {}
        self.preset_var = None

        self._rules_model = RulesTableModel()
        self._preview_model = PreviewDataModel()

        ensure_configs_dir()

        # Preserve execution tab and insert intermediate tabs
        self.notebook.setTabText(0, " 📁 1. Setup & Presets ")
        self.tab_execution = self.notebook.widget(1)
        self.notebook.removeTab(1)

        self._build_mapper_tab()
        self._build_json_tab()
        self._build_review_tab()

        self.notebook.addTab(self.tab_execution, " 📊 5. Live Activity & Logs ")

        self._sample_needs_loading = False
        self._review_needs_loading = False

        self._populate_setup_tab()
        self._restore_saved_state()
        self.notebook.currentChanged.connect(self._on_notebook_tab_changed)

    def _on_notebook_tab_changed(self, index: int):
        # Tab 1: Visual Field Mapper
        if index == 1:
            if getattr(self, "_sample_needs_loading", False) and self._verify_pdf_path:
                self._sample_needs_loading = False
                self._load_verify_pdf(self._verify_pdf_path)
        # Tab 3: Review & Self-Tune
        elif index == 3:
            if getattr(self, "_review_needs_loading", False) and self._verify_pdf_path and hasattr(self, "review_view"):
                self._review_needs_loading = False
                try:
                    self.review_view.load_pdf_for_review(self._verify_pdf_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Workspace Tab Creation
    # ------------------------------------------------------------------
    def _build_mapper_tab(self):
        self.tab_mapper = QWidget()
        self.notebook.insertTab(1, self.tab_mapper, " 🎯 2. Visual Field Mapper ")
        self._build_mapper_ui()

    def _build_json_tab(self):
        self.tab_json = QWidget()
        self.notebook.insertTab(2, self.tab_json, " 🛠 3. Advanced JSON ")
        self._build_json_ui()

    def _build_review_tab(self):
        self.tab_review = QWidget()
        self.notebook.insertTab(3, self.tab_review, " 🔍 4. Review & Self-Tune ")
        review_layout = QVBoxLayout(self.tab_review)
        review_layout.setContentsMargins(0, 0, 0, 0)
        self.review_view = PdfReviewView(self.tab_review, main_view=self)
        review_layout.addWidget(self.review_view)

    # ------------------------------------------------------------------
    # TAB 1: Setup & Presets
    # ------------------------------------------------------------------
    def _populate_setup_tab(self):
        ic = self.inputs_layout

        # ── Paths Card ────────────────────────────────────────────────
        paths_group = QGroupBox("📂  Source PDF Folder & Output File Locations")
        p_layout = QVBoxLayout(paths_group)
        p_layout.setSpacing(8)

        self._source_picker_frame = self.create_file_picker_row(
            p_layout, "Source Folder (PDFs to process):",
            self._on_source_folder_set, is_directory=True
        )
        self.lbl_source_folder = None
        for lbl in self._source_picker_frame.findChildren(QLabel):
            if "directory" in lbl.text().lower() or "selected" in lbl.text().lower():
                self.lbl_source_folder = lbl
                break

        out_row = QHBoxLayout()
        lbl_out = QLabel("Output Excel File (.xlsx):")
        lbl_out.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY}; min-width: 200px;")
        out_row.addWidget(lbl_out)

        self.lbl_output = QLabel("No path selected")
        self.lbl_output.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
        out_row.addWidget(self.lbl_output, stretch=1)

        btn_saveas = QPushButton("Save As...")
        btn_saveas.setCursor(Qt.PointingHandCursor)
        btn_saveas.clicked.connect(self._pick_output_file)
        out_row.addWidget(btn_saveas)
        p_layout.addLayout(out_row)

        ic.insertWidget(ic.count() - 1, paths_group)

        # ── Presets Card ──────────────────────────────────────────────
        preset_group = QGroupBox("⚙️  Extraction Config Presets")
        pr_layout = QVBoxLayout(preset_group)
        pr_layout.setSpacing(8)

        pr_row = QHBoxLayout()
        pr_row.setSpacing(8)
        lbl_preset = QLabel("Active Config Preset:")
        lbl_preset.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY};")
        pr_row.addWidget(lbl_preset)

        self.preset_var = QComboBox()
        self.preset_var.setMinimumWidth(220)
        self.preset_var.currentTextChanged.connect(self._on_preset_dropdown_changed)
        pr_row.addWidget(self.preset_var, stretch=1)

        btn_load = QPushButton("📥  Load")
        btn_load.setObjectName("btn_primary")
        btn_load.setCursor(Qt.PointingHandCursor)
        btn_load.clicked.connect(self._on_load_preset)
        pr_row.addWidget(btn_load)

        btn_save = QPushButton("💾  Save As...")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._on_save_preset)
        pr_row.addWidget(btn_save)

        btn_del = QPushButton("🗑  Delete")
        btn_del.setObjectName("btn_danger")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self._on_delete_preset)
        pr_row.addWidget(btn_del)

        btn_ml = QPushButton("🤖  Auto-Detect")
        btn_ml.setObjectName("btn_success")
        btn_ml.setCursor(Qt.PointingHandCursor)
        btn_ml.clicked.connect(self._on_auto_detect_ml)
        pr_row.addWidget(btn_ml)

        pr_layout.addLayout(pr_row)
        ic.insertWidget(ic.count() - 1, preset_group)

        # ── Overview Card with Quick-Launch CTA ───────────────────────
        overview_group = QGroupBox("📊  Active Extraction Configuration Overview")
        o_layout = QHBoxLayout(overview_group)
        o_layout.setContentsMargins(14, 12, 14, 12)

        self.lbl_config_summary = QLabel("Config summary loading...")
        self.lbl_config_summary.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; line-height: 1.5;")
        self.lbl_config_summary.setWordWrap(True)
        o_layout.addWidget(self.lbl_config_summary, stretch=1)

        cta_col = QVBoxLayout()
        cta_col.addStretch()
        btn_open_mapper = QPushButton("🎯  Open Visual Field Mapper →")
        btn_open_mapper.setObjectName("btn_primary")
        btn_open_mapper.setCursor(Qt.PointingHandCursor)
        btn_open_mapper.setStyleSheet("font-weight: 700; padding: 10px 18px; font-size: 13px;")
        btn_open_mapper.clicked.connect(lambda: self.notebook.setCurrentIndex(1))
        cta_col.addWidget(btn_open_mapper)
        cta_col.addStretch()
        o_layout.addLayout(cta_col)

        ic.insertWidget(ic.count() - 1, overview_group)
        self._refresh_preset_menu()

    # ------------------------------------------------------------------
    # TAB 2: Visual Field Mapper
    # ------------------------------------------------------------------
    def _build_mapper_ui(self):
        root = QVBoxLayout(self.tab_mapper)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Top Control Bar
        ctrl_bar = QFrame()
        ctrl_bar.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px;")
        c_layout = QHBoxLayout(ctrl_bar)
        c_layout.setContentsMargins(10, 6, 10, 6)
        c_layout.setSpacing(8)

        lbl_sp = QLabel("Sample PDF:")
        lbl_sp.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        c_layout.addWidget(lbl_sp)

        self.lbl_verify_pdf = QLabel("No PDF loaded")
        self.lbl_verify_pdf.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED}; border: none;")
        c_layout.addWidget(self.lbl_verify_pdf, stretch=1)

        btn_pick = QPushButton("📂  Pick PDF")
        btn_pick.setCursor(Qt.PointingHandCursor)
        btn_pick.clicked.connect(self._browse_sample_pdf)
        c_layout.addWidget(btn_pick)

        btn_rnd = QPushButton("🔀  Random Sample")
        btn_rnd.setCursor(Qt.PointingHandCursor)
        btn_rnd.clicked.connect(self._load_random_sample_pdf)
        c_layout.addWidget(btn_rnd)

        btn_run = QPushButton("▶  Run Extraction Preview")
        btn_run.setObjectName("btn_success")
        btn_run.setCursor(Qt.PointingHandCursor)
        btn_run.clicked.connect(self._run_verify_extraction)
        c_layout.addWidget(btn_run)

        root.addWidget(ctrl_bar)

        # Main Splitter (Left: Line Viewer & Preview | Right: Actions & Rules)
        main_split = QSplitter(Qt.Horizontal)

        # ── LEFT PANE ──────────────────────────────────────────────────
        left_widget = QWidget()
        l_vbox = QVBoxLayout(left_widget)
        l_vbox.setContentsMargins(0, 0, 0, 0)
        l_vbox.setSpacing(8)

        left_split = QSplitter(Qt.Vertical)

        # Left Top: Line Viewer
        line_box = QGroupBox("📄  PDF Line Viewer — drag-select text to tag")
        lb_layout = QVBoxLayout(line_box)
        lb_layout.setContentsMargins(6, 6, 6, 6)

        self.txt_verify_lines = QPlainTextEdit()
        self.txt_verify_lines.setReadOnly(True)
        self.txt_verify_lines.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_verify_lines.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: 1px solid {BORDER_MUTED};
                border-radius: 6px;
                padding: 6px;
                line-height: 1.5;
            }}
        """)
        self.txt_verify_lines.cursorPositionChanged.connect(self._on_lines_selection_changed)
        lb_layout.addWidget(self.txt_verify_lines)
        left_split.addWidget(line_box)

        # Left Bottom: Preview Panel
        preview_box = QGroupBox("🔍  Preview")
        pb_layout = QVBoxLayout(preview_box)
        pb_layout.setContentsMargins(6, 6, 6, 6)

        self.preview_nb = QTabWidget()

        # Preview Sub-Tab A: Table View
        tab_prev_table = QWidget()
        pt_layout = QVBoxLayout(tab_prev_table)
        pt_layout.setContentsMargins(0, 4, 0, 0)
        self.table_preview = QTableView()
        self.table_preview.setModel(self._preview_model)
        self.table_preview.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_preview.setAlternatingRowColors(False)
        pt_layout.addWidget(self.table_preview)
        self.preview_nb.addTab(tab_prev_table, " 📊 Extracted Data Table ")

        # Preview Sub-Tab B: Summary Log
        tab_prev_log = QWidget()
        pl_layout = QVBoxLayout(tab_prev_log)
        pl_layout.setContentsMargins(0, 4, 0, 0)
        self.txt_verify_results = QPlainTextEdit()
        self.txt_verify_results.setReadOnly(True)
        self.txt_verify_results.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: 1px solid {BORDER_MUTED};
                border-radius: 6px;
                padding: 6px;
            }}
        """)
        pl_layout.addWidget(self.txt_verify_results)
        self.preview_nb.addTab(tab_prev_log, " 📝 Summary Log ")

        pb_layout.addWidget(self.preview_nb)
        left_split.addWidget(preview_box)

        left_split.setSizes([350, 250])
        l_vbox.addWidget(left_split)
        main_split.addWidget(left_widget)

        # ── RIGHT PANE ─────────────────────────────────────────────────
        right_widget = QWidget()
        r_vbox = QVBoxLayout(right_widget)
        r_vbox.setContentsMargins(0, 0, 0, 0)
        r_vbox.setSpacing(8)

        right_split = QSplitter(Qt.Vertical)

        # Right Top: Action Center
        action_box = QGroupBox("🎯  Action Center")
        ab_layout = QVBoxLayout(action_box)
        ab_layout.setContentsMargins(8, 8, 8, 8)
        ab_layout.setSpacing(6)

        # Selection status display
        self.lbl_selection = QLabel("No selection yet.")
        self.lbl_selection.setStyleSheet(f"""
            background-color: {BG_CARD_ALT};
            color: {TEXT_ACCENT};
            font-family: Cascadia Code, Consolas, monospace;
            font-size: 11px;
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 4px;
            padding: 6px;
        """)
        self.lbl_selection.setWordWrap(True)
        ab_layout.addWidget(self.lbl_selection)

        status_strip = QHBoxLayout()
        self.lbl_pending_label = QLabel("🏷️ Label: (none)")
        self.lbl_pending_label.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        status_strip.addWidget(self.lbl_pending_label)
        status_strip.addSpacing(16)

        self.lbl_row_draft = QLabel("📊 Row draft: (empty)")
        self.lbl_row_draft.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        status_strip.addWidget(self.lbl_row_draft)
        status_strip.addStretch()
        ab_layout.addLayout(status_strip)

        # Action sub-tabs
        self.actions_nb = QTabWidget()

        # 1. Labels tab
        tab_labels = QWidget()
        tl_layout = QVBoxLayout(tab_labels)
        tl_layout.setContentsMargins(6, 6, 6, 6)
        tl_layout.addWidget(QLabel("Tag field names, record titles, or summary key-values:"))
        row_lbls = QHBoxLayout()
        row_lbls.setSpacing(6)
        btn_set_l = QPushButton("🏷️  Set as Label")
        btn_set_l.setCursor(Qt.PointingHandCursor)
        btn_set_l.clicked.connect(self._set_pending_label)
        row_lbls.addWidget(btn_set_l)

        btn_clr_l = QPushButton("❌  Clear Label")
        btn_clr_l.setCursor(Qt.PointingHandCursor)
        btn_clr_l.clicked.connect(self._clear_pending_label)
        row_lbls.addWidget(btn_clr_l)

        btn_name = QPushButton("→ Name Field")
        btn_name.setObjectName("btn_primary")
        btn_name.setCursor(Qt.PointingHandCursor)
        btn_name.clicked.connect(self._mark_name_field)
        row_lbls.addWidget(btn_name)

        btn_addr = QPushButton("→ Address Stop")
        btn_addr.setObjectName("btn_primary")
        btn_addr.setCursor(Qt.PointingHandCursor)
        btn_addr.clicked.connect(self._mark_address_stop)
        row_lbls.addWidget(btn_addr)

        btn_sum = QPushButton("→ Summary Field")
        btn_sum.setObjectName("btn_primary")
        btn_sum.setCursor(Qt.PointingHandCursor)
        btn_sum.clicked.connect(self._mark_summary_field)
        row_lbls.addWidget(btn_sum)
        tl_layout.addLayout(row_lbls)
        self.actions_nb.addTab(tab_labels, " 🏷️ Labels ")

        # 2. Rows tab
        tab_rows = QWidget()
        tr_layout = QVBoxLayout(tab_rows)
        tr_layout.setContentsMargins(6, 6, 6, 6)
        tr_layout.addWidget(QLabel("Select values on a repeating table line to build column patterns:"))
        row_rows = QHBoxLayout()
        row_rows.setSpacing(6)
        btn_add_col = QPushButton("➕  Add as Row Column")
        btn_add_col.setObjectName("btn_success")
        btn_add_col.setCursor(Qt.PointingHandCursor)
        btn_add_col.clicked.connect(self._add_row_column)
        row_rows.addWidget(btn_add_col)

        btn_fin_row = QPushButton("✅  Finalize Row Pattern")
        btn_fin_row.setObjectName("btn_success")
        btn_fin_row.setCursor(Qt.PointingHandCursor)
        btn_fin_row.clicked.connect(self._finalize_row_pattern)
        row_rows.addWidget(btn_fin_row)

        btn_clr_row = QPushButton("🗑️  Clear Row Draft")
        btn_clr_row.setCursor(Qt.PointingHandCursor)
        btn_clr_row.clicked.connect(self._clear_row_draft)
        row_rows.addWidget(btn_clr_row)
        tr_layout.addLayout(row_rows)
        self.actions_nb.addTab(tab_rows, " 📊 Rows ")

        # 3. Exclude tab
        tab_excl = QWidget()
        te_layout = QVBoxLayout(tab_excl)
        te_layout.setContentsMargins(6, 6, 6, 6)
        te_layout.addWidget(QLabel("Mark lines that split records, or patterns to exclude entirely:"))
        row_excl = QHBoxLayout()
        row_excl.setSpacing(6)
        btn_sep = QPushButton("✂️  Record Separator")
        btn_sep.setCursor(Qt.PointingHandCursor)
        btn_sep.clicked.connect(self._mark_record_separator)
        row_excl.addWidget(btn_sep)

        btn_excl_line = QPushButton("🚫  Exclude Line")
        btn_excl_line.setObjectName("btn_danger")
        btn_excl_line.setCursor(Qt.PointingHandCursor)
        btn_excl_line.clicked.connect(self._mark_exclude)
        row_excl.addWidget(btn_excl_line)
        row_excl.addStretch()
        te_layout.addLayout(row_excl)
        self.actions_nb.addTab(tab_excl, " ✂️ Exclude ")

        # 4. Sections tab
        tab_sec = QWidget()
        ts_layout = QVBoxLayout(tab_sec)
        ts_layout.setContentsMargins(6, 6, 6, 6)
        ts_layout.addWidget(QLabel("Define recurring multi-line blocks (e.g. employment, programs):"))
        row_secs = QHBoxLayout()
        row_secs.setSpacing(6)
        btn_new_sec = QPushButton("🔄  New Repeating Section")
        btn_new_sec.setStyleSheet("background-color: #A855F7; color: white; font-weight: 700; border-radius: 6px; padding: 6px 10px;")
        btn_new_sec.setCursor(Qt.PointingHandCursor)
        btn_new_sec.clicked.connect(self._mark_repeating_section)
        row_secs.addWidget(btn_new_sec)

        btn_sub_f = QPushButton("➕  Add Section Sub-Field")
        btn_sub_f.setStyleSheet("background-color: #A855F7; color: white; font-weight: 700; border-radius: 6px; padding: 6px 10px;")
        btn_sub_f.setCursor(Qt.PointingHandCursor)
        btn_sub_f.clicked.connect(self._add_section_subfield)
        row_secs.addWidget(btn_sub_f)

        btn_nxt_l = QPushButton("↳  Set Next-Line Value")
        btn_nxt_l.setStyleSheet("background-color: #A855F7; color: white; font-weight: 700; border-radius: 6px; padding: 6px 10px;")
        btn_nxt_l.setCursor(Qt.PointingHandCursor)
        btn_nxt_l.clicked.connect(self._mark_next_line_value)
        row_secs.addWidget(btn_nxt_l)
        ts_layout.addLayout(row_secs)
        self.actions_nb.addTab(tab_sec, " 🔄 Sections ")

        ab_layout.addWidget(self.actions_nb)
        right_split.addWidget(action_box)

        # Right Bottom: Rules Panel
        rules_box = QGroupBox("📋  Rules")
        rb_layout = QVBoxLayout(rules_box)
        rb_layout.setContentsMargins(8, 8, 8, 8)
        rb_layout.setSpacing(6)

        self.table_rules = QTableView()
        self.table_rules.setModel(self._rules_model)
        self.table_rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_rules.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_rules.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_rules.setAlternatingRowColors(False)
        self.table_rules.doubleClicked.connect(self._open_edit_rules_dialog)
        rb_layout.addWidget(self.table_rules)

        r_btns = QHBoxLayout()
        r_btns.setSpacing(6)
        btn_edit_r = QPushButton("✏️  Edit Rules")
        btn_edit_r.setObjectName("btn_success")
        btn_edit_r.setCursor(Qt.PointingHandCursor)
        btn_edit_r.clicked.connect(self._open_edit_rules_dialog)
        r_btns.addWidget(btn_edit_r)

        btn_rem_r = QPushButton("❌  Remove Selected")
        btn_rem_r.setObjectName("btn_danger")
        btn_rem_r.setCursor(Qt.PointingHandCursor)
        btn_rem_r.clicked.connect(self._remove_selected_field)
        r_btns.addWidget(btn_rem_r)

        btn_ref_r = QPushButton("🔄  Refresh Table")
        btn_ref_r.setCursor(Qt.PointingHandCursor)
        btn_ref_r.clicked.connect(self._refresh_fields_table)
        r_btns.addWidget(btn_ref_r)
        r_btns.addStretch()

        rb_layout.addLayout(r_btns)
        right_split.addWidget(rules_box)

        right_split.setSizes([260, 340])
        r_vbox.addWidget(right_split)
        main_split.addWidget(right_widget)

        main_split.setSizes([600, 500])
        root.addWidget(main_split, stretch=1)

        # Bottom Status Bar
        self.lbl_verify_status = QLabel("Load a PDF above to begin verification.")
        self.lbl_verify_status.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        root.addWidget(self.lbl_verify_status)

    # ------------------------------------------------------------------
    # TAB 3: Advanced JSON Editor
    # ------------------------------------------------------------------
    def _build_json_ui(self):
        root = QVBoxLayout(self.tab_json)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        hint = QLabel(
            "Directly view or modify the underlying JSON configuration rules. "
            "Changes sync automatically with the Visual Field Mapper when applied."
        )
        hint.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        root.addWidget(hint)

        self.txt_config = QPlainTextEdit()
        self.txt_config.setStyleSheet(f"""
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
        root.addWidget(self.txt_config, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_val = QPushButton("✔  Validate JSON")
        btn_val.setObjectName("btn_primary")
        btn_val.setCursor(Qt.PointingHandCursor)
        btn_val.clicked.connect(self._validate_config)
        btn_row.addWidget(btn_val)

        btn_rst = QPushButton("🔄  Reset to Blank Template")
        btn_rst.setCursor(Qt.PointingHandCursor)
        btn_rst.clicked.connect(self._reset_to_blank_template)
        btn_row.addWidget(btn_rst)

        btn_app = QPushButton("⚡  Apply JSON → Rules Table")
        btn_app.setObjectName("btn_success")
        btn_app.setCursor(Qt.PointingHandCursor)
        btn_app.clicked.connect(self._on_apply_raw_json)
        btn_row.addWidget(btn_app)

        btn_row.addStretch()
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Handlers & Actions for Setup / Presets & Persistence
    # ------------------------------------------------------------------
    def save_active_config(self, cfg: dict):
        """Called whenever config is updated in review/mapper/json editor to guarantee it persists."""
        preset_name = self.preset_var.currentText() if self.preset_var else ""
        save_extractor_state({
            "last_preset": preset_name or "(unsaved / custom)",
            "active_config": cfg
        })

    def _restore_saved_state(self):
        self._restoring_state = True
        try:
            state = load_extractor_state()
            last_source = state.get("last_source_folder", "")
            if last_source and os.path.isdir(last_source):
                self.source_folder = last_source
                if hasattr(self, "lbl_source_folder") and self.lbl_source_folder:
                    self.lbl_source_folder.setText(os.path.normpath(last_source))
                    self.lbl_source_folder.setStyleSheet(f"font-size: 12px; color: {TEXT_PRIMARY}; border: none;")

            last_out = state.get("last_output_xlsx", "")
            if last_out:
                self.output_xlsx = last_out
                if hasattr(self, "lbl_output") and self.lbl_output:
                    self.lbl_output.setText(os.path.normpath(last_out))
                    self.lbl_output.setStyleSheet(f"font-size: 12px; color: {TEXT_PRIMARY};")

            # Refresh preset list
            self._refresh_preset_menu()

            last_preset = state.get("last_preset", "")
            loaded_cfg = None

            # 1. Try loading last selected named preset
            if last_preset and last_preset in list_presets():
                try:
                    loaded_cfg = load_preset(last_preset)
                    idx = self.preset_var.findText(last_preset)
                    if idx >= 0:
                        self.preset_var.blockSignals(True)
                        self.preset_var.setCurrentIndex(idx)
                        self.preset_var.blockSignals(False)
                except Exception:
                    loaded_cfg = None

            # 2. If last_preset was unsaved / custom or loading failed, try saved active_config
            if not loaded_cfg:
                active_cfg = state.get("active_config")
                if active_cfg and isinstance(active_cfg, dict) and (active_cfg.get("summary_fields") or active_cfg.get("data_rows") or active_cfg.get("record_separator")):
                    loaded_cfg = active_cfg
                    idx = self.preset_var.findText("(unsaved / custom)")
                    if idx >= 0:
                        self.preset_var.blockSignals(True)
                        self.preset_var.setCurrentIndex(idx)
                        self.preset_var.blockSignals(False)

            # 3. If still not loaded, find preset with most verified runs in learning_data
            if not loaded_cfg:
                presets = list_presets()
                if presets:
                    best_preset = None
                    max_runs = -1
                    profiles = learning_engine._data.get("pdf_extractor_profiles", {})
                    for p in presets:
                        runs = profiles.get(p, {}).get("verified_runs", 0)
                        if runs > max_runs:
                            max_runs = runs
                            best_preset = p
                    chosen = best_preset if (best_preset and max_runs > 0) else presets[0]
                    try:
                        loaded_cfg = load_preset(chosen)
                        idx = self.preset_var.findText(chosen)
                        if idx >= 0:
                            self.preset_var.blockSignals(True)
                            self.preset_var.setCurrentIndex(idx)
                            self.preset_var.blockSignals(False)
                    except Exception:
                        loaded_cfg = None

            if not loaded_cfg:
                loaded_cfg = DEFAULT_CONFIG

            self._load_config_into_editor(loaded_cfg)
            self._refresh_fields_table()

            # Restore sample PDF (path only; actual extraction is deferred until tab is viewed)
            last_sample = state.get("last_sample_pdf", "")
            if last_sample and os.path.isfile(last_sample):
                self._verify_pdf_path = last_sample
                self.lbl_verify_pdf.setText(os.path.basename(last_sample))
                self.lbl_verify_status.setText(
                    f"Selected sample: {os.path.basename(last_sample)}. "
                    "Switch to Visual Field Mapper or click 'Run Extraction Preview' to analyze."
                )
                self._sample_needs_loading = True
                self._review_needs_loading = True
            elif self.source_folder and os.path.isdir(self.source_folder):
                self._sample_needs_loading = True
                self._review_needs_loading = True
        finally:
            self._restoring_state = False

    def _on_preset_dropdown_changed(self, name: str):
        if not name or getattr(self, "_restoring_state", False):
            return
        save_extractor_state({"last_preset": name})
        if name != "(unsaved / custom)":
            try:
                cfg = load_preset(name)
                self._load_config_into_editor(cfg)
                self._refresh_fields_table()
                if self._verify_lines:
                    self._render_verify_lines(self._verify_lines, cfg)
                save_extractor_state({"active_config": cfg})
                if hasattr(self, "review_view"):
                    self.review_view.refresh_extraction_preview()
                    self.review_view._update_stats_label()
                self.log("info", f"Loaded config preset '{name}'.")
            except Exception:
                pass
        else:
            if hasattr(self, "review_view"):
                self.review_view._update_stats_label()

    def _on_source_folder_set(self, path: str):
        self.source_folder = path
        save_extractor_state({"last_source_folder": path})
        self._auto_load_sample_pdf(path)
        if hasattr(self, "review_view"):
            try:
                self.review_view._refresh_sample_dropdown()
            except Exception:
                pass

    def _pick_output_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Select Output Excel File",
            filter="Excel Workbook (*.xlsx);;All Files (*.*)"
        )
        if path:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            self.output_xlsx = path
            save_extractor_state({"last_output_xlsx": path})
            self.lbl_output.setText(os.path.normpath(path))
            self.lbl_output.setStyleSheet(f"font-size: 12px; color: {TEXT_PRIMARY};")

    def _refresh_preset_menu(self):
        if self.preset_var is None:
            return
        current = self.preset_var.currentText()
        self.preset_var.blockSignals(True)
        self.preset_var.clear()
        self.preset_var.addItem("(unsaved / custom)")
        for name in list_presets():
            self.preset_var.addItem(name)
        idx = self.preset_var.findText(current)
        if idx >= 0:
            self.preset_var.setCurrentIndex(idx)
        self.preset_var.blockSignals(False)

    def _on_load_preset(self):
        name = self.preset_var.currentText() if self.preset_var else ""
        if not name or name == "(unsaved / custom)":
            QMessageBox.information(self, "Load Preset", "Select a saved preset from the dropdown first.")
            return
        try:
            cfg = load_preset(name)
            self._load_config_into_editor(cfg)
            self._refresh_fields_table()
            if self._verify_lines:
                self._render_verify_lines(self._verify_lines, cfg)
            save_extractor_state({"last_preset": name, "active_config": cfg})
            if hasattr(self, "review_view"):
                self.review_view.refresh_extraction_preview()
                self.review_view._update_stats_label()
            self.log("info", f"Loaded config preset '{name}'.")
        except Exception as e:
            QMessageBox.critical(self, "Load Failed", describe_exception(e))

    def _on_save_preset(self):
        cfg, err = self._parse_editor_json()
        if err:
            QMessageBox.critical(self, "Invalid JSON", err)
            return
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            save_preset(name, cfg)
            self._refresh_preset_menu()
            self.preset_var.setCurrentText(name)
            save_extractor_state({"last_preset": name, "active_config": cfg})
            if hasattr(self, "review_view"):
                self.review_view._update_stats_label()
            self.log("info", f"Saved config preset '{name}'.")
            QMessageBox.information(self, "Saved", f"Config preset '{name}' saved successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", describe_exception(e))

    def _on_delete_preset(self):
        name = self.preset_var.currentText() if self.preset_var else ""
        if not name or name == "(unsaved / custom)":
            return
        r = QMessageBox.question(self, "Delete Preset", f"Delete preset '{name}'? This cannot be undone.", QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        delete_preset(name)
        self._refresh_preset_menu()
        self.preset_var.setCurrentText("(unsaved / custom)")
        save_extractor_state({"last_preset": "(unsaved / custom)"})
        if hasattr(self, "review_view"):
            self.review_view._update_stats_label()
        self.log("info", f"Deleted config preset '{name}'.")

    def _on_auto_detect_ml(self):
        """Synthesizes preset layout rules using spatial ML from a sample PDF."""
        sample_path = self._verify_pdf_path
        if not sample_path or not os.path.isfile(sample_path):
            if self.source_folder and os.path.isdir(self.source_folder):
                for f in os.listdir(self.source_folder):
                    if f.lower().endswith(".pdf"):
                        sample_path = os.path.join(self.source_folder, f)
                        break

        if not sample_path or not os.path.isfile(sample_path):
            sample_path, _ = QFileDialog.getOpenFileName(
                self, "Select Sample PDF for ML Layout Analysis",
                filter="PDF files (*.pdf;*.PDF)"
            )

        if not sample_path or not os.path.isfile(sample_path):
            return

        try:
            preset = synthesize_preset_from_pdf(sample_path)
            doc_title = preset.get("document_title")
            rep_type = preset.get("report_type")
            if doc_title:
                base_title = doc_title
            elif rep_type == "student_schedule":
                base_title = "Student Schedule"
            elif rep_type == "student_transcript":
                base_title = "Student Transcript"
            elif rep_type == "student_ledger":
                base_title = "Student Ledger"
            elif rep_type == "resume":
                base_title = "Resume"
            elif rep_type == "attendance":
                base_title = "Attendance"
            elif rep_type == "invoice":
                base_title = "Invoice"
            else:
                base_title = preset.get("record_separator") or os.path.splitext(os.path.basename(sample_path))[0]
            default_name = f"{base_title} (Auto ML)"
            preset_name, ok = QInputDialog.getText(
                self, "Save ML-Generated Preset",
                "Enter a name for this auto-detected preset:",
                text=default_name
            )
            if not ok or not preset_name.strip():
                return
            preset_name = preset_name.strip()

            save_preset(preset_name, preset)
            self._refresh_preset_menu()
            self.preset_var.setCurrentText(preset_name)
            save_extractor_state({"last_preset": preset_name, "active_config": preset})
            self._load_config_into_editor(preset)
            self._refresh_fields_table()

            self._load_verify_pdf(sample_path)
            if hasattr(self, "review_view"):
                self.review_view.load_pdf_for_review(sample_path)
                self.review_view._update_stats_label()

            QMessageBox.information(
                self, "ML Preset Generated",
                f"Successfully synthesized preset '{preset_name}'!\n\n"
                f"• Detected {len(preset.get('summary_fields', []))} key-value fields\n"
                f"• Detected table with {len(preset.get('data_rows', {}).get('columns', [])) if preset.get('data_rows') else 0} columns\n\n"
                "The preset has been loaded into the Visual Field Mapper and Review tab."
            )
        except Exception as e:
            QMessageBox.critical(self, "ML Analysis Failed", f"Could not synthesize preset: {describe_exception(e)}")

    # ------------------------------------------------------------------
    # Sample PDF Loading & Line Viewer Rendering
    # ------------------------------------------------------------------
    def _browse_sample_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Sample PDF", filter="PDF Files (*.pdf);;All Files (*.*)")
        if path:
            self._load_verify_pdf(path)

    def _load_random_sample_pdf(self):
        folder = self.source_folder
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, "No Folder", "Select a Source Folder on Tab 1 first, then click Random Sample.")
            return
        self._auto_load_sample_pdf(folder)

    def _auto_load_sample_pdf(self, folder: str):
        pdfs = list(Path(folder).glob("*.pdf")) + list(Path(folder).glob("*.PDF"))
        if not pdfs:
            self.lbl_verify_status.setText("No PDFs found in the selected source folder.")
            return
        chosen = random.choice(pdfs)
        self._load_verify_pdf(str(chosen))

    def _load_verify_pdf(self, pdf_path: str):
        self._verify_pdf_path = pdf_path
        save_extractor_state({"last_sample_pdf": pdf_path})
        self._verify_selected_line = -1
        self._pending_label = None
        self._row_draft = None
        self.lbl_pending_label.setText("🏷️ Label: (none)")
        self.lbl_row_draft.setText("📊 Row draft: (empty)")
        self.lbl_selection.setText("No selection yet.")

        cfg, _ = self._parse_editor_json()
        engine = ConfigurableExtractor(cfg or DEFAULT_CONFIG)

        try:
            lines = engine.read_pdf_lines(pdf_path, max_pages=30)
            self._verify_lines = lines
            self._render_verify_lines(lines, cfg or DEFAULT_CONFIG)
            self.lbl_verify_pdf.setText(os.path.basename(pdf_path))
            self.lbl_verify_status.setText(
                f"Loaded {len(lines)} sample lines from {os.path.basename(pdf_path)}. "
                "Drag-select a word or phrase, then use the marking buttons."
            )
        except Exception as e:
            self.lbl_verify_status.setText(f"Error loading PDF: {describe_exception(e)}")

    def _render_verify_lines(self, lines: List[str], config: dict):
        sep = (config.get("record_separator") or "").strip()
        exclude_pats = [re.compile(p, re.IGNORECASE) for p in (config.get("exclude_patterns") or []) if p]

        name_pat_str = (config.get("name_detection") or {}).get("pattern", "")
        try:
            name_pat = re.compile(name_pat_str) if name_pat_str else None
        except re.error:
            name_pat = None

        data_row_str = (config.get("data_rows") or {}).get("pattern", "")
        try:
            data_pat = re.compile(data_row_str) if data_row_str else None
        except re.error:
            data_pat = None

        section_markers = [
            (s.get("marker") or "").strip()
            for s in (config.get("repeating_sections") or [])
            if (s.get("marker") or "").strip()
        ]

        MAX_DISPLAY = 2000
        display_lines = lines[:MAX_DISPLAY]

        text_lines = []
        for i, line in enumerate(display_lines):
            text_lines.append(f"{i+1:4d}  {line}")

        if len(lines) > MAX_DISPLAY:
            text_lines.append(f"     ... [Performance note: showing first {MAX_DISPLAY:,} of {len(lines):,} lines] ...")

        self.txt_verify_lines.setPlainText("\n".join(text_lines))

        # Color syntax formatting
        extra_selections = []
        doc = self.txt_verify_lines.document()

        def _make_fmt(color_hex: str, bold: bool = False) -> QTextCharFormat:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color_hex))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            return fmt

        fmt_matched = _make_fmt("#4ADE80")       # Green
        fmt_sep = _make_fmt("#F59E0B", bold=True) # Amber
        fmt_excl = _make_fmt("#6B7280")           # Gray
        fmt_sec = _make_fmt("#C084FC", bold=True) # Purple

        for i, line in enumerate(display_lines):
            stripped = line.strip()
            chosen_fmt = None
            if sep and sep in line:
                chosen_fmt = fmt_sep
            elif any(m in line for m in section_markers):
                chosen_fmt = fmt_sec
            elif any(p.search(line) for p in exclude_pats):
                chosen_fmt = fmt_excl
            elif (data_pat and data_pat.match(stripped)) or (name_pat and name_pat.match(stripped)):
                chosen_fmt = fmt_matched

            if chosen_fmt:
                block = doc.findBlockByNumber(i)
                if block.isValid():
                    blk_cursor: QTextCursor = QTextCursor(block)
                    blk_cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                    sel = QTextEdit.ExtraSelection()
                    setattr(sel, "cursor", blk_cursor)
                    setattr(sel, "format", chosen_fmt)
                    extra_selections.append(sel)

        self.txt_verify_lines.setExtraSelections(extra_selections)

    def _on_lines_selection_changed(self):
        cursor = self.txt_verify_lines.textCursor()
        line_idx = cursor.blockNumber()
        if 0 <= line_idx < len(self._verify_lines):
            self._verify_selected_line = line_idx
        self._update_selection_label()

    def _get_current_selection(self) -> Optional[dict]:
        cursor = self.txt_verify_lines.textCursor()
        if not cursor.hasSelection():
            return None

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        doc = self.txt_verify_lines.document()
        start_block = doc.findBlock(start)
        end_block = doc.findBlock(end)

        if start_block.blockNumber() != end_block.blockNumber():
            return None

        data_idx = start_block.blockNumber()
        if data_idx < 0 or data_idx >= len(self._verify_lines):
            return None

        raw_line = self._verify_lines[data_idx]
        prefix_len = 6  # "0001  "
        col_start = start - start_block.position()
        col_end = end - start_block.position()

        raw_start = min(max(0, col_start - prefix_len), len(raw_line))
        raw_end = min(max(0, col_end - prefix_len), len(raw_line))

        if raw_end <= raw_start:
            return None

        return {
            "line_idx": data_idx,
            "start": raw_start,
            "end": raw_end,
            "text": raw_line[raw_start:raw_end]
        }

    def _update_selection_label(self):
        sel = self._get_current_selection()
        if sel:
            text = sel["text"].strip()
            display = (text[:80] + "…") if len(text) > 80 else text
            self.lbl_selection.setText(f"Selected (line {sel['line_idx'] + 1}): {display!r}")
        elif 0 <= self._verify_selected_line < len(self._verify_lines):
            text = self._verify_lines[self._verify_selected_line].strip()
            display = (text[:80] + "…") if len(text) > 80 else text
            self.lbl_selection.setText(
                f"No word selected — whole line {self._verify_selected_line + 1} will be used: {display!r}"
            )
        else:
            self.lbl_selection.setText("No selection yet.")

    def _selected_text_or_line(self) -> str:
        sel = self._get_current_selection()
        if sel:
            return sel["text"].strip()
        if 0 <= self._verify_selected_line < len(self._verify_lines):
            return self._verify_lines[self._verify_selected_line].strip()
        return ""

    def _commit_config(self, cfg: dict, log_msg: str = ""):
        self._load_config_into_editor(cfg)
        if self._verify_lines:
            self._render_verify_lines(self._verify_lines, cfg)
        self._refresh_fields_table()
        if log_msg:
            self.log("info", f"[Verify] {log_msg}")

    # ------------------------------------------------------------------
    # Marking Actions (Action Center)
    # ------------------------------------------------------------------
    def _set_pending_label(self):
        sel = self._get_current_selection()
        if not sel:
            QMessageBox.information(self, "No Selection", "Drag-select the label text (e.g. 'GPA:') first.")
            return
        self._pending_label = {"text": sel["text"].strip(), "line_idx": sel["line_idx"]}
        self.lbl_pending_label.setText(
            f"🏷️ Label: {self._pending_label['text']!r} (line {sel['line_idx'] + 1})"
        )

    def _clear_pending_label(self):
        self._pending_label = None
        self.lbl_pending_label.setText("🏷️ Label: (none)")

    def _mark_record_separator(self):
        text = self._selected_text_or_line()
        if not text:
            QMessageBox.information(self, "No Selection", "Select text or click a line first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        cfg["record_separator"] = text
        self._commit_config(cfg, f"Set record_separator = {text!r}")

    def _mark_exclude(self):
        text = self._selected_text_or_line()
        if not text:
            QMessageBox.information(self, "No Selection", "Select text or click a line first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        pat = re.escape(text)
        lst = cfg.get("exclude_patterns") if isinstance(cfg.get("exclude_patterns"), list) else []
        if pat not in lst:
            lst.append(pat)
        cfg["exclude_patterns"] = lst
        self._commit_config(cfg, f"Added exclude_pattern: {pat!r}")

    def _mark_name_field(self):
        sel = self._get_current_selection()
        if not sel:
            QMessageBox.information(self, "No Selection", "Drag-select the name text in a line first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return

        raw_line = self._verify_lines[sel["line_idx"]]
        label_text = ""
        if self._pending_label and self._pending_label["line_idx"] == sel["line_idx"]:
            label_text = self._pending_label["text"]
        pattern = build_anchored_pattern(raw_line, sel["text"], label_text)

        nd = cfg.get("name_detection") if isinstance(cfg.get("name_detection"), dict) else None
        if not nd:
            col, ok = QInputDialog.getText(self, "Output Column", "Column name for this field:", text="record_name")
            if not ok or not col.strip():
                return
            nd = {"output_column": col.strip(), "stop_at": ""}
        nd["pattern"] = pattern
        cfg["name_detection"] = nd
        self._commit_config(cfg, f"Set name_detection.pattern from selection {sel['text']!r}")

    def _mark_address_stop(self):
        text = self._selected_text_or_line()
        if not text:
            QMessageBox.information(self, "No Selection", "Select text or click a line first.")
            return
        text = text[:60]
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        if not isinstance(cfg.get("address_block"), dict):
            cfg["address_block"] = {"output_column": "address", "stop_prefixes": []}
        existing = cfg["address_block"].setdefault("stop_prefixes", [])
        if text not in existing:
            existing.append(text)
        self._commit_config(cfg, f"Added address_block stop_prefix: {text!r}")

    def _mark_summary_field(self):
        sel = self._get_current_selection()
        if not sel:
            QMessageBox.information(self, "No Selection", "Drag-select the value text (e.g. a GPA number) first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return

        raw_line = self._verify_lines[sel["line_idx"]]
        label_text = ""
        default_name = "summary_value"
        if self._pending_label and self._pending_label["line_idx"] == sel["line_idx"]:
            label_text = self._pending_label["text"]
            default_name = re.sub(r"[^A-Za-z0-9]+", "_", label_text).strip("_").lower() or default_name

        pattern = build_anchored_pattern(raw_line, sel["text"], label_text)
        name, ok = QInputDialog.getText(self, "Summary Field Name", "Output column name:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()

        lst = cfg.get("summary_fields") if isinstance(cfg.get("summary_fields"), list) else []
        lst = [sf for sf in lst if sf.get("name") != name]
        lst.append({"name": name, "pattern": pattern})
        cfg["summary_fields"] = lst

        # Also ensure this field is present in output_columns so it's written to the xlsx
        out_cols = cfg.get("output_columns")
        if isinstance(out_cols, list) and name not in out_cols:
            out_cols.append(name)
            cfg["output_columns"] = out_cols

        self._commit_config(cfg, f"Added summary_field {name!r} from selection {sel['text']!r}")

    def _add_row_column(self):
        sel = self._get_current_selection()
        if not sel:
            QMessageBox.information(self, "No Selection", "Drag-select a value on the data row line first.")
            return
        if self._row_draft and self._row_draft["line_idx"] != sel["line_idx"]:
            r = QMessageBox.question(
                self, "New Row Line",
                "Your selection is on a different line than the current column draft. Start a new draft for this line?",
                QMessageBox.Yes | QMessageBox.No
            )
            if r != QMessageBox.Yes:
                return
            self._row_draft = None
        if not self._row_draft:
            self._row_draft = {"line_idx": sel["line_idx"], "cols": []}

        name, ok = QInputDialog.getText(
            self, "Column Name", "Name for this data column:",
            text=f"col_{len(self._row_draft['cols']) + 1}"
        )
        if not ok or not name.strip():
            return
        self._row_draft["cols"].append({
            "name": name.strip(), "start": sel["start"], "end": sel["end"], "text": sel["text"]
        })
        self._refresh_row_draft_label()

    def _refresh_row_draft_label(self):
        if not self._row_draft or not self._row_draft["cols"]:
            self.lbl_row_draft.setText("📊 Row draft: (empty)")
            return
        names = ", ".join(c["name"] for c in self._row_draft["cols"])
        self.lbl_row_draft.setText(
            f"📊 Row draft (line {self._row_draft['line_idx'] + 1}): {names}"
        )

    def _finalize_row_pattern(self):
        if not self._row_draft or not self._row_draft["cols"]:
            QMessageBox.information(self, "No Draft", "Add at least one column from a data row line first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        raw_line = self._verify_lines[self._row_draft["line_idx"]]
        pattern, columns = build_row_pattern(raw_line, self._row_draft["cols"])
        cfg["data_rows"] = {"pattern": pattern, "columns": columns}
        self._commit_config(cfg, f"Generated data_rows pattern with columns {columns}")
        self._row_draft = None
        self._refresh_row_draft_label()

    def _clear_row_draft(self):
        self._row_draft = None
        self._refresh_row_draft_label()

    def _mark_repeating_section(self):
        text = self._selected_text_or_line()
        if not text:
            QMessageBox.information(self, "No Selection", "Select marker text that repeats (e.g. 'Program Description:') first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return

        sections = cfg.get("repeating_sections") if isinstance(cfg.get("repeating_sections"), list) else []
        existing_names = [s.get("name") for s in sections]

        name, ok = QInputDialog.getText(
            self, "Repeating Section Name",
            "Name for this repeating block (e.g. 'program', 'employment'):",
            text=existing_names[0] if len(existing_names) == 1 else "section"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        existing = next((s for s in sections if s.get("name") == name), None)
        if existing:
            existing["marker"] = text
            existing.setdefault("marker_line_fields", [])
            existing.setdefault("sub_fields", [])
        else:
            sections.append({
                "name": name,
                "marker": text,
                "marker_line_fields": [],
                "next_line_value": None,
                "sub_fields": [],
            })
        cfg["repeating_sections"] = sections
        self._commit_config(cfg, f"Set repeating_sections[{name!r}].marker = {text!r}")
        QMessageBox.information(
            self, "Repeating Section Created",
            f"'{name}' will now start a new occurrence every time {text!r} appears inside a record."
        )

    def _pick_section_name(self, cfg: dict, prompt: str = "Which repeating section?") -> Optional[str]:
        sections = cfg.get("repeating_sections") or []
        if not sections:
            QMessageBox.information(self, "No Repeating Section", "Create a repeating section first with 'New Repeating Section'.")
            return None
        if len(sections) == 1:
            return sections[0]["name"]
        names = [s["name"] for s in sections]
        chosen, ok = QInputDialog.getItem(self, "Choose Section", prompt, names, 0, False)
        return chosen if ok else None

    def _add_section_subfield(self):
        sel = self._get_current_selection()
        if not sel:
            QMessageBox.information(self, "No Selection", "Drag-select the value text for this field first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return

        sec_name = self._pick_section_name(cfg)
        if not sec_name:
            return
        sections = cfg.get("repeating_sections") or []
        section = next((s for s in sections if s.get("name") == sec_name), None)
        if not section:
            return

        raw_line = self._verify_lines[sel["line_idx"]]
        label_text = ""
        default_name = "field"
        if self._pending_label and self._pending_label["line_idx"] == sel["line_idx"]:
            label_text = self._pending_label["text"]
            default_name = re.sub(r"[^A-Za-z0-9]+", "_", label_text).strip("_").lower() or default_name

        pattern = build_anchored_pattern(raw_line, sel["text"], label_text)
        field_name, ok = QInputDialog.getText(
            self, "Sub-Field Name", "Output column name for this field (per occurrence):",
            text=default_name
        )
        if not ok or not field_name.strip():
            return
        field_name = field_name.strip()

        sub_fields = section.setdefault("sub_fields", [])
        sub_fields = [sf for sf in sub_fields if sf.get("name") != field_name]
        sub_fields.append({"name": field_name, "pattern": pattern})
        section["sub_fields"] = sub_fields

        cfg["repeating_sections"] = sections
        self._commit_config(cfg, f"Added sub_field {field_name!r} to repeating_sections[{sec_name!r}]")

    def _mark_next_line_value(self):
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        sec_name = self._pick_section_name(
            cfg, prompt="Which section's marker is immediately followed by its (unlabeled) value?"
        )
        if not sec_name:
            return
        sections = cfg.get("repeating_sections") or []
        section = next((s for s in sections if s.get("name") == sec_name), None)
        if not section:
            return

        field_name, ok = QInputDialog.getText(
            self, "Next-Line Field Name",
            "Output column name for the value on the line right after the marker:",
            text=sec_name
        )
        if not ok or not field_name.strip():
            return
        field_name = field_name.strip()

        section["next_line_value"] = {"name": field_name}
        cfg["repeating_sections"] = sections
        self._commit_config(cfg, f"Set repeating_sections[{sec_name!r}].next_line_value = {field_name!r}")

    # ------------------------------------------------------------------
    # Rules Table & Edit Rules Modal Dialog
    # ------------------------------------------------------------------
    def _refresh_fields_table(self):
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        rows = list(describe_field_map(cfg))
        self._rules_model.set_rules(rows)
        self._update_config_summary(cfg, len(rows))

    def _remove_selected_field(self):
        sel = self.table_rules.selectionModel().selectedRows()
        if not sel:
            QMessageBox.information(self, "No Selection", "Select a row in the Rules table first.")
            return
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        row_idx = sel[0].row()
        if row_idx < len(self._rules_model._rows):
            r = self._rules_model._rows[row_idx]
            scope = r.get("scope")
            index = r.get("index")
            if scope:
                remove_field_entry(cfg, scope, index)
                self._commit_config(cfg, "Removed field entry via Rules table.")

    def _open_edit_rules_dialog(self):
        cfg, _ = self._parse_editor_json()
        dialog = EditRulesDialog(self, initial_config=cfg or DEFAULT_CONFIG)
        if dialog.exec() == QDialog.Accepted and dialog.result_config:
            self._commit_config(dialog.result_config, "Rules updated via Edit Rules dialog.")

    # ------------------------------------------------------------------
    # Preview Extraction Execution & Rendering
    # ------------------------------------------------------------------
    def _run_verify_extraction(self):
        if not self._verify_pdf_path or not self._verify_lines:
            QMessageBox.information(self, "No Sample", "Load a sample PDF first using 'Pick PDF' or 'Random Sample'.")
            return

        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            QMessageBox.critical(self, "Invalid Config", f"Fix the JSON config first:\n{err}")
            return

        def _worker():
            try:
                engine = ConfigurableExtractor(cfg)
                full_text, page_offsets = engine.read_pdf(self._verify_pdf_path, max_pages=30)
                records = engine.split_records(full_text, page_offsets)

                all_preview_rows = []
                lines = [
                    f"Sample file  : {os.path.basename(self._verify_pdf_path)} (preview of up to 30 pages)",
                    f"Records found: {len(records)}",
                    ""
                ]
                sep_line = "─" * 50

                if not records:
                    lines.append("⚠️  No records matched configured 'record_separator'.")
                    lines.append("   Check 'record_separator' — it must match text in your PDF.")
                    if full_text.strip():
                        lines.append("")
                        lines.append(sep_line)
                        lines.append("Fallback Preview (Evaluated against full document text):")
                        fb_name, fb_addr, fb_rep, fb_summary, fb_rows = engine.extract_record(full_text.strip())
                        preview = engine.preview_config_on_block(full_text.strip())
                        lines.append(f"  Name          : {preview.get('name', 'N/A')}")
                        lines.append(f"  Data rows     : {preview.get('data_rows_count', 0)}")
                        for sf_name, sf_val in (preview.get('summary_fields') or {}).items():
                            lines.append(f"  {sf_name} : {sf_val}")
                        fb_row_data = fb_rows if fb_rows else ([{}] if fb_summary or fb_name != 'N/A' else [])
                        name_col = (cfg.get("name_detection") or {}).get("output_column", "student_name")
                        for dr in fb_row_data:
                            r = {}
                            if fb_name != 'N/A':
                                r[name_col] = fb_name
                            r.update(fb_summary)
                            r.update(dr)
                            all_preview_rows.append(r)
                else:
                    name_col = (cfg.get("name_detection") or {}).get("output_column", "student_name")
                    for rec_idx, (block, page_num) in enumerate(records, 1):
                        name, address, rep_sections, summary, data_rows = engine.extract_record(block)
                        row_data_list = data_rows if data_rows else ([{}] if summary or name != 'N/A' else [])
                        for dr in row_data_list:
                            row = {}
                            if name != 'N/A':
                                row[name_col] = name
                            row.update(summary)
                            row.update(dr)
                            all_preview_rows.append(row)

                        if rec_idx <= 5:
                            preview = engine.preview_config_on_block(block)
                            lines.append(sep_line)
                            lines.append(f"Record #{rec_idx}  (page {page_num})")
                            lines.append(f"  Name          : {preview['name']}")
                            lines.append(f"  Data rows     : {preview['data_rows_count']}")

                            for sec_name, secs in (preview['repeating_sections'] or {}).items():
                                lines.append(f"  {sec_name} sections : {len(secs)}")

                            for sf_name, sf_val in (preview['summary_fields'] or {}).items():
                                lines.append(f"  {sf_name} : {sf_val}")

                    if len(records) > 5:
                        lines.append(sep_line)
                        lines.append(f"  … and {len(records) - 5} more record(s) not shown in text summary.")

                result_text = "\n".join(lines)
                QTimer.singleShot(0, lambda: self._update_preview_display(result_text, all_preview_rows))
            except Exception as e:
                err_text = f"Preview error: {describe_exception(e)}"
                QTimer.singleShot(0, lambda: self._update_preview_display(err_text, []))

        threading.Thread(target=_worker, daemon=True).start()
        self.lbl_verify_status.setText("Running preview extraction…")

    def _update_preview_display(self, text: str, rows: List[dict]):
        self.txt_verify_results.setPlainText(text)

        if not rows:
            self._preview_model.set_data(["Status"], [["No tabular data extracted. Check config patterns."]])
            self.lbl_verify_status.setText("Preview complete: 0 rows extracted.")
            return

        col_set = []
        for r in rows:
            for k in r.keys():
                if k not in col_set:
                    col_set.append(k)

        headers = [col.replace("_", " ").title() for col in col_set]
        matrix = []
        for r in rows:
            matrix.append([str(r.get(col, "N/A")) for col in col_set])

        self._preview_model.set_data(headers, matrix)
        self.preview_nb.setCurrentIndex(0)
        self.lbl_verify_status.setText(f"Preview complete: {len(rows)} row(s) extracted.")

    # ------------------------------------------------------------------
    # TAB 3 JSON Config & Summary Helpers
    # ------------------------------------------------------------------
    def _load_config_into_editor(self, cfg: dict):
        self.txt_config.setPlainText(json.dumps(cfg, indent=2, ensure_ascii=False))
        self._update_config_summary(cfg)

    def _parse_editor_json(self):
        raw = self.txt_config.toPlainText().strip()
        if not raw:
            return None, "Config editor is empty."
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as e:
            return None, f"JSON error: {e}"

    def _validate_config(self):
        cfg, err = self._parse_editor_json()
        if err:
            QMessageBox.critical(self, "Invalid Config", err)
        else:
            QMessageBox.information(self, "Valid Config", "Config JSON is valid.")

    def _reset_to_blank_template(self):
        self._load_config_into_editor(DEFAULT_CONFIG)
        self._refresh_fields_table()
        if self._verify_lines:
            self._render_verify_lines(self._verify_lines, DEFAULT_CONFIG)

    def _on_apply_raw_json(self):
        cfg, err = self._parse_editor_json()
        if err:
            QMessageBox.critical(self, "Invalid JSON", err)
            return
        self._refresh_fields_table()
        if self._verify_lines:
            self._render_verify_lines(self._verify_lines, cfg)
        self.save_active_config(cfg)
        if hasattr(self, "review_view"):
            self.review_view.refresh_extraction_preview()
            self.review_view._update_stats_label()
        self.lbl_verify_status.setText("Raw JSON rules applied successfully.")

    def _update_config_summary(self, cfg: dict, rules_count: int = 0):
        if not self.lbl_config_summary:
            return
        sep = cfg.get("record_separator") or "(not set)"
        excl = len(cfg.get("exclude_patterns") or [])
        secs = len(cfg.get("repeating_sections") or [])
        dr_pat = (cfg.get("data_rows") or {}).get("pattern", "(not set)")
        dr_short = (dr_pat[:55] + "...") if len(dr_pat) > 55 else dr_pat

        summary = (
            f"📌  Record Separator : {sep}\n"
            f"🧱  Fields & Rules   : {rules_count} rule(s)\n"
            f"🔁  Repeating Blocks : {secs} block(s)\n"
            f"📋  Data-Row Pattern : {dr_short}\n"
            f"🚫  Exclude Patterns : {excl} pattern(s)"
        )
        self.lbl_config_summary.setText(summary)

    # ------------------------------------------------------------------
    # BaseToolWidget Overrides & Execution
    # ------------------------------------------------------------------
    def get_current_inputs(self) -> dict:
        if not self.source_folder or not os.path.isdir(self.source_folder):
            QMessageBox.critical(self, "Missing Folder", "Please select a valid source folder containing PDF files.")
            return {}
        if not self.output_xlsx:
            QMessageBox.critical(self, "Missing Output", "Please choose an output .xlsx file path.")
            return {}
        cfg, err = self._parse_editor_json()
        if err:
            QMessageBox.critical(self, "Invalid Config", err)
            return {}

        return {
            "source_folder": self.source_folder,
            "output_xlsx": self.output_xlsx,
            "config": cfg,
        }

    def describe_task_inputs(self, inputs: dict) -> str:
        src = os.path.basename(inputs.get("source_folder", ""))
        out = os.path.basename(inputs.get("output_xlsx", ""))
        return f"PDF Extractor | Folder: {src} | Output: {out}"

    def execute_task_backend(self, inputs: dict, **kwargs):
        log_cb = kwargs.get("log_callback", print)
        prog_cb = kwargs.get("progress_callback", lambda c, t: None)
        kpi_cb = kwargs.get("kpi_callback", lambda d: None)
        is_aborted = kwargs.get("is_aborted", lambda: False)

        log_cb(f"Starting batch PDF extraction for folder: {inputs['source_folder']}", "INFO")

        total_files = [0]

        def _handle_progress(data: dict):
            t = data.get('type')
            tot = data.get('total')
            if tot and tot > total_files[0]:
                total_files[0] = tot
            effective_total = total_files[0] or 1

            if t == 'total':
                prog_cb(0, effective_total)
                kpi_cb({
                    "type": "init_totals",
                    "total_students": effective_total,
                    "total_files": effective_total,
                })
            elif t == 'item_start':
                idx = data.get('idx', 0)
                prog_cb(idx, effective_total)
                kpi_cb({
                    "type": "student_start",
                    "idx": idx,
                    "student_id": data.get('student_id', '-'),
                    "category": "Extracting",
                })
            elif t == 'item_done':
                completed = data.get('completed', 0)
                prog_cb(completed, effective_total)
                kpi_cb({
                    "type": "file_done",
                    "completed": completed,
                    "students_traversed": completed,
                    "file_name": data.get('student_id', ''),
                })

        self.exporter.run(
            source_folder=inputs["source_folder"],
            output_xlsx=inputs["output_xlsx"],
            config=inputs["config"],
            on_progress=_handle_progress,
            abort_flag=is_aborted,
            log=lambda lvl, msg: log_cb(msg, lvl.upper())
        )

        log_cb("Batch PDF extraction finished successfully!", "SUCCESS")

        # Prompt post-run review
        QTimer.singleShot(800, self._prompt_post_run_review)

    def _prompt_post_run_review(self):
        try:
            if hasattr(self, "review_view"):
                self.review_view.load_random_sample()
            self.notebook.setCurrentIndex(3)
            self.log("info", "[ML Tuning] Switched to 'Review & Self-Tune' tab for random sample verification.")
        except Exception:
            pass
