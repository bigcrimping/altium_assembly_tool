@echo off
if exist "windows\run_browser.bat" (
    call "windows\run_browser.bat" %*
) else if exist "AltiumAssemblyTool.exe" (
    echo Launching Altium Assembly Tool in Browser Mode...
    echo Server will start at http://127.0.0.1:4321 and open your browser automatically.
    "%~dp0AltiumAssemblyTool.exe" --browser %*
) else if exist "dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" (
    echo Launching Altium Assembly Tool in Browser Mode...
    echo Server will start at http://127.0.0.1:4321 and open your browser automatically.
    "%~dp0dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" --browser %*
) else if exist ".venv\Scripts\python.exe" (
    echo Launching Altium Assembly Tool in Browser Mode...
    echo Server will start at http://127.0.0.1:4321 and open your browser automatically.
    ".venv\Scripts\python.exe" "main.py" --browser %*
    if %ERRORLEVEL% NEQ 0 pause
) else (
    echo [ERROR] Altium Assembly Tool launcher could not find a valid environment or executable.
    pause
)
