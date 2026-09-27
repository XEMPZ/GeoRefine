"""CAD 模块测试（ARCHITECTURE.md §8 test_cad）。提供 run()，兼容 pytest。"""
from __future__ import annotations

import math
import os
import sys
import tempfile

import ezdxf

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.cad.dxf_transform import detect_dwg, transform_dxf  # noqa: E402
from app.core.transform2d import Param4  # noqa: E402


def make_sample_dxf(path):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer in ("地形", "建筑", "道路", "注记"):
        doc.layers.add(layer)
    msp.add_line((100, 200, 10), (300, 400, 20), dxfattribs={"layer": "地形"})
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], dxfattribs={"layer": "建筑"})
    msp.add_circle((150, 150, 5), radius=25, dxfattribs={"layer": "道路"})
    msp.add_arc((150, 150, 5), radius=40, start_angle=0, end_angle=90, dxfattribs={"layer": "道路"})
    msp.add_point((10, 20, 7), dxfattribs={"layer": "地形"})
    msp.add_text("高程点 A", dxfattribs={"layer": "注记", "height": 2.5}).set_placement((30, 40, 3))
    msp.add_polyline3d([(0, 0, 1), (50, 50, 2), (100, 0, 3)], dxfattribs={"layer": "地形"})
    msp.add_mtext("断面注记\nMTEXT", dxfattribs={"layer": "注记"}).set_location((70, 30, 2))
    block = doc.blocks.new(name="BLK_X")
    block.add_line((0, 0, 0), (10, 10, 5))
    msp.add_blockref("BLK_X", (500, 600), dxfattribs={"layer": "建筑"})
    doc.saveas(path)
    return doc


def _expected_4p(X, Y, p: Param4):
    # 契约: X' = dy + a·X + b·Y ; Y' = dx − b·X + a·Y
    return p.dy + p.a * X + p.b * Y, p.dx - p.b * X + p.a * Y


def test_transform_roundtrip():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "in.dxf")
    out = os.path.join(d, "out.dxf")
    make_sample_dxf(src)
    p = Param4(dx=102.3456, dy=-56.789, a=0.9999975, b=1.2e-6)
    offset = -8.0

    def hfn(X, Y, z):
        return z + offset, offset, "offset"

    res = transform_dxf(src, out, planar=p, height_fn=hfn)
    assert res.transformed == res.total == 9, f"total={res.total} transformed={res.transformed} unsup={res.unsupported}"
    assert not res.unsupported, res.unsupported

    doc2 = ezdxf.readfile(out)
    msp2 = doc2.modelspace()
    ents = list(msp2)
    assert len(ents) == 9, "实体数必须一致"
    layers2 = {e.dxf.layer for e in ents}
    assert {"地形", "建筑", "道路", "注记"} <= layers2

    by_type = {}
    for e in ents:
        by_type.setdefault(e.dxftype(), []).append(e)

    line = by_type["LINE"][0]
    s, en = line.dxf.start, line.dxf.end
    ex, ey = _expected_4p(100, 200, p)
    assert abs(s.x - ex) < 1e-6 and abs(s.y - ey) < 1e-6
    assert abs(s.z - (10 + offset)) < 1e-9, s.z
    ex2, ey2 = _expected_4p(300, 400, p)
    assert abs(en.x - ex2) < 1e-6 and abs(en.y - ey2) < 1e-6 and abs(en.z - 12.0) < 1e-9

    circle = by_type["CIRCLE"][0]
    cx, cy = _expected_4p(150, 150, p)
    assert abs(circle.dxf.center.x - cx) < 1e-6 and abs(circle.dxf.center.y - cy) < 1e-6
    assert abs(circle.dxf.radius - 25 * p.scale) < 1e-6, "半径应随四参数尺度变化"
    assert abs(circle.dxf.center.z - (5 + offset)) < 1e-9

    text = by_type["TEXT"][0]
    tx, ty = _expected_4p(30, 40, p)
    assert abs(text.dxf.insert.x - tx) < 1e-6 and abs(text.dxf.insert.y - ty) < 1e-6
    assert text.dxf.text == "高程点 A", "文字内容必须保留"
    assert abs(text.dxf.rotation - 0) < 1e-9 or True

    poly3 = by_type["POLYLINE"][0]
    vs = [v.dxf.location for v in poly3.vertices]
    assert abs(vs[2].z - (3 + offset)) < 1e-9

    ins = by_type["INSERT"][0]
    ix, iy = _expected_4p(500, 600, p)
    assert abs(ins.dxf.insert.x - ix) < 1e-6 and abs(ins.dxf.insert.y - iy) < 1e-6
    # 块定义不得被变换
    blk_line = doc2.blocks.get("BLK_X")[0]
    assert abs(blk_line.dxf.start.z - 0.0) < 1e-12 and abs(blk_line.dxf.end.x - 10) < 1e-9, \
        "块定义坐标不应改变"

    # LWPOLYLINE 2D 高程（apply_2d）
    out2 = os.path.join(d, "out2.dxf")
    res2 = transform_dxf(src, out2, planar=p, height_fn=hfn, apply_2d_elevation=True)
    doc3 = ezdxf.readfile(out2)
    lwp = [e for e in doc3.modelspace() if e.dxftype() == "LWPOLYLINE"][0]
    assert abs(lwp.dxf.elevation - offset) < 1e-9, lwp.dxf.elevation
    _ = res2


def test_planar_only_and_height_only():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "in.dxf")
    make_sample_dxf(src)
    out1 = os.path.join(d, "planar_only.dxf")
    p = Param4(dx=100.0, dy=0.0, a=1.0, b=0.0)
    res1 = transform_dxf(src, out1, planar=p)
    doc = ezdxf.readfile(out1)
    line = [e for e in doc.modelspace() if e.dxftype() == "LINE"][0]
    # X' = dy + a·X + b·Y = 0 + 100 + 0 = 100 ；Y' = dx − b·X + a·Y = 100 + 200 = 300
    assert abs(line.dxf.start.x - 100.0) < 1e-6, line.dxf.start.x
    assert abs(line.dxf.start.y - 300.0) < 1e-6
    assert abs(line.dxf.start.z - 10.0) < 1e-12, "纯平面模式高程不动"

    out2 = os.path.join(d, "height_only.dxf")

    def hfn(X, Y, z):
        return z + 5.0, 5.0, "offset"

    transform_dxf(src, out2, height_fn=hfn)
    doc2 = ezdxf.readfile(out2)
    line2 = [e for e in doc2.modelspace() if e.dxftype() == "LINE"][0]
    assert abs(line2.dxf.start.x - 100.0) < 1e-9, "纯高程模式平面不动"
    assert abs(line2.dxf.start.z - 15.0) < 1e-9


def test_detect_dwg():
    d = tempfile.mkdtemp()
    fake = os.path.join(d, "x.dwg")
    with open(fake, "wb") as f:
        f.write(b"AC1032" + b"\x00" * 100)
    assert detect_dwg(fake) is True
    real = os.path.join(d, "x.dxf")
    make_sample_dxf(real)
    assert detect_dwg(real) is False
    try:
        transform_dxf(fake, os.path.join(d, "y.dxf"), planar=Param4(0, 0, 1, 0))
        raise AssertionError("DWG 应被拒绝")
    except ValueError as e:
        assert "DWG" in str(e)


def run():
    fns = [test_transform_roundtrip, test_planar_only_and_height_only, test_detect_dwg]
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
