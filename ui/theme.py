"""
ui/theme.py
===========
Antigravity IDE Design System & Styling Tokens for Unified Dashboard.
Features ultra-sleek, modern aesthetics:
  - Flat, borderless card architecture with thin subtle borders
  - High-contrast crisp typography (Segoe UI / Consolas)
  - Custom TTK styles for Notebook, Treeview, Scrollbar, Progressbar, Combobox
  - 3 Color Schemes: System (Local PC), Antigravity Dark, Antigravity Light
  - Dynamic module-wide palette synchronization & recursive widget recoloring
"""

import sys
import tkinter as tk
from tkinter import ttk

if sys.platform == "win32":
    try:
        import winreg
    except ImportError:
        winreg = None
else:
    winreg = None


def detect_system_theme() -> str:
    """Detect whether Windows local PC is set to Light or Dark mode. Returns 'dark' or 'light'."""
    if winreg:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if value == 1 else "dark"
        except Exception:
            pass
    return "dark"


DARK_PALETTE = {
    "BG_MAIN": "#0d0e12",          # Ultra-sleek void dark canvas
    "BG_SIDEBAR": "#14161f",       # Antigravity IDE sidebar
    "BG_CARD": "#1a1d28",          # Modern card background
    "BG_CARD_ALT": "#222636",      # Card alternate / input surface
    "BG_LOG": "#08090c",           # Terminal log dark
    "ACCENT_PRIMARY": "#388bfd",   # Antigravity electric blue
    "ACCENT_PRIMARY_HOVER": "#1f6feb",
    "ACCENT_SUCCESS": "#238636",   # Emerald green
    "ACCENT_SUCCESS_HOVER": "#2ea043",
    "ACCENT_WARNING": "#d29922",   # Amber warning
    "ACCENT_DANGER": "#da3633",    # Rose danger
    "ACCENT_DANGER_HOVER": "#f85149",
    "ACCENT_INFO": "#388bfd",
    "ACCENT_INFO_HOVER": "#1f6feb",
    "TEXT_PRIMARY": "#e6edf3",     # Crisp light text
    "TEXT_SECONDARY": "#8b949e",   # Subdued text
    "TEXT_MUTED": "#6e7681",       # Dim helper text
    "TEXT_ACCENT": "#58a6ff",      # Highlight blue text
    "BORDER_COLOR": "#30363d",     # Antigravity thin subtle border
    "BORDER_FOCUS": "#58a6ff",
}

LIGHT_PALETTE = {
    "BG_MAIN": "#f6f8fa",          # Modern light canvas
    "BG_SIDEBAR": "#eaeef2",       # Sidebar background
    "BG_CARD": "#ffffff",          # Pure white cards
    "BG_CARD_ALT": "#f3f4f6",      # Card alternate / input surface
    "BG_LOG": "#1f2328",           # Terminal log dark
    "ACCENT_PRIMARY": "#0969da",   # Electric blue
    "ACCENT_PRIMARY_HOVER": "#0550ae",
    "ACCENT_SUCCESS": "#1a7f37",   # Success green
    "ACCENT_SUCCESS_HOVER": "#116329",
    "ACCENT_WARNING": "#9a6700",   # Amber warning
    "ACCENT_DANGER": "#cf222e",    # Red danger
    "ACCENT_DANGER_HOVER": "#a40e26",
    "ACCENT_INFO": "#0969da",
    "ACCENT_INFO_HOVER": "#0550ae",
    "TEXT_PRIMARY": "#1f2328",     # High contrast dark text
    "TEXT_SECONDARY": "#57606a",   # Muted grey text
    "TEXT_MUTED": "#6e7781",       # Dim helper text
    "TEXT_ACCENT": "#0969da",      # Highlight blue text
    "BORDER_COLOR": "#d0d7de",     # Subtle border
    "BORDER_FOCUS": "#0969da",
}


def get_effective_theme_mode(mode: str | None = None) -> str:
    """Resolves 'system', 'dark', or 'light' into either 'dark' or 'light'."""
    if not mode:
        try:
            from core.settings_manager import settings
            mode = settings.get("theme", "dark")
        except Exception:
            mode = "dark"

    if mode == "system":
        return detect_system_theme()
    elif mode == "light":
        return "light"
    else:
        return "dark"


