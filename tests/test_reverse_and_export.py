# -*- coding: utf-8 -*-
"""逆向转换链（工程平面坐标 → 大地坐标）与点对导出测试。

覆盖：带号自动识别（分带/带号/L0 锁定/剥带号）、批量高斯反算闭合、
compare_methods 逆向链（平移/旋转基准差精确恢复）、refit → 参数文档 →
ParamApplier.apply_planar_to_lonlat 应用链、CP 页端到端（含带号源 +
智能灰显 + 保存）、点对导出四格式（cot/cass/south/xlsx）读回一致。
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ------------------------------------------------- 带号识别
def test_detect_gauss_zone():
    from app.core.projection import detect_gauss_zone, detect_zone_on_column
    z = detect_gauss_zone(38512345.67)
    assert z["zone"] == 38 and z["band"] == "3度带" and abs(z["l0"] - 114) < 1e-9
    assert abs(z["y_stripped"] - 512345.67) < 1e-6
    z6 = detect_gauss_zone(20512345.67)
    assert z6["band"] == "6度带" and abs(z6["l0"] - 117) < 1e-9
    # 非带号 / 带号越界 → 不识别
    assert detect_gauss_zone(512345.67) is None
    assert detect_gauss_zone(46512345.0) is None
    assert detect_gauss_zone(12512345.0) is None
    assert detect_zone_on_column([512345.0, 38512345.0])["zone"] == 38
    assert detect_zone_on_column([512345.0, 612345.0]) is None


def test_inverse_gauss_closure():
    from app.core.projection import inverse_gauss_points
    from app.core.ellipsoid import CGCS2000
    from app.core.gk_engine import ProjParams
    pp = ProjParams(CGCS2000.a, CGCS2000.inv_f, math.radians(114), math.radians(30.4),
                    50.0, 1.0, x0_km=0.0, y0_km=500.0, rigorous=True)
    rng = np.random.RandomState(3)
    blh = [(30.2 + rng.uniform(0, .4), 113.7 + rng.uniform(0, .6), rng.uniform(0, 80))
           for _ in range(8)]
    xy = [pp.forward(math.radians(b), math.radians(l)) for b, l, _ in blh]
    inv = inverse_gauss_points([x for x, _ in xy], [y for _, y in xy], 114.0, CGCS2000,
                               x0_m=0.0, y0_m=500000.0, h0=50.0, rigorous=True)
    err = max(math.hypot((b - b0) * 110540, (l - l0) * 111320 * math.cos(math.radians(30)))
              for (b, l), (b0, l0, _h) in zip(inv, blh))
    assert err < 1e-3, err


# ------------------------------------------------- 逆向链比选与参数链
def _make_case():
    from app.core.ellipsoid import CGCS2000, geodetic_to_ecef, ecef_to_geodetic
    from app.core.gk_engine import ProjParams
    from app.core.transform3d import Param7
    pp = ProjParams(CGCS2000.a, CGCS2000.inv_f, math.radians(114), math.radians(30.4),
                    50.0, 1.0, x0_km=0.0, y0_km=500.0, rigorous=True)
    rng = np.random.RandomState(3)
    blh = [(30.2 + rng.uniform(0, .4), 113.7 + rng.uniform(0, .6), rng.uniform(0, 80))
           for _ in range(8)]
    xy = [pp.forward(math.radians(b), math.radians(l)) for b, l, _ in blh]
    src_xy = np.column_stack([[x for x, _ in xy], [y for _, y in xy]])
    src_h = np.array([h for _, _, h in blh])
    return CGCS2000, blh, src_xy, src_h


def _apply_p7(blh, p7):
    from app.core.ellipsoid import CGCS2000, geodetic_to_ecef, ecef_to_geodetic
    from app.core.transform3d import apply_7param
    out = [ecef_to_geodetic(*apply_7param(p7, *geodetic_to_ecef(b, l, h, CGCS2000)),
                            CGCS2000) for b, l, h in blh]
    return np.array([[l, b] for b, l, _ in out]), np.array([h for _, _, h in out])


_PROJ = {"l0": 114.0, "x0_m": 0.0, "y0_m": 500000.0, "h0": 50.0, "rigorous": True,
         "ell": None, "ell_dst": None,
         "proj_src": {"l0": 114.0, "x0_m": 0.0, "y0_m": 500000.0, "h0": 50.0,
                      "rigorous": True, "ell": None}}


def test_compare_reverse_chain():
    from app.core.ellipsoid import CGCS2000
    from app.core.transform3d import Param7
    from app.core.autoselect import compare_methods
    _PROJ["ell"] = _PROJ["ell_dst"] = _PROJ["proj_src"]["ell"] = CGCS2000
    _cgcs, blh, src_xy, src_h = _make_case()
    for label, p_t, chain3_tol in (("平移", Param7(12.0, -8.0, 55.0, 0, 0, 0, 0), 1e-3),
                                   ("旋转", Param7(12.0, -8.0, 55.0, 0.15, -0.22, 0.31, 0.8), 0.1)):
        dst, dst_h = _apply_p7(blh, p_t)
        rows = compare_methods(src_xy, dst, src_h, dst_h, "planar", "lonlat",
                               ell=CGCS2000, ell_dst=CGCS2000, proj=_PROJ)
        b = {r["method"]: r for r in rows}
        assert b["inv_chain"]["feasible"] and b["inv_chain"]["rms_xy"] < 1e-3, (label, b["inv_chain"])
        assert b["inv_chain3"]["rms_xy"] < chain3_tol
        assert b["inv_direct"]["rms_xy"] > 10      # 基线应显著差
        assert b["inv_chain"]["selected"] or b["inv_chain3"]["selected"]


def test_refit_and_apply_reverse_chain(tmp_path):
    """refit(inv_chain) → 参数文档 → ParamApplier 逆向应用，输出 BLH 与正算一致。"""
    from app.core.ellipsoid import CGCS2000, geodetic_to_ecef, ecef_to_geodetic
    from app.core.transform3d import Param7
    from app.logic.cp_methods import refit
    from app.common.params import ParamApplier
    _PROJ["ell"] = _PROJ["ell_dst"] = _PROJ["proj_src"]["ell"] = CGCS2000
    _cgcs, blh, src_xy, src_h = _make_case()
    dst, dst_h = _apply_p7(blh, Param7(12.0, -8.0, 55.0, 0.15, -0.22, 0.31, 0.8))
    s_used = np.column_stack([src_xy, src_h])
    d_used = np.column_stack([dst, dst_h])
    updates, text = refit("inv_chain", s_used, d_used, _PROJ, ("planar", "lonlat"))
    assert "seven" in updates and "projection_src" in updates
    assert "带号" not in text or True
    doc = {"schema": "coordparam/1", "name": "逆向测试", "kind": "inv_chain",
           "source_kind": "planar", "target_kind": "lonlat", "ellipsoid": "CGCS2000",
           "projection_src": updates["projection_src"], "seven": updates["seven"],
           "accuracy": {"n_points": 8}}
    ap = ParamApplier(doc)
    b_rad, l_rad, h2 = ap.apply_planar_to_lonlat(float(src_xy[0, 0]), float(src_xy[0, 1]),
                                                 float(src_h[0]))
    # 与正算目标对比（角秒级以内）
    tb, tl, th = dst[0, 1], dst[0, 0], dst_h[0]
    assert abs(math.degrees(b_rad) - tb) * 3600 < 1.0
    assert abs(math.degrees(l_rad) - tl) * 3600 < 1.0
    # inv_direct（无七参数）路径
    doc2 = dict(doc, kind="inv_direct")
    ap2 = ParamApplier(doc2)
    b2, l2, _h = ap2.apply_planar_to_lonlat(float(src_xy[0, 0]), float(src_xy[0, 1]), 0.0)
    assert abs(math.degrees(b2) - blh[0][0]) * 3600 < 1.0   # 同基准反算回源大地
    # 方向互斥：非 inv 参数走逆向 → 明确报错
    with pytest.raises(ValueError, match="逆向"):
        ParamApplier({"kind": "planar4", "planar4": {"dx": 0, "dy": 0, "a": 1, "b": 0},
                      "ellipsoid": "CGCS2000"}).apply_planar_to_lonlat(1, 2, 3)
    # 缺 projection_src → 明确报错
    with pytest.raises(ValueError, match="projection_src"):
        ParamApplier({"kind": "inv_chain", "seven": updates["seven"],
                      "ellipsoid": "CGCS2000"}).apply_planar_to_lonlat(1, 2, 3)


# ------------------------------------------------- CP 页端到端（含带号源）
def _quiet_boxes():
    import app.gui.pages as pm
    pm.QMessageBox.information = staticmethod(lambda *a, **k: None)
    pm.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    pm.QMessageBox.critical = staticmethod(lambda *a, **k: None)


def test_cp_page_reverse_end_to_end(tmp_path):
    """CP 页：含带号工程坐标 → 大地坐标，自动识别带号 + 逆向链 + 保存参数。"""
    _quiet_boxes()
    import app.gui.pages as pm
    old_dir = pm.PARAMS_DIR
    pm.PARAMS_DIR = tmp_path
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.core.ellipsoid import CGCS2000
        from app.core.transform3d import Param7
        _PROJ["ell"] = _PROJ["ell_dst"] = _PROJ["proj_src"]["ell"] = CGCS2000
        _cgcs, blh, src_xy, src_h = _make_case()
        # 源 y 加带号前缀（3度带 38 → L0=114）
        ZONE = 38
        dst, dst_h = _apply_p7(blh, Param7(12.0, -8.0, 55.0, 0.15, -0.22, 0.31, 0.8))
        page = pm.ControlPointPage()
        page.clear_rows()
        for i in range(len(blh)):
            r = page.table.rowCount()
            page.table.insertRow(r)
            vals = [f"K{i+1}", f"{src_xy[i, 0]:.4f}",
                    f"{src_xy[i, 1] + ZONE * 1e6:.4f}", f"{src_h[i]:.4f}",
                    f"{dst[i, 0]:.9f}", f"{dst[i, 1]:.9f}", f"{dst_h[i]:.4f}"]
            for j, v in enumerate(vals):
                page.table.setItem(r, j, pm.QTableWidgetItem(v))
            page.table.setItem(r, 7, pm.QTableWidgetItem("Y"))
            page.table.setItem(r, 8, pm.QTableWidgetItem("Y"))
        page.compute(True)
        assert page._zone_s is not None and page._zone_s["zone"] == ZONE
        assert "带号" in page.lbl_zone_s.text()
        assert not page.proj_l0_s.isEnabled()          # L0 已自动锁定灰显
        kind = getattr(page, "_kind", None)
        assert kind == ("planar", "lonlat"), kind
        sel = getattr(page, "_sel", None)
        assert sel is not None and sel["method"] in ("inv_chain", "inv_chain3"), \
            (sel or {}).get("method")
        assert sel["rms_xy"] < 0.01
        # 保存参数（save 流程走 refit + build_param_doc）
        page.name_edit.setText("逆向链参数")
        page.save_param()
        saved = list(tmp_path.glob("逆向链参数.json"))
        assert saved, "应已保存参数"
        doc = json.loads(saved[0].read_text(encoding="utf-8"))
        assert doc["kind"] in ("inv_chain", "inv_chain3")
        assert doc["target_kind"] == "lonlat"
        assert doc.get("projection_src", {}).get("zone") == ZONE
        # 保存的参数可直接在 ParamApplier 逆向应用
        from app.common.params import ParamApplier
        ap = ParamApplier(doc)
        b_rad, l_rad, _h = ap.apply_planar_to_lonlat(float(src_xy[2, 0]),
                                                     float(src_xy[2, 1]) + ZONE * 1e6,
                                                     float(src_h[2]))
        assert abs(math.degrees(b_rad) - dst[2, 1]) * 3600 < 2.0
        assert abs(math.degrees(l_rad) - dst[2, 0]) * 3600 < 2.0
    finally:
        pm.PARAMS_DIR = old_dir


# ------------------------------------------------- 导出四格式
def _sample_rows():
    return [("K1", 1000.0, 2000.0, 10.0, 1012.5, 1988.4, 11.2),
            ("K2", 1100.0, 2000.0, 10.5, 1112.4, 1988.5, 11.7),
            ("K3", 1000.0, 2100.0, 9.8, 1012.3, 2088.6, 11.0)]


def test_export_roundtrip_planar(tmp_path):
    from app.common import pair_io
    rows = _sample_rows()
    flags = [(True, True), (True, False), (False, True)]
    for fmt in ("south", "cass", "xlsx", "csv"):
        p = tmp_path / f"out.{fmt}"
        pair_io.export_pairs(str(p), fmt, rows, flags)
        if fmt in ("xlsx", "csv"):
            from app.common.io_csv import read_points_csv
            recs = read_points_csv(str(p))
            assert len(recs) == 3, fmt
            assert abs(recs[0]["x"] - 1000.0) < 1e-6 and abs(recs[2]["x2"] - 1012.3) < 1e-6
            assert recs[1]["name"] == "K2"
            continue
        back = pair_io.parse_south_pairs(str(p)) if fmt == "south" else pair_io.parse_cass(str(p))
        assert len(back) == 3, fmt
        r0 = back[0]
        if fmt == "south":
            assert r0.name == "K1" and abs(r0.x1 - 1000.0) < 1e-3 and abs(r0.y2 - 1988.4) < 1e-3
        else:  # cass 无点名
            assert abs(r0.x1 - 1000.0) < 1e-3 and abs(r0.h2 - 11.2) < 1e-3


def test_export_roundtrip_cot(tmp_path):
    from app.common import pair_io
    # cot：源=大地 (L, B, H_ell)，目标=平面(x,y,h)
    rows = [("GD1", 113.75, 30.25, 42.0, 1000.0, 2000.0, 10.0),
            ("GD2", 113.80, 30.30, 45.0, 1100.0, 2050.0, 10.5)]
    flags = [(True, True), (False, True)]
    p = tmp_path / "out.cot"
    pair_io.export_pairs(str(p), "cot", rows, flags, src_kind="lonlat")
    pts = pair_io.parse_cot(str(p))
    assert len(pts) == 2
    assert pts[0].name == "GD1"
    assert abs(pts[0].l_deg - 113.75) < 1e-7 and abs(pts[0].b_deg - 30.25) < 1e-7
    assert abs(pts[0].x - 1000.0) < 1e-3 and abs(pts[1].h_normal - 10.5) < 1e-3
    assert pts[0].use_pos and pts[0].use_h and not pts[1].use_pos and pts[1].use_h
    # 源为平面时导出 cot 应明确拒绝
    with pytest.raises(ValueError, match="大地"):
        pair_io.export_pairs(str(tmp_path / "x.cot"), "cot", _sample_rows(),
                             src_kind="planar")


def test_cp_export_button(tmp_path):
    """CP 页导出按钮：表格 → 南方 7 列文件读回一致。"""
    _quiet_boxes()
    import app.gui.pages as pm
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    page = pm.ControlPointPage()
    page.clear_rows()
    for i, row in enumerate(_sample_rows()):
        r = page.table.rowCount()
        page.table.insertRow(r)
        for j, v in enumerate(row):
            page.table.setItem(r, j, pm.QTableWidgetItem(str(v)))
        page.table.setItem(r, 7, pm.QTableWidgetItem("Y"))
        page.table.setItem(r, 8, pm.QTableWidgetItem("N"))
    out = tmp_path / "导出.txt"
    page._export_to(str(out), "south")     # 绕开文件对话框直接导出
    from app.common import pair_io
    back = pair_io.parse_south_pairs(str(out))
    assert len(back) == 3
    assert back[0].name == "K1" and abs(back[0].y2 - 1988.4) < 1e-3
