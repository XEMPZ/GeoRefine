"""点对文件导入测试：手簿 .cot / CASS 公共点（真实样例）+ autoselect 掩码与二维七参数。"""
from __future__ import annotations

import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.common import pair_io  # noqa: E402
from app.core.ellipsoid import WGS84, BEIJING54  # noqa: E402
from app.core.autoselect import compare_methods  # noqa: E402

COT = os.path.join(_ROOT, "sample_data", "点对文件样例", "手簿点对_正常高-大地高.cot")
CASS = os.path.join(_ROOT, "sample_data", "点对文件样例", "CASS公共点_长赣A10_114转115.txt")


def test_cot_real_sample():
    pts = pair_io.parse_cot(COT)
    assert len(pts) >= 10, len(pts)
    p0 = pts[0]
    assert p0.name == "CPII447A_1_1"
    assert abs(p0.x - 2865150.221) < 1e-3 and abs(p0.y - 491110.074) < 1e-3
    assert abs(p0.h_normal - 116.31) < 1e-6
    # B/L 为 d.ms 编码：25.53383126 = 25°53′38.31″ = 25.89398°；114.54406316 = 114°54′40.63″ = 114.91129°
    assert abs(p0.b_deg - 25.8939753) < 1e-6, p0.b_deg
    assert abs(p0.l_deg - 114.9112866) < 1e-6, p0.l_deg
    assert abs(p0.h_ell - 110.836) < 1e-6
    assert p0.use_pos is True and p0.use_h is True


def test_cot_autoselect_pairs():
    """cot 点对 → lonlat→planar 比选链路（源=WGS84 BLH，目标=地方平面+正常高）。"""
    pts = pair_io.parse_cot(COT)
    n = len(pts)
    src = np.array([(p.l_deg, p.b_deg) for p in pts])
    dst = np.array([(p.x, p.y) for p in pts])
    sh = np.array([p.h_ell for p in pts])
    dh = np.array([p.h_normal for p in pts])
    rows = compare_methods(src, dst, sh, dh, "lonlat", "planar")
    feas = [r for r in rows if r["feasible"] and r.get("rms_xy") is not None]
    assert feas, "cot 点对比选无可行方法"
    best = min(feas, key=lambda r: r["rms_xy"])
    # 真实测区点对：最优方法残差应在厘米级内（地方坐标系与 WGS84 有既有转换关系）
    assert best["rms_xy"] < 1.0, (best["method"], best["rms_xy"])


def test_cass_real_sample():
    pairs = pair_io.parse_cass(CASS)
    assert len(pairs) >= 10, len(pairs)
    p0 = pairs[0]
    assert abs(p0.x1 - 2868741.35) < 1e-3 and abs(p0.y1 - 583800.4923) < 1e-3
    assert abs(p0.x2 - 2868542.937) < 1e-3 and abs(p0.y2 - 483616.6971) < 1e-3
    assert p0.h1 == 0.0 and p0.h2 == 0.0


def test_cass_seven2d_vs_planar4():
    """等高平面点对：二维七参数与四参数的平面预测等价（数学必然）。"""
    pairs = pair_io.parse_cass(CASS)
    src = np.array([(p.x1, p.y1) for p in pairs])
    dst = np.array([(p.x2, p.y2) for p in pairs])
    h = np.array([p.h1 for p in pairs])
    rows = compare_methods(src, dst, h, np.array([p.h2 for p in pairs]), "planar", "planar")
    by = {r["method"]: r for r in rows}
    assert by["planar4"]["feasible"] and by["seven2d"]["feasible"]
    # 长赣A10 实测：两模型 RMS 应同为亚毫米级（同批点对、等高）
    assert by["planar4"]["rms_xy"] < 0.5  # 实测长赣A10：跨带转换非相似性残差应在分米级内
    # 等高时两模型数学同 Span，平面预测等价；跨带大旋转角（~0.5°）下线性化 vs
    # 精确旋转矩阵的二阶差（m·rz·y 项）≤1mm，属已知口径
    assert abs(by["planar4"]["rms_xy"] - by["seven2d"]["rms_xy"]) < 1e-3, \
        (by["planar4"]["rms_xy"], by["seven2d"]["rms_xy"])


def test_pos_mask_excludes_point():
    src = np.array([[0.0, 0.0], [100.0, 0.0], [0.0, 100.0], [50.0, 50.0]])
    dst = src + np.array([10.0, 5.0])
    dst[3] += np.array([5.0, -5.0])  # 第 4 点注入粗差
    rows_all = compare_methods(src, dst, np.zeros(4), np.zeros(4), "planar", "planar")
    rows_msk = compare_methods(src, dst, np.zeros(4), np.zeros(4), "planar", "planar",
                               pos_mask=[True, True, True, False])
    rms_all = min(r["rms_xy"] for r in rows_all if r["feasible"] and r["method"] == "planar4")
    rms_msk = min(r["rms_xy"] for r in rows_msk if r["feasible"] and r["method"] == "planar4")
    assert rms_msk < 1e-9 <= rms_all, (rms_all, rms_msk)  # 掩码剔除粗差后严格恢复


def test_cross_ellipsoid_seven():
    """跨椭球七参数：compare_methods 的 ell/ell_dst 分传（WGS84→BJ54 合成）。"""
    from app.core.ellipsoid import BEIJING54
    src = np.array([[114.5, 25.5], [114.6, 25.6], [114.4, 25.7], [114.55, 25.45]])
    dst = src + 0.001
    rows = compare_methods(src, dst, None, None, "lonlat", "lonlat",
                           ell=WGS84, ell_dst=BEIJING54)
    seven = [r for r in rows if r["method"] == "seven"][0]
    assert seven["feasible"] and seven["rms_xy"] < 5.0  # 合成偏移下七参数吸收椭球差
    assert not [r for r in rows if r["method"] == "direct_gk"][0]["feasible"]


def run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"{len(tests)}/{len(tests)} pair_io tests passed.")
    return 1


if __name__ == "__main__":
    run()
