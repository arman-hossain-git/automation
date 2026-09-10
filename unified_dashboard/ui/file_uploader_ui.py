"""
ui/file_uploader_ui.py
======================
UI frame for the combined File Uploader tool.
Replaces separate uploader views with a single, versatile File Uploader tab.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui.base_tool_frame import BaseToolFrame, QueueTask
from ui.theme import BG_CARD, FONT_BODY_BOLD, FONT_SMALL, TEXT_PRIMARY, TEXT_MUTED
from core.utils import load_credentials, read_links_csv
from core.settings_manager import settings
from core.session_manager import SessionManager
from modules.file_uploader import FileUploader, TARGET_FOLDER_OPTIONS, MODULE_ID


class FileUploaderUI(BaseToolFrame):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="File Uploader",
            description="Uploads local student files into PCR Educator & Orbund Files & Documents portal."
        )

        self.cred_path = ""
        self.links_path = ""
        self.source_dir = ""

        # --- REMEMBERED INPUTS: restore the last-used Credentials File,
        # Links CSV, Source Directory, and Upload Folder from settings.json
        # so the user doesn't have to re-browse for them every time the app
        # is launched (this is separate from -- and in addition to -- the
        # session-recovery flow below, which restores an in-progress RUN's
        # exact state after a crash/abort/shutdown). Paths that no longer
        # exist on disk (moved/deleted file) are silently skipped. ---
        remembered_cred = settings.get("file_uploader_last_cred_path", "")
        remembered_links = settings.get("file_uploader_last_links_path", "")
        remembered_source = settings.get("file_uploader_last_source_dir", "")
        remembered_target = settings.get("file_uploader_last_target_folder", "")

        if remembered_cred and os.path.isfile(remembered_cred):
            self.cred_path = remembered_cred
        if remembered_links and os.path.isfile(remembered_links):
            self.links_path = remembered_links
        if remembered_source and os.path.isdir(remembered_source):
            self.source_dir = remembered_source
            self._log_dir = remembered_source

        self.target_folder_var = tk.StringVar(
            value=remembered_target if remembered_target in TARGET_FOLDER_OPTIONS else TARGET_FOLDER_OPTIONS[0]
        )
        # Default from the global Settings tab instead of always starting unchecked --
        # this was the root cause of runs silently failing when a user checked the box
        # once in Settings but this tool ignored it and reset to False every launch.
        # The automation can still auto-enable this at run time if a site has proven
        # (from past runs) that it needs DevTools even with this box unchecked.
        self.devtools_var = tk.BooleanVar(value=settings.get("auto_open_devtools", False))

        self.uploader = FileUploader()
        self._populate_inputs()
        self._apply_remembered_labels()

        # Ask (once, shortly after the tab is built) whether to resume an
        # unfinished session left over from a prior run that didn't reach
        # the end -- an abort, a crash, or the app/computer being shut down
        # mid-run. Delayed slightly so the window has actually rendered
        # before a modal dialog can pop up over it.
        self.after(400, self._check_for_resumable_session)

    def _populate_inputs(self):
        self.lbl_cred = self.create_file_picker_row(
            self.inputs_container, 0, "Credentials File (.txt):",
            self._set_cred_path,
            file_types=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )

        self.lbl_links = self.create_file_picker_row(
            self.inputs_container, 1, "Student Links CSV (.csv):",
            self._set_links_path,
            file_types=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )

        self.lbl_source = self.create_file_picker_row(
            self.inputs_container, 2, "Source Files Directory:",
            self._set_source_dir,
            is_directory=True
        )

        folder_frame = tk.Frame(self.inputs_container, bg=BG_CARD)
        folder_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        tk.Label(folder_frame, text="Upload Under Folder:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left", padx=(0, 10))

        cmb = ttk.Combobox(
            folder_frame, textvariable=self.target_folder_var,
            values=TARGET_FOLDER_OPTIONS, state="readonly", width=42
        )
        cmb.pack(side="left")
        # Remember the Upload Under Folder choice too, same as the file/dir pickers.
        self.target_folder_var.trace_add("write", self._on_target_folder_changed)

        tk.Checkbutton(
            folder_frame, text="Auto-open DevTools",
            variable=self.devtools_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).pack(side="left", padx=(20, 0))

        tk.Label(
            self.inputs_container,
            text="Defaults from Settings. If a site has previously failed with this off, the "
                 "system will auto-enable it for that site and note it in the log.",
            font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD, anchor="w", justify="left"
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _apply_remembered_labels(self):
        """Reflect any remembered Credentials/CSV/Source Dir paths (loaded in
        __init__ from settings.json) onto the picker labels, since the
        pickers are only populated with 'No path selected' by default."""
        if self.cred_path:
            self.lbl_cred.config(text=os.path.basename(self.cred_path), fg=TEXT_PRIMARY)
        if self.links_path:
            self.lbl_links.config(text=os.path.basename(self.links_path), fg=TEXT_PRIMARY)
        if self.source_dir:
            self.lbl_source.config(text=self.source_dir, fg=TEXT_PRIMARY)

    def _set_cred_path(self, p: str):
        self.cred_path = p
        settings.set("file_uploader_last_cred_path", p)
        settings.save()

    def _set_links_path(self, p: str):
        self.links_path = p
        settings.set("file_uploader_last_links_path", p)
        settings.save()

    def _on_target_folder_changed(self, *_args):
        settings.set("file_uploader_last_target_folder", self.target_folder_var.get())
        settings.save()

    def _check_for_resumable_session(self):
        """If a prior run of this tool didn't reach the end (aborted, crashed,
        or the app/computer was shut down mid-run), ask the user whether to
        resume it. The Credentials File and Links CSV paths are already
        restored (from settings.json, in __init__) regardless of this
        dialog's outcome, since they're remembered generally across every
        launch -- this dialog only concerns the source directory / upload
        folder / completed-students progress tied to THIS specific run."""
        session = SessionManager.get_unfinished_session(MODULE_ID)
        if not session:
            return

        inputs = session.get('user_inputs', {}) or {}
        completed = len(session.get('completed_item_ids', []))
        total = session.get('total_items', 0)
        session_source_dir = inputs.get('source_dir', '')
        session_target_folder = inputs.get('target_folder_name', '')

        resume = messagebox.askyesno(
            "Resume Previous Session?",
            "An unfinished File Uploader session was found from a previous run "
            "that didn't finish (it was aborted, crashed, or the app/computer "
            f"was shut down mid-run).\n\n"
            f"Progress: {completed} of {total} students already completed.\n"
            f"Source directory: {session_source_dir or '(unknown)'}\n"
            f"Upload folder: {session_target_folder or '(Files & Documents root)'}\n\n"
            "Resume this session? Already-completed students will be skipped "
            "automatically.\n\n"
            "Choose 'No' to discard it and start a completely new run."
        )

        if resume:
            if session_source_dir and os.path.isdir(session_source_dir):
                self.source_dir = session_source_dir
                self._log_dir = session_source_dir
                self.lbl_source.config(text=session_source_dir, fg=TEXT_PRIMARY)
                settings.set("file_uploader_last_source_dir", session_source_dir)
            if session_target_folder and session_target_folder in TARGET_FOLDER_OPTIONS:
                self.target_folder_var.set(session_target_folder)
            elif not session_target_folder:
                self.target_folder_var.set(TARGET_FOLDER_OPTIONS[0])
            settings.save()
            self.update_status(
                f"Previous session found ({completed}/{total} done). "
                "Credentials, CSV, Source Dir & Folder are pre-filled — click Start to resume."
            )
            self.log("info", f"[SESSION] Found resumable session — {completed}/{total} students "
                              f"already completed. All inputs pre-filled; click Start to resume.")
        else:
            SessionManager.clear_session(MODULE_ID)
            self.log("info", "[SESSION] Discarded previous unfinished session; starting fresh.")

    def _set_source_dir(self, p: str):
        self.source_dir = p
        self._log_dir = p  # wire for Open Log / Open Folder buttons
        settings.set("file_uploader_last_source_dir", p)
        settings.save()

    def get_task_from_inputs(self, silent: bool = False) -> QueueTask:
        if not self.cred_path or not self.links_path or not self.source_dir:
            if not silent:
                messagebox.showerror("Missing Fields", "Please select Credentials, Links CSV, and Source Directory.")
            return None

        config = load_credentials(self.cred_path, required_keys=['username', 'password', 'login_url', 'login_btn_xpath'])
        if not config:
            if not silent:
                messagebox.showerror("Invalid Credentials File", "Credentials file must include username, password, login_url, and login_btn_xpath.")
            return None

        students = read_links_csv(self.links_path)
        if not students:
            if not silent:
                messagebox.showerror("Invalid Links CSV", "Could not parse any valid student links from the CSV file.")
            return None

        target_folder = self.target_folder_var.get()
        label = (
            f"Creds: {os.path.basename(self.cred_path)} | "
            f"CSV: {os.path.basename(self.links_path)} ({len(students)} recs) | "
            f"Source: {os.path.basename(self.source_dir)} | "
            f"Folder: {target_folder}"
        )

        inputs = {
            "config": config,
            "students": students,
            "source_dir": self.source_dir,
            "target_folder": target_folder,
            "devtools": self.devtools_var.get(),
            "cred_path": self.cred_path,
            "links_path": self.links_path,
        }

        import uuid
        return QueueTask(task_id=str(uuid.uuid4())[:8], label=label, inputs=inputs)

    def run_single_task(self, task: QueueTask) -> bool:
        inputs = task.inputs
        self._log_dir = inputs["source_dir"]
        self.log("info", f"Running File Uploader task: {task.label}")

        try:
            self.uploader.run(
                config=inputs["config"],
                students=inputs["students"],
                source_dir=inputs["source_dir"],
                target_folder_name=inputs["target_folder"],
                devtools=inputs["devtools"],
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log
            )
            return not self.abort_requested
        except Exception as e:
            import traceback
            self.log("error", f"Error executing task {task.label}: {e}")
            self.log("debug", traceback.format_exc())
            return False

    def verify_single_task(self, task: QueueTask):
        inputs = task.inputs
        source_dir = inputs.get("source_dir", "")
        config = inputs.get("config", None)
        students = inputs.get("students", None)
        target_folder = inputs.get("target_folder", "")
        devtools = inputs.get("devtools", False)

        if not source_dir:
            return

        if config and students:
            self.log("info", f"[VERIFY] Starting live portal verification and audit for: {task.label} ({len(students)} students)...")
            res = self.uploader.verify_and_audit(
                config=config,
                students=students,
                source_dir=source_dir,
                target_folder_name=target_folder,
                devtools=devtools,
                on_progress=self._handle_progress,
                abort_flag=lambda: self.abort_requested,
                log=self.log
            )
            def _show_audit_summary():
                messagebox.showinfo(
                    "Audit & Verification Complete",
                    f"Task: {task.label}\n\n"
                    f"Students Audited: {res['students_audited']}\n"
                    f"Correctly Placed Files: {res['correct_verified']}\n"
                    f"Misplaced Files Deleted: {res['misplaced_deleted']}\n"
                    f"Missing Files Re-uploaded: {res['reuploaded']}"
                )
            self.after(0, _show_audit_summary)
        else:
            res = self.uploader.verify(source_dir, log=self.log)
            self.log("info", f"[VERIFY] Task: {task.label} -> Uploaded: {res['uploaded']}, Skipped (unsupported): {res['skipped_unsupported']}, Skipped (malformed): {res['skipped_malformed']}, Failed: {res['failed']}")
            def _show_upload_summary():
                messagebox.showinfo(
                    "Upload Summary",
                    f"Uploaded: {res['uploaded']}\n"
                    f"Skipped (unsupported format): {res['skipped_unsupported']}\n"
                    f"Skipped (malformed/invalid file name): {res['skipped_malformed']}\n"
                    f"Failed: {res['failed']}\n"
                    f"Students logged: {res['students']}"
                )
            self.after(0, _show_upload_summary)

    def _handle_progress(self, data: dict):
        t = data.get('type')

        if t == 'init_totals':
            # Pre-scan totals — emitted once before any student starts
            self.update_kpis(
                total_students=data.get('total_students', self.total_students),
                files_total=data.get('total_files', self.files_total),
                total_bytes=data.get('total_bytes', 0),
            )
            self.set_progress(0, max(1, data.get('total_students', 1)))

        elif t == 'total':
            # Legacy fallback for older event format
            self.set_progress(0, data['total'])
            self.update_kpis(total_students=data['total'])

        elif t == 'student_start':
            self.update_status(
                f"Uploading for Student {data['student_id']} "
                f"({data['idx'] + 1}/{self.total_students})..."
            )
            self.set_progress(data['idx'] + 1)
            self.update_kpis(
                students_traversed=data['idx'],
                current_student_id=data['student_id'],
                bytes_done=data.get('bytes_done', self.bytes_done),
                total_bytes=data.get('total_bytes', self.total_bytes),
            )

        elif t == 'category':
            self.update_kpis(current_category=data['name'])

        elif t == 'files_total':
            self.update_kpis(
                files_total=data.get('total', self.files_total),
                bytes_done=data.get('bytes_done', self.bytes_done),
                total_bytes=data.get('total_bytes', self.total_bytes),
            )

        elif t == 'file_start':
            # Optional: emitted when a file upload begins (for in-flight display)
            self.update_kpis(
                current_file_name=data.get('file_name', '-'),
                current_file_size=data.get('file_size', 0),
                bytes_done=data.get('bytes_done', self.bytes_done),
                total_bytes=data.get('total_bytes', self.total_bytes),
            )

        elif t == 'file_done':
            self.update_kpis(
                files_done=data.get('files_done', self.files_done + 1),
                current_file_name=data.get('file_name', self.current_file_name),
                current_file_size=data.get('file_size', 0),
                bytes_done=data.get('bytes_done', self.bytes_done),
                total_bytes=data.get('total_bytes', self.total_bytes),
            )

        elif t == 'student_done':
            self.update_kpis(
                students_traversed=data.get('students_traversed', self.students_traversed + 1),
                bytes_done=data.get('bytes_done', self.bytes_done),
                total_bytes=data.get('total_bytes', self.total_bytes),
            )

    def on_verify_clicked(self):
        tasks_to_verify = [t for t in self.queue_tasks if t.status in ("Completed", "Pending")]
        task = self.get_task_from_inputs(silent=True)
        if tasks_to_verify or task:
            super().on_verify_clicked()
            return

        if self.source_dir:
            res = self.uploader.verify(self.source_dir, log=self.log)
            messagebox.showinfo(
                "Upload Summary",
                f"Uploaded: {res['uploaded']}\n"
                f"Skipped (unsupported format): {res['skipped_unsupported']}\n"
                f"Skipped (malformed/invalid file name): {res['skipped_malformed']}\n"
                f"Failed: {res['failed']}\n"
                f"Students logged: {res['students']}"
            )
        else:
            messagebox.showwarning("Warning", "Select Source Directory, Credentials, and Student CSV first.")
