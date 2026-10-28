"""
ui_qt/components/badge.py
=========================
Modern, minimalistic badge chips, status indicators, and metric KPI cards.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame
from ui_qt.theme import (
    BG_CARD, BG_CARD_ALT, BG_INPUT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_DANGER, ACCENT_INFO
)


class StatusBadge(QFrame):
    """A compact, pill-shaped badge indicating state (Ready, Running, Success, etc.)."""

    VARIANTS = {
        "primary": (ACCENT_PRIMARY, "rgba(59, 130, 246, 0.15)", TEXT_PRIMARY),
        "success": (ACCENT_SUCCESS, "rgba(16, 185, 129, 0.15)", TEXT_PRIMARY),
        "warning": (ACCENT_WARNING, "rgba(245, 158, 11, 0.15)", TEXT_PRIMARY),
        "danger": (ACCENT_DANGER, "rgba(239, 68, 68, 0.15)", TEXT_PRIMARY),
        "info": (ACCENT_INFO, "rgba(6, 182, 212, 0.15)", TEXT_PRIMARY),
        "neutral": (TEXT_MUTED, "rgba(100, 116, 139, 0.15)", TEXT_SECONDARY),
    }

    def __init__(self, text: str = "Ready", variant: str = "neutral", parent=None):
        super().__init__(parent)
        self.lbl_dot = QLabel("●")
        self.lbl_text = QLabel(text)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 10, 3)
        layout.setSpacing(6)
        layout.addWidget(self.lbl_dot)
        layout.addWidget(self.lbl_text)

        self.set_variant(variant, text)

    def set_variant(self, variant: str, text: str | None = None):
        if text is not None:
            self.lbl_text.setText(text)

        accent_color, bg_color, text_color = self.VARIANTS.get(variant, self.VARIANTS["neutral"])

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {accent_color};
                border-radius: 12px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                font-size: 11px;
                font-weight: 600;
            }}
        """)
        self.lbl_dot.setStyleSheet(f"color: {accent_color}; font-size: 10px; background: transparent; border: none;")
        self.lbl_text.setStyleSheet(f"color: {text_color}; font-size: 11px; font-weight: 600; background: transparent; border: none;")

    def text(self) -> str:
        return self.lbl_text.text()


class MetricCard(QFrame):
    """An elevated telemetry/KPI card with bold metrics and subtle border."""

    def __init__(self, title: str, value: str = "0", subtitle: str = "", icon: str = "📊", accent: str = ACCENT_PRIMARY, parent=None):
        super().__init__(parent)
        self.accent = accent

        self.setObjectName("MetricCard")
        self.setStyleSheet(f"""
            QFrame#MetricCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 8px;
                padding: 12px;
            }}
            QFrame#MetricCard:hover {{
                border: 1px solid {accent};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        # Header row: Icon + Title
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setStyleSheet(f"font-size: 18px; color: {accent}; background: transparent; border: none;")
        self.lbl_title = QLabel(title.upper())
        self.lbl_title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {TEXT_MUTED}; letter-spacing: 0.5px; background: transparent; border: none;")
        hdr_layout.addWidget(self.lbl_icon)
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch()

        # Big Value
        self.lbl_value = QLabel(value)
        self.lbl_value.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {TEXT_PRIMARY}; background: transparent; border: none; margin-top: 4px;")

        # Subtitle
        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; background: transparent; border: none;")

        layout.addLayout(hdr_layout)
        layout.addWidget(self.lbl_value)
        layout.addWidget(self.lbl_sub)
        layout.addStretch()

    def set_value(self, val: str):
        self.lbl_value.setText(str(val))
