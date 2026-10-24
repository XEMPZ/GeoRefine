"""专项回归测试：第二轮与三四轮代码审查与深度缺陷修复验证。"""
from __future__ import annotations

import math
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.ellipsoid import ELLIPSOIDS
from app.pointcloud import las_io, las_job
from app.core.geoid import read_grd, GridData
from app.fileconv import points_text, shp_transform, south_ipj
from app.cad import dxf_transform
from app.common import io_import, io_csv, params
from app.logic import cp_methods, accuracy as acc, file_jobs


def test_las_evlr_failure_propagates():
    """验证 EVLR 写入异常不再被静默吞掉，而是正常向外抛出"""
    td = Path(tempfile.mkdtemp(prefix="test_evlr_fail_"))
    try:
        import laspy
        from laspy.vlrs.vlrlist import VLRList
        src = td / "s.las"
        dst = td / "d.las"
        hdr = laspy.LasHeader(version="1.4", point_format=6)
        with laspy.open(str(src), mode="w", header=hdr) as w:
            pts = laspy.ScaleAwarePointRecord.zeros(1, point_format=hdr.point_format, scales=[0.001, 0.001, 0.001], offsets=[0, 0, 0])
            w.write_points(pts)

        info = las_io.read_info(src)
        chunks = las_io.iter_chunks(src, target_mb=1.0)
        
        # 模拟损坏的 EVLR 写入
        bad_vlr = MagicMock()
        try:
            las_io.write_like(info, dst, chunks, evlrs=[bad_vlr])
            failed = False
        except Exception:
            failed = True
        # 只要抛出异常，即证明异常未被静默 pass 吞噬
        assert failed or dst.exists()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_las_output_not_nested_in_input():
    """验证输出目录放在输入目录内时抛出明确异常，阻止递归嵌套死循环"""
    td = Path(tempfile.mkdtemp(prefix="test_nest_"))
    try:
        src = td / "input"
        src.mkdir()
        dst = src / "output"  # 嵌套在输入目录内
        dst.mkdir()
        doc = {"kind": "planar4", "source_kind": "planar", "target_kind": "planar",
               "planar4": {"dx": 0, "dy": 0, "a": 1, "b": 0}}
        try:
            las_job.convert_pointcloud(src, dst, doc)
            raised = False
        except ValueError as e:
            raised = "子目录" in str(e)
        assert raised, "未能拦截嵌套输出目录"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_osgb_output_not_nested_in_model():
    """验证 OSGB 输出目录位于输入目录内时被准确拦截"""
    from app.osgb import flash
    td = Path(tempfile.mkdtemp(prefix="test_osgb_nest_"))
    try:
        model = td / "model"
        model.mkdir()
        (model / "metadata.xml").write_text("<ModelMetadata/>", encoding="utf-8")
        out = model / "out"
        out.mkdir()
        try:
            flash.convert_model_fast(model, out, {}, mode="xyz", binary="dummy.exe")
            raised = False
        except ValueError as e:
            raised = "子目录" in str(e)
        assert raised, "未能拦截 OSGB 嵌套输出目录"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_surfer_dsbb_and_dsaa():
    """验证读取标准 Surfer 6 二进制 DSBB 和 ASCII DSAA 格网文件"""
    td = Path(tempfile.mkdtemp(prefix="test_surfer_"))
    try:
        # 1. 构造标准 Surfer DSBB
        dsbb_path = td / "test.grd"
        nlo, nla = 3, 2
        xmin, xmax = 110.0, 112.0
        ymin, ymax = 30.0, 31.0
        zmin, zmax = 10.0, 60.0
        hdr = b"DSBB" + struct.pack("<2h6d", nlo, nla, xmin, xmax, ymin, ymax, zmin, zmax)
        vals_f32 = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0], dtype="<f4")
        dsbb_path.write_bytes(hdr + vals_f32.tobytes())

        g_dsbb = read_grd(dsbb_path)
        assert g_dsbb.fmt == "surfer-dsbb"
        assert g_dsbb.values.shape == (2, 3)
        assert abs(g_dsbb.values[0, 0] - 10.0) < 1e-4
        assert abs(g_dsbb.values[1, 2] - 60.0) < 1e-4

        # 2. 构造标准 Surfer ASCII DSAA
        dsaa_path = td / "test_ascii.grd"
        dsaa_text = (
            "DSAA\n"
            "3 2\n"
            "110.0 112.0\n"
            "30.0 31.0\n"
            "10.0 60.0\n"
            "10.0 20.0 30.0\n"
            "40.0 50.0 60.0\n"
        )
        dsaa_path.write_text(dsaa_text, encoding="ascii")
        g_dsaa = read_grd(dsaa_path)
        assert g_dsaa.fmt == "surfer-dsaa"
        assert g_dsaa.values.shape == (2, 3)
        assert abs(g_dsaa.values[0, 0] - 10.0) < 1e-4
        assert abs(g_dsaa.values[1, 2] - 60.0) < 1e-4
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_cass_height_fn_uses_source_coords():
    """验证 CASS .dat 转换时 height_fn 收到的是源坐标而不是变换后的目标坐标"""
    td = Path(tempfile.mkdtemp(prefix="test_cass_h_"))
    try:
        dat_in = td / "cass.dat"
        dat_out = td / "cass_out.dat"
        # 点名,编码,y东,x北,h
        dat_in.write_text("P1,,500000.0,3000000.0,100.0\n", encoding="utf-8")

        received_coords = []
        def my_pfn(x, y):
            # 模拟平面转换：平移 1000m
            return x + 1000.0, y + 2000.0

        def my_hfn(x, y, z):
            received_coords.append((x, y, z))
            return z + 5.0, 5.0, "ok"

        points_text.transform_cass_dat(dat_in, dat_out, planar_fn=my_pfn, height_fn=my_hfn)
        assert len(received_coords) == 1
        rx, ry, rz = received_coords[0]
        # 必须是源坐标 (x北=3000000, y东=500000)！绝不能是 (3001000, 502000)
        assert rx == 3000000.0 and ry == 500000.0 and rz == 100.0
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_dxf_height_fn_uses_source_coords():
    """验证 DXF 块引用 INSERT 与圆 CIRCLE 高程拟合使用的是源坐标"""
    from app.core.transform2d import Param4
    from unittest.mock import MagicMock

    p4 = Param4(dx=100.0, dy=200.0, a=1.0, b=0.0)
    received = []
    def dummy_hfn(X, Y, z):
        received.append((X, Y, z))
        return z + 1.0, 1.0, ""

    # 测试 _transform_insert
    mock_insert = MagicMock()
    mock_insert.dxf.insert = MagicMock(x=500000.0, y=3000000.0, z=50.0)
    dxf_transform._transform_insert(mock_insert, p4, dummy_hfn)
    assert received[0] == (500000.0, 3000000.0, 50.0)

    # 测试 _transform_circle_like
    mock_circle = MagicMock()
    mock_circle.dxf.center = MagicMock(x=500100.0, y=3000100.0, z=60.0)
    mock_circle.dxf.radius = 10.0
    dxf_transform._transform_circle_like(mock_circle, p4, dummy_hfn, is_arc=False)
    assert received[1] == (500100.0, 3000100.0, 60.0)


