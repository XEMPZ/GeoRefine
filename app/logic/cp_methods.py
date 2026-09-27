"""控制点转换：方法策略与计算（勾选过滤 / 自动勾选 / 选优 / 参数重算 / 文档组装 / 残差映射）。

纯逻辑模块（无 Qt 依赖）。GUI 页面只负责从表格取数、把结果回填到控件；
所有"勾选代表什么、哪个方法可行、参数怎么算、残差怎么映射"的规则都在这里。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PLANE_METHODS = ("planar4", "planar3", "seven", "seven2d", "chain74",
                 "planar4deg", "chain73", "inv_chain", "inv_chain3")
BASELINE_PLANE = "direct_gk"          # 基线：同基准仅投影（恒显示）
BASELINE_HEIGHT = "heightfit:const"   # 基线：固定差（恒显示）

# 方法中文显示名（比选表/详情面板）；内部代号仅在参数库与参数文件中出现
METHOD_LABELS = {
    "planar4": "平面四参数",
    "planar3": "平面三参数（仅平移）",
    "seven2d": "二维七参数",
    "seven": "空间七参数",
    "three": "空间三参数",
    "chain74": "七参数+投影+四参数",
    "chain73": "投影+三参数",
    "planar4deg": "四参数（经纬度直接拟合）",
    "direct_gk": "直接投影（同基准基线）",
    "inv_direct": "直接反算（同基准基线）",
    "inv_chain3": "投影反算+三参数",
    "inv_chain": "投影反算+七参数",
    "heightfit:const": "固定差",
    "heightfit:plane": "平面拟合（斜面）",
    "heightfit:quadratic": "曲面拟合（二次曲面）",
}


def method_label(m: str) -> str:
    """比选表方法列的中文显示名（格网行带模型名）。"""
    if m.startswith("格网:"):
        return f"大地水准面格网：{m.split(':', 1)[1]}"
    return METHOD_LABELS.get(m, m)


@dataclass
class CpChecks:
    """控制点页的勾选状态（GUI 从控件读取后传入）。"""
    planar4: bool = False
    planar3: bool = False
    seven: bool = False
    fit_plane: bool = False
    fit_quad: bool = False
    geoid_model: bool = False
    precise_xy: bool = False
    precise_h: bool = False
    use_proj: bool = True     # ①使用投影参数：先投影再四参数（推荐）；不勾=直接以经纬度按平面坐标拟合


def visible(row: dict, c: CpChecks) -> bool:
    """勾选过滤：未勾选的方法不参与比选与显示（基线行保留）。"""
    m = row["method"]
    if m == "planar4":          # 平面→平面 四参数
        return c.planar4 and not c.precise_xy
    if m == "planar3":          # 平面→平面 三参数
        return c.planar3 or c.precise_xy
    if m == "planar4deg":       # 大地→平面：源经纬度按平面坐标直接拟合四参数（两组平面坐标转换，未过投影）
        return c.planar4 and not c.use_proj and not c.precise_xy
    if m == "chain74":          # 大地→平面：先投影再四参数（推荐）
        return c.planar4 and c.use_proj and not c.precise_xy
    if m == "chain73":          # 大地→平面：先投影再三参数（高精度坐标用）
        return (c.planar3 or c.precise_xy) and c.use_proj
    if m == "inv_chain":        # 平面→大地（逆向）：反算+七参数
        return c.seven and not c.precise_xy
    if m == "inv_chain3":       # 平面→大地（逆向）：反算+三参数
        return c.planar3 or c.precise_xy
    if m in ("seven", "seven2d"):
        return c.seven and not c.precise_xy
    if m == BASELINE_PLANE or m == "inv_direct":
        # 基线（同基准仅投影/仅反算）：仅在勾选了任一平面方法时作参照；
        # 全不勾 = 用户明确不要平面转换（可保存纯高程/格网参数）
        return c.planar4 or c.planar3 or c.seven
    if m.startswith("heightfit:"):
        mode = m.split(":", 1)[1]
        if mode == "const":
            return c.fit_plane or c.fit_quad or c.geoid_model or c.precise_h
        if c.precise_h:
            return False                  # 高精度高程：趋势拟合全部退出
        if mode == "plane":
            return c.fit_plane
        if mode == "quadratic":
            return c.fit_quad
        return True
    if m.startswith("格网:"):
        if not c.geoid_model:
            return False
        return not (c.precise_h and "+趋势" in m)   # 高精度：格网只做直接插值
    return True


def visible_rows(rows: list[dict], c: CpChecks) -> list[dict]:
    return [r for r in rows if visible(r, c)]


def auto_check(rows: list[dict], c: CpChecks) -> CpChecks:
    """主按钮语义：按完整比选结果自动勾选合适选项（不改 precise_*，那是用户声明）。"""
    out = CpChecks(planar4=False, planar3=False, seven=False,
                   fit_plane=False, fit_quad=False, geoid_model=c.geoid_model,
                   precise_xy=c.precise_xy, precise_h=c.precise_h)
    if c.precise_xy:
        out.planar3 = True
    else:
        feas = [r for r in rows if r["feasible"] and r.get("rms_xy") is not None
                and r["method"] in PLANE_METHODS]
        if feas:
            best = min(feas, key=lambda r: r["rms_xy"])["method"]
            out.planar4 = best in ("planar4", "planar4deg", "chain74")
            out.planar3 = best in ("planar3", "chain73", "inv_chain3")
            out.seven = best in ("seven", "seven2d", "chain74", "inv_chain")
            if best in ("planar4deg", "chain73", "chain74"):
                out.use_proj = best != "planar4deg"
    if c.precise_h:
        out.fit_plane = False
        out.fit_quad = False
    else:
        hcands = [r for r in rows if r["feasible"] and r.get("rms_h") is not None
                  and (r.get("heightfit") is not None or r.get("grid_name"))]
        if hcands:
            m = min(hcands, key=lambda r: r["rms_h"])["method"]
            out.geoid_model = m.startswith("格网:")
            out.fit_plane = m == "heightfit:plane"
            out.fit_quad = m == "heightfit:quadratic"
    return out


def select(rows: list[dict], c: CpChecks) -> tuple[dict | None, dict | None]:
    """按勾选过滤后选优：平面 RMS 最优一行 + 高程 RMS 最优一行。"""
    vis = visible_rows(rows, c)
    sel = None
    feas = [r for r in vis if r["feasible"] and r.get("rms_xy") is not None]
    if feas:
        sel = min(feas, key=lambda r: r["rms_xy"])
    hrows = [r for r in vis if r["feasible"] and r.get("rms_h") is not None
             and (r.get("heightfit") is not None or r.get("grid_name"))]
    sel_h = min(hrows, key=lambda r: r["rms_h"]) if hrows else None
    return sel, sel_h


def _make_projparams(ell, proj: dict, b0_deg: float):
    from app.core.ellipsoid import CGCS2000 as _CGCS

    if ell is None:
        ell = _CGCS
    """按页面投影参数（含投影面高 h0 与严密/近似）构造 gk_engine ProjParams。

    proj: {"l0": 度, "x0_m"/"y0_m": m, "h0": m, "rigorous": bool, "ell": Ellipsoid}
    b0_deg: 平均纬度（度），仅严密模式参与 Ra(B0)；近似模式不使用。
    """
    import math as _m

    from app.core.gk_engine import ProjParams

    b0_eff = float(proj.get("b0_manual") or b0_deg)   # 用户显式 B0 优先，否则自动均值
    return ProjParams(ell.a, ell.inv_f,
                      _m.radians(float(proj["l0"])), _m.radians(b0_eff),
                      float(proj.get("h0", 0.0) or 0.0), 1.0,
                      x0_km=float(proj.get("x0_m", 0.0)) / 1000.0,
                      y0_km=float(proj.get("y0_m", 500000.0)) / 1000.0,
                      rigorous=bool(proj.get("rigorous", False)))


def _projection_dict(proj: dict, b0_deg: float) -> dict:
    """序列化投影参数（JSON 安全，不含椭球对象）。"""
    return {"l0": float(proj["l0"]), "x0": float(proj.get("x0_m", 0.0)),
            "y0": float(proj.get("y0_m", 500000.0)), "k": 1.0,
            "h0": float(proj.get("h0", 0.0) or 0.0),
            "rigorous": bool(proj.get("rigorous", False)),
            "b0": float(b0_deg)}


def refit(method: str, s_used, d_used, proj: dict | None, kind: tuple[str, str]) -> tuple[dict, str]:
    """按方法重算参数（与保存共用），返回 (updates, 明细文本)。"""
    from app.core.transform2d import fit_4param, apply_4param, fit_3param2d
    from app.core.transform3d import fit_7param_refined, fit_3param
    from app.core.ellipsoid import geodetic_to_ecef
    from app.core.projection import gauss_kruger

    proj = proj or {}
    updates, text = {}, ""
    if method == "planar4":
        f4 = fit_4param(s_used[:, :2], d_used[:, :2])
        updates["planar4"] = {"dx": f4.param.dx, "dy": f4.param.dy,
                              "a": f4.param.a, "b": f4.param.b}
        text = (f"四参数：dx={f4.param.dx:.4f} m, dy={f4.param.dy:.4f} m\n"
                f"尺度={f4.param.a:.9f}, 旋转={math.degrees(math.atan2(f4.param.b, f4.param.a)) * 3600:+.2f}″")
    elif method == "planar3":
        f3p = fit_3param2d(s_used[:, :2], d_used[:, :2])
        updates["planar3"] = {"dx": f3p.param.dx, "dy": f3p.param.dy}
        text = (f"三参数（仅平移，无尺度/旋转）：dx={f3p.param.dx:.4f} m, dy={f3p.param.dy:.4f} m\n"
                f"适用于同椭球精确坐标（高精度）归算")
    elif method == "planar4deg":
        # 大地→平面：直接以 (B,L) 度值拟合四参数（未投影，粗略；小测区可用）
        f4 = fit_4param(s_used[:, [1, 0]], d_used[:, :2])
        updates["planar4"] = {"dx": f4.param.dx, "dy": f4.param.dy,
                              "a": f4.param.a, "b": f4.param.b}
        updates["direct_deg"] = True
        text = (f"四参数（源经纬度按平面坐标直接拟合——两组平面坐标转换，未过投影）：\n"
                f"dx={f4.param.dx:.4f} m, dy={f4.param.dy:.4f} m\n"
                f"尺度={f4.param.a:.9f}, 旋转={math.degrees(math.atan2(f4.param.b, f4.param.a)) * 3600:+.2f}″")
    elif method == "chain73":
        ell = proj.get("ell_dst") or proj.get("ell")
        b0_deg = float(proj.get("b0_manual") or np.mean(s_used[:, 1]))   # 用户 B0 优先
        pp = _make_projparams(ell, proj, b0_deg)
        gx, gy = zip(*[pp.forward(math.radians(float(b)), math.radians(float(l)))
                       for b, l in zip(s_used[:, 1], s_used[:, 0])])
        gx, gy = np.asarray(gx), np.asarray(gy)
        f3p = fit_3param2d(np.column_stack([gx, gy]), d_used[:, :2])
        updates["projection"] = _projection_dict(proj, b0_deg)
        updates["planar3"] = {"dx": f3p.param.dx, "dy": f3p.param.dy}
        text = (f"投影：L0={float(proj['l0']):.6f}°（先投影再三参数，高精度坐标适用）\n"
                f"三参数：dx={f3p.param.dx:.4f} m, dy={f3p.param.dy:.4f} m")
    elif method == "seven2d":
        cx0 = float(np.mean(s_used[:, 0])); cy0 = float(np.mean(s_used[:, 1]))
        s3 = np.column_stack([s_used[:, 0] - cx0, s_used[:, 1] - cy0, s_used[:, 2]])
        d3 = np.column_stack([d_used[:, 0] - cx0, d_used[:, 1] - cy0, d_used[:, 2]])
        f7 = fit_7param_refined(s3, d3)
        updates["kind"] = "seven2d"
        updates["seven"] = {"dx": f7.param.dx, "dy": f7.param.dy, "dz": f7.param.dz,
                            "rx_s": f7.param.rx_s, "ry_s": f7.param.ry_s, "rz_s": f7.param.rz_s,
                            "scale_ppm": f7.param.scale_ppm, "center": [cx0, cy0]}
        text = (f"二维七参数（重心 X0={cx0:.3f} Y0={cy0:.3f}）：\n"
                f"dX={f7.param.dx:.4f} dY={f7.param.dy:.4f} dZ={f7.param.dz:.4f} m\n"
                f"Rx={f7.param.rx_s:.2f}″ Ry={f7.param.ry_s:.2f}″ Rz={f7.param.rz_s:.2f}″ "
                f"m={f7.param.scale_ppm:+.4f} ppm")
    elif method == "chain74":
        ell = proj.get("ell_dst") or proj.get("ell")
        b0_deg = float(proj.get("b0_manual") or np.mean(s_used[:, 1]))   # 用户 B0 优先
        pp = _make_projparams(ell, proj, b0_deg)
        gx, gy = zip(*[pp.forward(math.radians(float(b)), math.radians(float(l)))
                       for b, l in zip(s_used[:, 1], s_used[:, 0])])
        gx, gy = np.asarray(gx), np.asarray(gy)
        f4 = fit_4param(np.column_stack([gx, gy]), d_used[:, :2])
        px, py = apply_4param(f4.param, gx, gy)
        updates["projection"] = _projection_dict(proj, b0_deg)
        updates["planar4"] = {"dx": f4.param.dx, "dy": f4.param.dy,
                              "a": f4.param.a, "b": f4.param.b}
        h0 = float(proj.get("h0", 0.0) or 0.0)
        text = (f"投影：L0={float(proj['l0']):.6f}°, y0={float(proj.get('y0_m', 0.0)) / 1000:.3f} km, "
                f"x0={float(proj.get('x0_m', 0.0)) / 1000:.3f} km"
                + (f", 投影面高 h0={h0:.3f} m" if h0 else "")
                + ("，严密工程椭球" if proj.get("rigorous") else "，近似工程椭球") + "\n"
                f"四参数：dx={f4.param.dx:.4f} m, dy={f4.param.dy:.4f} m\n"
                f"尺度={f4.param.a:.9f}, 旋转={math.degrees(math.atan2(f4.param.b, f4.param.a)) * 3600:+.2f}″")
    elif method == "direct_gk":
        b0_deg = float(proj.get("b0_manual") or np.mean(s_used[:, 1]))   # 用户 B0 优先
        updates["projection"] = _projection_dict(proj, b0_deg)
        text = (f"直接投影（无基准转换）：L0={float(proj['l0']):.6f}°, "
                f"y0={float(proj.get('y0_m', 0.0)) / 1000:.3f} km")
    elif method == "three":
        xyz1 = np.column_stack(geodetic_to_ecef(s_used[:, 1], s_used[:, 0], s_used[:, 2]))
        xyz2 = np.column_stack(geodetic_to_ecef(d_used[:, 1], d_used[:, 0], d_used[:, 2]))
        f3 = fit_3param(xyz1, xyz2)
        updates["three"] = {"dx": f3.param.dx, "dy": f3.param.dy, "dz": f3.param.dz}
        text = f"三参数（平移）：dX={f3.param.dx:.4f} dY={f3.param.dy:.4f} dZ={f3.param.dz:.4f} m"
    elif method == "seven":
        xyz1 = np.column_stack(geodetic_to_ecef(s_used[:, 1], s_used[:, 0], s_used[:, 2]))
        xyz2 = np.column_stack(geodetic_to_ecef(d_used[:, 1], d_used[:, 0], d_used[:, 2]))
        f7 = fit_7param_refined(xyz1, xyz2)
        updates["seven"] = {"dx": f7.param.dx, "dy": f7.param.dy, "dz": f7.param.dz,
                            "rx_s": f7.param.rx_s, "ry_s": f7.param.ry_s, "rz_s": f7.param.rz_s,
                            "scale_ppm": f7.param.scale_ppm}
        text = (f"七参数（布尔莎）：dX={f7.param.dx:.4f} dY={f7.param.dy:.4f} dZ={f7.param.dz:.4f} m\n"
                f"Rx={f7.param.rx_s:.2f}″ Ry={f7.param.ry_s:.2f}″ Rz={f7.param.rz_s:.2f}″ "
                f"m={f7.param.scale_ppm:+.4f} ppm")
    elif method in ("inv_direct", "inv_chain3", "inv_chain"):
        # 逆向链：源平面（工程坐标）--源侧投影反算--> 源大地 --三/七参数--> 目标大地
        from app.core.projection import detect_zone_on_column, inverse_gauss_points
        from app.core.ellipsoid import ecef_to_geodetic
        psrc = proj.get("proj_src") or {}
        ell = psrc.get("ell") or proj.get("ell")
        ell_t = proj.get("ell_dst") or proj.get("ell")
        zone = detect_zone_on_column(s_used[:, 1])
        l0_s = (float(zone["l0"]) if zone
                else (float(psrc["l0"]) if psrc.get("l0") is not None else None))
        if l0_s is None:
            raise ValueError("源侧投影参数未填写（中央子午线），且源坐标不含可识别带号；"
                             "无法把工程平面坐标反算为大地坐标")
        ys = s_used[:, 1] - zone["zone"] * 1_000_000 if zone else s_used[:, 1]
        blh = inverse_gauss_points(s_used[:, 0], ys, l0_s, ell=ell,
                                   x0_m=float(psrc.get("x0_m", 0.0) or 0.0),
                                   y0_m=float(psrc.get("y0_m", 500000.0) or 500000.0),
                                   h0=float(psrc.get("h0", 0.0) or 0.0),
                                   rigorous=bool(psrc.get("rigorous", False)))
        b_arr = np.asarray([b for b, _ in blh], dtype=float)
        l_arr = np.asarray([l for _, l in blh], dtype=float)
        b0_deg = float(psrc.get("b0_manual") or np.mean(b_arr))   # 用户 B0 优先
        updates["projection_src"] = {
            "l0": l0_s, "x0": float(psrc.get("x0_m", 0.0) or 0.0),
            "y0": float(psrc.get("y0_m", 500000.0) or 500000.0),
            "h0": float(psrc.get("h0", 0.0) or 0.0),
            "rigorous": bool(psrc.get("rigorous", False)), "b0": b0_deg,
            "zone": int(zone["zone"]) if zone else None,
            "band": zone["band"] if zone else None}
        ptxt = (f"源投影：L0={l0_s:.6f}°, y0={float(psrc.get('y0_m', 0.0)) / 1000:.3f} km, "
                f"x0={float(psrc.get('x0_m', 0.0)) / 1000:.3f} km"
                + (f", 投影面高 h0={float(psrc.get('h0', 0.0) or 0.0):.3f} m"
                   if psrc.get("h0") else "")
                + ("，严密工程椭球" if psrc.get("rigorous") else "，近似工程椭球")
                + (f"（源 y 含带号：{zone['band']} 带号{zone['zone']}，自动剥离）" if zone else ""))
        if method == "inv_direct":
            text = ptxt + "\n同基准直接反算（无基准转换，基线参照）"
        else:
            # 与比选同口径：两侧用真实高程（源=工程 h 列，目标=大地高列）
            xyz1 = np.column_stack(geodetic_to_ecef(b_arr, l_arr, s_used[:, 2], ell))
            xyz2 = np.column_stack(geodetic_to_ecef(d_used[:, 1], d_used[:, 0],
                                                    d_used[:, 2], ell_t))
            if method == "inv_chain3":
                f3 = fit_3param(xyz1, xyz2)
                updates["three"] = {"dx": f3.param.dx, "dy": f3.param.dy, "dz": f3.param.dz}
                text = (ptxt + "\n"
                        f"三参数（平移）：dX={f3.param.dx:.4f} dY={f3.param.dy:.4f} "
                        f"dZ={f3.param.dz:.4f} m")
            else:
                f7 = fit_7param_refined(xyz1, xyz2)
                updates["seven"] = {"dx": f7.param.dx, "dy": f7.param.dy, "dz": f7.param.dz,
                                    "rx_s": f7.param.rx_s, "ry_s": f7.param.ry_s,
                                    "rz_s": f7.param.rz_s, "scale_ppm": f7.param.scale_ppm}
                text = (ptxt + "\n"
                        f"七参数（布尔莎）：dX={f7.param.dx:.4f} dY={f7.param.dy:.4f} "
                        f"dZ={f7.param.dz:.4f} m\n"
                        f"Rx={f7.param.rx_s:.2f}″ Ry={f7.param.ry_s:.2f}″ "
                        f"Rz={f7.param.rz_s:.2f}″ m={f7.param.scale_ppm:+.4f} ppm")
    return updates, text


def seven_method_for(kind: tuple[str, str]) -> str:
    """勾选"七参数"时按数据类型实际使用的方法。"""
    src, dst = kind
    if src == "planar":
        return "inv_chain" if dst == "lonlat" else "seven2d"
    return "chain74" if dst == "planar" else "seven"


def planar_panel_text(method: str, s_used, d_used, proj, kind) -> str:
    try:
        _u, text = refit(method, s_used, d_used, proj, kind)
        return text or "未计算"
    except Exception as e:  # noqa: BLE001
        return f"（{method} 参数计算失败：{e}）"


def height_panel_text(row: dict | None) -> str:
    if row is None or not row.get("heightfit"):
        return "未计算"
    hf = row["heightfit"]
    loo = f"{hf.loo_rms:.4f}" if hf.loo_rms is not None else "—"
    return (f"{row['method']}    RMS={hf.rms:.4f} m    LOO={loo} m\n"
            f"系数={[round(float(c), 6) for c in hf.coef]}    "
            f"中心={[round(float(c), 3) for c in hf.center]}\n"
            f"{hf.note}")


def geoid_panel_text(grids: list, rows: list[dict]) -> str:
    lines = []
    for gname, gmodel in (grids or []):
        gd = getattr(gmodel, "grid", None)
        info = (f"{gd.name}: 双线性内插 ξ(经度,纬度)，出界返回 None"
                if gd is not None else f"{gname}: 双线性内插")
        grow = next((r for r in (rows or []) if r["method"] == f"格网:{gname}" and r["feasible"]), None)
        if grow:
            info += f"    残差RMS={grow['rms_h']:.4f} m"
        lines.append(info)
    if not lines:
        lines.append("（尚未计算或无可用格网模型：勾选后点【计算与比选】即自动加载格网并评估残差）")
    return "\n".join(lines)


def build_param_doc(name: str, kind: tuple[str, str], method: str, updates: dict,
                    sel_h: dict | None, names: list, use_geoid_flag: bool) -> dict:
    """组装 coordparam/1 参数文档（保存用）。"""
    from datetime import datetime

    d = {"schema": "coordparam/1", "name": name, "kind": method,
         "source_kind": kind[0], "target_kind": kind[1], "ellipsoid": "CGCS2000",
         "use_geoid": use_geoid_flag, "angle_format": "d",
         "projection": None, "planar4": None, "planar3": None, "seven": None,
         "three": None, "heightfit": None,
         "accuracy": {"n_points": 0, "note": ""},
         "created": datetime.now().isoformat(timespec="seconds"),
         "points_used": names}
    for k, v in (updates or {}).items():
        d[k] = v
    if sel_h is not None and sel_h.get("heightfit"):
        hf = sel_h["heightfit"]
        d["heightfit"] = {"mode": hf.mode, "space": hf.space, "value_type": hf.value_type,
                          "coef": [float(c) for c in hf.coef],
                          "center": [float(hf.center[0]), float(hf.center[1])],
                          "loo_rms": hf.loo_rms}
    if sel_h is not None and sel_h.get("grid_name"):
        d["geoid_grid"] = sel_h["grid_name"]
        d["use_geoid"] = True
        if not sel_h.get("heightfit"):
            d["kind"] = "geoid"
            d["accuracy"]["note"] = (d["accuracy"].get("note", "") +
                                     "；纯大地水准面参数（H−ξ格网）").lstrip("；")
        else:
            d["accuracy"]["note"] = (d["accuracy"].get("note", "") +
                                     "；高程=格网高程异常+残差趋势项（应用需该格网模型）").lstrip("；")
    return d


def residual_cells(n_table: int, pt_rows: list[int], sel: dict | None,
                   sel_h: dict | None) -> list[tuple[int, int, str]]:
    """残差回填清单：[(表格行, 列, 文本)]。列 9=x残差 10=y残差 11=h残差。

    pt_rows：数组下标 → 表格行号映射（GUI 侧在读取点对时记录）。
    """
    out: list[tuple[int, int, str]] = []
    if sel is not None and sel.get("res_xy") is not None:
        for i, ai in enumerate(sel.get("ixy", [])):
            if 0 <= ai < len(pt_rows):
                trow = pt_rows[ai]
                out.append((trow, 9, f"{sel['res_xy'][i, 0]:+.4f}"))
                out.append((trow, 10, f"{sel['res_xy'][i, 1]:+.4f}"))
    if sel_h is not None and sel_h.get("res_h") is not None:
        for i, ai in enumerate(sel_h.get("ih", [])):
            if 0 <= ai < len(pt_rows):
                out.append((pt_rows[ai], 11, f"{sel_h['res_h'][i]:+.4f}"))
    return out
