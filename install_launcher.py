#!/usr/bin/env python3
import os
import sys
from pathlib import Path
import shutil

def main():
    proj_dir = Path(__file__).parent.resolve()
    venv_python = proj_dir / ".venv" / "bin" / "python"
    main_py = proj_dir / "main.py"
    icon_svg = proj_dir / "assets" / "icon.svg"

    print(f"Project directory: {proj_dir}")
    print(f"Venv Python: {venv_python}")
    print(f"Main script: {main_py}")
    print(f"Icon SVG: {icon_svg}")

    if not venv_python.exists():
        print(f"Error: {venv_python} not found. Please set up the virtual environment first.")
        sys.exit(1)

    desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Altium Assembly Tool
Comment=2D PCB assembly viewer for Altium .PcbDoc files
Exec={venv_python} {main_py} %f
Icon={icon_svg}
Path={proj_dir}
Terminal=false
Categories=Development;Electronics;Engineering;
MimeType=application/octet-stream;
Actions=BrowserMode;

[Desktop Action BrowserMode]
Name=Launch in Browser Mode
Exec={venv_python} {main_py} --browser %f
"""

    # 1. Write to project directory
    proj_desktop = proj_dir / "altium_assembly_tool.desktop"
    proj_desktop.write_text(desktop_entry, encoding="utf-8")
    proj_desktop.chmod(0o755)
    print(f"Created launcher in project: {proj_desktop}")

    # 2. Write to Desktop folder
    desktop_folder = Path.home() / "Desktop"
    if desktop_folder.exists() and desktop_folder.is_dir():
        user_desktop = desktop_folder / "altium_assembly_tool.desktop"
        user_desktop.write_text(desktop_entry, encoding="utf-8")
        user_desktop.chmod(0o755)
        print(f"Created launcher on Desktop: {user_desktop}")
    else:
        print("Desktop folder not found, skipping Desktop launcher creation.")

    # 3. Write to local applications menu
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    menu_desktop = apps_dir / "altium_assembly_tool.desktop"
    menu_desktop.write_text(desktop_entry, encoding="utf-8")
    menu_desktop.chmod(0o755)
    print(f"Created launcher in applications menu: {menu_desktop}")

    # 4. Copy icon to local icons folder (scalable/apps)
    icons_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    try:
        icons_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_svg, icons_dir / "altium_assembly_tool.svg")
        print(f"Copied icon to: {icons_dir / 'altium_assembly_tool.svg'}")
    except Exception as e:
        print(f"Warning: Could not copy icon to icons folder: {e}")

    # 5. Try updating desktop database
    try:
        import subprocess
        subprocess.run(["update-desktop-database", str(apps_dir)], capture_output=True)
        print("Updated desktop database.")
    except Exception:
        pass

    print("\nLauncher setup successful! You can now launch Altium Assembly Tool from your applications menu or Desktop.")

if __name__ == "__main__":
    main()
