"""
ui/file_exporter_ui.py
======================
UI frame for the Universal File Exporter.

All configuration is dynamic — the system learns site structure after every
run.  The user only needs to set:
  1. Credentials file
  2. Record links CSV
  3. Save directory
  4. (Optional) Folder hierarchy template
  5. (Optional) Fine-tuning overrides (segment scope, column index, filters)
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import (
    BG_CARD, BG_CARD_ALT, BG_MAIN, BG_LOG,
    FONT_BODY_BOLD, FONT_BODY, FONT_SMALL,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, TEXT_ACCENT,
    ACCENT_PRIMARY, ACCENT_SUCCESS,
)
from core.utils import load_credentials, read_links_csv
from core.settings_manager import settings
from modules.file_exporter import FileExporter

DEFAULT_TEMPLATE = "{studentId}/{segment}/{category}"


class FileExporterUI(BaseToolFrame):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="File Exporter",
            description=(
                "Dynamically extracts and downloads files from any website. "
                "The system learns site structure after every run — no preset required."
            )
        )

        self.cred_path = ""
        self.links_path = ""
        self.download_dir = ""

        self.folder_template_var = tk.StringVar(value=DEFAULT_TEMPLATE)
        self.segment_names_var   = tk.StringVar(value="")
        self.category_col_var    = tk.StringVar(value="2")
        self.allow_dups_var      = tk.BooleanVar(value=True)
        self.filter_ext_var      = tk.StringVar(value="")
        self.filter_inc_var      = tk.StringVar(value="")
        self.filter_exc_var      = tk.StringVar(value="")
        # Defaults from the global Settings tab. The automation can still auto-enable
        # this at run time if a site has proven (from past runs) that it needs
        # DevTools even with this box unchecked.
        self.devtools_var        = tk.BooleanVar(value=settings.get("auto_open_devtools", False))
        self.workers_var         = tk.IntVar(value=1)

        self.exporter = FileExporter()
        self._populate_inputs()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _populate_inputs(self):
        ic = self.inputs_container

        # ── 1. File pickers ────────────────────────────────────────────
        self.create_file_picker_row(
            ic, 0, "Credentials File (.txt):", self._set_cred_path,
            file_types=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        self.create_file_picker_row(
            ic, 1, "Record Links CSV (.csv):",
            lambda p: setattr(self, "links_path", p),
            file_types=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        self.create_file_picker_row(
            ic, 2, "Save Files Directory:", self._set_download_dir,
            is_directory=True
        )

        # Thin divider
        tk.Frame(ic, bg=BG_CARD_ALT, height=1).grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(10, 8))

        # ── 2. Folder Hierarchy Template ───────────────────────────────
        tmpl_group = tk.LabelFrame(
            ic, text=" 📁 Folder Hierarchy Template ",
            font=FONT_BODY_BOLD, fg=ACCENT_PRIMARY, bg=BG_CARD,
            bd=1, relief="solid", padx=12, pady=10
        )
        tmpl_group.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        tmpl_group.columnconfigure(1, weight=1)

        # Entry row
        tk.Label(tmpl_group, text="Template:", font=FONT_BODY_BOLD,
                 fg=TEXT_PRIMARY, bg=BG_CARD).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)

        self._tmpl_entry = tk.Entry(
            tmpl_group, textvariable=self.folder_template_var,
            bg="#2b2b2b", fg="#ffffff", insertbackground="white",
            font=FONT_BODY, bd=1, relief="solid", width=50
        )
        self._tmpl_entry.grid(row=0, column=1, sticky="ew", pady=3)

        tk.Label(
            tmpl_group,
            text="Placeholders:  {studentId}  {segment}  {category}  {date}  {ext}",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD
        ).grid(row=1, column=1, sticky="w", pady=(0, 8))

        # ── 3. Segment & Scraper Settings ──────────────────────────────
        seg_group = tk.LabelFrame(
            ic, text=" ⚙️ Scraper Settings (Optional — system learns these automatically) ",
            font=FONT_BODY_BOLD, fg=TEXT_SECONDARY, bg=BG_CARD,
            bd=1, relief="solid", padx=12, pady=10
        )
        seg_group.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        seg_group.columnconfigure(1, weight=1)
        seg_group.columnconfigure(3, weight=1)

        # Row 0: Segment Names | Category Column
        tk.Label(seg_group, text="Segment Name(s):", font=FONT_BODY_BOLD,
                 fg=TEXT_PRIMARY, bg=BG_CARD).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        tk.Entry(seg_group, textvariable=self.segment_names_var, width=28,
                 bg="#2b2b2b", fg="#ffffff", insertbackground="white", bd=1, relief="solid"
                 ).grid(row=0, column=1, sticky="ew", padx=(0, 20), pady=3)
        tk.Label(seg_group, text="RadDock widget title(s), comma-separated  e.g. Summary, Actions",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD
                 ).grid(row=1, column=1, sticky="w", padx=(0, 20), pady=(0, 6))

        tk.Label(seg_group, text="Category Column:", font=FONT_BODY_BOLD,
                 fg=TEXT_PRIMARY, bg=BG_CARD).grid(row=0, column=2, sticky="w", padx=(0, 8), pady=3)
        tk.Spinbox(seg_group, from_=1, to=20, width=6, textvariable=self.category_col_var,
                   bg="#2b2b2b", fg="#ffffff", buttonbackground="#3c3c3c", bd=1, relief="solid"
                   ).grid(row=0, column=3, sticky="w", pady=3)
        tk.Label(seg_group, text="1-based table column index",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD
                 ).grid(row=1, column=3, sticky="w", pady=(0, 6))

        # Row 2: Allow Duplicates
        tk.Checkbutton(
            seg_group, text="Allow duplicate filenames  (suffixes __2, __3 …)",
            variable=self.allow_dups_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=3)

        # Row 3: Auto-open DevTools
        tk.Checkbutton(
            seg_group, text="Auto-open DevTools",
            variable=self.devtools_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=3)
        tk.Label(
            seg_group,
            text="Defaults from Settings. If a site has previously failed with this off, the "
                 "system auto-enables it for that site and notes it in the log.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w", justify="left"
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(0, 4))

        # Row 5: Parallel Workers
        tk.Label(seg_group, text="Parallel Workers:", font=FONT_BODY_BOLD,
                 fg=TEXT_PRIMARY, bg=BG_CARD).grid(row=5, column=0, sticky="w", padx=(0, 8), pady=3)
        tk.Spinbox(seg_group, from_=1, to=50, width=6, textvariable=self.workers_var,
                   bg="#2b2b2b", fg="#ffffff", buttonbackground="#3c3c3c", bd=1, relief="solid"
                   ).grid(row=5, column=1, sticky="w", pady=3)
        tk.Label(seg_group, text="(1 = sequential, 2–50 = parallel Chrome instances)",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD
                 ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(0, 4))

        # ── 4. File Filters ────────────────────────────────────────────
        filt_group = tk.LabelFrame(
            ic, text=" 🔎 File Filters (Optional) ",
            font=FONT_BODY_BOLD, fg=TEXT_SECONDARY, bg=BG_CARD,
            bd=1, relief="solid", padx=12, pady=10
        )
        filt_group.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        filt_group.columnconfigure(1, weight=1)
        filt_group.columnconfigure(3, weight=1)

        def _filt_row(parent, row, label, var, hint):
            tk.Label(parent, text=label, font=FONT_BODY_BOLD,
                     fg=TEXT_PRIMARY, bg=BG_CARD).grid(row=row*2, column=0, sticky="w", padx=(0, 8), pady=3)
            tk.Entry(parent, textvariable=var, width=26,
                     bg="#2b2b2b", fg="#ffffff", insertbackground="white", bd=1, relief="solid"
                     ).grid(row=row*2, column=1, sticky="ew", padx=(0, 20), pady=3)
            tk.Label(parent, text=hint, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD
                     ).grid(row=row*2+1, column=1, sticky="w", padx=(0, 20), pady=(0, 5))

        _filt_row(filt_group, 0, "File Extensions:", self.filter_ext_var,
                  "comma-separated  e.g. pdf, docx  (blank = download all types)")
        _filt_row(filt_group, 1, "Filename Contains:", self.filter_inc_var,
                  "only download files whose name contains this text")

        tk.Label(filt_group, text="Filename Excludes:", font=FONT_BODY_BOLD,
                 fg=TEXT_PRIMARY, bg=BG_CARD).grid(row=4, column=0, sticky="w", padx=(0, 8), pady=3)
        tk.Entry(filt_group, textvariable=self.filter_exc_var, width=26,
                 bg="#2b2b2b", fg="#ffffff", insertbackground="white", bd=1, relief="solid"
                 ).grid(row=4, column=1, sticky="ew", padx=(0, 20), pady=3)
        tk.Label(filt_group, text="skip files whose name contains this text",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD
                 ).grid(row=5, column=1, sticky="w", padx=(0, 20), pady=(0, 5))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_cred_path(self, p: str):
        self.cred_path = p

    def _set_download_dir(self, p: str):
        self.download_dir = p
        self._log_dir = p  # BaseToolFrame uses this for Open Log / Open Folder

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_task_from_inputs(self, silent: bool = False) -> QueueTask | None:
        if not self.cred_path or not self.links_path or not self.download_dir:
            if not silent:
                messagebox.showerror(
                    "Missing Fields",
                    "Please select:\n  • Credentials file\n  • Record Links CSV\n  • Save Files Directory"
                )
            return None

        config = load_credentials(self.cred_path, required_keys=["username", "password", "login_url"])
        if not config:
            if not silent:
                messagebox.showerror(
                    "Invalid Credentials",
                    "Credentials file must contain:\n  username=\n  password=\n  login_url="
                )
            return None

        students = read_links_csv(self.links_path)
        if not students:
            if not silent:
                messagebox.showerror(
                    "Invalid Links CSV",
                    "Could not parse any valid records.\nEnsure the CSV has 'studentId' and 'link' columns."
                )
            return None

        try:
            cat_col = int(self.category_col_var.get())
        except ValueError:
            cat_col = 2

        filters = {}
        if self.filter_ext_var.get().strip():
            filters["type_include"] = [e.strip().lower() for e in self.filter_ext_var.get().split(",") if e.strip()]
        if self.filter_inc_var.get().strip():
            filters["name_include"] = [n.strip().lower() for n in self.filter_inc_var.get().split(",") if n.strip()]
        if self.filter_exc_var.get().strip():
            filters["name_exclude"] = [n.strip().lower() for n in self.filter_exc_var.get().split(",") if n.strip()]

        template = self.folder_template_var.get().strip() or "{studentId}/{segment}/{category}"
        seg_str = self.segment_names_var.get().strip()

        n_workers = max(1, self.workers_var.get())
        workers_tag = f" | {n_workers} workers" if n_workers > 1 else ""
        label = (
            f"Creds: {os.path.basename(self.cred_path)} | "
            f"CSV: {os.path.basename(self.links_path)} ({len(students)} recs){workers_tag} | "
            f"Dir: {os.path.basename(self.download_dir)}"
        )

        inputs = {
            "config": config,
            "students": students,
            "download_dir": self.download_dir,
            "template": template,
            "allow_dups": self.allow_dups_var.get(),
            "seg_str": seg_str,
            "cat_col": cat_col,
            "filters": filters,
            "devtools": self.devtools_var.get(),
            "cred_path": self.cred_path,
            "links_path": self.links_path,
            "n_workers": n_workers,
        }

        import uuid
        return QueueTask(task_id=str(uuid.uuid4())[:8], label=label, inputs=inputs)

    def run_single_task(self, task: QueueTask) -> bool:
        inputs = task.inputs
        self._log_dir = inputs["download_dir"]
        n_workers = max(1, inputs.get("n_workers", 1))
        self.log("info", f"Running File Exporter task ({'parallel ×' + str(n_workers) if n_workers > 1 else 'sequential'}): {task.label}")

        try:
            common_kwargs = dict(
                config=inputs["config"],
                students=inputs["students"],
                download_dir=inputs["download_dir"],
                folder_hierarchy_template=inputs["template"],
                allow_duplicates=inputs["allow_dups"],
                segment_names_str=inputs["seg_str"],
                category_column_index=inputs["cat_col"],
                filters=inputs["filters"],
                devtools=inputs["devtools"],
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log
            )
            if n_workers > 1:
                self.exporter.run_parallel(n_workers=n_workers, **common_kwargs)
            else:
                self.exporter.run(**common_kwargs)
            return not self.abort_requested
        except Exception as e:
            import traceback
            self.log("error", f"Error executing task {task.label}: {e}")
            self.log("debug", traceback.format_exc())
            return False

    def verify_single_task(self, task: QueueTask):
        download_dir = task.inputs.get("download_dir", "")
        if not download_dir:
            return

        res = self.exporter.verify(download_dir, log=self.log)
        self.log("info", f"[VERIFY] Task: {task.label} -> Verified: {res['verified']} files, Missing folders: {res['folders_missing']}, Missing files: {len(res['missing'])}")

    def _handle_progress(self, data: dict):
        t = data.get("type")
        if t == "total":
            self.set_progress(0, data["total"])
            self.update_kpis(total_students=data["total"])
        elif t == "student_start":
            sid, idx = data["student_id"], data["idx"]
            self.update_status(f"Record {sid}  ({idx+1}/{self.total_students})…")
            self.set_progress(idx + 1)
            self.update_kpis(students_traversed=idx, current_student_id=sid)
        elif t == "files_total":
            self.update_kpis(files_total=data["total"])
        elif t == "file_done":
            self.update_kpis(files_done=data["files_done"])
        elif t == "student_done":
            self.update_kpis(students_traversed=data.get("students_traversed", self.students_traversed + 1))

    def on_verify_clicked(self):
        if not self.download_dir:
            messagebox.showwarning("Warning", "Select Save Files Directory first.")
            return

        res = self.exporter.verify(self.download_dir, log=self.log)

        detail_win = tk.Toplevel(self)
        detail_win.title("Verification Report")
        detail_win.geometry("700x480")
        detail_win.configure(bg=BG_MAIN)

        summary_text = (
            f"  Verified files on disk:    {res['verified']}\n"
            f"  Missing folder count:      {res['folders_missing']}\n"
            f"  Missing file entries:      {len(res['missing'])}\n"
        )
        tk.Label(detail_win, text="Verification Report", font=FONT_BODY_BOLD,
                 fg=TEXT_PRIMARY, bg=BG_MAIN, anchor="w").pack(fill="x", padx=20, pady=(12, 4))
        tk.Label(detail_win, text=summary_text, font=FONT_BODY,
                 fg=TEXT_SECONDARY, bg=BG_MAIN, justify="left").pack(fill="x", padx=20, pady=(0, 4))

        if res["missing"]:
            tk.Label(detail_win, text="Missing Entries:", font=FONT_BODY_BOLD,
                     fg=TEXT_PRIMARY, bg=BG_MAIN, anchor="w").pack(fill="x", padx=20)
            txt = scrolledtext.ScrolledText(
                detail_win, height=15, wrap="word",
                bg="#1e1e1e", fg="#ef4444", font=("Consolas", 9)
            )
            txt.pack(fill="both", expand=True, padx=20, pady=(4, 10))
            for entry in res["missing"]:
                txt.insert("end", entry + "\n")
            txt.configure(state="disabled")
        else:
            tk.Label(detail_win, text="✅  All logged files found on disk.",
                     font=FONT_BODY_BOLD, fg="#22c55e", bg=BG_MAIN).pack(pady=20)

        tk.Button(
            detail_win, text="Close", command=detail_win.destroy,
            bg=ACCENT_PRIMARY, fg="white", font=FONT_BODY_BOLD,
            bd=0, padx=16, pady=6, cursor="hand2"
        ).pack(pady=(0, 12))
