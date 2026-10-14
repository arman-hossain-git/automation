"""
ui_qt/views/home_view.py
========================
Home Dashboard View displaying Adaptive Learning Telemetry & KPIs.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame
)
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_INFO, ACCENT_PURPLE
)
from ui_qt.components.badge import MetricCard
from core.learning_system import learning_engine


class HomeView(QWidget):
    """Modern home dashboard displaying live adaptive learning KPIs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kpi_cards = {}
        self._build_ui()
        self.refresh_kpis()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        # Header Frame
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 14px;")
        h_layout = QHBoxLayout(hdr_frame)
        h_layout.setContentsMargins(14, 8, 14, 8)

        t_layout = QVBoxLayout()
        t_layout.setSpacing(4)
        lbl_title = QLabel("🏠  Home — Adaptive Learning Telemetry")
        lbl_title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")

        lbl_sub = QLabel(
            "Real-time Key Performance Indicators (KPIs) learned and accumulated by the "
            "adaptive automation engine across all runs."
        )
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet(f"font-size: 12px; color: {TEXT_SECONDARY}; border: none;")

        t_layout.addWidget(lbl_title)
        t_layout.addWidget(lbl_sub)
        h_layout.addLayout(t_layout, stretch=1)

        btn_refresh = QPushButton("🔄  Refresh KPIs")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
                border: none;
            }}
        """)
        btn_refresh.clicked.connect(self.refresh_kpis)
        h_layout.addWidget(btn_refresh, alignment=Qt.AlignVCenter)

        root.addWidget(hdr_frame)

        # Grid of KPI Cards (2 rows x 3 columns)
        grid_layout = QGridLayout()
        grid_layout.setSpacing(14)

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
            self.kpi_cards[key] = card
            grid_layout.addWidget(card, row, col)

        root.addLayout(grid_layout)
        root.addStretch()

        # Auto-refresh timer to keep dashboard KPIs continuously up-to-date
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self.refresh_kpis)
        self._refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_kpis()

    def refresh_kpis(self):
        try:
            kpis = learning_engine.get_summary_stats()
        except Exception:
            try:
                kpis = learning_engine.get_all_kpis()
            except Exception:
                kpis = {}

        for key, card in self.kpi_cards.items():
            val = kpis.get(key, 0)
            card.set_value(f"{val:,}" if isinstance(val, (int, float)) else str(val))
