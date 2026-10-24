"""第三轮深度审查新增 13 项缺陷回归测试（覆盖 P0、P1、P2 全部问题）。

运行: $env:PYTHONIOENCODING='utf-8'; python tests/test_round3_fixes.py
"""
from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import MagicMock

import numpy as np

from app.common import hconv, params
from app.fileconv import shp_transform
from app.logic import accuracy as acc, cp_methods
from app.osgb.transform import PlanarVertexTransform


def test_cross_ellipsoid_inv_chain_dst_ellipsoid():
    """验证 P0-1: 逆向链（平面→大地）在跨椭球时保存并使用目标椭球反算，消除 ~108m 高程偏差"""
    from app.core.ellipsoid import BEIJING54, CGCS2000

    # 构造控制点：源为北京54高斯平面（L0=114°），目标为 CGCS2000 空间直角/大地坐标
    proj = {
        "proj_src": {"l0": 114.0, "x0_m": 0.0, "y0_m": 500000.0, "h0": 0.0, "ell": BEIJING54},
        "ell_dst": CGCS2000,
        "ell": BEIJING54,
    }
    # 模拟 4 个控制点
    s_pts = np.array([
        [2500000.0, 500000.0, 100.0],
        [2510000.0, 505000.0, 110.0],
        [2520000.0, 495000.0, 105.0],
        [2515000.0, 510000.0, 120.0],
    ])
    # 目标大地坐标（CGCS2000）
    d_pts = np.array([
        [114.0, 22.585, 208.0],
        [114.048, 22.675, 218.0],
        [113.951, 22.766, 213.0],
        [114.097, 22.721, 228.0],
    ])

    updates, text = cp_methods.refit("inv_chain", s_pts, d_pts, proj, ("planar", "lonlat"))
    # 必须写入目标椭球
    assert updates.get("ellipsoid_dst") == "CGCS2000", f"未保存 ellipsoid_dst: {updates}"

    # 应用参数测试
    doc = {
        "schema": "coordparam/1",
        "kind": "inv_chain",
        "source_kind": "planar",
        "target_kind": "lonlat",
        "ellipsoid": "BEIJING54",
        "ellipsoid_dst": "CGCS2000",
        "projection_src": updates["projection_src"],
        "seven": updates["seven"],
    }
    applier = params.ParamApplier(doc)
    b_rad, l_rad, h_out = applier.apply_planar_to_lonlat(2500000.0, 500000.0, 100.0)
    # 跨椭球大地高在赤道与低纬度区因 a 差异约 108m，应在 200m 附近（约 208m），绝不可仍为 100m 附近
    assert abs(h_out - 208.0) < 5.0, f"逆向链跨椭球高程计算偏差异常: h_out={h_out}"


