"""DXF 平面坐标 + 高程转换。

坐标系：DXF 内部为 (X=东, Y=北)，与测量 (x=北, y=东) 相反。
四参数矩阵（ezdxf Matrix44 行主序，平移在第 4 列）已按换算推导：
  X2 = dy + b·Y_cad − a·X_cad  → 行1 = [-a, b, 0, dy]
  Y2 = dx + a·Y_cad − b·X_cad  → 行2 = [-b, a, 0, dx]
INSERT 整体 transform（子实体自动跟随，不改块定义，防双重变换）。
高程：height_fn(X_cad, Y_cad, z) -> (z_new, v, detail)，对带 Z 的点位重写。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44

from app.core.transform2d import Param4


@dataclass
class CadResult:
    total: int = 0
    transformed: int = 0
    skipped: int = 0
    by_type: dict = field(default_factory=dict)
    unsupported: dict = field(default_factory=dict)
    out_path: str = ""
    log: list = field(default_factory=list)


def detect_dwg(path) -> bool:
    """按二进制魔数检测 DWG（'AC10xx' 等版本头）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(6)
        return head.startswith(b"AC")
    except Exception:
        return False


def _build_matrix(p: Param4) -> Matrix44:
    """ezdxf Matrix44 为行向量右乘（v·M），平移在第 4 行。
    测量系四参数 (x北,y东) 换到 CAD 系 (X东,Y北)：
    X' = dy + a·X + b·Y ; Y' = dx − b·X + a·Y （det = a²+b² > 0，纯旋转+缩放无镜像）
    扁平行主序: 行1(a,−b,0,0) 行2(b,a,0,0) 行3(0,0,1,0) 行4(dy,dx,0,1)"""
    return Matrix44([p.a, -p.b, 0.0, 0.0,
                     p.b, p.a, 0.0, 0.0,
                     0.0, 0.0, 1.0, 0.0,
                     p.dy, p.dx, 0.0, 1.0])
    return Matrix44([-p.a, -p.b, 0.0, 0.0,
                     p.b, p.a, 0.0, 0.0,
                     0.0, 0.0, 1.0, 0.0,
                     p.dy, p.dx, 0.0, 1.0])


def _apply_height_to_z(height_fn, X, Y, z):
    """对单个 z 值应用高程转换；返回 (z_new, detail) 或 (None, detail)。"""
    z_new, _v, detail = height_fn(X, Y, z)
    return z_new, detail


def transform_dxf(in_path, out_path, planar: Param4 | None = None, height_fn=None,
                  apply_2d_elevation: bool = False, progress_cb=None, cancel=None) -> CadResult:
    """转换 DXF。planar 与 height_fn 至少给一个；两者都给则一并执行。"""
    in_path, out_path = str(in_path), str(out_path)
    if detect_dwg(in_path):
        raise ValueError("检测到 DWG 二进制格式，请先在 CAD 中另存为 DXF（或用 ODA File Converter 转换）")
    if planar is None and height_fn is None:
        raise ValueError("planar 与 height_fn 至少需要一个")
    try:
        doc = ezdxf.readfile(in_path)
    except Exception as e:
        raise ValueError(f"DXF 读取失败: {e}") from e

    msp = doc.modelspace()
    result = CadResult()
    M = _build_matrix(planar) if planar is not None else None
    entities = list(msp)
    result.total = len(entities)
    before_counts: dict = {}
    after_counts: dict = {}

    def _bump(d, key):
        d[key] = d.get(key, 0) + 1

    for idx, e in enumerate(entities):
        if cancel is not None and cancel.is_set():
            result.skipped += 1
            continue
        dxftype = e.dxftype()
        _bump(before_counts, dxftype)
        try:
            if M is not None and dxftype == "INSERT":
                _transform_insert(e, planar, height_fn)   # INSERT 拒绝非严格正交矩阵，走专用分支
            elif M is not None and dxftype == "CIRCLE":
                _transform_circle_like(e, planar, height_fn, is_arc=False)  # OCS 实体走专用分支，避免镜像路径
            elif M is not None and dxftype == "ARC":
                _transform_circle_like(e, planar, height_fn, is_arc=True)
            elif M is not None:
                e.transform(M)
            # 高程处理
            if height_fn is not None and dxftype not in ("INSERT", "CIRCLE", "ARC"):
                _apply_height(e, dxftype, height_fn, apply_2d_elevation)
            _bump(after_counts, dxftype)
            result.transformed += 1
        except Exception as ex:  # noqa: BLE001
            _bump(result.unsupported, dxftype)
            result.log.append(f"{dxftype}#{e.dxf.get('handle', '?')}: {ex}")
        if progress_cb is not None:
            progress_cb(idx + 1, result.total, dxftype)
    result.by_type = after_counts
    # 实体数一致性校验
    if sum(after_counts.values()) != result.total - result.skipped:
        result.log.append("警告：转换后实体数与预期不一致")
    doc.saveas(out_path)
    result.out_path = out_path
    return result


