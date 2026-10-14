# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files needed at runtime
datas = [
    ('settings.json', '.'),
    ('learning_data.json', '.'),
    ('pdf_extractor_configs', 'pdf_extractor_configs'),
    ('core', 'core'),
    ('modules', 'modules'),
    ('ui', 'ui'),
]

# Collect customtkinter assets (fonts, themes, images)
datas += collect_data_files('customtkinter')

# Collect pdfplumber data files
try:
    datas += collect_data_files('pdfplumber')
except Exception:
    pass

# Pandas ships a large number of compiled submodules that PyInstaller's
# static import scan can miss; collect them explicitly so DataFrame/Excel
# features don't fail at runtime with a hidden ImportError.
try:
    pandas_submodules = collect_submodules('pandas')
except Exception:
    pandas_submodules = []

# Selenium's real module tree is far bigger than what static analysis of
# our code reaches (e.g. selenium.webdriver.chrome.webdriver is imported
# dynamically inside selenium itself). Missing even one submodule breaks
# Chrome launch with an ImportError, which silently falls through to the
# webdriver-manager fallback (which needs internet) -- so collect all of it.
try:
    selenium_submodules = collect_submodules('selenium')
except Exception:
    selenium_submodules = []

try:
    webdriver_manager_submodules = collect_submodules('webdriver_manager')
except Exception:
    webdriver_manager_submodules = []

try:
    sklearn_submodules = collect_submodules('sklearn')
except Exception:
    sklearn_submodules = []

try:
    datas += collect_data_files('sklearn')
except Exception:
    pass

# Selenium Manager ships a small platform-specific binary
# (selenium/webdriver/common/windows/selenium-manager.exe) that resolves a
# matching chromedriver WITHOUT needing internet. If this isn't bundled as
# a data file, attempt 1 in core/chrome_driver.py can't run at all and the
# app falls back to the online-only webdriver-manager path.
try:
    datas += collect_data_files('selenium')
except Exception:
    pass

hidden_imports = [
    # Project internals
    'core',
    'core.chrome_driver',
    'core.learning_system',
    'core.logger',
    'core.paths',
    'core.selenium_helpers',
    'core.session_manager',
    'core.settings_manager',
    'core.utils',
    'modules',
    'modules.file_exporter',
    'modules.file_uploader',
    'modules.grade_submission',
    'modules.pdf_extractor',
    'ui',
    'ui.base_tool_frame',
    'ui.dashboard',
    'ui.documentation_ui',
    'ui.file_exporter_ui',
    'ui.file_uploader_ui',
    'ui.grade_ui',
    'ui.learning_ui',
    'ui.pdf_extractor_ui',
    'ui.settings_ui',
    'ui.theme',
    'ui.time_series_graph',
    # Selenium (collect_submodules below adds the full tree; these are
    # just belt-and-suspenders for the most commonly used entry points)
    'selenium',
    'selenium.webdriver',
    'selenium.webdriver.chrome',
    'selenium.webdriver.chrome.options',
    'selenium.webdriver.chrome.service',
    'selenium.webdriver.chrome.webdriver',
    'selenium.webdriver.common.by',
    'selenium.webdriver.common.action_chains',
    'selenium.webdriver.common.keys',
    'selenium.webdriver.support.ui',
    'selenium.webdriver.support.expected_conditions',
    'selenium.common.exceptions',
    'webdriver_manager',
    'webdriver_manager.chrome',
    # Data
    'pandas',
    'openpyxl',
    'pdfplumber',
    'pdfminer',
    'pdfminer.high_level',
    'pdfminer.layout',
    # GUI
    'customtkinter',
    'PIL',
    'PIL.Image',
    'PIL.ImageTk',
    'PIL._tkinter_finder',
    'tkinter',
    'tkinter.ttk',
    'tkinter.filedialog',
    'tkinter.messagebox',
    'tkinter.scrolledtext',
    # Stdlib extras that PyInstaller may miss
    'csv',
    'threading',
    'logging',
    'hashlib',
    'shutil',
    'tempfile',
    'urllib.parse',
    'base64',
    'json',
    'datetime',
    're',
    'traceback',
    'winreg',
    'socket',
    'glob',
    'modules.pdf_spatial_ml',
    'modules.pdf_rule_tuner',
    'ui.pdf_review_tab',
    'sklearn',
    'sklearn.cluster',
] + pandas_submodules + selenium_submodules + webdriver_manager_submodules + sklearn_submodules

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook.py'],
    excludes=['matplotlib', 'cv2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='UnifiedDashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # Replace with 'icon.ico' if you have one
)

# Single portable file: no COLLECT step. Everything (interpreter, libs,
# data files) is embedded directly into dist\UnifiedDashboard.exe above, so
# distributing the app is just handing someone that one .exe -- no installer,
# no Python, no separate folder of DLLs required on the target machine.
# All user-writable data (settings.json, learning_data.json, sessions/,
# pdf_extractor_configs/) is written next to wherever that .exe is placed
# (see core/paths.py), so it stays fully portable (e.g. on a USB stick).
