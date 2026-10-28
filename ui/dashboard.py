"""
ui/dashboard.py
===============
Main dashboard window featuring dark modern aesthetics, a collapsible/expandable
left navigation sidebar, vertical accordion expansion for active module instructions,
dedicated Home view with live Adaptive Learning KPIs, and sub-feature navigation.
"""

import tkinter as tk
from ui.theme import (
    BG_MAIN, BG_SIDEBAR, BG_CARD, BG_CARD_ALT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD, FONT_SMALL, FONT_SMALL_BOLD
)
from ui.home_ui import HomeUI
from ui.file_exporter_ui import FileExporterUI
from ui.file_uploader_ui import FileUploaderUI
from ui.grade_ui import GradeUI
from ui.pdf_extractor_ui import PdfExtractorUI
from ui.documentation_ui import DocumentationUI
from ui.settings_ui import SettingsUI
from ui.learning_ui import LearningUI
from ui.field_updater_ui import FieldUpdaterUI
from ui.student_removal_ui import StudentRemovalUI

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "id": "home",
        "title": "Home",
        "subtitle": "Overview & learning KPIs",
        "icon": "🏠",
        "class": HomeUI,
    },
    {
        "id": "files",
        "title": "Files",
        "subtitle": "Export or upload student files",
        "icon": "📁",
        "sub_features": [
            {
                "id": "file_exporter",
                "title": "File Exporter",
                "icon": "📥",
                "desc": "Scrape & export files from PCR",
                "class": FileExporterUI,
            },
            {
                "id": "file_uploader",
                "title": "File Uploader",
                "icon": "📤",
                "desc": "Upload files into PCR folders",
                "class": FileUploaderUI,
            },
        ],
    },
    {
        "id": "grades",
        "title": "Grades",
        "subtitle": "Submit or un-submit final grades",
        "icon": "🎓",
        "sub_features": [
            {
                "id": "submit",
                "title": "Submit Grades",
                "icon": "🎓",
                "desc": "Post final grades, dates & scores",
            },
            {
                "id": "unsubmit",
                "title": "Un-submit Grades",
                "icon": "↩️",
                "desc": "Revert status (Mark 'No')",
            },
        ],
        "class": GradeUI,
    },
    {
        "id": "student_removal",
        "title": "Student Removal",
        "subtitle": "Remove students from class & save",
        "icon": "🗑️",
        "instructions": (
            "1. Select a credentials file.\n"
            "2. Choose a CSV with columns: studentId, link.\n"
            "3. Set the number of parallel workers.\n"
            "4. Click ▶ Start Automation."
        ),
        "class": StudentRemovalUI,
    },
    {
        "id": "field_updater",
        "title": "Field Updater",
        "subtitle": "Bulk-update page fields from a CSV",
        "icon": "✏️",
        "instructions": (
            "1. Select a credentials file.\n"
            "2. Choose a CSV with columns: link, field_1, value_1, field_2, value_2 …\n"
            "3. Set the number of parallel workers.\n"
            "4. Click ▶ Start Automation."
        ),
        "class": FieldUpdaterUI,
    },
    {
        "id": "pdf_extractor",
        "title": "PDF → Excel Extractor",
        "subtitle": "Batch-convert a folder of PDFs into one workbook",
        "icon": "🧾",
        "instructions": (
            "1. Select the folder containing the PDFs to process.\n"
            "2. Choose an output .xlsx file location.\n"
            "3. Load a config preset or edit the JSON extraction config directly.\n"
            "4. Click ▶ Start Automation."
        ),
        "class": PdfExtractorUI,
    },
    {
        "id": "documentation",
        "title": "Documentation",
        "subtitle": "Operating guide, file formats & templates",
        "icon": "📖",
        "instructions": (
            "Read technical guide, copy credentials template, and inspect CSV column requirements."
        ),
        "class": DocumentationUI,
    },
    {
        "id": "settings",
        "title": "System Settings",
        "subtitle": "Configure browser engine & preferences",
        "icon": "⚙️",
        "instructions": (
            "Set headless mode, DevTools, timeouts, retries, and default save locations."
        ),
        "class": SettingsUI,
    },
    {
        "id": "learning",
        "title": "Learning Insights",
        "subtitle": "View what the system has learned",
        "icon": "🧠",
        "instructions": (
            "Browse run history, XPath patterns, latency profiles, and per-student insights "
            "the system has accumulated. Click Refresh to update after a run."
        ),
        "class": LearningUI,
    },
]


