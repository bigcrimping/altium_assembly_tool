@echo off
setlocal EnableDelayedExpansion

title Create Windows Shortcuts

:: Find project root (one level up if inside windows\ folder)
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%..\main.py" (
    cd /d "%SCRIPT_DIR%.."
) else (
    cd /d "%SCRIPT_DIR%"
)
set "PROJ_DIR=%CD%"
set "ICON_PATH=%PROJ_DIR%\assets\app_icon.ico"

echo ==========================================================
echo        Create Altium Assembly Tool Desktop Shortcuts
echo ==========================================================
echo Project directory: %PROJ_DIR%
echo.

:: Determine target executable / batch
set "TARGET_DESKTOP="
if exist "%PROJ_DIR%\dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" (
    set "TARGET_DESKTOP=%PROJ_DIR%\dist\AltiumAssemblyTool\AltiumAssemblyTool.exe"
    echo Found standalone executable at dist\AltiumAssemblyTool\AltiumAssemblyTool.exe
) else (
    set "TARGET_DESKTOP=%PROJ_DIR%\windows\run_desktop.bat"
    if not exist "!TARGET_DESKTOP!" (
        set "TARGET_DESKTOP=%PROJ_DIR%\run_desktop.bat"
    )
    echo Using launcher batch script as target.
)

:: Create Desktop Shortcut via PowerShell
echo Creating "Altium Assembly Tool" shortcut on Desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell;" ^
    "$sc = $ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Altium Assembly Tool.lnk'));" ^
    "$sc.TargetPath = '%TARGET_DESKTOP%';" ^
    "$sc.WorkingDirectory = '%PROJ_DIR%';" ^
    if (Test-Path '%ICON_PATH%') { "$sc.IconLocation = '%ICON_PATH%';" } ^
    "$sc.Description = 'Altium Assembly Tool - 2D PCB BOM Viewer';" ^
    "$sc.Save();"

if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Desktop shortcut created successfully!
) else (
    echo [ERROR] Failed to create shortcut via PowerShell.
)

:: Ask if user also wants Browser Mode shortcut
echo.
set /p CREATE_BROWSER="Also create shortcut for Browser Mode? (Y/N): "
if /i "!CREATE_BROWSER!"=="Y" (
    set "TARGET_BROWSER=%PROJ_DIR%\windows\run_browser.bat"
    if not exist "!TARGET_BROWSER!" (
        set "TARGET_BROWSER=%PROJ_DIR%\run_browser.bat"
    )
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ws = New-Object -ComObject WScript.Shell;" ^
        "$sc = $ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Altium Assembly Tool (Browser Mode).lnk'));" ^
        "$sc.TargetPath = '!TARGET_BROWSER!';" ^
        "$sc.WorkingDirectory = '%PROJ_DIR%';" ^
        if (Test-Path '%ICON_PATH%') { "$sc.IconLocation = '%ICON_PATH%';" } ^
        "$sc.Description = 'Altium Assembly Tool - Browser Mode';" ^
        "$sc.Save();"
    echo [SUCCESS] Browser Mode shortcut created!
)

echo.
pause
