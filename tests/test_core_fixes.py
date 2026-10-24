"""核心数值回归：七参数 BLH 输出、平面高程拟合自变量、d.ms 识别、子午线弧长系数。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np  # noqa: E402

from app.common.params import ParamApplier  # noqa: E402
from app.core import anglefmt  # noqa: E402
from app.core.ellipsoid import get_ellipsoid  # noqa: E402
from app.logic import apply_params, cp_methods  # noqa: E402


def _exact_arc(ell, b_deg: float) -> float:
    x, w = np.polynomial.legendre.leggauss(200)
    b = math.radians(b_deg)
    phi = (x + 1) / 2 * b
    ww = w / 2 * b
    f = ell.a * (1 - ell.e2) / (1 - ell.e2 * np.sin(phi) ** 2) ** 1.5
    return float(np.sum(f * ww))


def run() -> int:
    checks = 0
    ell = get_ellipsoid("WGS84")

    # 1) 七参数恒等变换：BLH → BLH 输出必须等于输入（曾把度当弧度，输出 1718°）
    doc = {"schema": "coordparam/1", "kind": "seven", "ellipsoid": "WGS84",
           "source_kind": "lonlat", "target_kind": "lonlat",
           "seven": {"dx": 0.0, "dy": 0.0, "dz": 0.0, "rx_s": 0.0, "ry_s": 0.0, "rz_s": 0.0,
                     "scale_ppm": 0.0},
           "projection": None, "projection_src": None, "heightfit": None}
    res, err = apply_params.apply_points(["P"], [("30.0", "116.0", "100.0")], doc,
                                         out_lonlat=True, src_lonlat=False, kind="seven",
                                         do_plane=True, do_height=True, ell=ell)
    assert err is None, err
    b_out, l_out = res[0]["vals"][0], res[0]["vals"][1]
    assert abs(b_out - 30.0) < 1e-6 and abs(l_out - 116.0) < 1e-6, (b_out, l_out)
    assert abs(res[0]["vals"][2] - 100.0) < 1e-3, res[0]["vals"][2]
    checks += 1

    # 2) 平面→平面 + 高程拟合：拟合自变量是源坐标（曾用平移后的目标坐标，差 100 m）
    doc2 = {"schema": "coordparam/1", "kind": "planar4", "ellipsoid": "CGCS2000",
            "source_kind": "planar", "target_kind": "planar",
            "planar4": {"dx": 100000.0, "dy": 0.0, "a": 1.0, "b": 0.0},
            "projection": None, "projection_src": None,
            "heightfit": {"mode": "plane", "space": "planar", "value_type": "dh",
                          "coef": [0.0, 0.001, 0.0], "center": [0.0, 0.0], "loo_rms": None}}
    ap = ParamApplier(doc2)
    _x2, _y2, h2 = ap.apply_planar(0.0, 0.0, 10.0)
    assert abs(h2 - 10.0) < 1e-6, h2
    checks += 1

    # 3) d.ms 结构判别：30.59（=30°59′）是合法 d.ms，不能被当成十进制度
    for v in (30.59, 30.02, 30.04, 30.09, 30.15, 30.18, 30.40):
        assert anglefmt.dms_plausible(v) is True, v
    assert anglefmt.dms_plausible(25.905919) is False       # 90′59″ 不合法
    checks += 1

    # 4) 子午线弧长：projection 与精确积分差 < 5 µm（e⁸ 分母曾写成 32768）；
    #    gk_engine 的 A4/e¹⁰ 分母已修正（曾写成 13072），但它没有 sin8φ 项，
    #    留着约 31 µm（0.03 mm）的 sin8 残差，属截断量级，这里按 50 µm 卡。
    from app.core import gk_engine, projection
    m1, m2, m3, m4 = gk_engine._merid_coefs(ell.a, ell.e2)
    for b in (30.0, 45.0, 60.0):
        ex = _exact_arc(ell, b)
        d_gk = abs(gk_engine._meridian_arc(math.radians(b), m1, m2, m3, m4) - ex)
        d_pj = abs(float(projection._meridian_arc(math.radians(b), ell)) - ex)
        assert d_gk < 5e-5, (b, d_gk)
        assert d_pj < 5e-6, (b, d_pj)
    checks += 1

    # 5) 保存参数时椭球要跟随页面选择
    d = cp_methods.build_param_doc("t", ("planar", "planar"), "planar4", {}, None, [], False,
                                   ell_name="Beijing54")
    assert d["ellipsoid"] == "Beijing54", d["ellipsoid"]
    checks += 1

    return checks
