# Windows Launch & Build Guide

This folder contains pre-configured launcher scripts and build tools designed specifically for **Windows**.

---

## Quick Start (No Manual Setup Needed)

1. **Desktop GUI App**:
   - Double-click **`run_desktop.bat`** (or drag and drop your `.PcbDoc` file onto it).
   - If this is your first run, it will automatically detect Python, create `.venv`, and install dependencies for you.

2. **Browser Mode UI**:
   - Double-click **`run_browser.bat`** (or drag and drop your `.PcbDoc` file onto it).
   - It will start the local server on `http://127.0.0.1:4321` and automatically open your default web browser.

3. **Create Desktop Shortcuts**:
   - Double-click **`create_desktop_shortcut.bat`** to generate shortcuts with the custom app icon on your Windows Desktop.

---

## Building a Standalone Executable (.exe) & Offline Release

To package the tool into a standalone folder and zip that runs on any Windows PC without Python, Git, or an internet connection:

1. Double-click **`build_exe.bat`**.
2. It verifies dependencies, builds with PyInstaller, verifies the executable, creates 1-click launchers, and packages:
   - Standalone folder:
     ```
     dist\AltiumAssemblyTool\
     ```
   - Portable ZIP file:
     ```
     dist\AltiumAssemblyTool-Windows-Portable.zip
     ```
3. You can copy the `dist\AltiumAssemblyTool\` folder or share `dist\AltiumAssemblyTool-Windows-Portable.zip`. Inside are `AltiumAssemblyTool.exe`, `run_desktop.bat`, and `run_browser.bat` which work **100% offline** without needing Python or Git installed!

---

## Offline & Portable Execution

If you are running on an air-gapped or offline PC without Python installed:
- Simply use the pre-built `dist\AltiumAssemblyTool\` folder (or unpack `AltiumAssemblyTool-Windows-Portable.zip`).
- Double-clicking **`run_desktop.bat`** automatically detects the standalone executable if `.venv` is missing or unconfigured.

---

## Files in this Folder

| File | Purpose |
|---|---|
| **`run_desktop.bat`** | 1-Click launcher for the desktop PySide6 GUI window. Auto-installs dependencies if `.venv` is missing. Drag-and-drop compatible. |
| **`run_browser.bat`** | 1-Click launcher for Browser Mode (Flask + web UI). Drag-and-drop compatible. |
| **`setup_env.bat`** | Dedicated setup script to create `.venv` and install/upgrade requirements and PyInstaller. |
| **`build_exe.bat`** | PyInstaller build script to compile a portable `.exe` folder. |
| **`create_desktop_shortcut.bat`** | Creates "Altium Assembly Tool" and "Browser Mode" desktop shortcuts with icons. |

---

## Prerequisites (Only for Running from Source or Building)

- **Python 3.12** installed from [python.org](https://www.python.org/downloads/) (with *"Add python.exe to PATH"* checked).
- **Git for Windows** installed from [git-scm.com](https://git-scm.com/downloads) (needed by pip to download `altium-monkey`).
