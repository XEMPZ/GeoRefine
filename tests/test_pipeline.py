"""端到端测试：样例控制点 → 参数 → 样例照片批处理 → 样例DXF转换，全链路对照真值。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.common import hconv  # noqa: E402
from app.common.io_csv import read_points_csv  # noqa: E402
from app.common.params import ParamApplier, ParamLibrary  # noqa: E402
from app.core import autoselect  # noqa: E402
from app.core.geoid import GridModel, load_grid  # noqa: E402
from app.core.heightfit import fit_height  # noqa: E402
from app.core.transform2d import apply_4param, fit_4param  # noqa: E402
from app.photo import batch as photo_batch  # noqa: E402


def test_planar_chain(tmp_path=None):
    pts = read_points_csv(str(_ROOT / "sample_data" / "控制点样本_平面对.csv"))
    assert len(pts) == 12
    src = np.array([(p["x"], p["y"], p["h"]) for p in pts])
    dst = np.array([(p["x2"], p["y2"], p["H"]) for p in pts])
    rows = autoselect.compare_methods(src[:, :2], dst[:, :2], src[:, 2], dst[:, 2], "planar", "planar")
    sel = [r for r in rows if r["selected"]]
    assert sel, rows
    assert sel[0]["method"] == "planar4"
    assert sel[0]["rms_xy"] < 0.02, f"四参数 rms={sel[0]['rms_xy']}"
    # 真值参数正算 vs 拟合参数应用：区域内误差 < 2cm
    f4 = fit_4param(src[:, :2], dst[:, :2])
    chk = np.column_stack([src[:3, 0] + 111.0, src[:3, 1] - 77.0])
    pred = apply_4param(f4.param, chk[:, 0], chk[:, 1])
    px, py = apply_4param(__import__("app.core.transform2d", fromlist=["Param4"]).Param4(
        102.3456, -56.7890,
        0.9999975 * np.cos(np.radians(0.25 / 3600)), 0.9999975 * np.sin(np.radians(0.25 / 3600))),
        chk[:, 0], chk[:, 1])
    err = np.hypot(pred[0] - px, pred[1] - py)
    assert np.max(err) < 0.02, f"与真值预测差 {np.max(err):.4f} m"
    # 参数库 → ParamApplier 平面应用
    d = {"schema": "coordparam/1", "name": "pipeline_p4", "kind": "planar4",
         "source_kind": "planar", "target_kind": "planar", "ellipsoid": "WGS84",
         "projection": None,
         "planar4": {"dx": f4.param.dx, "dy": f4.param.dy, "a": f4.param.a, "b": f4.param.b},
         "seven": None,
         "heightfit": None, "accuracy": {}, "created": "", "points_used": []}
    ap = ParamApplier(d)
    x2, y2, h2 = ap.apply_planar(src[0, 0], src[0, 1], src[0, 2])
    assert abs(np.hypot(x2 - dst[0, 0], y2 - dst[0, 1])) < 0.03
    _ = tmp_path


def test_heightfit_dh():
    pts = read_points_csv(str(_ROOT / "sample_data" / "控制点样本_平面对.csv"))
    src = np.array([(p["x"], p["y"], p["h"]) for p in pts])
    dst = np.array([(p["x2"], p["y2"], p["H"]) for p in pts])
    dh = dst[:, 2] - src[:, 2]
    hf = fit_height(src[:, :2], dh, "plane", "dh", "planar", "last")
    assert hf.coef.shape == (3,)
    # dh 真值: 0.05 + 2e-5*(x2-x2[-1]) - 1.5e-5*(y2-y2[-1])，拟合 RMS 应 < 噪声3倍
    assert hf.rms < 0.05, hf.rms


def test_spatial_chain():
    pts = read_points_csv(str(_ROOT / "sample_data" / "控制点样本_空间对.csv"))
    assert len(pts) == 10
    src = np.array([(p["L"], p["B"], p["H_ell"]) for p in pts])     # (lon,lat,h)
    dst = np.array([(p["L2"], p["B2"], p["H"]) for p in pts])
    rows = autoselect.compare_methods(src[:, :2], dst[:, :2], src[:, 2], dst[:, 2],
                                      "lonlat", "lonlat")
    sel = [r for r in rows if r["selected"]]
    assert sel and sel[0]["method"] == "seven", [r["method"] for r in rows]
    assert sel[0]["rms_xy"] < 0.05, f"七参数 rms={sel[0]['rms_xy']}"


def test_photo_pipeline_with_grid():
    photo_root = _ROOT / "sample_data" / "样例照片POS"
    grid = load_grid(str(_ROOT / "sample_data" / "局部似大地水准面格网_样例.csv"))
    model = GridModel(grid)
    fn = hconv.make_photo_height_fn("geoid", model=model)
    res = photo_batch.process(str(photo_root), fn, mode_name="格网:样例")
    try:
        assert res["total"] == 8 and res["ok"] == 8, (res["total"], res["ok"], res["rows"][0])
        # 台账数值与直接计算一致：h_before − ξ == h_after == 处理后读回值（EXIF 存储栅格 1cm）
        from app.photo.pos_io import extract_pos
        for row in res["rows"]:
            pos = extract_pos(row["path"])
            xi = model.undulation(pos.lon, pos.lat)
            assert abs(row["h_after"] - pos.h_ell) < 0.01, row["path"]
            assert abs(row["h_before"] - xi - row["h_after"]) < 0.01, row["path"]
        # 台账文件存在且 8 行数据
        assert Path(res["ledger_csv"]).exists()
        with open(res["ledger_csv"], encoding="utf-8-sig") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        assert len(lines) == 9, len(lines)
    finally:
        rb = photo_batch.rollback(str(photo_root))
        assert rb["restored"] == 8
        shutil.rmtree(photo_root / "_POS备份", ignore_errors=True)
        for f in photo_root.glob("POS处理台账_*.csv"):
            f.unlink()


def test_cad_pipeline_with_param():
    from app.cad.dxf_transform import transform_dxf
    from app.core.transform2d import Param4
    import ezdxf
    d = tempfile.mkdtemp()
    out = os.path.join(d, "out.dxf")
    p = Param4(dx=102.3456, dy=-56.789, a=0.9999975, b=1.2e-6)

    def hfn(X, Y, z):
        return z - 8.0, -8.0, "offset"

    res = transform_dxf(str(_ROOT / "sample_data" / "样例测区.dxf"), out, planar=p, height_fn=hfn)
    assert res.transformed == res.total and not res.unsupported
    doc2 = ezdxf.readfile(out)
    ents = list(doc2.modelspace())
    assert len(ents) == res.total
    # 抽查 POINT 实体：从输入 DXF 读原坐标，按契约公式 X'=dy+a·X+b·Y ; Y'=dx−b·X+a·Y 计算期望
    doc0 = ezdxf.readfile(str(_ROOT / "sample_data" / "样例测区.dxf"))
    p0 = [e for e in doc0.modelspace() if e.dxftype() == "POINT"][0]
    pe = [e for e in ents if e.dxftype() == "POINT"][0]
    ex = p.dy + p.a * p0.dxf.location.x + p.b * p0.dxf.location.y
    ey = p.dx - p.b * p0.dxf.location.x + p.a * p0.dxf.location.y
    assert abs(pe.dxf.location.x - ex) < 1e-6, (pe.dxf.location.x, ex)
    assert abs(pe.dxf.location.y - ey) < 1e-6, (pe.dxf.location.y, ey)
    assert abs(pe.dxf.location.z - (p0.dxf.location.z - 8.0)) < 1e-9
    shutil.rmtree(d, ignore_errors=True)


def test_param_library_roundtrip():
    lib = ParamLibrary(str(_ROOT / "params"))
    before = {p["name"] for p in lib.list()}
    d = {"schema": "coordparam/1", "name": "_pipeline_temp_", "kind": "planar4",
         "planar4": {"dx": 1, "dy": 2, "a": 1.0, "b": 0.0},
         "accuracy": {}, "created": ""}
    lib.save(d)
    assert "_pipeline_temp_" in {p["name"] for p in lib.list()}
    lib.delete("_pipeline_temp_")
    assert {p["name"] for p in lib.list()} == before


def run():
    fns = [test_planar_chain, test_heightfit_dh, test_spatial_chain,
           test_photo_pipeline_with_grid, test_cad_pipeline_with_param,
           test_param_library_roundtrip]
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
            import traceback
            traceback.print_exc()
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed.")
    sys.exit(1 if failed else 0)