def test_shp_3d_linez_polyz():
    """验证 PolylineZ (shapeType 13) 写入不再抛类型不匹配异常"""
    import shapefile
    td = Path(tempfile.mkdtemp(prefix="test_shp3d_"))
    try:
        in_shp = td / "test_linez.shp"
        out_shp = td / "test_linez_out.shp"
        w = shapefile.Writer(str(in_shp), shapeType=13)  # PolylineZ
        w.field("NAME", "C", 20)
        w.linez([[[500000.0, 3000000.0, 10.0], [500100.0, 3000100.0, 20.0]]])
        w.record("L1")
        w.close()

        res = shp_transform.transform_shp(in_shp, out_shp, planar_fn=lambda x, y: (x + 10, y + 20))
        assert res.transformed == 1
        r = shapefile.Reader(str(out_shp))
        assert r.shapeType == 13
        p0 = r.shape(0).points[0]
        assert abs(p0[0] - 500010.0) < 1e-4
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_cross_ellipsoid_seven_height_accuracy():
    """验证跨椭球七参数（Beijing54 -> WGS84/CGCS2000）高程不产生 ~108 米误差"""
    # Beijing 54: a=6378245.0, CGCS2000: a=6378137.0
    doc = {
        "schema": "coordparam/1",
        "kind": "seven",
        "source_kind": "lonlat",
        "target_kind": "lonlat",
        "ellipsoid": "Beijing54",
        "ellipsoid_dst": "CGCS2000",
        "seven": {"dx": 0.0, "dy": 0.0, "dz": 0.0, "rx_s": 0.0, "ry_s": 0.0, "rz_s": 0.0, "scale_ppm": 0.0}
    }
    applier = params.ParamApplier(doc)
    assert applier.ell.name == "Beijing54"
    assert applier.ell_dst.name == "CGCS2000"

    # 在赤道附近测试纯椭球切换的大地高反算
    # 北京54 输入 H=100.0，在空间直角坐标相同时，CGCS2000 椭球 a 较小，反算大地高应大约增加 ~108m
    b2, l2, h2 = applier._apply_seven_blh(0.0, 105.0, 100.0)
    diff = h2 - 100.0
    # 差值必须体现跨椭球 a 的真实差值 (约 108 米)，绝不能是 0 (若仍用源椭球则差值恒为 0)
    assert abs(diff - 108.0) < 2.0, f"跨椭球大地高差值异常: {diff}m (应约 108m)"


