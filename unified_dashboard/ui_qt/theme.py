"""
ui_qt/theme.py
==============
Design system tokens, color palettes, typography, and QSS style sheet generator
for the modern, minimalist, and elegant PySide6 Unified Automation Dashboard.
"""

# ---------------------------------------------------------------------------
# Color Palette: Deep Obsidian / Charcoal Minimalist Theme
# ---------------------------------------------------------------------------
BG_BASE = "#0C0E12"          # Ultra-dark application background
BG_SIDEBAR = "#12151C"       # Navigation sidebar rail
BG_SIDEBAR_ACTIVE = "#1C2230"# Active sidebar item background
BG_CARD = "#181B24"          # Elevated container / card surface
BG_CARD_ALT = "#202533"      # Secondary card / nested header surface
BG_INPUT = "#13161F"         # Form input, textfield, combobox background
BG_HOVER = "#242A3B"         # General hover background
BG_CONSOLE = "#0A0C10"       # High-contrast terminal / console log background

# Borders
BORDER_SUBTLE = "#262C3D"    # Clean 1px hairline border
BORDER_MUTED = "#1E2330"     # Very faint separator line
BORDER_FOCUS = "#3B82F6"     # Focus state highlight

# Text Hierarchy
TEXT_PRIMARY = "#F8FAFC"     # Slate 50 - high contrast headings & values
TEXT_SECONDARY = "#94A3B8"   # Slate 400 - body text, subtitles
TEXT_MUTED = "#64748B"       # Slate 500 - hints, placeholders, timestamps
TEXT_INVERTED = "#0F172A"    # Dark text for bright pill badges
TEXT_ACCENT = "#3B82F6"      # Accent text color (Electric Blue)

# Accents & Semantic Status
ACCENT_PRIMARY = "#3B82F6"   # Electric Blue (actions, selections)
ACCENT_PRIMARY_HOVER = "#2563EB"
ACCENT_SUCCESS = "#10B981"   # Emerald (running, verified, success)
ACCENT_SUCCESS_HOVER = "#059669"
ACCENT_WARNING = "#F59E0B"   # Amber (paused, warnings, caution)
ACCENT_WARNING_HOVER = "#D97706"
ACCENT_DANGER = "#EF4444"    # Coral / Red (error, abort, discard)
ACCENT_DANGER_HOVER = "#DC2626"
ACCENT_INFO = "#06B6D4"      # Cyan (informational, stats, inspection)
ACCENT_PURPLE = "#8B5CF6"    # Violet (AI / adaptive learning tokens)

# Typography Family Preferences
FONT_FAMILY = "Segoe UI Variable Text, Inter, Segoe UI, -apple-system, system-ui, sans-serif"
FONT_MONO = "Cascadia Code, Consolas, Fira Code, Courier New, monospace"

