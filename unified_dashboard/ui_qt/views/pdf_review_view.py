"""
ui_qt/views/pdf_review_view.py
================================
Modern PySide6 Human-in-the-Loop Review & Self-Tune view for PDF Extractor.

Features:
  1. Pick random documents (with dropdown options for random sample, source folder, browse).
  2. Cross-check extracted field values and extracted table rows side-by-side with original text.
  3. Mark fields or table rows as verified (boosting confidence in learning_engine).
  4. Correct mismatched fields (triggering automated regex anchor re-synthesis via tune_field_in_preset).
  5. Point row column values (via tune_row_field_in_preset).
  6. Point sample data row lines (via tune_data_row_line_in_preset).
  7. Add recurring noise lines to exclusion patterns (via add_exclude_rule_to_preset).
  8. Interactive document text viewer with line numbers and green match highlighting.
"""

import os
import re
import random
from typing import Dict, Any, List, Optional

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGroupBox, QComboBox, QSplitter, QPlainTextEdit, QTextEdit,
    QTableView, QAbstractItemView, QHeaderView, QMessageBox, QFileDialog,
    QInputDialog, QMenu, QTabWidget
)
from PySide6.QtGui import (
    QColor, QBrush, QTextCharFormat, QTextCursor, QFont
)

from ui_qt.theme import (
    BG_CARD, BG_CARD_ALT, BG_CONSOLE, BG_INPUT, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER,
    ACCENT_WARNING, ACCENT_DANGER, ACCENT_INFO
)
from modules.pdf_extractor import ConfigurableExtractor, save_preset, save_extractor_state
from modules.pdf_rule_tuner import (
    tune_field_in_preset, tune_row_field_in_preset, tune_data_row_line_in_preset,
    add_exclude_rule_to_preset, record_verification_feedback
)
from core.learning_system import learning_engine


# ---------------------------------------------------------------------------
# Models for Virtualized Table Views
# ---------------------------------------------------------------------------
class FieldsTableModel(QAbstractTableModel):
    """Model for Document & Summary Fields with interactive Checkboxes."""
    HEADERS = ["✔", "Field / Column", "Extracted Value", "Verification Status"]
    verification_toggled = Signal(int, bool)

    def __init__(self):
        super().__init__()
        self._rows: List[tuple] = []  # (field_name, value, status)

    def set_data(self, rows: List[tuple]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if section < len(self.HEADERS):
                return self.HEADERS[section]
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal and section == 0:
            return "Click checkbox to mark field as correct"
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        default_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return default_flags | Qt.ItemFlag.ItemIsUserCheckable
        return default_flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        col = index.column()

        if col == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                status = str(row[2])
                return Qt.CheckState.Checked if "Verified" in status else Qt.CheckState.Unchecked
            elif role == Qt.ItemDataRole.DisplayRole:
                return ""
            elif role == Qt.ItemDataRole.ToolTipRole:
                return "Click checkbox to mark field as verified / correct"
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            return None

        # Data columns:
        # col 1 -> field_name (row[0])
        # col 2 -> value (row[1])
        # col 3 -> status (row[2])
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 1:
                return str(row[0])
            elif col == 2:
                return str(row[1])
            elif col == 3:
                return str(row[2])
        elif role == Qt.ItemDataRole.ForegroundRole:
            status = str(row[2])
            if "Verified" in status:
                return QBrush(QColor(ACCENT_SUCCESS))
            elif "Corrected" in status:
                return QBrush(QColor(ACCENT_WARNING))
            elif col == 1:
                return QBrush(QColor(TEXT_PRIMARY))
            elif col == 2:
                return QBrush(QColor(TEXT_SECONDARY))
            return QBrush(QColor(TEXT_MUTED))
        elif role == Qt.ItemDataRole.TextAlignmentRole and col == 3:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return False
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            is_checked = (value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value or value == 2)
            row = self._rows[index.row()]
            new_status = "✅ Verified" if is_checked else "⚪ Unverified"
            self._rows[index.row()] = (row[0], row[1], new_status)
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), 3))
            self.verification_toggled.emit(index.row(), is_checked)
            return True
        return False

    def update_status(self, row_idx: int, new_status: str):
        if 0 <= row_idx < len(self._rows):
            row = self._rows[row_idx]
            self._rows[row_idx] = (row[0], row[1], new_status)
            self.dataChanged.emit(self.index(row_idx, 0), self.index(row_idx, 3))


