"""坐标转换引擎测试（gk_engine / anglefmt / 三参数）。内嵌实测真值。

真值来源：CoordTran.exe（铁四院 V4.2.3）反射黑盒探针实测输出，
正算与 144 组已知参考坐标全量对比 max 0.5 µm（见 tests/data/projection_truth.csv）。
运行: $env:PYTHONIOENCODING='utf-8'; python tests\test_gk_engine.py
"""
from __future__ import annotations

import csv
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

D2R = math.pi / 180.0

from app.core import anglefmt  # noqa: E402
from app.core.gk_engine import (ProjParams, effective_a, gauss_forward,  # noqa: E402
                                gauss_inverse, mean_curvature_radius)
from app.core.transform3d import fit_3param, apply_3param  # noqa: E402

A, INVF = 6378137.0, 298.257223563

# CoordTran 实测真值（mode,B0deg,h0,k,x0km,y0km,L0,Bdeg,Ldeg -> X,Y）
TRUTH = [
    ("approx", 0.0, 1.0, 28.0, 103.5, 3099348.64467, 352447.704379),
    ("approx", 0.0, 1.0, 30.26345678, 105.0, 3349318.811454, 500000.0),
    ("approx", 500.0, 1.0, 32.5, 107.9, 3601288.722844, 772603.530468),
    ("rigorous", 500.0, 1.0, 32.5, 107.9, 3601289.19847, 772603.566471),
    ("approx", 2000.0, 0.9996, 32.5, 107.9, 3600694.747503, 772558.568848),
    ("rigorous", 2000.0, 0.9996, 28.0, 103.5, 3099082.019713, 352460.397732),
]


def test_forward_against_coordtran():
    worst = 0.0
    for mode, h0, k, bd, ld, x_ref, y_ref in TRUTH:
        x, y = gauss_forward(A, INVF, 105 * D2R, 30 * D2R, h0, k, 0.0, 500.0,
                             bd * D2R, ld * D2R, rigorous=(mode == "rigorous"))
        worst = max(worst, abs(x - x_ref), abs(y - y_ref))
    assert worst < 1e-5, f"vs CoordTran 真值偏差 {worst*1e6:.3f} µm"


def test_truth_table_file():
    path = os.path.join(_ROOT, "tests", "data", "projection_truth.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert len(rows) >= 140
    worst = 0.0
    for r in rows:
        x, y = gauss_forward(A, INVF, float(r["L0deg"]) * D2R, float(r["B0deg"]) * D2R,
                             float(r["h0"]), float(r["k"]), float(r["x0km"]), float(r["y0km"]),
                             float(r["Bdeg"]) * D2R, float(r["Ldeg"]) * D2R,
                             rigorous=r["mode"] == "rigorous")
        worst = max(worst, abs(x - float(r["X"])), abs(y - float(r["Y"])))
    assert worst < 1e-5, f"全量真值表偏差 {worst*1e6:.3f} µm"


def test_roundtrip():
    worst = 0.0
    for h0, rig in ((0.0, False), (2000.0, True), (2000.0, False)):
        for bd, ld in ((28.0, 103.5), (30.26345678, 105.0), (32.5, 107.9)):
            x, y = gauss_forward(A, INVF, 105 * D2R, 30 * D2R, h0, 1.0, 0.0, 500.0,
                                 bd * D2R, ld * D2R, rigorous=rig)
            b, l = gauss_inverse(A, INVF, 105 * D2R, 30 * D2R, h0, 1.0, 0.0, 500.0, x, y,
                                 rigorous=rig)
            worst = max(worst, abs(b - bd * D2R) * 6.4e6, abs(l - ld * D2R) * 6.4e6)
    assert worst < 1e-6, f"往返闭合 {worst*1000:.6f} mm"


def test_effective_a():
    e2 = 2 / INVF - 1 / INVF ** 2
    assert effective_a(A, INVF, 30 * D2R, 0.0, True) == A
    assert abs(effective_a(A, INVF, 30 * D2R, 1000.0, False) - (A + 1000)) < 1e-9
    # 严密 = a(1 + h0/Ra(B0))，与 CoordTran 探针实测吻合（B0 ∈ {0,15,30.26,51.6,80.2}°）
    for b0d in (0.0, 15.0, 30.26345678, 51.5718, 80.214):
        a_eff = effective_a(A, INVF, b0d * D2R, 1000.0, True)
        expect = A + 1000.0 * (1 - e2 * math.sin(b0d * D2R) ** 2) / math.sqrt(1 - e2)
        assert abs(a_eff - expect) < 1e-9
    Ra = mean_curvature_radius(A, e2, 30 * D2R)
    assert abs(Ra - A * math.sqrt(1 - e2) / (1 - e2 * math.sin(30 * D2R) ** 2)) < 1e-6


def test_dms_anglefmt():
    # CoordTran GetDMSOf/GetRADOf 实测：0.5282 rad -> 30.1549070659716 -> 回 1.8e-14
    dms = anglefmt.rad_to_dms(0.5282)
    assert abs(dms - 30.1549070659716) < 5e-10, dms
    assert abs(anglefmt.dms_to_rad(30.1549070659716) - 0.5282) < 1e-14
    # 空格分隔解析
    assert abs(anglefmt.parse_flexible("105 30 25.68") - anglefmt.dms_to_rad(105.302568)) < 1e-12
    # 进位
    assert abs(anglefmt.dms_to_rad(anglefmt.rad_to_dms(-0.5282)) + 0.5282) < 1e-12
    # 编码语义：30.26345678 = 30°26′34.5678″
    b = anglefmt.dms_to_rad(30.26345678) * 180 / math.pi
    assert abs(b - (30 + 26 / 60 + 34.5678 / 3600)) < 1e-9


def test_projparams_zone_change():
    src = ProjParams(A, INVF, 105 * D2R, 30 * D2R, 0.0, 1.0, 0.0, 500.0)
    dst = ProjParams(A, INVF, 108 * D2R, 30 * D2R, 0.0, 1.0, 0.0, 500.0)
    x, y = src.forward(30 * D2R, 106 * D2R)
    b, l = src.inverse(x, y)
    x2, y2 = dst.forward(b, l)
    b2, l2 = dst.inverse(x2, y2)
    assert abs(l2 - 106 * D2R) < 1e-12 and abs(b2 - 30 * D2R) < 1e-12


def test_three_param():
    import numpy as np
    xyz = np.array([[2000000.0, 500000.0, 3500000.0],
                    [2010123.4, 512345.6, 3498765.4],
                    [1995678.9, 487654.3, 3501234.5]])
    f3 = fit_3param(xyz, xyz + np.array([10.0, -20.0, 30.0]))
    assert abs(f3.param.dx - 10) < 1e-9 and abs(f3.param.dy + 20) < 1e-9
    out = apply_3param(f3.param, xyz[:, 0], xyz[:, 1], xyz[:, 2])
    assert np.max(np.abs(np.column_stack(out) - (xyz + np.array([10.0, -20.0, 30.0])))) < 1e-9
    try:
        fit_3param(xyz[:0], xyz[:0])
        assert False, "空点应报错"
    except ValueError:
        pass


def run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"{len(tests)}/{len(tests)} gk_engine tests passed.")
    return 1


if __name__ == "__main__":
    run()
