"""文件转换页的任务构建与回调派生。纯逻辑，无 Qt 依赖。"""
from __future__ import annotations

from pathlib import Path

PENDING_EXTS = ("wt", "wl", "wp", "wn", "mpj", "edb")
PENDING_MSG = ("WT/WL/WP/WN/MPJ/EDB：经核实，同类 icoord 文件转换亦只支持"
               "矢量(mdb/dwg/shp)/文本/GDB（其帮助 §2.4.2）；MapGIS 解析器藏在其 VirBox 加壳核"
               "ientity.dll 内（引擎枚举有 COOR_MAPGIS 分支但无法提取），且本机无样例文件，"
               "按“算法必须有验证依据”红线暂不开放解析。\n"
               "请提供任一格式样例文件，我们按其真实结构实现解析器；"
               "或先在南方软件/CASS 中转出 SHP/DXF/MDB。")


def pending_files(files) -> list[str]:
    return [f for f in files if Path(f).suffix.lower().lstrip(".") in PENDING_EXTS]


def build_planar(planar_data: dict | None):
    """按所选参数构造平面变换：返回 (Param4, 错误消息)。

    planar4 走原样；seven2d 换算为严格等价四参数（矩阵/回调共用）。
    """
    if not planar_data:
        return None, None
    from app.core.transform2d import Param4
    if planar_data.get("kind") == "seven2d" and planar_data.get("seven"):
        from app.common.params import seven2d_to_param4
        try:
            return seven2d_to_param4(planar_data), None
        except Exception as e:  # noqa: BLE001
            return None, f"二维七参数换算失败：{e}"
    if planar_data.get("planar4"):
        p4 = planar_data["planar4"]
        return Param4(p4["dx"], p4["dy"], p4["a"], p4["b"]), None
    return None, None


def planar_fns(planar_p4):
    """由 Param4 派生两个回调：测量系 (x北,y东) 与 CAD/SHP 系 (X东,Y北)。"""
    if planar_p4 is None:
        return None, None
    from app.core.transform2d import apply_4param
    survey = lambda x, y: apply_4param(planar_p4, x, y)   # noqa: E731
    cad = lambda X, Y: apply_4param(planar_p4, Y, X)      # noqa: E731
    return survey, cad


def height_fn_points_adapter(hfn):
    """文本点文件回调为测量系 (x北,y东,z)，高程回调是 CAD 系 (X东,Y北,z)——适配。"""
    return (lambda x, y, z: hfn(y, x, z)) if hfn is not None else None


def build_jobs(files, out_dir: str, planar_data: dict | None, hdata: dict,
               apply_2d: bool) -> tuple[list[dict], str | None]:
    """按扩展名派发任务。返回 (jobs, 错误消息)；jobs 供后台工作线程执行。

    planar_data：平面参数文档——含 planar4/seven2d 为平面→平面；仅含 projection 为
    大地→平面（仅支持文本点文件，列序 点名,经度,纬度,h）。
    hdata：高程选择 {"mode": none/offset/param, "offset":…, "param":…}。
    """
    from app.common import hconv
    from app.common.params import ParamApplier

    lonlat_mode = bool(planar_data and planar_data.get("projection")
                       and not planar_data.get("planar4")
                       and planar_data.get("kind") != "seven2d")
    planar_p4, err = (None, None) if lonlat_mode else build_planar(planar_data)
    if err:
        return [], err
    planar_fn_survey, planar_fn_cad = planar_fns(planar_p4)

    mode = (hdata or {}).get("mode", "none")
    if mode == "offset":
        hfn = hconv.make_cad_height_fn("offset", offset=float(hdata.get("offset", 0.0)))
    elif mode == "param":
        hfn = hconv.make_cad_height_fn("param", param=hdata.get("param"))
    else:
        hfn = None

    jobs = []
    for f in files:
        ext = Path(f).suffix.lower().lstrip(".")
        if ext in ("dxf", "shp", "dwg", "mdb") and lonlat_mode:
            return [], ("所选平面参数为“大地→平面”，仅支持文本点文件"
                        "（点名,经度,纬度,h；.dat 为 CASS 平面格式不适用）")
        if ext == "dxf":
            out = str(Path(out_dir) / (Path(f).stem + "_转换后.dxf"))
            jobs.append({"kind": "dxf", "in": f, "out": out, "planar": planar_p4,
                         "height_fn": hfn, "apply_2d": apply_2d})
        elif ext == "shp":
            out = str(Path(out_dir) / (Path(f).stem + "_转换后.shp"))
            jobs.append({"kind": "shp", "in": f, "out": out,
                         "planar_fn": planar_fn_cad, "height_fn": hfn})
        elif ext == "dwg":
            if planar_p4 is None and hfn is None:
                return [], "DWG 至少需要平面或高程转换方式"
            out = str(Path(out_dir) / (Path(f).stem + "_转换后.dxf"))
            jobs.append({"kind": "dwg", "in": f, "out": out, "planar": planar_p4,
                         "height_fn": hfn, "apply_2d": apply_2d})
        elif ext == "mdb":
            jobs.append({"kind": "mdb", "in": f, "out": str(Path(out_dir)),
                         "planar_fn": planar_fn_cad, "height_fn": hfn})
        else:   # dat/txt/csv/xlsx 点文本
            out = str(Path(out_dir) / (Path(f).stem + "_转换后." + (ext or "txt")))
            if lonlat_mode:
                ap = ParamApplier(planar_data)
                pfn = lambda lon, lat: ap.apply_lonlat(lat, lon, 0.0,
                                                       do_plane=True, do_height=False)[:2]  # noqa: E731
                hpts = (hconv.make_photo_height_fn(mode, param=hdata.get("param"),
                                                   offset=float(hdata.get("offset", 0.0)))
                        if mode != "none" else None)
            else:
                pfn = planar_fn_survey
                hpts = height_fn_points_adapter(hfn)
            jobs.append({"kind": "points", "in": f, "out": out,
                         "planar_fn": pfn, "height_fn": hpts,
                         "input_lonlat": lonlat_mode})
    return jobs, None
