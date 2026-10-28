"""
ui/pdf_review_tab.py
====================
Human-in-the-Loop Active Learning: Review & Self-Tune Tab for PDF Extractor.

Allows users to:
  1. Pick random documents from an extraction batch or source folder.
  2. Cross-check extracted field values side-by-side with original document text.
  3. Mark fields as correct (boosting confidence in learning_data.json).
  4. Correct mismatched fields (triggering automated regex anchor re-synthesis).
  5. Add recurring noise lines to exclusion patterns.
  6. Automatically tune presets so they self-improve with every run.
"""

import os
import random
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, scrolledtext, filedialog
from typing import Dict, Any, List, Optional

from ui.theme import (
    BG_CARD, BG_CARD_ALT, BG_MAIN,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, TEXT_ACCENT,
    ACCENT_INFO, ACCENT_INFO_HOVER, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER,
    ACCENT_DANGER, ACCENT_DANGER_HOVER,
    FONT_BODY, FONT_BODY_BOLD, FONT_SMALL, FONT_SMALL_BOLD, FONT_SUBTITLE, FONT_LOG,
    bind_hover_effect
)
from modules.pdf_extractor import ConfigurableExtractor, save_preset
from modules.pdf_rule_tuner import (
    tune_field_in_preset, tune_row_field_in_preset, tune_data_row_line_in_preset,
    add_exclude_rule_to_preset, record_verification_feedback
)
from core.learning_system import learning_engine