def get_active_palette(mode: str | None = None) -> dict:
    """Returns the color palette dict for the active theme."""
    eff = get_effective_theme_mode(mode)
    return LIGHT_PALETTE if eff == "light" else DARK_PALETTE


# Set initial color token variables
_initial_palette = get_active_palette()
BG_MAIN = _initial_palette["BG_MAIN"]
BG_SIDEBAR = _initial_palette["BG_SIDEBAR"]
BG_CARD = _initial_palette["BG_CARD"]
BG_CARD_ALT = _initial_palette["BG_CARD_ALT"]
BG_LOG = _initial_palette["BG_LOG"]

ACCENT_PRIMARY = _initial_palette["ACCENT_PRIMARY"]
ACCENT_PRIMARY_HOVER = _initial_palette["ACCENT_PRIMARY_HOVER"]
ACCENT_SUCCESS = _initial_palette["ACCENT_SUCCESS"]
ACCENT_SUCCESS_HOVER = _initial_palette["ACCENT_SUCCESS_HOVER"]
ACCENT_WARNING = _initial_palette["ACCENT_WARNING"]
ACCENT_DANGER = _initial_palette["ACCENT_DANGER"]
ACCENT_DANGER_HOVER = _initial_palette["ACCENT_DANGER_HOVER"]
ACCENT_INFO = _initial_palette["ACCENT_INFO"]
ACCENT_INFO_HOVER = _initial_palette["ACCENT_INFO_HOVER"]

TEXT_PRIMARY = _initial_palette["TEXT_PRIMARY"]
TEXT_SECONDARY = _initial_palette["TEXT_SECONDARY"]
TEXT_MUTED = _initial_palette["TEXT_MUTED"]
TEXT_ACCENT = _initial_palette["TEXT_ACCENT"]

BORDER_COLOR = _initial_palette["BORDER_COLOR"]
BORDER_FOCUS = _initial_palette["BORDER_FOCUS"]

# Antigravity IDE Font Definitions
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

