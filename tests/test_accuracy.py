# -*- coding: utf-8 -*-
"""精度对比重构测试：logic/accuracy 引擎 + 健壮性（损坏参数/相同参数/纯格网参数）。"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.logic import accuracy as acc  # noqa: E402
from app.logic import cp_methods as cp  # noqa: E402


def _make_cot(path: Path, l0: float = 115.0) -> list[tuple]:
    """生成手簿 cot（源=GNSS 大地，目标=施工平面），返回 (lon, lat) 列表。"""
    from app.core import anglefmt
    from app.core.projection import gauss_kruger
    rng = np.random.RandomState(21)
    src = [(115.05 + rng.uniform(0, 0.25), 30.05 + rng.uniform(0, 0.15)) for _ in range(6)]
    lines = []
    for i, (lon, lat) in enumerate(src):
        gx, gy = gauss_kruger(lat, lon, l0)
        bd = anglefmt.rad_to_dms(math.radians(lat))
        ld = anglefmt.rad_to_dms(math.radians(lon))
        lines.append(f"P{i+1},{gx:.4f},{gy:.4f},{52.0 + i * 0.1:.4f},"
                     f"{bd:.8f},{ld:.8f},{60.0 + i * 0.1:.4f},Y,Y")
    path.write_text("\n".join(lines), encoding="utf-8")
    return src


def _refit_chain74_from_pairs(pairs: acc.PairSet, l0: float) -> dict:
    """模拟控制点页：对 cot 点对按 chain74 拟合并组参数文档。"""
    updates, _t = cp.refit("chain74", pairs.src, pairs.dst,
                           {"l0": l0, "x0_m": 0.0, "y0_m": 500000.0},
                           (pairs.src_kind, pairs.dst_kind))
    return cp.build_param_doc("链式参数", (pairs.src_kind, pairs.dst_kind), "chain74",
                              updates, None, pairs.names, False)


def test_load_pairs_cot_and_cass():
    d = Path(tempfile.mkdtemp())
    src = _make_cot(d / "p.cot")
    pairs = acc.load_pairs(d / "p.cot")
    assert pairs.src_kind == "lonlat" and pairs.dst_kind == "planar"
    assert len(pairs.names) == 6 and pairs.dst is not None
    assert np.allclose(pairs.src[:, 0], [s[0] for s in src], atol=1e-9)
    # CASS
    cass = d / "p.txt"
    cass.write_text("100.0,200.0,10.0:1100.0,1200.0,12.0\n"
                    "300.0,400.0,11.0:1300.0,1400.0,13.0\n", encoding="utf-8")
    p2 = acc.load_pairs(cass)
    assert p2.src_kind == "planar" and p2.dst_kind == "planar" and len(p2.names) == 2


def test_compare_two_params_verdict():
    """cot 点对 × 两参数（链式 vs 直接经纬度）：链式残差更小，结论点名推荐。"""
    d = Path(tempfile.mkdtemp())
    _make_cot(d / "p.cot")
    pairs = acc.load_pairs(d / "p.cot")
    from app.core.heightfit import fit_height
    doc_a = _refit_chain74_from_pairs(pairs, 115.0)
    hf_a = fit_height(pairs.src, pairs.dst_h - pairs.src_h, "plane", "dh", "lonlat")
    doc_a = cp.build_param_doc("链式参数", ("lonlat", "planar"), "chain74",
                               _refit_chain74_from_pairs(pairs, 115.0) or {},
                               {"heightfit": hf_a}, pairs.names, False)
    updates_b, _t = cp.refit("planar4deg", pairs.src, pairs.dst,
                             {"l0": 115.0, "x0_m": 0.0, "y0_m": 500000.0},
                             ("lonlat", "planar"))
    hf_b = fit_height(pairs.src, pairs.dst_h - pairs.src_h, "quadratic", "dh", "lonlat")
    doc_b = cp.build_param_doc("直接四参数", ("lonlat", "planar"), "planar4deg",
                               updates_b, {"heightfit": hf_b}, pairs.names, False)
    rows, verdict = acc.compare(pairs, [("链式参数", doc_a), ("直接四参数", doc_b)],
                                True, True, os.path.join(_ROOT, "models"))
    by = {r["name"]: r for r in rows}
    assert by["链式参数"]["feasible_plane"] and by["链式参数"]["feasible_h"]
    assert by["链式参数"]["rms_xy"] < 0.01, by["链式参数"]["rms_xy"]
    assert by["直接四参数"]["rms_xy"] > by["链式参数"]["rms_xy"]
    assert by["直接四参数"]["rms_h"] <= by["链式参数"]["rms_h"] + 1e-12  # 曲面⊃平面
    assert "更优" in verdict and "坐标对比" in verdict and "高程对比" in verdict
    # 平面残差逐点已入行
    assert by["链式参数"]["res_xy"] is not None and len(by["链式参数"]["res_xy"]) == 6


def test_identical_and_corrupted_params():
    """相同参数对比不崩溃且有提示；损坏参数文件按不可用行呈现、列表可见可删。"""
    from app.common.params import ParamLibrary

    d = Path(tempfile.mkdtemp())
    _make_cot(d / "p.cot")
    pairs = acc.load_pairs(d / "p.cot")
    doc_a = _refit_chain74_from_pairs(pairs, 115.0)
    rows, verdict = acc.compare(pairs, [("参数A", doc_a), ("参数A", doc_a)],
                                True, True, os.path.join(_ROOT, "models"))
    assert "相同的参数" in verdict
    assert all(r["feasible_plane"] for r in rows)
    # 损坏 JSON：list 可见（标记损坏）、load 抛 ValueError（不崩溃）
    lib = ParamLibrary(d / "params")
    (d / "params" / "bad.json").write_text("{ this is not json", encoding="utf-8")
    names = [p["name"] for p in lib.list()]
    assert any("损坏" in n for n in names), names
    try:
        lib.load("bad")
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass
    # 对比引擎收到损坏文档 → 不可用行而非异常
    rows2, v2 = acc.compare(pairs, [("坏参数", None)], True, False,
                            os.path.join(_ROOT, "models"))
    assert not rows2[0]["feasible_plane"] and "无法使用" in rows2[0]["note"]
    assert "无可用的平面转换参数" in v2 or "仅" in v2


def test_geoid_only_param_on_cot():
    """纯大地水准面参数（无平面/无高程拟合）在 cot 点对上仅高程可对比。"""
    d = Path(tempfile.mkdtemp())
    _make_cot(d / "p.cot")
    pairs = acc.load_pairs(d / "p.cot")
    doc = {"kind": "geoid", "name": "纯格网参数", "geoid_grid": "egm96_15",
           "use_geoid": True, "ellipsoid": "CGCS2000"}
    rows, verdict = acc.compare(pairs, [("纯格网参数", doc)], False, True,
                                os.path.join(_ROOT, "models"))
    r = rows[0]
    assert not r["feasible_plane"] and r["feasible_h"], r["note"]
    assert r["rms_h"] is not None and r["n_h"] > 0
    # build_param_doc：纯格网 sel_h 保存为 geoid 参数
    fake_hf = type("HF", (), {})()  # 占位（不该被用到）
    doc2 = cp.build_param_doc("纯格网", ("lonlat", "planar"), "geoid", {},
                              {"grid_name": "样例格网", "n_h": 5, "rms_h": 0.02,
                               "loo_rms": None, "note": ""}, pairs.names, False)
    assert doc2["kind"] == "geoid" and doc2["geoid_grid"] == "样例格网"
    assert doc2.get("heightfit") is None


def run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"{len(tests)}/{len(tests)} accuracy tests passed.")
    return 1


if __name__ == "__main__":
    run()
