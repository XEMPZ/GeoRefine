"""GUI 冒烟测试（offscreen）：实例化主窗口、遍历页面、跑一次控制点计算+保存+照片批处理。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 全程屏蔽模态弹窗（offscreen 环境无人点击会卡死）
import app.gui.pages as _pm
_pm.QMessageBox.information = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.warning = staticmethod(lambda *a, **k: None)
_pm.QMessageBox.critical = staticmethod(lambda *a, **k: None)


def run() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 未安装，SKIP")
        return 0
    app = QApplication.instance() or QApplication(sys.argv)
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    for i in range(len(win.pages)):
        win.nav.setCurrentRow(i)
        app.processEvents()

    # 1) 控制点转换页：导入样例 CSV → 计算 → 保存参数（临时参数库）
    page = win.pages[4]
    page.import_csv.__self__  # noqa: B018  仅引用
    from app.common.io_csv import read_points_csv
    pts = read_points_csv(str(_ROOT / "sample_data" / "控制点样本_平面对.csv"))
    assert len(pts) == 12, f"样例点数 {len(pts)}"
    for p in pts:
        r = page.table.rowCount()
        page.table.insertRow(r)
        for j, val in enumerate([p.get("name"), p.get("x"), p.get("y"), p.get("h"),
                                 p.get("x2"), p.get("y2"), p.get("H")]):
            page.table.setItem(r, j, QTableWidgetItem2(str(val)))
    page.compute()
    assert page._rows, "compare_methods 无结果"
    sel = [r for r in page._rows if r.get("selected")]
    assert sel and sel[0]["method"] in ("planar4", "chain74", "seven"), sel
    # 保存到临时目录
    import tempfile
    from app.common.params import ParamLibrary
    tmp_params = tempfile.mkdtemp()
    import app.gui.pages as pages_mod
    old_dir = pages_mod.PARAMS_DIR
    pages_mod.PARAMS_DIR = Path(tmp_params)
    page._names = [p.get("name") or f"P{i}" for i, p in enumerate(pts)]
    page.save_param.__self__  # noqa: B018
    # 直接调用保存逻辑（不弹对话框版本）
    from datetime import datetime
    sel_row = sel[0]
    from app.core.transform2d import fit_4param
    import numpy as np
    f4 = fit_4param(page._src[:, :2], page._dst[:, :2])
    d = {"schema": "coordparam/1", "name": "冒烟_四参数", "kind": sel_row["method"],
         "source_kind": "planar", "target_kind": "planar", "ellipsoid": "WGS84",
         "projection": None, "planar4": {"dx": f4.param.dx, "dy": f4.param.dy,
                                         "a": f4.param.a, "b": f4.param.b},
         "seven": None,
         "heightfit": ({"mode": page._rows[-1]["heightfit"].mode,
                        "space": page._rows[-1]["heightfit"].space,
                        "value_type": page._rows[-1]["heightfit"].value_type,
                        "coef": [float(c) for c in page._rows[-1]["heightfit"].coef],
                        "center": list(page._rows[-1]["heightfit"].center)} if
                       any(x.get("heightfit") for x in page._rows) else None),
         "accuracy": {"rms_xy_m": sel_row.get("rms_xy"), "n_points": sel_row["n"]},
         "created": datetime.now().isoformat(timespec="seconds")}
    pages_mod.PARAMS_DIR = old_dir
    saved = ParamLibrary(tmp_params).save(d)
    assert Path(saved).exists()

    # 2) 参数应用页：把刚保存的四参数用于一个点（临时参数库）
    ap = win.pages[3]
    pages_mod.PARAMS_DIR = Path(tmp_params)
    ap.reload_params()
    assert ap.param_combo.count() >= 1, "参数应用页未发现参数"
    ap.param_combo.setCurrentText("冒烟_四参数")
    ap.table_in.insertRow(0)
    for j, val in enumerate(["A1", str(pts[0]["x"]), str(pts[0]["y"]), str(pts[0].get("h") or 0)]):
        ap.table_in.setItem(0, j, QTableWidgetItem2(str(val)))
    ap.run()
    assert ap.table_out.rowCount() == 1, "参数应用无输出"
    pages_mod.PARAMS_DIR = old_dir

    # 2b) cot 全链路：手簿点对 → 比选 → 覆盖 chain74 → 保存 → 参数应用还原施工坐标
    pages_mod.PARAMS_DIR = Path(tmp_params)
    page.load_file(str(_ROOT / "sample_data" / "点对文件样例" / "手簿点对_正常高-大地高.cot"))
    assert page.table.rowCount() >= 39, page.table.rowCount()
    # 投影参数：cot 目标施工坐标系中央子午线 115°（手簿逻辑：用户指定目标投影）
    page.proj_l0.setText("115.00000000")
    page.compute()
    assert page._rows, "cot 比选无结果"
    page.chk_seven.setChecked(True)   # 新 UI：勾选七参数（大地→平面自动走链式 chain74）
    sel = [r for r in page._rows if r.get("selected")]
    assert sel and sel[0]["method"] == "chain74", "平面转换选择未生效"
    page.name_edit.setText("冒烟_cot链式")
    orig_info = pages_mod.QMessageBox.information
    pages_mod.QMessageBox.information = staticmethod(lambda *a, **k: None)
    try:
        page.save_param()
    finally:
        pages_mod.QMessageBox.information = orig_info
    ap2 = win.pages[3]
    ap2.reload_params()
    ap2.param_combo.setCurrentText("冒烟_cot链式")
    ap2.chk_do_plane.setChecked(True)    # 用户勾选要应用的转换内容
    ap2.chk_do_height.setChecked(True)
    ap2.table_in.setRowCount(0)
    ap2.table_in.insertRow(0)
    # ApplyPage 大地坐标列序为 B 前 L 后；控制点页表格为 L 前 B 后
    for j, c in enumerate((2, 1, 3)):  # B, L, H（十进制度）
        it = page.table.item(0, c)
        ap2.table_in.setItem(0, j + 1, QTableWidgetItem2(it.text()))
    ap2.run()
    assert ap2.table_out.rowCount() == 1, "cot 参数应用无输出"
    from app.common import pair_io as _pio
    cot0 = _pio.parse_cot(str(_ROOT / "sample_data" / "点对文件样例" / "手簿点对_正常高-大地高.cot"))[0]
    ox = float(ap2.table_out.item(0, 1).text())
    oy = float(ap2.table_out.item(0, 2).text())
    assert abs(ox - cot0.x) < 1.0 and abs(oy - cot0.y) < 1.0, (ox, oy, cot0.x, cot0.y)
    pages_mod.PARAMS_DIR = old_dir

    # 3) 高斯投影换带页：xy→BL 换带一次（CoordTran 真值口径）
    gz = win.pages[1]
    gz.ell_combo.setCurrentText("WGS84")
    gz.table_in.insertRow(0)
    for j, val in enumerate(["G1", "3099348.64467", "352447.704379"]):
        gz.table_in.setItem(0, j, QTableWidgetItem2(val))
    gz.rb_src_xy.setChecked(True)
    gz.rb_dst_bl.setChecked(True)
    gz.run()
    assert gz.table_out.rowCount() == 1, "换带无输出"
    # 源 xy (L0=105, B0=30, h0=0) 反算应为 B=28°, L=103.5°（d.ms 编码 28.00000000）
    b_txt = gz.table_out.item(0, 1).text()
    assert abs(float(b_txt) - 28.0) < 1e-7, b_txt
    # d 格式输出：L 应为十进制 103.5
    gz.fmt_dst_d.setChecked(True)
    gz.run()
    l_txt = gz.table_out.item(0, 2).text()
    assert abs(float(l_txt) - 103.5) < 1e-6, l_txt
    gz.fmt_dst_dms.setChecked(True)
    # 回车增行：末行回车应追加一行
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest as _QTest
    rows0 = gz.table_in.rowCount()
    gz.table_in.setCurrentCell(rows0 - 1, 1)
    _QTest.keyClick(gz.table_in, _Qt.Key_Return)
    assert gz.table_in.rowCount() == rows0 + 1, "回车未增行"
    # 自定义椭球：填入 WGS84 同值，结果应一致
    gz.ell_combo.setCurrentText("自定义")
    gz.a_edit.setText("6378137.0")
    gz.invf_edit.setText("298.257223563")
    gz.run()
    assert abs(float(gz.table_out.item(0, 1).text()) - 28.0) < 1e-7, "自定义椭球结果异常"
    gz.ell_combo.setCurrentText("WGS84")
    # 通用导入：样例 CSV（点号, x, y）→ 3 点 + 空行
    gz.load_file(str(_ROOT / "sample_data" / "样例导入数据" / "01_csv_逗号分隔_有点号" / "平面坐标_xy.csv"))
    assert gz.table_in.rowCount() == 4, gz.table_in.rowCount()  # 3 点 + 空行

    # 3) 空间坐标转换页：BLH->XYZ 一次
    sp = win.pages[2]
    sp.ell_combo.setCurrentText("WGS84")
    sp.table_in.insertRow(0)
    from app.core import anglefmt as _af
    b_dms = _af.rad_to_dms(28.0 * 3.141592653589793 / 180.0)
    for j, val in enumerate(["S1", f"{b_dms:.8f}", "103.30000000", "100"]):
        sp.table_in.setItem(0, j, QTableWidgetItem2(str(val)))
    for rb in sp.type_rb.values():
        rb.setChecked(rb is sp.type_rb["BLH->XYZ"])
    sp.run()
    assert sp.table_out.rowCount() == 1, "空间转换无输出"
    xyz0 = [float(sp.table_out.item(0, j).text()) for j in (1, 2, 3)]
    # d 格式输入应与 d.ms 等价：dms 28.00000000/103.30000000 = 28.0°/103.5°
    sp.fmt_in_d.setChecked(True)
    sp.table_in.insertRow(1)
    for j, val in enumerate(["S2", "28.0", "103.5", "100"]):
        sp.table_in.setItem(1, j, QTableWidgetItem2(str(val)))
    sp.run()
    xyz1 = [float(sp.table_out.item(1, j).text()) for j in (1, 2, 3)]
    assert all(abs(p - q) < 1e-3 for p, q in zip(xyz0, xyz1)), (xyz0, xyz1)
    sp.fmt_in_dms.setChecked(True)

    # 4) 照片POS页：对样例照片跑批（格网=样例CSV）
    photo_root = _ROOT / "sample_data" / "样例照片POS"
    from app.common import hconv
    from app.core.geoid import GridModel, load_grid
    grid = load_grid(str(_ROOT / "sample_data" / "局部似大地水准面格网_样例.csv"))
    fn = hconv.make_photo_height_fn("geoid", model=GridModel(grid))
    res = __import__("app.photo.batch", fromlist=["process"]).process(
        str(photo_root), fn, mode_name="smoke", update_xmp=True)
    assert res["total"] == 8 and res["ok"] == 8, (res["total"], res["ok"])
    assert Path(res["ledger_csv"]).exists()
    # 回滚恢复样例
    rb = __import__("app.photo.batch", fromlist=["rollback"]).rollback(str(photo_root))
    assert rb["restored"] == 8, rb
    # 清理冒烟产物（备份目录与台账），保持样例目录干净
    import shutil as _sh
    _sh.rmtree(photo_root / "_POS备份", ignore_errors=True)
    for f in photo_root.glob("POS处理台账_*.csv"):
        f.unlink()

    # 5) 换带页：高程列透传（不参与计算，原值入第4列）+ d.ms 分秒边界不漂移
    from app.gui.pages import GaussPage
    gp = GaussPage()
    gp.table_in.setRowCount(0)
    r = gp.table_in.rowCount(); gp.table_in.insertRow(r)
    for c, v in enumerate(("P1", "3456789.1234", "500000.0000", "1234.5678")):
        gp.table_in.setItem(r, c, QTableWidgetItem2(v))
    gp.run()
    assert gp.table_out.columnCount() == 4
    assert gp.table_out.item(0, 3).text() == "1234.5678", gp.table_out.item(0, 3).text()
    gp.rb_src_bl.setChecked(True); gp.rb_dst_bl.setChecked(True)
    gp.table_in.setRowCount(0)
    r = gp.table_in.rowCount(); gp.table_in.insertRow(r)
    for c, v in enumerate(("G1", "30.500000", "105.300000", "56.78")):
        gp.table_in.setItem(r, c, QTableWidgetItem2(v))
    gp.run()
    assert gp.table_out.item(0, 3).text() == "56.7800", gp.table_out.item(0, 3).text()
    assert abs(float(gp.table_out.item(0, 1).text()) - 30.5) < 5e-9, gp.table_out.item(0, 1).text()

    win.close()
    print(f"GUI smoke OK（页面 {len(win.pages)} 个；照片批处理 {res['ok']}/{res['total']}）")
    return 1


from PySide6.QtWidgets import QTableWidgetItem as QTableWidgetItem2  # noqa: E402

if __name__ == "__main__":
    run()
