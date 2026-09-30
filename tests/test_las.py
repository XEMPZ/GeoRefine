r"""LAS 点云转换测试：读写往返、坐标正确性、**非坐标数据不可改动**、轴序。

夹具：用项目样例数据现场生成一个小 LAS（不依赖外部大文件），
确保测试在任何机器上都能跑。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_las(path, n=500, *, with_rgb=True):
    """造一个点格式 3 的小 LAS（含 RGB/GPS 时间/强度/分类），供往返测试。"""
    import laspy
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.0001, 0.0001, 0.0001])
    header.offsets = np.array([526777.0, 3564283.0, 993.0])
    las = laspy.LasData(header)
    rng = np.random.default_rng(42)
    las.x = 526777.0 + rng.uniform(-300, 300, n)
    las.y = 3564283.0 + rng.uniform(-300, 300, n)
    las.z = 993.0 + rng.uniform(-100, 100, n)
    # 非坐标数据：必须有明显可辨识的值，才能证明"没被改动"
    las.intensity = rng.integers(1, 65000, n).astype(np.uint16)
    las.classification = rng.integers(0, 20, n).astype(np.uint8)
    las.gps_time = (1726023356.0 + rng.uniform(0, 1000, n))
    las.user_data = rng.integers(0, 255, n).astype(np.uint8)
    if with_rgb:
        las.red = rng.integers(0, 65535, n).astype(np.uint16)
        las.green = rng.integers(0, 65535, n).astype(np.uint16)
        las.blue = rng.integers(0, 65535, n).astype(np.uint16)
    las.write(path)
    return path


def _param(dx=100.0, dy=-7.0, a=0.999995, b=0.00002, dh=1.5):
    return {"schema": "coordparam/1", "name": "las_test", "kind": "planar4",
            "source_kind": "planar", "target_kind": "planar", "ellipsoid": "CGCS2000",
            "planar4": {"dx": dx, "dy": dy, "a": a, "b": b},
            "heightfit": {"mode": "const", "space": "planar", "value_type": "dh",
                          "coef": [dh], "center": [0.0, 0.0], "loo_rms": 0.0}}


def _skip_if_no_laspy():
    from app.pointcloud.las_io import has_laspy
    if not has_laspy():
        print("    [skip] 未安装 laspy")
        return True
    return False


def test_las_info_and_axes():
    """LAS 头信息与轴序提示。"""
    if _skip_if_no_laspy():
        return
    from app.pointcloud import las_io
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        p = _make_las(os.path.join(wd, "a.las"))
        info = las_io.read_info(p)
        assert info.point_format == 3, info.point_format
        assert info.point_count == 500, info.point_count
        assert abs(info.quantum[0] - 0.0001) < 1e-12, info.quantum
        assert info.has_color, "点格式 3 应带 RGB"
        assert "东坐标量级" in info.axis_hint(), info.axis_hint()
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_las_roundtrip_preserves_everything():
    """零位移往返：坐标与**所有**非坐标维度都应逐位一致。"""
    if _skip_if_no_laspy():
        return
    import laspy
    from app.pointcloud import las_job
    from app.pointcloud.las_transform import LasVertexTransform
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        src = _make_las(os.path.join(wd, "a.las"))
        dst = os.path.join(wd, "b.las")
        tf = LasVertexTransform(_param(dx=0.0, dy=0.0, a=1.0, b=0.0, dh=0.0), mode="xyz")
        res = las_job.convert_las_file(tf, src, dst)
        assert res.ok, res.reason
        assert res.points == 500, res.points
        a = laspy.read(src)
        b = laspy.read(dst)
        for dn in a.point_format.dimension_names:
            assert np.array_equal(np.asarray(a[dn]), np.asarray(b[dn])), \
                "零位移时维度 %s 不应改变" % dn
        assert a.header.point_format.id == b.header.point_format.id
        assert np.array_equal(a.header.scales, b.header.scales)
        assert np.allclose(a.header.offsets, b.header.offsets)
        assert len(a.header.vlrs) == len(b.header.vlrs)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_las_transform_only_coords():
    """有位移转换：坐标按参数变，**非坐标维度逐位不变**。"""
    if _skip_if_no_laspy():
        return
    import laspy
    from app.pointcloud import las_job
    from app.pointcloud.las_transform import LasVertexTransform
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        src = _make_las(os.path.join(wd, "a.las"))
        dst = os.path.join(wd, "b.las")
        doc = _param(dx=100.0, dy=-7.0, a=1.0, b=0.0, dh=1.5)
        tf = LasVertexTransform(doc, mode="xyz")
        res = las_job.convert_las_file(tf, src, dst)
        assert res.ok, res.reason
        a = laspy.read(src)
        b = laspy.read(dst)
        # 轴序：LAS 的 X=东、Y=北；参数 dx=北向、dy=东向
        d_east = np.asarray(b.x) - np.asarray(a.x)
        d_north = np.asarray(b.y) - np.asarray(a.y)
        d_h = np.asarray(b.z) - np.asarray(a.z)
        assert np.abs(d_east - (-7.0)).max() < 2e-4, d_east[:5]
        assert np.abs(d_north - 100.0).max() < 2e-4, d_north[:5]
        assert np.abs(d_h - 1.5).max() < 2e-4, d_h[:5]
        # 非坐标维度：逐位不变
        for dn in a.point_format.dimension_names:
            if dn in ("X", "Y", "Z"):
                continue
            assert np.array_equal(np.asarray(a[dn]), np.asarray(b[dn])), \
                "非坐标维度 %s 被改动了！" % dn
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_las_decouple():
    """平面/高程解耦：仅平面不动 Z，仅高程不动 XY（逐位）。"""
    if _skip_if_no_laspy():
        return
    from app.pointcloud.las_transform import LasVertexTransform
    x = np.array([526700.0, 526800.0, 526900.0])
    y = np.array([3564200.0, 3564300.0, 3564400.0])
    z = np.array([900.0, 1000.0, 1100.0])
    doc = _param(dx=100.0, dy=-7.0, a=1.0, b=0.0, dh=1.5)
    oe, on, oz = LasVertexTransform(doc, mode="xy").apply(x, y, z)
    assert np.array_equal(oz, z), "仅平面时 Z 应逐位不变"
    assert np.abs(oe - (x - 7.0)).max() < 1e-9
    assert np.abs(on - (y + 100.0)).max() < 1e-9
    oe, on, oz = LasVertexTransform(doc, mode="z").apply(x, y, z)
    assert np.array_equal(oe, x) and np.array_equal(on, y), "仅高程时 XY 应逐位不变"
    assert np.abs(oz - (z + 1.5)).max() < 1e-9


def test_las_rejects_same_dir():
    """输出目录等于输入目录必须拒绝（不覆盖源数据）。"""
    if _skip_if_no_laspy():
        return
    from app.pointcloud import las_job
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        _make_las(os.path.join(wd, "a.las"))
        try:
            las_job.convert_pointcloud(wd, wd, _param())
        except ValueError as e:
            assert "覆盖" in str(e) or "相同" in str(e), str(e)
            return
        raise AssertionError("输出目录等于输入目录时应拒绝")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_las_skips_index_files():
    """索引/中间文件不得被当成主数据处理。"""
    if _skip_if_no_laspy():
        return
    from app.pointcloud import las_job
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        for n in ("tile.las", "tile_dasindex.las", "tile_dasindex.xlas", "tile.laz"):
            open(os.path.join(wd, n), "wb").close()
        os.makedirs(os.path.join(wd, "sub"))
        open(os.path.join(wd, "sub", "deep.las"), "wb").close()
        got = [p.name for p in las_job.find_las_files(wd)]
        assert "tile.las" in got and "tile.laz" in got and "deep.las" in got, got
        assert "tile_dasindex.las" not in got, got
        assert "tile_dasindex.xlas" not in got, got
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_las_custom_extra_dimension():
    """带**自定义维度**的文件（扫描仪合并导出常见）：维度清单与取值都必须完整保留。

    背景：这类文件的 header.point_format.id 与实际不符，若按 id 重建头会报
    "Incompatible point formats"。正确做法是沿用源头的头对象。
    """
    if _skip_if_no_laspy():
        return
    import laspy
    from app.pointcloud import las_job
    from app.pointcloud.las_transform import LasVertexTransform
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        src = os.path.join(wd, "extra.las")
        header = laspy.LasHeader(point_format=3, version="1.3")
        header.scales = np.array([0.01, 0.01, 0.01])
        header.offsets = np.array([498000.0, 2865000.0, 0.0])
        las = laspy.LasData(header)
        rng = np.random.default_rng(9)
        n = 300
        las.x = 498830.0 + rng.uniform(-30, 30, n)
        las.y = 2865949.0 + rng.uniform(-30, 30, n)
        las.z = 145.0 + rng.uniform(-20, 20, n)
        las.intensity = np.zeros(n, dtype=np.uint16)          # 全零（扫描仪导出常见）
        las.red = np.full(n, 65535, dtype=np.uint16)          # 白色占位 = 无颜色
        las.green = np.full(n, 65535, dtype=np.uint16)
        las.blue = np.full(n, 65535, dtype=np.uint16)
        extra = rng.integers(0, 3, n).astype(np.uint8)
        las.add_extra_dim(laspy.ExtraBytesParams(name="Original cloud index",
                                                 type=np.uint8))
        las["Original cloud index"] = extra
        las.write(src)

        dst = os.path.join(wd, "extra_out.las")
        tf = LasVertexTransform(_param(dx=100.0, dy=-7.0, a=1.0, b=0.0, dh=1.5),
                                mode="xyz")
        res = las_job.convert_las_file(tf, src, dst)
        assert res.ok, res.reason
        assert res.points == n, res.points

        a = laspy.read(src)
        b = laspy.read(dst)
        da = list(a.point_format.dimension_names)
        db = list(b.point_format.dimension_names)
        assert da == db, "维度清单应一致：%s vs %s" % (da, db)
        assert "Original cloud index" in db, db
        # 自定义维度逐位一致
        assert np.array_equal(np.asarray(a["Original cloud index"]),
                              np.asarray(b["Original cloud index"]))
        # 全零强度 / 白色占位 RGB 也必须保持
        assert np.all(np.asarray(b.intensity) == 0)
        assert np.all(np.asarray(b.red) == 65535)
        assert np.all(np.asarray(b.green) == 65535)
        assert np.all(np.asarray(b.blue) == 65535)
        # 坐标按参数位移（LAS 轴序 X=东、Y=北）
        assert abs((np.asarray(b.x) - np.asarray(a.x)).mean() + 7.0) < 1e-6
        assert abs((np.asarray(b.y) - np.asarray(a.y)).mean() - 100.0) < 1e-6
        # scale/offset 不变
        assert np.allclose(a.header.scales, b.header.scales)
        assert np.allclose(a.header.offsets, b.header.offsets)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_las_coarse_scale_precision():
    """量化步长较粗（scale=0.01，扫描仪常见）时，往返误差不超过半个量化步长。"""
    if _skip_if_no_laspy():
        return
    import laspy
    from app.pointcloud import las_job
    from app.pointcloud.las_transform import LasVertexTransform
    wd = tempfile.mkdtemp(prefix="lastest_")
    try:
        src = os.path.join(wd, "coarse.las")
        header = laspy.LasHeader(point_format=2, version="1.3")
        header.scales = np.array([0.01, 0.01, 0.01])
        header.offsets = np.array([0.0, 0.0, 0.0])
        las = laspy.LasData(header)
        rng = np.random.default_rng(13)
        n = 400
        las.x = 498830.0 + rng.uniform(-30, 30, n)
        las.y = 2865949.0 + rng.uniform(-30, 30, n)
        las.z = 145.0 + rng.uniform(-20, 20, n)
        las.write(src)
        dst = os.path.join(wd, "coarse_out.las")
        tf = LasVertexTransform(_param(dx=100.0, dy=-7.0, a=1.0, b=0.0, dh=1.5),
                                mode="xyz")
        res = las_job.convert_las_file(tf, src, dst)
        assert res.ok, res.reason
        a = laspy.read(src)
        b = laspy.read(dst)
        half = 0.005    # scale=0.01 的一半
        assert np.abs((np.asarray(b.x) - np.asarray(a.x)) + 7.0).max() <= half + 1e-9
        assert np.abs((np.asarray(b.y) - np.asarray(a.y)) - 100.0).max() <= half + 1e-9
        assert np.abs((np.asarray(b.z) - np.asarray(a.z)) - 1.5).max() <= half + 1e-9
        # 点格式 2（无 GPS 时间、有 RGB）也要能整份搬运
        assert list(a.point_format.dimension_names) == list(b.point_format.dimension_names)
    finally:
        shutil.rmtree(wd, ignore_errors=True)

TESTS = [test_las_info_and_axes, test_las_roundtrip_preserves_everything,
         test_las_transform_only_coords, test_las_decouple,
         test_las_rejects_same_dir, test_las_skips_index_files,
         test_las_custom_extra_dimension, test_las_coarse_scale_precision]


def run():
    """执行全部用例，返回检查数；失败抛异常。"""
    ns = 0
    for fn in TESTS:
        fn()
        ns += 1
        print("    ok  %s" % fn.__name__)
    return ns


if __name__ == "__main__":
    run()