class PdfReviewTab(tk.Frame):
    """
    Side-by-side Review & Self-Tuning workspace for PDF extraction verification.
    """

    def __init__(self, parent, main_ui):
        super().__init__(parent, bg=BG_CARD, padx=12, pady=10)
        self.main_ui = main_ui
        self._current_pdf_path: str = ""
        self._current_lines: List[str] = []
        self._extracted_fields: Dict[str, Any] = {}
        self._extracted_rows: List[Dict[str, Any]] = []
        self._row_columns: List[str] = []
        self._selected_line_idx: int = -1
        self._sample_candidates_map: Dict[str, str] = {}

        self._build_ui()

    def _build_ui(self):
        # 1. Header & Instructions
        header_frame = tk.Frame(self, bg=BG_CARD)
        header_frame.pack(fill="x", pady=(0, 8))

        tk.Label(
            header_frame,
            text="🔍 Active Learning & Self-Tuning: Random Document Verification",
            font=FONT_SUBTITLE, fg=TEXT_PRIMARY, bg=BG_CARD
        ).pack(side="left")

        self.lbl_stats = tk.Label(
            header_frame,
            text="Tuned: 0 | Confidence: --",
            font=FONT_SMALL_BOLD, fg=TEXT_ACCENT, bg=BG_CARD
        )
        self.lbl_stats.pack(side="right")

        desc = tk.Label(
            self,
            text=(
                "Pick a random sample PDF to verify extracted data. If any field or table row was extracted incorrectly, "
                "select the true text in the document viewer and click 'Point Row Value' or 'Edit / Auto-Tune'."
            ),
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w", justify="left"
        )
        desc.pack(fill="x", pady=(0, 8))

        # 2. Controls Toolbar
        toolbar = tk.LabelFrame(
            self, text=" 📂 Sample Document Selection & Quick Actions ",
            font=FONT_SMALL_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=10, pady=8
        )
        toolbar.pack(fill="x", pady=(0, 10))

        # Button: Pick Random Document
        self.btn_random = tk.Button(
            toolbar, text="🎲 Pick Random Document", font=FONT_BODY_BOLD,
            bg=ACCENT_INFO, fg="white", activebackground=ACCENT_INFO_HOVER,
            bd=0, padx=10, pady=4, cursor="hand2", command=self.load_random_sample
        )
        self.btn_random.pack(side="left", padx=(0, 2))
        bind_hover_effect(self.btn_random, ACCENT_INFO, ACCENT_INFO_HOVER)

        # Quick Dropdown menu button for Pick Random Document
        self.btn_random_menu = tk.Button(
            toolbar, text="▾", font=FONT_BODY_BOLD,
            bg=ACCENT_INFO, fg="white", activebackground=ACCENT_INFO_HOVER,
            bd=0, padx=6, pady=4, cursor="hand2", command=self._show_random_menu
        )
        self.btn_random_menu.pack(side="left", padx=(0, 8))
        bind_hover_effect(self.btn_random_menu, ACCENT_INFO, ACCENT_INFO_HOVER)
        self.btn_random.bind("<Button-3>", lambda e: self._show_random_menu())

        # Button: Pick / Browse PDF File
        btn_browse = tk.Button(
            toolbar, text="📂 Pick PDF...", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=10, pady=4, cursor="hand2", command=self.browse_pdf_sample
        )
        btn_browse.pack(side="left", padx=(0, 10))
        bind_hover_effect(btn_browse, BG_CARD_ALT, BG_CARD)

        # Label: Sample Combobox
        tk.Label(
            toolbar, text="Sample:", font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD
        ).pack(side="left", padx=(0, 4))

        # Combobox: Select any sample
        self.combo_sample = ttk.Combobox(
            toolbar, font=FONT_BODY, state="readonly", width=30,
            postcommand=self._refresh_sample_dropdown
        )
        self.combo_sample.pack(side="left", padx=(0, 6))
        self.combo_sample.bind("<<ComboboxSelected>>", self._on_sample_combobox_selected)

        self.lbl_sample_count = tk.Label(
            toolbar, text="", font=FONT_SMALL,
            fg=TEXT_MUTED, bg=BG_CARD
        )
        self.lbl_sample_count.pack(side="left", padx=(0, 10))

        # Right side actions
        btn_refresh = tk.Button(
            toolbar, text="🔄 Re-Extract Preview", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=10, pady=4, cursor="hand2",
            command=self.refresh_extraction_preview
        )
        btn_refresh.pack(side="right", padx=4)

        self.lbl_current_doc = tk.Label(
            toolbar, text="(No sample loaded - choose a document or click 'Pick Random Document')",
            font=FONT_SMALL, fg=TEXT_SECONDARY, bg=BG_CARD, wraplength=260, justify="right"
        )
        self.lbl_current_doc.pack(side="right", padx=(0, 10))

        # 3. Split Verification View (Document Viewer on Left, Fields & Rows on Right)
        split_pane = tk.PanedWindow(self, orient="horizontal", bg=BG_CARD, bd=0, sashwidth=6)
        split_pane.pack(fill="both", expand=True)

        # Left Pane: Original Document Text
        left_frame = tk.LabelFrame(
            split_pane, text=" 📄 Original Document Text (select text to point/tune field) ",
            font=FONT_SMALL_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=8, pady=8
        )
        split_pane.add(left_frame, minsize=400, stretch="always")

        self.txt_doc = scrolledtext.ScrolledText(
            left_frame, wrap="none", font=FONT_LOG,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, bd=0,
            selectbackground=ACCENT_INFO, selectforeground="#ffffff"
        )
        self.txt_doc.pack(fill="both", expand=True)
        self.txt_doc.tag_config("match", foreground="#4ade80", font=(FONT_LOG[0], FONT_LOG[1], "bold"))
        self.txt_doc.tag_config("selected_line", background="#1e293b")

        # Right Pane: Extracted Data Table & Tuning
        right_frame = tk.LabelFrame(
            split_pane, text=" 📊 Extracted Data & Active Self-Tuning ",
            font=FONT_SMALL_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid",
            padx=8, pady=8
        )
        split_pane.add(right_frame, minsize=450, stretch="always")

        # Tabs for Fields vs Table Rows
        self.review_nb = ttk.Notebook(right_frame)
        self.review_nb.pack(fill="both", expand=True)

        self.tab_fields = tk.Frame(self.review_nb, bg=BG_CARD)
        self.review_nb.add(self.tab_fields, text=" 📋 Document & Summary Fields ")

        self.tab_rows = tk.Frame(self.review_nb, bg=BG_CARD)
        self.review_nb.add(self.tab_rows, text=" 📊 Extracted Table Rows ")

        # Treeview for Fields (Tab 1)
        tree_frame = tk.Frame(self.tab_fields, bg=BG_CARD)
        tree_frame.pack(fill="both", expand=True)

        columns = ("check", "field", "value", "status")
        self.tree_fields = ttk.Treeview(tree_frame, columns=columns, show="headings", height=12)
        self.tree_fields.heading("check", text="✔")
        self.tree_fields.heading("field", text="Field / Column")
        self.tree_fields.heading("value", text="Extracted Value")
        self.tree_fields.heading("status", text="Verification Status")

        self.tree_fields.column("check", width=35, anchor="center")
        self.tree_fields.column("field", width=135, anchor="w")
        self.tree_fields.column("value", width=180, anchor="w")
        self.tree_fields.column("status", width=90, anchor="center")

        v_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_fields.yview)
        self.tree_fields.configure(yscrollcommand=v_scroll.set)
        self.tree_fields.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")
        self.tree_fields.bind("<<TreeviewSelect>>", self._on_field_selected)
        self.tree_fields.bind("<Button-1>", self._on_tree_fields_click)

        # Treeview for Extracted Data Rows (Tab 2)
        rows_frame = tk.Frame(self.tab_rows, bg=BG_CARD)
        rows_frame.pack(fill="both", expand=True)

        self.tree_rows = ttk.Treeview(rows_frame, show="headings", height=12)
        r_vscroll = ttk.Scrollbar(rows_frame, orient="vertical", command=self.tree_rows.yview)
        r_hscroll = ttk.Scrollbar(rows_frame, orient="horizontal", command=self.tree_rows.xview)
        self.tree_rows.configure(yscrollcommand=r_vscroll.set, xscrollcommand=r_hscroll.set)
        r_vscroll.pack(side="right", fill="y")
        r_hscroll.pack(side="bottom", fill="x")
        self.tree_rows.pack(fill="both", expand=True)
        self.tree_rows.bind("<<TreeviewSelect>>", self._on_row_selected)

        # Action Buttons
        action_bar = tk.Frame(right_frame, bg=BG_CARD)
        action_bar.pack(fill="x", pady=(8, 0))

        btn_correct = tk.Button(
            action_bar, text="✔ Mark Correct", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", activebackground=ACCENT_SUCCESS_HOVER,
            bd=0, padx=10, pady=5, cursor="hand2", command=self._on_mark_correct
        )
        btn_correct.pack(side="left", padx=(0, 4))
        bind_hover_effect(btn_correct, ACCENT_SUCCESS, ACCENT_SUCCESS_HOVER)

        btn_mark_all = tk.Button(
            action_bar, text="✔✔ Mark All Correct", font=FONT_BODY_BOLD,
            bg="#065f46", fg="white", activebackground="#047857",
            bd=0, padx=10, pady=5, cursor="hand2", command=self._on_mark_all_correct
        )
        btn_mark_all.pack(side="left", padx=4)
        bind_hover_effect(btn_mark_all, "#065f46", "#047857")

        btn_edit = tk.Button(
            action_bar, text="✏ Edit / Auto-Tune", font=FONT_BODY_BOLD,
            bg=ACCENT_INFO, fg="white", activebackground=ACCENT_INFO_HOVER,
            bd=0, padx=10, pady=5, cursor="hand2", command=self._on_edit_and_tune
        )
        btn_edit.pack(side="left", padx=4)
        bind_hover_effect(btn_edit, ACCENT_INFO, ACCENT_INFO_HOVER)

        btn_point_row = tk.Button(
            action_bar, text="🎯 Point Row Value", font=FONT_BODY_BOLD,
            bg="#8b5cf6", fg="white", activebackground="#7c3aed",
            bd=0, padx=10, pady=5, cursor="hand2", command=self._on_point_row_value
        )
        btn_point_row.pack(side="left", padx=4)
        bind_hover_effect(btn_point_row, "#8b5cf6", "#7c3aed")

        btn_point_line = tk.Button(
            action_bar, text="📍 Point Row Line", font=FONT_BODY,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY,
            bd=0, padx=8, pady=5, cursor="hand2", command=self._on_point_row_line
        )
        btn_point_line.pack(side="left", padx=4)
        bind_hover_effect(btn_point_line, BG_CARD_ALT, BG_CARD)

        btn_exclude = tk.Button(
            action_bar, text="🚫 Exclude Line", font=FONT_BODY,
            bg=ACCENT_DANGER, fg="white", activebackground=ACCENT_DANGER_HOVER,
            bd=0, padx=8, pady=5, cursor="hand2", command=self._on_add_exclude
        )
        btn_exclude.pack(side="right", padx=(4, 0))
        bind_hover_effect(btn_exclude, ACCENT_DANGER, ACCENT_DANGER_HOVER)

        self.lbl_review_feedback = tk.Label(
            right_frame, text="", font=FONT_SMALL_BOLD,
            fg=ACCENT_SUCCESS, bg=BG_CARD, anchor="w"
        )
        self.lbl_review_feedback.pack(fill="x", pady=(4, 0))

        # Initial refresh of candidate samples in dropdown
        self._refresh_sample_dropdown()

    # ----------------------------------------------------------------------
    # Logic: Loading Samples & Extracting
    # ----------------------------------------------------------------------

    def get_sample_candidates(self) -> List[str]:
        """Collects candidate PDF file paths from source folder, verified path, and project."""
        candidates: List[str] = []
        seen = set()

        def _add_file(path_str: str):
            if not path_str:
                return
            try:
                norm = os.path.normpath(os.path.abspath(path_str))
                if norm not in seen and os.path.isfile(norm) and norm.lower().endswith(".pdf"):
                    seen.add(norm)
                    candidates.append(norm)
            except Exception:
                pass

        # 1. Active sample if loaded
        if self._current_pdf_path:
            _add_file(self._current_pdf_path)

        # 2. Main UI verified sample path
        verify_pdf = getattr(self.main_ui, "_verify_pdf_path", "")
        if verify_pdf:
            _add_file(verify_pdf)

        # 3. Source folder configured on Tab 1
        source_dir = getattr(self.main_ui, "source_folder", "")
        if source_dir and os.path.isdir(source_dir):
            try:
                for f in sorted(os.listdir(source_dir)):
                    if f.lower().endswith(".pdf"):
                        _add_file(os.path.join(source_dir, f))
            except Exception:
                pass

        # 4. Fallback search in project root & workspace parent if no source folder files found
        if not candidates:
            search_dirs = [
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # unified_dashboard
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # workspace root
            ]
            for s_dir in search_dirs:
                if not os.path.isdir(s_dir):
                    continue
                for root, dirs, files in os.walk(s_dir):
                    dirs[:] = [d for d in dirs if d not in (".venv", ".git", "build", "dist", "__pycache__", "node_modules", "export test", "upload test")]
                    for f in sorted(files):
                        if f.lower().endswith(".pdf"):
                            _add_file(os.path.join(root, f))
                if candidates:
                    break

        return candidates

    def _refresh_sample_dropdown(self):
        """Refreshes the candidate sample PDFs list in the dropdown combobox."""
        candidates = self.get_sample_candidates()

        new_map: Dict[str, str] = {}
        bname_counts: Dict[str, int] = {}
        for p in candidates:
            bname = os.path.basename(p)
            bname_counts[bname] = bname_counts.get(bname, 0) + 1

        for p in candidates:
            bname = os.path.basename(p)
            if bname_counts[bname] > 1:
                parent = os.path.basename(os.path.dirname(p))
                display_name = f"{bname} ({parent})"
            else:
                display_name = bname
            new_map[display_name] = p

        self._sample_candidates_map = new_map
        sorted_names = sorted(new_map.keys(), key=lambda s: s.lower())
        self.combo_sample["values"] = sorted_names

        if sorted_names:
            self.lbl_sample_count.config(text=f"({len(sorted_names)} available)")
        else:
            self.lbl_sample_count.config(text="(No PDFs)")

        # Sync combobox value to current PDF if one is loaded
        if self._current_pdf_path and os.path.isfile(self._current_pdf_path):
            current_norm = os.path.normpath(os.path.abspath(self._current_pdf_path))
            for dname, fpath in new_map.items():
                if os.path.normpath(os.path.abspath(fpath)) == current_norm:
                    self.combo_sample.set(dname)
                    break

    def _on_sample_combobox_selected(self, event=None):
        """Loads the document selected by the user in the combobox."""
        selected_display = self.combo_sample.get()
        if selected_display in self._sample_candidates_map:
            path = self._sample_candidates_map[selected_display]
            self.load_pdf_for_review(path)

    def browse_pdf_sample(self):
        """Opens a file dialog so user can select any PDF on disk to review & self-tune."""
        init_dir = getattr(self.main_ui, "source_folder", "") or "."
        if not os.path.isdir(init_dir):
            init_dir = "."
        path = filedialog.askopenfilename(
            title="Select PDF Document to Review & Self-Tune",
            initialdir=init_dir,
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")]
        )
        if path and os.path.isfile(path):
            self.load_pdf_for_review(path)

    def _show_random_menu(self):
        """Dropdown/context menu providing quick options: Pick Random, Browse, or Pick from list."""
        menu = tk.Menu(self, tearoff=0, bg=BG_CARD, fg=TEXT_PRIMARY, activebackground=ACCENT_INFO)
        menu.add_command(label="🎲 Pick Random Document", command=self.load_random_sample)
        menu.add_command(label="📂 Browse Any PDF File...", command=self.browse_pdf_sample)
        menu.add_separator()

        self._refresh_sample_dropdown()
        items = list(self._sample_candidates_map.keys())
        if items:
            menu.add_command(label=f"📋 Available Documents ({len(items)}):", state="disabled")
            for name in items[:20]:
                path = self._sample_candidates_map[name]
                menu.add_command(label=f"   {name}", command=lambda p=path: self.load_pdf_for_review(p))
            if len(items) > 20:
                menu.add_command(
                    label=f"   ... and {len(items) - 20} more (use Sample dropdown)",
                    command=lambda: self.combo_sample.focus_set()
                )
        else:
            menu.add_command(label="(No PDF files found - click Browse)", state="disabled")

        try:
            x = self.btn_random.winfo_rootx()
            y = self.btn_random.winfo_rooty() + self.btn_random.winfo_height()
            menu.tk_popup(x, y)
        except Exception:
            pass

    def load_random_sample(self):
        """Picks a random PDF from the candidate documents and loads it."""
        candidates = self.get_sample_candidates()
        if not candidates:
            messagebox.showinfo(
                "No PDFs Found",
                "No PDF files available to review. Please choose a Source Folder on Tab 1 or click '📂 Pick PDF...' to browse for a document."
            )
            return

        chosen_pdf = random.choice(candidates)
        self.load_pdf_for_review(chosen_pdf)

    def load_pdf_for_review(self, pdf_path: str):
        """Loads a specific PDF file into the review text viewer and extracts fields."""
        if not os.path.isfile(pdf_path):
            return

        self._current_pdf_path = os.path.normpath(os.path.abspath(pdf_path))
        filename = os.path.basename(pdf_path)
        self.lbl_current_doc.config(text=f"Active: {filename}")

        # Update combobox to reflect selected document
        self._refresh_sample_dropdown()
        current_norm = self._current_pdf_path
        matched = False
        for dname, fpath in self._sample_candidates_map.items():
            if os.path.normpath(os.path.abspath(fpath)) == current_norm:
                self.combo_sample.set(dname)
                matched = True
                break
        if not matched:
            self.combo_sample.set(filename)

        cfg, _ = self.main_ui._parse_editor_json()
        extractor = ConfigurableExtractor(cfg or {})

        try:
            self._current_lines = extractor.read_pdf_lines(pdf_path)
        except Exception as e:
            self._current_lines = []
            messagebox.showerror("Read Error", f"Could not read PDF lines: {e}")
            return

        self._render_doc_text()
        self.refresh_extraction_preview()
        self._update_stats_label()

    def _render_doc_text(self):
        """Fills txt_doc with line numbers and original document text."""
        self.txt_doc.delete("1.0", "end")
        for i, line in enumerate(self._current_lines):
            self.txt_doc.insert("end", f"{i+1:3d} | {line}\n")

    def refresh_extraction_preview(self):
        """Re-runs extraction using current active configuration and populates fields & rows tables."""
        if not self._current_pdf_path:
            return

        cfg, _ = self.main_ui._parse_editor_json()
        extractor = ConfigurableExtractor(cfg or {})

        all_rows = []
        preview = {}
        try:
            full_text, offsets = extractor.read_pdf(self._current_pdf_path)
            records = extractor.split_records(full_text, offsets)
            if records:
                first_block = records[0][0]
                preview = extractor.preview_config_on_block(first_block)
                try:
                    _, _, _, _, all_rows = extractor.extract_record(first_block)
                except Exception:
                    all_rows = preview.get("data_rows_sample", [])
            elif full_text.strip():
                first_block = full_text.strip()
                preview = extractor.preview_config_on_block(first_block)
                try:
                    _, _, _, _, all_rows = extractor.extract_record(first_block)
                except Exception:
                    all_rows = preview.get("data_rows_sample", [])
            else:
                preview = {}
                all_rows = []
        except Exception:
            preview = {}
            all_rows = []

        self._extracted_fields.clear()
        self._extracted_rows = all_rows or []

        # Populate Name & Summary fields
        if preview.get("name") and preview["name"] != "N/A":
            self._extracted_fields["student_name"] = preview["name"]

        for k, v in preview.get("summary_fields", {}).items():
            self._extracted_fields[k] = v

        # Determine row columns
        row_cols = []
        if all_rows:
            row_cols = [col for col in all_rows[0].keys() if col not in ("source_file", "page_number")]
        elif cfg and cfg.get("data_rows", {}).get("columns"):
            row_cols = list(cfg["data_rows"]["columns"])
        self._row_columns = row_cols

        # Add data row template columns to fields list
        if row_cols:
            first_row = all_rows[0] if all_rows else {}
            for col in row_cols:
                val = first_row.get(col, "(no rows matched)")
                self._extracted_fields[f"row_{col}"] = str(val)

        # Clear and repopulate Fields Treeview
        for item in self.tree_fields.get_children():
            self.tree_fields.delete(item)

        for field_name, value in self._extracted_fields.items():
            display_val = str(value) if value is not None else "(empty)"
            self.tree_fields.insert("", "end", values=("☐", field_name, display_val, "⚪ Unverified"))

        # Clear and repopulate Data Rows Treeview
        for item in self.tree_rows.get_children():
            self.tree_rows.delete(item)

        if row_cols:
            cols_def = ["#"] + row_cols
            self.tree_rows["columns"] = cols_def
            self.tree_rows.heading("#", text="#")
            self.tree_rows.column("#", width=40, anchor="center")

            for col in row_cols:
                heading_text = col.replace("_", " ").title()
                self.tree_rows.heading(col, text=heading_text)
                self.tree_rows.column(col, width=130, anchor="w")

            for i, r in enumerate(all_rows):
                row_vals = [str(i + 1)] + [str(r.get(c, "")) for c in row_cols]
                self.tree_rows.insert("", "end", values=row_vals)
        else:
            self.tree_rows["columns"] = ["status"]
            self.tree_rows.heading("status", text="Table Status")
            self.tree_rows.column("status", width=400, anchor="w")
            self.tree_rows.insert(
                "", "end",
                values=("(No table rows extracted yet — select a line on the left and click '📍 Point Row Line')",)
            )

        self._highlight_matches()

    def _highlight_matches(self):
        """Highlights matching extracted values in the document text pane."""
        self.txt_doc.tag_remove("match", "1.0", "end")

        for field_name, val in self._extracted_fields.items():
            s_val = str(val).strip()
            if len(s_val) < 2 or s_val in ("N/A", "(empty)", "(no rows matched)"):
                continue
            start_idx = "1.0"
            while True:
                pos = self.txt_doc.search(s_val, start_idx, stopindex="end")
                if not pos:
                    break
                end_pos = f"{pos}+{len(s_val)}c"
                self.txt_doc.tag_add("match", pos, end_pos)
                start_idx = end_pos

    def _on_field_selected(self, event=None):
        """When user selects a field in treeview, jumps to and highlights it in text viewer."""
        selected = self.tree_fields.selection()
        if not selected:
            return
        item_vals = self.tree_fields.item(selected[0], "values")
        if not item_vals or len(item_vals) < 3:
            return
        val = item_vals[2]
        if val and val not in ("N/A", "(empty)", "(no rows matched)"):
            pos = self.txt_doc.search(val, "1.0", stopindex="end")
            if pos:
                self.txt_doc.see(pos)

    def _on_tree_fields_click(self, event):
        """Clicking on checkbox column toggles verification status without pop-up."""
        region = self.tree_fields.identify_region(event.x, event.y)
        if region == "cell":
            col = self.tree_fields.identify_column(event.x)
            item = self.tree_fields.identify_row(event.y)
            if item and col == "#1":
                vals = list(self.tree_fields.item(item, "values"))
                current_check = vals[0]
                new_check = "☑" if current_check != "☑" else "☐"
                new_status = "✅ Verified" if new_check == "☑" else "⚪ Unverified"
                vals[0] = new_check
                vals[3] = new_status
                self.tree_fields.item(item, values=vals)

                field_name = vals[1]
                value = vals[2]
                preset_name = self.main_ui.preset_var.get()
                if new_check == "☑":
                    try:
                        record_verification_feedback(preset_name, field_name, is_correct=True, old_value=value, new_value=value)
                    except Exception:
                        pass
                    self.lbl_review_feedback.config(text=f"✔ Field '{field_name}' marked as verified! Rule confidence boosted.")
                else:
                    self.lbl_review_feedback.config(text=f"Field '{field_name}' unchecked.")
                self._update_stats_label()

    def _on_row_selected(self, event=None):
        """When user selects a row in tree_rows, scrolls to and highlights the matching line in txt_doc."""
        selected = self.tree_rows.selection()
        if not selected:
            return
        item_vals = self.tree_rows.item(selected[0], "values")
        if not item_vals or len(item_vals) < 2:
            return

        for val in item_vals[1:]:
            s_val = str(val).strip()
            if len(s_val) >= 3 and s_val not in ("N/A", "(empty)", "(no rows matched)"):
                pos = self.txt_doc.search(s_val, "1.0", stopindex="end")
                if pos:
                    self.txt_doc.see(pos)
                    line_no = pos.split(".")[0]
                    self.txt_doc.tag_remove("selected_line", "1.0", "end")
                    self.txt_doc.tag_add("selected_line", f"{line_no}.0", f"{line_no}.end+1c")
                    break

    # ----------------------------------------------------------------------
    # Active Learning & Tuning Actions
    # ----------------------------------------------------------------------

    def _on_mark_correct(self):
        """User confirms the selected field or table row is extracted correctly without pop-ups."""
        active_tab = self.review_nb.index(self.review_nb.select())

        if active_tab == 1:
            # Extracted rows tab
            selected = self.tree_rows.selection()
            if not selected:
                self.lbl_review_feedback.config(text="⚠ Please select a row in the Extracted Data Rows table.")
                return
            item = selected[0]
            row_vals = self.tree_rows.item(item, "values")
            preset_name = self.main_ui.preset_var.get()
            for col in (self._row_columns or []):
                try:
                    record_verification_feedback(preset_name, f"row_{col}", is_correct=True)
                except Exception:
                    pass
            self._update_stats_label()
            row_num = row_vals[0] if row_vals else "1"
            self.lbl_review_feedback.config(text=f"✔ Row #{row_num} marked as verified! Confidence boosted.")
            return

        selected = self.tree_fields.selection()
        if not selected:
            self.lbl_review_feedback.config(text="⚠ Please select a field row or click the checkbox beside it.")
            return

        item = selected[0]
        vals = list(self.tree_fields.item(item, "values"))
        field_name = vals[1]
        value = vals[2]

        preset_name = self.main_ui.preset_var.get()
        try:
            record_verification_feedback(preset_name, field_name, is_correct=True, old_value=value, new_value=value)
        except Exception:
            pass

        vals[0] = "☑"
        vals[3] = "✅ Verified"
        self.tree_fields.item(item, values=vals)
        self._update_stats_label()
        self.lbl_review_feedback.config(text=f"✔ Field '{field_name}' marked as verified! Rule confidence increased.")

    def _on_mark_all_correct(self):
        """Marks all displayed fields or rows as verified in one click without pop-ups."""
        preset_name = self.main_ui.preset_var.get()
        active_tab = self.review_nb.index(self.review_nb.select())

        if active_tab == 1:
            count = 0
            for item in self.tree_rows.get_children():
                count += 1
                for col in (self._row_columns or []):
                    try:
                        record_verification_feedback(preset_name, f"row_{col}", is_correct=True)
                    except Exception:
                        pass
            self._update_stats_label()
            self.lbl_review_feedback.config(text=f"✔ All {count} table rows marked as verified! Confidence boosted.")
            return

        count = 0
        for item in self.tree_fields.get_children():
            count += 1
            vals = list(self.tree_fields.item(item, "values"))
            field_name, value = vals[1], vals[2]
            try:
                record_verification_feedback(preset_name, field_name, is_correct=True, old_value=value, new_value=value)
            except Exception:
                pass
            vals[0] = "☑"
            vals[3] = "✅ Verified"
            self.tree_fields.item(item, values=vals)
        self._update_stats_label()
        self.lbl_review_feedback.config(text=f"✔ All {count} fields marked as verified! Rule confidence boosted.")

    def _on_point_row_value(self):
        """Points highlighted text from document viewer as the ground-truth value for a row column."""
        highlighted_text = ""
        try:
            highlighted_text = self.txt_doc.get("sel.first", "sel.last").strip()
        except Exception:
            pass

        cfg, _ = self.main_ui._parse_editor_json()
        cfg = cfg or {}
        row_cols = self._row_columns or cfg.get("data_rows", {}).get("columns") or []

        col_name = ""
        # 1. From tree_fields if selected
        sel_f = self.tree_fields.selection()
        if sel_f:
            vals = self.tree_fields.item(sel_f[0], "values")
            f_name = vals[1] if len(vals) > 1 else vals[0]
            if f_name.startswith("row_") or f_name in row_cols:
                col_name = f_name[4:] if f_name.startswith("row_") else f_name

        # 2. Or ask user from available row columns
        if not col_name and row_cols:
            col_options = ", ".join(row_cols)
            chosen_col = simpledialog.askstring(
                "Select Row Column",
                f"Which row column are you pointing?\nAvailable columns: {col_options}",
                initialvalue=row_cols[0],
                parent=self
            )
            if not chosen_col:
                return
            col_name = chosen_col.strip()

        if not col_name:
            col_name = simpledialog.askstring(
                "Row Column Name",
                "Enter the row column name to point (e.g. description, debit, credit):",
                initialvalue="description",
                parent=self
            )
            if not col_name:
                return

        col_name = col_name[4:] if col_name.startswith("row_") else col_name

        corrected = simpledialog.askstring(
            "Point Row Field Value",
            f"Enter or confirm the true value for row column '{col_name}':\n(Hint: You can select text in the left pane before clicking this button)",
            initialvalue=highlighted_text,
            parent=self
        )
        if not corrected or not corrected.strip():
            return
        corrected_val = corrected.strip()

        # Find target line in current lines
        target_line = ""
        for line in self._current_lines:
            if corrected_val in line:
                target_line = line
                break
        if not target_line and self._current_lines:
            target_line = self._current_lines[0]

        updated_cfg, msg = tune_row_field_in_preset(
            cfg, col_name, corrected_val, target_line, all_lines=self._current_lines
        )

        self.main_ui._load_config_into_editor(updated_cfg)
        self.main_ui._refresh_fields_table()

        preset_name = self.main_ui.preset_var.get()
        if preset_name and preset_name != "(unsaved / custom)":
            save_preset(preset_name, updated_cfg)

        record_verification_feedback(
            preset_name or "active_preset",
            f"row_{col_name}",
            is_correct=False,
            old_value="",
            new_value=corrected_val
        )

        self.refresh_extraction_preview()
        self._update_stats_label()
        messagebox.showinfo("Row Pattern Auto-Tuned", f"Successfully auto-tuned row pattern for '{col_name}'!\n\n{msg}")

    def _on_point_row_line(self):
        """Allows user to select any line in txt_doc and point it as a sample data row to tune data_rows."""
        highlighted_line = ""
        try:
            highlighted_line = self.txt_doc.get("sel.first", "sel.last").strip()
        except Exception:
            pass

        if not highlighted_line:
            try:
                line_idx = self.txt_doc.index("insert").split(".")[0]
                line_no = int(line_idx) - 1
                if 0 <= line_no < len(self._current_lines):
                    highlighted_line = self._current_lines[line_no]
            except Exception:
                pass

        pointed_line = simpledialog.askstring(
            "Point Data Row Line",
            "Enter or confirm the sample data row line text from the document:\n(Hint: You can select a row line on the left first)",
            initialvalue=highlighted_line,
            parent=self
        )
        if not pointed_line or not pointed_line.strip():
            return

        cfg, _ = self.main_ui._parse_editor_json()
        cfg = cfg or {}

        updated_cfg, msg = tune_data_row_line_in_preset(cfg, pointed_line.strip())

        self.main_ui._load_config_into_editor(updated_cfg)
        self.main_ui._refresh_fields_table()

        preset_name = self.main_ui.preset_var.get()
        if preset_name and preset_name != "(unsaved / custom)":
            save_preset(preset_name, updated_cfg)

        self.refresh_extraction_preview()
        self._update_stats_label()
        messagebox.showinfo("Data Row Synthesized", f"Successfully synthesized data_rows from sample line!\n\n{msg}")

    def _on_edit_and_tune(self):
        """User corrects a field value, triggering regex auto-tuning."""
        active_tab = self.review_nb.index(self.review_nb.select())
        if active_tab == 1:
            self._on_point_row_value()
            return

        selected = self.tree_fields.selection()
        if not selected:
            messagebox.showinfo("Select Field", "Please select a field in the table to edit.")
            return

        item = selected[0]
        vals = self.tree_fields.item(item, "values")
        field_name = vals[1]
        current_val = vals[2]

        if field_name.startswith("row_") or field_name in (self._row_columns or []):
            self._on_point_row_value()
            return

        # Check if user has highlighted text in the document viewer
        highlighted_text = ""
        try:
            highlighted_text = self.txt_doc.get("sel.first", "sel.last").strip()
        except Exception:
            pass

        initial_val = highlighted_text if highlighted_text else current_val
        corrected = simpledialog.askstring(
            "Correct Field Value",
            f"Enter the true value for '{field_name}':\n(Hint: You can select text in the left pane before clicking edit)",
            initialvalue=initial_val,
            parent=self
        )

        if not corrected or corrected.strip() == current_val:
            return

        corrected_val = corrected.strip()

        # Smart line resolution (supports single-line, multi-line values, and field label anchors)
        target_line = ""
        for line in self._current_lines:
            if corrected_val in line:
                target_line = line
                break

        if not target_line:
            clean_target = field_name.replace("_", " ").lower()
            for line in self._current_lines:
                line_lower = line.lower()
                if clean_target in line_lower or any(w in line_lower for w in clean_target.split() if len(w) > 3):
                    target_line = line
                    break

        if not target_line:
            words = corrected_val.split()
            for i in range(len(words), 0, -1):
                sub = " ".join(words[:i])
                for line in self._current_lines:
                    if sub in line:
                        target_line = line
                        break
                if target_line:
                    break

        if not target_line:
            target_line = self._current_lines[0] if self._current_lines else ""

        # Perform Rule Induction / Regex Auto-Tuning
        cfg, _ = self.main_ui._parse_editor_json()
        updated_cfg, msg = tune_field_in_preset(
            cfg, field_name, corrected_val, target_line, all_lines=self._current_lines
        )

        # Save to main editor and preset
        self.main_ui._load_config_into_editor(updated_cfg)
        self.main_ui._refresh_fields_table()

        preset_name = self.main_ui.preset_var.get()
        if preset_name and preset_name != "(unsaved / custom)":
            save_preset(preset_name, updated_cfg)

        # Record in learning_data.json
        record_verification_feedback(
            preset_name or "active_preset",
            field_name,
            is_correct=False,
            old_value=current_val,
            new_value=corrected_val
        )

        # Re-extract and update UI
        self.refresh_extraction_preview()
        self._update_stats_label()

        messagebox.showinfo("Self-Tuning Applied", f"Successfully auto-tuned preset rules!\n\n{msg}")

    def _on_add_exclude(self):
        """Adds highlighted text or selected line to preset exclusion patterns."""
        selected_text = ""
        try:
            selected_text = self.txt_doc.get("sel.first", "sel.last").strip()
        except Exception:
            pass

        if not selected_text:
            selected_text = simpledialog.askstring(
                "Exclude Noise Pattern",
                "Enter text or regex pattern to exclude from all pages (e.g. footers, page numbers):",
                parent=self
            )

        if not selected_text:
            return

        cfg, _ = self.main_ui._parse_editor_json()
        updated_cfg, msg = add_exclude_rule_to_preset(cfg, selected_text)

        self.main_ui._load_config_into_editor(updated_cfg)
        self.main_ui._refresh_fields_table()

        preset_name = self.main_ui.preset_var.get()
        if preset_name and preset_name != "(unsaved / custom)":
            save_preset(preset_name, updated_cfg)

        self.refresh_extraction_preview()
        messagebox.showinfo("Exclusion Added", msg)

    def _update_stats_label(self):
        """Refreshes the stats label from learning_data.json."""
        preset_name = self.main_ui.preset_var.get()
        profile = learning_engine._data.get("pdf_extractor_profiles", {}).get(preset_name, {})
        runs = profile.get("verified_runs", 0)
        conf_map = profile.get("field_confidence", {})
        avg_conf = f"{sum(conf_map.values()) / len(conf_map) * 100:.0f}%" if conf_map else "--"

        self.lbl_stats.config(text=f"Tuned Runs: {runs} | Confidence: {avg_conf}")
