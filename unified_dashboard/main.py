"""
main.py
=======
Launcher entry point for the Unified System Automation Dashboard.
"""

import sys
import os
import threading
import traceback
import tkinter as tk
from tkinter import messagebox

# Ensure the project directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.dashboard import UnifiedDashboardApp


def _handle_exception(exc_type, exc_value, exc_traceback):
    """Global exception handler to prevent silent program termination."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(f"[UNHANDLED ERROR] {err_msg}", file=sys.stderr)
    try:
        messagebox.showerror("Unexpected Error", f"An unexpected error occurred:\n\n{exc_value}\n\nThe application will stay open. See console/logs for details.")
    except Exception:
        pass


def _handle_thread_exception(args):
    """Global handler for exceptions in background threads."""
    err_msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    print(f"[THREAD ERROR in {args.thread.name}] {err_msg}", file=sys.stderr)


if __name__ == "__main__":
    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception

    app = UnifiedDashboardApp()
    # Intercept Tkinter callback exceptions to prevent mainloop exit
    app.report_callback_exception = _handle_exception
    app.mainloop()
