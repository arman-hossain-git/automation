"""
ui/learning_ui.py
=================
Learning Insights Dashboard Tab.

Displays everything the automation suite has learned across all past runs:
  - Live KPI tiles: total runs, items processed, domains learned, XPath patterns
  - Run history table with per-run performance metrics
  - Domain Profiles: XPath success hits per domain
  - Student Insights: searchable per-student learned facts
  - ⚠️ Clear Learning Memory button (with confirmation)
"""

import tkinter as tk
from tkinter import ttk, messagebox

from core.learning_system import learning_engine
from ui.theme import (
    BG_MAIN, BG_CARD, BG_CARD_ALT, BG_LOG, BG_SIDEBAR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, TEXT_ACCENT,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_DANGER, ACCENT_WARNING,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD,
    FONT_SMALL, FONT_SMALL_BOLD, FONT_LOG,
    apply_notebook_style,
)


class LearningUI(tk.Frame):
    """Learning Insights view — shows what the system has learned and how it applies it."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG_MAIN)
        self._build_ui()
        self.refresh_all()

    # ------------------------------------------------------------------
    # Layout Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # --- Header ---
        header = tk.Frame(self, bg=BG_MAIN)
        header.pack(fill="x", padx=20, pady=(12, 6))
        tk.Label(header, text="🧠 Adaptive Learning Insights",
                 font=FONT_TITLE, fg=TEXT_PRIMARY, bg=BG_MAIN, anchor="w").pack(fill="x")
        tk.Label(
            header,
            text="The system learns from every run — faster XPath resolution, smarter timeouts, "
                 "and per-student memory that speeds up repeated processing.",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_MAIN, anchor="w", wraplength=900
        ).pack(fill="x", pady=(2, 0))

        # --- KPI Row ---
        self._kpi_frame = tk.Frame(self, bg=BG_MAIN)
        self._kpi_frame.pack(fill="x", padx=20, pady=(8, 4))

        self._kpi_vars = {}
        kpi_defs = [
            ("total_runs",            "Total Runs",       "🔄", ACCENT_PRIMARY),
            ("total_items_processed", "Records Processed","📋", ACCENT_SUCCESS),
            ("total_files_handled",   "Files Handled",    "📁", ACCENT_WARNING),
            ("domain_count",          "Domains Learned",  "🌐", "#a78bfa"),
            ("learned_patterns",      "XPath Patterns",   "🔍", "#38bdf8"),
            ("learned_students",      "Student Insights", "👤", "#fb923c"),
        ]
        for col, (key, label, icon, color) in enumerate(kpi_defs):
            tile = self._make_kpi_tile(self._kpi_frame, icon, label, "—", color)
            tile.grid(row=0, column=col, padx=6, pady=4, sticky="ew")
            self._kpi_frame.columnconfigure(col, weight=1)
            self._kpi_vars[key] = tile._value_label

        # --- Notebook (tabs) ---
        apply_notebook_style()
        nb_frame = tk.LabelFrame(
            self, text=" Detailed Insights ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=8, pady=6
        )
        nb_frame.pack(fill="both", expand=True, padx=20, pady=(4, 10))

        self._notebook = ttk.Notebook(nb_frame)
        self._notebook.pack(fill="both", expand=True, padx=4, pady=4)

        # Tab 1: Run History
        self._tab_history = tk.Frame(self._notebook, bg=BG_CARD)
        self._notebook.add(self._tab_history, text=" 📈 Run History ")
        self._build_history_tab()

        # Tab 2: Domain Profiles
        self._tab_domains = tk.Frame(self._notebook, bg=BG_CARD)
        self._notebook.add(self._tab_domains, text=" 🌐 Domain Profiles ")
        self._build_domains_tab()

        # Tab 3: Student Insights
        self._tab_students = tk.Frame(self._notebook, bg=BG_CARD)
        self._notebook.add(self._tab_students, text=" 👤 Student Insights ")
        self._build_students_tab()

        # Tab 4: DevTools Requirements
        self._tab_devtools = tk.Frame(self._notebook, bg=BG_CARD)
        self._notebook.add(self._tab_devtools, text=" 🛠 DevTools Requirements ")
        self._build_devtools_tab()

        # Tab 5: PDF Presets
        self._tab_pdf = tk.Frame(self._notebook, bg=BG_CARD)
        self._notebook.add(self._tab_pdf, text=" 📄 PDF Preset Learning ")
        self._build_pdf_tab()

        # --- Action Buttons ---
        btn_bar = tk.Frame(self, bg=BG_MAIN)
        btn_bar.pack(fill="x", padx=20, pady=(0, 12))

        tk.Button(
            btn_bar, text="🔄 Refresh", font=FONT_BODY_BOLD,
            bg=ACCENT_PRIMARY, fg="white", bd=0, padx=14, pady=6,
            cursor="hand2", command=self.refresh_all
        ).pack(side="left", padx=(0, 10))

        tk.Button(
            btn_bar, text="⚠️ Clear All Learning Memory", font=FONT_BODY_BOLD,
            bg=ACCENT_DANGER, fg="white", bd=0, padx=14, pady=6,
            cursor="hand2", command=self._confirm_clear
        ).pack(side="right")

    # ------------------------------------------------------------------
    # KPI Tile Factory
    # ------------------------------------------------------------------

    def _make_kpi_tile(self, parent, icon: str, label: str, value: str, color: str) -> tk.Frame:
        tile = tk.Frame(parent, bg=BG_CARD, bd=1, relief="solid", padx=10, pady=8)
        tk.Label(tile, text=icon, font=(FONT_BODY[0], 18), fg=color, bg=BG_CARD).pack()
        val_lbl = tk.Label(tile, text=value, font=(FONT_BODY[0], 16, "bold"),
                           fg=color, bg=BG_CARD)
        val_lbl.pack()
        setattr(tile, "_value_label", val_lbl)
        tk.Label(tile, text=label, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD).pack()
        return tile

    # ------------------------------------------------------------------
    # Run History Tab
    # ------------------------------------------------------------------

    def _build_history_tab(self):
        cols = ("Timestamp", "Module", "Items", "✓ Success", "✗ Failed", "Files", "Duration", "Avg Speed", "Success%")
        col_widths = (140, 130, 55, 70, 60, 55, 70, 80, 70)

        frame = tk.Frame(self._tab_history, bg=BG_CARD)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        style = ttk.Style()
        style.configure("Learning.Treeview",
                         background=BG_LOG, foreground=TEXT_PRIMARY,
                         fieldbackground=BG_LOG, font=FONT_SMALL,
                         rowheight=22)
        style.configure("Learning.Treeview.Heading",
                         background=BG_SIDEBAR, foreground=TEXT_PRIMARY,
                         font=FONT_SMALL_BOLD, relief="flat")
        style.map("Learning.Treeview",
                  background=[("selected", ACCENT_PRIMARY)],
                  foreground=[("selected", "#ffffff")])

        self._history_tree = ttk.Treeview(
            frame, columns=cols, show="headings",
            style="Learning.Treeview", selectmode="browse"
        )
        for col, width in zip(cols, col_widths):
            self._history_tree.heading(col, text=col)
            self._history_tree.column(col, width=width, minwidth=40, anchor="center")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._history_tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._history_tree.xview)
        self._history_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._history_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # Domain Profiles Tab
    # ------------------------------------------------------------------

    def _build_domains_tab(self):
        frame = tk.Frame(self._tab_domains, bg=BG_CARD)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        self._domains_text = tk.Text(
            frame, bg=BG_LOG, fg="#9cdcfe", font=FONT_LOG,
            wrap="none", bd=0, state="disabled"
        )
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._domains_text.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._domains_text.xview)
        self._domains_text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._domains_text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # Student Insights Tab
    # ------------------------------------------------------------------

    def _build_students_tab(self):
        search_frame = tk.Frame(self._tab_students, bg=BG_CARD)
        search_frame.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(search_frame, text="🔍 Search Student ID:",
                 font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter_students())
        tk.Entry(search_frame, textvariable=self._search_var,
                 bg=BG_CARD_ALT, fg=TEXT_PRIMARY, font=FONT_BODY,
                 insertbackground=TEXT_PRIMARY, bd=1, relief="solid"
                 ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        frame = tk.Frame(self._tab_students, bg=BG_CARD)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        cols = ("Student ID", "Key", "Value")
        self._students_tree = ttk.Treeview(
            frame, columns=cols, show="headings",
            style="Learning.Treeview", selectmode="browse"
        )
        for col, width in zip(cols, (120, 200, 300)):
            self._students_tree.heading(col, text=col)
            self._students_tree.column(col, width=width, minwidth=60, anchor="w")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._students_tree.yview)
        self._students_tree.configure(yscrollcommand=vsb.set)
        self._students_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self._all_student_rows = []  # cache for filtering

    # ------------------------------------------------------------------
    # DevTools Requirements Tab
    # ------------------------------------------------------------------

    def _build_devtools_tab(self):
        tk.Label(
            self._tab_devtools,
            text="Sites the system has learned only work with 'Auto-open DevTools' checked. "
                 "When 'confirmed', the automation auto-enables DevTools for that domain even if "
                 "the checkbox is left unchecked (toggle this in Settings).",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD, anchor="w", wraplength=900, justify="left"
        ).pack(fill="x", padx=8, pady=(8, 4))

        frame = tk.Frame(self._tab_devtools, bg=BG_CARD)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        cols = ("Domain", "Status", "Off → Failures", "Off → Successes", "On → Successes", "On → Failures", "Last Updated")
        self._devtools_tree = ttk.Treeview(
            frame, columns=cols, show="headings",
            style="Learning.Treeview", selectmode="browse"
        )
        for col, width in zip(cols, (200, 110, 90, 90, 90, 90, 140)):
            self._devtools_tree.heading(col, text=col)
            self._devtools_tree.column(col, width=width, minwidth=50, anchor="center")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._devtools_tree.yview)
        self._devtools_tree.configure(yscrollcommand=vsb.set)
        self._devtools_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _load_devtools(self):
        for row in self._devtools_tree.get_children():
            self._devtools_tree.delete(row)

        reqs = learning_engine.get_all_devtools_requirements()
        if not reqs:
            self._devtools_tree.insert("", "end", values=(
                "No DevTools patterns learned yet.", "", "", "", "", "", ""
            ))
            return

        for domain, info in sorted(reqs.items()):
            status = "✅ Confirmed" if info.get("confirmed_required") else "— Watching"
            self._devtools_tree.insert("", "end", values=(
                domain,
                status,
                info.get("off_failures", 0),
                info.get("off_successes", 0),
                info.get("on_successes", 0),
                info.get("on_failures", 0),
                info.get("last_updated", "—"),
            ))

    def _build_pdf_tab(self):
        tk.Label(
            self._tab_pdf,
            text="Field-level confidence and tuning history learned from random document reviews.",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD, anchor="w", wraplength=900, justify="left"
        ).pack(fill="x", padx=8, pady=(8, 4))

        frame = tk.Frame(self._tab_pdf, bg=BG_CARD)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        cols = ("Preset Name", "Verified Runs", "Fields Tracked", "Avg Confidence", "Last Tuned Field")
        self._pdf_tree = ttk.Treeview(
            frame, columns=cols, show="headings",
            style="Learning.Treeview", selectmode="browse"
        )
        for col, width in zip(cols, (200, 100, 110, 110, 180)):
            self._pdf_tree.heading(col, text=col)
            self._pdf_tree.column(col, width=width, minwidth=60, anchor="center")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._pdf_tree.yview)
        self._pdf_tree.configure(yscrollcommand=vsb.set)
        self._pdf_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _load_pdf_profiles(self):
        for row in self._pdf_tree.get_children():
            self._pdf_tree.delete(row)

        profiles = learning_engine._data.get("pdf_extractor_profiles", {})
        if not profiles:
            self._pdf_tree.insert("", "end", values=(
                "No PDF profiles learned yet. Use 'Review & Self-Tune' tab.", "", "", "", ""
            ))
            return

        for name, info in sorted(profiles.items()):
            conf_map = info.get("field_confidence", {})
            avg_conf = f"{sum(conf_map.values()) / len(conf_map) * 100:.0f}%" if conf_map else "--"
            history = info.get("tuning_history", [])
            last_tuned = history[-1].get("field", "—") if history else "—"
            self._pdf_tree.insert("", "end", values=(
                name,
                info.get("verified_runs", 0),
                len(conf_map),
                avg_conf,
                last_tuned
            ))

    # ------------------------------------------------------------------
    # Data Loading & Refresh
    # ------------------------------------------------------------------

    def refresh_all(self):
        """Reload all data from the learning engine and repopulate all views."""
        stats = learning_engine.get_summary_stats()

        # Update KPI tiles
        mapping = {
            "total_runs":            stats["total_runs"],
            "total_items_processed": stats["total_items_processed"],
            "total_files_handled":   stats["total_files_handled"],
            "domain_count":          stats["domain_count"],
            "learned_patterns":      stats["learned_patterns"],
            "learned_students":      stats["learned_students"],
        }
        for key, val in mapping.items():
            lbl = self._kpi_vars.get(key)
            if lbl:
                lbl.config(text=str(val))

        self._load_history()
        self._load_domains()
        self._load_students()
        self._load_devtools()
        self._load_pdf_profiles()

    def _load_history(self):
        # Clear
        for row in self._history_tree.get_children():
            self._history_tree.delete(row)

        history = learning_engine.get_run_history()
        for entry in reversed(history):  # Newest first
            self._history_tree.insert("", "end", values=(
                entry.get("timestamp", ""),
                entry.get("module", ""),
                entry.get("total_items", 0),
                entry.get("success_count", 0),
                entry.get("failed_count", 0),
                entry.get("total_files", 0),
                f"{entry.get('duration_sec', 0):.1f}s",
                f"{entry.get('avg_speed_sec_per_item', 0):.2f}s",
                f"{entry.get('success_rate_pct', 0):.1f}%",
            ))

        if not history:
            self._history_tree.insert("", "end", values=(
                "No runs recorded yet.", "", "", "", "", "", "", "", ""
            ))

    def _load_domains(self):
        self._domains_text.config(state="normal")
        self._domains_text.delete("1.0", "end")

        profiles = learning_engine.get_domain_profiles()
        if not profiles:
            self._domains_text.insert("end", "No domain profiles learned yet.\n\nRun an automation to start building knowledge.\n")
        else:
            for domain, data in profiles.items():
                self._domains_text.insert("end", f"╔══ Domain: {domain} ══╗\n")

                # Latencies
                latencies = data.get("latencies", {})
                if latencies:
                    self._domains_text.insert("end", "  ⏱  Learned Latencies:\n")
                    ceiling = learning_engine.get_wait_ceiling()
                    for op, avg in latencies.items():
                        optimal = max(5.0, min(ceiling, round(avg * 2.5, 1)))
                        self._domains_text.insert("end",
                            f"      {op:30s}  avg={avg:.3f}s  →  timeout={optimal}s (ceiling={ceiling:.0f}s)\n"
                        )

                # XPath patterns
                xpaths = data.get("xpaths", {})
                if xpaths:
                    self._domains_text.insert("end", "  🔍 Learned XPath Patterns (sorted by success hits):\n")
                    for key, key_map in sorted(xpaths.items()):
                        self._domains_text.insert("end", f"      [{key}]\n")
                        for xpath, hits in sorted(key_map.items(), key=lambda x: x[1], reverse=True):
                            self._domains_text.insert("end", f"        {hits:>4}x  {xpath}\n")

                self._domains_text.insert("end", "\n")

        self._domains_text.config(state="disabled")

    def _load_students(self):
        self._all_student_rows = []
        for row in self._students_tree.get_children():
            self._students_tree.delete(row)

        insights = learning_engine.get_all_student_insights()
        if not insights:
            self._students_tree.insert("", "end", values=("—", "No insights yet", "Run an automation first"))
            return

        for sid in sorted(insights.keys()):
            for key, val in insights[sid].items():
                self._all_student_rows.append((sid, key, str(val)))

        self._filter_students()

    def _filter_students(self):
        query = self._search_var.get().strip().lower()
        for row in self._students_tree.get_children():
            self._students_tree.delete(row)
        for sid, key, val in self._all_student_rows:
            if not query or query in sid.lower() or query in key.lower() or query in val.lower():
                self._students_tree.insert("", "end", values=(sid, key, val))

    # ------------------------------------------------------------------
    # Clear Memory
    # ------------------------------------------------------------------

    def _confirm_clear(self):
        result = messagebox.askyesno(
            "Clear Learning Memory",
            "⚠️ This will permanently delete all learned XPaths, latencies, student insights, "
            "and run history.\n\nAre you absolutely sure you want to reset the learning memory?",
            icon="warning"
        )
        if result:
            learning_engine.clear_learning_memory()
            self.refresh_all()
            messagebox.showinfo("Done", "Learning memory has been cleared. The system will start fresh on the next run.")
