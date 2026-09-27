"""SHP 平面坐标 + 高程转换（pyshp，纯 Python，无 GDAL 依赖）。

坐标系：SHP 内部为 (X=东, Y=北)，与 DXF 相同处理约定。
planar_fn(X_east, Y_north) -> (X2, Y2)；height_fn(X, Y, z) -> (z_new, v, detail)。
输出：几何写 <名>_转换后.shp/.shx，DBF 字段与属性记录原样复制，.prj/.cpg 原样复制
（.prj 描述的是目标坐标系，需用户自行维护）。
实体与属性记录必须逐条对齐：所有实体都写出（未知几何类型写 null shape 占位），
单顶点转换失败时该顶点保持原值并记入 unsupported，不中断。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import shapefile  # pyshp


@dataclass
class ShpResult:
    total: int = 0
    transformed: int = 0
    skipped: int = 0
    out_path: str = ""
    unsupported: dict = field(default_factory=dict)
    log: list = field(default_factory=list)


def _split_parts(points: list, parts: list) -> list[list]:
    """按 parts 起始索引把扁平点列切成段。"""
    if not parts:
        return [points]
    segs = []
    for i, s in enumerate(parts):
        e = parts[i + 1] if i + 1 < len(parts) else len(points)
        segs.append(points[s:e])
    return segs


def transform_shp(in_path, out_path, planar_fn=None, height_fn=None,
                  progress_cb=None, cancel=None) -> ShpResult:
    """转换 SHP。planar_fn/height_fn 至少给一个；返回字段与 CadResult 兼容。"""
    if planar_fn is None and height_fn is None:
        raise ValueError("planar_fn 与 height_fn 至少需要一个")
    res = ShpResult()
    r = shapefile.Reader(str(in_path))
    w = shapefile.Writer(target=str(out_path), shapeType=r.shapeType,
                         encoding=r.encoding or "utf-8")
    try:
        for f in r.fields:
            if f[0] == "DeletionFlag":
                continue
            w.field(f[0], f[1], f[2], f[3])
        res.total = len(r.shapes())
        for i in range(res.total):
            if cancel is not None and cancel.is_set():
                break
            shp = r.shape(i)
            pts = [list(p) for p in shp.points]
            has_z = bool(getattr(shp, "hasZ", False))
            zs = list(shp.z) if has_z else None
            ok = True
            for k, (X, Y) in enumerate(pts):
                try:
                    if planar_fn is not None:
                        X2, Y2 = planar_fn(X, Y)
                        pts[k] = [float(X2), float(Y2)]
                    if height_fn is not None and has_z:
                        z_new, _v, _d = height_fn(X, Y, float(zs[k]))
                        if z_new is not None:
                            zs[k] = float(z_new)
                except Exception as e:  # noqa: BLE001
                    res.unsupported[f"实体{i}顶点{k}"] = str(e)   # 该顶点保持原值
                    ok = False
            segs = _split_parts(pts, list(getattr(shp, "parts", []) or []))
            st = r.shapeType
            if st in (1, 11, 21):                       # point / pointZ / pointM
                if st == 11:
                    w.pointz(pts[0][0], pts[0][1], float(zs[0]) if zs else 0.0)
                else:
                    w.point(pts[0][0], pts[0][1])
            elif st in (8, 18, 28):                     # multipoint(M/Z)
                w.multipoint(pts)
            elif st in (3, 13, 23):                     # polyline(M/Z)
                w.line(segs)
            elif st in (5, 15, 25):                     # polygon(M/Z)
                w.poly(segs)
            else:                                       # 未知类型：null 占位保持记录对齐
                res.unsupported[f"实体{i}"] = f"未支持的 shapeType {st}（写 null 占位）"
                w.null()
                ok = False
            rec = r.record(i)
            w.record(*rec)
            if ok:
                res.transformed += 1
            else:
                res.skipped += 1
            if progress_cb:
                progress_cb(i + 1, res.total, Path(str(in_path)).name)
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass
        r.close()
    src = Path(str(in_path))
    for ext in (".prj", ".cpg"):
        s = src.with_suffix(ext)
        if s.exists():
            shutil.copy2(s, Path(str(out_path)).with_suffix(ext))
    res.out_path = str(out_path)
    res.log.append(f"实体 {res.transformed}/{res.total}，跳过/占位 {res.skipped}")
    return res
