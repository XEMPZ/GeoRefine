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
                res.skipped = res.total - res.transformed
                try:
                    w.close()
                    for ext in (".shp", ".shx", ".dbf", ".cpg"):
                        Path(out_path).with_suffix(ext).unlink(missing_ok=True)
                except Exception:
                    pass
                return res
            shp = r.shape(i)
            pts = [list(p) for p in shp.points]
            st = r.shapeType
            has_z = (st in (11, 13, 15, 18, 31)) or (hasattr(shp, "z") and bool(getattr(shp, "z", None)))
            has_m = (st in (21, 23, 25, 28)) or (hasattr(shp, "m") and bool(getattr(shp, "m", None)) and any(m is not None for m in getattr(shp, "m", [])))
            zs = list(shp.z) if (hasattr(shp, "z") and shp.z) else ([0.0] * len(pts) if has_z else None)
            ms = list(shp.m) if (hasattr(shp, "m") and shp.m) else None
            ok = True
            for k, (X, Y) in enumerate(pts):
                try:
                    if height_fn is not None and has_z and zs is not None:
                        z_new, _v, _d = height_fn(X, Y, float(zs[k]))
                        if z_new is not None:
                            zs[k] = float(z_new)
                    if planar_fn is not None:
                        X2, Y2 = planar_fn(X, Y)
                        pts[k] = [float(X2), float(Y2)]
                except Exception as e:  # noqa: BLE001
                    res.unsupported[f"实体{i}顶点{k}"] = str(e)   # 该顶点保持原值
                    ok = False
            parts = list(getattr(shp, "parts", []) or [])
            # 组织 2D / 3D(Z) / M 顶点数据
            if has_z and zs is not None:
                if has_m and ms is not None:
                    pts_out = [[p[0], p[1], float(zs[k]), (float(ms[k]) if ms[k] is not None else 0.0)] for k, p in enumerate(pts)]
                else:
                    pts_out = [[p[0], p[1], float(zs[k])] for k, p in enumerate(pts)]
            elif has_m and ms is not None:
                pts_out = [[p[0], p[1], (float(ms[k]) if ms[k] is not None else 0.0)] for k, p in enumerate(pts)]
            else:
                pts_out = pts
            segs_out = _split_parts(pts_out, parts)

            if st in (1, 11, 21):                       # Point / PointZ / PointM
                if st == 11:
                    w.pointz(pts[0][0], pts[0][1], float(zs[0]) if zs else 0.0,
                             m=(float(ms[0]) if ms and ms[0] is not None else None))
                elif st == 21:
                    w.pointm(pts[0][0], pts[0][1], m=(float(ms[0]) if ms and ms[0] is not None else 0.0))
                else:
                    w.point(pts[0][0], pts[0][1])
            elif st in (8, 18, 28):                     # MultiPoint / MultiPointZ / MultiPointM
                if st == 18:
                    w.multipointz(pts_out)
                elif st == 28:
                    w.multipointm(pts_out)
                else:
                    w.multipoint(pts)
            elif st in (3, 13, 23):                     # PolyLine / PolyLineZ / PolyLineM
                if st == 13:
                    w.linez(segs_out)
                elif st == 23:
                    w.linem(segs_out)
                else:
                    w.line(segs_out)
            elif st in (5, 15, 25):                     # Polygon / PolygonZ / PolygonM
                if st == 15:
                    w.polyz(segs_out)
                elif st == 25:
                    w.polym(segs_out)
                else:
                    w.poly(segs_out)
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
