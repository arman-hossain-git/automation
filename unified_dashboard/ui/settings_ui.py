"""
ui/settings_ui.py
=================
System Settings View.
Allows users to configure global automation parameters, Chrome driver options,
logging verbosity, color scheme theme (System / Dark / Light), and system behavior.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from ui.theme import (
    BG_MAIN, BG_CARD, BG_CARD_ALT,
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT_PRIMARY, ACCENT_SUCCESS,
    FONT_TITLE, FONT_SUBTITLE, FONT_BODY, FONT_BODY_BOLD,
    apply_theme, update_widget_tree_colors
)
from core.settings_manager import settings

THEME_MAP = {
    "Same as Local PC (System)": "system",
    "Dark Mode": "dark",
    "Light Mode": "light",
}
REV_THEME_MAP = {v: k for k, v in THEME_MAP.items()}

INACTIVITY_ACTION_MAP = {
    "Close Application (Safely exit)": "close_app",
    "Pause Queue (Wait for user to resume)": "pause_queue",
    "Proceed with Next Task (Continue automatically)": "proceed_next",
    "Stop Automation (Abort remaining tasks)": "stop_automation",
}
REV_INACTIVITY_ACTION_MAP = {v: k for k, v in INACTIVITY_ACTION_MAP.items()}


class SettingsUI(tk.Frame):
    """System Settings View Panel."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG_MAIN)

        self.headless_var = tk.BooleanVar(value=settings.get("headless"))
        self.devtools_var = tk.BooleanVar(value=settings.get("auto_open_devtools"))
        self.timeout_var = tk.StringVar(value=str(settings.get("download_timeout")))
        self.max_wait_var = tk.StringVar(value=str(settings.get("max_action_wait_seconds")))
        self.retries_var = tk.StringVar(value=str(settings.get("max_retries")))
        self.retry_wait_var = tk.StringVar(value=str(settings.get("retry_wait_seconds", 5)))
        self.log_level_var = tk.StringVar(value=settings.get("log_level"))
        self.default_dir_var = tk.StringVar(value=settings.get("default_download_dir"))
        self.confirm_abort_var = tk.BooleanVar(value=settings.get("confirm_on_abort"))
        self.auto_apply_devtools_var = tk.BooleanVar(value=settings.get("auto_apply_learned_devtools", True))
        self.inter_task_timeout_var = tk.StringVar(value=str(settings.get("inter_task_timeout_minutes", 15)))
        cur_inact = settings.get("inter_task_inactivity_action", "close_app")
        self.inactivity_action_var = tk.StringVar(value=REV_INACTIVITY_ACTION_MAP.get(cur_inact, "Close Application (Safely exit)"))

        cur_theme_code = settings.get("theme", "system")
        self.theme_var = tk.StringVar(value=REV_THEME_MAP.get(cur_theme_code, "Same as Local PC (System)"))

        self._build_ui()

    def _build_ui(self):
        # 1. Header banner
        header_frame = tk.Frame(self, bg=BG_MAIN)
        header_frame.pack(fill="x", padx=20, pady=(15, 10))

        tk.Label(
            header_frame, text="⚙️ System Settings & Preferences",
            font=FONT_TITLE, fg=TEXT_PRIMARY, bg=BG_MAIN, anchor="w"
        ).pack(fill="x")

        tk.Label(
            header_frame,
            text="Configure Chrome driver execution, color scheme themes, timeouts, logging verbosity, and directories.",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_MAIN, anchor="w"
        ).pack(fill="x", pady=(2, 0))

        # 2. Pinned Action Buttons Bar at BOTTOM (Guarantees Save / Reset buttons are 100% visible)
        btn_bar = tk.Frame(self, bg=BG_MAIN)
        btn_bar.pack(side="bottom", fill="x", padx=20, pady=12)

        tk.Button(
            btn_bar, text="💾 Save Preferences", font=FONT_BODY_BOLD,
            bg=ACCENT_SUCCESS, fg="white", bd=0, padx=18, pady=7, cursor="hand2",
            command=self._save_settings
        ).pack(side="left", padx=(0, 10))

        tk.Button(
            btn_bar, text="🔄 Reset to Defaults", font=FONT_BODY_BOLD,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=18, pady=7, cursor="hand2",
            command=self._reset_settings
        ).pack(side="left")

        # 3. Main Scrollable Preferences Area (Fills remaining height above btn_bar)
        pref_wrapper = tk.Frame(self, bg=BG_MAIN)
        pref_wrapper.pack(side="top", fill="both", expand=True, padx=20, pady=(5, 0))

        canvas = tk.Canvas(pref_wrapper, bg=BG_MAIN, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(pref_wrapper, orient="vertical", command=canvas.yview)

        container_scroll = tk.Frame(canvas, bg=BG_MAIN)

        def _update_scroll(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            container_scroll.update_idletasks()
            content_h = container_scroll.winfo_reqheight()
            canvas_h = canvas.winfo_height()
            if content_h > canvas_h and canvas_h > 1:
                if not scrollbar.winfo_ismapped():
                    scrollbar.pack(side="right", fill="y")
            else:
                if scrollbar.winfo_ismapped():
                    scrollbar.pack_forget()

        def _on_canvas_config(event):
            canvas.itemconfig(canvas_win, width=event.width)
            _update_scroll()

        canvas_win = canvas.create_window((0, 0), window=container_scroll, anchor="nw")
        container_scroll.bind("<Configure>", _update_scroll)
        canvas.bind("<Configure>", _on_canvas_config)
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_mw(e):
            if container_scroll.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mw))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side="left", fill="both", expand=True)

        # Settings Card Container
        container = tk.LabelFrame(
            container_scroll, text=" Automation Engine Preferences ", font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY, bg=BG_CARD, bd=1, relief="solid", padx=20, pady=15
        )
        container.pack(fill="x", expand=True)

        # 1. Chrome Options
        tk.Label(container, text="Browser Engine Settings", font=FONT_SUBTITLE, fg=ACCENT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        tk.Checkbutton(
            container, text="Run Chrome in Headless Mode (invisible browser)",
            variable=self.headless_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)

        tk.Checkbutton(
            container, text="Auto-open DevTools Inspector on browser launch",
            variable=self.devtools_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        tk.Checkbutton(
            container,
            text="Auto-apply DevTools for sites the system has learned need it",
            variable=self.auto_apply_devtools_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        tk.Label(
            container,
            text="If a site has previously failed with DevTools unchecked and succeeded with it checked,\n"
                 "the automation applies it automatically on future runs and notes this in the log.",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD, justify="left", anchor="w"
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # 2. Timeouts & Retries
        tk.Label(container, text="Page Load & Download Timeout (sec):", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=5, column=0, sticky="w", pady=6)
        tk.Spinbox(container, from_=5, to=300, width=8, textvariable=self.timeout_var, bg=BG_CARD_ALT, fg=TEXT_PRIMARY).grid(row=5, column=1, sticky="w", padx=10, pady=6)

        tk.Label(container, text="Maximum Wait Time per Action (sec):", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=6, column=0, sticky="w", pady=6)
        tk.Spinbox(container, from_=5, to=120, width=8, textvariable=self.max_wait_var, bg=BG_CARD_ALT, fg=TEXT_PRIMARY).grid(row=6, column=1, sticky="w", padx=10, pady=6)

        tk.Label(container, text="Max Automation Action / Row Retries:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=7, column=0, sticky="w", pady=6)
        tk.Spinbox(container, from_=1, to=20, width=8, textvariable=self.retries_var, bg=BG_CARD_ALT, fg=TEXT_PRIMARY).grid(row=7, column=1, sticky="w", padx=10, pady=6)

        tk.Label(container, text="Retry Wait Delay on Error (sec):", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=8, column=0, sticky="w", pady=6)
        tk.Spinbox(container, from_=1, to=120, width=8, textvariable=self.retry_wait_var, bg=BG_CARD_ALT, fg=TEXT_PRIMARY).grid(row=8, column=1, sticky="w", padx=10, pady=6)

        tk.Label(container, text="Inter-Task Inactivity Timeout (minutes):", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=9, column=0, sticky="w", pady=6)
        tk.Spinbox(container, from_=1, to=120, width=8, textvariable=self.inter_task_timeout_var, bg=BG_CARD_ALT, fg=TEXT_PRIMARY).grid(row=9, column=1, sticky="w", padx=10, pady=6)

        tk.Label(container, text="Action on Inactivity Timeout:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=10, column=0, sticky="w", pady=6)
        cmb_inactivity = ttk.Combobox(
            container, textvariable=self.inactivity_action_var,
            values=list(INACTIVITY_ACTION_MAP.keys()),
            state="readonly", width=38
        )
        cmb_inactivity.grid(row=10, column=1, sticky="w", padx=10, pady=6)

        tk.Label(
            container,
            text="Action to execute when inter-task countdown reaches 0:00 (e.g. user away from desk).",
            font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD, justify="left", anchor="w"
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # 3. Appearance & Color Scheme (Live Theme Switching)
        tk.Label(container, text="System Color Scheme / Theme:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=12, column=0, sticky="w", pady=6)
        cmb_theme = ttk.Combobox(
            container, textvariable=self.theme_var,
            values=["Same as Local PC (System)", "Dark Mode", "Light Mode"],
            state="readonly", width=26
        )
        cmb_theme.grid(row=12, column=1, sticky="w", padx=10, pady=6)
        cmb_theme.bind("<<ComboboxSelected>>", self._on_theme_change)

        # 4. Logging & General
        tk.Label(container, text="Log Console Verbosity Level:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=13, column=0, sticky="w", pady=6)
        cmb_log = ttk.Combobox(container, textvariable=self.log_level_var, values=["DEBUG", "INFO", "WARNING", "ERROR"], state="readonly", width=12)
        cmb_log.grid(row=13, column=1, sticky="w", padx=10, pady=6)

        tk.Checkbutton(
            container, text="Ask for confirmation before aborting running automation",
            variable=self.confirm_abort_var, font=FONT_BODY_BOLD,
            fg=TEXT_PRIMARY, bg=BG_CARD, selectcolor=BG_CARD, activebackground=BG_CARD
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=6)

        # 5. Default Download Directory
        tk.Label(container, text="Default Download Directory:", font=FONT_BODY_BOLD, fg=TEXT_PRIMARY, bg=BG_CARD, anchor="w").grid(row=15, column=0, sticky="w", pady=8)
        dir_frame = tk.Frame(container, bg=BG_CARD)
        dir_frame.grid(row=15, column=1, sticky="w", padx=10, pady=8)

        tk.Entry(dir_frame, textvariable=self.default_dir_var, width=40, bg=BG_CARD_ALT, fg=TEXT_PRIMARY, insertbackground="white").pack(side="left", padx=(0, 8))
        tk.Button(dir_frame, text="Browse...", font=FONT_BODY, bg=BG_CARD_ALT, fg=TEXT_PRIMARY, bd=0, padx=8, pady=2, command=self._browse_default_dir).pack(side="left")

        container.columnconfigure(0, weight=0)
        container.columnconfigure(1, weight=1)

    def _on_theme_change(self, event=None):
        """Live theme preview listener when dropdown selection changes."""
        theme_code = THEME_MAP.get(self.theme_var.get(), "system")
        apply_theme(theme_code)
        update_widget_tree_colors(self.winfo_toplevel(), theme_code)

    def _browse_default_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.default_dir_var.set(path)

    def _save_settings(self):
        try:
            settings.set("headless", self.headless_var.get())
            settings.set("auto_open_devtools", self.devtools_var.get())
            settings.set("download_timeout", int(self.timeout_var.get()))
            settings.set("max_action_wait_seconds", int(self.max_wait_var.get()))
            settings.set("max_retries", int(self.retries_var.get()))
            settings.set("retry_wait_seconds", int(self.retry_wait_var.get()))
            settings.set("log_level", self.log_level_var.get())
            settings.set("default_download_dir", self.default_dir_var.get())
            settings.set("confirm_on_abort", self.confirm_abort_var.get())
            settings.set("auto_apply_learned_devtools", self.auto_apply_devtools_var.get())
            settings.set("inter_task_timeout_minutes", int(self.inter_task_timeout_var.get()))
            settings.set("inter_task_inactivity_action", INACTIVITY_ACTION_MAP.get(self.inactivity_action_var.get(), "close_app"))

            theme_code = THEME_MAP.get(self.theme_var.get(), "system")
            settings.set("theme", theme_code)
            apply_theme(theme_code)
            update_widget_tree_colors(self.winfo_toplevel(), theme_code)

            if settings.save():
                messagebox.showinfo("Settings Saved", "System preferences and color scheme saved successfully!")
            else:
                messagebox.showerror("Save Error", "Could not save settings.json file.")
        except ValueError:
            messagebox.showerror("Input Error", "Please enter valid numeric values for timeouts, retries, and inter-task timeout.")

    def _reset_settings(self):
        if messagebox.askyesno("Reset Settings", "Reset all settings to default values?"):
            settings.reset_to_defaults()
            self.headless_var.set(settings.get("headless"))
            self.devtools_var.set(settings.get("auto_open_devtools"))
            self.timeout_var.set(str(settings.get("download_timeout")))
            self.max_wait_var.set(str(settings.get("max_action_wait_seconds")))
            self.retries_var.set(str(settings.get("max_retries")))
            self.retry_wait_var.set(str(settings.get("retry_wait_seconds", 5)))
            self.log_level_var.set(settings.get("log_level"))
            self.default_dir_var.set(settings.get("default_download_dir"))
            self.confirm_abort_var.set(settings.get("confirm_on_abort"))
            self.auto_apply_devtools_var.set(settings.get("auto_apply_learned_devtools", True))
            self.inter_task_timeout_var.set(str(settings.get("inter_task_timeout_minutes", 15)))
            cur_inact = settings.get("inter_task_inactivity_action", "close_app")
            self.inactivity_action_var.set(REV_INACTIVITY_ACTION_MAP.get(cur_inact, "Close Application (Safely exit)"))

            cur_theme_code = settings.get("theme", "system")
            self.theme_var.set(REV_THEME_MAP.get(cur_theme_code, "Same as Local PC (System)"))
            apply_theme(cur_theme_code)
            update_widget_tree_colors(self.winfo_toplevel(), cur_theme_code)

            messagebox.showinfo("Reset Complete", "Settings reset to defaults.")
