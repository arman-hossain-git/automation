"""
ui_qt/views/data_harmonizer_view.py
===================================
Adaptive Dataset Harmonizer View for the PySide6 Unified Dashboard.

Organized into a clean, intuitive multi-step tabbed workflow:
  1. Datasets & Sources: Upload raw datasets and gold standard sample schemas.
  2. Schema Mapping & Rules: Interactive schema alignment, matching matrix, and rules.
  3. Preview & Export: Real-time harmonized output preview and export to CSV/Excel/JSON/DB.
  4. Adaptive Intelligence: Inspection and management of persistent learned patterns.
"""

import os
import re
from typing import List, Dict, Any, Optional
import pandas as pd

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QFileDialog, QMessageBox, QTabWidget, QCheckBox, QProgressBar,
    QSplitter, QScrollArea, QSizePolicy, QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QAbstractItemView
)
from PySide6.QtGui import QFont, QColor

from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BG_INPUT, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER, ACCENT_SUCCESS, ACCENT_WARNING,
    ACCENT_DANGER, ACCENT_INFO, ACCENT_PURPLE
)
from ui_qt.components.badge import StatusBadge
from core.learning_system import learning_engine
from core.data_harmonizer.profiler import DataProfiler, DatasetProfile, ColumnProfile
from core.data_harmonizer.combiner import MultiSourceCombiner
from core.data_harmonizer.matcher import SchemaMatcher, ColumnMappingMatch
from core.data_harmonizer.transformer import DataTransformer
from core.data_harmonizer.adaptive_memory import harmonizer_memory
from core.ollama_engine import ollama_engine


class SheetSelectionDialog(QDialog):
    """Dialog allowing user to choose which sheets/tabs from a multi-tab file to import."""

    def __init__(self, filename: str, sheet_names: List[str], single_select: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Select {'Tab' if single_select else 'Tabs'}: {os.path.basename(filename)}")
        self.resize(420, 340)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_CARD}; color: {TEXT_PRIMARY}; }}
            QLabel {{ color: {TEXT_PRIMARY}; background: transparent; }}
            QListWidget {{
                background-color: {BG_INPUT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 4px;
            }}
            QListWidget::item {{ padding: 6px; }}
            QListWidget::item:selected {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
                border-radius: 4px;
            }}
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        prompt = (
            f"The file <b>{os.path.basename(filename)}</b> contains multiple tabs ({len(sheet_names)} found).<br>"
            f"Select which {'tab represents the target sample schema:' if single_select else 'tabs to load as source tables:'}"
        )
        lbl = QLabel(prompt)
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.list_widget = QListWidget()
        for s in sheet_names:
            item = QListWidgetItem(s)
            if not single_select:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)

        if single_select and self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        layout.addWidget(self.list_widget)

        if not single_select:
            sel_bar = QHBoxLayout()
            sel_bar.setSpacing(8)
            btn_select_all = QPushButton("Select All")
            btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
            btn_deselect_all = QPushButton("Deselect All")
            btn_deselect_all.clicked.connect(lambda: self._set_all_checked(False))
            sel_bar.addWidget(btn_select_all)
            sel_bar.addWidget(btn_deselect_all)
            sel_bar.addStretch()
            layout.addLayout(sel_bar)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def get_selected_sheets(self) -> List[str]:
        selected = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.text())
        return selected

    def get_single_sheet(self) -> Optional[str]:
        curr = self.list_widget.currentItem()
        return curr.text() if curr else None


