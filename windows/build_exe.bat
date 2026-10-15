@echo off
setlocal EnableDelayedExpansion

title Build Altium Assembly Tool Portable Executable (.exe)

:: Find project root (one level up if inside windows\ folder)
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%..\main.py" (
    cd /d "%SCRIPT_DIR%.."
) else (
    cd /d "%SCRIPT_DIR%"
)
set "PROJ_DIR=%CD%"

echo ==========================================================
echo    Building Portable Standalone Executable (.exe)
echo ==========================================================
echo Project directory: %PROJ_DIR%
echo.

:: Ensure .venv exists and has dependencies installed
set "VENV_READY=0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import PySide6, flask, altium_monkey" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "VENV_READY=1"
    )
)

if "!VENV_READY!"=="0" (
    echo Virtual environment is missing or incomplete. Running environment setup first...
    if exist "windows\setup_env.bat" (
        call "windows\setup_env.bat"
    ) else if exist "setup_env.bat" (
        call "setup_env.bat"
    )
    ".venv\Scripts\python.exe" -c "import PySide6, flask, altium_monkey" >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] Required dependencies are not installed in .venv.
        echo An active internet connection and Git are required to set up the build environment.
        echo Please connect to the internet, run setup_env.bat, and try again.
        pause
        exit /b 1
    )
)

:: Check if PyInstaller is installed in .venv
if not exist ".venv\Scripts\pyinstaller.exe" (
    echo Installing PyInstaller into .venv...
    ".venv\Scripts\python.exe" -m pip install pyinstaller
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to install PyInstaller!
        pause
        exit /b 1
    )
)

:: Run PyInstaller build
echo.
echo Running PyInstaller with altium_assembly_tool.spec ...
echo (This may take 1-2 minutes)
echo.
".venv\Scripts\pyinstaller.exe" altium_assembly_tool.spec --noconfirm

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] PyInstaller build failed! Please check the output above.
    pause
    exit /b 1
)

:: Verify built executable
echo.
echo Verifying built executable...
dist\AltiumAssemblyTool\AltiumAssemblyTool.exe --help >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Built executable failed verification! Check if modules or DLLs are missing.
    pause
    exit /b 1
)

:: Add convenience launchers to dist\AltiumAssemblyTool\
echo Adding 1-click launchers into dist\AltiumAssemblyTool\ ...
(
    echo @echo off
    echo title Altium Assembly Tool
    echo start "" "%%~dp0AltiumAssemblyTool.exe" %%*
) > "dist\AltiumAssemblyTool\run_desktop.bat"

(
    echo @echo off
    echo title Altium Assembly Tool - Browser Mode
    echo echo Launching Altium Assembly Tool in Browser Mode...
    echo echo Server will start at http://127.0.0.1:4321 and open your browser automatically.
    echo "%%~dp0AltiumAssemblyTool.exe" --browser %%*
) > "dist\AltiumAssemblyTool\run_browser.bat"

(
    echo Altium Assembly Tool - Standalone Portable Windows Release
    echo ==========================================================
    echo.
    echo This standalone folder runs completely offline on Windows 10/11.
    echo No Python, Git, or internet connection required!
    echo.
    echo - Double-click run_desktop.bat or AltiumAssemblyTool.exe to launch the desktop app.
    echo - Double-click run_browser.bat to launch in web browser mode.
    echo - Drag-and-drop any .PcbDoc file onto either launcher to open it directly.
) > "dist\AltiumAssemblyTool\README.txt"

:: Create portable zip archive for easy sharing
echo Creating standalone zip archive: dist\AltiumAssemblyTool-Windows-Portable.zip ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "if (Test-Path 'dist\AltiumAssemblyTool-Windows-Portable.zip') { Remove-Item -Force 'dist\AltiumAssemblyTool-Windows-Portable.zip' }; Compress-Archive -Path 'dist\AltiumAssemblyTool\*' -DestinationPath 'dist\AltiumAssemblyTool-Windows-Portable.zip' -Force"

echo.
echo ==========================================================
if exist "dist\AltiumAssemblyTool\AltiumAssemblyTool.exe" (
    echo [SUCCESS] Build succeeded and verified!
    echo.
    echo Standalone executable folder:
    echo   %PROJ_DIR%\dist\AltiumAssemblyTool\
    echo.
    echo Portable ZIP package (ready to distribute / share):
    echo   %PROJ_DIR%\dist\AltiumAssemblyTool-Windows-Portable.zip
    echo.
    echo This standalone release runs 100%% OFFLINE on any Windows PC!
    echo ==========================================================
    echo.
    set /p OPEN_EXPLORER="Would you like to open the output folder now? (Y/N): "
    if /i "!OPEN_EXPLORER!"=="Y" (
        explorer "%PROJ_DIR%\dist"
    )
) else (
    echo [WARNING] Build finished but AltiumAssemblyTool.exe was not found in dist.
)
echo.
pause
