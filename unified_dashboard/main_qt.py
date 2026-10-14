"""
main_qt.py
==========
Compatibility alias for main.py (PySide6 Qt).
"""

from main import run_app

if __name__ == "__main__":
    import sys
    sys.exit(run_app())
