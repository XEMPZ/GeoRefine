r"""大地水准面格网格式测试。

锁住一个曾被我在文档里写错的结论：格网支持**不止 gtx**，
而且 RMS 转换 / OSGB 模型转换 / 点云转换三条链路对格式完全无感。

夹具现场生成：按 app/core/geoid.py::read_ggf 的布局（天宝 GGF）造一个
与实际模型区域重叠的小格网，与同解析式的 CSV 格网交叉校验。
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 覆盖广东模型区域（与 models/_test_geoid_liangchang.csv 同范围同解析式）
LON0, LON1, LAT0, LAT1 = 117.91, 117.99, 25.85, 25.93
NLA = NLO = 9


def _xi(lat, lon):
    """已知解析式，便于校验插值结果。"""
    return 12.0 + 0.08 * (lon - 117.95) + 0.05 * (lat - 25.89)


def _make_ggf(path):
    """按 read_ggf 的布局造一个真实 GGF 文件（北在前）。"""
    dla = (LAT1 - LAT0) / (NLA - 1)
    dlo = (LON1 - LON0) / (NLO - 1)
    lats = np.linspace(LAT0, LAT1, NLA)
    lons = np.linspace(LON0, LON1, NLO)
    vals_sn = np.array([[_xi(la, lo) for lo in lons] for la in lats])
    vals_ns = vals_sn[::-1, :]                     # 文件里北在前
    buf = bytearray(146)
    nm = b"TESTGGF"
    buf[16:16 + len(nm)] = nm
    struct.pack_into("<6d", buf, 48, LAT0, LAT1, LON0, LON1, dla, dlo)
    struct.pack_into("<2i", buf, 96, NLA, NLO)
    struct.pack_into("<h", buf, 136, 1)
    Path(path).write_bytes(bytes(buf) + np.asarray(vals_ns, dtype="<f4").tobytes())
    return vals_sn


def _make_csv(path):
    dla = (LAT1 - LAT0) / (NLA - 1)
    dlo = (LON1 - LON0) / (NLO - 1)
    lats = np.linspace(LAT0, LAT1, NLA)
    lons = np.linspace(LON0, LON1, NLO)
    # 列序为 lon,lat,value（见 app/core/geoid.py::read_csv_grid 第 95 行）
    lines = ["lon,lat,xi"]
    for la in lats:
        for lo in lons:
            lines.append("%.6f,%.6f,%.6f" % (lo, la, _xi(la, lo)))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_ggf_parses_like_its_layout():
    """GGF 按 read_ggf 的布局解析：范围、形状、数值都要对。"""
    from app.core.geoid import load_grid
    wd = tempfile.mkdtemp(prefix="ggftest_")
    try:
        p = os.path.join(wd, "t.ggf")
        vals_sn = _make_ggf(p)
        g = load_grid(p)
        assert g.fmt == "ggf", g.fmt
        assert g.values.shape == (NLA, NLO), g.values.shape
        assert abs(g.lat_min - LAT0) < 1e-9 and abs(g.lat_max - LAT1) < 1e-9
        assert abs(g.lon_min - LON0) < 1e-9 and abs(g.lon_max - LON1) < 1e-9
        # 行序：文件北在前，读出后应转成南在前
        assert np.abs(g.values - vals_sn).max() < 1e-5, "行序或数值不对"
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_ggf_and_csv_agree():
    """GGF 与 CSV 两种格式给出一致的 ξ（插值层面）。"""
    from app.core.geoid import GridModel, load_grid
    wd = tempfile.mkdtemp(prefix="ggftest_")
    try:
        pg = os.path.join(wd, "t.ggf")
        pc = os.path.join(wd, "t.csv")
        _make_ggf(pg)
        _make_csv(pc)
        mg = GridModel(load_grid(pg))
        mc = GridModel(load_grid(pc))
        rng = np.random.default_rng(3)
        worst = 0.0
        for _ in range(40):
            la = LAT0 + rng.uniform(0.001, LAT1 - LAT0 - 0.001)
            lo = LON0 + rng.uniform(0.001, LON1 - LON0 - 0.001)
            worst = max(worst, abs(mg.undulation(lo, la) - mc.undulation(lo, la)))
        assert worst < 1e-4, "GGF 与 CSV 结果差异过大：%.3e m" % worst
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_grid_format_agnostic_in_transform_path():
    """**核心**：转换链路（ParamApplier）对格网格式无感——GGF 与 CSV 结果一致。

    这条锁住的是"OSGB/点云转换只能吃 gtx"这个错误印象：
    适配层没有任何格式判断，一律经 geoid.load_grid() 分发。
    """
    from app.common.params import ParamApplier
    wd = tempfile.mkdtemp(prefix="ggftest_")
    try:
        # 参数查格网是按 models/ 目录里的 stem 名，故夹具要放进项目 models/
        md = _ROOT / "models"
        md.mkdir(exist_ok=True)
        name_g, name_c = "_pytest_grid_ggf", "_pytest_grid_csv"
        _make_ggf(md / (name_g + ".ggf"))
        _make_csv(md / (name_c + ".csv"))

        def doc(grid):
            return {"schema": "coordparam/1", "name": "t", "kind": "planar4",
                    "source_kind": "lonlat",          # 源侧投影反算必需
                    "target_kind": "planar", "ellipsoid": "CGCS2000",
                    "planar4": {"dx": 100.0, "dy": -7.0, "a": 1.0, "b": 0.0},
                    "projection_src": {"l0": 117.0, "x0": 0.0, "y0": 500000.0,
                                       "h0": 0.0, "rigorous": False, "b0": 25.9},
                    "geoid_grid": grid}

        north, east, h = 2865087.029595, 595421.877208, 130.0
        outs = []
        for gname in (name_g, name_c):
            ap = ParamApplier(doc(gname))
            outs.append(ap.apply_planar_at(north, east, h, north, east,
                                           do_plane=True, do_height=True))
        d = np.abs(np.array(outs[0]) - np.array(outs[1])).max()
        assert d < 1e-4, "GGF 与 CSV 在转换链路里结果不一致：%.3e m" % d
        # 高程确实被 ξ 修正了（不是原值透传）
        assert abs(outs[0][2] - h) > 1.0, "格网未被应用：h=%.4f" % outs[0][2]
        # 平面部分不受格网格式影响
        assert abs(outs[0][0] - (north + 100.0)) < 1e-6
        assert abs(outs[0][1] - (east - 7.0)) < 1e-6
    finally:
        for n in ("_pytest_grid_ggf.ggf", "_pytest_grid_csv.csv"):
            try:
                (_ROOT / "models" / n).unlink()
            except OSError:
                pass
        shutil.rmtree(wd, ignore_errors=True)


TESTS = [test_ggf_parses_like_its_layout, test_ggf_and_csv_agree,
         test_grid_format_agnostic_in_transform_path]


def run():
    for fn in TESTS:
        fn()
        print("    ok  %s" % fn.__name__)
    return len(TESTS)


if __name__ == "__main__":
    run()
