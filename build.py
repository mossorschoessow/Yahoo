"""打包成 Windows 單一執行檔：dist/YahooAuctionMonitor.exe

    pip install -r requirements.txt -r requirements-desktop.txt
    python build.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
BUILD.mkdir(exist_ok=True)

sys.path.insert(0, str(ROOT))
from monitor.icon import save_ico  # noqa: E402

icon = BUILD / "icon.ico"
save_ico(icon)

sep = os.pathsep  # Windows 是 ;，其他系統是 :
args = [
    str(ROOT / "desktop.py"),
    "--name", "YahooAuctionMonitor",
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--icon", str(icon),
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(BUILD / "pyinstaller"),
    "--specpath", str(BUILD),
    "--add-data", f"{ROOT / 'static'}{sep}static",
    "--add-data", f"{ROOT / 'prompts'}{sep}prompts",
    "--add-data", f"{ROOT / 'tasks.example.json'}{sep}.",
    # uvicorn / pystray 有動態載入的模組
    "--collect-submodules", "uvicorn",
    "--hidden-import", "pystray._win32",
    "--exclude-module", "tkinter",
]
PyInstaller.__main__.run(args)
print("\n完成：", ROOT / "dist" / "YahooAuctionMonitor.exe")