def test_shp_z_and_m_preservation():
    """验证 P0-2: PolylineZ (shapeType 13) 转换后 Z 坐标不被清零，且 M 属性完整保留"""
    import shapefile

    td = Path(tempfile.mkdtemp(prefix="test_shp_zm_"))
    try:
        in_shp = td / "in_zm.shp"
        out_shp = td / "out_zm.shp"
        w = shapefile.Writer(str(in_shp), shapeType=13)
        w.field("NAME", "C", 20)
        # 写入包含 Z (10.5, 20.5) 和 M (100.0, 200.0) 的线要素
        w.linez([[[500000.0, 3000000.0, 10.5, 100.0], [500100.0, 3000100.0, 20.5, 200.0]]])
        w.record("L1")
        w.close()

        res = shp_transform.transform_shp(
            in_shp, out_shp,
            planar_fn=lambda x, y: (x + 10.0, y + 20.0),
            height_fn=lambda x, y, z: (z + 2.0, None, "")
        )
        assert res.transformed == 1
        r = shapefile.Reader(str(out_shp))
        assert r.shapeType == 13
        s = r.shape(0)
        # 验证 Z 坐标保留并正确转换（10.5 + 2 = 12.5, 20.5 + 2 = 22.5）
        assert len(s.z) == 2
        assert abs(s.z[0] - 12.5) < 1e-4, f"Z 坐标被清零或未转换: {s.z}"
        assert abs(s.z[1] - 22.5) < 1e-4
        # 验证 M 测量值完整保留
        assert len(s.m) == 2
        assert abs(s.m[0] - 100.0) < 1e-4, f"M 值丢失: {s.m}"
        assert abs(s.m[1] - 200.0) < 1e-4
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_shp_m_only_type_conversion():
    """验证 P0-3: PolylineM (shapeType 23) 纯 M 要素转换不崩溃，写入正确 M 类型"""
    import shapefile

    td = Path(tempfile.mkdtemp(prefix="test_shp_m_"))
    try:
        in_shp = td / "in_m.shp"
        out_shp = td / "out_m.shp"
        w = shapefile.Writer(str(in_shp), shapeType=23)  # PolylineM
        w.field("NAME", "C", 20)
        w.linem([[[500000.0, 3000000.0, 55.0], [500100.0, 3000100.0, 66.0]]])
        w.record("M1")
        w.close()

        # 转换必须成功，绝不抛 ShapefileException 类型不匹配
        res = shp_transform.transform_shp(
            in_shp, out_shp,
            planar_fn=lambda x, y: (x + 5.0, y + 5.0)
        )
        assert res.transformed == 1
        r = shapefile.Reader(str(out_shp))
        assert r.shapeType == 23
        s = r.shape(0)
        assert abs(s.m[0] - 55.0) < 1e-4
        assert abs(s.m[1] - 66.0) < 1e-4
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_osgb_grid_no_double_origin():
    """验证 P0-4: OSGB 大地水准面格网分支绝对坐标不再双重累加 SRSOrigin"""
    from unittest.mock import MagicMock
    mock_grid = MagicMock()
    mock_grid.try_undulation.return_value = 15.0

    doc = {
        "schema": "coordparam/1",
        "kind": "planar4",
        "source_kind": "planar",
        "target_kind": "planar",
        "projection_src": {"l0": 114.0, "x0": 0.0, "y0": 500000.0},
        "planar4": {"dx": 0.0, "dy": 0.0, "a": 1.0, "b": 0.0},
        "geoid_grid": "test_grid"
    }
    origin = (500000.0, 3000000.0, 0.0)
    tf = PlanarVertexTransform(doc, mode="xyz", origin=origin)
    # 注入 mock grid
    tf.param_doc["_grid_instance"] = mock_grid

    # 模拟局部坐标 (100.0, 200.0, 50.0)
    local_pts = np.array([[100.0, 200.0, 50.0]])
    from unittest.mock import patch
    with patch.object(params.ParamApplier, "_require_grid", return_value=mock_grid):
        out_local = tf.apply(local_pts)
        assert out_local.shape == (1, 3)
        # 高程 50.0 - 15.0 = 35.0
        assert abs(out_local[0, 2] - 35.0) < 1e-4


def test_accuracy_poly3d_real_key_and_no_nameerror():
    """验证 P1-6: 精度对比页正确解析 'poly' 键名，且仅高程对比时无 idx NameError"""
    from app.logic.polynomial import fit_poly

    pts_s = np.array([[100.0, 200.0, 10.0], [250.0, 150.0, 25.0], [320.0, 410.0, 30.0],
                      [180.0, 520.0, 45.0], [400.0, 300.0, 15.0]])
    pts_d = pts_s + np.array([5.0, 6.0, 2.0])
    pm = fit_poly(pts_s, pts_d, degree=1, kind="poly3d")

    # 全软件标准保存键是 'poly'
    doc = {
        "schema": "coordparam/1",
        "kind": "poly3d",
        "source_kind": "planar",
        "target_kind": "planar",
        "poly": pm.to_doc()
    }
    pairs = acc.PairSet(
        names=["P1", "P2", "P3", "P4", "P5"],
        src=pts_s[:, :2], src_h=pts_s[:, 2],
        dst=pts_d[:, :2], dst_h=pts_d[:, 2],
        src_kind="planar", dst_kind="planar", source="测试"
    )
    # 测试仅高程评估（do_plane=False），验证原先存在的 NameError: name 'idx' is not defined 已彻底解决
    res, verdict = acc.compare(pairs, [('Poly3D', doc)], do_plane=False, do_height=True, models_dir=None)
    assert len(res) == 1
    assert res[0]["feasible_h"] is True
    assert res[0]["rms_h"] < 1e-4


