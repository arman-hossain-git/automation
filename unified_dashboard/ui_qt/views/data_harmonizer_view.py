"""
ui_qt/views/data_harmonizer_view.py
===================================
Adaptive Dataset Harmonizer View for the PySide6 Unified Dashboard.

Allows users to upload one or multiple raw datasets (CSV, Excel, JSON, SQLite)
and a sample dataset (or multiple), automatically aligns and transforms the raw
data to match the sample format using adaptive learning, and lets the user
interactively review, verify, and export the harmonized dataset.
"""

import os
import re
from typing import List, Dict, Any, Optional
import pandas as pd

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QFileDialog, QMessageBox, QTabWidget, QCheckBox, QProgressBar,
    QSplitter, QScrollArea, QSizePolicy, QDialog, QListWidget, QListWidgetItem, QDialogButtonBox
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


class SheetSelectionDialog(QDialog):
    """Dialog allowing user to choose which sheets/tabs from a multi-tab file to import."""

    def __init__(self, filename: str, sheet_names: List[str], single_select: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Select {'Tab' if single_select else 'Tabs'}: {os.path.basename(filename)}")
        self.resize(400, 320)
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
                item.setCheckState(Qt.CheckState.Checked)
            self.list_widget.addItem(item)

        if single_select and self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        layout.addWidget(self.list_widget)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

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
    """PySide6 view for multi-source data reshaping, normalization, and adaptive harmonization."""

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
    # UI Layout Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        # ── 1. Header Banner ──────────────────────────────────────────
        hdr = QFrame()
        hdr.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px; padding: 10px;"
        )
        h_layout = QHBoxLayout(hdr)
        h_layout.setContentsMargins(12, 6, 12, 6)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_title = QLabel("Adaptive Dataset Harmonizer")
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

        # ── 2. Input Datasets & Sample Loader Grid ────────────────────
        inputs_frame = QFrame()
        inputs_frame.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px; padding: 8px;"
        )
        grid = QGridLayout(inputs_frame)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(14)

        # Left Column: Raw Input Datasets
        raw_header = QLabel("1. Raw Source Dataset(s) or Database")
        raw_header.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        grid.addWidget(raw_header, 0, 0)

        raw_btn_box = QHBoxLayout()
        self.btn_add_raw = QPushButton("+ Add Raw Files (CSV, XLSX, JSON, DB)")
        self.btn_add_raw.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_PRIMARY_HOVER};
            }}
        """)
        self.btn_add_raw.clicked.connect(self._browse_raw_files)
        raw_btn_box.addWidget(self.btn_add_raw)

        self.btn_clear_raw = QPushButton("Clear")
        self.btn_clear_raw.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT};
                color: {TEXT_MUTED};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        self.btn_clear_raw.clicked.connect(self._clear_raw_files)
        raw_btn_box.addWidget(self.btn_clear_raw)
        grid.addLayout(raw_btn_box, 1, 0)

        self.lbl_raw_status = QLabel("No raw datasets loaded.")
        self.lbl_raw_status.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        grid.addWidget(self.lbl_raw_status, 2, 0)

        # Combine Mode Selector
        combine_box = QHBoxLayout()
        lbl_mode = QLabel("Multi-Source Combine:")
        lbl_mode.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")
        combine_box.addWidget(lbl_mode)

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
        combine_box.addWidget(self.combo_combine_mode)
        combine_box.addStretch()
        grid.addLayout(combine_box, 3, 0)

        # Right Column: Sample Dataset(s)
        sample_header = QLabel("2. Sample Target Dataset (Gold Standard Schema)")
        sample_header.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        grid.addWidget(sample_header, 0, 1)

        sample_btn_box = QHBoxLayout()
        self.btn_upload_sample = QPushButton("📂 Upload Sample Dataset")
        self.btn_upload_sample.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_PRIMARY};
            }}
        """)
        self.btn_upload_sample.clicked.connect(self._browse_sample_file)
        sample_btn_box.addWidget(self.btn_upload_sample)

        self.btn_clear_sample = QPushButton("Clear")
        self.btn_clear_sample.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT};
                color: {TEXT_MUTED};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        self.btn_clear_sample.clicked.connect(self._clear_sample_file)
        sample_btn_box.addWidget(self.btn_clear_sample)
        grid.addLayout(sample_btn_box, 1, 1)

        self.lbl_sample_status = QLabel("No sample dataset loaded.")
        self.lbl_sample_status.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        grid.addWidget(self.lbl_sample_status, 2, 1)

        # Trigger Button
        self.btn_analyze = QPushButton("⚡ Align Schemas & Auto-Match Mappings")
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_PURPLE};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
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
        grid.addWidget(self.btn_analyze, 3, 1)

        root.addWidget(inputs_frame)

        # ── 3. Main Splitter: Mapping Matrix & Live Data Preview ──────
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)

        # Upper Area: Mapping Matrix Table
        mapping_card = QFrame()
        mapping_card.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px;"
        )
        map_layout = QVBoxLayout(mapping_card)
        map_layout.setContentsMargins(12, 10, 12, 10)
        map_layout.setSpacing(8)

        map_title_box = QHBoxLayout()
        lbl_map_title = QLabel("Schema Harmonization & Transformation Matrix")
        lbl_map_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        map_title_box.addWidget(lbl_map_title)

        self.lbl_match_summary = QLabel("Upload both raw & sample datasets to preview mappings.")
        self.lbl_match_summary.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")
        map_title_box.addWidget(self.lbl_match_summary)
        map_title_box.addStretch()

        self.btn_reapply_all = QPushButton("Recompute Previews")
        self.btn_reapply_all.setVisible(False)
        self.btn_reapply_all.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        self.btn_reapply_all.clicked.connect(self._update_all_previews)
        map_title_box.addWidget(self.btn_reapply_all)

        map_layout.addLayout(map_title_box)

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
        self.tbl_mappings.setStyleSheet(f"""
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
        """)
        map_layout.addWidget(self.tbl_mappings)
        splitter.addWidget(mapping_card)

        # Lower Area: Live Tabbed Preview
        preview_card = QFrame()
        preview_card.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px;"
        )
        prev_layout = QVBoxLayout(preview_card)
        prev_layout.setContentsMargins(12, 8, 12, 8)
        prev_layout.setSpacing(6)

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

        # Tab 1: Harmonized Target Preview
        self.tbl_out_preview = QTableWidget(0, 0)
        self.tbl_out_preview.setStyleSheet(self.tbl_mappings.styleSheet())
        self.tabs_preview.addTab(self.tbl_out_preview, "✨ Harmonized Output (Sample Order & Format)")

        # Tab 2: Combined Raw Input Preview
        self.tbl_raw_preview = QTableWidget(0, 0)
        self.tbl_raw_preview.setStyleSheet(self.tbl_mappings.styleSheet())
        self.tabs_preview.addTab(self.tbl_raw_preview, "📋 Raw / Combined Input Preview")

        # Tab 3: Learned Insights
        self.tab_insights = QWidget()
        ins_layout = QVBoxLayout(self.tab_insights)
        self.lbl_insights = QLabel("Learned patterns will appear here once verified.")
        self.lbl_insights.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        ins_layout.addWidget(self.lbl_insights)
        self.tabs_preview.addTab(self.tab_insights, "🧠 Adaptive Harmonizer Knowledge")

        prev_layout.addWidget(self.tabs_preview)
        splitter.addWidget(preview_card)

        splitter.setSizes([320, 240])
        root.addWidget(splitter, stretch=1)

        # ── 4. Bottom Action Bar ──────────────────────────────────────
        footer = QFrame()
        footer.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px; padding: 6px 12px;"
        )
        foot_layout = QHBoxLayout(footer)
        foot_layout.setContentsMargins(12, 6, 12, 6)

        self.chk_learn = QCheckBox("Save & reinforce learned column associations for future runs")
        self.chk_learn.setChecked(True)
        self.chk_learn.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        foot_layout.addWidget(self.chk_learn)

        foot_layout.addStretch()

        lbl_export_fmt = QLabel("Export Format:")
        lbl_export_fmt.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        foot_layout.addWidget(lbl_export_fmt)

        self.combo_export_fmt = QComboBox()
        self.combo_export_fmt.addItems(["CSV (.csv)", "Excel (.xlsx)", "JSON (.json)", "SQLite (.db)"])
        self.combo_export_fmt.setStyleSheet(f"""
            QComboBox {{
                background-color: {BG_INPUT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 12px;
            }}
        """)
        foot_layout.addWidget(self.combo_export_fmt)

        self.btn_export = QPushButton("🚀 Transform & Export Harmonized Data")
        self.btn_export.setEnabled(False)
        self.btn_export.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_SUCCESS};
                color: #FFFFFF;
                border-radius: 6px;
                padding: 8px 18px;
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
        foot_layout.addWidget(self.btn_export)

        root.addWidget(footer)

    # ------------------------------------------------------------------
    # Data Loading & Management
    # ------------------------------------------------------------------

    def _browse_raw_files(self):
        """Allows selecting one or multiple raw dataset files, supporting multi-tab Excel files."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Raw Datasets or Database",
            "",
            "All Supported (*.csv *.xlsx *.xls *.json *.db *.sqlite *.sqlite3);;CSV (*.csv);;Excel (*.xlsx *.xls);;JSON (*.json);;SQLite (*.db *.sqlite)"
        )
        if not files:
            return

        loaded_profiles = []
        for f in files:
            sheets = DataProfiler.list_sheets_or_tables(f)
            if len(sheets) > 1:
                dlg = SheetSelectionDialog(f, sheets, single_select=False, parent=self)
                if dlg.exec() == QDialog.Accepted:
                    chosen = dlg.get_selected_sheets()
                    for sheet in chosen:
                        try:
                            prof = DataProfiler.profile_dataset(f, table_name=sheet)
                            loaded_profiles.append(prof)
                        except Exception as e:
                            QMessageBox.warning(self, "Load Error", f"Could not load tab '{sheet}':\n{e}")
            else:
                try:
                    prof = DataProfiler.profile_dataset(f)
                    loaded_profiles.append(prof)
                except Exception as e:
                    QMessageBox.warning(self, "Load Error", f"Could not load {os.path.basename(f)}:\n{e}")

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
        if not self.raw_profiles:
            self.lbl_raw_status.setText("No raw datasets loaded.")
            return

        total_rows = sum(p.row_count for p in self.raw_profiles)
        total_cols = sum(p.column_count for p in self.raw_profiles)
        file_names = ", ".join(p.source_name for p in self.raw_profiles[:3])
        if len(self.raw_profiles) > 3:
            file_names += f" (+{len(self.raw_profiles)-3} more)"

        self.lbl_raw_status.setText(
            f"✅ {len(self.raw_profiles)} dataset table(s) loaded ({file_names}) — Total {total_rows} rows"
        )

    def _browse_sample_file(self):
        """Allows selecting a target sample dataset, with tab selection for multi-sheet workbooks."""
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Select Sample Target Dataset",
            "",
            "All Supported (*.csv *.xlsx *.xls *.json *.db *.sqlite *.sqlite3);;CSV (*.csv);;Excel (*.xlsx *.xls);;JSON (*.json)"
        )
        if not f:
            return

        chosen_sheet = None
        sheets = DataProfiler.list_sheets_or_tables(f)
        if len(sheets) > 1:
            dlg = SheetSelectionDialog(f, sheets, single_select=True, parent=self)
            if dlg.exec() == QDialog.Accepted:
                chosen_sheet = dlg.get_single_sheet()
            else:
                return

        try:
            self.sample_profile = DataProfiler.profile_dataset(f, table_name=chosen_sheet)
            self.lbl_sample_status.setText(
                f"✅ Sample: {self.sample_profile.source_name} ({self.sample_profile.column_count} cols, {self.sample_profile.row_count} sample rows)"
            )
            self._check_ready()
        except Exception as e:
            QMessageBox.warning(self, "Sample Error", f"Could not profile sample dataset:\n{e}")

    def _clear_sample_file(self):
        self.sample_profile = None
        self.lbl_sample_status.setText("No sample dataset loaded.")
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

    def _update_confidence_cell(self, row: int, confidence: float, reason: str):
        pct = int(confidence * 100)
        badge_frame = QFrame()
        b_layout = QHBoxLayout(badge_frame)
        b_layout.setContentsMargins(4, 2, 4, 2)

        if confidence >= 0.85:
            badge = StatusBadge(f"{pct}% ({reason[:24]})", "success")
        elif confidence >= 0.50:
            badge = StatusBadge(f"{pct}% (Review)", "warning")
        elif confidence > 0.0:
            badge = StatusBadge(f"{pct}% (Low)", "danger")
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
            # Preserve composite rule
            pass
        else:
            match.source_column = selected_text
            match.confidence = 1.0  # User explicitly chose it
            match.tier_reason = "Manual user selection"
            match.is_user_overridden = True

            # Re-infer rule if needed
            if self.combined_profile and self.sample_profile:
                src_cp = self.combined_profile.columns.get(selected_text)
                tgt_cp = self.sample_profile.columns.get(match.sample_column)
                if src_cp and tgt_cp:
                    matcher = SchemaMatcher(learning_engine)
                    rule, params = matcher._infer_transformation_rule(src_cp, tgt_cp)
                    match.transformation_rule = rule
                    match.rule_params = params

                    # Update rule combo
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
            # Find sample raw value
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
            # Transform head
            sample_head = self.combined_raw_df.head(10).copy()
            preview_out = DataTransformer.transform(sample_head, self.current_matches, self.sample_profile.column_order)

            # Render in Harmonized Output Tab
            self._render_dataframe_to_table(preview_out, self.tbl_out_preview)

            # Update row preview strings
            for r in range(len(self.current_matches)):
                self._update_row_preview(r)

        except Exception as e:
            print(f"Preview update error: {e}")

    def _render_raw_preview(self):
        """Renders first 10 rows of combined raw input in Tab 2."""
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

        # Pick destination
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
                self.sample_profile.column_order
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
                # Record individual column association feedback
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

        # Record dataset fingerprint recognition
        src_fp = self.combined_profile.fingerprint if self.combined_profile else "unknown"
        sample_fp = self.sample_profile.fingerprint if self.sample_profile else "unknown"
        learning_engine.record_harmonizer_run(src_fp, sample_fp, verified_list, rows_processed=rows_processed)

        self._refresh_learning_stats()

    def _refresh_learning_stats(self):
        """Updates the Adaptive Harmonizer Knowledge tab."""
        stats = learning_engine.get_harmonizer_profile_stats()
        total_mappings = stats.get("total_learned_mappings", 0)
        fingerprints = stats.get("recognized_fingerprints", 0)

        html = f"""
        <div style="font-family: 'Segoe UI', sans-serif; padding: 12px; color: {TEXT_PRIMARY};">
            <h3 style="color: {ACCENT_PURPLE}; margin-bottom: 8px;">Adaptive Learning Status</h3>
            <p><b>Learned Column Associations:</b> {total_mappings}</p>
            <p><b>Recognized Dataset Fingerprints:</b> {fingerprints}</p>
            <p style="color: {TEXT_MUTED}; font-size: 11px;">
                Every time you verify and export a dataset, the system remembers which raw columns map to
                which sample columns and stores the optimal conversion rules. Subsequent transformations on
                matching schemas will auto-align with near 100% confidence.
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
