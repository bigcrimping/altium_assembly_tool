@echo off
if exist "windows\run_desktop.bat" (
    call "windows\run_desktop.bat" %*
) else if exist "AltiumAssemblyTool.exe" (
    start "" "%~dp0AltiumAssemblyTool.exe" %*
) else if exist "dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" (
    start "" "%~dp0dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" %*
) else if exist ".venv\Scripts\python.exe" (
    echo Launching Altium Assembly Tool...
    ".venv\Scripts\python.exe" "main.py" %*
    if %ERRORLEVEL% NEQ 0 pause
) else (
    echo [ERROR] Altium Assembly Tool launcher could not find a valid environment or executable.
    pause
)
