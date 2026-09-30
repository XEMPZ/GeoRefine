"""GeoRefine 入口。

源码运行：python main.py
打包运行：GeoRefine.exe（PyInstaller 冻结，sys.frozen = True）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 打包后模块已由 PyInstaller 打进包内，不能再往 sys.path 塞源码目录
if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("GeoRefine")
    app.setStyle("Fusion")
    try:
        app.setFont(QFont("Microsoft YaHei UI", 9))
    except Exception:  # noqa: BLE001
        pass
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
