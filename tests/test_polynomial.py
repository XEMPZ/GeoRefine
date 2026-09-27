# -*- coding: utf-8 -*-
"""多项式转换功能全链路测试。

覆盖：引擎拟合/恢复精度、健壮性（点数不足/秩亏/损坏参数）、
from_doc 重建校验、ParamApplier poly2d/poly3d 应用分支（含方向互斥与
do_plane/do_height 拆分）、南方点对经精度对比 load_pairs 导入、
PolyPage 端到端（导入→拟合→存库）与 ApplyPage d.ms 角度归一化。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ------------------------------------------------- 引擎
def test_engine_exact_recovery():
    from app.logic.polynomial import eval_poly, fit_poly
    rng = np.random.RandomState(4)
    src = rng.uniform(-50, 50, (40, 3))
    A = rng.uniform(-1, 1, 10)
    dst = np.array([(A[0] + A[1] * x + A[2] * y + A[3] * h + A[4] * x * x + A[5] * y * y
                     + A[6] * h * h + A[7] * x * y + A[8] * x * h + A[9] * y * h)
                    for x, y, h in src])
    m = fit_poly(src, dst, 2, "poly3d")
    res = max(abs(np.array(eval_poly(s, m)) - d).max() for s, d in zip(src, dst))
    assert res < 1e-9
    src2 = src[:, :2]
    B = rng.uniform(-1, 1, 6)
    dst2 = np.array([[B[0] + B[1] * x + B[2] * y + B[3] * x * x + B[4] * x * y + B[5] * y * y]
                     for x, y in src2])
    m2 = fit_poly(src2, dst2, 2, "poly2d")
    res2 = max(abs(np.array(eval_poly(s, m2)) - d).max() for s, d in zip(src2, dst2))
    assert res2 < 1e-9


def test_engine_robustness():
    from app.logic.polynomial import fit_poly
    rng = np.random.RandomState(5)
    # 点数不足
    with pytest.raises(ValueError, match="点数不足|至少"):
        fit_poly(rng.uniform(0, 1, (9, 3)), rng.uniform(0, 1, (9, 1)), 2, "poly3d")
    # 秩亏（共线点）
    t = np.linspace(0, 1, 12)
    collinear = np.column_stack([t, 2 * t, np.zeros(12)])
    with pytest.raises(ValueError, match="秩亏"):
        fit_poly(collinear, rng.uniform(0, 1, (12, 1)), 2, "poly3d")
    # 未知类型
    with pytest.raises(ValueError, match="未知多项式类型"):
        fit_poly(rng.uniform(0, 1, (10, 2)), rng.uniform(0, 1, (10, 1)), 1, "poly4d")


def test_from_doc_and_corruption():
    from app.logic.polynomial import eval_poly, fit_poly
    rng = np.random.RandomState(6)
    src = rng.uniform(-10, 10, (20, 2))
    dst = np.column_stack([src[:, 0] * 2 + 1, src[:, 1] * 3 - 2])
    doc = fit_poly(src, dst, 1, "poly2d").to_doc()
    # 重建后求值一致
    from app.logic.polynomial import PolyModel
    m = PolyModel.from_doc(doc)
    assert np.allclose(eval_poly((5.0, -7.0), m), eval_poly((5.0, -7.0),
                       fit_poly(src, dst, 1, "poly2d")))
    # 各种损坏 → ValueError（不允许静默错算）
    for bad in ({}, {"kind": "poly2d"}, dict(doc, coefs=[[1, 2]]),
                dict(doc, degree=9), dict(doc, ndim=5), dict(doc, terms=[[0, 0]]),
                "not-a-dict", dict(doc, center=["x", "y"])):
        with pytest.raises(ValueError):
            PolyModel.from_doc(bad)


def test_check_src_plausible():
    from app.logic.polynomial import check_src_plausible
    assert check_src_plausible([[30.5, 115.25, 10.0], [31.1, 115.9, 12.0]], "lonlat") is None
    assert check_src_plausible([[130.0, 115.0, 0.0]], "lonlat") is not None   # B 超 90°
    assert check_src_plausible([[30.0, 400.0, 0.0]], "lonlat") is not None    # L 超 360°
    assert check_src_plausible([[2850000.0, 500000.0]], "planar") is None


def test_residual_summary():
    from app.logic.polynomial import residual_summary
    s = residual_summary([(3.0, 4.0, 1.0), (-3.0, -4.0, -1.0)])
    assert s["n"] == 2
    assert abs(s["rms_xy_m"] - 5.0) < 1e-12          # 点位残差均 5
    assert abs(s["max_xy_m"] - 5.0) < 1e-12
    assert abs(s["rms_h_m"] - 1.0) < 1e-12
    s2 = residual_summary([(1.0, 2.0)])
    assert s2["rms_h_m"] is None and s2["rms_xy_m"] is not None


# ------------------------------------------------- 参数应用链
def _poly2d_doc(tmpdir, name="poly2d测试"):
    from app.logic.polynomial import fit_poly
    src = np.array([[1000.0, 2000.0], [1100.0, 2000.0], [1000.0, 2100.0],
                    [1200.0, 2200.0], [1300.0, 1900.0], [1050.0, 2150.0]])
    dst = np.column_stack([src[:, 0] * 1.0001 + 12.5, src[:, 1] * 0.9999 - 8.3])
    doc = {"name": name, "kind": "poly2d", "ellipsoid": "CGCS2000",
           "source_kind": "planar", "target_kind": "planar",
           "poly": fit_poly(src, dst, 1, "poly2d").to_doc(),
           "accuracy": {"n_points": 6, "rms_xy_m": 0.0, "note": "测试"}}
    p = Path(tmpdir) / "poly2d.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc, src, dst


def _poly3d_doc(tmpdir, name="poly3d测试"):
    from app.logic.polynomial import fit_poly
    rng = np.random.RandomState(9)
    src = np.column_stack([rng.uniform(30.0, 31.0, 12),     # B（度）
                           rng.uniform(115.0, 116.0, 12),   # L（度）
                           rng.uniform(0.0, 100.0, 12)])    # H（米）
    dst = np.column_stack([1000 + 111000 * src[:, 0] + 5 * src[:, 2],
                           2000 + 95000 * src[:, 1] + 3 * src[:, 2],
                           10 + 2 * src[:, 2]])
    doc = {"name": name, "kind": "poly3d", "ellipsoid": "CGCS2000",
           "source_kind": "lonlat", "target_kind": "planar",
           "poly": fit_poly(src, dst, 1, "poly3d").to_doc(),
           "accuracy": {"n_points": 12, "rms_xy_m": 0.0, "rms_h_m": 0.0, "note": "测试"}}
    p = Path(tmpdir) / f"{name}.json"   # ParamLibrary 按参数名（安全化）查找文件
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc, src, dst


def test_apply_poly2d(tmp_path):
    from app.common.params import ParamApplier
    from app.logic.polynomial import eval_poly
    doc, src, dst = _poly2d_doc(tmp_path)
    ap = ParamApplier(doc)
    x, y, h = ap.apply_planar(1050.0, 2050.0, 7.0)
    ex, ey = eval_poly((1050.0, 2050.0), ap._poly)[:2]
    assert abs(x - ex) < 1e-9 and abs(y - ey) < 1e-9 and h == 7.0   # 高程透传
    # do_plane=False → 原值透传
    x2, y2, h2 = ap.apply_planar(1050.0, 2050.0, 7.0, do_plane=False)
    assert (x2, y2, h2) == (1050.0, 2050.0, 7.0)
    # 方向互斥：poly2d 不能用于大地输入
    with pytest.raises(ValueError, match="大地"):
        ap.apply_lonlat(30.0, 115.0, 0.0)
    # 损坏 poly → 构造即报错
    bad = dict(doc, poly={"kind": "poly2d"})
    with pytest.raises(ValueError, match="参数不完整|损坏"):
        ParamApplier(bad)


def test_apply_poly3d(tmp_path):
    from app.common.params import ParamApplier
    from app.logic.polynomial import eval_poly
    doc, src, dst = _poly3d_doc(tmp_path)
    ap = ParamApplier(doc)
    b, l, h = 30.5, 115.25, 50.0
    x, y, h2 = ap.apply_lonlat(b, l, h)
    ex, ey, eh = eval_poly((b, l, h), ap._poly)
    assert abs(x - ex) < 1e-9 and abs(y - ey) < 1e-9 and abs(h2 - eh) < 1e-9
    # do_height=False → 高程透传；do_plane=False → 大地坐标透传
    x2, y2, h3 = ap.apply_lonlat(b, l, h, do_height=False)
    assert h3 == h and abs(x2 - x) < 1e-9
    x3, y3, h4 = ap.apply_lonlat(b, l, h, do_plane=False)
    assert (x3, y3) == (b, l)
    # 方向互斥：poly3d(大地源) 不能用于平面输入
    with pytest.raises(ValueError, match="平面"):
        ap.apply_planar(1000.0, 2000.0, 0.0)


# ------------------------------------------------- 精度对比：南方点对
def test_accuracy_load_pairs_south(tmp_path):
    from app.logic.accuracy import load_pairs
    p = tmp_path / "pairs.txt"
    p.write_text("# 南方公共点对\n"
                 "K1,1000.000,2000.000,10.000,1012.500,1988.400,11.200\n"
                 "K2,1100.000,2000.000,10.500,1112.400,1988.500,11.700\n"
                 "K3,1000.000,2100.000,9.800,1012.300,2088.600,11.000\n", encoding="utf-8")
    ps = load_pairs(str(p))
    assert ps.src_kind == "planar" and ps.dst_kind == "planar"
    assert len(ps.names) == 3
    assert abs(ps.src[0][0] - 1000.0) < 1e-9 and abs(ps.dst[0][0] - 1012.5) < 1e-9


# ------------------------------------------------- GUI 端到端
def _quiet_boxes():
    import app.gui.pages as pm
    pm.QMessageBox.information = staticmethod(lambda *a, **k: None)
    pm.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    pm.QMessageBox.critical = staticmethod(lambda *a, **k: None)


def test_poly_page_end_to_end(tmp_path):
    """PolyPage：南方 7 列文件导入 → 拟合 → 存库 → 参数可被 ParamApplier 应用。"""
    _quiet_boxes()
    import app.gui.pages as pm
    old_dir = pm.PARAMS_DIR
    pm.PARAMS_DIR = tmp_path
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.gui.pages import POLY_DISCLAIMER, PolyPage
        assert "不适用于转换外推" in POLY_DISCLAIMER and "一般不用这个" in POLY_DISCLAIMER

        f = tmp_path / "south_pairs.txt"
        rows = ["# name,旧x,旧y,旧h,新x,新y,新h"]
        pts = [(1000 + 60 * (i % 4) + 13 * (i // 4), 2000 + 80 * (i // 4) + 29 * (i % 4),
                10 + 0.5 * i) for i in range(8)]
        for i, (x, y, h) in enumerate(pts):
            rows.append(f"K{i+1},{x:.3f},{y:.3f},{h:.3f},{x + 12.0:.3f},"
                        f"{y - 7.5:.3f},{h + 1.2:.3f}")
        f.write_text("\n".join(rows), encoding="utf-8")

        page = PolyPage()
        page.load_file(str(f))
        assert page.table.rowCount() >= 8
        page.name_edit.setText("平移测试参数")
        page.degree_spin.setValue(1)
        page.fit_and_save()
        saved = list(tmp_path.glob("*.json"))
        assert saved, "拟合后应已保存参数"
        doc = json.loads(saved[0].read_text(encoding="utf-8"))
        # 平面点对：默认投影 L0 有值 → 模型走大地层（反算-拟合-正算闭环），数学等价
        assert doc["kind"] == "poly2d" and doc["source_kind"] in ("planar", "lonlat")
        assert doc["source_kind"] == doc["target_kind"]
        assert "不适用于转换外推" in (doc.get("accuracy", {}).get("note") or "")
        # 保存的参数立即可用，且恢复出严格的平移关系
        from app.common.params import ParamApplier
        ap = ParamApplier(doc)
        x, y, h = ap.apply_planar(1100.0, 2100.0, 12.0)
        assert abs(x - 1112.0) < 1e-6 and abs(y - 2092.5) < 1e-6
        assert h == 12.0   # poly2d 高程透传（不做高程变换）
    finally:
        pm.PARAMS_DIR = old_dir


def test_apply_page_dms_normalization(tmp_path):
    """ApplyPage：d.ms 编码输入须归一化为十进制度再进引擎（此前死代码导致的隐患）。"""
    _quiet_boxes()
    import app.gui.pages as pm
    old_dir = pm.PARAMS_DIR
    pm.PARAMS_DIR = tmp_path
    try:
        doc, src, dst = _poly3d_doc(tmp_path, name="dms测试")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.gui.pages import ApplyPage
        page = ApplyPage()
        page.reload_params()
        # 十进制度 30.5 → d.ms 30.3000；115.25 → 115.1500；表内填 d.ms 编码
        for r, (b, l, h) in enumerate([(30.5, 115.25, 50.0), (30.75, 115.5, 60.0)]):
            page.table_in.insertRow(page.table_in.rowCount())
            b_dms = int(b) + (b - int(b)) * 60 / 100.0
            l_dms = int(l) + (l - int(l)) * 60 / 100.0
            for c, v in enumerate([f"T{r+1}", f"{b_dms:.4f}", f"{l_dms:.4f}", f"{h:.1f}"]):
                page.table_in.setItem(r, c, pm.QTableWidgetItem(v))
        page.chk_do_plane.setChecked(True)
        page.chk_do_height.setChecked(True)
        page.run()
        assert page.table_out.rowCount() >= 2, "转换应有输出"
        from app.common.params import ParamApplier
        from app.logic.polynomial import eval_poly
        ap = ParamApplier(doc)
        ex = eval_poly((30.5, 115.25, 50.0), ap._poly)
        got = float(page.table_out.item(0, 1).text())
        assert abs(got - ex[0]) < 1e-3, f"d.ms 归一化失败：{got} vs {ex[0]}"
    finally:
        pm.PARAMS_DIR = old_dir