class DynamicRowsTableModel(QAbstractTableModel):
    """Model for Extracted Table Rows with dynamic column headers."""

    def __init__(self):
        super().__init__()
        self._headers: List[str] = ["#", "Status"]
        self._rows: List[List[str]] = []

    def set_data(self, headers: List[str], rows: List[List[str]]):
        self.beginResetModel()
        self._headers = headers if headers else ["#", "Status"]
        self._rows = rows
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
# Main Review & Self-Tune Widget
# ---------------------------------------------------------------------------
class PdfReviewView(QWidget):
    """Side-by-side Review & Self-Tuning workspace for PDF extraction verification."""

    def __init__(self, parent=None, main_view=None):
        super().__init__(parent)
        self.main_view = main_view

        self._current_pdf_path: str = ""
        self._current_lines: List[str] = []
        self._extracted_fields: Dict[str, Any] = {}
        self._extracted_rows: List[Dict[str, Any]] = []
        self._row_columns: List[str] = []
        self._selected_line_idx: int = -1
        self._sample_candidates_map: Dict[str, str] = {}

        self._fields_model = FieldsTableModel()
        self._rows_model = DynamicRowsTableModel()

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ── Header & Stats ─────────────────────────────────────────────
        hdr = QHBoxLayout()
        lbl_title = QLabel("🔍  Active Learning & Self-Tuning: Random Document Verification")
        lbl_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        hdr.addWidget(lbl_title)
        hdr.addStretch()

        self.lbl_stats = QLabel("Tuned: 0 | Confidence: --")
        self.lbl_stats.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ACCENT_INFO}; border: none;")
        hdr.addWidget(self.lbl_stats)
        root.addLayout(hdr)

        lbl_desc = QLabel(
            "Pick a sample PDF to verify extracted data. If any field or table row was extracted incorrectly, "
            "select the true text in the document viewer and click 'Point Row Value' or 'Edit / Auto-Tune'."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        root.addWidget(lbl_desc)

        # ── Controls Toolbar ───────────────────────────────────────────
        toolbar = QGroupBox("📂  Sample Document Selection & Quick Actions")
        t_layout = QHBoxLayout(toolbar)
        t_layout.setSpacing(8)

        # Random sample button with popup menu
        self.btn_random = QPushButton("🎲  Pick Random Document")
        self.btn_random.setObjectName("btn_primary")
        self.btn_random.setCursor(Qt.PointingHandCursor)
        self.btn_random.clicked.connect(self.load_random_sample)
        t_layout.addWidget(self.btn_random)

        btn_random_menu = QPushButton("▾")
        btn_random_menu.setObjectName("btn_primary")
        btn_random_menu.setFixedWidth(26)
        btn_random_menu.setCursor(Qt.PointingHandCursor)
        btn_random_menu.clicked.connect(self._show_random_menu)
        t_layout.addWidget(btn_random_menu)

        btn_browse_doc = QPushButton("📂  Pick PDF...")
        btn_browse_doc.setCursor(Qt.PointingHandCursor)
        btn_browse_doc.clicked.connect(self.browse_pdf_sample)
        t_layout.addWidget(btn_browse_doc)

        t_layout.addWidget(QLabel("Sample:"))
        self.combo_sample = QComboBox()
        self.combo_sample.setMinimumWidth(260)
        self.combo_sample.currentIndexChanged.connect(self._on_sample_combobox_selected)
        t_layout.addWidget(self.combo_sample, stretch=1)

        self.lbl_sample_count = QLabel("")
        self.lbl_sample_count.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        t_layout.addWidget(self.lbl_sample_count)

        btn_refresh = QPushButton("🔄  Re-Extract Preview")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh_extraction_preview)
        t_layout.addWidget(btn_refresh)

        root.addWidget(toolbar)

        # ── Current Document Status Strip ──────────────────────────────
        doc_bar = QFrame()
        doc_bar.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px;")
        d_layout = QHBoxLayout(doc_bar)
        d_layout.setContentsMargins(12, 6, 12, 6)

        lbl_doc_prefix = QLabel("Active Document:")
        lbl_doc_prefix.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {TEXT_MUTED}; border: none;")
        d_layout.addWidget(lbl_doc_prefix)

        self.lbl_current_doc = QLabel("(No sample loaded - choose a document or click 'Pick Random Document')")
        self.lbl_current_doc.setStyleSheet(f"font-size: 11px; color: {TEXT_PRIMARY}; border: none;")
        d_layout.addWidget(self.lbl_current_doc, stretch=1)
        root.addWidget(doc_bar)

        # ── Main Splitter: Document Text (Left) | Extracted Data (Right) ─
        splitter = QSplitter(Qt.Horizontal)

        # Left: Original Document Text
        left_box = QGroupBox("📄  Original Document Text (select text to point / auto-tune)")
        l_layout = QVBoxLayout(left_box)
        l_layout.setContentsMargins(8, 8, 8, 8)
        l_layout.setSpacing(6)

        self.txt_doc = QPlainTextEdit()
        self.txt_doc.setReadOnly(True)
        self.txt_doc.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_doc.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {BG_CONSOLE};
                color: #D4D4D4;
                font-family: Cascadia Code, Consolas, Courier New, monospace;
                font-size: 12px;
                border: 1px solid {BORDER_MUTED};
                border-radius: 6px;
                padding: 10px;
                line-height: 1.6;
            }}
        """)
        self.txt_doc.cursorPositionChanged.connect(self._on_doc_cursor_changed)
        l_layout.addWidget(self.txt_doc)
        splitter.addWidget(left_box)

        # Right: Extracted Data & Active Self-Tuning
        right_box = QGroupBox("📊  Extracted Data & Active Self-Tuning")
        r_layout = QVBoxLayout(right_box)
        r_layout.setContentsMargins(8, 8, 8, 8)
        r_layout.setSpacing(8)

        # Tabs for Fields vs Table Rows
        self.review_nb = QTabWidget()

        # Tab A: Document & Summary Fields
        tab_fields_widget = QWidget()
        tf_layout = QVBoxLayout(tab_fields_widget)
        tf_layout.setContentsMargins(0, 4, 0, 0)
        self.table_fields = QTableView()
        self.table_fields.setModel(self._fields_model)
        self.table_fields.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_fields.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_fields.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_fields.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_fields.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_fields.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_fields.setAlternatingRowColors(False)
        self.table_fields.selectionModel().selectionChanged.connect(self._on_field_selected)
        self._fields_model.verification_toggled.connect(self._on_field_verification_toggled)
        tf_layout.addWidget(self.table_fields)
        self.review_nb.addTab(tab_fields_widget, " 📋 Document & Summary Fields ")

        # Tab B: Extracted Table Rows
        tab_rows_widget = QWidget()
        tr_layout = QVBoxLayout(tab_rows_widget)
        tr_layout.setContentsMargins(0, 4, 0, 0)
        self.table_rows = QTableView()
        self.table_rows.setModel(self._rows_model)
        self.table_rows.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_rows.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_rows.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_rows.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_rows.setAlternatingRowColors(False)
        self.table_rows.selectionModel().selectionChanged.connect(self._on_row_selected)
        tr_layout.addWidget(self.table_rows)
        self.review_nb.addTab(tab_rows_widget, " 📊 Extracted Table Rows ")

        r_layout.addWidget(self.review_nb, stretch=1)

        # Action Buttons Toolbar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)

        btn_correct = QPushButton("✔  Mark Correct")
        btn_correct.setObjectName("btn_success")
        btn_correct.setCursor(Qt.PointingHandCursor)
        btn_correct.setToolTip("Mark selected field or row as verified without pop-up")
        btn_correct.clicked.connect(self._on_mark_correct)
        action_bar.addWidget(btn_correct)

        btn_mark_all = QPushButton("✔✔  Mark All Correct")
        btn_mark_all.setCursor(Qt.PointingHandCursor)
        btn_mark_all.setStyleSheet("background-color: #065F46; color: white; font-weight: 700; border-radius: 6px; padding: 6px 12px;")
        btn_mark_all.setToolTip("Verify all extracted fields at once")
        btn_mark_all.clicked.connect(self._on_mark_all_correct)
        action_bar.addWidget(btn_mark_all)

        btn_edit = QPushButton("✏  Edit / Auto-Tune")
        btn_edit.setObjectName("btn_primary")
        btn_edit.setCursor(Qt.PointingHandCursor)
        btn_edit.clicked.connect(self._on_edit_and_tune)
        action_bar.addWidget(btn_edit)

        btn_point_row = QPushButton("🎯  Point Row Value")
        btn_point_row.setCursor(Qt.PointingHandCursor)
        btn_point_row.setStyleSheet(f"background-color: #8B5CF6; color: white; font-weight: 700; border-radius: 6px; padding: 6px 12px;")
        btn_point_row.clicked.connect(self._on_point_row_value)
        action_bar.addWidget(btn_point_row)

        btn_point_line = QPushButton("📍  Point Row Line")
        btn_point_line.setCursor(Qt.PointingHandCursor)
        btn_point_line.clicked.connect(self._on_point_row_line)
        action_bar.addWidget(btn_point_line)

        action_bar.addStretch()

        btn_add_field = QPushButton("➕  Add Field")
        btn_add_field.setCursor(Qt.PointingHandCursor)
        btn_add_field.setStyleSheet("background-color: #1D4ED8; color: white; font-weight: 700; border-radius: 6px; padding: 6px 12px;")
        btn_add_field.setToolTip("Add a new extraction field to the preset (optionally pre-filled from selected text)")
        btn_add_field.clicked.connect(self._on_add_field)
        action_bar.addWidget(btn_add_field)

        btn_remove_field = QPushButton("🗑  Remove Field")
        btn_remove_field.setCursor(Qt.PointingHandCursor)
        btn_remove_field.setStyleSheet("background-color: #7F1D1D; color: white; font-weight: 700; border-radius: 6px; padding: 6px 12px;")
        btn_remove_field.setToolTip("Remove the selected field from the preset configuration")
        btn_remove_field.clicked.connect(self._on_remove_field)
        action_bar.addWidget(btn_remove_field)

        btn_exclude = QPushButton("🚫  Exclude Line")
        btn_exclude.setObjectName("btn_danger")
        btn_exclude.setCursor(Qt.PointingHandCursor)
        btn_exclude.clicked.connect(self._on_add_exclude)
        action_bar.addWidget(btn_exclude)

        r_layout.addLayout(action_bar)

        self.lbl_review_feedback = QLabel("")
        self.lbl_review_feedback.setStyleSheet(f"color: {ACCENT_SUCCESS}; font-size: 12px; font-weight: 600;")
        r_layout.addWidget(self.lbl_review_feedback)
        splitter.addWidget(right_box)

        splitter.setSizes([560, 520])
        root.addWidget(splitter, stretch=1)

        self._refresh_sample_dropdown()

    # ----------------------------------------------------------------------
    # Candidate Document Discovery & Dropdown
    # ----------------------------------------------------------------------
    def get_sample_candidates(self) -> List[str]:
        candidates: List[str] = []
        seen = set()

        def _add_file(p: str):
            if not p:
                return
            try:
                norm = os.path.normpath(os.path.abspath(p))
                if norm not in seen and os.path.isfile(norm) and norm.lower().endswith(".pdf"):
                    seen.add(norm)
                    candidates.append(norm)
            except Exception:
                pass

        if self._current_pdf_path:
            _add_file(self._current_pdf_path)

        if self.main_view:
            v_pdf = getattr(self.main_view, "_verify_pdf_path", "")
            if v_pdf:
                _add_file(v_pdf)

            src = getattr(self.main_view, "source_folder", "")
            if src and os.path.isdir(src):
                try:
                    for f in sorted(os.listdir(src)):
                        if f.lower().endswith(".pdf"):
                            _add_file(os.path.join(src, f))
                except Exception:
                    pass

        # Fallback search in project root (shallow)
        proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if not candidates and os.path.isdir(proj_root):
            for f in sorted(os.listdir(proj_root)):
                if f.lower().endswith(".pdf"):
                    _add_file(os.path.join(proj_root, f))

        return candidates

    def _refresh_sample_dropdown(self):
        candidates = self.get_sample_candidates()
        new_map: Dict[str, str] = {}
        bname_counts: Dict[str, int] = {}

        for p in candidates:
            bname = os.path.basename(p)
            bname_counts[bname] = bname_counts.get(bname, 0) + 1

        for p in candidates:
            bname = os.path.basename(p)
            if bname_counts[bname] > 1:
                parent = os.path.basename(os.path.dirname(p))
                display_name = f"{bname} ({parent})"
            else:
                display_name = bname
            new_map[display_name] = p

        self._sample_candidates_map = new_map
        sorted_names = sorted(new_map.keys(), key=lambda s: s.lower())

        self.combo_sample.blockSignals(True)
        self.combo_sample.clear()
        self.combo_sample.addItems(sorted_names)

        if sorted_names:
            self.lbl_sample_count.setText(f"({len(sorted_names)} available)")
        else:
            self.lbl_sample_count.setText("(No PDFs)")

        if self._current_pdf_path and os.path.isfile(self._current_pdf_path):
            current_norm = os.path.normpath(os.path.abspath(self._current_pdf_path))
            for dname, fpath in new_map.items():
                if os.path.normpath(os.path.abspath(fpath)) == current_norm:
                    self.combo_sample.setCurrentText(dname)
                    break
        self.combo_sample.blockSignals(False)

    def _show_random_menu(self):
        menu = QMenu(self)
        act_rnd = menu.addAction("🎲 Pick Random Document")
        act_rnd.triggered.connect(self.load_random_sample)

        act_browse = menu.addAction("📂 Browse Any PDF File...")
        act_browse.triggered.connect(self.browse_pdf_sample)

        menu.addSeparator()
        self._refresh_sample_dropdown()
        items = list(self._sample_candidates_map.keys())
        if items:
            hdr_act = menu.addAction(f"Available Documents ({len(items)}):")
            hdr_act.setEnabled(False)
            for name in items[:15]:
                path = self._sample_candidates_map[name]
                act = menu.addAction(f"   {name}")
                act.triggered.connect(lambda checked, p=path: self.load_pdf_for_review(p))
        else:
            no_act = menu.addAction("(No PDF files found - click Browse)")
            no_act.setEnabled(False)

        menu.exec(self.btn_random.mapToGlobal(self.btn_random.rect().bottomLeft()))

    def _on_sample_combobox_selected(self):
        if getattr(self, "_loading_pdf", False):
            return
        selected_display = self.combo_sample.currentText()
        if selected_display in self._sample_candidates_map:
            path = self._sample_candidates_map[selected_display]
            if self._current_pdf_path and os.path.normpath(os.path.abspath(path)) == os.path.normpath(os.path.abspath(self._current_pdf_path)):
                return
            self.load_pdf_for_review(path)

    def browse_pdf_sample(self):
        init_dir = getattr(self.main_view, "source_folder", "") or "."
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF Document to Review & Self-Tune",
            dir=init_dir, filter="PDF Files (*.pdf);;All Files (*.*)"
        )
        if path and os.path.isfile(path):
            self.load_pdf_for_review(path)

    def load_random_sample(self):
        candidates = self.get_sample_candidates()
        if not candidates:
            QMessageBox.information(
                self, "No PDFs Found",
                "No PDF files available to review. Choose a Source Folder on Tab 1 or click '📂 Pick PDF...' to browse."
            )
            return
        chosen = random.choice(candidates)
        self.load_pdf_for_review(chosen)

    # ----------------------------------------------------------------------
    # Document Loading & Extraction
    # ----------------------------------------------------------------------
    def load_pdf_for_review(self, pdf_path: str):
        if not os.path.isfile(pdf_path):
            return

        if getattr(self, "_loading_pdf", False):
            return
        self._loading_pdf = True

        try:
            self._current_pdf_path = os.path.normpath(os.path.abspath(pdf_path))
            filename = os.path.basename(pdf_path)
            self.lbl_current_doc.setText(f"Active: {filename}")

            self._refresh_sample_dropdown()
            current_norm = self._current_pdf_path
            matched = False

            self.combo_sample.blockSignals(True)
            for dname, fpath in self._sample_candidates_map.items():
                if os.path.normpath(os.path.abspath(fpath)) == current_norm:
                    self.combo_sample.setCurrentText(dname)
                    matched = True
                    break
            if not matched:
                self.combo_sample.setCurrentText(filename)
            self.combo_sample.blockSignals(False)

            cfg = self._get_current_config()
            extractor = ConfigurableExtractor(cfg or {})

            try:
                self._current_lines = extractor.read_pdf_lines(pdf_path, max_pages=30)
            except Exception as e:
                self._current_lines = []
                QMessageBox.critical(self, "Read Error", f"Could not read PDF lines: {e}")
                return

            self._render_doc_text()
            self.refresh_extraction_preview()
            self._update_stats_label()
        finally:
            self._loading_pdf = False

    def _render_doc_text(self):
        MAX_DISPLAY = 2000
        display_lines = self._current_lines[:MAX_DISPLAY]
        text_lines = []
        for i, line in enumerate(display_lines):
            text_lines.append(f"{i+1:3d} | {line}")
        if len(self._current_lines) > MAX_DISPLAY:
            text_lines.append(f"    | ... [Performance note: showing first {MAX_DISPLAY:,} of {len(self._current_lines):,} lines] ...")
        self.txt_doc.setPlainText("\n".join(text_lines))

    def refresh_extraction_preview(self):
        if not self._current_pdf_path or not os.path.isfile(self._current_pdf_path):
            return

        cfg = self._get_current_config()
        self._extracted_fields.clear()
        all_rows = []
        row_cols = []

        # ── Student Schedule: delegate to dedicated extractor ───────────────
        if cfg.get("report_type") == "student_schedule":
            try:
                from modules.pdf_extractor import StudentScheduleExtractor
                sse = StudentScheduleExtractor(cfg)
                full_text, offsets = sse.read_pdf(self._current_pdf_path, max_pages=30)
                records = sse.split_records(full_text, offsets)
                if records:
                    all_rows = sse.extract_record(
                        records[0][0],
                        source_file=self._current_pdf_path,
                        page_number=records[0][1]
                    )
                elif full_text.strip():
                    all_rows = sse.extract_record(
                        full_text.strip(),
                        source_file=self._current_pdf_path,
                        page_number=1
                    )
            except Exception:
                all_rows = []

            # Student-level summary fields come from the first row's context keys
            SCHEDULE_SUMMARY_KEYS = (
                "id", "student_id", "student_name", "program", "program_description",
                "drop_date", "group", "first_term", "exp_grad_date", "ft_pt", "status",
                "grad_date", "session", "term"
            )
            first_row = all_rows[0] if all_rows else {}
            for key in SCHEDULE_SUMMARY_KEYS:
                val = first_row.get(key)
                if val is not None:
                    self._extracted_fields[key] = val

            # Also expose any extra summary fields from the preset that were populated
            for sf in (cfg.get("summary_fields") or []):
                key = sf.get("name")
                if key and key not in self._extracted_fields:
                    val = first_row.get(key)
                    if val is not None:
                        self._extracted_fields[key] = val

            # Table columns: preserve all columns per output_columns or row keys
            out_cols = cfg.get("output_columns") or []
            skip_extra = {"source_file", "page_number", "student_id", "_partial"}
            if out_cols and all_rows:
                row_cols = [c for c in out_cols if c in all_rows[0]]
                for c in all_rows[0].keys():
                    if c not in row_cols and c not in skip_extra:
                        row_cols.append(c)
            elif all_rows:
                row_cols = [c for c in all_rows[0].keys() if c not in skip_extra]
            elif cfg.get("data_rows", {}).get("columns"):
                row_cols = list(cfg["data_rows"]["columns"])

        # ── All other report types: use ConfigurableExtractor ───────────────
        else:
            preview = {}
            try:
                extractor = ConfigurableExtractor(cfg or {})
                full_text, offsets = extractor.read_pdf(self._current_pdf_path, max_pages=30)
                records = extractor.split_records(full_text, offsets)
                if records:
                    first_block = records[0][0]
                    preview = extractor.preview_config_on_block(first_block)
                    try:
                        _, _, _, _, all_rows = extractor.extract_record(first_block)
                    except Exception:
                        all_rows = preview.get("data_rows_sample", [])
                elif full_text.strip():
                    first_block = full_text.strip()
                    preview = extractor.preview_config_on_block(first_block)
                    try:
                        _, _, _, _, all_rows = extractor.extract_record(first_block)
                    except Exception:
                        all_rows = preview.get("data_rows_sample", [])
            except Exception:
                preview = {}
                all_rows = []

            if preview.get("name") and preview["name"] != "N/A":
                self._extracted_fields["student_name"] = preview["name"]
            for k, v in preview.get("summary_fields", {}).items():
                self._extracted_fields[k] = v

            row_cols = []
            if all_rows:
                row_cols = [col for col in all_rows[0].keys() if col not in ("source_file", "page_number")]
            elif cfg and cfg.get("data_rows", {}).get("columns"):
                row_cols = list(cfg["data_rows"]["columns"])

        self._extracted_rows = all_rows or []
        self._row_columns = row_cols

        # Add course-level column sample values (prefixed with row_)
        data_cols = cfg.get("data_rows", {}).get("columns") or []
        if data_cols:
            first_row = all_rows[0] if all_rows else {}
            for col in data_cols:
                val = first_row.get(col, "(no rows matched)")
                self._extracted_fields[f"row_{col}"] = str(val)

        # Repopulate Fields Model — display all summary & student-level fields
        priority_fields = [
            "id", "student_name", "program_description", "program", "group", "status",
            "first_term", "exp_grad_date", "ft_pt", "session", "drop_date", "grad_date", "term"
        ]
        cfg_summary_names = [sf.get("name") for sf in (cfg.get("summary_fields") or [])]
        display_keys = []
        for fn in cfg_summary_names + priority_fields:
            if fn and fn not in display_keys:
                display_keys.append(fn)

        field_tuples = []
        seen_fields = set()
        for field_name in display_keys:
            val = self._extracted_fields.get(field_name)
            if val is not None and val != "":
                display_val = str(val)
            elif field_name in cfg_summary_names or field_name in ("id", "student_name", "program_description", "group", "status"):
                display_val = "(not matched)"
            else:
                continue
            field_tuples.append((field_name, display_val, "⚪ Unverified"))
            seen_fields.add(field_name)

        # Append any remaining extracted fields (e.g. row_*)
        skip_summary = {"student_id", "_partial", "source_file", "page_number"}
        for field_name, value in self._extracted_fields.items():
            if field_name not in seen_fields and field_name not in skip_summary:
                display_val = str(value) if value is not None else "(empty)"
                field_tuples.append((field_name, display_val, "⚪ Unverified"))
                seen_fields.add(field_name)
        self._fields_model.set_data(field_tuples)

        # Repopulate Rows Model
        row_cols = self._row_columns
        if row_cols and all_rows:
            headers = ["#"] + [c.replace("_", " ").title() for c in row_cols]
            rows_matrix = []
            for i, r in enumerate(all_rows):
                row_vals = [str(i + 1)] + [str(r.get(c, "")) for c in row_cols]
                rows_matrix.append(row_vals)
            self._rows_model.set_data(headers, rows_matrix)
        else:
            self._rows_model.set_data(
                ["Table Status"],
                [["(No table rows extracted yet — select a line on left and click '📍 Point Row Line')"]]
            )

        self._highlight_matches()

    def _highlight_matches(self):
        """Highlights matching extracted values in green in the document viewer."""
        extra_selections = []
        doc = self.txt_doc.document()

        match_format = QTextCharFormat()
        match_format.setForeground(QColor("#4ADE80"))
        match_format.setFontWeight(QFont.Bold)

        for field_name, val in self._extracted_fields.items():
            s_val = str(val).strip()
            if len(s_val) < 2 or s_val in ("N/A", "(empty)", "(no rows matched)"):
                continue

            cursor = QTextCursor(doc)
            while True:
                cursor = doc.find(s_val, cursor)
                if cursor.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                sel.format = match_format
                extra_selections.append(sel)

        self.txt_doc.setExtraSelections(extra_selections)

    def _on_doc_cursor_changed(self):
        cursor = self.txt_doc.textCursor()
        self._selected_line_idx = cursor.blockNumber()

    def _on_field_selected(self, selected, deselected):
        indexes = self.table_fields.selectionModel().selectedRows()
        if not indexes:
            return
        row = indexes[0].row()
        val = self._fields_model._rows[row][1]
        if val and val not in ("N/A", "(empty)", "(no rows matched)"):
            self._scroll_to_text(val)

    def _on_row_selected(self, selected, deselected):
        indexes = self.table_rows.selectionModel().selectedRows()
        if not indexes:
            return
        r = indexes[0].row()
        if r < len(self._rows_model._rows):
            row_vals = self._rows_model._rows[r]
            for v in row_vals[1:]:
                s_v = str(v).strip()
                if len(s_v) >= 3 and s_v not in ("N/A", "(empty)", "(no rows matched)"):
                    if self._scroll_to_text(s_v):
                        break

    def _scroll_to_text(self, text_to_find: str) -> bool:
        doc = self.txt_doc.document()
        cursor = doc.find(text_to_find)
        if not cursor.isNull():
            self.txt_doc.setTextCursor(cursor)
            self.txt_doc.centerCursor()
            return True
        return False

    def _get_current_config(self) -> dict:
        if self.main_view and hasattr(self.main_view, "_parse_editor_json"):
            cfg, _ = self.main_view._parse_editor_json()
            if cfg:
                return cfg
        return {}

    def _get_preset_name(self) -> str:
        if self.main_view and hasattr(self.main_view, "preset_var"):
            pv = self.main_view.preset_var
            return pv.currentText() if pv else ""
        return ""

    def _get_learning_profile_key(self) -> str:
        name = self._get_preset_name()
        if not name or name == "(unsaved / custom)":
            return "default"
        return name

    # ----------------------------------------------------------------------
    # Active Learning / Self-Tuning Actions
    # ----------------------------------------------------------------------
    def _on_field_verification_toggled(self, row_idx: int, is_verified: bool):
        """Called immediately when user toggles the checkbox beside any field."""
        profile_key = self._get_learning_profile_key()
        if 0 <= row_idx < len(self._fields_model._rows):
            field_name, value, _ = self._fields_model._rows[row_idx]
            if is_verified:
                try:
                    record_verification_feedback(profile_key, field_name, is_correct=True, old_value=value, new_value=value)
                except Exception:
                    pass
                self.lbl_review_feedback.setText(f"✔ Field '{field_name}' verified! Rule confidence boosted.")
            else:
                self.lbl_review_feedback.setText(f"Field '{field_name}' marked unverified.")
            self._update_stats_label()

    def _on_mark_correct(self):
        """Marks currently selected field or row as verified without blocking pop-ups."""
        active_tab = self.review_nb.currentIndex()
        profile_key = self._get_learning_profile_key()

        if active_tab == 1:
            # Extracted rows tab
            sel = self.table_rows.selectionModel().selectedRows()
            if not sel:
                self.lbl_review_feedback.setText("⚠ Please select a row in the Extracted Data Rows table.")
                return
            row_num = sel[0].row() + 1
            for col in (self._row_columns or []):
                try:
                    record_verification_feedback(profile_key, f"row_{col}", is_correct=True)
                except Exception:
                    pass
            self._update_stats_label()
            self.lbl_review_feedback.setText(f"✔ Row #{row_num} marked as verified! Confidence boosted.")
            return

        sel = self.table_fields.selectionModel().selectedRows()
        if not sel:
            self.lbl_review_feedback.setText("⚠ Please select a field row or click its checkbox beside it.")
            return

        r = sel[0].row()
        field_name, value, _ = self._fields_model._rows[r]
        try:
            record_verification_feedback(profile_key, field_name, is_correct=True, old_value=value, new_value=value)
        except Exception:
            pass

        self._fields_model.update_status(r, "✅ Verified")
        self._update_stats_label()
        self.lbl_review_feedback.setText(f"✔ Field '{field_name}' marked as verified! Rule confidence increased.")

    def _on_mark_all_correct(self):
        """Marks all currently displayed fields as verified in one click without pop-ups."""
        active_tab = self.review_nb.currentIndex()
        profile_key = self._get_learning_profile_key()

        if active_tab == 1:
            row_count = len(self._rows_model._rows)
            for r_idx in range(row_count):
                for col in (self._row_columns or []):
                    try:
                        record_verification_feedback(profile_key, f"row_{col}", is_correct=True)
                    except Exception:
                        pass
            self._update_stats_label()
            self.lbl_review_feedback.setText(f"✔ All {row_count} table rows verified! Confidence boosted.")
            return

        field_count = len(self._fields_model._rows)
        for r in range(field_count):
            field_name, value, _ = self._fields_model._rows[r]
            try:
                record_verification_feedback(profile_key, field_name, is_correct=True, old_value=value, new_value=value)
            except Exception:
                pass
            self._fields_model.update_status(r, "✅ Verified")
        self._update_stats_label()
        self.lbl_review_feedback.setText(f"✔ All {field_count} fields marked as verified! Rule confidence boosted.")

    def _on_point_row_value(self):
        cursor = self.txt_doc.textCursor()
        highlighted_text = cursor.selectedText().strip()

        cfg = self._get_current_config()
        row_cols = self._row_columns or cfg.get("data_rows", {}).get("columns") or []

        col_name = ""
        sel_f = self.table_fields.selectionModel().selectedRows()
        if sel_f:
            f_name = self._fields_model._rows[sel_f[0].row()][0]
            if f_name.startswith("row_") or f_name in row_cols:
                col_name = f_name[4:] if f_name.startswith("row_") else f_name

        if not col_name and row_cols:
            chosen, ok = QInputDialog.getItem(
                self, "Select Row Column", "Which row column are you pointing?",
                row_cols, 0, False
            )
            if not ok or not chosen:
                return
            col_name = chosen.strip()

        if not col_name:
            chosen, ok = QInputDialog.getText(
                self, "Row Column Name", "Enter the row column name to point (e.g. description, score):"
            )
            if not ok or not chosen.strip():
                return
            col_name = chosen.strip()

        col_name = col_name[4:] if col_name.startswith("row_") else col_name

        corrected, ok = QInputDialog.getText(
            self, "Point Row Field Value",
            f"Enter or confirm the true value for row column '{col_name}':\n(Hint: You can select text in the document viewer first)",
            text=highlighted_text
        )
        if not ok or not corrected.strip():
            return
        corrected_val = corrected.strip()

        target_line = ""
        for line in self._current_lines:
            if corrected_val in line:
                target_line = line
                break
        if not target_line and self._current_lines:
            target_line = self._current_lines[0]

        updated_cfg, msg = tune_row_field_in_preset(
            cfg, col_name, corrected_val, target_line, all_lines=self._current_lines
        )

        self._apply_config_update(updated_cfg)

        profile_key = self._get_learning_profile_key()
        try:
            record_verification_feedback(
                profile_key,
                f"row_{col_name}",
                is_correct=False,
                old_value="",
                new_value=corrected_val
            )
        except Exception:
            pass

        self.refresh_extraction_preview()
        self._update_stats_label()
        QMessageBox.information(self, "Row Pattern Auto-Tuned", f"Successfully auto-tuned row pattern for '{col_name}'!\n\n{msg}")

    def _on_point_row_line(self):
        cursor = self.txt_doc.textCursor()
        highlighted_line = cursor.selectedText().strip()

        if not highlighted_line and 0 <= self._selected_line_idx < len(self._current_lines):
            highlighted_line = self._current_lines[self._selected_line_idx].strip()

        pointed_line, ok = QInputDialog.getText(
            self, "Point Data Row Line",
            "Enter or confirm the sample data row line text from the document:\n(Hint: You can select a row line on the left first)",
            text=highlighted_line
        )
        if not ok or not pointed_line.strip():
            return

        cfg = self._get_current_config()
        updated_cfg, msg = tune_data_row_line_in_preset(cfg, pointed_line.strip())

        self._apply_config_update(updated_cfg)
        self.refresh_extraction_preview()
        self._update_stats_label()
        QMessageBox.information(self, "Data Row Synthesized", f"Successfully synthesized data_rows from sample line!\n\n{msg}")

    def _on_edit_and_tune(self):
        active_tab = self.review_nb.currentIndex()
        if active_tab == 1:
            self._on_point_row_value()
            return

        sel = self.table_fields.selectionModel().selectedRows()
        if not sel:
            QMessageBox.information(self, "Select Field", "Please select a field in the table to edit.")
            return

        r = sel[0].row()
        field_name, current_val, _ = self._fields_model._rows[r]

        if field_name.startswith("row_") or field_name in (self._row_columns or []):
            self._on_point_row_value()
            return

        cursor = self.txt_doc.textCursor()
        highlighted_text = cursor.selectedText().strip()
        initial_val = highlighted_text if highlighted_text else current_val

        corrected, ok = QInputDialog.getText(
            self, "Correct Field Value",
            f"Enter the true value for '{field_name}':\n(Hint: You can select text on the left first)",
            text=initial_val
        )
        # Only cancel if user hit Cancel — allow blank (clear field) or unchanged value.
        if not ok:
            return

        corrected_val = corrected.strip()

        # If user left the field blank, clear the pattern so extraction returns ''
        if corrected_val == "":
            cfg = self._get_current_config()
            updated_cfg, msg = tune_field_in_preset(
                cfg, field_name, "", "", all_lines=self._current_lines
            )
            self._apply_config_update(updated_cfg)
            # Immediately update the local model row to show (empty)
            r_to_update = sel[0].row()
            field_row = self._fields_model._rows[r_to_update]
            self._fields_model._rows[r_to_update] = (field_row[0], "(empty)", "⚪ Unverified")
            self._fields_model.dataChanged.emit(
                self._fields_model.index(r_to_update, 0),
                self._fields_model.index(r_to_update, 3)
            )
            self.refresh_extraction_preview()
            self._update_stats_label()
            self.lbl_review_feedback.setText(f"🧹 Field '{field_name}' cleared — will extract as empty.")
            return

        target_line = ""
        for line in self._current_lines:
            if corrected_val in line:
                target_line = line
                break
        if not target_line:
            clean_target = field_name.replace("_", " ").lower()
            for line in self._current_lines:
                if clean_target in line.lower():
                    target_line = line
                    break
        if not target_line and self._current_lines:
            target_line = self._current_lines[0]

        cfg = self._get_current_config()
        updated_cfg, msg = tune_field_in_preset(
            cfg, field_name, corrected_val, target_line, all_lines=self._current_lines
        )

        self._apply_config_update(updated_cfg)

        profile_key = self._get_learning_profile_key()
        try:
            record_verification_feedback(
                profile_key,
                field_name,
                is_correct=False,
                old_value=current_val,
                new_value=corrected_val
            )
        except Exception:
            pass

        self.refresh_extraction_preview()
        self._update_stats_label()
        self.lbl_review_feedback.setText(f"✏ Field '{field_name}' auto-tuned successfully. {msg}")

    def _on_add_field(self):
        """Add a brand-new extraction field to the preset."""
        cursor = self.txt_doc.textCursor()
        highlighted_text = cursor.selectedText().strip()

        # Ask for the field name
        field_name, ok = QInputDialog.getText(
            self, "Add New Field",
            "Enter the field name (snake_case, e.g. invoice_date, tax_id):"
        )
        if not ok or not field_name.strip():
            return
        field_name = field_name.strip().lower().replace(" ", "_")

        cfg = self._get_current_config()

        # Check if field already exists
        existing_names = [sf.get("name") for sf in cfg.get("summary_fields", [])]
        if field_name in existing_names:
            self.lbl_review_feedback.setText(f"⚠ Field '{field_name}' already exists. Use Edit / Auto-Tune to update it.")
            return

        if highlighted_text:
            # User has text highlighted — auto-tune a pattern from it
            target_line = ""
            for line in self._current_lines:
                if highlighted_text in line:
                    target_line = line
                    break
            if not target_line and self._current_lines:
                target_line = self._current_lines[0]
            updated_cfg, msg = tune_field_in_preset(
                cfg, field_name, highlighted_text, target_line, all_lines=self._current_lines
            )
            self._apply_config_update(updated_cfg)
            self.refresh_extraction_preview()
            self._update_stats_label()
            self.lbl_review_feedback.setText(f"➕ Field '{field_name}' added and pattern auto-tuned from selection.")
        else:
            # No selection — add an empty sentinel so the field appears in the table
            cfg.setdefault("summary_fields", []).append(
                {"name": field_name, "pattern": r"(?!)"}
            )
            if field_name not in cfg.get("output_columns", []):
                cfg.setdefault("output_columns", []).append(field_name)
            self._apply_config_update(cfg)
            self.refresh_extraction_preview()
            self._update_stats_label()
            self.lbl_review_feedback.setText(
                f"➕ Field '{field_name}' added (empty). Select its value in the document then use Edit / Auto-Tune."
            )

    def _on_remove_field(self):
        """Remove the currently selected field from the preset configuration."""
        active_tab = self.review_nb.currentIndex()
        if active_tab != 0:
            self.lbl_review_feedback.setText("⚠ Switch to the Summary Fields tab to remove a field.")
            return

        sel = self.table_fields.selectionModel().selectedRows()
        if not sel:
            self.lbl_review_feedback.setText("⚠ Please select a field in the table first.")
            return

        r = sel[0].row()
        field_name = self._fields_model._rows[r][0]

        # Confirm before deleting (non-blocking — simple Yes/No)
        reply = QMessageBox.question(
            self, "Remove Field",
            f"Remove field '{field_name}' from this preset?\nThis cannot be undone from this view.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        cfg = self._get_current_config()

        # Remove from summary_fields
        s_fields = cfg.get("summary_fields", []) or []
        idx_to_remove = next(
            (i for i, sf in enumerate(s_fields) if sf.get("name") == field_name), None
        )
        if idx_to_remove is not None:
            from modules.pdf_extractor import remove_field_entry
            remove_field_entry(cfg, "summary_fields", idx_to_remove)
        # Also remove from output_columns
        out_cols = cfg.get("output_columns", []) or []
        if field_name in out_cols:
            out_cols.remove(field_name)
            cfg["output_columns"] = out_cols
        # Remove from computed_fields references
        computed = cfg.get("computed_fields", []) or []
        cfg["computed_fields"] = [cf for cf in computed if field_name not in cf.get("join", []) and cf.get("name") != field_name]

        self._apply_config_update(cfg)
        self.refresh_extraction_preview()
        self._update_stats_label()
        self.lbl_review_feedback.setText(f"🗑 Field '{field_name}' removed from preset.")

    def _on_add_exclude(self):
        cursor = self.txt_doc.textCursor()
        selected_text = cursor.selectedText().strip()

        if not selected_text:
            text, ok = QInputDialog.getText(
                self, "Exclude Noise Pattern",
                "Enter text or regex pattern to exclude from all pages (e.g. footers, page numbers):"
            )
            if not ok or not text.strip():
                return
            selected_text = text.strip()

        cfg = self._get_current_config()
        updated_cfg, msg = add_exclude_rule_to_preset(cfg, selected_text)

        self._apply_config_update(updated_cfg)
        self.refresh_extraction_preview()
        self.lbl_review_feedback.setText(f"🚫 {msg}")

    def _apply_config_update(self, updated_cfg: dict):
        if self.main_view:
            if hasattr(self.main_view, "_load_config_into_editor"):
                self.main_view._load_config_into_editor(updated_cfg)
            if hasattr(self.main_view, "_refresh_fields_table"):
                self.main_view._refresh_fields_table()
            if hasattr(self.main_view, "save_active_config"):
                self.main_view.save_active_config(updated_cfg)

        preset_name = self._get_preset_name()
        if preset_name and preset_name != "(unsaved / custom)":
            try:
                save_preset(preset_name, updated_cfg)
            except Exception:
                pass

        try:
            save_extractor_state({
                "last_preset": preset_name or "(unsaved / custom)",
                "active_config": updated_cfg
            })
        except Exception:
            pass

    def _update_stats_label(self):
        profile_key = self._get_learning_profile_key()
        profile = learning_engine._data.get("pdf_extractor_profiles", {}).get(profile_key, {})
        runs = profile.get("verified_runs", 0)
        conf_map = profile.get("field_confidence", {})
        avg_conf = f"{sum(conf_map.values()) / len(conf_map) * 100:.0f}%" if conf_map else "--"
        disp_name = profile_key if profile_key != "default" else "Active Config"
        self.lbl_stats.setText(f"Preset: {disp_name} | Tuned Runs: {runs} | Confidence: {avg_conf}")
