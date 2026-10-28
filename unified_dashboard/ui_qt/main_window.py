"""
ui_qt/main_window.py
====================
Main application window for the PySide6 Unified System Automation Dashboard.

Features:
  - Sleek, minimalist collapsible navigation rail sidebar (animated width)
  - QStackedWidget for flicker-free, instantaneous view transitions
  - Accordion sub-feature expansion for Files module
  - Branding header with version badge
  - Windows 11 High-DPI aware styling
"""

import sys
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFrame, QScrollArea, QSizePolicy
)
from PySide6.QtGui import QFont, QIcon

from ui_qt.theme import (
    BG_BASE, BG_SIDEBAR, BG_SIDEBAR_ACTIVE, BG_CARD, BG_CARD_ALT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER,
    get_global_stylesheet
)
from ui_qt.views.home_view import HomeView

SIDEBAR_EXPANDED_W = 260
SIDEBAR_COLLAPSED_W = 56


# ---------------------------------------------------------------------------
# Navigation Item Widget
# ---------------------------------------------------------------------------
class NavItem(QFrame):
    """A single navigation row in the sidebar rail."""

    def __init__(self, icon: str, title: str, subtitle: str = "",
                 tool_id: str = "", has_sub: bool = False, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.has_sub = has_sub
        self._is_active = False
        self.sub_items: list = []
        self._sub_expanded = False
        self.sub_container: "QFrame | None" = None

        self.setObjectName("NavItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style(active=False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(10)

        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setStyleSheet(f"font-size: 18px; background: transparent; border: none; min-width: 24px;")
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_icon)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {TEXT_PRIMARY}; background: transparent; border: none;")
        text_box.addWidget(self.lbl_title)

        if subtitle:
            self.lbl_sub = QLabel(subtitle)
            self.lbl_sub.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent; border: none;")
            text_box.addWidget(self.lbl_sub)
        else:
            self.lbl_sub = None

        layout.addLayout(text_box, stretch=1)

        if has_sub:
            self.lbl_arrow = QLabel("›")
            self.lbl_arrow.setStyleSheet(f"font-size: 14px; color: {TEXT_MUTED}; background: transparent; border: none;")
            layout.addWidget(self.lbl_arrow)
        else:
            self.lbl_arrow = None

    def _apply_style(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QFrame#NavItem {{
                    background-color: {BG_SIDEBAR_ACTIVE};
                    border-radius: 8px;
                    border-left: 3px solid {ACCENT_PRIMARY};
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame#NavItem {{
                    background-color: transparent;
                    border-radius: 8px;
                    border-left: 3px solid transparent;
                }}
                QFrame#NavItem:hover {{
                    background-color: {BG_CARD_ALT};
                }}
            """)

    def set_active(self, active: bool):
        self._is_active = active
        self._apply_style(active)
        if active:
            self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ACCENT_PRIMARY}; background: transparent; border: none;")
        else:
            self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {TEXT_PRIMARY}; background: transparent; border: none;")

    def set_text_visible(self, visible: bool):
        for w in [self.lbl_title, self.lbl_sub, self.lbl_arrow]:
            if w:
                w.setVisible(visible)
        if self.sub_container:
            if not visible:
                self.sub_container.setVisible(False)
            elif self._sub_expanded:
                self.sub_container.setVisible(True)


# ---------------------------------------------------------------------------
# Sub-feature Item Widget
# ---------------------------------------------------------------------------
class SubNavItem(QFrame):
    """A child navigation item nested under a parent NavItem."""

    def __init__(self, icon: str, title: str, desc: str, sub_id: str, parent=None):
        super().__init__(parent)
        self.sub_id = sub_id
        self.setObjectName("SubNavItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#SubNavItem {{
                background-color: {BG_CARD_ALT};
                border-radius: 6px;
                border: none;
            }}
            QFrame#SubNavItem:hover {{
                background-color: {BG_SIDEBAR_ACTIVE};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 6, 10, 6)
        layout.setSpacing(1)

        self.lbl_title = QLabel(f"{icon}  {title}")
        self.lbl_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {TEXT_PRIMARY}; background: transparent; border: none;")
        layout.addWidget(self.lbl_title)

        lbl_desc = QLabel(desc)
        lbl_desc.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent; border: none;")
        layout.addWidget(lbl_desc)

    def set_active(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QFrame#SubNavItem {{
                    background-color: {ACCENT_PRIMARY};
                    border-radius: 6px;
                    border: none;
                }}
            """)
            self.lbl_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: #FFFFFF; background: transparent; border: none;")
        else:
            self.setStyleSheet(f"""
                QFrame#SubNavItem {{
                    background-color: {BG_CARD_ALT};
                    border-radius: 6px;
                    border: none;
                }}
                QFrame#SubNavItem:hover {{
                    background-color: {BG_SIDEBAR_ACTIVE};
                }}
            """)
            self.lbl_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {TEXT_PRIMARY}; background: transparent; border: none;")