class DataHarmonizerView(QWidget):
    """PySide6 modern tabbed view for multi-source data reshaping, normalization, and adaptive harmonization."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.raw_profiles: List[DatasetProfile] = []
        self.sample_profile: Optional[DatasetProfile] = None
        self.combined_raw_df: Optional[pd.DataFrame] = None
        self.combined_profile: Optional[DatasetProfile] = None
        self.current_matches: List[ColumnMappingMatch] = []
        self.transformed_df: Optional[pd.DataFrame] = None

        self._build_ui()
        self._refresh_learning_stats()

    # ------------------------------------------------------------------
    # UI Layout Construction (Tabbed Architecture)
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ── 1. Header Banner ──────────────────────────────────────────
        hdr = QFrame()
        hdr.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px; padding: 6px 14px;"
        )
        h_layout = QHBoxLayout(hdr)
        h_layout.setContentsMargins(12, 6, 12, 6)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_title = QLabel("✨ Adaptive Dataset Harmonizer")
        lbl_title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        lbl_sub = QLabel("Multi-source schema matching, relational combining, and self-learning format normalization")
        lbl_sub.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        h_layout.addLayout(title_box)

        h_layout.addStretch()

        self.badge_learning = StatusBadge("🧠 Adaptive Engine Ready", "primary")
        h_layout.addWidget(self.badge_learning)

        self.btn_clear_learning = QPushButton("Reset Harmonizer Memory")
        self.btn_clear_learning.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_DANGER};
                color: #FFFFFF;
            }}
        """)
        self.btn_clear_learning.clicked.connect(self._clear_harmonizer_memory)
        h_layout.addWidget(self.btn_clear_learning)

        root.addWidget(hdr)

        # ── 2. Main Tabbed Container ───────────────────────────────────
        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {BORDER_SUBTLE};
                background-color: {BG_BASE};
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                padding: 9px 18px;
                margin-right: 4px;
                border: 1px solid {BORDER_SUBTLE};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }}
            QTabBar::tab:selected {{
                background-color: {BG_BASE};
                color: {TEXT_PRIMARY};
                border-bottom: 3px solid {ACCENT_PRIMARY};
                font-weight: 700;
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
            }}
        """)

        # Tab 1: Datasets & Sources Setup
        self.tab_sources = self._build_sources_tab()
        self.main_tabs.addTab(self.tab_sources, "📁  1. Datasets & Sources")

        # Tab 2: Schema Mapping Matrix & Rules
        self.tab_mapping = self._build_mapping_tab()
        self.main_tabs.addTab(self.tab_mapping, "🔗  2. Schema Mapping & Rules")

        # Tab 3: Harmonized Preview & Export
        self.tab_export = self._build_export_tab()
        self.main_tabs.addTab(self.tab_export, "🚀  3. Preview & Export")

        # Tab 4: Adaptive Learning Knowledge
        self.tab_memory = self._build_memory_tab()
        self.main_tabs.addTab(self.tab_memory, "🧠  4. Adaptive Intelligence")

        root.addWidget(self.main_tabs, stretch=1)

    # ------------------------------------------------------------------
    # TAB 1: Datasets & Sources Setup
    # ------------------------------------------------------------------

    def _build_sources_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        # 2-Column Split: Raw Sources (Left) vs Sample Target (Right)
        cols_layout = QHBoxLayout()
        cols_layout.setSpacing(14)

        # ── Card Left: Raw Source Datasets ──
        raw_card = QFrame()
        raw_card.setStyleSheet(f"""
            QFrame#rawCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 8px;
            }}
        """)
        raw_card.setObjectName("rawCard")
        r_layout = QVBoxLayout(raw_card)
        r_layout.setContentsMargins(14, 12, 14, 12)
        r_layout.setSpacing(10)

        r_title = QLabel("1. Raw Source Dataset(s) or Database")
        r_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        r_layout.addWidget(r_title)

        r_sub = QLabel("Select raw files to reshape into the target standard.")
        r_sub.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        r_layout.addWidget(r_sub)

        # Action Buttons
        r_btn_row = QHBoxLayout()
        self.btn_add_raw = QPushButton("+ Add Raw Files (CSV, XLSX, JSON, DB)")
        self.btn_add_raw.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_PRIMARY_HOVER};
            }}
        """)
        self.btn_add_raw.clicked.connect(self._browse_raw_files)
        r_btn_row.addWidget(self.btn_add_raw)

        self.btn_clear_raw = QPushButton("Clear")
        self.btn_clear_raw.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT};
                color: {TEXT_MUTED};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 7px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        self.btn_clear_raw.clicked.connect(self._clear_raw_files)
        r_btn_row.addWidget(self.btn_clear_raw)
        r_btn_row.addStretch()
        r_layout.addLayout(r_btn_row)

        self.lbl_raw_status = QLabel("No raw datasets loaded.")
        self.lbl_raw_status.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        r_layout.addWidget(self.lbl_raw_status)

        # Summary Table for Loaded Raw Sources
        self.tbl_raw_files_summary = QTableWidget(0, 4)
        self.tbl_raw_files_summary.setHorizontalHeaderLabels(["Source Table / File", "Format", "Rows", "Cols"])
        self.tbl_raw_files_summary.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_raw_files_summary.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_raw_files_summary.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_raw_files_summary.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_raw_files_summary.verticalHeader().setVisible(False)
        self.tbl_raw_files_summary.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_raw_files_summary.setStyleSheet(self._table_stylesheet())
        r_layout.addWidget(self.tbl_raw_files_summary, stretch=1)

        # Multi-Source Combine Box
        combine_card = QFrame()
        combine_card.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px; padding: 6px;")
        comb_layout = QVBoxLayout(combine_card)
        comb_layout.setContentsMargins(8, 6, 8, 6)
        comb_layout.setSpacing(4)

        comb_row = QHBoxLayout()
        lbl_mode = QLabel("Multi-Source Combine:")
        lbl_mode.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_PRIMARY}; border: none;")
        comb_row.addWidget(lbl_mode)

        self.combo_combine_mode = QComboBox()
        self.combo_combine_mode.addItems(["Auto-Detect (Smart)", "Smart Union (Stack Rows)", "Smart Join (Relational Merge)"])
        self.combo_combine_mode.setStyleSheet(f"""
            QComboBox {{
                background-color: {BG_INPUT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)
        comb_row.addWidget(self.combo_combine_mode, stretch=1)
        comb_layout.addLayout(comb_row)

        lbl_comb_hint = QLabel("Auto-Detect selects Union for matching headers, or Join on common ID keys.")
        lbl_comb_hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; border: none;")
        comb_layout.addWidget(lbl_comb_hint)

        r_layout.addWidget(combine_card)
        cols_layout.addWidget(raw_card, stretch=1)

        # ── Card Right: Sample Target Dataset ──
        sample_card = QFrame()
        sample_card.setStyleSheet(f"""
            QFrame#sampleCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 8px;
            }}
        """)
        sample_card.setObjectName("sampleCard")
        s_layout = QVBoxLayout(sample_card)
        s_layout.setContentsMargins(14, 12, 14, 12)
        s_layout.setSpacing(10)

        s_title = QLabel("2. Sample Target Dataset (Gold Standard Schema)")
        s_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        s_layout.addWidget(s_title)

        s_sub = QLabel("Defines the expected column names, types, order, and format.")
        s_sub.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        s_layout.addWidget(s_sub)

        # Action Buttons
        s_btn_row = QHBoxLayout()
        self.btn_upload_sample = QPushButton("📂 Upload Sample Dataset")
        self.btn_upload_sample.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_PRIMARY};
                background-color: {BG_INPUT};
            }}
        """)
        self.btn_upload_sample.clicked.connect(self._browse_sample_file)
        s_btn_row.addWidget(self.btn_upload_sample)

        self.btn_clear_sample = QPushButton("Clear")
        self.btn_clear_sample.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT};
                color: {TEXT_MUTED};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 7px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        self.btn_clear_sample.clicked.connect(self._clear_sample_file)
        s_btn_row.addWidget(self.btn_clear_sample)
        s_btn_row.addStretch()
        s_layout.addLayout(s_btn_row)

        self.lbl_sample_status = QLabel("No sample dataset loaded.")
        self.lbl_sample_status.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        s_layout.addWidget(self.lbl_sample_status)

        # Summary Table for Target Columns
        self.tbl_sample_columns_summary = QTableWidget(0, 3)
        self.tbl_sample_columns_summary.setHorizontalHeaderLabels(["Target Column Name", "Inferred Type", "Format / Key"])
        self.tbl_sample_columns_summary.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_sample_columns_summary.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_sample_columns_summary.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_sample_columns_summary.verticalHeader().setVisible(False)
        self.tbl_sample_columns_summary.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_sample_columns_summary.setStyleSheet(self._table_stylesheet())
        s_layout.addWidget(self.tbl_sample_columns_summary, stretch=1)

        cols_layout.addWidget(sample_card, stretch=1)
        layout.addLayout(cols_layout, stretch=1)

        # ── Bottom CTA Action Banner ──
        banner = QFrame()
        banner.setStyleSheet(f"""
            background-color: {BG_CARD};
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 8px;
            padding: 10px 14px;
        """)
        b_layout = QHBoxLayout(banner)
        b_layout.setContentsMargins(12, 6, 12, 6)

        msg_box = QVBoxLayout()
        msg_box.setSpacing(2)
        lbl_step_note = QLabel("Step 1 Complete: When ready, auto-align schemas and proceed to Mapping Matrix")
        lbl_step_note.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY}; border: none;")
        lbl_step_desc = QLabel("Schema matching utilizes learned precedents, type semantics, composite detection, and AI heuristics.")
        lbl_step_desc.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        msg_box.addWidget(lbl_step_note)
        msg_box.addWidget(lbl_step_desc)
        b_layout.addLayout(msg_box)

        b_layout.addStretch()

        self.btn_analyze = QPushButton("⚡ Align Schemas & Auto-Match Mappings ➜")
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_PURPLE};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:disabled {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_MUTED};
            }}
            QPushButton:hover:!disabled {{
                background-color: #7C3AED;
            }}
        """)
        self.btn_analyze.clicked.connect(self._align_and_match)
        b_layout.addWidget(self.btn_analyze)

        layout.addWidget(banner)
        return tab

    # ------------------------------------------------------------------
    # TAB 2: Schema Mapping Matrix & Rules
    # ------------------------------------------------------------------

    def _build_mapping_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # ── Top Toolbar: Status, Filters & Actions ──
        tb = QFrame()
        tb.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 6px 12px;")
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(10, 6, 10, 6)
        tb_layout.setSpacing(12)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        lbl_map_title = QLabel("Schema Harmonization & Transformation Matrix")
        lbl_map_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        self.lbl_match_summary = QLabel("Upload both raw & sample datasets to preview mappings.")
        self.lbl_match_summary.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        info_box.addWidget(lbl_map_title)
        info_box.addWidget(self.lbl_match_summary)
        tb_layout.addLayout(info_box)

        tb_layout.addStretch()

        # Filter Mappings Dropdown
        lbl_filter = QLabel("Filter:")
        lbl_filter.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")
        tb_layout.addWidget(lbl_filter)

        self.combo_mapping_filter = QComboBox()
        self.combo_mapping_filter.addItems(["All Columns", "Needs Review / Low Match", "Unmapped", "High Match"])
        self.combo_mapping_filter.setStyleSheet(f"""
            QComboBox {{
                background-color: {BG_INPUT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)
        self.combo_mapping_filter.currentTextChanged.connect(self._on_filter_mappings)
        tb_layout.addWidget(self.combo_mapping_filter)

        self.btn_reapply_all = QPushButton("🔄 Recompute Previews")
        self.btn_reapply_all.setVisible(False)
        self.btn_reapply_all.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        self.btn_reapply_all.clicked.connect(self._update_all_previews)
        tb_layout.addWidget(self.btn_reapply_all)

        self.btn_goto_export = QPushButton("Proceed to Preview & Export ➜")
        self.btn_goto_export.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_PRIMARY_HOVER};
            }}
        """)
        self.btn_goto_export.clicked.connect(lambda: self.main_tabs.setCurrentIndex(2))
        tb_layout.addWidget(self.btn_goto_export)

        layout.addWidget(tb)

        # ── Full Height Mapping Matrix Table ──
        self.tbl_mappings = QTableWidget(0, 6)
        self.tbl_mappings.setHorizontalHeaderLabels([
            "Target Column (Sample)", "Target Type", "Source Column",
            "Confidence", "Transformation Rule", "Sample Output Preview"
        ])
        self.tbl_mappings.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_mappings.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_mappings.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_mappings.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_mappings.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tbl_mappings.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.tbl_mappings.verticalHeader().setVisible(False)
        self.tbl_mappings.setStyleSheet(self._table_stylesheet())
        layout.addWidget(self.tbl_mappings, stretch=1)

        # Helpful Bottom Tip
        tip_frame = QFrame()
        tip_frame.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px; padding: 6px 12px;")
        tip_layout = QHBoxLayout(tip_frame)
        tip_layout.setContentsMargins(8, 4, 8, 4)
        lbl_tip = QLabel("💡 Tip: Customize any source mapping or conversion rule above. Any modifications automatically update the live preview on Tab 3.")
        lbl_tip.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; border: none;")
        tip_layout.addWidget(lbl_tip)
        layout.addWidget(tip_frame)

        return tab

    # ------------------------------------------------------------------
    # TAB 3: Harmonized Preview & Export
    # ------------------------------------------------------------------

    def _build_export_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # ── Sub-tabs for Previews ──
        self.tabs_preview = QTabWidget()
        self.tabs_preview.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {BORDER_SUBTLE};
                background-color: {BG_BASE};
                border-radius: 6px;
            }}
            QTabBar::tab {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                padding: 6px 14px;
                border: 1px solid {BORDER_SUBTLE};
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-weight: 600;
                font-size: 11px;
            }}
            QTabBar::tab:selected {{
                background-color: {BG_BASE};
                color: {TEXT_PRIMARY};
                border-bottom: 2px solid {ACCENT_PRIMARY};
            }}
        """)

        # Sub-tab 1: Harmonized Target Preview
        self.tbl_out_preview = QTableWidget(0, 0)
        self.tbl_out_preview.setStyleSheet(self._table_stylesheet())
        self.tabs_preview.addTab(self.tbl_out_preview, "✨ Harmonized Output Preview (Sample Order & Format)")

        # Sub-tab 2: Combined Raw Input Preview
        self.tbl_raw_preview = QTableWidget(0, 0)
        self.tbl_raw_preview.setStyleSheet(self._table_stylesheet())
        self.tabs_preview.addTab(self.tbl_raw_preview, "📋 Raw / Combined Input Preview")

        layout.addWidget(self.tabs_preview, stretch=1)

        # ── Export & Adaptive Reinforcement Configuration Card ──
        export_card = QFrame()
        export_card.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px; padding: 8px 14px;"
        )
        exp_layout = QHBoxLayout(export_card)
        exp_layout.setContentsMargins(14, 8, 14, 8)
        exp_layout.setSpacing(16)

        chk_col = QVBoxLayout()
        chk_col.setSpacing(4)
        self.chk_learn = QCheckBox("Save & reinforce learned column associations for future runs")
        self.chk_learn.setChecked(True)
        self.chk_learn.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        chk_col.addWidget(self.chk_learn)

        self.chk_exclude_blank = QCheckBox("Exclude blank / unmapped fields from export")
        self.chk_exclude_blank.setChecked(False)
        self.chk_exclude_blank.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        self.chk_exclude_blank.toggled.connect(self._update_all_previews)
        chk_col.addWidget(self.chk_exclude_blank)

        exp_layout.addLayout(chk_col)
        exp_layout.addStretch()

        # Back to mapping button
        btn_back_to_map = QPushButton("⬅ Adjust Mappings")
        btn_back_to_map.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        btn_back_to_map.clicked.connect(lambda: self.main_tabs.setCurrentIndex(1))
        exp_layout.addWidget(btn_back_to_map)

        lbl_export_fmt = QLabel("Export Format:")
        lbl_export_fmt.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        exp_layout.addWidget(lbl_export_fmt)

        self.combo_export_fmt = QComboBox()
        self.combo_export_fmt.addItems(["CSV (.csv)", "Excel (.xlsx)", "JSON (.json)", "SQLite (.db)"])
        self.combo_export_fmt.setStyleSheet(f"""
            QComboBox {{
                background-color: {BG_INPUT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 5px;
                padding: 6px 12px;
                font-size: 12px;
            }}
        """)
        exp_layout.addWidget(self.combo_export_fmt)

        self.btn_export = QPushButton("🚀 Transform & Export Harmonized Data")
        self.btn_export.setEnabled(False)
        self.btn_export.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_SUCCESS};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 9px 20px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:disabled {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_MUTED};
            }}
            QPushButton:hover:!disabled {{
                background-color: #059669;
            }}
        """)
        self.btn_export.clicked.connect(self._export_harmonized_data)
        exp_layout.addWidget(self.btn_export)

        layout.addWidget(export_card)
        return tab

    # ------------------------------------------------------------------
    # TAB 4: Adaptive Learning Knowledge
    # ------------------------------------------------------------------

    def _build_memory_tab(self) -> QWidget:
        self.tab_insights = QWidget()
        layout = QVBoxLayout(self.tab_insights)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # ── KPI Stats Cards Row ──
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(14)

        self.lbl_stat_mappings = QLabel("0")
        self.lbl_stat_fingerprints = QLabel("0")
        self.lbl_stat_rules = QLabel("Active")

        card_map = self._create_kpi_card("Learned Column Associations", self.lbl_stat_mappings, ACCENT_PURPLE)
        card_fp = self._create_kpi_card("Recognized Dataset Fingerprints", self.lbl_stat_fingerprints, ACCENT_PRIMARY)
        card_status = self._create_kpi_card("Learning Engine Status", self.lbl_stat_rules, ACCENT_SUCCESS)

        kpi_row.addWidget(card_map)
        kpi_row.addWidget(card_fp)
        kpi_row.addWidget(card_status)
        layout.addLayout(kpi_row)

        # ── Learned Column Associations Memory Table ──
        lbl_tbl_title = QLabel("Permanent Column Mappings & Conversion Precedents")
        lbl_tbl_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY};")
        layout.addWidget(lbl_tbl_title)

        self.tbl_learned_memory = QTableWidget(0, 4)
        self.tbl_learned_memory.setHorizontalHeaderLabels([
            "Raw Source Column Pattern", "Target Sample Column", "Saved Transformation Rule", "Confirmations"
        ])
        self.tbl_learned_memory.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_learned_memory.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_learned_memory.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_learned_memory.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_learned_memory.verticalHeader().setVisible(False)
        self.tbl_learned_memory.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_learned_memory.setStyleSheet(self._table_stylesheet())
        layout.addWidget(self.tbl_learned_memory, stretch=1)

        # Explanatory card
        desc_card = QFrame()
        desc_card.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 12px;")
        d_layout = QVBoxLayout(desc_card)
        self.lbl_insights = QLabel()
        self.lbl_insights.setWordWrap(True)
        d_layout.addWidget(self.lbl_insights)
        layout.addWidget(desc_card)

        return self.tab_insights

    def _create_kpi_card(self, title: str, value_lbl: QLabel, accent_color: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"""
            background-color: {BG_CARD};
            border: 1px solid {BORDER_SUBTLE};
            border-left: 4px solid {accent_color};
            border-radius: 8px;
            padding: 10px 14px;
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(10, 8, 10, 8)
        c_layout.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")
        value_lbl.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")

        c_layout.addWidget(lbl_t)
        c_layout.addWidget(value_lbl)
        return card

    def _table_stylesheet(self) -> str:
        return f"""
            QTableWidget {{
                background-color: {BG_BASE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                gridline-color: {BORDER_MUTED};
            }}
            QHeaderView::section {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER_SUBTLE};
                padding: 6px;
                font-weight: 600;
                font-size: 11px;
            }}
        """

    # ------------------------------------------------------------------
    # Data Loading & Management
    # ------------------------------------------------------------------

    def _browse_raw_files(self):
        """Allows selecting one or multiple raw dataset files, supporting multi-tab Excel files."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Raw Datasets or Database",
            "",
            "All Supported (*.csv *.xlsx *.xls *.json *.db *.sqlite *.sqlite3 *.sql);;CSV (*.csv);;Excel (*.xlsx *.xls);;JSON (*.json);;SQLite (*.db *.sqlite);;SQL Dump (*.sql)"
        )
        if not files:
            return

        loaded_profiles = []
        for f in files:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                sheets = DataProfiler.list_sheets_or_tables(f)
            finally:
                QApplication.restoreOverrideCursor()

            if len(sheets) > 1:
                dlg = SheetSelectionDialog(f, sheets, single_select=False, parent=self)
                if dlg.exec() == QDialog.Accepted:
                    chosen = dlg.get_selected_sheets()
                    if chosen:
                        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                        try:
                            loaded_profiles.extend(DataProfiler.profile_selected_sources(f, chosen))
                        except Exception as e:
                            QMessageBox.warning(self, "Load Error", f"Could not load selected tables:\n{e}")
                        finally:
                            QApplication.restoreOverrideCursor()
            else:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    prof = DataProfiler.profile_dataset(f)
                    loaded_profiles.append(prof)
                except Exception as e:
                    QMessageBox.warning(self, "Load Error", f"Could not load {os.path.basename(f)}:\n{e}")
                finally:
                    QApplication.restoreOverrideCursor()

        if loaded_profiles:
            self.raw_profiles.extend(loaded_profiles)
            self._update_raw_status()
            self._check_ready()

    def _clear_raw_files(self):
        self.raw_profiles.clear()
        self.combined_raw_df = None
        self.combined_profile = None
        self._update_raw_status()
        self._check_ready()

    def _update_raw_status(self):
        self.tbl_raw_files_summary.setRowCount(0)
        if not self.raw_profiles:
            self.lbl_raw_status.setText("No raw datasets loaded.")
            return

        total_rows = sum(p.row_count for p in self.raw_profiles)
        file_names = ", ".join(p.source_name for p in self.raw_profiles[:3])
        if len(self.raw_profiles) > 3:
            file_names += f" (+{len(self.raw_profiles)-3} more)"

        self.lbl_raw_status.setText(
            f"✅ {len(self.raw_profiles)} dataset table(s) loaded ({file_names}) — Total {total_rows} rows"
        )

        # Populate summary table
        self.tbl_raw_files_summary.setRowCount(len(self.raw_profiles))
        for r, prof in enumerate(self.raw_profiles):
            it_name = QTableWidgetItem(prof.source_name)
            it_name.setForeground(QColor(TEXT_PRIMARY))
            self.tbl_raw_files_summary.setItem(r, 0, it_name)

            ext = os.path.splitext(prof.source_name)[1].lstrip(".").upper()
            if not ext:
                ext = "TABULAR"
            it_fmt = QTableWidgetItem(ext)
            it_fmt.setForeground(QColor(TEXT_MUTED))
            self.tbl_raw_files_summary.setItem(r, 1, it_fmt)

            it_rows = QTableWidgetItem(str(prof.row_count))
            it_rows.setForeground(QColor(TEXT_PRIMARY))
            self.tbl_raw_files_summary.setItem(r, 2, it_rows)

            it_cols = QTableWidgetItem(str(prof.column_count))
            it_cols.setForeground(QColor(TEXT_PRIMARY))
            self.tbl_raw_files_summary.setItem(r, 3, it_cols)

    def _browse_sample_file(self):
        """Allows selecting a target sample dataset, with tab selection for multi-sheet workbooks."""
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Select Sample Target Dataset",
            "",
            "All Supported (*.csv *.xlsx *.xls *.json *.db *.sqlite *.sqlite3 *.sql);;CSV (*.csv);;Excel (*.xlsx *.xls);;JSON (*.json);;SQLite (*.db *.sqlite *.sqlite3);;SQL Dump (*.sql)"
        )
        if not f:
            return

        chosen_sheet = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            sheets = DataProfiler.list_sheets_or_tables(f)
        finally:
            QApplication.restoreOverrideCursor()
        if len(sheets) > 1:
            dlg = SheetSelectionDialog(f, sheets, single_select=True, parent=self)
            if dlg.exec() == QDialog.Accepted:
                chosen_sheet = dlg.get_single_sheet()
            else:
                return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.sample_profile = DataProfiler.profile_dataset(f, table_name=chosen_sheet)
            self.lbl_sample_status.setText(
                f"✅ Sample: {self.sample_profile.source_name} ({self.sample_profile.column_count} cols, {self.sample_profile.row_count} sample rows)"
            )
            # Populate sample columns summary table
            self._update_sample_summary_table()
            self._check_ready()
        except Exception as e:
            QMessageBox.warning(self, "Sample Error", f"Could not profile sample dataset:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _update_sample_summary_table(self):
        self.tbl_sample_columns_summary.setRowCount(0)
        if not self.sample_profile:
            return

        cols = list(self.sample_profile.columns.items())
        self.tbl_sample_columns_summary.setRowCount(len(cols))
        for r, (col_name, cp) in enumerate(cols):
            it_name = QTableWidgetItem(col_name)
            it_name.setFont(QFont("Segoe UI", 9, QFont.Bold))
            it_name.setForeground(QColor(TEXT_PRIMARY))
            self.tbl_sample_columns_summary.setItem(r, 0, it_name)

            it_type = QTableWidgetItem(cp.inferred_type)
            it_type.setForeground(QColor(ACCENT_PRIMARY))
            self.tbl_sample_columns_summary.setItem(r, 1, it_type)

            desc = cp.detected_format or ("Candidate Key" if cp.is_candidate_key else "-")
            it_fmt = QTableWidgetItem(desc)
            it_fmt.setForeground(QColor(TEXT_MUTED))
            self.tbl_sample_columns_summary.setItem(r, 2, it_fmt)

    def _clear_sample_file(self):
        self.sample_profile = None
        self.lbl_sample_status.setText("No sample dataset loaded.")
        self.tbl_sample_columns_summary.setRowCount(0)
        self._check_ready()

    def _check_ready(self):
        ready = bool(self.raw_profiles and self.sample_profile)
        self.btn_analyze.setEnabled(ready)

    # ------------------------------------------------------------------
    # Align & Schema Matching
    # ------------------------------------------------------------------

    def _align_and_match(self):
        """Combines raw inputs and matches them against the sample dataset."""
        if not self.raw_profiles or not self.sample_profile:
            return

        try:
            mode_choice = self.combo_combine_mode.currentText()

            # Step 1: Combine multi-source raw datasets if needed
            if len(self.raw_profiles) == 1:
                self.combined_raw_df = self.raw_profiles[0].raw_df.copy()
                self.combined_profile = self.raw_profiles[0]
            elif "Union" in mode_choice:
                df, det = MultiSourceCombiner.smart_union(self.raw_profiles)
                self.combined_raw_df = df
                self.combined_profile = DataProfiler.profile_dataset(df)
            elif "Join" in mode_choice:
                df, det = MultiSourceCombiner.smart_join(self.raw_profiles)
                self.combined_raw_df = df
                self.combined_profile = DataProfiler.profile_dataset(df)
            else:  # Auto-Detect
                df, mode, det = MultiSourceCombiner.auto_combine(self.raw_profiles)
                self.combined_raw_df = df
                self.combined_profile = DataProfiler.profile_dataset(df)

            # Step 2: Run SchemaMatcher with persistent learning engine
            matcher = SchemaMatcher(learning_engine)
            self.current_matches = matcher.match_schemas(self.combined_profile, self.sample_profile)

            # Step 3: Populate interactive mapping table
            self._render_mapping_table()

            # Step 4: Populate live previews
            self._render_raw_preview()
            self._update_all_previews()

            self.btn_reapply_all.setVisible(True)
            self.btn_export.setEnabled(True)

            high_conf = sum(1 for m in self.current_matches if m.confidence >= 0.85)
            self.lbl_match_summary.setText(
                f"Matched {len(self.current_matches)} target columns ({high_conf} high confidence). You can adjust any mapping below."
            )

            # Smoothly switch to Tab 2 (Schema Mapping Matrix)
            self.main_tabs.setCurrentIndex(1)

        except Exception as e:
            QMessageBox.critical(self, "Harmonization Error", f"Failed to match schemas:\n{e}")

    # ------------------------------------------------------------------
    # Interactive Mapping Grid Rendering & Interaction
    # ------------------------------------------------------------------

    def _render_mapping_table(self):
        """Fills the interactive mapping table with editable dropdowns for each column."""
        self.tbl_mappings.setRowCount(0)
        if not self.current_matches or not self.combined_profile:
            return

        all_source_cols = ["(Unmapped / Blank)"] + list(self.combined_profile.columns.keys())
        self.tbl_mappings.setRowCount(len(self.current_matches))

        for row, match in enumerate(self.current_matches):
            # Target Column
            item_target = QTableWidgetItem(match.sample_column)
            item_target.setFont(QFont("Segoe UI", 10, QFont.Bold))
            item_target.setForeground(QColor(TEXT_PRIMARY))
            self.tbl_mappings.setItem(row, 0, item_target)

            # Target Type
            item_type = QTableWidgetItem(match.sample_type)
            item_type.setForeground(QColor(TEXT_MUTED))
            self.tbl_mappings.setItem(row, 1, item_type)

            # Source Column Dropdown
            combo_source = QComboBox()
            combo_source.setStyleSheet(f"""
                QComboBox {{
                    background-color: {BG_INPUT};
                    color: {TEXT_PRIMARY};
                    border: 1px solid {BORDER_SUBTLE};
                    border-radius: 4px;
                    padding: 3px 6px;
                }}
            """)
            combo_source.addItems(all_source_cols)
            # Add composite option if applicable
            if match.transformation_rule == "concat" and match.source_column:
                if match.source_column not in all_source_cols:
                    combo_source.addItem(f"Composite: {match.source_column}")

            # Select current match
            curr_src = match.source_column
            if curr_src and match.transformation_rule == "concat" and f"Composite: {curr_src}" in [combo_source.itemText(i) for i in range(combo_source.count())]:
                combo_source.setCurrentText(f"Composite: {curr_src}")
            elif curr_src in all_source_cols:
                combo_source.setCurrentText(curr_src)
            else:
                combo_source.setCurrentIndex(0)

            combo_source.currentIndexChanged.connect(lambda idx, r=row: self._on_source_column_changed(r))
            self.tbl_mappings.setCellWidget(row, 2, combo_source)

            # Confidence Badge
            self._update_confidence_cell(row, match.confidence, match.tier_reason)

            # Transformation Rule Dropdown
            combo_rule = QComboBox()
            combo_rule.setStyleSheet(combo_source.styleSheet())
            combo_rule.addItems(["direct", "date_reformat", "clean_currency", "numeric_cast", "case_match", "concat", "unmapped"])
            combo_rule.setCurrentText(match.transformation_rule)
            combo_rule.currentTextChanged.connect(lambda txt, r=row: self._on_rule_changed(r, txt))
            self.tbl_mappings.setCellWidget(row, 4, combo_rule)

            # Sample Output Preview
            item_prev = QTableWidgetItem(match.sample_transformed_preview)
            item_prev.setForeground(QColor(ACCENT_SUCCESS))
            self.tbl_mappings.setItem(row, 5, item_prev)

        # Apply active filter
        self._on_filter_mappings(self.combo_mapping_filter.currentText())

    def _on_filter_mappings(self, filter_text: str):
        """Hides or reveals rows based on the selected confidence/mapping category."""
        if not self.current_matches:
            return

        for row, match in enumerate(self.current_matches):
            show = True
            if filter_text == "Needs Review / Low Match":
                show = (0.0 < match.confidence < 0.85)
            elif filter_text == "Unmapped":
                show = (match.confidence == 0.0 or not match.source_column or match.transformation_rule == "unmapped")
            elif filter_text == "High Match":
                show = (match.confidence >= 0.85)
            self.tbl_mappings.setRowHidden(row, not show)

    def _update_confidence_cell(self, row: int, confidence: float, reason: str):
        badge_frame = QFrame()
        b_layout = QHBoxLayout(badge_frame)
        b_layout.setContentsMargins(4, 2, 4, 2)

        reason_lower = (reason or "").lower()
        if "golden memory" in reason_lower or "learned memory" in reason_lower:
            badge = StatusBadge("✓ Learned Precedent", "success")
        elif "adaptive ai" in reason_lower or "ollama" in reason_lower:
            badge = StatusBadge("✨ Smart AI Match", "success")
        elif confidence >= 0.85:
            badge = StatusBadge("✓ High Match", "success")
        elif confidence >= 0.45:
            badge = StatusBadge("⚡ Needs Review", "warning")
        elif confidence > 0.0:
            badge = StatusBadge("⚠ Low Match", "danger")
        else:
            badge = StatusBadge("Unmapped", "neutral")

        b_layout.addWidget(badge)
        self.tbl_mappings.setCellWidget(row, 3, badge_frame)

    def _on_source_column_changed(self, row: int):
        """User manually remapped a column."""
        combo = self.tbl_mappings.cellWidget(row, 2)
        if not combo or row >= len(self.current_matches):
            return

        selected_text = combo.currentText()
        match = self.current_matches[row]

        if selected_text.startswith("(Unmapped"):
            match.source_column = None
            match.confidence = 0.0
            match.transformation_rule = "unmapped"
            match.tier_reason = "User unmapped"
        elif selected_text.startswith("Composite:"):
            pass
        else:
            match.source_column = selected_text
            match.confidence = 1.0
            match.tier_reason = "Manual user selection"
            match.is_user_overridden = True

            if self.combined_profile and self.sample_profile:
                src_cp = self.combined_profile.columns.get(selected_text)
                tgt_cp = self.sample_profile.columns.get(match.sample_column)
                if src_cp and tgt_cp:
                    matcher = SchemaMatcher(learning_engine)
                    rule, params = matcher._infer_transformation_rule(src_cp, tgt_cp)
                    match.transformation_rule = rule
                    match.rule_params = params

                    rule_combo = self.tbl_mappings.cellWidget(row, 4)
                    if rule_combo:
                        rule_combo.blockSignals(True)
                        rule_combo.setCurrentText(rule)
                        rule_combo.blockSignals(False)

        self._update_confidence_cell(row, match.confidence, match.tier_reason)
        self._update_row_preview(row)

    def _on_rule_changed(self, row: int, new_rule: str):
        if row >= len(self.current_matches):
            return
        match = self.current_matches[row]
        match.transformation_rule = new_rule
        match.is_user_overridden = True
        self._update_row_preview(row)

    def _update_row_preview(self, row: int):
        """Updates the preview string for a single row in the table."""
        if row >= len(self.current_matches) or self.combined_raw_df is None:
            return

        match = self.current_matches[row]
        sample_cp = self.sample_profile.columns.get(match.sample_column)

        if not match.source_column or match.transformation_rule == "unmapped":
            match.sample_transformed_preview = ""
        else:
            matcher = SchemaMatcher(learning_engine)
            raw_val = ""
            if match.transformation_rule == "concat":
                cols = match.rule_params.get("columns", [])
                vals = [str(self.combined_raw_df[c].dropna().iloc[0]) for c in cols if c in self.combined_raw_df.columns]
                raw_val = " ".join(vals)
                match.sample_transformed_preview = raw_val
            elif match.source_column in self.combined_raw_df.columns:
                series = self.combined_raw_df[match.source_column].dropna()
                if not series.empty:
                    raw_val = str(series.iloc[0])
                    match.sample_transformed_preview = matcher._preview_transform(
                        raw_val, match.transformation_rule, match.rule_params, sample_cp
                    )

        item = self.tbl_mappings.item(row, 5)
        if item:
            item.setText(match.sample_transformed_preview)

    def _update_all_previews(self):
        """Transforms the first 10 rows and updates the Harmonized Output tab."""
        if self.combined_raw_df is None or not self.current_matches or not self.sample_profile:
            return

        try:
            sample_head = self.combined_raw_df.head(10).copy()
            preview_out = DataTransformer.transform(
                sample_head,
                self.current_matches,
                self.sample_profile.column_order,
                exclude_blank=self.chk_exclude_blank.isChecked(),
            )

            self._render_dataframe_to_table(preview_out, self.tbl_out_preview)

            for r in range(len(self.current_matches)):
                self._update_row_preview(r)

        except Exception as e:
            print(f"Preview update error: {e}")

    def _render_raw_preview(self):
        """Renders first 10 rows of combined raw input in Sub-tab 2."""
        if self.combined_raw_df is None:
            return
        self._render_dataframe_to_table(self.combined_raw_df.head(10), self.tbl_raw_preview)

    def _render_dataframe_to_table(self, df: pd.DataFrame, table: QTableWidget):
        """Utility to render a Pandas DataFrame into a QTableWidget."""
        table.setRowCount(0)
        table.setColumnCount(len(df.columns))
        table.setHorizontalHeaderLabels([str(c) for c in df.columns])

        table.setRowCount(len(df))
        for r_idx in range(len(df)):
            for c_idx, col in enumerate(df.columns):
                val = df.iloc[r_idx, c_idx]
                val_str = "" if pd.isna(val) else str(val)
                item = QTableWidgetItem(val_str)
                item.setForeground(QColor(TEXT_PRIMARY))
                table.setItem(r_idx, c_idx, item)

    # ------------------------------------------------------------------
    # Export & Adaptive Learning Reinforcement
    # ------------------------------------------------------------------

    def _export_harmonized_data(self):
        """Executes full dataset transformation, saves to disk, and trains the adaptive engine."""
        if self.combined_raw_df is None or not self.current_matches or not self.sample_profile:
            return

        fmt_choice = self.combo_export_fmt.currentText()
        ext_filter = "CSV (*.csv)"
        default_ext = ".csv"
        if "Excel" in fmt_choice:
            ext_filter = "Excel (*.xlsx)"
            default_ext = ".xlsx"
        elif "JSON" in fmt_choice:
            ext_filter = "JSON (*.json)"
            default_ext = ".json"
        elif "SQLite" in fmt_choice:
            ext_filter = "SQLite Database (*.db)"
            default_ext = ".db"

        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Harmonized Dataset",
            f"harmonized_dataset{default_ext}",
            ext_filter
        )
        if not dest_path:
            return

        try:
            # 1. Transform entire combined dataset
            result_df = DataTransformer.transform(
                self.combined_raw_df,
                self.current_matches,
                self.sample_profile.column_order,
                exclude_blank=self.chk_exclude_blank.isChecked(),
            )

            # 2. Export to disk
            DataTransformer.export_dataset(result_df, dest_path)

            # 3. Train Adaptive Learning Engine if checked
            if self.chk_learn.isChecked():
                self._train_adaptive_engine(len(result_df))

            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully harmonized and exported {len(result_df)} records to:\n{dest_path}\n\n"
                f"Adaptive learning patterns have been recorded and saved for future runs."
            )

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not export harmonized dataset:\n{e}")

    def _train_adaptive_engine(self, rows_processed: int):
        """Feeds the verified mappings and fingerprints into the persistent LearningEngine."""
        verified_list = []
        for match in self.current_matches:
            if match.source_column and match.transformation_rule != "unmapped":
                learning_engine.record_harmonizer_feedback(
                    source_col=match.source_column,
                    target_col=match.sample_column,
                    confirmed=True,
                    rule=match.transformation_rule,
                    params=match.rule_params
                )
                verified_list.append({
                    "sample": match.sample_column,
                    "source": match.source_column,
                    "rule": match.transformation_rule
                })

        src_fp = self.combined_profile.fingerprint if self.combined_profile else "unknown"
        sample_fp = self.sample_profile.fingerprint if self.sample_profile else "unknown"
        learning_engine.record_harmonizer_run(src_fp, sample_fp, verified_list, rows_processed=rows_processed)

        if self.combined_profile and self.sample_profile:
            harmonizer_memory.record_user_feedback(
                self.combined_profile, self.sample_profile, self.current_matches
            )

        self._refresh_learning_stats()

    def _refresh_learning_stats(self):
        """Updates the Adaptive Harmonizer Knowledge tab and KPI cards."""
        stats = learning_engine.get_harmonizer_profile_stats()
        total_mappings = stats.get("total_learned_mappings", 0)
        fingerprints = stats.get("recognized_fingerprints", 0)

        # Update KPI cards
        if hasattr(self, "lbl_stat_mappings"):
            self.lbl_stat_mappings.setText(str(total_mappings))
        if hasattr(self, "lbl_stat_fingerprints"):
            self.lbl_stat_fingerprints.setText(str(fingerprints))

        # Populate memory associations table
        if hasattr(self, "tbl_learned_memory"):
            self.tbl_learned_memory.setRowCount(0)
            profiles = stats.get("profiles", {})
            assocs = profiles.get("column_associations", {})

            rows = []
            for src, targets in assocs.items():
                for tgt, details in targets.items():
                    rows.append((src, tgt, details.get("rule", "direct"), details.get("confirmed", 1)))

            self.tbl_learned_memory.setRowCount(len(rows))
            for idx, (src, tgt, rule, count) in enumerate(rows):
                it_src = QTableWidgetItem(src)
                it_src.setForeground(QColor(TEXT_PRIMARY))
                self.tbl_learned_memory.setItem(idx, 0, it_src)

                it_tgt = QTableWidgetItem(tgt)
                it_tgt.setFont(QFont("Segoe UI", 9, QFont.Bold))
                it_tgt.setForeground(QColor(ACCENT_PRIMARY))
                self.tbl_learned_memory.setItem(idx, 1, it_tgt)

                it_rule = QTableWidgetItem(rule)
                it_rule.setForeground(QColor(ACCENT_PURPLE))
                self.tbl_learned_memory.setItem(idx, 2, it_rule)

                it_count = QTableWidgetItem(str(count))
                it_count.setForeground(QColor(ACCENT_SUCCESS))
                self.tbl_learned_memory.setItem(idx, 3, it_count)

        html = f"""
        <div style="font-family: 'Segoe UI', sans-serif; color: {TEXT_PRIMARY};">
            <h4 style="color: {ACCENT_PURPLE}; margin-top: 0; margin-bottom: 6px;">How Adaptive Learning Works</h4>
            <p style="color: {TEXT_SECONDARY}; font-size: 11px; margin-bottom: 4px;">
                Every time you verify and export a dataset, the system securely stores which raw source columns map
                to which target sample columns, as well as the inferred conversion rules (date reformatting, currency cleaning,
                casing, composite joins).
            </p>
            <p style="color: {TEXT_MUTED}; font-size: 11px; margin: 0;">
                Subsequent harmonizations with identical or similar schema fingerprints will automatically align with
                near 100% confidence, saving you time and eliminating repetitive manual column mapping.
            </p>
        </div>
        """
        self.lbl_insights.setText(html)

    def _clear_harmonizer_memory(self):
        """Clears only the harmonizer profiles from the learning memory."""
        res = QMessageBox.question(
            self,
            "Reset Harmonizer Memory",
            "Are you sure you want to clear learned dataset column mappings?",
            QMessageBox.Yes | QMessageBox.No
        )
        if res == QMessageBox.Yes:
            with learning_engine._lock:
                profiles = learning_engine._data.setdefault("data_harmonizer_profiles", {})
                profiles["column_associations"] = {}
                profiles["composite_rules"] = {}
                profiles["fingerprint_runs"] = {}
            learning_engine.save(force=True)
            self._refresh_learning_stats()
            QMessageBox.information(self, "Memory Reset", "Data Harmonizer learned memory has been reset.")
