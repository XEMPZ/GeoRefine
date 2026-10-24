"""空间坐标转换页回归（offscreen）：源侧第三列是大地高时，留空按 0 计，不再整行静默丢弃。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app.gui.pages as _pm  # noqa: E402  先导入再屏蔽弹窗（offscreen 无人点击）

_pm.QMessageBox.information = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.warning = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.critical = staticmethod(lambda *a, **k: None)


def _fill(page, row, values):
    page.table_in.setItem(row, 0, _pm.QTableWidgetItem(values[0]))
    for j, v in enumerate(values[1:], start=1):
        page.table_in.setItem(row, j, _pm.QTableWidgetItem(v))


def run() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 未安装，SKIP")
        return 0
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    from app.gui.pages import SpacePage

    checks = 0

    # 1) BLH → xyH：大地高留空，仍应算出 x/y，H 按 0
    page = SpacePage()
    page.ell_combo.setCurrentText("WGS84")
    page.l0_edit.setText("114")
    page.type_rb["BLH->xyH"].setChecked(True)
    page.fmt_in_d.setChecked(True)      # 十进制度输入
    page.fmt_out_d.setChecked(True)
    page.table_in.setRowCount(0)
    page.table_in.insertRow(0)
    _fill(page, 0, ["1", "25.905919", "114.973155", ""])
    page.run()
    assert page.table_out.rowCount() == 1, f"H 留空应仍转换出 1 行，实得 {page.table_out.rowCount()}"
    checks += 1
    x = float(page.table_out.item(0, 1).text())
    y = float(page.table_out.item(0, 2).text())
    h = float(page.table_out.item(0, 3).text())
    assert abs(x - 2866773.4089) < 1e-3, x
    assert abs(y - 597510.4488) < 1e-3, y
    assert abs(h) < 1e-9, h
    checks += 1

    # 2) 同一行补上大地高后结果不变（H 只影响第三列）
    page.table_in.setItem(0, 3, _pm.QTableWidgetItem("0"))
    page.run()
    assert abs(float(page.table_out.item(0, 1).text()) - x) < 1e-6
    checks += 1

    # 3) XYZ 源：Z 留空是缺坐标，不能静默当成 0 算
    page.type_rb["XYZ->BLH"].setChecked(True)
    page.table_in.setRowCount(0)
    page.table_in.insertRow(0)
    _fill(page, 0, ["1", "-2423764.3439", "5204144.0114", ""])
    page.run()
    assert page.table_out.rowCount() == 0, "XYZ 源缺 Z 不应转换"
    checks += 1

    # 4) XYZ 源三列齐全时正常转换回 BLH
    page.table_in.setItem(0, 3, _pm.QTableWidgetItem("2769690.0893"))
    page.run()
    assert page.table_out.rowCount() == 1, page.table_out.rowCount()
    b = float(page.table_out.item(0, 1).text())
    assert abs(b - 25.905919) < 1e-5, b
    checks += 1

    # 5) 全空表：不抛异常、不出结果
    page.table_in.setRowCount(0)
    page.table_in.insertRow(0)
    page.run()
    assert page.table_out.rowCount() == 0
    checks += 1

    return checks
