"""页面回归：控制点导入目标列、照片批处理回调键、换带页高程列、SRSOrigin 解析。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app.gui.pages as _pm  # noqa: E402

_pm.QMessageBox.information = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.warning = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.critical = staticmethod(lambda *a, **k: None)


def _cell(table, r, c):
    it = table.item(r, c)
    return it.text() if it else ""


def run() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 未安装，SKIP")
        return 0
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    from app.gui.pages import ControlPointPage, GaussPage, PhotoPosPage

    checks = 0

    # 1) 控制点导入：有 B/L 的行，目标列必须用 CSV 里的 目标x/目标y（x2/y2），不能被源值覆盖
    cp = ControlPointPage()
    pts = [{"name": "A1", "B": 30.1, "L": 105.2, "H_ell": 500.0,
            "x2": 1000.0, "y2": 2000.0, "H": 480.0}]
    cp._fill_pairs(pts)
    got = [_cell(cp.table, 0, i) for i in range(7)]
    assert got[1:3] == ["105.2", "30.1"], got        # 源侧 L,B
    assert got[4:6] == ["1000.0", "2000.0"], got     # 目标侧 x2,y2（曾被打成 105.2/30.1）
    checks += 1

    # 没有 x2/y2 时仍退回源值（不硬塞 0）
    cp._fill_pairs([{"name": "A2", "B": 30.2, "L": 105.3, "H_ell": 501.0}])
    got2 = [_cell(cp.table, 0, i) for i in range(7)]
    assert got2[4:6] == ["105.3", "30.2"], got2
    checks += 1

    # 2) 照片页：批处理完成回调不能因缺 'root' 抛异常（PhotoBatchWorker 汇总的是 roots）
    pp = PhotoPosPage()
    res = {"rows": [], "ok": 3, "failed": 0, "skipped": 0, "total": 3,
           "ledger_csv": "", "roots": ["C:/photos"], "dry_run": False}
    pp._on_done(res)      # 之前这里 KeyError('root')
    checks += 1
    assert hasattr(pp, "plane_param_combo"), "照片页缺少平面参数下拉（勾选即 AttributeError）"
    checks += 1

    # 3) 换带页：第 4 列非数值不再中断整批，原样带出
    gp = GaussPage()
    gp.table_in.setRowCount(0)
    for i, row in enumerate([["1", "105.5", "30.5", "高程待定"],
                             ["2", "105.6", "30.6", "12.5"]]):
        gp.table_in.insertRow(i)
        for j, v in enumerate(row):
            gp.table_in.setItem(i, j, _pm.QTableWidgetItem(v))
    gp.run()
    assert gp.table_out.rowCount() == 2, gp.table_out.rowCount()
    assert _cell(gp.table_out, 0, 3) == "高程待定", _cell(gp.table_out, 0, 3)
    assert _cell(gp.table_out, 1, 3) == "12.5000", _cell(gp.table_out, 1, 3)
    checks += 1

    # 4) SRSOrigin：空格分隔要能解析；非法值必须报错，不能静默变 0
    from app.osgb.srs import parse_metadata_xml
    xml_sp = "<ModelMetadata><SRS>EPSG:4547</SRS><SRSOrigin>595251.0978 2865092.1622 234.955</SRSOrigin></ModelMetadata>"
    info = parse_metadata_xml(xml_sp)
    assert abs(info.origin[0] - 595251.0978) < 1e-6, info.origin
    assert abs(info.origin[1] - 2865092.1622) < 1e-6, info.origin
    checks += 1
    xml_comma = "<ModelMetadata><SRS>EPSG:4547</SRS><SRSOrigin>1.5,2.5,3.5</SRSOrigin></ModelMetadata>"
    assert parse_metadata_xml(xml_comma).origin == (1.5, 2.5, 3.5)
    checks += 1
    xml_bad = "<ModelMetadata><SRS>EPSG:4547</SRS><SRSOrigin>abc,def,ghi</SRSOrigin></ModelMetadata>"
    try:
        parse_metadata_xml(xml_bad)
        raise AssertionError("非法 SRSOrigin 应报错而不是静默置 0")
    except ValueError:
        checks += 1

    return checks
