"""
main.py (Root Launcher)
=======================
Primary launcher for the Unified System Automation Suite.
Resolves paths and delegates directly to the modern PySide6 Qt application
inside unified_dashboard.
"""

import sys
import os
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


if __name__ == "__main__":
    sys.exit(run_app())
