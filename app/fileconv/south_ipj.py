"""南方坐标转换处理软件 V2.0（icoord）方案文件导入。

格式结论（实测）：.ipj 与 CoordSetting.db 均为 SQLite 数据库（样例 projects/*.ipj 实测），
表结构自描述：Ellipsoid(A, E1=1/f)、Projection(CentralMeridian, EastDeclination=y0东加常数m,
NorthDeclination=x0北加常数m, Yardstick/Scale=尺度k)、Parm4(North/East平移, Rotate, Scale)、
Parm7(X/Y/Z平移m, K尺度小数, A/B/R旋转弧度)、IdenticalPoint(公共点 X1..Z2 + Rms)、
Polynomial(2/3维多项式系数)、SchemeStore(编码方案串)。

导入策略（与"算法必须有验证依据"红线一致）：
- 投影+椭球 → 本软件 direct_gk 参数（字段语义已用已知带号样例验证，可直接应用）；
- IdenticalPoint 公共点 → 用本软件已验证的 fit 函数重新解算参数（不采用厂商原始参数约定）；
- Parm4/Parm7/Parm72 → 仅存"参考对照"JSON（south_ref，旋转/尺度约定未经交叉验证，不可应用，
  供跨软件参数级人工比对）。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

from app.common.params import ParamLibrary
from app.core.ellipsoid import all_ellipsoids, geodetic_to_ecef
from app.core.projection import auto_central_meridian, gauss_kruger
from app.core.transform2d import apply_4param, fit_4param
from app.core.transform3d import fit_3param, fit_7param, fit_7param_refined


def read_ipj(path) -> dict:
    """读 .ipj/.db（SQLite）→ {表名: [行dict]}；表不存在则缺省空表。"""
    con = sqlite3.connect(str(path))
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {}
        for t in ("Ellipsoid", "Projection", "Parm4", "Parm7", "Parm72",
                  "Polynomial", "IdenticalPoint"):
            out[t] = []
            if t not in tables:
                continue
            cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{t}")')]
            for row in cur.execute(f'SELECT * FROM "{t}"').fetchall():
                out[t].append(dict(zip(cols, row)))
        return out
    finally:
        con.close()


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _match_ellipsoid(a: float, invf: float):
    """按 (a, 1/f) 归一化距离最近匹配椭球（WGS84 与 CGCS2000 的 1/f 仅差 1.5e-6，
    必须用评分制而非顺序容差）。返回 (名称, 是否近似)。"""
    def _score(kv):
        e = kv[1]
        return abs(e.a - a) / a + abs(e.inv_f - invf) / invf

    nm, e = min(all_ellipsoids().items(), key=_score)
    s = _score((nm, e))
    if s < 1e-6:
        return nm, False
    if s < 1e-4:
        return nm, True
    return "CGCS2000", True


def _rms_planar(f4, src_xy, dst_xy) -> float:
    err = []
    for (x, y), (dx, dy) in zip(src_xy, dst_xy):
        x2, y2 = apply_4param(f4, x, y)
        err.append((x2 - dx) ** 2 + (y2 - dy) ** 2)
    return math.sqrt(sum(err) / len(err))


def _fit_public_points(src, dst, k1, k2):
    """公共点拟合：只用本软件已验证的 fit 函数，返回 (method, doc片段, rms_xy, note)。"""
    cands = []
    if k1 == "planar" and k2 == "planar":
        f4 = fit_4param(src[:, :2], dst[:, :2])
        cands.append(("planar4",
                      {"planar4": {"dx": f4.param.dx, "dy": f4.param.dy,
                                   "a": f4.param.a, "b": f4.param.b}},
                      _rms_planar(f4.param, src[:, :2], dst[:, :2])))
        if len(src) >= 3:
            cx0, cy0 = float(np.mean(src[:, 0])), float(np.mean(src[:, 1]))
            s3 = np.column_stack([src[:, 0] - cx0, src[:, 1] - cy0, src[:, 2]])
            d3 = np.column_stack([dst[:, 0] - cx0, dst[:, 1] - cy0, dst[:, 2]])
            f7 = fit_7param_refined(s3, d3)
            cands.append(("seven2d",
                          {"seven": {"dx": f7.param.dx, "dy": f7.param.dy, "dz": f7.param.dz,
                                     "rx_s": f7.param.rx_s, "ry_s": f7.param.ry_s,
                                     "rz_s": f7.param.rz_s, "scale_ppm": f7.param.scale_ppm,
                                     "center": [cx0, cy0]}},
                          math.sqrt(float(np.mean(f7.res_xyz[:, :2] ** 2)))))
    elif k1 == "lonlat" and k2 == "planar":
        # 链式：自动中央子午线投影 + 四参数（与控制点页 chain74 同口径）
        l0 = auto_central_meridian(float(np.mean(src[:, 0])), 3)
        gx, gy = gauss_kruger(src[:, 1], src[:, 0], l0)
        f4 = fit_4param(np.column_stack([gx, gy]), dst[:, :2])
        cands.append(("chain74",
                      {"projection": {"l0": l0, "x0": 0.0, "y0": 500000.0, "k": 1.0},
                       "planar4": {"dx": f4.param.dx, "dy": f4.param.dy,
                                   "a": f4.param.a, "b": f4.param.b}},
                      _rms_planar(f4.param, np.column_stack([gx, gy]), dst[:, :2])))
    elif k1 == "lonlat" and k2 == "lonlat":
        xyz1 = np.column_stack(geodetic_to_ecef(src[:, 1], src[:, 0], src[:, 2]))
        xyz2 = np.column_stack(geodetic_to_ecef(dst[:, 1], dst[:, 0], dst[:, 2]))
        if len(src) >= 3:
            f7 = fit_7param(xyz1, xyz2)
            cands.append(("seven", {"seven": {"dx": f7.param.dx, "dy": f7.param.dy,
                                              "dz": f7.param.dz, "rx_s": f7.param.rx_s,
                                              "ry_s": f7.param.ry_s, "rz_s": f7.param.rz_s,
                                              "scale_ppm": f7.param.scale_ppm}}, None))
        else:
            f3 = fit_3param(xyz1, xyz2)
            cands.append(("three", {"three": {"dx": f3.param.dx, "dy": f3.param.dy,
                                              "dz": f3.param.dz}}, None))
    if not cands:
        return None
    best = min(cands, key=lambda c: c[2] if c[2] is not None else float("inf"))
    return best


def import_ipj(path, params_dir, save: bool = True) -> dict:
    """导入南方方案。返回 {"imported": [参数名...], "notes": [...], "counts": {...}}。

    save=True 时写入参数库；False 仅解析预览。
    """
    d = read_ipj(path)
    imported, notes, counts = [], [], {}
    stem = Path(path).stem
    now = datetime.now().isoformat(timespec="seconds")
    ells = {int(e["ID"]): e for e in d["Ellipsoid"] if e.get("ID") is not None}

    # ---- 投影+椭球 → direct_gk（可应用） ----
    n_proj = 0
    for p in d["Projection"]:
        l0 = _num(p.get("CentralMeridian"))
        y0 = _num(p.get("EastDeclination"), 500000.0)
        x0 = _num(p.get("NorthDeclination"), 0.0)
        k = _num(p.get("Yardstick")) or _num(p.get("Scale")) or 1.0
        if l0 is None:
            continue
        ell_name, approx = "CGCS2000", True
        ell_row = ells.get(int(p["EsID"]) if p.get("EsID") is not None else -1)
        if ell_row:
            a = _num(ell_row.get("A"))
            invf = _num(ell_row.get("E1"))
            if a and invf:
                ell_name, approx = _match_ellipsoid(a, invf)
        name = f"{stem}_{p.get('Name') or f'L0{l0:g}'}"
        doc = {"schema": "coordparam/1", "name": name, "kind": "direct_gk",
               "source_kind": "lonlat", "target_kind": "planar", "ellipsoid": ell_name,
               "use_geoid": False, "angle_format": "d",
               "projection": {"l0": l0, "x0": x0, "y0": y0, "k": k},
               "planar4": None, "seven": None, "heightfit": None,
               "accuracy": {"note": f"导入自南方方案 {Path(path).name}"
                                    + ("（椭球按 A、1/f 最近匹配）" if approx else "")},
               "created": now}
        if save:
            ParamLibrary(params_dir).save(doc)
        imported.append(name)
        n_proj += 1
    counts["投影参数"] = n_proj

    # ---- IdenticalPoint 公共点 → 本软件算法重新拟合（可应用） ----
    pts = [r for r in d["IdenticalPoint"]
           if all(_num(r.get(c)) is not None for c in ("X1", "Y1", "Z1", "X2", "Y2", "Z2"))]
    counts["公共点"] = len(pts)
    if len(pts) >= 2:
        try:
            from app.core import autoselect
            src = np.array([[_num(r["X1"]), _num(r["Y1"]), _num(r["Z1"], 0.0)] for r in pts])
            dst = np.array([[_num(r["X2"]), _num(r["Y2"]), _num(r["Z2"], 0.0)] for r in pts])
            k1 = autoselect.detect_coord_kind(src[:, :2])
            k2 = autoselect.detect_coord_kind(dst[:, :2])
            fit = _fit_public_points(src, dst, k1, k2)
            if fit is None:
                notes.append(f"公共点坐标类型组合不支持拟合（{k1}→{k2}）")
            else:
                method, pieces, rms_xy = fit
                name = f"{stem}_公共点拟合"
                doc = {"schema": "coordparam/1", "name": name, "kind": method,
                       "source_kind": k1, "target_kind": k2, "ellipsoid": "CGCS2000",
                       "use_geoid": False, "angle_format": "d",
                       "projection": None, "planar4": None, "seven": None,
                       "heightfit": None, **pieces,
                       "accuracy": {"rms_xy_m": rms_xy, "n_points": len(pts),
                                    "note": f"南方方案公共点({len(pts)}个)由本软件已验证算法"
                                            f"重新拟合，方法 {method}"},
                       "created": now}
                if save:
                    ParamLibrary(params_dir).save(doc)
                imported.append(name)
        except Exception as e:  # noqa: BLE001
            notes.append(f"公共点拟合失败: {e}")

    # ---- Parm4/Parm7/Parm72 → 参考对照（不可应用） ----
    for table in ("Parm4", "Parm7", "Parm72"):
        rows_t = [r for r in d[table] if r]
        if not rows_t:
            continue
        name = f"{stem}_{table}_参考"
        doc = {"schema": "southref/1", "name": name, "south_table": table,
               "rows": rows_t, "created": now,
               "note": "南方软件原始参数（Parm7：平移 m、尺度小数、旋转弧度），单位与旋转约定"
                       "未经交叉验证，仅供跨软件参数级人工比对，不能在本软件中直接应用"}
        if save:
            ParamLibrary(params_dir).save(doc)
        imported.append(name)
        counts[f"{table}(参考)"] = len(rows_t)

    return {"imported": imported, "notes": notes, "counts": counts}
