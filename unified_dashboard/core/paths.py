"""
core/paths.py
=============
Central path resolution for writable app data vs. bundled read-only
resources, so the app works identically in three run modes:

  1. Dev mode (running main.py directly with a normal Python install).
  2. PyInstaller ONEDIR build (a folder containing UnifiedDashboard.exe).
  3. PyInstaller ONEFILE build (a single portable .exe) -- the mode used
     for distributing the app so end users don't need to install anything.

In onefile mode, PyInstaller silently unpacks the bundle into a fresh
temporary folder (sys._MEIPASS) every time the exe is launched, and deletes
it on exit. Writable data (settings, learning history, saved sessions, PDF
extractor presets) must NOT live there, or every user edit would vanish the
moment the app closes. Instead, all writable data lives in a folder next to
the actual .exe (or next to main.py in dev mode) -- this keeps the app truly
portable: drop the .exe anywhere (a USB stick, a shared drive, Desktop) and
its settings/data travel with it, with nothing written to the Windows
registry or AppData.
"""

import os
import sys

# Repo root when running un-frozen (dev mode): two levels up from this file
# (core/paths.py -> core/ -> project root).
_DEV_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_persistent_dir() -> str:
    """Folder for writable app data (settings.json, learning_data.json,
    sessions/, pdf_extractor_configs/). Always resolves to a stable,
    non-temporary location:
      - Frozen (onefile or onedir): the folder containing the .exe.
      - Dev mode: the project root.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _DEV_ROOT


def get_bundle_dir() -> str:
    """Folder containing bundled READ-ONLY resources (default presets,
    templates shipped with the app). In onefile mode this is the temporary
    extraction dir (sys._MEIPASS) that disappears on exit -- never write
    here. In dev mode / onedir mode it's the same as the persistent dir."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", get_persistent_dir())
    return _DEV_ROOT