# ---------------------------------------------------------------------------
# Distinct Log Category & Subsystem Tag Colors
# ---------------------------------------------------------------------------
LOG_TAG_COLORS = {
    # Severity & Status Level
    "INFO": "#3B82F6",                # Electric Blue
    "SUCCESS": "#10B981",             # Emerald Green
    "WARNING": "#F59E0B",             # Amber
    "ERROR": "#EF4444",               # Coral Red
    "DEBUG": "#64748B",               # Slate Grey
    "DANGER": "#EF4444",              # Coral Red

    # Session & State Management
    "SESSION": "#38BDF8",             # Sky Blue
    "SESSION RECOVERY": "#2DD4BF",    # Bright Teal
    "SETTINGS": "#818CF8",            # Indigo
    "HISTORY": "#A5B4FC",             # Lavender

    # Audit Subsystem
    "AUDIT": "#C084FC",               # Purple
    "AUDIT VERIFIED": "#34D399",      # Emerald
    "VERIFIED": "#34D399",
    "AUDIT COMPLETE": "#4ADE80",      # Bright Green
    "COMPLETE": "#4ADE80",
    "AUDIT MISSING": "#FB7185",       # Rose Red
    "MISSING": "#FB7185",
    "AUDIT MISMATCH": "#FB923C",      # Orange
    "MISMATCH": "#FB923C",
    "AUDIT CLEANUP": "#E879F9",       # Fuchsia
    "CLEANUP": "#E879F9",
    "AUDIT REMEDIATION": "#FACC15",   # Sunflower Gold
    "REMEDIATION": "#FACC15",

    # File & Checksum Verification
    "CHECKSUM MATCH": "#22C55E",      # Vivid Green
    "CONTENT MISMATCH": "#F97316",    # Dark Orange
    "SKIP": "#94A3B8",                # Cool Slate
    "SANITIZE": "#06B6D4",            # Cyan / Turquoise
    "MISPLACED FILE": "#F87171",      # Coral / Salmon
    "REMOVE WRONG FILE": "#EF4444",   # Signal Red
    "REMOVED": "#F43F5E",             # Crimson
    "RECOVERED": "#A3E635",           # Lime Green

    # Intelligence & Learning
    "LEARNING": "#A855F7",            # Electric Violet
    "ML TUNING": "#D8B4FE",           # Soft Purple

    # Navigation & Driving
    "NAV": "#60A5FA",                 # Dodger Blue
    "NAV-CONFIRM": "#38BDF8",         # Sky Blue
    "RETURN": "#3B82F6",              # Royal Blue
    "TAB": "#7DD3FC",                 # Ice Blue
    "AUTO-DETECT": "#67E8F9",         # Aquamarine
    "MODE": "#0EA5E9",                # Ocean Blue
    "GENERIC": "#64748B",             # Muted Slate

    # Network, Security & Health
    "NETWORK DISRUPTION": "#FBBF24",  # Amber Warning
    "NETWORK": "#FDE047",             # Lemon Yellow
    "SECURITY GUARD": "#FF2E63",      # Neon Alert Red
    "RETRY": "#F59E0B",               # Amber Gold
    "RECOVERY": "#FB923C",            # Bright Orange
    "FALLBACK": "#EA580C",            # Burnt Orange
    "PARALLEL": "#6366F1",            # Indigo Violet
    "ORBUND REJECTION": "#EF4444",    # Red Alert
    "REJECTION": "#EF4444",
    "UNHANDLED ERROR": "#FF0055",     # Fluorescent Red

    # Queue & Verification
    "QUEUE": "#14B8A6",               # Teal Green
    "VERIFY": "#10B981",              # Emerald Green
    "LOG": "#94A3B8",                 # Cool Slate

    # PDF Extractor Modes
    "STUDENT_SCHEDULE": "#93C5FD",    # Soft Blue
    "STUDENT_TRANSCRIPT": "#5EEAD4",  # Soft Teal
    "ATTENDANCE_ROSTER": "#86EFAC",   # Soft Green
}

FALLBACK_TAG_PALETTE = [
    "#38BDF8", "#34D399", "#FBBF24", "#F472B6", "#A78BFA",
    "#4ADE80", "#FB923C", "#2DD4BF", "#E879F9", "#60A5FA"
]


def get_log_tag_color(tag_name: str) -> str:
    """Return a unique, high-contrast hex color for a log tag string with deterministic fallback."""
    cleaned = tag_name.strip().upper()
    if cleaned in LOG_TAG_COLORS:
        return LOG_TAG_COLORS[cleaned]
    h = sum(ord(c) for c in cleaned)
    return FALLBACK_TAG_PALETTE[h % len(FALLBACK_TAG_PALETTE)]