FONT_TITLE = (FONT_FAMILY, 15, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 11, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_BODY_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
FONT_SMALL_BOLD = (FONT_FAMILY, 9, "bold")
FONT_LOG = (FONT_MONO, 9)


def bind_hover_effect(btn, normal_bg, hover_bg, normal_fg="white", hover_fg="white"):
    """Attach smooth micro-animation hover highlight listeners to flat buttons."""
    def _on_enter(e):
        try:
            btn.config(bg=hover_bg, fg=hover_fg)
        except Exception:
            pass

    def _on_leave(e):
        try:
            btn.config(bg=normal_bg, fg=normal_fg)
        except Exception:
            pass

    btn.bind("<Enter>", _on_enter)
    btn.bind("<Leave>", _on_leave)


def apply_notebook_style(mode: str | None = None):
    """Apply high-contrast theme styling to TTK Notebook, Treeview, Scrollbars, and Combobox widgets."""
    style = ttk.Style()
    try:
        style.theme_use('clam')
    except Exception:
        pass

    colors = get_active_palette(mode)

    # Configure TTK Notebook tabs (Antigravity IDE tab bar style)
    style.configure("TNotebook", background=colors['BG_CARD'], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=colors['BG_SIDEBAR'],
        foreground=colors['TEXT_SECONDARY'],
        padding=[18, 7],
        font=FONT_BODY_BOLD,
        borderwidth=0,
        focuscolor="",
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors['BG_CARD']), ("active", colors['BG_CARD_ALT'])],
        foreground=[("selected", colors['TEXT_ACCENT']), ("active", colors['TEXT_PRIMARY'])],
    )

    # Configure TTK Treeview table (Task Queue & tabular displays — Antigravity IDE clean grid style)
    style.configure(
        "Treeview",
        background=colors['BG_CARD_ALT'],
        foreground=colors['TEXT_PRIMARY'],
        fieldbackground=colors['BG_CARD_ALT'],
        rowheight=28,
        borderwidth=1,
        relief="solid",
        font=FONT_BODY,
    )
    style.map(
        "Treeview",
        background=[("selected", colors['ACCENT_PRIMARY'])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=colors['BG_SIDEBAR'],
        foreground=colors['TEXT_PRIMARY'],
        font=FONT_BODY_BOLD,
        borderwidth=1,
        relief="flat",
    )
    style.map(
        "Treeview.Heading",
        background=[("active", colors['ACCENT_PRIMARY_HOVER'])],
        foreground=[("active", "#ffffff")],
    )

    # Configure TTK Progressbar (Sleek Antigravity bar)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=colors['BG_CARD_ALT'],
        background=colors['ACCENT_SUCCESS'],
        bordercolor=colors['BG_CARD_ALT'],
        lightcolor=colors['ACCENT_SUCCESS'],
        darkcolor=colors['ACCENT_SUCCESS'],
        thickness=8,
    )

    # Configure TTK Combobox & Spinbox (High Contrast Dropdown Field)
    style.configure(
        "TCombobox",
        fieldbackground=colors['BG_CARD_ALT'],
        background=colors['BG_CARD_ALT'],
        foreground=colors['TEXT_PRIMARY'],
        selectbackground=colors['ACCENT_PRIMARY'],
        selectforeground="#ffffff",
        arrowcolor=colors['TEXT_PRIMARY'],
        bordercolor=colors['BORDER_COLOR'],
        lightcolor=colors['BORDER_COLOR'],
        darkcolor=colors['BORDER_COLOR'],
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", colors['BG_CARD_ALT']), ("focus", colors['BG_CARD_ALT'])],
        foreground=[("readonly", colors['TEXT_PRIMARY']), ("focus", colors['TEXT_PRIMARY'])],
        selectbackground=[("readonly", colors['ACCENT_PRIMARY'])],
        selectforeground=[("readonly", "#ffffff")],
    )

    style.configure(
        "Vertical.TScrollbar",
        background=colors['BG_CARD_ALT'],
        troughcolor=colors['BG_CARD'],
        bordercolor=colors['BG_CARD'],
        arrowcolor=colors['TEXT_SECONDARY'],
        width=10,
    )

    # Configure Option Database for Tk Combobox popup listbox
    try:
        root = getattr(tk, "_default_root", None)
        if root:
            root.option_add("*TCombobox*Listbox*background", colors['BG_CARD_ALT'])
            root.option_add("*TCombobox*Listbox*foreground", colors['TEXT_PRIMARY'])
            root.option_add("*TCombobox*Listbox*selectBackground", colors['ACCENT_PRIMARY'])
            root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
            root.option_add("*TCombobox*Listbox*font", FONT_BODY)
            root.option_add("*TCombobox*Listbox*borderWidth", 1)
            root.option_add("*TCombobox*Listbox*relief", "flat")
    except Exception:
        pass

    return style


def apply_theme(mode: str | None = None):
    """Updates dynamic theme variables in theme.py and across all loaded ui.* modules."""
    palette = get_active_palette(mode)

    # Update theme.py globals
    g = globals()
    for k, v in palette.items():
        g[k] = v

    # Synchronize all loaded ui.* modules so newly created widgets inherit the new theme colors
    for mod_name, module in list(sys.modules.items()):
        if module and (mod_name.startswith("ui.") or mod_name == "ui"):
            for k, v in palette.items():
                if hasattr(module, k):
                    setattr(module, k, v)

    return apply_notebook_style(mode)