class UnifiedDashboardApp(tk.Tk):
    """Main Application Window with Collapsible Navigation Sidebar."""

    def __init__(self):
        super().__init__()

        from ui.theme import apply_theme, BG_MAIN
        apply_theme()

        self.title("Unified System Automation Suite")
        self.geometry("1200x840")
        self.minsize(1000, 700)
        self.configure(bg=BG_MAIN)

        self.sidebar_expanded    = True
        self.active_tool_id      = None
        self.active_frame        = None
        self.active_sub_features = {}
        self.expanded_parents    = {}
        self.tool_buttons        = {}
        self.tool_frames         = {}

        self._build_layout()
        self.select_tool("home")

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Sidebar
        self.sidebar = tk.Frame(self, bg=BG_SIDEBAR, width=280)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Header row
        self.sidebar_header = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        self.sidebar_header.pack(fill="x", padx=12, pady=(15, 10))

        self.btn_toggle_sidebar = tk.Button(
            self.sidebar_header, text="◀", font=("Segoe UI", 12, "bold"),
            fg=ACCENT_PRIMARY, bg=BG_CARD_ALT, activebackground=BG_CARD,
            activeforeground="white", bd=0, padx=8, pady=4, cursor="hand2",
            command=self.toggle_sidebar
        )
        self.btn_toggle_sidebar.pack(side="right")

        self.brand_title = tk.Label(
            self.sidebar_header, text="AUTOMATION SUITE", font=FONT_TITLE,
            fg=ACCENT_PRIMARY, bg=BG_SIDEBAR, anchor="w"
        )
        self.brand_title.pack(side="left", fill="x", expand=True)

        self.brand_sub = tk.Label(
            self.sidebar, text="PCR Educator & Web Automation", font=FONT_SMALL,
            fg=TEXT_MUTED, bg=BG_SIDEBAR, anchor="w"
        )
        self.brand_sub.pack(fill="x", padx=15, pady=(0, 10))

        self.divider = tk.Frame(self.sidebar, bg=BG_CARD_ALT, height=1)
        self.divider.pack(fill="x", padx=12, pady=(0, 10))

        self.nav_label = tk.Label(
            self.sidebar, text="AVAILABLE MODULES", font=FONT_SMALL,
            fg=TEXT_MUTED, bg=BG_SIDEBAR, anchor="w"
        )
        self.nav_label.pack(fill="x", padx=15, pady=(0, 6))

        self.nav_scroll_frame = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        self.nav_scroll_frame.pack(fill="both", expand=True)

        # Tool nav items
        for tool in TOOLS:
            tool_id = tool["id"]
            has_sub = "sub_features" in tool

            btn_frame = tk.Frame(self.nav_scroll_frame, bg=BG_SIDEBAR, cursor="hand2", bd=0)
            btn_frame.pack(fill="x", padx=8, pady=3)

            lbl_icon = tk.Label(
                btn_frame, text=tool["icon"], font=("Segoe UI Emoji", 14),
                fg=TEXT_PRIMARY, bg=BG_SIDEBAR
            )
            lbl_icon.pack(side="left", padx=(10, 8), pady=8, anchor="n")

            txt_container = tk.Frame(btn_frame, bg=BG_SIDEBAR)
            txt_container.pack(side="left", fill="both", expand=True, pady=6)

            title_row = tk.Frame(txt_container, bg=BG_SIDEBAR)
            title_row.pack(fill="x")

            lbl_title = tk.Label(
                title_row, text=tool["title"], font=FONT_BODY_BOLD,
                fg=TEXT_PRIMARY, bg=BG_SIDEBAR, anchor="w"
            )
            lbl_title.pack(side="left")

            lbl_arrow = None
            if has_sub:
                lbl_arrow = tk.Label(
                    title_row, text="▸", font=FONT_SMALL_BOLD,
                    fg=TEXT_MUTED, bg=BG_SIDEBAR
                )
                lbl_arrow.pack(side="right", padx=(0, 6))

            lbl_sub = tk.Label(
                txt_container, text=tool["subtitle"], font=FONT_SMALL,
                fg=TEXT_SECONDARY, bg=BG_SIDEBAR, anchor="w"
            )
            lbl_sub.pack(fill="x")

            sub_buttons      = {}
            sub_container    = None
            lbl_instructions = None

            if has_sub:
                sub_container = tk.Frame(txt_container, bg=BG_CARD, bd=0)
                for sf in tool["sub_features"]:
                    sf_id  = sf["id"]
                    sf_btn = tk.Frame(sub_container, bg=BG_CARD_ALT, cursor="hand2", padx=8, pady=6)
                    sf_btn.pack(fill="x", pady=2)

                    sf_title = tk.Label(
                        sf_btn, text=f"{sf['icon']}  {sf['title']}",
                        font=FONT_SMALL_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD_ALT, anchor="w"
                    )
                    sf_title.pack(fill="x")

                    sf_desc = tk.Label(
                        sf_btn, text=sf["desc"],
                        font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD_ALT, anchor="w"
                    )
                    sf_desc.pack(fill="x")

                    for w in (sf_btn, sf_title, sf_desc):
                        w.bind("<Button-1>", lambda e, tid=tool_id, sid=sf_id:
                               self.select_sub_feature(tid, sid))

                    sub_buttons[sf_id] = {
                        "frame": sf_btn,
                        "title": sf_title,
                        "desc":  sf_desc,
                    }

                # Parent tool header click ONLY toggles the accordion in the left panel
                # It does NOT navigate or change the active dashboard frame
                parent_header_widgets = [btn_frame, lbl_icon, txt_container, title_row, lbl_title, lbl_sub]
                if lbl_arrow:
                    parent_header_widgets.append(lbl_arrow)
                for widget in parent_header_widgets:
                    widget.bind("<Button-1>", lambda e, tid=tool_id: self.toggle_parent_accordion(tid))
            else:
                lbl_instructions = tk.Label(
                    txt_container, text=tool.get("instructions", ""), font=FONT_SMALL,
                    fg=TEXT_MUTED, bg=BG_CARD_ALT, anchor="w", justify="left",
                    wraplength=190, padx=10, pady=8, bd=0
                )
                standalone_widgets = [btn_frame, lbl_icon, txt_container, title_row, lbl_title, lbl_sub]
                for widget in standalone_widgets:
                    widget.bind("<Button-1>", lambda e, tid=tool_id: self.select_tool(tid))

            self.tool_buttons[tool_id] = {
                "frame":            btn_frame,
                "widgets":          [btn_frame, lbl_icon, txt_container, title_row, lbl_title, lbl_sub] + ([lbl_arrow] if lbl_arrow else []),
                "txt_container":    txt_container,
                "title_row":        title_row,
                "lbl_title":        lbl_title,
                "lbl_sub":          lbl_sub,
                "lbl_icon":         lbl_icon,
                "lbl_arrow":        lbl_arrow,
                "lbl_instructions": lbl_instructions,
                "sub_container":    sub_container,
                "sub_buttons":      sub_buttons,
            }

        # Content area
        self.content_area = tk.Frame(self, bg=BG_MAIN)
        self.content_area.pack(side="right", fill="both", expand=True)

        # Instantiate all module frames
        for tool in TOOLS:
            if "class" in tool:
                self.tool_frames[tool["id"]] = tool["class"](self.content_area)
            if "sub_features" in tool:
                first_sf = tool["sub_features"][0]["id"]
                self.active_sub_features[tool["id"]] = first_sf
                for sf in tool["sub_features"]:
                    if "class" in sf:
                        self.tool_frames[sf["id"]] = sf["class"](self.content_area)

    # ── Sidebar toggle ─────────────────────────────────────────────────────────

    def toggle_sidebar(self):
        """Instantly collapse or expand the left sidebar."""
        self.sidebar_expanded = not self.sidebar_expanded
        if self.sidebar_expanded:
            self.sidebar.config(width=280)
            self.btn_toggle_sidebar.config(text="◀")
            self.brand_title.pack(side="left", fill="x", expand=True)
            self.brand_sub.pack(fill="x", padx=15, pady=(0, 10))
            self.nav_label.pack(fill="x", padx=15, pady=(0, 6))
            for btn_data in self.tool_buttons.values():
                btn_data["txt_container"].pack(side="left", fill="both", expand=True, pady=6)
            # Re-expose expanded parent accordions
            for tid, is_open in self.expanded_parents.items():
                if is_open:
                    bdata = self.tool_buttons.get(tid)
                    if bdata and bdata["sub_container"] and not bdata["sub_container"].winfo_ismapped():
                        bdata["sub_container"].pack(fill="x", pady=(6, 2))
            # Re-expose active standalone tool instructions
            if self.active_tool_id:
                btn = self.tool_buttons.get(self.active_tool_id)
                if btn and btn["lbl_instructions"] and not btn["lbl_instructions"].winfo_ismapped():
                    btn["lbl_instructions"].pack(fill="x", pady=(6, 2))
        else:
            self.sidebar.config(width=60)
            self.btn_toggle_sidebar.config(text="▶")
            self.brand_title.pack_forget()
            self.brand_sub.pack_forget()
            self.nav_label.pack_forget()
            for btn_data in self.tool_buttons.values():
                btn_data["txt_container"].pack_forget()

    # ── Content frame switch ───────────────────────────────────────────────────

    def _show_frame(self, new_frame):
        """Instantly swap the visible content frame."""
        if self.active_frame is new_frame:
            return
        if self.active_frame:
            self.active_frame.pack_forget()
        self.active_frame = new_frame
        self.active_frame.pack(fill="both", expand=True)

    # ── Navigation logic ───────────────────────────────────────────────────────

    def toggle_parent_accordion(self, tool_id: str):
        """
        Toggle sub-feature accordion in sidebar without changing the main dashboard view.
        The dashboard remains on whichever view was already active.
        """
        if not self.sidebar_expanded:
            self.toggle_sidebar()

        btn_data = self.tool_buttons.get(tool_id)
        if not btn_data or not btn_data["sub_container"]:
            return

        is_open = self.expanded_parents.get(tool_id, False)
        if is_open:
            btn_data["sub_container"].pack_forget()
            self.expanded_parents[tool_id] = False
            if btn_data.get("lbl_arrow"):
                btn_data["lbl_arrow"].config(text="▸")
        else:
            btn_data["sub_container"].config(bg=BG_CARD)
            btn_data["sub_container"].pack(fill="x", pady=(6, 2))
            self.expanded_parents[tool_id] = True
            if btn_data.get("lbl_arrow"):
                btn_data["lbl_arrow"].config(text="▾")

    def select_sub_feature(self, tool_id: str, sub_feature_id: str):
        """
        Handle clicking directly on a sub-feature in the left navigation sidebar.
        This switches the active dashboard frame to the requested sub-feature.
        """
        # Ensure parent accordion is open
        btn_data = self.tool_buttons.get(tool_id)
        if btn_data and btn_data["sub_container"]:
            if not self.expanded_parents.get(tool_id, False):
                btn_data["sub_container"].config(bg=BG_CARD)
                btn_data["sub_container"].pack(fill="x", pady=(6, 2))
                self.expanded_parents[tool_id] = True
                if btn_data.get("lbl_arrow"):
                    btn_data["lbl_arrow"].config(text="▾")

        self.active_tool_id = tool_id
        self.active_sub_features[tool_id] = sub_feature_id

        # Update sidebar styling: highlight active parent and sub-feature
        for tid, bdata in self.tool_buttons.items():
            if tid == tool_id:
                bdata["frame"].config(bg=BG_CARD)
                for w in bdata["widgets"]:
                    w.config(bg=BG_CARD)
                bdata["lbl_title"].config(fg=ACCENT_PRIMARY)
            else:
                bdata["frame"].config(bg=BG_SIDEBAR)
                for w in bdata["widgets"]:
                    w.config(bg=BG_SIDEBAR)
                bdata["lbl_title"].config(fg=TEXT_PRIMARY)
                if bdata["lbl_instructions"]:
                    bdata["lbl_instructions"].pack_forget()

        self._highlight_sub_feature(tool_id, sub_feature_id)

        # Show target content frame
        target_frame = None
        if sub_feature_id in self.tool_frames:
            target_frame = self.tool_frames[sub_feature_id]
        elif tool_id in self.tool_frames:
            target_frame = self.tool_frames[tool_id]
            if hasattr(target_frame, "set_sub_feature"):
                target_frame.set_sub_feature(sub_feature_id)

        if target_frame:
            self._show_frame(target_frame)

    def select_tool(self, tool_id: str):
        """Handle clicking standalone modules (Home, PDF Extractor, Documentation, Settings, etc.)."""
        btn_data = self.tool_buttons.get(tool_id)
        # If tool has sub-features, clicking the parent toggles the accordion only
        if btn_data and btn_data.get("sub_buttons"):
            self.toggle_parent_accordion(tool_id)
            return

        if self.active_tool_id == tool_id:
            # Re-refresh if Home is re-clicked
            if tool_id == "home" and "home" in self.tool_frames:
                self.tool_frames["home"].refresh()
            return

        self.active_tool_id = tool_id

        # Update sidebar styling
        for tid, bdata in self.tool_buttons.items():
            if tid == tool_id:
                bdata["frame"].config(bg=BG_CARD)
                for w in bdata["widgets"]:
                    w.config(bg=BG_CARD)
                bdata["lbl_title"].config(fg=ACCENT_PRIMARY)
                if bdata["lbl_instructions"]:
                    bdata["lbl_instructions"].pack(fill="x", pady=(6, 2))
            else:
                bdata["frame"].config(bg=BG_SIDEBAR)
                for w in bdata["widgets"]:
                    w.config(bg=BG_SIDEBAR)
                bdata["lbl_title"].config(fg=TEXT_PRIMARY)
                if bdata["lbl_instructions"]:
                    bdata["lbl_instructions"].pack_forget()

        # Un-highlight all sub-features because active tool is now a standalone module
        for tid, bdata in self.tool_buttons.items():
            if bdata.get("sub_buttons"):
                for sf_info in bdata["sub_buttons"].values():
                    sf_info["frame"].config(bg=BG_CARD_ALT)
                    sf_info["title"].config(bg=BG_CARD_ALT, fg=TEXT_PRIMARY)
                    sf_info["desc"].config(bg=BG_CARD_ALT, fg=TEXT_MUTED)

        target_frame = self.tool_frames.get(tool_id)
        if target_frame:
            self._show_frame(target_frame)
            if hasattr(target_frame, "refresh"):
                target_frame.refresh()

    def _highlight_sub_feature(self, tool_id: str, active_sf: str):
        """Update active/inactive styling for sub-features in the left sidebar."""
        for tid, bdata in self.tool_buttons.items():
            if bdata.get("sub_buttons"):
                for sf_id, sf_info in bdata["sub_buttons"].items():
                    if tid == tool_id and sf_id == active_sf:
                        sf_info["frame"].config(bg=ACCENT_PRIMARY)
                        sf_info["title"].config(bg=ACCENT_PRIMARY, fg="white")
                        sf_info["desc"].config(bg=ACCENT_PRIMARY, fg="#e0e7ff")
                    else:
                        sf_info["frame"].config(bg=BG_CARD_ALT)
                        sf_info["title"].config(bg=BG_CARD_ALT, fg=TEXT_PRIMARY)
                        sf_info["desc"].config(bg=BG_CARD_ALT, fg=TEXT_MUTED)