def get_global_stylesheet() -> str:
    """Returns the comprehensive dark minimalist Qt Style Sheet (QSS)."""
    return f"""
    /* --- Base Application Window --- */
    QMainWindow, QWidget#central_widget {{
        background-color: {BG_BASE};
        color: {TEXT_PRIMARY};
        font-family: {FONT_FAMILY};
        font-size: 13px;
    }}

    /* Ensure all labels inherit dark-theme text color */
    QLabel {{
        color: {TEXT_PRIMARY};
        background: transparent;
    }}

    /* --- Universal Scrollbars --- */
    QScrollBar:vertical {{
        border: none;
        background: {BG_BASE};
        width: 8px;
        margin: 0px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_SUBTLE};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {TEXT_MUTED};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        border: none;
        background: {BG_BASE};
        height: 8px;
        margin: 0px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER_SUBTLE};
        min-width: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {TEXT_MUTED};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* --- Form Inputs (QLineEdit, QPlainTextEdit, QTextEdit) --- */
    QLineEdit, QPlainTextEdit, QTextEdit {{
        background-color: {BG_INPUT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: {ACCENT_PRIMARY};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid {BORDER_FOCUS};
        background-color: {BG_CARD};
    }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
        background-color: {BG_BASE};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_MUTED};
    }}

    /* --- ComboBox --- */
    QComboBox {{
        background-color: {BG_INPUT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 6px;
        padding: 6px 12px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border: 1px solid {BORDER_FOCUS};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 26px;
        border-left: 1px solid {BORDER_SUBTLE};
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {BG_CARD};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_SUBTLE};
        selection-background-color: {ACCENT_PRIMARY};
        selection-color: #FFFFFF;
        outline: none;
        padding: 4px;
        border-radius: 6px;
    }}

    /* --- Buttons --- */
    QPushButton {{
        background-color: {BG_CARD_ALT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 6px;
        padding: 7px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {BG_HOVER};
        border: 1px solid {BORDER_FOCUS};
    }}
    QPushButton:pressed {{
        background-color: {BG_INPUT};
    }}
    QPushButton:disabled {{
        background-color: {BG_CARD};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_MUTED};
    }}

    /* Accent Button Variants (by ObjectName or Property) */
    QPushButton#btn_primary {{
        background-color: {ACCENT_PRIMARY};
        color: #FFFFFF;
        border: none;
    }}
    QPushButton#btn_primary:hover {{
        background-color: {ACCENT_PRIMARY_HOVER};
    }}
    QPushButton#btn_success {{
        background-color: {ACCENT_SUCCESS};
        color: #FFFFFF;
        border: none;
    }}
    QPushButton#btn_success:hover {{
        background-color: {ACCENT_SUCCESS_HOVER};
    }}
    QPushButton#btn_danger {{
        background-color: {ACCENT_DANGER};
        color: #FFFFFF;
        border: none;
    }}
    QPushButton#btn_danger:hover {{
        background-color: {ACCENT_DANGER_HOVER};
    }}
    QPushButton#btn_warning {{
        background-color: {ACCENT_WARNING};
        color: #FFFFFF;
        border: none;
    }}
    QPushButton#btn_warning:hover {{
        background-color: {ACCENT_WARNING_HOVER};
    }}

    /* --- Group Box Cards --- */
    QGroupBox {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 8px;
        margin-top: 24px;
        padding: 16px;
        font-weight: 600;
        color: {TEXT_PRIMARY};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 14px;
        padding: 0 6px;
        color: {TEXT_PRIMARY};
    }}

    /* --- Tab Widget --- */
    QTabWidget::pane {{
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 8px;
        background-color: {BG_CARD};
        top: -1px;
    }}
    QTabBar::tab {{
        background: {BG_BASE};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER_SUBTLE};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 8px 16px;
        margin-right: 4px;
        font-weight: 500;
    }}
    QTabBar::tab:selected {{
        background: {BG_CARD};
        color: {ACCENT_PRIMARY};
        font-weight: 600;
        border-bottom: 2px solid {ACCENT_PRIMARY};
    }}
    QTabBar::tab:hover:!selected {{
        background: {BG_HOVER};
        color: {TEXT_PRIMARY};
    }}

    /* --- Table & Tree Views --- */
    QTableView, QTreeView, QTableWidget, QTreeWidget {{
        background-color: {BG_INPUT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 6px;
        gridline-color: {BORDER_MUTED};
        selection-background-color: {BG_SIDEBAR_ACTIVE};
        selection-color: {TEXT_PRIMARY};
        alternate-background-color: {BG_INPUT}; /* same as base = no zebra */
        outline: none;
    }}
    QTableView::item, QTreeView::item,
    QTableWidget::item, QTreeWidget::item {{
        color: {TEXT_PRIMARY};
        background-color: {BG_INPUT};
        padding: 4px 8px;
    }}
    QTableView::item:alternate, QTreeView::item:alternate,
    QTableWidget::item:alternate, QTreeWidget::item:alternate {{
        color: {TEXT_PRIMARY};
        background-color: {BG_INPUT};
    }}
    QTableView::item:hover, QTreeView::item:hover,
    QTableWidget::item:hover, QTreeWidget::item:hover {{
        background-color: {BG_HOVER};
        color: {TEXT_PRIMARY};
    }}
    QTableView::item:selected, QTreeView::item:selected,
    QTableWidget::item:selected, QTreeWidget::item:selected {{
        background-color: {BG_SIDEBAR_ACTIVE};
        border-left: 2px solid {ACCENT_PRIMARY};
        color: {TEXT_PRIMARY};
    }}
    QHeaderView::section {{
        background-color: {BG_CARD_ALT};
        color: {TEXT_PRIMARY};
        padding: 6px 10px;
        border: none;
        border-right: 1px solid {BORDER_SUBTLE};
        border-bottom: 1px solid {BORDER_SUBTLE};
        font-weight: 600;
        font-size: 12px;
    }}

    /* --- Progress Bar --- */
    QProgressBar {{
        background-color: {BG_INPUT};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 6px;
        height: 12px;
        text-align: center;
        color: {TEXT_PRIMARY};
        font-size: 11px;
        font-weight: 600;
    }}
    QProgressBar::chunk {{
        background-color: {ACCENT_PRIMARY};
        border-radius: 5px;
    }}

    /* --- Checkboxes --- */
    QCheckBox {{
        color: {TEXT_PRIMARY};
        spacing: 8px;
    }}
    QCheckBox::indicator, QTableView::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {BORDER_SUBTLE};
        background: {BG_INPUT};
    }}
    QCheckBox::indicator:hover, QTableView::indicator:hover {{
        border: 1px solid {BORDER_FOCUS};
    }}
    QCheckBox::indicator:checked, QTableView::indicator:checked {{
        background: {ACCENT_SUCCESS};
        border: 1px solid {ACCENT_SUCCESS};
    }}

    /* --- Radio Buttons --- */
    QRadioButton {{
        color: {TEXT_PRIMARY};
        spacing: 8px;
    }}
    QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 8px;
        border: 1px solid {BORDER_SUBTLE};
        background: {BG_INPUT};
    }}
    QRadioButton::indicator:hover {{
        border: 1px solid {BORDER_FOCUS};
    }}
    QRadioButton::indicator:checked {{
        background: {ACCENT_SUCCESS};
        border: 1px solid {ACCENT_SUCCESS};
    }}

    /* --- Splitters --- */
    QSplitter::handle {{
        background-color: {BORDER_SUBTLE};
    }}
    QSplitter::handle:hover {{
        background-color: {ACCENT_PRIMARY};
    }}
    """


def get_dark_palette():
    """Builds a global QPalette with Obsidian dark tones and NO white zebra striping."""
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_BASE))
    palette.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Base, QColor(BG_INPUT))
    palette.setColor(QPalette.AlternateBase, QColor(BG_INPUT))  # Never pure white
    palette.setColor(QPalette.ToolTipBase, QColor(BG_CARD))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Button, QColor(BG_CARD))
    palette.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.Link, QColor(ACCENT_PRIMARY))
    palette.setColor(QPalette.Highlight, QColor(BG_SIDEBAR_ACTIVE))
    palette.setColor(QPalette.HighlightedText, QColor(TEXT_PRIMARY))
    return palette

