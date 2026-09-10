@echo off
setlocal

echo ============================================================
echo  Unified Dashboard - Windows Standalone Build
echo ============================================================

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH. Install Python 3.10+ and re-run this script.
    exit /b 1
)

echo [1/3] Installing / upgrading dependencies...
python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Dependency installation failed. See output above.
    exit /b 1
)

echo.
echo [2/3] Cleaning previous build artifacts...

REM Kill any running instance of the app first -- a locked exe from a
REM previous run (or File Explorer/antivirus still touching a freshly
REM written one) is the #1 cause of "PermissionError ... UnifiedDashboard.exe"
REM during this step.
taskkill /F /IM UnifiedDashboard.exe /T >nul 2>nul
timeout /t 1 /nobreak >nul

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/3] Building standalone EXE (this may take a few minutes)...
python -m PyInstaller build.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  BUILD FAILED
    echo  If the error above mentions "Permission denied" on
    echo  UnifiedDashboard.exe, close the app if it's running, add an
    echo  antivirus/Defender exclusion for this folder (real-time
    echo  scanners often lock a freshly written .exe for a moment),
    echo  then re-run this script.
    echo ============================================================
    exit /b 1
)

echo.
if exist "dist\UnifiedDashboard.exe" (
    echo ============================================================
    echo  BUILD SUCCEEDED
    echo  Output: dist\UnifiedDashboard.exe
    echo  This is a single portable file -- copy it anywhere (USB
    echo  stick, another PC, a shared drive) and run it directly.
    echo  No installer and no separate Python install is needed on
    echo  the target machine. Google Chrome must still be installed
    echo  there, since the automation drives real Chrome.
    echo ============================================================
) else (
    echo ============================================================
    echo  BUILD FAILED - check output above for errors
    echo ============================================================
    exit /b 1
)

pause
