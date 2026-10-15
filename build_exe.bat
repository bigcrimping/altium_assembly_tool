@echo off
if exist "windows\build_exe.bat" (
    call "windows\build_exe.bat" %*
) else (
    echo Building Altium Assembly Tool Portable Executable...
    ".venv\Scripts\pyinstaller.exe" altium_assembly_tool.spec --noconfirm
    if %ERRORLEVEL% NEQ 0 pause
)
