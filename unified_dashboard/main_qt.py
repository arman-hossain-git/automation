"""
main_qt.py
==========
Alias entry point for the Unified System Automation Dashboard (PySide6).
Delegates directly to main.py.
"""

import sys
import os
import importlib.util

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

_main_path = os.path.join(_dir, "main.py")
_spec = importlib.util.spec_from_file_location("main", _main_path)
_main_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_main_mod)
run_app = _main_mod.run_app

if __name__ == "__main__":
    sys.exit(run_app())
