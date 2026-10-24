"""DXF 高程转换覆盖：仅高程模式下 LINE/CIRCLE/ARC/INSERT 都要改 z，且平面+高程各作用一次。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import ezdxf  # noqa: E402

from app.cad.dxf_transform import transform_dxf  # noqa: E402
from app.core.transform2d import Param4  # noqa: E402


def _sample(d: Path) -> str:
    src = str(d / "in.dxf")
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0, 0), (10, 0, 0))
    msp.add_circle((5, 5, 0), 2)
    msp.add_arc((5, -5, 0), 2, 0, 90)
    blk = doc.blocks.new("B1")
    blk.add_line((0, 0, 0), (1, 1, 0))
    msp.add_blockref("B1", (3, 3, 0))
    doc.saveas(src)
    return src


def _z_by_type(path: str) -> dict:
    out = {}
    for e in ezdxf.readfile(path).modelspace():
        t = e.dxftype()
        if t == "LINE":
            out.setdefault("LINE", []).append((e.dxf.start.z, e.dxf.end.z))
        elif t in ("CIRCLE", "ARC"):
            out[t] = e.dxf.center.z
        elif t == "INSERT":
            out["INSERT"] = (e.dxf.insert.x, e.dxf.insert.y, e.dxf.insert.z)
    return out


def run() -> int:
    def h10(X, Y, z):
        return z + 10.0, 10.0, "offset+10"

    checks = 0
    d = Path(tempfile.mkdtemp())
    src = _sample(d)

    # 1) 仅高程：四类实体 z 都要 +10
    out1 = str(d / "out1.dxf")
    transform_dxf(src, out1, planar=None, height_fn=h10)
    z1 = _z_by_type(out1)
    assert z1["LINE"] == [(10.0, 10.0)], z1["LINE"]
    assert z1["CIRCLE"] == 10.0, z1["CIRCLE"]
    assert z1["ARC"] == 10.0, z1["ARC"]
    assert z1["INSERT"][2] == 10.0, z1["INSERT"]
    checks += 1

    # 2) 平面+高程：XY 只变换一次、z 只加一次
    p = Param4(dx=100.0, dy=200.0, a=1.0, b=0.0)     # dx=北向平移, dy=东向平移
    out2 = str(d / "out2.dxf")
    transform_dxf(src, out2, planar=p, height_fn=h10)
    z2 = _z_by_type(out2)
    assert z2["CIRCLE"] == 10.0, z2["CIRCLE"]
    assert z2["INSERT"] == (203.0, 103.0, 10.0), z2["INSERT"]   # X=dy+x, Y=dx+y
    checks += 1

    # 3) 只有平面、没有高程时不动 z
    out3 = str(d / "out3.dxf")
    transform_dxf(src, out3, planar=p, height_fn=None)
    z3 = _z_by_type(out3)
    assert z3["CIRCLE"] == 0.0 and z3["INSERT"][2] == 0.0, (z3["CIRCLE"], z3["INSERT"])
    checks += 1

    return checks
