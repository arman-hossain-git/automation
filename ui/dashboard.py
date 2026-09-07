"""
ui/dashboard.py
===============
Main dashboard window featuring dark modern aesthetics, a collapsible/expandable
left navigation sidebar, vertical accordion expansion for active module instructions,
and integrated Documentation & Settings views.
"""

import tkinter as tk
from ui.theme import (
    BG_MAIN, BG_SIDEBAR, BG_CARD, BG_CARD_ALT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT_PRIMARY, ACCENT_PRIMARY_HOVER,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD, FONT_SMALL
)
from ui.file_exporter_ui import FileExporterUI
from ui.file_uploader_ui import FileUploaderUI
from ui.grade_ui import GradeUI
from ui.pdf_extractor_ui import PdfExtractorUI
from ui.documentation_ui import DocumentationUI
from ui.settings_ui import SettingsUI
from ui.learning_ui import LearningUI

TOOLS = [
    {
        "id": "file_exporter",
        "title": "File Exporter",
        "subtitle": "Scrape & export files from PCR or web",
        "icon": "📥",
        "instructions": (
            "1. Select Credentials (.txt) and Record Links CSV.\n"
            "2. Pick Save Files Directory.\n"
            "3. Choose an Exporter Preset or enter custom segment names.\n"
            "4. Click ▶ Start Automation."
        ),
        "class": FileExporterUI,
    },
    {
        "id": "file_uploader",
        "title": "File Uploader",
        "subtitle": "Upload files into PCR Files & Documents",
        "icon": "📤",
        "instructions": (
            "1. Select Credentials (.txt) and Student Links CSV.\n"
            "2. Select Source Directory containing student ID folders.\n"
            "3. Choose Upload Target Folder (Root or Subfolder).\n"
            "4. Click ▶ Start Automation."
        ),
        "class": FileUploaderUI,
    },
    {
        "id": "grade_submission",
        "title": "Grade Submission",
        "subtitle": "Combined final grade & score submission",
        "icon": "🎓",
        "instructions": (
            "1. Select Credentials (.txt) and Grade Links CSV.\n"
            "2. Verify CSV includes finalGradeReceivedDate and score.\n"
            "3. Click ▶ Start Automation to post grades & scores."
        ),
        "class": GradeUI,
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

        self.sidebar_expanded = True
        self.active_tool_id = None
        self.tool_buttons = {}
        self.tool_frames = {}

        self._build_layout()
        self.select_tool(TOOLS[0]["id"])

    def _build_layout(self):
        # Sidebar container
        self.sidebar = tk.Frame(self, bg=BG_SIDEBAR, width=280)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Sidebar Header with Toggle / Collapse Button
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

        # Divider
        self.divider = tk.Frame(self.sidebar, bg=BG_CARD_ALT, height=1)
        self.divider.pack(fill="x", padx=12, pady=(0, 10))

        # Navigation Section Label
        self.nav_label = tk.Label(
            self.sidebar, text="AVAILABLE MODULES", font=FONT_SMALL,
            fg=TEXT_MUTED, bg=BG_SIDEBAR, anchor="w"
        )
        self.nav_label.pack(fill="x", padx=15, pady=(0, 6))

        # Navigation Scroll Area for Tool items
        self.nav_scroll_frame = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        self.nav_scroll_frame.pack(fill="both", expand=True)

        # Build Accordion Tool Buttons
        for tool in TOOLS:
            btn_frame = tk.Frame(self.nav_scroll_frame, bg=BG_SIDEBAR, cursor="hand2", bd=0)
            btn_frame.pack(fill="x", padx=8, pady=3)

            # Icon label
            lbl_icon = tk.Label(
                btn_frame, text=tool["icon"], font=("Segoe UI Emoji", 14),
                fg=TEXT_PRIMARY, bg=BG_SIDEBAR
            )
            lbl_icon.pack(side="left", padx=(10, 8), pady=8, anchor="n")

            # Text container
            txt_container = tk.Frame(btn_frame, bg=BG_SIDEBAR)
            txt_container.pack(side="left", fill="both", expand=True, pady=6)

            lbl_title = tk.Label(
                txt_container, text=tool["title"], font=FONT_BODY_BOLD,
                fg=TEXT_PRIMARY, bg=BG_SIDEBAR, anchor="w"
            )
            lbl_title.pack(fill="x")

            lbl_sub = tk.Label(
                txt_container, text=tool["subtitle"], font=FONT_SMALL,
                fg=TEXT_SECONDARY, bg=BG_SIDEBAR, anchor="w"
            )
            lbl_sub.pack(fill="x")

            # Expandable Vertical Instruction Box (accordion detail)
            lbl_instructions = tk.Label(
                txt_container, text=tool["instructions"], font=FONT_SMALL,
                fg=TEXT_MUTED, bg=BG_CARD_ALT, anchor="w", justify="left",
                wraplength=190, padx=10, pady=8, bd=0
            )

            tool_id = tool["id"]
            # Bind click events across all container child widgets
            for widget in (btn_frame, lbl_icon, txt_container, lbl_title, lbl_sub):
                widget.bind("<Button-1>", lambda e, tid=tool_id: self.select_tool(tid))

            self.tool_buttons[tool_id] = {
                "frame": btn_frame,
                "widgets": [btn_frame, lbl_icon, txt_container, lbl_title, lbl_sub],
                "txt_container": txt_container,
                "lbl_title": lbl_title,
                "lbl_sub": lbl_sub,
                "lbl_icon": lbl_icon,
                "lbl_instructions": lbl_instructions,
            }

        # Content Area
        self.content_area = tk.Frame(self, bg=BG_MAIN)
        self.content_area.pack(side="right", fill="both", expand=True)

        # Instantiate all module frames
        for tool in TOOLS:
            frame_cls = tool["class"]
            frame_instance = frame_cls(self.content_area)
            self.tool_frames[tool["id"]] = frame_instance

    def toggle_sidebar(self):
        """Collapse or expand the left sidebar with smooth sliding animation."""
        self.sidebar_expanded = not self.sidebar_expanded
        target_w = 280 if self.sidebar_expanded else 60
        self._animate_sidebar_slide(target_w)

    def _animate_sidebar_slide(self, target_w: int):
        cur_w = self.sidebar.winfo_width()
        diff = target_w - cur_w
        if abs(diff) <= 8:
            self.sidebar.config(width=target_w)
            self._update_sidebar_visibility()
        else:
            new_w = cur_w + int(diff * 0.45)
            self.sidebar.config(width=new_w)
            self.after(15, lambda: self._animate_sidebar_slide(target_w))

    def _update_sidebar_visibility(self):
        if self.sidebar_expanded:
            self.btn_toggle_sidebar.config(text="◀")
            self.brand_title.pack(side="left", fill="x", expand=True)
            self.brand_sub.pack(fill="x", padx=15, pady=(0, 10))
            self.nav_label.pack(fill="x", padx=15, pady=(0, 6))
            for btn_data in self.tool_buttons.values():
                btn_data["txt_container"].pack(side="left", fill="both", expand=True, pady=6)
        else:
            self.btn_toggle_sidebar.config(text="▶")
            self.brand_title.pack_forget()
            self.brand_sub.pack_forget()
            self.nav_label.pack_forget()
            for btn_data in self.tool_buttons.values():
                btn_data["txt_container"].pack_forget()

    def select_tool(self, tool_id: str):
        if self.active_tool_id == tool_id:
            return

        # Update sidebar item styling and vertical accordion expansion
        for tid, btn_data in self.tool_buttons.items():
            if tid == tool_id:
                btn_data["frame"].config(bg=BG_CARD)
                for w in btn_data["widgets"]:
                    w.config(bg=BG_CARD)
                btn_data["lbl_title"].config(fg=ACCENT_PRIMARY)
                # Expand vertical instruction box downward
                btn_data["lbl_instructions"].pack(fill="x", pady=(6, 2))
            else:
                btn_data["frame"].config(bg=BG_SIDEBAR)
                for w in btn_data["widgets"]:
                    w.config(bg=BG_SIDEBAR)
                btn_data["lbl_title"].config(fg=TEXT_PRIMARY)
                # Collapse vertical instruction box
                btn_data["lbl_instructions"].pack_forget()

        # Hide current frame, show new frame
        if self.active_tool_id and self.active_tool_id in self.tool_frames:
            self.tool_frames[self.active_tool_id].pack_forget()

        self.active_tool_id = tool_id
        selected_frame = self.tool_frames[tool_id]
        selected_frame.pack(fill="both", expand=True)
