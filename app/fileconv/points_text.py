"""文本点文件坐标转换。

- CASS .dat：`点名,编码,y东,x北,h`（南方 CASS 标准坐标文件，可无编码/高程）
- 通用 txt/csv：`点名,x北,y东[,h,...]`（与全软件导入口径一致，分隔符/表头自动识别）
- xlsx：首行表头可自动跳过，列同 txt

回调：fn_survey(x_north, y_east, z=None) -> (x2, y2)；height_fn(x, y, z) -> (z_new, v, detail)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class PointsResult:
    total: int = 0
    transformed: int = 0
    skipped: int = 0
    out_path: str = ""
    unsupported: dict = field(default_factory=dict)
    log: list = field(default_factory=list)


def _decode(path) -> str:
    """编码自适应读文本（utf-8-sig → gb18030，与 pair_io 同口径）。"""
    from app.common.pair_io import _decode as _pair_decode
    return _pair_decode(str(path))


def read_cass_dat(path) -> list[dict]:
    """读 CASS .dat → [{name, code, y, x, h}]；h 缺省为 None。"""
    rows = []
    for ln, line in enumerate(_decode(path).splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        parts = [p.strip() for p in s.split(",")]
        if len(parts) < 4:
            raise ValueError(f"第{ln}行不是 CASS 坐标格式（点名,编码,y东,x北[,h]）：{line[:60]}")
        rows.append({"name": parts[0], "code": parts[1],
                     "y": float(parts[2]), "x": float(parts[3]),
                     "h": float(parts[4]) if len(parts) >= 5 and parts[4] else None})
    return rows


def write_cass_dat(path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        for r in rows:
            h = f"{r['h']:.4f}" if r.get("h") is not None else ""
            f.write(f"{r['name']},{r.get('code', '')},{r['y']:.4f},{r['x']:.4f},{h}\n".rstrip(",\n")
                    + "\n")


def transform_cass_dat(in_path, out_path, planar_fn=None, height_fn=None,
                       progress_cb=None, cancel=None) -> PointsResult:
    """转换 CASS .dat。planar_fn(x_north, y_east) -> (x2, y2)（测量系）。"""
    res = PointsResult()
    rows = read_cass_dat(in_path)
    res.total = len(rows)
    for r in rows:
        if cancel is not None and cancel.is_set():
            break
        try:
            x2, y2 = float(r["x"]), float(r["y"])
            if planar_fn is not None:
                x2, y2 = planar_fn(float(r["x"]), float(r["y"]))
            r["x"], r["y"] = float(x2), float(y2)
            if height_fn is not None and r["h"] is not None:
                z_new, _v, _d = height_fn(float(r["x"]), float(r["y"]), float(r["h"]))
                if z_new is not None:
                    r["h"] = float(z_new)
            res.transformed += 1
        except Exception as e:  # noqa: BLE001
            res.unsupported[r.get("name") or f"第{res.transformed + res.skipped + 1}行"] = str(e)
            res.skipped += 1
        if progress_cb:
            progress_cb(res.transformed + res.skipped, res.total, Path(str(in_path)).name)
    write_cass_dat(out_path, rows)
    res.out_path = str(out_path)
    return res


def transform_generic_points(in_path, out_path, planar_fn=None, height_fn=None,
                             progress_cb=None, cancel=None,
                             input_lonlat: bool | None = None) -> PointsResult:
    """转换通用 txt/csv/xlsx 点文件。

    列序约定：input_lonlat=False（默认）为 点名,x北,y东[,h]；True 为 点名,经度,纬度[,h]
    （所选平面参数为大地→平面时使用，planar_fn 第一/二参 = 经度/纬度）。
    input_lonlat=None 时按文件实际内容自动识别（两列均 ≤360 判为经纬度）。
    输出：csv/txt → utf-8-sig CSV；xlsx → xlsx。
    """
    from app.common.io_import import read_points_table
    from app.core.autoselect import detect_coord_kind

    res = PointsResult()
    rows, notes = read_points_table(in_path, coord_cols=2, keep_extra=True)
    res.total = len(rows)
    if rows:
        arr = np.array([[float(v[0]), float(v[1])] for _n, v in rows])
        kind = detect_coord_kind(arr)
        if input_lonlat is not None and (kind == "lonlat") != input_lonlat:
            raise ValueError(
                ("文件坐标识别为经纬度，但所选平面参数为平面→平面；"
                 if kind == "lonlat" else
                 "文件坐标识别为平面坐标，但所选平面参数为大地→平面；")
                + "请核对参数或数据。")
        input_lonlat = (kind == "lonlat")
    if input_lonlat and planar_fn is None:
        raise ValueError("文件为经纬度坐标，但未选择“大地→平面”参数，无法转换平面")
    out_rows = []
    for i, (name, vals) in enumerate(rows):
        if cancel is not None and cancel.is_set():
            break
        x, y = float(vals[0]), float(vals[1])   # input_lonlat 时 = (经度, 纬度)
        h = float(vals[2]) if len(vals) >= 3 else None
        try:
            x2, y2 = planar_fn(x, y) if planar_fn is not None else (x, y)
            h2 = h
            if height_fn is not None and h is not None:
                z_new, _v, _d = height_fn(x, y, h)
                h2 = float(z_new) if z_new is not None else None
            out_rows.append([name] + [f"{float(x2):.4f}", f"{float(y2):.4f}"]
                            + ([f"{h2:.4f}"] if h2 is not None else [])
                            + [str(v) for v in vals[3:]])
            res.transformed += 1
        except Exception as e:  # noqa: BLE001
            res.unsupported[name or f"P{i+1}"] = str(e)
            res.skipped += 1
        if progress_cb:
            progress_cb(res.transformed + res.skipped, res.total, Path(str(in_path)).name)
    ext = Path(out_path).suffix.lower()
    if ext == ".xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "转换结果"
        for r in out_rows:
            ws.append(r)
        wb.save(out_path)
    else:
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            for r in out_rows:
                f.write(",".join(r) + "\n")
    if notes:
        res.log.append("；".join(notes))
    res.out_path = str(out_path)
    return res


def transform_points_text(in_path, out_path, planar_fn=None, height_fn=None,
                          progress_cb=None, cancel=None,
                          input_lonlat: bool | None = None) -> PointsResult:
    """按扩展名分流：.dat → CASS 格式（平面）；其余 → 通用点文本。"""
    if Path(str(in_path)).suffix.lower() == ".dat":
        if input_lonlat:
            raise ValueError(".dat 为 CASS 平面格式（y东,x北），不适用于大地→平面参数；"
                             "请改用 txt/csv（点名,经度,纬度,h）")
        return transform_cass_dat(in_path, out_path, planar_fn, height_fn,
                                  progress_cb=progress_cb, cancel=cancel)
    return transform_generic_points(in_path, out_path, planar_fn, height_fn,
                                    progress_cb=progress_cb, cancel=cancel,
                                    input_lonlat=input_lonlat)