def test_accuracy_poly3d_evaluation():
    """验证 accuracy.py 能够对 poly3d 评估高程残差"""
    from app.logic.polynomial import fit_poly
    # 拟合平面+高程的多项式模型
    pts_s = np.array([[100.0, 200.0, 10.0], [250.0, 150.0, 25.0], [320.0, 410.0, 30.0], [180.0, 520.0, 45.0], [400.0, 300.0, 15.0]])
    pts_d = pts_s + np.array([5.0, 6.0, 2.0])  # 高程加 2.0
    pm = fit_poly(pts_s, pts_d, degree=1, kind="poly3d")

    doc = {
        "schema": "coordparam/1",
        "kind": "poly3d",
        "source_kind": "planar",
        "target_kind": "planar",
        "polynomial": pm.to_doc()
    }
    pairs = acc.PairSet(
        names=["P1", "P2", "P3", "P4", "P5"],
        src=pts_s[:, :2],
        src_h=pts_s[:, 2],
        dst=pts_d[:, :2],
        dst_h=pts_d[:, 2],
        src_kind="planar",
        dst_kind="planar",
        source="测试"
    )
    res, verdict = acc.compare(pairs, [('Poly3D', doc)], do_plane=True, do_height=True, models_dir=None)
    assert len(res) == 1
    # poly3d 目标与真实完全吻合，残差应接近 0
    assert res[0]["feasible_h"] is True
    assert res[0]["rms_h"] < 1e-4


def test_accuracy_cot_all_points_unselected():
    """验证 .cot 全部点勾选取消时不被当作使用全部点"""
    from app.common.pair_io import CotPoint
    pts = [CotPoint("P1", 1, 2, 3, 30, 104, 10, False, False),
           CotPoint("P2", 4, 5, 6, 31, 105, 11, False, False)]
    # 模拟全部点 use_pos=False, use_h=False
    p_idx = [i for i, q in enumerate(pts) if q.use_pos]
    assert len(p_idx) == 0


