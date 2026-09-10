"""
runtime_hook.py
---------------
Ensures sys.path and the working directory are set up correctly when
running as a PyInstaller-frozen executable (onefile or onedir).

Two different folders matter for a frozen build:
  - sys._MEIPASS: the bundle's read-only resources. In ONEFILE mode this is
    a fresh temporary folder created on every launch and deleted on exit --
    never a place to write persistent data.
  - The folder containing the actual .exe: stable and persistent, so this
    is where the app's writable data (settings.json, learning_data.json,
    sessions/, pdf_extractor_configs/) lives (see core/paths.py) and where
    the current working directory is pointed, matching what a portable app
    user expects (e.g. relative paths typed into the app resolve next to
    the .exe they double-clicked, not into a temp folder that vanishes).
"""
import sys
import os

if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
    # Make the bundle root importable so our packages/data resolve.
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)
    # Point CWD at the folder holding the actual .exe (persistent, portable),
    # not at the ephemeral onefile extraction folder.
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    try:
        os.chdir(exe_dir)
    except OSError:
        pass