class LazyViewDict(dict):
    """Dictionary that lazily loads and instantiates QWidget views on demand."""
    def __init__(self, main_window):
        super().__init__()
        self._main_window = main_window
        self._registered_keys = {
            "home", "file_exporter", "file_uploader",
            "grades", "student_removal", "field_updater", "pdf_extractor", "data_harmonizer", "learning",
            "documentation", "settings"
        }

    def __contains__(self, key):
        return key in self._registered_keys or super().__contains__(key)

    def __getitem__(self, key):
        if not super().__contains__(key) and key in self._registered_keys:
            view = self._main_window._create_view(key)
            if view is not None:
                self[key] = view
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in self._registered_keys or super().__contains__(key):
            try:
                return self[key]
            except KeyError:
                return default
        return default


# ---------------------------------------------------------------------------
# Main Application Window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    """Modern PySide6 main window with animated sidebar rail and stacked content."""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Unified System Automation Suite")
        self.resize(1340, 880)
        self.setMinimumSize(1000, 700)
        self.setStyleSheet(get_global_stylesheet())

        self._sidebar_expanded = True
        self._active_tool_id: str = ""
        self._active_sub_id: str = ""
        self._nav_items: dict[str, NavItem] = {}
        self._sub_items: dict[str, dict[str, SubNavItem]] = {}
        self._views: LazyViewDict = LazyViewDict(self)

        self._build_ui()
        self._instantiate_views()
        self.navigate_to("home")

    # ------------------------------------------------------------------
    # Layout Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central_widget")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(SIDEBAR_EXPANDED_W)
        self.sidebar.setStyleSheet(f"""
            QFrame#Sidebar {{
                background-color: {BG_SIDEBAR};
                border-right: 1px solid {BORDER_SUBTLE};
            }}
        """)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 16, 10, 12)
        sidebar_layout.setSpacing(4)

        # Brand header row
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 0, 6, 0)
        brand_row.setSpacing(10)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(1)
        self.lbl_brand = QLabel("AUTOMATION SUITE")
        self.lbl_brand.setStyleSheet(f"font-size: 13px; font-weight: 800; color: {ACCENT_PRIMARY}; letter-spacing: 1px; background: transparent; border: none;")
        self.lbl_brand_sub = QLabel("PCR Educator & Web Automation")
        self.lbl_brand_sub.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent; border: none;")
        brand_col.addWidget(self.lbl_brand)
        brand_col.addWidget(self.lbl_brand_sub)
        brand_row.addLayout(brand_col, stretch=1)

        self.btn_toggle = QPushButton("◀")
        self.btn_toggle.setFixedSize(28, 28)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD_ALT};
                color: {ACCENT_PRIMARY};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 6px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_PRIMARY};
                color: #FFFFFF;
                border-color: {ACCENT_PRIMARY};
            }}
        """)
        self.btn_toggle.clicked.connect(self.toggle_sidebar)
        brand_row.addWidget(self.btn_toggle, alignment=Qt.AlignVCenter)

        sidebar_layout.addLayout(brand_row)

        # Thin divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background-color: {BORDER_SUBTLE}; border: none;")
        sidebar_layout.addWidget(div)
        sidebar_layout.addSpacing(8)

        # MODULES label
        self.lbl_modules = QLabel("MODULES")
        self.lbl_modules.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none; padding-left: 6px;")
        sidebar_layout.addWidget(self.lbl_modules)
        sidebar_layout.addSpacing(4)

        # Navigation Items
        nav_def = [
            {
                "id": "home",
                "icon": "🏠",
                "title": "Home",
                "subtitle": "Overview & KPIs",
            },
            {
                "id": "files",
                "icon": "📁",
                "title": "Files",
                "subtitle": "Export or upload student files",
                "sub_features": [
                    {"id": "file_exporter", "icon": "📥", "title": "File Exporter", "desc": "Scrape & export files from PCR"},
                    {"id": "file_uploader", "icon": "📤", "title": "File Uploader", "desc": "Upload files into PCR folders"},
                ],
            },
            {
                "id": "grades",
                "icon": "🎓",
                "title": "Grades",
                "subtitle": "Submit or un-submit final grades",
            },
            {
                "id": "student_removal",
                "icon": "🗑️",
                "title": "Student Removal",
                "subtitle": "Remove students from class & save",
            },
            {
                "id": "field_updater",
                "icon": "✏️",
                "title": "Field Updater",
                "subtitle": "Bulk-update page fields from a CSV",
            },
            {
                "id": "pdf_extractor",
                "icon": "🧾",
                "title": "PDF → Excel Extractor",
                "subtitle": "Batch-convert PDFs to workbook",
            },
            {
                "id": "data_harmonizer",
                "icon": "🔄",
                "title": "Data Harmonizer",
                "subtitle": "Multi-dataset adaptive formatter",
            },
            {
                "id": "learning",
                "icon": "🧠",
                "title": "Learning Insights",
                "subtitle": "Run history, domain & student data",
            },
            {
                "id": "documentation",
                "icon": "📖",
                "title": "Documentation",
                "subtitle": "Guides, templates & CSV formats",
            },
            {
                "id": "settings",
                "icon": "⚙️",
                "title": "System Settings",
                "subtitle": "Configure engine & preferences",
            },
        ]

        for item_def in nav_def:
            tid = item_def["id"]
            has_sub = "sub_features" in item_def

            nav_item = NavItem(
                icon=item_def["icon"],
                title=item_def["title"],
                subtitle=item_def.get("subtitle", ""),
                tool_id=tid,
                has_sub=has_sub,
            )
            nav_item.mousePressEvent = lambda e, t=tid, hs=has_sub: self._on_nav_click(t, hs)
            sidebar_layout.addWidget(nav_item)
            self._nav_items[tid] = nav_item

            if has_sub:
                sub_container = QFrame()
                sub_container.setVisible(False)
                sub_container.setStyleSheet("background: transparent; border: none;")
                sub_layout = QVBoxLayout(sub_container)
                sub_layout.setContentsMargins(4, 2, 4, 4)
                sub_layout.setSpacing(4)

                sub_dict = {}
                for sf in item_def["sub_features"]:
                    sf_item = SubNavItem(sf["icon"], sf["title"], sf["desc"], sf["id"])
                    sf_item.mousePressEvent = lambda e, t=tid, s=sf["id"]: self._on_sub_nav_click(t, s)
                    sub_layout.addWidget(sf_item)
                    sub_dict[sf["id"]] = sf_item

                sidebar_layout.addWidget(sub_container)
                nav_item.sub_container = sub_container
                nav_item._sub_expanded = False
                self._sub_items[tid] = sub_dict

        sidebar_layout.addStretch()

        # Version footer
        lbl_version = QLabel("v2.0 — PySide6 Edition")
        lbl_version.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent; border: none; padding-left: 6px;")
        lbl_version.setAlignment(Qt.AlignLeft)
        sidebar_layout.addWidget(lbl_version)

        root.addWidget(self.sidebar)

        # ── Content Area ───────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {BG_BASE};")
        root.addWidget(self.stack, stretch=1)

    def _instantiate_views(self):
        """Pre-load default initial home view; remaining views load on-demand."""
        _ = self._views.get("home")

    def _create_view(self, view_id: str):
        """Creates a view widget and adds it to the stacked layout."""
        view_factories = {
            "home": lambda: HomeView(),
            "file_exporter": lambda: __import__("ui_qt.views.file_exporter_view", fromlist=["FileExporterView"]).FileExporterView(),
            "file_uploader": lambda: __import__("ui_qt.views.file_uploader_view", fromlist=["FileUploaderView"]).FileUploaderView(),
            "grades": lambda: __import__("ui_qt.views.grade_view", fromlist=["GradeView"]).GradeView(),
            "student_removal": lambda: __import__("ui_qt.views.student_removal_view", fromlist=["StudentRemovalView"]).StudentRemovalView(),
            "field_updater": lambda: __import__("ui_qt.views.field_updater_view", fromlist=["FieldUpdaterView"]).FieldUpdaterView(),
            "pdf_extractor": lambda: __import__("ui_qt.views.pdf_extractor_view", fromlist=["PdfExtractorView"]).PdfExtractorView(),
            "data_harmonizer": lambda: __import__("ui_qt.views.data_harmonizer_view", fromlist=["DataHarmonizerView"]).DataHarmonizerView(),
            "learning": lambda: __import__("ui_qt.views.learning_view", fromlist=["LearningView"]).LearningView(),
            "documentation": lambda: __import__("ui_qt.views.documentation_view", fromlist=["DocumentationView"]).DocumentationView(),
            "settings": lambda: __import__("ui_qt.views.settings_view", fromlist=["SettingsView"]).SettingsView(),
        }

        factory = view_factories.get(view_id)
        if not factory:
            return None

        try:
            view = factory()
            self.stack.addWidget(view)
            return view
        except Exception as e:
            print(f"[ERROR] Failed to load view '{view_id}': {e}", file=sys.stderr)
            return None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _on_nav_click(self, tool_id: str, has_sub: bool):
        if has_sub:
            self._toggle_accordion(tool_id)
        else:
            self.navigate_to(tool_id)

    def _on_sub_nav_click(self, parent_id: str, sub_id: str):
        # Deactivate all parent nav items
        for tid, nav in self._nav_items.items():
            nav.set_active(tid == parent_id)

        # Deactivate all sub-items
        for tid, sub_dict in self._sub_items.items():
            for sid, sub_item in sub_dict.items():
                sub_item.set_active(tid == parent_id and sid == sub_id)

        self._active_tool_id = parent_id
        self._active_sub_id = sub_id

        view = self._views.get(sub_id) or self._views.get(parent_id)
        if view:
            self.stack.setCurrentWidget(view)
            if hasattr(view, "set_sub_feature"):
                view.set_sub_feature(sub_id)

    def navigate_to(self, tool_id: str):
        """Switch the main content area to the specified view."""
        self._active_tool_id = tool_id
        self._active_sub_id = ""

        # Update nav item styling
        for tid, nav in self._nav_items.items():
            nav.set_active(tid == tool_id)

        # Clear all sub-item highlights
        for sub_dict in self._sub_items.values():
            for sub_item in sub_dict.values():
                sub_item.set_active(False)

        view = self._views.get(tool_id)
        if view:
            self.stack.setCurrentWidget(view)
            if hasattr(view, "refresh_kpis"):
                view.refresh_kpis()

    def _toggle_accordion(self, tool_id: str):
        nav_item = self._nav_items.get(tool_id)
        if not nav_item or not nav_item.sub_container:
            return

        if not self._sidebar_expanded:
            self.toggle_sidebar()

        is_open = nav_item._sub_expanded
        nav_item._sub_expanded = not is_open
        nav_item.sub_container.setVisible(not is_open)

        if nav_item.lbl_arrow:
            nav_item.lbl_arrow.setText("∨" if not is_open else "›")

    # ------------------------------------------------------------------
    # Sidebar Toggle (with smooth animation)
    # ------------------------------------------------------------------
    def toggle_sidebar(self):
        self._sidebar_expanded = not self._sidebar_expanded
        target_w = SIDEBAR_EXPANDED_W if self._sidebar_expanded else SIDEBAR_COLLAPSED_W

        self._anim = QPropertyAnimation(self.sidebar, b"minimumWidth")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.InOutQuart)
        self._anim.setStartValue(self.sidebar.width())
        self._anim.setEndValue(target_w)
        self._anim.start()

        self._anim2 = QPropertyAnimation(self.sidebar, b"maximumWidth")
        self._anim2.setDuration(220)
        self._anim2.setEasingCurve(QEasingCurve.InOutQuart)
        self._anim2.setStartValue(self.sidebar.width())
        self._anim2.setEndValue(target_w)
        self._anim2.start()

        # Toggle text labels visibility
        text_visible = self._sidebar_expanded
        self.lbl_brand.setVisible(text_visible)
        self.lbl_brand_sub.setVisible(text_visible)
        self.lbl_modules.setVisible(text_visible)
        self.btn_toggle.setText("◀" if text_visible else "▶")

        for nav_item in self._nav_items.values():
            nav_item.set_text_visible(text_visible)