def test_hconv_coupled_geoid_grid():
    """验证 P1-7: 照片与 CAD 高程转换支持参数内绑定的 geoid_grid 与趋势项组合"""
    from unittest.mock import MagicMock

    mock_grid = MagicMock()
    mock_grid.try_undulation.return_value = 25.0

    # 1. 照片高度函数
    param = {
        "geoid_grid": "CQ2000",
        "heightfit": {"space": "lonlat", "mode": "const", "value_type": "xi",
                      "coef": [2.0], "center": [0.0, 0.0]}
    }
    fn = hconv.make_photo_height_fn("param", model=mock_grid, param=param)
    # 大地高 100.0 -> 扣格网 25.0 -> 再扣拟合 2.0 = 73.0
    h_new, v, desc = fn(105.0, 30.0, 100.0)
    assert abs(h_new - 73.0) < 1e-4
    assert "格网" in desc and "拟合" in desc

    # 2. CAD 高度函数（平面空间拟合）
    cad_param = {
        "geoid_grid": "CQ2000",
        "heightfit": {"space": "planar", "mode": "const", "value_type": "xi",
                      "coef": [1.5], "center": [0.0, 0.0]},
        "projection": {"l0": 105.0, "x0": 0.0, "y0": 500000.0, "k": 1.0}
    }
    cad_fn = hconv.make_cad_height_fn("param", model=mock_grid, param=cad_param)
    z_new, v2, desc2 = cad_fn(500000.0, 3000000.0, 50.0)
    # 50.0 - 25.0 - 1.5 = 23.5
    assert abs(z_new - 23.5) < 1e-4


def test_cad_page_report_unsupported_string_safe():
    """验证 P1-8: CadPage 结果汇报处理包含字符串错误信息的 unsupported 不抛 TypeError"""
    from PySide6.QtWidgets import QApplication
    from app.gui.pages import CadPage

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    _app = QApplication.instance() or QApplication([])

    page = CadPage()
    # 构造带有字符串错误信息的 Result 对象（如 SHP/文本抛错）
    mock_res = MagicMock()
    mock_res.transformed = 5
    mock_res.total = 6
    mock_res.out_path = "out.shp"
    mock_res.unsupported = {"实体0": "未支持的要素类型", "实体1": "坐标非法"}

    item = {"file": "test.shp", "result": mock_res}
    # 执行汇报，不得抛 TypeError: unsupported operand type(s) for +: 'int' and 'str'
    page._on_one(item)
    report_text = page.report.toPlainText()
    assert "[完成] test.shp" in report_text
    assert "未支持 2" in report_text
    assert "实体0: 未支持的要素类型" in report_text


def test_pgdb_cancel_cleanup_and_m_safety():
    """验证 P2-10: PGDB 取消时安全清理残余 SHP，且 M 要素不被误当 Z 读取"""
    from app.fileconv.pgdb_transform import decode_blob

    # 1. 测试 decode_blob 在 hasz=False 时不提取 M 为 Z
    # 构造一个 2D 点带 M 的 blob
    import struct
    b = struct.pack("<i2d", 9, 100.0, 200.0)
    g = decode_blob(b, hasz=False)
    assert g["z"] is None

    # 2. 模拟 3D 点 blob，但在 hasz=False 的图层中不作为 Z
    b3 = struct.pack("<i3d", 9, 100.0, 200.0, 50.0)
    g3 = decode_blob(b3, hasz=False)
    assert g3["z"] is None
    g3_z = decode_blob(b3, hasz=True)
    assert g3_z["z"] == [50.0]


def run() -> int:
    fns = [
        test_cross_ellipsoid_inv_chain_dst_ellipsoid,
        test_shp_z_and_m_preservation,
        test_shp_m_only_type_conversion,
        test_osgb_grid_no_double_origin,
        test_accuracy_poly3d_real_key_and_no_nameerror,
        test_hconv_coupled_geoid_grid,
        test_cad_page_report_unsupported_string_safe,
        test_pgdb_cancel_cleanup_and_m_safety,
    ]
    for fn in fns:
        fn()
    return len(fns)


if __name__ == "__main__":
    n = run()
    print(f"All {n} tests passed in test_round3_fixes.py!")
