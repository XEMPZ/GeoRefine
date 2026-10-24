"""崩溃钩子回归：未捕获异常要写 logs/crash_*.log 并弹窗，而不是无声无息。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def run() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 未安装，SKIP")
        return 0
    import PySide6.QtWidgets as QtWidgets

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    import main as entry

    class _Stub:
        def __init__(self):
            self.calls = []

        def critical(self, *args):
            self.calls.append(args)

    checks = 0
    stub = _Stub()
    original_box = QtWidgets.QMessageBox
    QtWidgets.QMessageBox = stub
    try:
        entry._install_crash_hook()
        try:
            raise ValueError("boom-for-test")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert stub.calls, "excepthook 没有弹窗"
        assert "boom-for-test" in str(stub.calls[0][-1]), stub.calls
        checks += 1

        logs = sorted((Path(entry.__file__).resolve().parent / "logs").glob("crash_*.log"))
        assert logs, "没有写出崩溃日志"
        text = logs[-1].read_text(encoding="utf-8")
        assert "boom-for-test" in text and "ValueError" in text, text[:200]
        logs[-1].unlink()
        checks += 1
    finally:
        QtWidgets.QMessageBox = original_box
        sys.excepthook = sys.__excepthook__

    return checks
