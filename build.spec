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
    ('ui_qt', 'ui_qt'),
]

# Collect PySide6 assets and submodules
try:
    datas += collect_data_files('PySide6')
except Exception:
    pass

try:
    pyside6_submodules = collect_submodules('PySide6')
except Exception:
    pyside6_submodules = []

# Collect pdfplumber data files
try:
    datas += collect_data_files('pdfplumber')
except Exception:
    pass

# Pandas submodules
try:
    pandas_submodules = collect_submodules('pandas')
except Exception:
    pandas_submodules = []

# Selenium submodules
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

# Selenium Manager binary
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
    'modules.pdf_spatial_ml',
    'modules.pdf_rule_tuner',
    # Qt UI (PySide6)
    'ui_qt',
    'ui_qt.theme',
    'ui_qt.main_window',
    'ui_qt.components',
    'ui_qt.components.badge',
    'ui_qt.components.base_tool_widget',
    'ui_qt.components.inter_task_dialog',
    'ui_qt.components.time_series_graph',
    'ui_qt.views',
    'ui_qt.views.documentation_view',
    'ui_qt.views.file_exporter_view',
    'ui_qt.views.file_uploader_view',
    'ui_qt.views.grade_view',
    'ui_qt.views.home_view',
    'ui_qt.views.learning_view',
    'ui_qt.views.pdf_extractor_view',
    'ui_qt.views.pdf_review_view',
    'ui_qt.views.settings_view',
    # PySide6 GUI
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    # Selenium
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
    'PIL',
    'PIL.Image',
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
<<<<<<< Updated upstream:build.spec
] + pandas_submodules + selenium_submodules + webdriver_manager_submodules
=======
    'sklearn',
    'sklearn.cluster',
] + pandas_submodules + selenium_submodules + webdriver_manager_submodules + sklearn_submodules + pyside6_submodules
>>>>>>> Stashed changes:unified_dashboard/build.spec

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook.py'],
<<<<<<< Updated upstream:build.spec
    excludes=['matplotlib', 'scipy', 'cv2'],
=======
    excludes=['matplotlib', 'cv2', 'tkinter', 'customtkinter'],
>>>>>>> Stashed changes:unified_dashboard/build.spec
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
