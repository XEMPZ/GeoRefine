"""表格复制回归：所有表格都是 _CopyTable，Ctrl+C 能按 TSV 导出选区/整表。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app.gui.pages as _pm  # noqa: E402  先导入再屏蔽弹窗

_pm.QMessageBox.information = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.warning = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.critical = staticmethod(lambda *a, **k: None)


def run() -> int:
    try:
        from PySide6.QtWidgets import (QApplication, QTableWidget, QTableWidgetItem,
                                       QTableWidgetSelectionRange)
    except ImportError:
        print("PySide6 未安装，SKIP")
        return 0
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from app.gui.main_window import MainWindow
    from app.gui.pages import _CopyTable

    checks = 0
    win = MainWindow()
    tables = win.findChildren(QTableWidget)
    assert tables, "主窗口里没有找到任何表格"
    bad = [t for t in tables if not isinstance(t, _CopyTable)]
    assert not bad, f"还有 {len(bad)} 个表格不支持复制：{sorted({type(t).__name__ for t in bad})}"
    checks += 1
    assert len(tables) >= 14, f"表格数量 {len(tables)}，预期 >= 14"
    checks += 1

    t = win.pages[2].table_in          # 空间坐标转换页的输入表
    t.setRowCount(0)
    t.insertRow(0)
    for c, v in enumerate(["1", "25.905919", "114.973155", "0"]):
        t.setItem(0, c, QTableWidgetItem(v))

    # 1) 选中连续两格 → 只复制这两格
    t.clearSelection()
    t.setRangeSelected(QTableWidgetSelectionRange(0, 1, 0, 2), True)
    txt = t.copy_to_clipboard()
    assert txt == "25.905919\t114.973155", repr(txt)
    assert QApplication.clipboard().text() == txt, "剪贴板没写进去"
    checks += 1

    # 2) Ctrl+C 按键路径（单选一格）
    t.clearSelection()
    t.setCurrentCell(0, 0)
    QTest.keyClick(t, Qt.Key_C, Qt.ControlModifier)
    assert QApplication.clipboard().text() == "1", repr(QApplication.clipboard().text())
    checks += 1

    # 3) 无选区 → 整表；末尾空行不带走
    t.clearSelection()
    t.insertRow(1)
    whole = t.copy_to_clipboard()
    assert whole == "1\t25.905919\t114.973155\t0", repr(whole)
    checks += 1

    # 4) 只读结果表也能复制
    out = win.pages[2].table_out
    out.setRowCount(0)
    out.insertRow(0)
    for c, v in enumerate(["1", "2866773.4089", "597510.4488", "0.0000"]):
        out.setItem(0, c, QTableWidgetItem(v))
    out.clearSelection()
    assert out.copy_to_clipboard() == "1\t2866773.4089\t597510.4488\t0.0000"
    checks += 1

    return checks
