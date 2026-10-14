"""
ui_qt/views/learning_view.py
============================
Adaptive Learning Insights View for the PySide6 Unified Dashboard.

Displays everything the automation suite has learned across all past runs:
  - Live KPI tiles: total runs, items processed, files handled, domains learned,
    XPath patterns, student insights.
  - Tab 1: Run History (historical performance and throughput metrics)
  - Tab 2: Domain Profiles (learned XPath selectors and hit counts)
  - Tab 3: Student Insights (per-student learned properties with search filter)
  - Tab 4: DevTools Requirements (per-domain DevTools requirement heuristics)
  - Tab 5: PDF Extractor Profiles (tuned spatial rules and confidence scores)
  - Actions: Refresh All Insights, Clear Learning Memory
"""

import datetime
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QMessageBox
)
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BG_INPUT, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_DANGER,
    ACCENT_INFO, ACCENT_PURPLE
)
from ui_qt.components.badge import MetricCard
from core.learning_system import learning_engine


class LearningView(QWidget):
    """Modern PySide6 Learning Insights Dashboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kpi_cards = {}
        self._build_ui()
        self.refresh_all()

    # ------------------------------------------------------------------
    # UI Layout Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        # ── Header Banner ──────────────────────────────────────────────
        hdr = QFrame()
        hdr.setStyleSheet(
            f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 8px; padding: 10px;"
        )
        h_layout = QHBoxLayout(hdr)
        h_layout.setContentsMargins(12, 6, 12, 6)

        t_box = QVBoxLayout()
        t_box.setSpacing(3)
        lbl_title = QLabel("🧠  Adaptive Learning Insights")
        lbl_title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        lbl_sub = QLabel(
            "The system learns from every run — faster XPath resolution, smarter timeouts, "
            "and per-student memory that speeds up repeated processing."
        )
        lbl_sub.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; border: none;")
        t_box.addWidget(lbl_title)
        t_box.addWidget(lbl_sub)
        h_layout.addLayout(t_box, stretch=1)

        btn_refresh = QPushButton("🔄  Refresh")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh_all)
        h_layout.addWidget(btn_refresh)

        btn_clear = QPushButton("⚠️  Clear Memory")
        btn_clear.setObjectName("btn_danger")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.clicked.connect(self._confirm_clear_memory)
        h_layout.addWidget(btn_clear)

        root.addWidget(hdr)

        # ── KPI Row ────────────────────────────────────────────────────
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(12)

        kpi_defs = [
            (0, 0, "total_runs",            "Total Runs",        "0", "Completed automation sessions", "🔄", ACCENT_PRIMARY),
            (0, 1, "total_items_processed", "Records Processed", "0", "Total student & ledger records completed", "📋", ACCENT_SUCCESS),
            (0, 2, "total_files_handled",   "Files Handled",     "0", "Files exported and uploaded to PCR", "📁", ACCENT_WARNING),
            (1, 0, "domain_count",          "Domains Learned",   "0", "Unique portal domains mapped & memorized", "🌐", ACCENT_PURPLE),
            (1, 1, "learned_patterns",      "XPath Patterns",    "0", "Optimized UI selectors & adaptive fallbacks", "🔍", ACCENT_INFO),
            (1, 2, "learned_students",      "Student Insights",  "0", "Unique student IDs with cached properties", "👤", "#FB923C"),
        ]

        for row, col, key, title, def_val, sub, icon, accent in kpi_defs:
            card = MetricCard(title=title, value=def_val, subtitle=sub, icon=icon, accent=accent)
            self._kpi_cards[key] = card
            kpi_grid.addWidget(card, row, col)

        root.addLayout(kpi_grid)

        # ── Detailed Tabs ──────────────────────────────────────────────
        self.notebook = QTabWidget()
        root.addWidget(self.notebook, stretch=1)

        # Tab 1: Run History
        self.tab_history = QWidget()
        self._build_history_tab()
        self.notebook.addTab(self.tab_history, " 📈 Run History ")

        # Tab 2: Domain Profiles
        self.tab_domains = QWidget()
        self._build_domains_tab()
        self.notebook.addTab(self.tab_domains, " 🌐 Domain Profiles ")

        # Tab 3: Student Insights
        self.tab_students = QWidget()
        self._build_students_tab()
        self.notebook.addTab(self.tab_students, " 👤 Student Insights ")

        # Tab 4: DevTools Requirements
        self.tab_devtools = QWidget()
        self._build_devtools_tab()
        self.notebook.addTab(self.tab_devtools, " 🛠 DevTools Requirements ")

        # Tab 5: PDF Extractor Profiles
        self.tab_pdf = QWidget()
        self._build_pdf_tab()
        self.notebook.addTab(self.tab_pdf, " 🧾 PDF Extractor Profiles ")

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh_all)
        self._timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_all()

    # ------------------------------------------------------------------
    # Tab Builders
    # ------------------------------------------------------------------

    def _setup_table(self, table: QTableWidget, headers: list[str]):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        if len(headers) > 1:
            table.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)

    def _build_history_tab(self):
        layout = QVBoxLayout(self.tab_history)
        layout.setContentsMargins(8, 8, 8, 8)
        self.table_history = QTableWidget()
        self._setup_table(self.table_history, ["Timestamp", "Module", "Items Done", "Files Handled", "Duration (s)", "Throughput", "Outcome"])
        layout.addWidget(self.table_history)

    def _build_domains_tab(self):
        layout = QVBoxLayout(self.tab_domains)
        layout.setContentsMargins(8, 8, 8, 8)
        self.table_domains = QTableWidget()
        self._setup_table(self.table_domains, ["Domain", "Field / Element Key", "Learned XPath Selector", "Hit Count"])
        layout.addWidget(self.table_domains)

    def _build_students_tab(self):
        layout = QVBoxLayout(self.tab_students)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        search_box = QHBoxLayout()
        lbl = QLabel("Search Student ID or Insight:")
        lbl.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY};")
        self.txt_student_filter = QLineEdit()
        self.txt_student_filter.setPlaceholderText("Filter insights...")
        self.txt_student_filter.textChanged.connect(self._filter_students)
        search_box.addWidget(lbl)
        search_box.addWidget(self.txt_student_filter, stretch=1)
        layout.addLayout(search_box)

        self.table_students = QTableWidget()
        self._setup_table(self.table_students, ["Student ID", "Insight Category / Key", "Learned Value"])
        layout.addWidget(self.table_students)

    def _build_devtools_tab(self):
        layout = QVBoxLayout(self.tab_devtools)
        layout.setContentsMargins(8, 8, 8, 8)
        self.table_devtools = QTableWidget()
        self._setup_table(self.table_devtools, ["Domain", "Requires DevTools", "DevTools Success Rate", "Headless Success Rate", "Reason / State"])
        layout.addWidget(self.table_devtools)

    def _build_pdf_tab(self):
        layout = QVBoxLayout(self.tab_pdf)
        layout.setContentsMargins(8, 8, 8, 8)
        self.table_pdf = QTableWidget()
        self._setup_table(self.table_pdf, ["Preset / Profile Name", "Confidence Score", "Learned Rules Count", "Verified Samples"])
        layout.addWidget(self.table_pdf)

    # ------------------------------------------------------------------
    # Data Refreshing
    # ------------------------------------------------------------------

    def refresh_all(self):
        """Fetch latest statistics and profiles from learning_engine."""
        try:
            stats = learning_engine.get_summary_stats()
        except Exception:
            stats = {}

        # Update KPI cards
        for key, card in self._kpi_cards.items():
            val = stats.get(key, 0)
            card.set_value(f"{val:,}" if isinstance(val, (int, float)) else str(val))

        # Refresh tables
        self._refresh_history()
        self._refresh_domains()
        self._refresh_students()
        self._refresh_devtools()
        self._refresh_pdf()

    def _refresh_history(self):
        try:
            history = learning_engine.get_run_history()
        except Exception:
            history = []

        self.table_history.setRowCount(len(history))
        for row, item in enumerate(reversed(history)):
            ts = item.get("timestamp", "-")
            mod = item.get("module", "-")
            items = str(item.get("items_processed", 0))
            files = str(item.get("files_handled", 0))
            dur = f"{item.get('duration_seconds', 0.0):.1f}"
            thp = f"{item.get('items_per_minute', 0.0):.1f}/min"
            outcome = "✓ Success" if item.get("success", True) else "❌ Failed"

            vals = [ts, mod, items, files, dur, thp, outcome]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setForeground(Qt.white)
                self.table_history.setItem(row, col, it)

    def _refresh_domains(self):
        try:
            profiles = learning_engine.get_domain_profiles()
        except Exception:
            profiles = {}

        rows = []
        for domain, ddata in profiles.items():
            xpaths = ddata.get("xpaths", {})
            for field_key, path_counts in xpaths.items():
                for xpath, count in path_counts.items():
                    rows.append((domain, field_key, xpath, str(count)))

        self.table_domains.setRowCount(len(rows))
        for r_idx, (domain, fkey, xpath, count) in enumerate(rows):
            for c_idx, val in enumerate([domain, fkey, xpath, count]):
                it = QTableWidgetItem(val)
                it.setForeground(Qt.white)
                self.table_domains.setItem(r_idx, c_idx, it)

    def _refresh_students(self):
        try:
            insights = learning_engine.get_all_student_insights()
        except Exception:
            insights = {}

        self._all_student_rows = []
        for sid, props in insights.items():
            for k, v in props.items():
                self._all_student_rows.append((str(sid), str(k), str(v)))

        self._filter_students()

    def _filter_students(self):
        query = self.txt_student_filter.text().lower().strip()
        matched = [
            r for r in getattr(self, "_all_student_rows", [])
            if not query or query in r[0].lower() or query in r[1].lower() or query in r[2].lower()
        ]

        self.table_students.setRowCount(len(matched))
        for r_idx, (sid, k, v) in enumerate(matched):
            for c_idx, val in enumerate([sid, k, v]):
                it = QTableWidgetItem(val)
                it.setForeground(Qt.white)
                self.table_students.setItem(r_idx, c_idx, it)

    def _refresh_devtools(self):
        try:
            reqs = learning_engine.get_all_devtools_requirements()
        except Exception:
            reqs = {}

        self.table_devtools.setRowCount(len(reqs))
        for r_idx, (domain, info) in enumerate(reqs.items()):
            req = "Yes" if info.get("requires_devtools") else "No"
            d_rate = f"{info.get('devtools_success_rate', 0.0) * 100:.0f}%"
            h_rate = f"{info.get('headless_success_rate', 0.0) * 100:.0f}%"
            reason = info.get("reason", "-")
            for c_idx, val in enumerate([domain, req, d_rate, h_rate, reason]):
                it = QTableWidgetItem(val)
                it.setForeground(Qt.white)
                self.table_devtools.setItem(r_idx, c_idx, it)

    def _refresh_pdf(self):
        try:
            profiles = learning_engine._data.get("pdf_extractor_profiles", {})
        except Exception:
            profiles = {}

        self.table_pdf.setRowCount(len(profiles))
        for r_idx, (pname, pdata) in enumerate(profiles.items()):
            conf = f"{pdata.get('confidence_score', 0.0) * 100:.1f}%"
            rules_cnt = str(len(pdata.get("rules", {})))
            samples_cnt = str(len(pdata.get("verified_samples", [])))
            for c_idx, val in enumerate([pname, conf, rules_cnt, samples_cnt]):
                it = QTableWidgetItem(val)
                it.setForeground(Qt.white)
                self.table_pdf.setItem(r_idx, c_idx, it)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _confirm_clear_memory(self):
        res = QMessageBox.warning(
            self,
            "Clear Learning Memory",
            "Are you sure you want to completely erase all learned patterns, "
            "domain insights, XPath hit counts, and performance profiles?\n\n"
            "This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if res == QMessageBox.Yes:
            try:
                learning_engine.clear_learning_memory()
                self.refresh_all()
                QMessageBox.information(self, "Memory Cleared", "All learned data has been successfully reset.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to reset memory: {e}")
