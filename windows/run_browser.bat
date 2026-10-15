@echo off
setlocal EnableDelayedExpansion

title Altium Assembly Tool - Browser Mode

:: Find project root (one level up if inside windows\ folder)
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%..\main.py" (
    cd /d "%SCRIPT_DIR%.."
) else (
    cd /d "%SCRIPT_DIR%"
)
set "PROJ_DIR=%CD%"

:: Check if standalone executable exists
set "STANDALONE_EXE="
if exist "%PROJ_DIR%\AltiumAssemblyTool.exe" (
    set "STANDALONE_EXE=%PROJ_DIR%\AltiumAssemblyTool.exe"
) else if exist "%PROJ_DIR%\dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" (
    set "STANDALONE_EXE=%PROJ_DIR%\dist\AltiumAssemblyTool\AltiumAssemblyTool.exe"
)

:: Check if .venv exists and has PySide6 + Flask installed
set "VENV_OK=0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import flask, altium_monkey" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "VENV_OK=1"
    )
)

echo Launching Altium Assembly Tool in Browser Mode...
echo Server will start at http://127.0.0.1:4321 and open your browser automatically.
echo (Press Ctrl+C in this terminal window to stop the server when done.)
echo.

:: 1. If venv is healthy, run via Python venv
if "!VENV_OK!"=="1" (
    ".venv\Scripts\python.exe" "main.py" --browser %*
    set "EXIT_CODE=!ERRORLEVEL!"
    if !EXIT_CODE! NEQ 0 (
        echo.
        echo [ERROR] Application exited with error code !EXIT_CODE!.
        pause
    )
    exit /b !EXIT_CODE!
)

:: 2. If venv is not healthy, but standalone executable exists, launch it!
if defined STANDALONE_EXE (
    echo [INFO] Running via standalone executable...
    "!STANDALONE_EXE!" --browser %*
    exit /b 0
)

:: 3. Neither healthy venv nor standalone exe exists -> run setup
echo Virtual environment not found or incomplete. Running setup...
if exist "windows\setup_env.bat" (
    call "windows\setup_env.bat"
) else if exist "setup_env.bat" (
    call "setup_env.bat"
)

:: Re-verify after setup
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import flask, altium_monkey" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        ".venv\Scripts\python.exe" "main.py" --browser %*
        set "EXIT_CODE=!ERRORLEVEL!"
        if !EXIT_CODE! NEQ 0 pause
        exit /b !EXIT_CODE!
    )
)

echo.
echo ==========================================================
echo [ERROR] Could not launch Altium Assembly Tool!
echo.
echo 1. The Python virtual environment (.venv) is missing required packages (flask).
echo 2. No standalone executable was found at dist\AltiumAssemblyTool\AltiumAssemblyTool.exe.
echo.
echo If you are OFFLINE:
echo   - Download the standalone portable Windows release (.zip) or
echo   - Build the standalone executable on an online PC with build_exe.bat
echo If you are ONLINE:
echo   - Run windows\setup_env.bat while connected to the internet.
echo ==========================================================
pause
exit /b 1
