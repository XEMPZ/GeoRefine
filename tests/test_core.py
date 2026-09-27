"""核心算法测试（ARCHITECTURE.md §8 ①~⑧）。提供 run()，兼容 pytest。

运行: $env:PYTHONIOENCODING='utf-8'; python tests\test_core.py
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core.ellipsoid import CGCS2000, WGS84, ecef_to_geodetic, geodetic_to_ecef  # noqa: E402
from app.core.heightfit import eval_height, fit_height, select_best_fit  # noqa: E402
from app.core.projection import auto_central_meridian, gauss_kruger, gauss_kruger_inverse  # noqa: E402
from app.core.transform2d import Param4, apply_4param, fit_4param  # noqa: E402
from app.core.transform3d import Param7, apply_7param, fit_7param  # noqa: E402

RNG = np.random.default_rng(42)


# ① 四参数合成→恢复（平移与尺度/旋转强相关是数据几何本性：绝对坐标大、相对散布小时，
# dx/dy 参数本身方差达米级，但区域内预测精度 mm 级 —— 因此检验预测精度与尺度/旋转恢复）
def test_4param_recovery():
    true = Param4(dx=102.3456, dy=-56.7890, a=0.9999975 * np.cos(np.radians(0.25 / 3600)),
                  b=0.9999975 * np.sin(np.radians(0.25 / 3600)))
    src = np.column_stack([3354000 + RNG.uniform(-2000, 2000, 12),
                           500000 + RNG.uniform(-2500, 2500, 12)])
    px, py = apply_4param(true, src[:, 0], src[:, 1])
    dst = np.column_stack([px + RNG.normal(0, 0.002, 12), py + RNG.normal(0, 0.002, 12)])
    f = fit_4param(src, dst)
    assert abs(f.param.scale - true.scale) < 5e-7
    assert abs(f.param.rot_sec - 0.25) < 0.5
    assert f.rms_xy < 0.01, f"rms {f.rms_xy}"
    # 独立预测检验：区域内新点预测 < 1cm
    chk = np.column_stack([3354000 + RNG.uniform(-2000, 2000, 5),
                           500000 + RNG.uniform(-2500, 2500, 5)])
    cx, cy = apply_4param(true, chk[:, 0], chk[:, 1])
    fx, fy = apply_4param(f.param, chk[:, 0], chk[:, 1])
    err = np.hypot(fx - cx, fy - cy)
    assert np.max(err) < 0.01, f"预测偏差 {np.max(err)}"


# ② 七参数合成→恢复
def test_7param_recovery():
    true = Param7(dx=1.234, dy=-2.345, dz=3.456, rx_s=0.30, ry_s=-0.20, rz_s=0.15, scale_ppm=2.5)
    lat = RNG.uniform(29.9, 30.1, 10)
    lon = RNG.uniform(113.9, 114.1, 10)
    h = RNG.uniform(30, 60, 10)
    x, y, z = geodetic_to_ecef(lat, lon, h, WGS84)
    src = np.column_stack([x, y, z])
    x2, y2, z2 = apply_7param(true, x, y, z)
    dst = np.column_stack([x2, y2, z2])
    f = fit_7param(src, dst)
    assert abs(f.param.dx - true.dx) < 0.02
    assert abs(f.param.dy - true.dy) < 0.02
    assert abs(f.param.dz - true.dz) < 0.02
    assert abs(f.param.rx_s - true.rx_s) < 0.05
    assert abs(f.param.ry_s - true.ry_s) < 0.05
    assert abs(f.param.rz_s - true.rz_s) < 0.05
    assert abs(f.param.scale_ppm - true.scale_ppm) < 0.5
    assert f.rms_3d < 0.01


# ③ 高斯正反算往返（6 阶级数适用域为 3° 带内，故 lon 限制在 L0±1.4°）
def test_gk_roundtrip():
    rng = np.random.default_rng(7)
    lat = rng.uniform(20, 45, 30)
    lon = rng.uniform(112.6, 115.4, 30)
    l0 = 114.0
    x, y = gauss_kruger(lat, lon, l0, CGCS2000)
    b2, l2 = gauss_kruger_inverse(x, y, l0, CGCS2000)
    assert np.max(np.abs(b2 - lat)) < 1e-9, f"B 往返 {np.max(np.abs(b2 - lat))}"
    assert np.max(np.abs(l2 - lon)) < 1e-9
    # L==L0 时 y==500000 精确成立
    xs, ys = gauss_kruger(np.array([22.0, 40.0]), np.array([114.0, 114.0]), l0, CGCS2000)
    assert np.max(np.abs(ys - 500000.0)) < 1e-6
    # 子午线弧长软校验（B=30° 实测 3320113.4m，与 PROJ 一致）
    x30, _ = gauss_kruger(30.0, l0, l0, CGCS2000)
    assert abs(x30 - 3320113.4) < 10.0, f"B=30° 子午弧长异常: {x30}"
    assert auto_central_meridian(113.9) == 114.0


# ④ BLH↔ECEF 往返
def test_blh_ecef_roundtrip():
    lat = RNG.uniform(-80, 80, 20)
    lon = RNG.uniform(-179, 179, 20)
    h = RNG.uniform(-100, 8000, 20)
    x, y, z = geodetic_to_ecef(lat, lon, h, CGCS2000)
    b2, l2, h2 = ecef_to_geodetic(x, y, z, CGCS2000)
    assert np.max(np.abs(b2 - lat)) < 1e-11
    assert np.max(np.abs(l2 - lon)) < 1e-11
    assert np.max(np.abs(h2 - h)) < 1e-5


# ⑤ 合成 GTX 往返（硬性正确性证明）
def test_gtx_synthetic_roundtrip(tmp_path=None):
    import tempfile
    from app.core.geoid import GridModel, load_grid

    d = tmp_path or tempfile.mkdtemp()
    path = os.path.join(d, "synthetic.gtx")
    lat_min, lon_min, dlat, dlon, nlat, nlon = 20.0, 100.0, 0.5, 0.5, 41, 61
    # 线性斜坡 ξ = 10 + 0.01*lat + 0.005*lon
    vals = (10.0 + 0.01 * (lat_min + dlat * np.arange(nlat)[:, None])
            + 0.005 * (lon_min + dlon * np.arange(nlon)[None, :])).astype(">f4")
    with open(path, "wb") as f:
        f.write(struct.pack(">ddddii", lat_min, lon_min, dlat, dlon, nlat, nlon))
        f.write(vals.tobytes())
    grid = load_grid(path)
    model = GridModel(grid)
    for lon, lat in [(113.2, 31.4), (105.7, 25.9), (129.8, 39.9)]:
        v = model.undulation(lon, lat)
        expect = 10.0 + 0.01 * lat + 0.005 * lon
        assert abs(v - expect) < 1e-3, f"GTX 内插偏差 {v - expect} @({lon},{lat})"
    try:
        model.undulation(50.0, 10.0)
        raise AssertionError("超范围应抛 ValueError")
    except ValueError:
        pass
    assert model.try_undulation(50.0, 10.0) is None


# ⑥ ZGF 小端合成→读取一致
def test_zgf_synthetic():
    import tempfile
    from app.core.geoid import load_grid

    d = tempfile.mkdtemp()
    path = os.path.join(d, "synthetic.zgf")
    glamn, glamx, glomn, glomx, dla, dlo = 20.0, 40.0, 100.0, 120.0, 0.5, 0.5
    nla, nlo = int((glamx - glamn) / dla) + 1, int((glomx - glomn) / dlo) + 1
    # 存储行序北在前（imove=1 → 读取时翻转为南在前）
    vals_north_first = (5.0 + 0.02 * (glamx - dla * np.arange(nla)[:, None])
                        + 0.01 * (glomn + dlo * np.arange(nlo)[None, :])).astype("<f4")
    with open(path, "wb") as f:
        f.write(b"\x00" * 8)
        f.write(struct.pack("<6d", glamn, glamx, glomn, glomx, dla, dlo))
        f.write(struct.pack("<5i", nla, nlo, 1, 0, 1))
        f.seek(86)
        f.write(b"SYNTHETIC\x00")
        f.seek(96)
        f.write(vals_north_first.tobytes())
    g = load_grid(path)
    assert abs(g.lat_min - glamn) < 1e-9 and abs(g.lat_max - glamx) < 1e-9
    # 双线性取格点精确值：北端行原存储 = 5+0.02*glamx；南端行 = 5+0.02*glamn
    m = __import__("app.core.geoid", fromlist=["GridModel"]).GridModel(g)
    v_south = m.undulation(glomn, glamn)   # 南端角点
    expect_south = 5.0 + 0.02 * glamn + 0.01 * glomn
    assert abs(v_south - expect_south) < 1e-3, f"ZGF 行序或翻转错误: {v_south} vs {expect_south}"
    v_north = m.undulation(glomn, glamx)
    expect_north = 5.0 + 0.02 * glamx + 0.01 * glomn
    assert abs(v_north - expect_north) < 1e-3


# ⑦ 平面/二次拟合合成参数恢复 + LOO 自动选阶
def test_heightfit():
    from app.core.heightfit import MIN_POINTS

    rng = np.random.default_rng(11)
    coords = np.column_stack([rng.uniform(-2000, 2000, 20), rng.uniform(-2000, 2000, 20)])
    true = np.array([12.0, 0.0008, -0.0005, 1e-8, -8e-9, 5e-9])   # 二次曲面系数
    dx, dy = coords[:, 0], coords[:, 1]
    v = true[0] + true[1] * dx + true[2] * dy + true[3] * dx ** 2 + true[4] * dy ** 2 + true[5] * dx * dy
    v_noisy = v + rng.normal(0, 0.005, 20)
    # 用 GNSSTools 约定：中心=最后一个点（coef 是相对中心的），因此先转绝对面再检验恢复
    center = (float(coords[-1, 0]), float(coords[-1, 1]))
    f = fit_height(coords, v_noisy, "quadratic", "xi", "planar", "last")
    assert f.coef.shape == (6,)
    # 在格点处比较拟合面与真值面（允许 mm 级）
    for lon in (-1500.0, 0.0, 1500.0):
        for lat in (-1500.0, 0.0, 1500.0):
            x_abs, y_abs = center[0] + lon, center[1] + lat
            tv = (12.0 + 0.0008 * x_abs + -0.0005 * y_abs + 1e-8 * x_abs ** 2
                  - 8e-9 * y_abs ** 2 + 5e-9 * x_abs * y_abs)
            cv = true[0] + true[1] * lon + true[2] * lat + true[3] * lon ** 2 + true[4] * lat ** 2 + true[5] * lon * lat
            # 注意: 真值面按绝对坐标定义，拟合按中心化坐标；两者在同一点应一致
            fitv = eval_height(f, x_abs, y_abs)
            assert abs(fitv - tv) < 0.02, f"二次面偏差 {fitv - tv} @({x_abs},{y_abs})"
            _ = cv
    best = select_best_fit(coords, v_noisy, "xi", "planar")
    assert best.mode in MIN_POINTS
    assert best.loo_rms is not None
    # 点数不足必须抛错
    try:
        fit_height(coords[:4], v_noisy[:4], "quadratic", "xi", "planar")
        raise AssertionError("点数不足应抛 ValueError")
    except ValueError:
        pass


# ⑧ autoselect 对比表结构
def test_autoselect():
    from app.core.autoselect import compare_methods, detect_coord_kind

    rng = np.random.default_rng(3)
    # 平面→平面
    src = np.column_stack([3354000 + rng.uniform(-2000, 2000, 8), 500000 + rng.uniform(-2000, 2000, 8)])
    p = Param4(dx=100.0, dy=-50.0, a=0.999998, b=1e-6)
    px, py = apply_4param(p, src[:, 0], src[:, 1])
    dst = np.column_stack([px, py])
    h1 = rng.uniform(30, 60, 8)
    h2 = h1 - (12.0 + 0.0003 * src[:, 0] * 0.001) + rng.normal(0, 0.01, 8)
    rows = compare_methods(src, dst, h1, h2, "planar", "planar")
    methods = [r["method"] for r in rows]
    assert "planar4" in methods and any(r["selected"] for r in rows)
    sel = [r for r in rows if r["selected"]][0]
    assert sel["rms_xy"] < 1e-6
    assert any(r["method"].startswith("heightfit") for r in rows)
    # 大地→平面
    lat = rng.uniform(29.9, 30.1, 8)
    lon = rng.uniform(113.9, 114.1, 8)
    gx, gy = gauss_kruger(lat, lon, 114.0, CGCS2000)
    dst2 = np.column_stack([gx + 50.0, gy - 30.0])
    rows2 = compare_methods(np.column_stack([lon, lat]), dst2, None, None, "lonlat", "planar")
    m2 = [r["method"] for r in rows2]
    assert "direct_gk" in m2 and "chain74" in m2
    assert detect_coord_kind(np.array([[114.0, 30.0], [113.9, 30.1]])) == "lonlat"
    assert detect_coord_kind(src) == "planar"


def run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    return len(fns)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed.")
    sys.exit(1 if failed else 0)
