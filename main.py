"""
<<<<<<< Updated upstream
main.py
=======
PySide6 application entry point for the Unified System Automation Dashboard.
Modern, resilient Qt interface.
=======
main.py (Root Launcher)
=======================
Primary launcher for the Unified System Automation Suite.
Resolves paths and delegates directly to the modern PySide6 Qt application
inside unified_dashboard.
>>>>>>> Stashed changes
"""

import sys
import os
<<<<<<< Updated upstream
import traceback
import threading

# Ensure the project directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Python 3.14 / pandas 3.x compatibility patch ─────────────────────────────
# pandas RangeIndex.__iter__ uses `yield from self._range`, making it a
# generator. Python 3.14 internally calls .send() on iterators used inside
# zip(..., strict=True), which is then forwarded to the C range_iterator
# that has no .send() method → AttributeError. Replacing it with a plain
# iter() call returns the raw C iterator and eliminates the crash.
try:
    import pandas as _pd
    if hasattr(_pd.RangeIndex, '__iter__'):
        _pd.RangeIndex.__iter__ = lambda self: iter(self._range)
except Exception:
    pass  # Never block startup for a compat patch failure
# ─────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ui_qt.theme import get_dark_palette
from ui_qt.main_window import MainWindow


def _safe_format_exc(exc_type, exc_value, exc_traceback):
    """Format an exception safely, falling back to a plain string on failure."""
    try:
        return "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    except Exception:
        pass
    # Secondary fallback: repr-only (never raises)
    try:
        return f"{exc_type.__name__}: {exc_value}"
    except Exception:
        return "<unknown error — traceback formatter failed>"


def _handle_exception(exc_type, exc_value, exc_traceback):
    """Global exception handler to prevent silent program termination."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    err_msg = _safe_format_exc(exc_type, exc_value, exc_traceback)
    print(f"[UNHANDLED ERROR]\n{err_msg}", file=sys.stderr)

    try:
        QMessageBox.critical(
            None,
            "Unexpected Error",
            f"An unexpected error occurred:\n\n{exc_value}\n\n"
            "The application will remain open. See console/logs for details."
        )
    except Exception:
        pass


def _handle_thread_exception(args):
    """Global handler for exceptions in background threads."""
    err_msg = _safe_format_exc(args.exc_type, args.exc_value, args.exc_traceback)
    print(f"[THREAD ERROR in {args.thread.name}] {err_msg}", file=sys.stderr)


def run_app(argv=None):
    """Launch the PySide6 Unified System Automation Dashboard."""
    if argv is None:
        argv = sys.argv

    # High-DPI configuration (must be set before QApplication is created)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication.instance()
    if app is None:
        app = QApplication(argv)

    app.setApplicationName("Unified System Automation Suite")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("Automation Suite")
    app.setPalette(get_dark_palette())

    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception

    window = MainWindow()
    window.show()

    return app.exec()
=======
import importlib.util

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(ROOT_DIR, "unified_dashboard")

os.chdir(DASHBOARD_DIR)
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)


def run_app(argv=None):
    main_file = os.path.join(DASHBOARD_DIR, "main.py")
    spec = importlib.util.spec_from_file_location("unified_dashboard_main", main_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["unified_dashboard_main"] = mod
    spec.loader.exec_module(mod)
    return mod.run_app(argv)
>>>>>>> Stashed changes


if __name__ == "__main__":
    sys.exit(run_app())
