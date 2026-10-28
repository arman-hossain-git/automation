"""
ui/pdf_extractor_ui.py
========================
UI frame for the PDF -> Excel Extractor tool.

Tabs (merged):
  1. Setup, Config & Verify - source folder, output path, preset management,
                               a sample-PDF word-selector (drag-select a word
                               or phrase and tell the system what it is: a
                               field label, a field's value, a data-row
                               column, text to exclude, etc), a Configured
                               Fields table showing the system's current
                               findings, and a collapsible raw-JSON editor
                               for advanced hand-editing.
  2. Live Activity & Logs   - KPIs + live console (inherited from
                               BaseToolFrame), extended here with an in-app
                               Debug Log tail + Execution Log CSV viewer.
"""

import os
import re
import json
import random
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from pathlib import Path

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import (
    BG_CARD, BG_CARD_ALT, BG_LOG, BG_MAIN,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, TEXT_ACCENT,
    ACCENT_INFO, ACCENT_INFO_HOVER, ACCENT_DANGER, ACCENT_DANGER_HOVER,
    ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER,
    FONT_BODY, FONT_BODY_BOLD, FONT_SMALL, FONT_SMALL_BOLD, FONT_SUBTITLE, FONT_LOG
)
from core.utils import describe_exception
from modules.pdf_extractor import (
    BatchPDFToExcelExporter, ConfigurableExtractor, DEFAULT_CONFIG,
    list_presets, load_preset, save_preset, delete_preset, ensure_configs_dir,
    build_anchored_pattern, build_row_pattern, describe_field_map, remove_field_entry,
    debug_log_path, execution_log_path, read_debug_log_tail, read_execution_log_rows,
)
from ui.pdf_review_tab import PdfReviewTab
from modules.pdf_spatial_ml import synthesize_preset_from_pdf

# Colour tags used in the line viewer to show what the current config would
# already match/exclude/split on.
_TAG_MATCHED   = "matched"
_TAG_SEPARATOR = "separator_line"
_TAG_EXCLUDED  = "excluded_line"
_TAG_SECTION   = "section_marker_line"


