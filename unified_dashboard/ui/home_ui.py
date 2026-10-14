"""
ui/home_ui.py
=============
Home Dashboard Overview Tab.

Displays ONLY the Key Performance Indicators (KPIs) from Adaptive Learning Insights:
  - Total Runs
  - Records Processed
  - Files Handled
  - Domains Learned
  - XPath Patterns
  - Student Insights
"""

import tkinter as tk
from core.learning_system import learning_engine
from ui.theme import (
    BG_MAIN, BG_CARD, BG_CARD_ALT, BORDER_COLOR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD,
    FONT_SMALL, FONT_SMALL_BOLD
)


class HomeUI(tk.Frame):
    """Home view — displays exclusively the KPIs from Adaptive Learning Insights."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG_MAIN)
        self._kpi_labels = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # --- Top Header ---
        header = tk.Frame(self, bg=BG_MAIN)
        header.pack(fill="x", padx=28, pady=(24, 16))

        title_row = tk.Frame(header, bg=BG_MAIN)
        title_row.pack(fill="x")

        lbl_title = tk.Label(
            title_row,
            text="🏠  Home — Adaptive Learning Telemetry",
            font=FONT_TITLE,
            fg=TEXT_PRIMARY,
            bg=BG_MAIN,
            anchor="w"
        )
        lbl_title.pack(side="left")

        btn_refresh = tk.Button(
            title_row,
            text="🔄  Refresh KPIs",
            font=FONT_BODY_BOLD,
            bg=BG_CARD_ALT,
            fg=TEXT_PRIMARY,
            activebackground=ACCENT_PRIMARY,
            activeforeground="white",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.refresh
        )
        btn_refresh.pack(side="right")

        lbl_sub = tk.Label(
            header,
            text="Real-time Key Performance Indicators (KPIs) learned and accumulated by the adaptive automation engine across all runs.",
            font=FONT_BODY,
            fg=TEXT_SECONDARY,
            bg=BG_MAIN,
            anchor="w",
            wraplength=950
        )
        lbl_sub.pack(fill="x", pady=(6, 0))

        # --- KPI Cards Grid Container ---
        grid_container = tk.Frame(self, bg=BG_MAIN)
        grid_container.pack(fill="both", expand=True, padx=28, pady=(8, 20))

        # 2 rows x 3 columns
        grid_container.columnconfigure(0, weight=1, uniform="kpi")
        grid_container.columnconfigure(1, weight=1, uniform="kpi")
        grid_container.columnconfigure(2, weight=1, uniform="kpi")
        grid_container.rowconfigure(0, weight=1, uniform="kpi")
        grid_container.rowconfigure(1, weight=1, uniform="kpi")

        kpi_specs = [
            # row 0
            (0, 0, "total_runs",            "Total Runs",        "🔄", ACCENT_PRIMARY, "Completed automation sessions"),
            (0, 1, "total_items_processed", "Records Processed", "📋", ACCENT_SUCCESS, "Total student & ledger records completed"),
            (0, 2, "total_files_handled",   "Files Handled",     "📁", ACCENT_WARNING, "Files exported and uploaded to PCR"),
            # row 1
            (1, 0, "domain_count",          "Domains Learned",   "🌐", "#a78bfa",      "Unique portal domains mapped & memorized"),
            (1, 1, "learned_patterns",      "XPath Patterns",    "🔍", "#38bdf8",      "Optimized UI selectors & adaptive fallbacks"),
            (1, 2, "learned_students",      "Student Insights",  "👤", "#fb923c",      "Unique student IDs with cached properties"),
        ]

        for row, col, key, title, icon, color, desc in kpi_specs:
            card = self._create_kpi_card(grid_container, icon, title, desc, color)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            self._kpi_labels[key] = card._val_label

        # --- Bottom Status Bar ---
        footer = tk.Frame(self, bg=BG_CARD_ALT, bd=1, relief="solid")
        footer.pack(fill="x", padx=28, pady=(0, 20))

        lbl_footer = tk.Label(
            footer,
            text="💡 Telemetry updates automatically after each execution. Select a sub-feature from the left panel to run automations.",
            font=FONT_SMALL,
            fg=TEXT_MUTED,
            bg=BG_CARD_ALT,
            padx=12,
            pady=8,
            anchor="w"
        )
        lbl_footer.pack(fill="x")

    def _create_kpi_card(self, parent, icon: str, title: str, desc: str, accent_color: str) -> tk.Frame:
        """Create a modern, high-contrast KPI card."""
        card = tk.Frame(parent, bg=BG_CARD, bd=1, relief="solid", padx=20, pady=18)

        # Header row inside card: Icon and Title
        top_row = tk.Frame(card, bg=BG_CARD)
        top_row.pack(fill="x")

        lbl_icon = tk.Label(
            top_row, text=icon, font=("Segoe UI Emoji", 20),
            fg=accent_color, bg=BG_CARD
        )
        lbl_icon.pack(side="left")

        lbl_title = tk.Label(
            top_row, text=title, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD
        )
        lbl_title.pack(side="left", padx=(10, 0))

        # Main Big Value
        val_label = tk.Label(
            card, text="—", font=("Segoe UI", 32, "bold"),
            fg=accent_color, bg=BG_CARD, anchor="w"
        )
        val_label.pack(fill="x", pady=(12, 4))
        card._val_label = val_label

        # Subtitle / Description
        lbl_desc = tk.Label(
            card, text=desc, font=FONT_SMALL,
            fg=TEXT_MUTED, bg=BG_CARD, anchor="w", justify="left"
        )
        lbl_desc.pack(fill="x")

        return card

    def refresh(self):
        """Fetch latest statistics from learning_engine and update KPI values."""
        try:
            stats = learning_engine.get_summary_stats()
            mapping = {
                "total_runs":            f"{stats.get('total_runs', 0):,}",
                "total_items_processed": f"{stats.get('total_items_processed', 0):,}",
                "total_files_handled":   f"{stats.get('total_files_handled', 0):,}",
                "domain_count":          f"{stats.get('domain_count', 0):,}",
                "learned_patterns":      f"{stats.get('learned_patterns', 0):,}",
                "learned_students":      f"{stats.get('learned_students', 0):,}",
            }
            for key, val_str in mapping.items():
                lbl = self._kpi_labels.get(key)
                if lbl and lbl.winfo_exists():
                    lbl.config(text=val_str)
        except Exception:
            pass
