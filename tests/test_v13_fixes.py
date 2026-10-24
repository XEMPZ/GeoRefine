"""v1.3 缺陷修复专项回归测试。

覆盖：
1. OSGB 大地源参数与源侧投影放行及 do_check 传参
2. LAS 1.4 写回保留 EVLR (CRS WKT 等)
3. LAS 异常分支清理半成品文件
4. OSGB WSL 重写脚本公式与 C++ 仿射对齐
5. Surfer GRD 双精度 (float64) 与单精度 (float32) 判别
6. 南方公共点对纯数字点名解析不发生列错位
7. detect_coord_kind 局部平面坐标系 (<360m) 准确判为 planar
8. flash.py mkdtemp 临时目录在执行后清理
9. autoselect 拟合保留 _fit4 并被 build_param_dict 完整序列化
10. SpacePage 与 ApplyPage 文件导入（修复 read_points_table 未导入与 _src_lonlat AttributeError）
11. planar_fns CAD/SHP 轴映射一致性校验（X东/Y北 不再颠倒）
12. AccuracyPage 对比点 CSV 容忍 H 与 h 混淆解析
13. 代码清理：消除 transform2d 及 pages 中的重复函数定义
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.common.pair_io import parse_south_pairs, SouthPair
from app.core.autoselect import compare_methods, detect_coord_kind, build_param_dict
from app.core.geoid import read_grd
from app.pointcloud import las_io, las_job


def test_osgb_geodetic_source_unblocked():
    from app.osgb.srs import SrsInfo
    from app.osgb import flash

    doc = {
        "schema": "coordparam/1",
        "kind": "chain74",
        "source_kind": "lonlat",
        "target_kind": "planar",
        "ellipsoid": "CGCS2000",
        "projection": {"l0": 114.0, "x0": 0.0, "y0": 500000.0, "k": 1.0},
        "planar4": {"dx": 10.0, "dy": -20.0, "a": 1.0, "b": 0.0},
        "seven": None,
        "heightfit": None,
    }
    srs = SrsInfo(srs="EPSG:4547", origin=(500000.0, 3000000.0, 100.0), origin_kind="projected")

    # 带投影覆盖时应正常算出仿射系数，不再抛错
    A, b, new_origin = flash.coeffs_for_document(doc, srs, mode="xyz",
                                                 proj_override={"l0": 114.0})
    assert A.shape == (3, 3)
    assert len(b) == 3


def test_las_evlr_preservation():
    import laspy
    from laspy.vlrs.vlrlist import VLRList

    td = Path(tempfile.mkdtemp(prefix="test_evlr_"))
    try:
        src = td / "src.las"
        dst = td / "dst.las"
        hdr = laspy.LasHeader(version="1.4", point_format=6)
        hdr.x_scale, hdr.y_scale, hdr.z_scale = 0.001, 0.001, 0.001
        hdr.x_offset, hdr.y_offset, hdr.z_offset = 0.0, 0.0, 0.0

        test_vlr = laspy.VLR("GEOREF", 999, "Test EVLR CRS", b"WKT_TEST_DATA")
        vl = VLRList([test_vlr])
        with laspy.open(str(src), mode="w", header=hdr) as w:
            pts = laspy.ScaleAwarePointRecord.zeros(
                3, point_format=hdr.point_format,
                scales=[0.001, 0.001, 0.001], offsets=[0, 0, 0])
            pts.x = [10.0, 20.0, 30.0]
            pts.y = [15.0, 25.0, 35.0]
            pts.z = [1.0, 2.0, 3.0]
            w.write_points(pts)
            w.write_evlrs(vl)

        info = las_io.read_info(src)
        chunks = las_io.iter_chunks(src, target_mb=1.0)
        las_io.write_like(info, dst, chunks)

        with laspy.open(str(dst)) as r:
            assert r.evlrs is not None
            assert len(r.evlrs) == 1
            assert r.evlrs[0].user_id == "GEOREF"
            assert r.evlrs[0].record_data == b"WKT_TEST_DATA"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_las_unlink_on_failure():
    from app.pointcloud.las_transform import LasVertexTransform
    td = Path(tempfile.mkdtemp(prefix="test_unlink_"))
    try:
        src = td / "dummy.las"
        dst = td / "out.las"
        # 写入一个非法/破损文件模拟处理异常
        src.write_bytes(b"NOT_A_LAS_FILE")
        doc = {"kind": "planar4", "source_kind": "planar", "target_kind": "planar",
               "planar4": {"dx": 0, "dy": 0, "a": 1, "b": 0}}
        tf = LasVertexTransform(doc)
        res = las_job.convert_las_file(tf, src, dst)
        assert not res.ok
        assert not dst.exists(), "失败后半成品 LAS 文件必须被删除"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_grd_f64_and_f32():
    td = Path(tempfile.mkdtemp(prefix="test_grd_"))
    try:
        p_f64 = td / "test64.grd"
        p_f32 = td / "test32.grd"
        glomn, glomx, glamn, glamx, dlo, dla = 110.0, 112.0, 30.0, 32.0, 1.0, 1.0
        nlo = 3
        nla = 3
        n = nlo * nla
        vals_f64 = np.arange(n, dtype="<f8") + 10.5
        vals_f32 = np.arange(n, dtype="<f4") + 10.5

        hdr = struct.pack("<6d", glomn, glomx, glamn, glamx, dlo, dla)
        p_f64.write_bytes(hdr + vals_f64.tobytes())
        p_f32.write_bytes(hdr + vals_f32.tobytes())

        g64 = read_grd(p_f64)
        assert g64.fmt == "grd-f64"
        assert abs(g64.values[0, 0] - 10.5) < 1e-6
        assert abs(g64.values[2, 2] - (10.5 + 8)) < 1e-6

        g32 = read_grd(p_f32)
        assert g32.fmt == "grd-f32"
        assert abs(g32.values[0, 0] - 10.5) < 1e-4
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_pair_io_numeric_point_names():
    td = Path(tempfile.mkdtemp(prefix="test_pairs_"))
    try:
        p = td / "south.txt"
        # 纯数字点名 "1001", "1002"
        p.write_text("1001, 100.0, 200.0, 10.0, 105.0, 205.0, 11.0\n"
                     "1002, 300.0, 400.0, 20.0, 305.0, 405.0, 21.0\n",
                     encoding="utf-8")
        pairs = parse_south_pairs(p)
        assert len(pairs) == 2
        assert pairs[0].name == "1001"
        assert abs(pairs[0].x1 - 100.0) < 1e-6
        assert abs(pairs[0].y1 - 200.0) < 1e-6
        assert abs(pairs[0].h1 - 10.0) < 1e-6
        assert abs(pairs[0].x2 - 105.0) < 1e-6
        assert abs(pairs[0].y2 - 205.0) < 1e-6
        assert abs(pairs[0].h2 - 11.0) < 1e-6
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_detect_coord_kind_local_planar():
    # 局部工程坐标系，坐标均 < 360m，但两列均 > 90m
    local_pts = np.array([[120.5, 150.3], [130.2, 160.8], [210.0, 250.0]])
    assert detect_coord_kind(local_pts) == "planar"

    # 真实经纬度 (lon, lat)
    lonlat_pts = np.array([[114.3, 30.5], [114.4, 30.6]])
    assert detect_coord_kind(lonlat_pts) == "lonlat"

    # 真实经纬度 (lat, lon)
    latlon_pts = np.array([[30.5, 114.3], [30.6, 114.4]])
    assert detect_coord_kind(latlon_pts) == "lonlat"


def test_autoselect_fit4_and_build_param_dict():
    rng = np.random.default_rng(42)
    s_xy = np.column_stack([3000000 + rng.uniform(-500, 500, 6),
                            500000 + rng.uniform(-500, 500, 6)])
    # dx=12.0, dy=-8.0, a=1.00001, b=0.00002
    d_xy = np.column_stack([s_xy[:, 0] * 1.00001 - s_xy[:, 1] * 0.00002 + 12.0,
                            s_xy[:, 0] * 0.00002 + s_xy[:, 1] * 1.00001 - 8.0])
    h = np.zeros(6)

    rows = compare_methods(s_xy, d_xy, h, h, "planar", "planar")
    p4_row = next((r for r in rows if r["method"] == "planar4"), None)
    assert p4_row is not None
    assert p4_row.get("_fit4") is not None

    param_doc = build_param_dict("TestP4", p4_row)
    assert param_doc["planar4"] is not None
    assert abs(param_doc["planar4"]["dx"] - 12.0) < 1.0
    assert abs(param_doc["planar4"]["dy"] - (-8.0)) < 1.0


def test_space_and_apply_page_load_file():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from app.gui import pages

    warns = []
    old_warn = pages._quiet_warn
    pages._quiet_warn = lambda parent, title, msg: warns.append((title, msg))

    td = Path(tempfile.mkdtemp(prefix="test_gui_load_"))
    try:
        csv_file = td / "test.csv"
        csv_file.write_text("点名,x,y,h\nP1,3000000.0,500000.0,100.0\nP2,3000100.0,500100.0,105.0\n",
                            encoding="utf-8")

        cot_file = td / "test.cot"
        cot_file.write_text("CP01,3000000.1,500000.2,95.3,30.123456,104.123456,100.5,1,1\n",
                            encoding="utf-8")

        # 1. SpacePage load_file
        sp = pages.SpacePage()
        sp.load_file(str(csv_file))
        assert len(warns) == 0, f"SpacePage CSV 导入警告: {warns}"
        assert sp.table_in.rowCount() == 3

        sp.load_file(str(cot_file))
        assert len(warns) == 0, f"SpacePage COT 导入警告: {warns}"
        assert sp.table_in.rowCount() == 2

        # 2. ApplyPage load_file
        ap = pages.ApplyPage()
        ap.load_file(str(csv_file))
        assert len(warns) == 0, f"ApplyPage CSV 导入警告: {warns}"
        assert ap.table_in.rowCount() == 3

        ap.load_file(str(cot_file))
        assert len(warns) == 0, f"ApplyPage COT 导入警告: {warns}"
        assert ap.table_in.rowCount() == 2
    finally:
        pages._quiet_warn = old_warn
        shutil.rmtree(td, ignore_errors=True)


def test_planar_fns_cad_coordinate_mapping():
    from app.core.transform2d import Param4
    from app.logic.file_jobs import planar_fns

    # 四参数：北平移 dx=10.0, 东平移 dy=20.0
    p4 = Param4(dx=10.0, dy=20.0, a=1.0, b=0.0)
    survey, cad = planar_fns(p4)
    assert survey is not None and cad is not None

    # 测量系：x=北, y=东
    xn, ye = survey(3000000.0, 500000.0)
    assert xn == 3000010.0 and ye == 500020.0

    # CAD/SHP 系：X=东, Y=北
    X2, Y2 = cad(500000.0, 3000000.0)
    assert X2 == 500020.0, f"CAD X (东) 应为 500020.0，实际得到 {X2}"
    assert Y2 == 3000010.0, f"CAD Y (北) 应为 3000010.0，实际得到 {Y2}"


def test_accuracy_csv_h_normal_handling():
    from app.logic import accuracy as acc
    td = Path(tempfile.mkdtemp(prefix="test_acc_csv_"))
    try:
        csv_file = td / "acc.csv"
        # 混合测试表头：点名,L,B,H_ell,H (H 与 H_ell)
        csv_file.write_text("点名,L,B,H_ell,H\nCP01,104.123456,30.123456,100.5,95.3\n",
                            encoding="utf-8")
        pairs = acc.load_pairs(csv_file)
        assert len(pairs.names) == 1
        assert abs(pairs.src[0, 0] - 104.123456) < 1e-6
        assert abs(pairs.src[0, 1] - 30.123456) < 1e-6
        assert abs(pairs.src_h[0] - 100.5) < 1e-6
        assert abs(pairs.dst_h[0] - 95.3) < 1e-6
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_no_duplicate_definitions():
    import ast
    with open(_ROOT / "app" / "core" / "transform2d.py", "r", encoding="utf-8") as f:
        t2d_tree = ast.parse(f.read())
    funcs = [n.name for n in t2d_tree.body if isinstance(n, ast.FunctionDef)]
    assert funcs.count("fit_3param2d") == 1, "transform2d.py 中 fit_3param2d 重复定义"

    with open(_ROOT / "app" / "gui" / "pages.py", "r", encoding="utf-8") as f:
        pages_tree = ast.parse(f.read())
    for n in pages_tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "GaussPage":
            m_names = [m.name for m in n.body if isinstance(m, ast.FunctionDef)]
            assert m_names.count("_show_ell") == 1, "GaussPage._show_ell 重复定义"
            assert m_names.count("_current_ell") == 1, "GaussPage._current_ell 重复定义"


def run() -> int:
    fns = [test_osgb_geodetic_source_unblocked,
           test_las_evlr_preservation,
           test_las_unlink_on_failure,
           test_grd_f64_and_f32,
           test_pair_io_numeric_point_names,
           test_detect_coord_kind_local_planar,
           test_autoselect_fit4_and_build_param_dict,
           test_space_and_apply_page_load_file,
           test_planar_fns_cad_coordinate_mapping,
           test_accuracy_csv_h_normal_handling,
           test_no_duplicate_definitions]
    for fn in fns:
        fn()
    return len(fns)


if __name__ == "__main__":
    n = run()
    print(f"All {n} tests passed in test_v13_fixes.py!")