def update_widget_tree_colors(widget, mode: str | None = None):
    """Recursively updates all widget colors in the given hierarchy to match active theme palette."""
    palette = get_active_palette(mode)
    apply_theme(mode)

    tokens = [
        "BG_MAIN", "BG_SIDEBAR", "BG_CARD", "BG_CARD_ALT", "BG_LOG",
        "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED", "TEXT_ACCENT",
        "BORDER_COLOR", "BORDER_FOCUS",
        "ACCENT_PRIMARY", "ACCENT_PRIMARY_HOVER",
        "ACCENT_SUCCESS", "ACCENT_SUCCESS_HOVER",
        "ACCENT_WARNING", "ACCENT_DANGER", "ACCENT_DANGER_HOVER",
        "ACCENT_INFO", "ACCENT_INFO_HOVER",
    ]

    bg_map = {}
    fg_map = {}

    for t in tokens:
        dark_val = DARK_PALETTE.get(t)
        light_val = LIGHT_PALETTE.get(t)
        new_val = palette.get(t)

        if dark_val and new_val:
            bg_map[dark_val] = new_val
            fg_map[dark_val] = new_val
        if light_val and new_val:
            bg_map[light_val] = new_val
            fg_map[light_val] = new_val

    # Extra legacy/hardcoded color mappings
    if palette["BG_MAIN"] == LIGHT_PALETTE["BG_MAIN"]:
        fg_map["#d4d4d4"] = palette["TEXT_PRIMARY"]
        fg_map["#f3f4f6"] = palette["TEXT_PRIMARY"]
        fg_map["#e6edf3"] = palette["TEXT_PRIMARY"]
        fg_map["#9cdcfe"] = palette["TEXT_ACCENT"]
        fg_map["#60a5fa"] = palette["TEXT_ACCENT"]
        fg_map["#4f9cf9"] = palette["ACCENT_PRIMARY"]
        bg_map["#1e2230"] = palette["BG_CARD"]
        bg_map["#252a3b"] = palette["BG_CARD_ALT"]
        bg_map["#0f1117"] = palette["BG_MAIN"]
        bg_map["#161922"] = palette["BG_SIDEBAR"]
        bg_map["#0b0c10"] = palette["BG_LOG"]
    else:
        bg_map["#ffffff"] = palette["BG_CARD"]
        bg_map["#f8fafc"] = palette["BG_MAIN"]
        bg_map["#eaeef2"] = palette["BG_SIDEBAR"]
        bg_map["#f3f4f6"] = palette["BG_CARD_ALT"]
        fg_map["#0f172a"] = palette["TEXT_PRIMARY"]
        fg_map["#1f2328"] = palette["TEXT_PRIMARY"]
        fg_map["#475569"] = palette["TEXT_SECONDARY"]
        fg_map["#64748b"] = palette["TEXT_MUTED"]

    def _recolor(w):
        w_class = w.winfo_class()

        # Input fields (Entry, Spinbox, Listbox) — force background to match BG_CARD_ALT (Credentials field background)
        if w_class in ("Entry", "Spinbox", "Listbox"):
            try:
                w.config(
                    bg=palette["BG_CARD_ALT"],
                    fg=palette["TEXT_PRIMARY"],
                    insertbackground=palette["TEXT_PRIMARY"],
                    readonlybackground=palette["BG_CARD_ALT"]
                )
            except Exception:
                pass

        # Update background
        try:
            cur_bg = w.cget("bg")
            if cur_bg in bg_map:
                w.config(bg=bg_map[cur_bg])
        except Exception:
            pass

        # Update foreground
        try:
            cur_fg = w.cget("fg")
            if cur_fg in fg_map:
                w.config(fg=fg_map[cur_fg])
        except Exception:
            pass

        # Update activebackground / activeforeground / selectcolor / insertbackground
        try:
            cur_abg = w.cget("activebackground")
            if cur_abg in bg_map:
                w.config(activebackground=bg_map[cur_abg])
        except Exception:
            pass

        try:
            cur_afg = w.cget("activeforeground")
            if cur_afg in fg_map:
                w.config(activeforeground=fg_map[cur_afg])
        except Exception:
            pass

        try:
            cur_sc = w.cget("selectcolor")
            if cur_sc in bg_map:
                w.config(selectcolor=bg_map[cur_sc])
        except Exception:
            pass

        try:
            cur_hb = w.cget("highlightbackground")
            if cur_hb in bg_map:
                w.config(highlightbackground=bg_map[cur_hb])
        except Exception:
            pass

        # Text & ScrolledText documentation / editor recoloring
        if w_class in ("Text", "ScrolledText"):
            try:
                w.config(
                    bg=palette["BG_CARD_ALT"],
                    fg=palette["TEXT_PRIMARY"],
                    insertbackground=palette["TEXT_PRIMARY"]
                )
                w.tag_config("INFO", foreground=palette["TEXT_ACCENT"])
                w.tag_config("WARNING", foreground=palette["ACCENT_WARNING"])
                w.tag_config("ERROR", foreground=palette["ACCENT_DANGER"])
                w.tag_config("DEBUG", foreground=palette["TEXT_MUTED"])
            except Exception:
                pass

        for child in w.winfo_children():
            _recolor(child)

    _recolor(widget)
    apply_notebook_style(mode)
