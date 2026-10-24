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


def _install_crash_hook():
    """未捕获异常 → logs/crash_*.log + 弹窗。

    打包版是无控制台的 windowed exe：PySide6 把槽函数里的异常交给 sys.excepthook 后
    继续运行，默认实现只往并不存在的 stderr 打印，用户看到的就是"点了没反应"。
    这里把现场留下来并明示。
    """
    import datetime
    import threading
    import traceback

    def _report(exc_type, exc, tb):
        from app import app_root
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log_path = None
        try:
            log_dir = Path(app_root()) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"crash_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
            log_path.write_text(text, encoding="utf-8")
        except Exception:      # 记录失败也不能在异常钩子里再抛
            log_path = None
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "GeoRefine 出错",
                    f"{exc_type.__name__}: {exc}\n\n"
                    + (f"详细信息已记录：{log_path}" if log_path else "详细信息记录失败"),
                )
        except Exception:
            pass

    sys.excepthook = _report

    def _thread_report(args):
        _report(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_report


def main():
    _install_crash_hook()
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