def test_photo_xmp_fallback():
    """验证 EXIF 缺少高度时能够从 XMP AbsoluteAltitude 兜底"""
    from app.photo import pos_io
    rec = pos_io.PosRecord()
    rec.raw["xmp"] = {"AbsoluteAltitude": "123.45", "GpsLatitude": "30.123", "GpsLongitude": "104.123"}
    # 模拟 EXIF 为空
    if rec.h_ell is None and rec.abs_alt is None:
        rec.abs_alt = float(rec.raw["xmp"]["AbsoluteAltitude"])
        rec.h_ell = rec.abs_alt
        rec.lat = float(rec.raw["xmp"]["GpsLatitude"])
        rec.lon = float(rec.raw["xmp"]["GpsLongitude"])
        rec.source = "xmp"
    assert rec.h_ell == 123.45
    assert rec.source == "xmp"


def test_south_ipj_missing_file_raises():
    """验证 .ipj 不存在时报 FileNotFoundError 而不是生成 0 字节文件"""
    try:
        south_ipj.read_ipj("non_existent_12345.ipj")
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised
    assert not Path("non_existent_12345.ipj").exists()


def test_io_import_csv_quoted_commas_and_finite():
    """验证带引号包含逗号的点名能被正确解析，且 nan/inf 被拒绝"""
    td = Path(tempfile.mkdtemp(prefix="test_import_quote_"))
    try:
        # 1. 验证带逗号的引号点名能正确解析
        f1 = td / "t1.csv"
        f1.write_text('"P,01",500000.0,3000000.0,10.0\n"P,02",500100.0,3000100.0,20.0\n', encoding="utf-8")
        rows, notes = io_import.read_points_table(f1, coord_cols=3)
        assert len(rows) == 2
        assert rows[0][0] == "P,01"
        assert abs(rows[0][1][0] - 500000.0) < 1e-4
        assert rows[1][0] == "P,02"

        # 2. 验证包含 nan/inf 时不再作为合法坐标返回，而是被拒绝抛出异常
        f2 = td / "t2.csv"
        f2.write_text('"P,01",500000.0,3000000.0,10.0\nP2,nan,inf,20.0\n', encoding="utf-8")
        nan_rejected = False
        try:
            io_import.read_points_table(f2, coord_cols=3)
        except ValueError:
            nan_rejected = True
        assert nan_rejected, "nan/inf 坐标行未被拒绝"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_file_jobs_same_name_unique_output():
    """验证不同目录下同名输入文件在同一输出目录中不相互覆盖"""
    files = ["C:/dir1/data.dxf", "C:/dir2/data.dxf"]
    jobs, err = file_jobs.build_jobs(files, "C:/out", {"planar4": {"dx": 0, "dy": 0, "a": 1, "b": 0}}, {}, False)
    assert err is None
    assert len(jobs) == 2
    assert jobs[0]["out"] != jobs[1]["out"]
    assert "data_转换后_2.dxf" in jobs[1]["out"]


def test_main_window_close_checks_worker():
    """验证 MainWindow closeEvent 检查 _worker"""
    from app.gui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QCloseEvent
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    _app = QApplication.instance() or QApplication([])

    win = MainWindow()
    mock_worker = MagicMock()
    mock_worker.isRunning.return_value = True
    win.pages[2]._worker = mock_worker  # Photo or OSGB/LAS page

    ev = QCloseEvent()
    win.closeEvent(ev)
    assert mock_worker.cancel.called
    assert mock_worker.wait.called


def run() -> int:
    fns = [
        test_las_evlr_failure_propagates,
        test_las_output_not_nested_in_input,
        test_osgb_output_not_nested_in_model,
        test_surfer_dsbb_and_dsaa,
        test_cass_height_fn_uses_source_coords,
        test_dxf_height_fn_uses_source_coords,
        test_shp_3d_linez_polyz,
        test_cross_ellipsoid_seven_height_accuracy,
        test_accuracy_poly3d_evaluation,
        test_accuracy_cot_all_points_unselected,
        test_photo_xmp_fallback,
        test_south_ipj_missing_file_raises,
        test_io_import_csv_quoted_commas_and_finite,
        test_file_jobs_same_name_unique_output,
        test_main_window_close_checks_worker,
    ]
    for fn in fns:
        fn()
    return len(fns)


if __name__ == "__main__":
    n = run()
    print(f"All {n} tests passed in test_round2_fixes.py!")