class PdfExtractorUI(BaseToolFrame):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="PDF \u2192 Excel Extractor",
            description=(
                "Batch-extracts structured data from a folder of same-type PDF documents "
                "into a single Excel workbook, using a fully editable extraction config."
            )
        )

        self.source_folder = ""
        self.output_xlsx = ""
        self.exporter = BatchPDFToExcelExporter()

        # Sample-PDF / word-selection state
        self._verify_pdf_path: str = ""
        self._verify_lines: list[str] = []
        self._verify_selected_line: int = -1
        self._pending_label = None          # {"text":..., "line_idx":...}
        self._row_draft = None              # {"line_idx":..., "cols":[{"name","start","end","text"}]}
        self._field_row_map = {}            # treeview iid -> (scope, index)
        self._advanced_visible = False
        self.lbl_config_summary = None

        # Multi-tab workspace setup on self.notebook
        self.tab_mapper = tk.Frame(self.notebook, bg=BG_CARD, padx=12, pady=10)
        self.tab_json = tk.Frame(self.notebook, bg=BG_CARD, padx=12, pady=10)
        self.tab_review = tk.Frame(self.notebook, bg=BG_CARD, padx=12, pady=10)

        try:
            self.notebook.tab(self.tab_config, text=" \U0001F4C1 1. Setup & Presets ")
            self.notebook.insert(1, self.tab_mapper, text=" \U0001F3AF 2. Visual Field Mapper ")
            self.notebook.insert(2, self.tab_json, text=" \U0001F6E0 3. Advanced JSON ")
            self.notebook.insert(3, self.tab_review, text=" \U0001F50D 4. Review & Self-Tune ")
            self.notebook.tab(self.tab_execution, text=" \U0001F4CA 5. Live Activity & Logs ")
        except Exception:
            pass

        ensure_configs_dir()
        self._populate_inputs()
        self._build_field_mapper_tab()
        self._build_advanced_json_tab()

        self.review_tab = PdfReviewTab(self.tab_review, self)
        self.review_tab.pack(fill="both", expand=True)

        if hasattr(self, "btn_open_log"):
            self.btn_open_log.pack_forget()
        if hasattr(self, "btn_open_output"):
            self.btn_open_output.pack_forget()

        self._load_config_into_editor(DEFAULT_CONFIG)
        self._refresh_fields_table()

    # ======================================================================
    # TOP: Setup inputs (source/output/presets) - compact, non-expanding
    # ======================================================================
    def _populate_inputs(self):
        c = self.inputs_container
        c.pack_configure(fill="x", expand=False)

        # Card 1: Paths Configuration
        paths_card = tk.LabelFrame(
            c, text=" \U0001F4C2 Source PDF Folder & Output File Locations ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=12, pady=10
        )
        paths_card.pack(fill="x", pady=(0, 10))

        self.create_file_picker_row(
            paths_card, 0, "Source Folder (PDFs to process):",
            self._on_source_folder_set,
            is_directory=True
        )

        tk.Label(
            paths_card, text="Output Excel File (.xlsx):", font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w"
        ).grid(row=1, column=0, sticky="w", pady=4, padx=(0, 10))

        self.lbl_output = tk.Label(
            paths_card, text="No path selected", font=FONT_BODY, fg=TEXT_SECONDARY,
            bg=BG_CARD, anchor="w", wraplength=450, justify="left"
        )
        self.lbl_output.grid(row=1, column=1, sticky="w", padx=10, pady=4)

        tk.Button(
            paths_card, text="Save As...", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=10, pady=2, cursor="hand2", command=self._pick_output_file
        ).grid(row=1, column=2, sticky="e", pady=4, padx=(10, 0))

        paths_card.columnconfigure(1, weight=1)

        # Card 2: Preset Management
        preset_card = tk.LabelFrame(
            c, text=" \u2699\ufe0f Extraction Config Presets ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=12, pady=10
        )
        preset_card.pack(fill="x", pady=(0, 10))

        preset_row = tk.Frame(preset_card, bg=BG_CARD)
        preset_row.pack(fill="x", pady=2)

        tk.Label(
            preset_row, text="Active Config Preset:", font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD
        ).pack(side="left", padx=(0, 10))

        self.preset_var = tk.StringVar(value="(unsaved / custom)")
        self.preset_menu = tk.OptionMenu(preset_row, self.preset_var, "(unsaved / custom)")
        self.preset_menu.config(
            font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0,
            activebackground=BG_CARD_ALT, highlightthickness=0
        )
        self.preset_menu["menu"].config(bg=BG_CARD_ALT, fg=TEXT_PRIMARY)
        self.preset_menu.pack(side="left", padx=(0, 10))
        # Refresh the preset list every time the dropdown is opened
        self.preset_menu.bind("<ButtonPress-1>", lambda e: self._refresh_preset_menu())
        self._refresh_preset_menu()

        tk.Button(
            preset_row, text="\U0001F4E5 Load", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=10, pady=2, cursor="hand2", command=self._on_load_preset
        ).pack(side="left", padx=3)

        tk.Button(
            preset_row, text="\U0001F4BE Save As...", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=10, pady=2, cursor="hand2", command=self._on_save_preset
        ).pack(side="left", padx=3)

        tk.Button(
            preset_row, text="\U0001F5D1 Delete", font=FONT_BODY, bg=ACCENT_DANGER, fg="white",
            activebackground=ACCENT_DANGER_HOVER, bd=0, padx=10, pady=2, cursor="hand2",
            command=self._on_delete_preset
        ).pack(side="left", padx=3)

        tk.Button(
            preset_row, text="\U0001F916 Auto-Detect", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            bd=0, padx=10, pady=2, cursor="hand2", command=self._on_auto_detect_preset_ml
        ).pack(side="left", padx=(8, 3))

        # Card 3: Active Config Overview & Quick Actions
        overview_card = tk.LabelFrame(
            c, text=" \U0001F4CA Active Extraction Configuration Overview ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=12, pady=10
        )
        overview_card.pack(fill="both", expand=True, pady=(0, 5))

        self.lbl_config_summary = tk.Label(
            overview_card, text="Config summary loading...",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD, justify="left", anchor="nw"
        )
        self.lbl_config_summary.pack(fill="both", expand=True, side="left")

        cta_frame = tk.Frame(overview_card, bg=BG_CARD)
        cta_frame.pack(side="right", fill="y", padx=(20, 0))

        tk.Button(
            cta_frame, text="\U0001F3AF Open Visual Field Mapper \u2192", font=FONT_BODY_BOLD,
            bg=ACCENT_INFO, fg="white", activebackground=ACCENT_INFO_HOVER,
            bd=0, padx=14, pady=8, cursor="hand2",
            command=lambda: self.notebook.select(self.tab_mapper)
        ).pack(side="bottom", pady=10)

    def _on_source_folder_set(self, path: str):
        self.source_folder = path
        self._auto_load_sample_pdf(path)
        if hasattr(self, "review_tab"):
            try:
                self.review_tab._refresh_sample_dropdown()
            except Exception:
                pass

    def _pick_output_file(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
            initialfile="extracted_data.xlsx"
        )
        if path:
            self.output_xlsx = path
            self.lbl_output.config(text=path, fg=TEXT_PRIMARY)
            self._log_dir = os.path.dirname(path) or "."
            self._refresh_logs_view()

    # ======================================================================
    # TAB 2: Visual Field Mapper & Tester
    # ======================================================================
    def _build_field_mapper_tab(self):
        body = tk.Frame(self.tab_mapper, bg=BG_CARD)
        body.pack(fill="both", expand=True)

        from ui.theme import bind_hover_effect

        # ── Sample PDF control bar ──────────────────────────────────────
        ctrl = tk.Frame(body, bg=BG_CARD)
        ctrl.pack(fill="x", pady=(0, 6))

        tk.Label(
            ctrl, text="Sample PDF:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD
        ).pack(side="left")

        self.lbl_verify_pdf = tk.Label(
            ctrl, text="No PDF loaded", font=FONT_BODY, fg=TEXT_SECONDARY,
            bg=BG_CARD, width=35, anchor="w"
        )
        self.lbl_verify_pdf.pack(side="left", padx=(8, 4))

        btn_pick = tk.Button(
            ctrl, text="📂 Pick PDF", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=8, pady=2, cursor="hand2", command=self._browse_sample_pdf
        )
        btn_pick.pack(side="left", padx=3)
        bind_hover_effect(btn_pick, BG_CARD_ALT, BG_CARD)

        btn_rnd = tk.Button(
            ctrl, text="🔀 Random Sample", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=8, pady=2, cursor="hand2", command=self._load_random_sample_pdf
        )
        btn_rnd.pack(side="left", padx=3)
        bind_hover_effect(btn_rnd, BG_CARD_ALT, BG_CARD)

        btn_run = tk.Button(
            ctrl, text="▶ Run Extraction Preview", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            bd=0, padx=12, pady=3, cursor="hand2", command=self._run_verify_extraction
        )
        btn_run.pack(side="right")
        bind_hover_effect(btn_run, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

        # ── Main split ─────────────────────────────────────────────────
        self.main_split = tk.PanedWindow(
            body, orient="horizontal", bg=BG_CARD, sashwidth=6, sashrelief="flat"
        )
        self.main_split.pack(fill="both", expand=True)

        left = tk.Frame(self.main_split, bg=BG_CARD)
        right = tk.Frame(self.main_split, bg=BG_CARD)
        self.main_split.add(left, minsize=420)
        self.main_split.add(right, minsize=380)

        # LEFT PANE: Grid with uniform height allocation (100% strictly equal 50%/50% height)
        left.grid_rowconfigure(0, weight=1, uniform="equal_left")
        left.grid_rowconfigure(1, weight=1, uniform="equal_left")
        left.grid_columnconfigure(0, weight=1)

        line_container = tk.Frame(left, bg=BG_CARD)
        line_container.grid(row=0, column=0, sticky="nsew", pady=(0, 3))

        preview_container = tk.Frame(left, bg=BG_CARD)
        preview_container.grid(row=1, column=0, sticky="nsew", pady=(3, 0))

        self._build_line_viewer(line_container)
        self._build_preview_panel(preview_container)

        # RIGHT PANE: Top = Action Center, Bottom = Rules
        self._build_action_center(right)
        self._build_rules_panel(right)

        # Status bar
        self.lbl_verify_status = tk.Label(
            body, text="Load a PDF above to begin verification.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w"
        )
        self.lbl_verify_status.pack(fill="x", pady=(4, 0))

    # ======================================================================
    # TAB 3: Advanced JSON Config Editor
    # ======================================================================
    def _build_advanced_json_tab(self):
        frame = tk.LabelFrame(
            self.tab_json, text=" 📄 Extraction Config (Raw JSON Editor) ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=12, pady=10
        )
        frame.pack(fill="both", expand=True)

        hint = tk.Label(
            frame,
            text=(
                "Directly view or modify the underlying JSON configuration rules. "
                "Changes sync automatically with the Visual Field Mapper when applied."
            ),
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w", justify="left"
        )
        hint.pack(fill="x", pady=(0, 6))

        self.txt_config = scrolledtext.ScrolledText(
            frame, height=18, wrap="none", font=FONT_LOG,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, bd=0
        )
        self.txt_config.pack(fill="both", expand=True)

        editor_btns = tk.Frame(frame, bg=BG_CARD)
        editor_btns.pack(fill="x", pady=(8, 0))

        from ui.theme import bind_hover_effect

        btn_val = tk.Button(
            editor_btns, text="✔ Validate JSON", font=FONT_BODY, bg=ACCENT_INFO, fg="white",
            activebackground=ACCENT_INFO_HOVER, bd=0, padx=12, pady=4, cursor="hand2",
            command=self._validate_config
        )
        btn_val.pack(side="left")
        bind_hover_effect(btn_val, ACCENT_INFO, ACCENT_INFO_HOVER)

        btn_rst = tk.Button(
            editor_btns, text="🔄 Reset to Blank Template", font=FONT_BODY, bg=BG_CARD_ALT,
            fg=TEXT_PRIMARY, bd=0, padx=12, pady=4, cursor="hand2",
            command=self._reset_to_blank_template
        )
        btn_rst.pack(side="left", padx=(10, 0))
        bind_hover_effect(btn_rst, BG_CARD_ALT, BG_CARD)

        btn_app = tk.Button(
            editor_btns, text="⚡ Apply JSON → Rules Table", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            bd=0, padx=12, pady=4, cursor="hand2",
            command=self._on_apply_raw_json
        )
        btn_app.pack(side="left", padx=(10, 0))
        bind_hover_effect(btn_app, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

    def _reset_to_blank_template(self):
        self._load_config_into_editor(DEFAULT_CONFIG)
        self._refresh_fields_table()
        if self._verify_lines:
            self._render_verify_lines(self._verify_lines, DEFAULT_CONFIG)

    def _on_apply_raw_json(self):
        cfg, err = self._parse_editor_json()
        if err:
            messagebox.showerror("Invalid JSON", err)
            return
        self._refresh_fields_table()
        if self._verify_lines:
            self._render_verify_lines(self._verify_lines, cfg)
        self.lbl_verify_status.config(text="Raw JSON rules applied successfully.")

    def _toggle_advanced_json(self):
        """Legacy helper: navigates directly to the Advanced JSON tab."""
        try:
            self.notebook.select(self.tab_json)
        except Exception:
            pass

    # ── LEFT TOP: PDF Line Viewer ─────────────────────────────────────────
    def _build_line_viewer(self, parent):
        left = tk.LabelFrame(
            parent, text=" 📄 PDF Line Viewer — drag-select text to tag ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=6, pady=6
        )
        left.pack(fill="both", expand=True)

        line_area = tk.Frame(left, bg=BG_CARD_ALT)
        line_area.pack(fill="both", expand=True)

        self.txt_verify_lines = tk.Text(
            line_area, wrap="none", font=FONT_LOG,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            bd=0, cursor="xterm",
            selectbackground=ACCENT_INFO, selectforeground="#ffffff"
        )
        v_scroll = tk.Scrollbar(line_area, orient="vertical", command=self.txt_verify_lines.yview)
        h_scroll = tk.Scrollbar(line_area, orient="horizontal", command=self.txt_verify_lines.xview)
        self.txt_verify_lines.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")
        self.txt_verify_lines.pack(fill="both", expand=True)

        self.txt_verify_lines.tag_config(_TAG_MATCHED, foreground="#4ade80")
        self.txt_verify_lines.tag_config(
            _TAG_SEPARATOR, foreground="#f59e0b", font=(FONT_LOG[0], FONT_LOG[1], "bold")
        )
        self.txt_verify_lines.tag_config(_TAG_EXCLUDED, foreground="#6b7280")
        self.txt_verify_lines.tag_config(
            _TAG_SECTION, foreground="#c084fc", font=(FONT_LOG[0], FONT_LOG[1], "bold")
        )

        self.txt_verify_lines.bind("<Key>", lambda e: "break")
        self.txt_verify_lines.bind("<ButtonRelease-1>", self._on_lines_mouse_up)

    # ── LEFT BOTTOM: Preview ──────────────────────────────────────────────
    def _build_preview_panel(self, parent):
        results_frame = tk.LabelFrame(
            parent, text=" 🔍 Preview ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=6, pady=6
        )
        results_frame.pack(fill="both", expand=True)

        self.preview_nb = ttk.Notebook(results_frame)
        self.preview_nb.pack(fill="both", expand=True)

        # Sub-Tab A: Table View (Extracted Data Table with vertical and horizontal scrollbars)
        self.tab_preview_table = tk.Frame(self.preview_nb, bg=BG_CARD)
        self.preview_nb.add(self.tab_preview_table, text=" 📊 Extracted Data Table ")

        style = ttk.Style()
        style.configure(
            "Preview.Treeview", background=BG_CARD_ALT, fieldbackground=BG_CARD_ALT,
            foreground=TEXT_PRIMARY, rowheight=22, font=FONT_SMALL
        )
        style.configure(
            "Preview.Treeview.Heading", background=BG_CARD_ALT,
            foreground=TEXT_PRIMARY, font=FONT_SMALL_BOLD
        )

        preview_tree_area = tk.Frame(self.tab_preview_table, bg=BG_CARD)
        preview_tree_area.pack(fill="both", expand=True)

        self.tv_preview = ttk.Treeview(
            preview_tree_area, show="headings", height=8, style="Preview.Treeview"
        )
        p_vscroll = tk.Scrollbar(preview_tree_area, orient="vertical", command=self.tv_preview.yview)
        p_hscroll = tk.Scrollbar(preview_tree_area, orient="horizontal", command=self.tv_preview.xview)
        self.tv_preview.configure(yscrollcommand=p_vscroll.set, xscrollcommand=p_hscroll.set)
        p_vscroll.pack(side="right", fill="y")
        p_hscroll.pack(side="bottom", fill="x")
        self.tv_preview.pack(fill="both", expand=True)

        # Sub-Tab B: Summary Log with BOTH vertical and horizontal scrollbars
        self.tab_preview_log = tk.Frame(self.preview_nb, bg=BG_CARD)
        self.preview_nb.add(self.tab_preview_log, text=" 📝 Summary Log ")

        summary_area = tk.Frame(self.tab_preview_log, bg=BG_CARD_ALT)
        summary_area.pack(fill="both", expand=True)

        self.txt_verify_results = tk.Text(
            summary_area, height=8, wrap="none", font=FONT_LOG,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, bd=0
        )
        s_vscroll = tk.Scrollbar(summary_area, orient="vertical", command=self.txt_verify_results.yview)
        s_hscroll = tk.Scrollbar(summary_area, orient="horizontal", command=self.txt_verify_results.xview)
        self.txt_verify_results.configure(yscrollcommand=s_vscroll.set, xscrollcommand=s_hscroll.set)
        s_vscroll.pack(side="right", fill="y")
        s_hscroll.pack(side="bottom", fill="x")
        self.txt_verify_results.pack(fill="both", expand=True)
        self.txt_verify_results.config(state="disabled")

    # ── RIGHT TOP: Action Center ──────────────────────────────────────────
    def _build_action_center(self, parent):
        marks = tk.LabelFrame(
            parent, text=" 🎯 Action Center ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=8, pady=6
        )
        marks.pack(side="top", fill="x", pady=(0, 6))

        from ui.theme import bind_hover_effect

        # Selection status labels
        self.lbl_selection = tk.Label(
            marks, text="No selection yet.", font=FONT_LOG, fg=TEXT_ACCENT, bg=BG_CARD_ALT,
            anchor="w", wraplength=480, justify="left", padx=6, pady=4
        )
        self.lbl_selection.pack(fill="x", pady=(0, 2))

        status_row = tk.Frame(marks, bg=BG_CARD)
        status_row.pack(fill="x", pady=(0, 4))

        self.lbl_pending_label = tk.Label(
            status_row, text="🏷️ Label: (none)", font=FONT_SMALL, fg=TEXT_MUTED,
            bg=BG_CARD, anchor="w"
        )
        self.lbl_pending_label.pack(side="left", padx=(0, 20))

        self.lbl_row_draft = tk.Label(
            status_row, text="📊 Row draft: (empty)", font=FONT_SMALL, fg=TEXT_MUTED,
            bg=BG_CARD, anchor="w"
        )
        self.lbl_row_draft.pack(side="left")

        # Categorized action sub-tabs (single-word titles)
        actions_nb = ttk.Notebook(marks)
        actions_nb.pack(fill="x", pady=(2, 0))

        def _btn(parent_row, text, cmd, bg=BG_CARD_ALT, fg=TEXT_PRIMARY):
            b = tk.Button(
                parent_row, text=text, font=FONT_SMALL, bg=bg, fg=fg, bd=0,
                padx=8, pady=3, cursor="hand2", command=cmd
            )
            b.pack(side="left", padx=3, pady=2)
            bind_hover_effect(b, bg, BG_CARD)

        # Sub-Tab 1: Labels
        tab_labels = tk.Frame(actions_nb, bg=BG_CARD, padx=6, pady=4)
        actions_nb.add(tab_labels, text=" 🏷️ Labels ")
        tk.Label(
            tab_labels, text="Tag field names, record titles, or summary key-values.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w"
        ).pack(fill="x", pady=(0, 2))
        row_lbls = tk.Frame(tab_labels, bg=BG_CARD); row_lbls.pack(fill="x")
        _btn(row_lbls, "🏷️ Set as Label", self._set_pending_label)
        _btn(row_lbls, "❌ Clear Label", self._clear_pending_label)
        _btn(row_lbls, "→ Name Field", self._mark_name_field, bg=ACCENT_INFO, fg="white")
        _btn(row_lbls, "→ Address Stop", self._mark_address_stop, bg=ACCENT_INFO, fg="white")
        _btn(row_lbls, "→ Summary Field", self._mark_summary_field, bg=ACCENT_INFO, fg="white")

        # Sub-Tab 2: Rows
        tab_rows = tk.Frame(actions_nb, bg=BG_CARD, padx=6, pady=4)
        actions_nb.add(tab_rows, text=" 📊 Rows ")
        tk.Label(
            tab_rows, text="Select values on a repeating table line to build column patterns.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w"
        ).pack(fill="x", pady=(0, 2))
        row_rows = tk.Frame(tab_rows, bg=BG_CARD); row_rows.pack(fill="x")
        _btn(row_rows, "➕ Add as Row Column", self._add_row_column, bg=ACCENT_SUCCESS, fg="white")
        _btn(row_rows, "✅ Finalize Row Pattern", self._finalize_row_pattern, bg=ACCENT_SUCCESS, fg="white")
        _btn(row_rows, "🗑️ Clear Row Draft", self._clear_row_draft)

        # Sub-Tab 3: Exclude
        tab_sep = tk.Frame(actions_nb, bg=BG_CARD, padx=6, pady=4)
        actions_nb.add(tab_sep, text=" ✂️ Exclude ")
        tk.Label(
            tab_sep, text="Mark lines that split records, or patterns to exclude entirely.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w"
        ).pack(fill="x", pady=(0, 2))
        row_seps = tk.Frame(tab_sep, bg=BG_CARD); row_seps.pack(fill="x")
        _btn(row_seps, "✂️ Record Separator", self._mark_record_separator)
        _btn(row_seps, "🚫 Exclude Line", self._mark_exclude)

        # Sub-Tab 4: Sections
        tab_sec = tk.Frame(actions_nb, bg=BG_CARD, padx=6, pady=4)
        actions_nb.add(tab_sec, text=" 🔄 Sections ")
        tk.Label(
            tab_sec, text="Define recurring multi-line blocks (e.g. employment, courses, programs).",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w"
        ).pack(fill="x", pady=(0, 2))
        row_secs = tk.Frame(tab_sec, bg=BG_CARD); row_secs.pack(fill="x")
        _btn(row_secs, "🔄 New Repeating Section", self._mark_repeating_section, bg="#a855f7", fg="white")
        _btn(row_secs, "➕ Add Section Sub-Field", self._add_section_subfield, bg="#a855f7", fg="white")
        _btn(row_secs, "↳ Set Next-Line Value", self._mark_next_line_value, bg="#a855f7", fg="white")

    # ── RIGHT BOTTOM: Rules ──────────────────────────────────────────────────
    def _build_rules_panel(self, parent):
        fields_frame = tk.LabelFrame(
            parent, text=" 📋 Rules ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=8, pady=8
        )
        fields_frame.pack(side="bottom", fill="both", expand=True)

        from ui.theme import bind_hover_effect

        style = ttk.Style()
        style.configure(
            "Fields.Treeview", background=BG_CARD_ALT, fieldbackground=BG_CARD_ALT,
            foreground=TEXT_PRIMARY, rowheight=22, font=FONT_SMALL
        )
        style.configure(
            "Fields.Treeview.Heading", background=BG_CARD_ALT,
            foreground=TEXT_PRIMARY, font=FONT_SMALL_BOLD
        )

        tree_area = tk.Frame(fields_frame, bg=BG_CARD)
        tree_area.pack(fill="both", expand=True)

        self.tv_fields = ttk.Treeview(
            tree_area, columns=("field", "pattern"), show="headings", height=8,
            style="Fields.Treeview"
        )
        self.tv_fields.heading("field", text="Field / Scope")
        self.tv_fields.heading("pattern", text="Pattern / Value")
        self.tv_fields.column("field", width=160, anchor="w")
        self.tv_fields.column("pattern", width=200, anchor="w")
        fields_vscroll = tk.Scrollbar(tree_area, orient="vertical", command=self.tv_fields.yview)
        self.tv_fields.configure(yscrollcommand=fields_vscroll.set)
        fields_vscroll.pack(side="right", fill="y")
        self.tv_fields.pack(fill="both", expand=True)

        self.tv_fields.bind("<Double-1>", lambda e: self._open_edit_rules_dialog())

        table_btns = tk.Frame(fields_frame, bg=BG_CARD)
        table_btns.pack(fill="x", pady=(6, 0))

        btn_edit = tk.Button(
            table_btns, text="✏️ Edit Rules", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            bd=0, padx=12, pady=3, cursor="hand2", command=self._open_edit_rules_dialog
        )
        btn_edit.pack(side="left")
        bind_hover_effect(btn_edit, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

        btn_rem = tk.Button(
            table_btns, text="❌ Remove Selected", font=FONT_BODY, bg=ACCENT_DANGER, fg="white",
            activebackground=ACCENT_DANGER_HOVER, bd=0, padx=8, pady=3, cursor="hand2",
            command=self._remove_selected_field
        )
        btn_rem.pack(side="left", padx=(8, 0))
        bind_hover_effect(btn_rem, ACCENT_DANGER, ACCENT_DANGER_HOVER)

        btn_ref = tk.Button(
            table_btns, text="🔄 Refresh Table", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=8, pady=3, cursor="hand2", command=self._refresh_fields_table
        )
        btn_ref.pack(side="left", padx=(8, 0))
        bind_hover_effect(btn_ref, BG_CARD_ALT, BG_CARD)

    def _get_current_config(self) -> dict:
        cfg, err = self._parse_editor_json()
        if not err and cfg:
            return cfg
        return self.exporter.config if hasattr(self, "exporter") and self.exporter else DEFAULT_CONFIG

    def _open_edit_rules_dialog(self):
        """Open a modal popup dialog box to edit the extraction rules specification."""
        dialog = tk.Toplevel(self)
        dialog.title("Edit Rules Specification")
        dialog.geometry("660x540")
        dialog.configure(bg=BG_CARD)
        dialog.transient(self)
        dialog.grab_set()

        hdr = tk.Label(
            dialog, text=" ✏️ Edit Extraction Rules Specification ",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w", padx=12, pady=8
        )
        hdr.pack(fill="x")

        editor_frame = tk.Frame(dialog, bg=BG_CARD_ALT, padx=12, pady=6)
        editor_frame.pack(fill="both", expand=True)

        txt_dialog_editor = scrolledtext.ScrolledText(
            editor_frame, wrap="none", font=FONT_LOG,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, bd=0
        )
        txt_dialog_editor.pack(fill="both", expand=True)

        current_cfg = self._get_current_config()
        txt_dialog_editor.insert("1.0", json.dumps(current_cfg, indent=2, ensure_ascii=False))

        btn_bar = tk.Frame(dialog, bg=BG_CARD, padx=12, pady=10)
        btn_bar.pack(fill="x")

        def _save_and_close():
            raw_text = txt_dialog_editor.get("1.0", "end-1c").strip()
            try:
                cfg = json.loads(raw_text)
            except Exception as e:
                messagebox.showerror("Invalid JSON", f"Syntax Error in JSON rules:\n{e}", parent=dialog)
                return
            
            self._load_config_into_editor(cfg)
            self._refresh_fields_table()
            if self._verify_lines:
                self._render_verify_lines(self._verify_lines, cfg)
            self.lbl_verify_status.config(text="Rules updated & applied successfully.")
            messagebox.showinfo("Rules Saved", "Extraction rules updated successfully!", parent=dialog)
            dialog.destroy()

        def _validate():
            raw_text = txt_dialog_editor.get("1.0", "end-1c").strip()
            try:
                json.loads(raw_text)
                messagebox.showinfo("Valid JSON", "JSON rules syntax is valid!", parent=dialog)
            except Exception as e:
                messagebox.showerror("Invalid JSON", f"Syntax Error:\n{e}", parent=dialog)

        def _reset():
            if messagebox.askyesno("Reset Rules", "Reset rules to blank default template?", parent=dialog):
                txt_dialog_editor.delete("1.0", "end")
                txt_dialog_editor.insert("1.0", json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))

        from ui.theme import bind_hover_effect

        btn_save = tk.Button(
            btn_bar, text="💾 Save & Apply Rules", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            bd=0, padx=12, pady=4, cursor="hand2", command=_save_and_close
        )
        btn_save.pack(side="left")
        bind_hover_effect(btn_save, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

        btn_v = tk.Button(
            btn_bar, text="✔ Validate", font=FONT_BODY, bg=ACCENT_INFO, fg="white",
            activebackground=ACCENT_INFO_HOVER, bd=0, padx=10, pady=4, cursor="hand2", command=_validate
        )
        btn_v.pack(side="left", padx=(8, 0))
        bind_hover_effect(btn_v, ACCENT_INFO, ACCENT_INFO_HOVER)

        btn_r = tk.Button(
            btn_bar, text="🔄 Reset Defaults", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=10, pady=4, cursor="hand2", command=_reset
        )
        btn_r.pack(side="left", padx=(8, 0))
        bind_hover_effect(btn_r, BG_CARD_ALT, BG_CARD)

        btn_cancel = tk.Button(
            btn_bar, text="Cancel", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=10, pady=4, cursor="hand2", command=dialog.destroy
        )
        btn_cancel.pack(side="right")
        bind_hover_effect(btn_cancel, BG_CARD_ALT, BG_CARD)


    # ── Sample PDF loading ────────────────────────────────────────────────

    def _browse_sample_pdf(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")]
        )
        if path:
            self._load_verify_pdf(path)

    def _load_random_sample_pdf(self):
        folder = self.source_folder
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo(
                "No Folder",
                "Select a Source Folder above first, then click Random Sample."
            )
            return
        self._auto_load_sample_pdf(folder)

    def _auto_load_sample_pdf(self, folder: str):
        pdfs = (
            list(Path(folder).glob("*.pdf")) +
            list(Path(folder).glob("*.PDF"))
        )
        if not pdfs:
            self.lbl_verify_status.config(text="No PDFs found in the selected source folder.")
            return
        chosen = random.choice(pdfs)
        self._load_verify_pdf(str(chosen))

    def _load_verify_pdf(self, pdf_path: str):
        self._verify_pdf_path = pdf_path
        self._verify_selected_line = -1
        self._pending_label = None
        self._row_draft = None
        self.lbl_pending_label.config(text="\U0001F3F7 Label: (none)")
        self.lbl_row_draft.config(text="\U0001F4CA Row draft: (empty)")
        self.lbl_selection.config(text="No selection yet.")

        cfg, err = self._parse_editor_json()
        engine = ConfigurableExtractor(cfg or DEFAULT_CONFIG)

        def _worker():
            try:
                lines = engine.read_pdf_lines(pdf_path)
                self._verify_lines = lines
                self.after(0, lambda: self._render_verify_lines(lines, cfg or DEFAULT_CONFIG))
                self.after(0, lambda: self.lbl_verify_pdf.config(
                    text=os.path.basename(pdf_path), fg=TEXT_PRIMARY
                ))
                self.after(0, lambda: self.lbl_verify_status.config(
                    text=f"Loaded {len(lines)} lines from {os.path.basename(pdf_path)}. "
                         f"Drag-select a word or phrase, then use the marking buttons."
                ))
            except Exception as e:
                self.after(0, lambda: self.lbl_verify_status.config(
                    text=f"Error loading PDF: {describe_exception(e)}"
                ))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_verify_lines(self, lines: list[str], config: dict):
        sep = (config.get("record_separator") or "").strip()
        exclude_pats = [
            re.compile(p, re.IGNORECASE)
            for p in (config.get("exclude_patterns") or [])
            if p
        ]
        name_pat_str = (config.get("name_detection") or {}).get("pattern", "")
        try:
            name_pat = re.compile(name_pat_str) if name_pat_str else None
        except re.error:
            name_pat = None

        data_row_str = (config.get("data_rows") or {}).get("pattern", "")
        try:
            data_pat = re.compile(data_row_str) if data_row_str else None
        except re.error:
            data_pat = None

        section_markers = [
            (s.get("marker") or "").strip()
            for s in (config.get("repeating_sections") or [])
            if (s.get("marker") or "").strip()
        ]

        self.txt_verify_lines.delete("1.0", "end")

        for i, line in enumerate(lines):
            line_num_str = f"{i + 1:>4}  "
            display = line_num_str + (line if line.strip() else "") + "\n"

            tag = None
            stripped = line.strip()
            if sep and sep in line:
                tag = _TAG_SEPARATOR
            elif any(m in line for m in section_markers):
                tag = _TAG_SECTION
            elif any(p.search(line) for p in exclude_pats):
                tag = _TAG_EXCLUDED
            elif data_pat and data_pat.match(stripped):
                tag = _TAG_MATCHED
            elif name_pat and name_pat.match(stripped):
                tag = _TAG_MATCHED

            self.txt_verify_lines.insert("end", display, tag or "")

    # ── Selection handling ──────────────────────────────────────────────

    def _on_lines_mouse_up(self, event):
        idx = self.txt_verify_lines.index(f"@{event.x},{event.y}")
        line_num = int(idx.split(".")[0])
        data_idx = line_num - 1
        if 0 <= data_idx < len(self._verify_lines):
            self._verify_selected_line = data_idx
        self._update_selection_label()

    def _get_current_selection(self):
        """Return {"line_idx","start","end","text"} for the current
        single-line word/phrase selection in the line viewer, or None."""
        ranges = self.txt_verify_lines.tag_ranges("sel")
        if not ranges:
            return None
        start_line, start_col = map(int, str(ranges[0]).split("."))
        end_line, end_col = map(int, str(ranges[1]).split("."))
        if start_line != end_line:
            return None
        data_idx = start_line - 1
        if data_idx < 0 or data_idx >= len(self._verify_lines):
            return None
        raw_line = self._verify_lines[data_idx]
        prefix_len = len(f"{data_idx + 1:>4}  ")
        raw_start = min(max(0, start_col - prefix_len), len(raw_line))
        raw_end = min(max(0, end_col - prefix_len), len(raw_line))
        if raw_end <= raw_start:
            return None
        return {"line_idx": data_idx, "start": raw_start, "end": raw_end,
                "text": raw_line[raw_start:raw_end]}

    def _update_selection_label(self):
        sel = self._get_current_selection()
        if sel:
            text = sel["text"].strip()
            display = (text[:80] + "\u2026") if len(text) > 80 else text
            self.lbl_selection.config(text=f"Selected (line {sel['line_idx'] + 1}): {display!r}")
        elif 0 <= self._verify_selected_line < len(self._verify_lines):
            text = self._verify_lines[self._verify_selected_line].strip()
            display = (text[:80] + "\u2026") if len(text) > 80 else text
            self.lbl_selection.config(
                text=f"No word selected \u2014 whole line {self._verify_selected_line + 1} "
                     f"will be used: {display!r}"
            )
        else:
            self.lbl_selection.config(text="No selection yet.")

    def _selected_text_or_line(self):
        """Return the best-effort text to act on: a word/phrase selection if
        present, else the whole line that was last clicked."""
        sel = self._get_current_selection()
        if sel:
            return sel["text"].strip()
        if 0 <= self._verify_selected_line < len(self._verify_lines):
            return self._verify_lines[self._verify_selected_line].strip()
        return ""

    # ── Config read/write helpers ───────────────────────────────────────

    def _require_valid_config(self):
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            messagebox.showerror("Invalid Config", f"Fix the JSON config before applying:\n{err}")
            return None, err
        return cfg, None

    def _commit_config(self, cfg, log_msg=""):
        self._load_config_into_editor(cfg)
        if self._verify_lines:
            self._render_verify_lines(self._verify_lines, cfg)
        self._refresh_fields_table()
        if log_msg:
            self.log("info", f"[Verify] {log_msg}")

    # ── Marking actions ──────────────────────────────────────────────────

    def _set_pending_label(self):
        sel = self._get_current_selection()
        if not sel:
            messagebox.showinfo("No Selection", "Drag-select the label text (e.g. 'GPA:') first.")
            return
        self._pending_label = {"text": sel["text"].strip(), "line_idx": sel["line_idx"]}
        self.lbl_pending_label.config(
            text=f"\U0001F3F7 Label: {self._pending_label['text']!r} (line {sel['line_idx'] + 1})"
        )

    def _clear_pending_label(self):
        self._pending_label = None
        self.lbl_pending_label.config(text="\U0001F3F7 Label: (none)")

    def _mark_record_separator(self):
        text = self._selected_text_or_line()
        if not text:
            messagebox.showinfo("No Selection", "Select text or click a line first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        cfg["record_separator"] = text
        self._commit_config(cfg, f"Set record_separator = {text!r}")

    def _mark_exclude(self):
        text = self._selected_text_or_line()
        if not text:
            messagebox.showinfo("No Selection", "Select text or click a line first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        pat = re.escape(text)
        lst = cfg.get("exclude_patterns") if isinstance(cfg.get("exclude_patterns"), list) else []
        if pat not in lst:
            lst.append(pat)
        cfg["exclude_patterns"] = lst
        self._commit_config(cfg, f"Added exclude_pattern: {pat!r}")

    def _mark_name_field(self):
        sel = self._get_current_selection()
        if not sel:
            messagebox.showinfo("No Selection", "Drag-select the name text in a line first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        raw_line = self._verify_lines[sel["line_idx"]]
        label_text = ""
        if self._pending_label and self._pending_label["line_idx"] == sel["line_idx"]:
            label_text = self._pending_label["text"]
        pattern = build_anchored_pattern(raw_line, sel["text"], label_text)

        nd = cfg.get("name_detection") if isinstance(cfg.get("name_detection"), dict) else None
        if not nd:
            col = simpledialog.askstring(
                "Output Column", "Column name for this field:",
                initialvalue="record_name", parent=self
            )
            if not col:
                return
            nd = {"output_column": col, "stop_at": ""}
        nd["pattern"] = pattern
        cfg["name_detection"] = nd
        self._commit_config(cfg, f"Set name_detection.pattern from selection {sel['text']!r}")

    def _mark_address_stop(self):
        text = self._selected_text_or_line()
        if not text:
            messagebox.showinfo("No Selection", "Select text or click a line first.")
            return
        text = text[:60]
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        if not isinstance(cfg.get("address_block"), dict):
            cfg["address_block"] = {"output_column": "address", "stop_prefixes": []}
        existing = cfg["address_block"].setdefault("stop_prefixes", [])
        if text not in existing:
            existing.append(text)
        self._commit_config(cfg, f"Added address_block stop_prefix: {text!r}")

    def _mark_summary_field(self):
        sel = self._get_current_selection()
        if not sel:
            messagebox.showinfo("No Selection", "Drag-select the value text (e.g. a GPA number) first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        raw_line = self._verify_lines[sel["line_idx"]]
        label_text = ""
        default_name = "summary_value"
        if self._pending_label and self._pending_label["line_idx"] == sel["line_idx"]:
            label_text = self._pending_label["text"]
            default_name = re.sub(r"[^A-Za-z0-9]+", "_", label_text).strip("_").lower() or default_name

        pattern = build_anchored_pattern(raw_line, sel["text"], label_text)
        name = simpledialog.askstring(
            "Summary Field Name", "Output column name:", initialvalue=default_name, parent=self
        )
        if not name:
            return

        lst = cfg.get("summary_fields") if isinstance(cfg.get("summary_fields"), list) else []
        lst = [sf for sf in lst if sf.get("name") != name]
        lst.append({"name": name, "pattern": pattern})
        cfg["summary_fields"] = lst
        self._commit_config(cfg, f"Added summary_field {name!r} from selection {sel['text']!r}")

    def _add_row_column(self):
        sel = self._get_current_selection()
        if not sel:
            messagebox.showinfo("No Selection", "Drag-select a value on the data row line first.")
            return
        if self._row_draft and self._row_draft["line_idx"] != sel["line_idx"]:
            if not messagebox.askyesno(
                "New Row Line",
                "Your selection is on a different line than the current column draft. "
                "Start a new draft for this line?"
            ):
                return
            self._row_draft = None
        if not self._row_draft:
            self._row_draft = {"line_idx": sel["line_idx"], "cols": []}

        name = simpledialog.askstring(
            "Column Name", "Name for this data column:",
            initialvalue=f"col_{len(self._row_draft['cols']) + 1}", parent=self
        )
        if not name:
            return
        self._row_draft["cols"].append({
            "name": name, "start": sel["start"], "end": sel["end"], "text": sel["text"]
        })
        self._refresh_row_draft_label()

    def _refresh_row_draft_label(self):
        if not self._row_draft or not self._row_draft["cols"]:
            self.lbl_row_draft.config(text="Data-row column draft: (empty)")
            return
        names = ", ".join(c["name"] for c in self._row_draft["cols"])
        self.lbl_row_draft.config(
            text=f"Data-row column draft (line {self._row_draft['line_idx'] + 1}): {names}"
        )

    def _finalize_row_pattern(self):
        if not self._row_draft or not self._row_draft["cols"]:
            messagebox.showinfo("No Draft", "Add at least one column from a data row line first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        raw_line = self._verify_lines[self._row_draft["line_idx"]]
        pattern, columns = build_row_pattern(raw_line, self._row_draft["cols"])
        cfg["data_rows"] = {"pattern": pattern, "columns": columns}
        self._commit_config(cfg, f"Generated data_rows pattern with columns {columns}")
        self._row_draft = None
        self._refresh_row_draft_label()

    def _clear_row_draft(self):
        self._row_draft = None
        self._refresh_row_draft_label()

    # ── Repeating Section marking ─────────────────────────────────────────
    # This is the block you were missing: it lets you mark a phrase/line that
    # recurs multiple times *inside a single record* (e.g. "Program Description:",
    # "Employer:", one per job/term/course) as a repeating_sections entry,
    # instead of hand-editing the raw JSON.

    def _mark_repeating_section(self):
        text = self._selected_text_or_line()
        if not text:
            messagebox.showinfo(
                "No Selection",
                "Select the marker text/line that repeats (e.g. 'Program Description:') first."
            )
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return

        sections = cfg.get("repeating_sections") if isinstance(cfg.get("repeating_sections"), list) else []
        existing_names = [s.get("name") for s in sections]

        name = simpledialog.askstring(
            "Repeating Section Name",
            "Name for this repeating block (e.g. 'program', 'employment', 'course'):",
            initialvalue=existing_names[0] if len(existing_names) == 1 else "section",
            parent=self
        )
        if not name:
            return

        existing = next((s for s in sections if s.get("name") == name), None)
        if existing:
            existing["marker"] = text
            existing.setdefault("marker_line_fields", [])
            existing.setdefault("sub_fields", [])
        else:
            sections.append({
                "name": name,
                "marker": text,
                "marker_line_fields": [],
                "next_line_value": None,
                "sub_fields": [],
            })
        cfg["repeating_sections"] = sections
        self._commit_config(cfg, f"Set repeating_sections[{name!r}].marker = {text!r}")
        messagebox.showinfo(
            "Repeating Section Created",
            f"'{name}' will now start a new occurrence every time {text!r} appears inside a "
            "record.\n\nNext: drag-select a value on one of the lines that follows a marker and "
            "click 'Add Section Sub-Field' (or use 'Set Next-Line Value' if the very next line "
            "IS the value, with no label)."
        )

    def _pick_section_name(self, cfg, prompt="Which repeating section is this field part of?"):
        sections = cfg.get("repeating_sections") or []
        if not sections:
            messagebox.showinfo(
                "No Repeating Section Yet",
                "Create a repeating section first with 'New Repeating Section'."
            )
            return None
        if len(sections) == 1:
            return sections[0]["name"]
        names = [s["name"] for s in sections]
        return simpledialog.askstring(
            "Choose Section", f"{prompt}\nOptions: {', '.join(names)}",
            initialvalue=names[0], parent=self
        )

    def _add_section_subfield(self):
        sel = self._get_current_selection()
        if not sel:
            messagebox.showinfo("No Selection", "Drag-select the value text for this field first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return

        sec_name = self._pick_section_name(cfg)
        if not sec_name:
            return
        sections = cfg.get("repeating_sections") or []
        section = next((s for s in sections if s.get("name") == sec_name), None)
        if not section:
            messagebox.showerror("Not Found", f"Section '{sec_name}' not found.")
            return

        raw_line = self._verify_lines[sel["line_idx"]]
        label_text = ""
        default_name = "field"
        if self._pending_label and self._pending_label["line_idx"] == sel["line_idx"]:
            label_text = self._pending_label["text"]
            default_name = re.sub(r"[^A-Za-z0-9]+", "_", label_text).strip("_").lower() or default_name

        pattern = build_anchored_pattern(raw_line, sel["text"], label_text)
        field_name = simpledialog.askstring(
            "Sub-Field Name", "Output column name for this field (per occurrence):",
            initialvalue=default_name, parent=self
        )
        if not field_name:
            return

        sub_fields = section.setdefault("sub_fields", [])
        sub_fields = [sf for sf in sub_fields if sf.get("name") != field_name]
        sub_fields.append({"name": field_name, "pattern": pattern})
        section["sub_fields"] = sub_fields

        cfg["repeating_sections"] = sections
        self._commit_config(
            cfg, f"Added sub_field {field_name!r} to repeating_sections[{sec_name!r}]"
        )

    def _mark_next_line_value(self):
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        sec_name = self._pick_section_name(
            cfg, prompt="Which section's marker is immediately followed by its (unlabeled) value?"
        )
        if not sec_name:
            return
        sections = cfg.get("repeating_sections") or []
        section = next((s for s in sections if s.get("name") == sec_name), None)
        if not section:
            return

        field_name = simpledialog.askstring(
            "Next-Line Field Name",
            "Output column name for the value on the line right after the marker "
            "(e.g. 'program', 'employer'):",
            initialvalue=sec_name, parent=self
        )
        if not field_name:
            return

        section["next_line_value"] = {"name": field_name}
        cfg["repeating_sections"] = sections
        self._commit_config(
            cfg, f"Set repeating_sections[{sec_name!r}].next_line_value = {field_name!r}"
        )

    # ── Configured Fields table ──────────────────────────────────────────

    def _refresh_fields_table(self):
        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            return
        for item in self.tv_fields.get_children():
            self.tv_fields.delete(item)
        self._field_row_map = {}
        rows = list(describe_field_map(cfg))
        for i, row in enumerate(rows):
            iid = f"row{i}"
            self._field_row_map[iid] = (row["scope"], row["index"])
            pattern_display = row["pattern"]
            if len(pattern_display) > 70:
                pattern_display = pattern_display[:67] + "..."
            self.tv_fields.insert("", "end", iid=iid, values=(row["label"], pattern_display))

        # Keep Tab 1 config overview live
        if self.lbl_config_summary:
            sep = cfg.get("record_separator") or "(not set)"
            excl = len(cfg.get("exclude_patterns") or [])
            secs = len(cfg.get("repeating_sections") or [])
            dr_pat = (cfg.get("data_rows") or {}).get("pattern", "(not set)")
            dr_short = (dr_pat[:60] + "...") if len(dr_pat) > 60 else dr_pat
            summary = (
                f"\U0001F4CC  Record Separator : {sep}\n"
                f"\U0001F9F1  Fields & Rules   : {len(rows)} rule(s)\n"
                f"\U0001F501  Repeating Sections: {secs} block(s)\n"
                f"\U0001F4CB  Data-Row Pattern : {dr_short}\n"
                f"\U0001F6AB  Exclude Patterns : {excl} pattern(s)"
            )
            self.lbl_config_summary.config(text=summary)


    def _remove_selected_field(self):
        sel = self.tv_fields.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Select a row in the Configured Fields table first.")
            return
        cfg, err = self._require_valid_config()
        if cfg is None:
            return
        for iid in sel:
            scope, index = self._field_row_map.get(iid, (None, None))
            if scope:
                remove_field_entry(cfg, scope, index)
        self._commit_config(cfg, "Removed field-map entry via Configured Fields table.")

    # ── Extraction preview ────────────────────────────────────────────────

    # ── Extraction preview ────────────────────────────────────────────────

    def _run_verify_extraction(self):
        if not self._verify_pdf_path or not self._verify_lines:
            messagebox.showinfo(
                "No Sample",
                "Load a sample PDF first using 'Pick PDF' or 'Random Sample'."
            )
            return

        cfg, err = self._parse_editor_json()
        if err or cfg is None:
            messagebox.showerror("Invalid Config", f"Fix the JSON config first:\n{err}")
            return

        def _worker():
            try:
                engine = ConfigurableExtractor(cfg)
                full_text, page_offsets = engine.read_pdf(self._verify_pdf_path)
                records = engine.split_records(full_text, page_offsets)

                all_preview_rows = []
                lines = []
                lines.append(f"Sample file  : {os.path.basename(self._verify_pdf_path)}")
                lines.append(f"Records found: {len(records)}")
                lines.append("")

                sep_line = "\u2500" * 50

                if not records:
                    lines.append("\u26A0  No records matched configured 'record_separator'.")
                    lines.append("   Check 'record_separator' \u2014 it must match text in your PDF.")
                    if full_text.strip():
                        lines.append("")
                        lines.append(sep_line)
                        lines.append("Fallback Preview (Evaluated against full document text):")
                        fb_name, fb_addr, fb_rep, fb_summary, fb_rows = engine.extract_record(full_text.strip())
                        preview = engine.preview_config_on_block(full_text.strip())
                        lines.append(f"  Name          : {preview.get('name', 'N/A')}")
                        lines.append(f"  Data rows     : {preview.get('data_rows_count', 0)}")
                        for sf_name, sf_val in (preview.get('summary_fields') or {}).items():
                            lines.append(f"  {sf_name} : {sf_val}")
                        fb_row_data = fb_rows if fb_rows else ([{}] if fb_summary or fb_name != 'N/A' else [])
                        for dr in fb_row_data:
                            r = {}
                            if fb_name != 'N/A':
                                r['student_name'] = fb_name
                            r.update(fb_summary)
                            r.update(dr)
                            all_preview_rows.append(r)
                else:
                    for rec_idx, (block, page_num) in enumerate(records, 1):
                        name, address, rep_sections, summary, data_rows = engine.extract_record(block)

                        row_data_list = data_rows if data_rows else ([{}] if summary or name != 'N/A' else [])
                        for dr in row_data_list:
                            row = {}
                            if name != 'N/A':
                                row['student_name'] = name
                            row.update(summary)
                            row.update(dr)
                            all_preview_rows.append(row)

                        if rec_idx <= 5:
                            preview = engine.preview_config_on_block(block)
                            lines.append(sep_line)
                            lines.append(f"Record #{rec_idx}  (page {page_num})")
                            lines.append(f"  Name          : {preview['name']}")
                            lines.append(f"  Data rows     : {preview['data_rows_count']}")

                            for sec_name, secs in (preview['repeating_sections'] or {}).items():
                                lines.append(f"  {sec_name} sections : {len(secs)}")

                            for sf_name, sf_val in (preview['summary_fields'] or {}).items():
                                lines.append(f"  {sf_name} : {sf_val}")

                    if len(records) > 5:
                        lines.append(sep_line)
                        lines.append(f"  \u2026 and {len(records) - 5} more record(s) not shown in text summary.")

                result_text = "\n".join(lines)
                self.after(0, lambda: self._update_preview_display(result_text, all_preview_rows))
                self.after(0, lambda: self.lbl_verify_status.config(
                    text=f"Preview complete: {len(records)} record(s), {len(all_preview_rows)} row(s) extracted from "
                         f"{os.path.basename(self._verify_pdf_path)}."
                ))
            except Exception as e:
                err_text = f"Preview error: {describe_exception(e)}"
                self.after(0, lambda: self._update_preview_display(err_text, []))
                self.after(0, lambda: self.lbl_verify_status.config(text=err_text))

        threading.Thread(target=_worker, daemon=True).start()
        self.lbl_verify_status.config(text="Running preview extraction\u2026")

    def _update_preview_display(self, text: str, rows: list[dict]):
        # Update text log
        self.txt_verify_results.configure(state="normal")
        self.txt_verify_results.delete("1.0", "end")
        self.txt_verify_results.insert("1.0", text)
        self.txt_verify_results.configure(state="disabled")

        # Update Treeview table
        for item in self.tv_preview.get_children():
            self.tv_preview.delete(item)

        if not rows:
            self.tv_preview["columns"] = ("status",)
            self.tv_preview.heading("status", text="Status")
            self.tv_preview.column("status", width=400, anchor="w")
            self.tv_preview.insert("", "end", values=("No tabular data extracted. Check config patterns.",))
            return

        # Collect unique column headers in order
        col_set = []
        for r in rows:
            for k in r.keys():
                if k not in col_set:
                    col_set.append(k)

        self.tv_preview["columns"] = tuple(col_set)
        for col in col_set:
            title = col.replace("_", " ").title()
            width = max(110, len(title) * 11)
            self.tv_preview.heading(col, text=title)
            self.tv_preview.column(col, width=width, anchor="w")

        for r in rows:
            row_vals = [str(r.get(col, "N/A")) for col in col_set]
            self.tv_preview.insert("", "end", values=tuple(row_vals))

        # Auto select the table view tab
        try:
            self.preview_nb.select(self.tab_preview_table)
        except Exception:
            pass

    def _set_verify_results(self, text: str):
        self._update_preview_display(text, [])

    # ======================================================================
    # Preset management
    # ======================================================================
    def _refresh_preset_menu(self):
        menu = self.preset_menu["menu"]
        menu.delete(0, "end")
        names = ["(unsaved / custom)"] + list_presets()
        for name in names:
            menu.add_command(label=name, command=lambda n=name: self.preset_var.set(n))

    def _on_load_preset(self):
        name = self.preset_var.get()
        if name == "(unsaved / custom)":
            messagebox.showinfo("Load Preset", "Select a saved preset from the dropdown first.")
            return
        try:
            cfg = load_preset(name)
            self._load_config_into_editor(cfg)
            self._refresh_fields_table()
            if self._verify_lines:
                self._render_verify_lines(self._verify_lines, cfg)
            self.log("info", f"Loaded config preset '{name}'.")
        except Exception as e:
            messagebox.showerror("Load Failed", describe_exception(e))

    def _on_save_preset(self):
        cfg, err = self._parse_editor_json()
        if err:
            messagebox.showerror("Invalid JSON", err)
            return
        name = simpledialog.askstring("Save Preset", "Preset name:", parent=self)
        if not name:
            return
        try:
            save_preset(name, cfg)
            self._refresh_preset_menu()
            self.preset_var.set(name)
            self.log("info", f"Saved config preset '{name}'.")
        except Exception as e:
            messagebox.showerror("Save Failed", describe_exception(e))

    def _on_delete_preset(self):
        name = self.preset_var.get()
        if name == "(unsaved / custom)":
            return
        if not messagebox.askyesno("Delete Preset", f"Delete preset '{name}'? This cannot be undone."):
            return
        delete_preset(name)
        self._refresh_preset_menu()
        self.preset_var.set("(unsaved / custom)")
        self.log("info", f"Deleted config preset '{name}'.")

    def _on_auto_detect_preset_ml(self):
        sample_path = self._verify_pdf_path
        if not sample_path or not os.path.isfile(sample_path):
            if self.source_folder and os.path.isdir(self.source_folder):
                for f in os.listdir(self.source_folder):
                    if f.lower().endswith(".pdf"):
                        sample_path = os.path.join(self.source_folder, f)
                        break

        if not sample_path or not os.path.isfile(sample_path):
            sample_path = filedialog.askopenfilename(
                title="Select Sample PDF for ML Layout Analysis",
                filetypes=[("PDF files", "*.pdf;*.PDF")]
            )

        if not sample_path or not os.path.isfile(sample_path):
            return

        try:
            preset = synthesize_preset_from_pdf(sample_path)
            doc_title = preset.get("document_title")
            rep_type = preset.get("report_type")
            if doc_title:
                base_title = doc_title
            elif rep_type == "student_schedule":
                base_title = "Student Schedule"
            elif rep_type == "student_transcript":
                base_title = "Student Transcript"
            elif rep_type == "student_ledger":
                base_title = "Student Ledger"
            elif rep_type == "resume":
                base_title = "Resume"
            elif rep_type == "attendance":
                base_title = "Attendance"
            elif rep_type == "invoice":
                base_title = "Invoice"
            else:
                base_title = preset.get("record_separator") or os.path.splitext(os.path.basename(sample_path))[0]
            default_name = f"{base_title} (Auto ML)"
            preset_name = simpledialog.askstring(
                "Save ML-Generated Preset",
                "Enter a name for this auto-detected preset:",
                initialvalue=default_name,
                parent=self
            )
            if not preset_name:
                return

            save_preset(preset_name, preset)
            self._refresh_preset_menu()
            self.preset_var.set(preset_name)
            self._load_config_into_editor(preset)
            self._refresh_fields_table()

            # Load into line viewer and review tab
            self._load_verify_pdf(sample_path)
            self.review_tab.load_pdf_for_review(sample_path)

            messagebox.showinfo(
                "ML Preset Generated",
                f"Successfully synthesized preset '{preset_name}'!\n\n"
                f"• Detected {len(preset.get('summary_fields', []))} key-value fields\n"
                f"• Detected table with {len(preset.get('data_rows', {}).get('columns', [])) if preset.get('data_rows') else 0} columns\n\n"
                "The preset has been loaded into the Visual Field Mapper and Review tab."
            )
        except Exception as e:
            messagebox.showerror("ML Analysis Failed", f"Could not synthesize preset: {describe_exception(e)}")

    def _load_config_into_editor(self, cfg: dict):
        self.txt_config.delete("1.0", "end")
        self.txt_config.insert("1.0", json.dumps(cfg, indent=2, ensure_ascii=False))

    def _parse_editor_json(self):
        raw = self.txt_config.get("1.0", "end").strip()
        if not raw:
            return None, "Config editor is empty."
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as e:
            return None, f"JSON error: {e}"

    def _validate_config(self):
        cfg, err = self._parse_editor_json()
        if err:
            messagebox.showerror("Invalid Config", err)
            return
        messagebox.showinfo("Valid Config", "Config JSON is valid.")

    # ======================================================================
    # Debug Log & Execution Log CSV viewers (Live Activity & Logs tab)
    # ======================================================================
    def _refresh_logs_view(self):
        pass

    # ======================================================================
    # Run (Start button)
    # ======================================================================
    def get_task_from_inputs(self, silent: bool = False) -> QueueTask | None:
        if not self.source_folder or not os.path.isdir(self.source_folder):
            if not silent:
                messagebox.showerror("Missing Folder", "Please select a source folder containing PDF files.")
            return None
        if not self.output_xlsx:
            if not silent:
                messagebox.showerror("Missing Output", "Please choose an output .xlsx file path.")
            return None
        cfg, err = self._parse_editor_json()
        if err:
            if not silent:
                messagebox.showerror("Invalid Config", err)
            return None

        label = f"Source: {os.path.basename(self.source_folder)} | Output: {os.path.basename(self.output_xlsx)}"

        inputs = {
            "source_folder": self.source_folder,
            "output_xlsx": self.output_xlsx,
            "config": cfg,
        }

        import uuid
        return QueueTask(task_id=str(uuid.uuid4())[:8], label=label, inputs=inputs)

    def run_single_task(self, task: QueueTask) -> bool:
        inputs = task.inputs
        self._log_dir = os.path.dirname(inputs["output_xlsx"]) or "."
        self.log("info", f"Running PDF Extractor task: {task.label}")

        try:
            result = self.exporter.run(
                source_folder=inputs["source_folder"],
                output_xlsx=inputs["output_xlsx"],
                config=inputs["config"],
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log
            )
            self.after(0, self._refresh_logs_view)
            if not self.abort_requested:
                self.after(600, self._prompt_post_run_review)
            return not self.abort_requested
        except Exception as e:
            import traceback
            self.log("error", f"Error executing task {task.label}: {describe_exception(e)}")
            self.log("debug", traceback.format_exc())
            self.after(0, self._refresh_logs_view)
            return False

    def _prompt_post_run_review(self):
        try:
            self.review_tab.load_random_sample()
            self.notebook.select(self.tab_review)
            self.log("info", "[ML Tuning] Switched to 'Review & Self-Tune' tab for random sample verification.")
        except Exception:
            pass

    def verify_single_task(self, task: QueueTask):
        output_xlsx = task.inputs.get("output_xlsx", "")
        source_folder = task.inputs.get("source_folder", "")
        if not output_xlsx or not os.path.isfile(output_xlsx):
            return

        try:
            res = self.exporter.verify(output_xlsx, source_folder)
            self.log("info", f"[VERIFY] Task: {task.label} -> PDFs: {res['pdf_count']}, Unique Sources: {res['unique_sources']}, Rows Written: {res['rows']}, Missing: {len(res['missing_files'])}")
        except Exception as e:
            self.log("error", f"[VERIFY] Verification failed for task {task.label}: {describe_exception(e)}")

    def _handle_progress(self, data: dict):
        t = data.get('type')
        if t == 'total':
            self.set_progress(0, data['total'])
            self.update_kpis(total_students=data['total'])
        elif t == 'item_start':
            self.update_status(f"Processing file {data['idx']+1}/{self.total_students} ({data['student_id']})…")
            self.update_kpis(
                students_traversed=data['idx'],
                current_student_id=data['student_id'],
                current_category="Extracting"
            )
        elif t == 'item_done':
            self.set_progress(data['completed'])
            self.update_kpis(
                students_traversed=data['completed'],
                files_done=data['completed']
            )

    # ======================================================================
    # Verify Results button
    # ======================================================================
    def on_verify_clicked(self):
        if not self.output_xlsx or not os.path.isfile(self.output_xlsx):
            messagebox.showwarning("Warning", "Run the extraction first (or select an existing output file).")
            return
        try:
            res = self.exporter.verify(self.output_xlsx, self.source_folder)
            msg = (
                f"PDFs in Source Folder: {res['pdf_count']}\n"
                f"Unique Source Files in Output: {res['unique_sources']}\n"
                f"Total Rows Written: {res['rows']}\n"
            )
            if res['missing_files']:
                msg += f"\nPDFs with no rows in output:\n" + "\n".join(res['missing_files'][:15])
            else:
                msg += "\nAll PDFs accounted for in the output workbook."

            if self._log_dir:
                exec_log = execution_log_path(self._log_dir)
                if exec_log and os.path.isfile(exec_log):
                    msg += f"\n\nExecution log: {exec_log}"

            messagebox.showinfo("Verification Report", msg)
        except Exception as e:
            messagebox.showerror("Verification Failed", describe_exception(e))
