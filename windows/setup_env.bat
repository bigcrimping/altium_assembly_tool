@echo off
setlocal EnableDelayedExpansion

:: -------------------------------------------------------------
:: Setup Virtual Environment for Altium Assembly Tool on Windows
:: -------------------------------------------------------------

title Altium Assembly Tool - Environment Setup

:: Find project root (one level up if inside windows\ folder)
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%..\main.py" (
    cd /d "%SCRIPT_DIR%.."
) else (
    cd /d "%SCRIPT_DIR%"
)
set "PROJ_DIR=%CD%"

echo ==========================================================
echo        Altium Assembly Tool - Windows Setup
echo ==========================================================
echo Project directory: %PROJ_DIR%
echo.

:: 1. Detect Python
echo [1/4] Detecting Python...
set "PY_CMD="

:: Try Python 3.12 via py launcher first
py -3.12 --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PY_CMD=py -3.12"
    goto :PYTHON_FOUND
)

:: Try standard py launcher
py --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PY_CMD=py"
    goto :PYTHON_FOUND
)

:: Try python on PATH
python --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PY_CMD=python"
    goto :PYTHON_FOUND
)

echo.
echo [ERROR] Python 3 was not found on your system!
echo Please install Python 3.12 from: https://www.python.org/downloads/
echo Make sure to check "Add python.exe to PATH" during installation.
echo.
pause
exit /b 1

:PYTHON_FOUND
for /f "delims=" %%v in ('!PY_CMD! --version 2^>^&1') do set "PY_VER=%%v"
echo Using: !PY_CMD! (!PY_VER!)
echo.

:: 2. Check / Create Virtual Environment
echo [2/4] Setting up virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    !PY_CMD! -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo Created .venv successfully.
) else (
    echo Existing .venv found.
)
echo.

:: 3. Install Requirements
echo [3/4] Installing dependencies from requirements.txt...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [NOTE] Pip upgrade skipped (proceeding with package install)...
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo ==========================================================
    echo [ERROR] Failed to install dependencies!
    echo.
    echo Possible causes:
    echo 1. You are OFFLINE or have no internet connection.
    echo    Installing dependencies requires an active internet connection.
    echo    If you must run offline, please use the pre-built standalone
    echo    release (AltiumAssemblyTool.exe) instead of source mode.
    echo 2. Git is not installed or not on your PATH.
    echo    Installing altium-monkey requires Git on PATH: https://git-scm.com/
    echo ==========================================================
    pause
    exit /b 1
)

:: Also install pyinstaller for building standalone executables
echo Installing PyInstaller (for building .exe releases)...
".venv\Scripts\python.exe" -m pip install pyinstaller
echo.

:: 4. Verify installation
echo [4/4] Verifying installation...
".venv\Scripts\python.exe" -c "import PySide6, flask, altium_monkey" >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Verification failed! One or more required packages could not be imported.
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo [SUCCESS] Environment setup complete and verified!
echo Everything is installed and ready to go.
echo You can now run the app with run_desktop.bat or run_browser.bat!
echo ==========================================================
echo.
pause
