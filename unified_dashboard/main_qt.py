"""
main_qt.py
==========
PySide6 application entry point for the Unified System Automation Dashboard.

Parallel to main.py (Tkinter). Run this file directly to launch the new
modern Qt interface without affecting the existing Tkinter version.
"""

import sys
import os
import traceback

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

import threading
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


if __name__ == "__main__":
    # ── High-DPI configuration (must be set before QApplication is created) ──
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Unified System Automation Suite")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("Automation Suite")
    app.setPalette(get_dark_palette())

    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
