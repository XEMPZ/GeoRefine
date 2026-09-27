"""GeoRefine 入口：python main.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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