def _transform_insert(e, p: Param4, height_fn):
    """INSERT 专用变换（避免 ezdxf 对块引用的矩阵限制）：
    X' = dy + a·X + b·Y ; Y' = dx − b·X + a·Y ; 旋转角 −α；缩放 ×scale；高程作用在插入点 z。"""
    import math
    ins = e.dxf.insert
    X, Y, z = ins.x, ins.y, ins.z
    X2 = p.dy + p.a * X + p.b * Y
    Y2 = p.dx - p.b * X + p.a * Y
    z2 = z
    if height_fn is not None:
        zn, _v, _d = height_fn(X2, Y2, z)
        if zn is not None:
            z2 = zn
    e.dxf.insert = (X2, Y2, z2)
    rot_deg = math.degrees(p.rot_rad)
    try:
        e.dxf.xscale = e.dxf.xscale * p.scale
        e.dxf.yscale = e.dxf.yscale * p.scale
        e.dxf.zscale = e.dxf.zscale * p.scale
    except Exception:  # noqa: BLE001
        pass
    e.dxf.rotation = (e.dxf.rotation or 0.0) - rot_deg


def _transform_circle_like(e, p: Param4, height_fn, is_arc: bool):
    """CIRCLE/ARC 专用：圆心公式换算 + 半径×scale + 高程；ARC 角度 −α（保向，不交换起止）。"""
    import math
    c = e.dxf.center
    X2 = p.dy + p.a * c.x + p.b * c.y
    Y2 = p.dx - p.b * c.x + p.a * c.y
    z2 = c.z
    if height_fn is not None:
        zn, _v, _d = height_fn(X2, Y2, c.z)
        if zn is not None:
            z2 = zn
    e.dxf.center = (X2, Y2, z2)
    e.dxf.radius = e.dxf.radius * p.scale
    if is_arc:
        shift = -math.degrees(p.rot_rad)
        e.dxf.start_angle = (e.dxf.start_angle + shift) % 360.0
        e.dxf.end_angle = (e.dxf.end_angle + shift) % 360.0


def _apply_height(e, dxftype: str, height_fn, apply_2d: bool):
    """按实体类型重写带 Z 的点位。z_new 为 None 时跳过该点。"""

    def hz(X, Y, z):
        z_new, _v, _d = height_fn(X, Y, z)
        return z_new

    if dxftype == "LINE":
        for key in ("start", "end"):
            p = e.dxf.get(key)
            z = hz(p.x, p.y, p.z)
            if z is not None:
                p = p.replace(z=z)
                e.dxf.set(key, p)
    elif dxftype == "POINT":
        p = e.dxf.get("location")
        z = hz(p.x, p.y, p.z)
        if z is not None:
            e.dxf.location = p.replace(z=z)
    elif dxftype in ("CIRCLE", "ARC"):
        c = e.dxf.get("center")
        if c.z != 0.0 or True:
            z = hz(c.x, c.y, c.z)
            if z is not None:
                e.dxf.center = c.replace(z=z)
    elif dxftype in ("TEXT", "MTEXT"):
        p_ins = e.dxf.get("insert")
        z = hz(p_ins.x, p_ins.y, p_ins.z)
        if z is not None:
            e.dxf.insert = p_ins.replace(z=z)
    elif dxftype == "3DPOLY" or (dxftype == "POLYLINE" and e.get_mode() == "AcDb3dPolyline"):
        for v in e.vertices:
            loc = v.dxf.get("location")
            z = hz(loc.x, loc.y, loc.z)
            if z is not None:
                v.dxf.location = loc.replace(z=z)
    elif dxftype == "SPLINE":
        ctrl = list(e.control_points)
        e.control_points = [(p.x, p.y, hz(p.x, p.y, p.z) if hz(p.x, p.y, p.z) is not None else p.z)
                            for p in ctrl]
        if e.fit_points:
            fit = list(e.fit_points)
            e.fit_points = [(p.x, p.y, hz(p.x, p.y, p.z) if hz(p.x, p.y, p.z) is not None else p.z)
                            for p in fit]
    elif dxftype == "ELLIPSE":
        c = e.dxf.get("center")
        z = hz(c.x, c.y, c.z)
        if z is not None:
            e.dxf.center = c.replace(z=z)
    elif dxftype in ("3DFACE", "SOLID", "TRACE"):
        for key in ("vtx0", "vtx1", "vtx2", "vtx3"):
            if e.dxf.hasattr(key):
                p = e.dxf.get(key)
                z = hz(p.x, p.y, p.z)
                if z is not None:
                    e.dxf.set(key, p.replace(z=z))
    elif dxftype == "LWPOLYLINE":
        if apply_2d:
            pts = e.get_points()
            if pts:
                x0, y0 = pts[0][0], pts[0][1]
                z = hz(x0, y0, e.dxf.get("elevation", 0.0))
                if z is not None:
                    e.dxf.elevation = z
    elif dxftype == "HATCH":
        pass  # 2D 填充无独立高程
    # 其余类型（DIMENSION/LEADER 等）几何由 defpoint 决定，保守不处理高程
