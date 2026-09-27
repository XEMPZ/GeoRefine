"""转换方法自动比选（手簿风格）。

compare_methods 返回方法对比表，每行:
  {"method", "feasible", "n", "rms_xy", "rms_h", "loo_rms_h", "selected", "note"}
方法池: planar4（平面四参数）/ seven（空间七参数）/ chain74（七参数+投影+四参数）
        / direct_gk（直接高斯投影基线）；高程方案（const/plane/quadratic）独立评估 LOO。
坐标数组约定: lonlat → (lon, lat)；planar → (x北, y东)。
"""
from __future__ import annotations

import numpy as np

from app.core.ellipsoid import WGS84, geodetic_to_ecef
from app.core.heightfit import HeightFit, fit_height, select_best_fit
from app.core.projection import auto_central_meridian, gauss_kruger
from app.core.transform2d import Param4, apply_4param, fit_4param
from app.core.transform3d import (apply_3param, apply_7param, fit_3param,
                                  fit_7param, fit_7param_refined)

_METHOD_TIPS = {
    "planar4": "四参数适用于小测区（经验上 ≤50km²）的平面基准对齐",
    "planar3": "三参数（仅平移）：同椭球精确坐标（高精度）或无尺度旋转差的基准对齐",
    "three": "三参数（仅平移）适用于同尺度基准的粗略归算或点数受限制",
    "seven": "七参数适用于跨基准/大范围（>50km²）转换",
    "seven2d": "二维七参数（布尔莎平面退化）：平面点对，含高差倾斜项；等高时与四参数等价",
    "chain74": "七参数+投影+四参数适用于 源大地坐标→地方平面坐标系 的完整链路",
    "inv_direct": "同基准直接反算：源工程平面坐标按源投影反算为大地坐标（无基准转换）",
    "inv_chain3": "反算+三参数：源工程平面坐标反算大地后做三参数（仅平移，高精度/同尺度基准）",
    "inv_chain": "反算+七参数：源工程平面坐标反算大地后做七参数（跨基准，工程坐标→大地坐标）",
    "planar4deg": "四参数（源经纬度按平面坐标直接拟合）：两组平面坐标转换，源以经纬度表达、未过投影",
    "chain73": "投影+三参数：高精度二维坐标（同椭球 GNSS 静态）归算",
    "direct_gk": "直接投影不做基准转换，仅当源/目标同基准时成立",
}


def detect_coord_kind(arr2d) -> str:
    """判别 (n,2) 数组是经纬度还是平面坐标。"""
    a = np.asarray(arr2d, dtype=float)
    c0, c1 = np.abs(a[:, 0]).max(), np.abs(a[:, 1]).max()
    if c0 <= 360 and c1 <= 360:
        return "lonlat"
    return "planar"


def _rms(d):
    d = np.asarray(d, dtype=float)
    return float(np.sqrt(np.mean(d ** 2)))


def compare_methods(src_pts, dst_pts, src_h, dst_h, src_kind, dst_kind,
                    ell=WGS84, ell_dst=None, l0: float | None = None,
                    force_mode: str | None = None,
                    pos_mask=None, h_mask=None,
                    plane_force: str | None = None,
                    proj: dict | None = None,
                    geoid_grids=None,
                    no_height: bool = False,
                    height_modes=None) -> list[dict]:
    """生成方法对比表并自动选优（selected=True 的 rms_xy 最小）。

    src_pts/dst_pts: (n,2)；src_h/dst_h: (n,) 高程（可为 None = 不做高程评估）。
    force_mode: 强制高程拟合阶（'const'/'plane'/'quadratic'），None=LOO 自动定阶。
    pos_mask/h_mask: 每点布尔掩码（手簿 cot 的 Y/N）——平面参数/高程拟合分别只用
                     掩码为 True 的点；残差统计也在各自子集上进行。
    plane_force: 强制平面方法（'planar4'/'seven'/'seven2d'/'chain74'/'direct_gk'），
                 None=自动比选全部可行方法。
    proj: 用户投影参数 {"l0": 度, "x0_m": m, "y0_m": m}；给了则 direct_gk/chain74
          直接采用（不再多候选推定中央子午线）。
    geoid_grids: [(名称, GridModel), ...]——追加各格网模型在控制点上的高程残差行
                 （rms_h = RMS(h正常 − (H − ξ格网))），供格网 vs 拟合对比。
    no_height: True 时跳过高程方案评估。
    height_modes: 高程拟合模式列表（如 ["const","plane","quadratic"]），逐模式各出一行；
                  None = LOO 自动定阶只出最优一行。
    """
    src = np.asarray(src_pts, dtype=float)
    dst = np.asarray(dst_pts, dtype=float)
    n = src.shape[0]
    rows: list[dict] = []
    has_h = src_h is not None and dst_h is not None and not no_height
    ell_t = ell_dst or ell   # 目标椭球（投影/目标大地坐标所属）

    ixy = list(range(n)) if pos_mask is None else [i for i in range(n) if pos_mask[i]]
    ih = list(range(n)) if h_mask is None else [i for i in range(n) if h_mask[i]]
    s_xy, d_xy = src[ixy], dst[ixy]
    n_xy, n_h = len(ixy), len(ih)

    def _xy_row(method, feasible, res_xy, note=""):
        if res_xy is None:
            rows.append({"method": method, "feasible": False, "n": n_xy, "rms_xy": None,
                         "rms_h": None, "loo_rms_h": None, "selected": False, "note": note})
            return
        d = np.hypot(res_xy[:, 0], res_xy[:, 1])
        rows.append({"method": method, "feasible": feasible, "n": n_xy,
                     "rms_xy": _rms(d), "max_xy": float(np.max(d)),
                     "rms_h": None, "loo_rms_h": None, "selected": False,
                     "note": (note + "；" if note else "") + _METHOD_TIPS.get(method, ""),
                     "res_xy": np.asarray(res_xy, dtype=float), "ixy": list(ixy)})

    # 用户投影参数（B/L→平面的 L0 与加常数）
    proj_l0 = float(proj["l0"]) if (proj and proj.get("l0") is not None) else None
    proj_x0 = float(proj.get("x0_m", 0.0)) if proj else 0.0
    proj_y0 = float(proj.get("y0_m", 500000.0)) if proj else 500000.0

    if src_kind == "planar" and dst_kind == "planar":
        if plane_force in (None, "planar4"):
            try:
                f4 = fit_4param(s_xy, d_xy)
                pred = apply_4param(f4.param, s_xy[:, 0], s_xy[:, 1])
                _xy_row("planar4", True, np.column_stack([pred[0] - d_xy[:, 0], pred[1] - d_xy[:, 1]]), f4.note)
            except ValueError as e:
                _xy_row("planar4", False, None, str(e))
        # 二维七参数（布尔莎模型平面退化）：以 (x, y, h) 构造局部 XYZ 解算，
        # 平面预测含高差倾斜项（等高/无高程时与四参数预测等价）；
        # 坐标重心化后解算（残差在平移下不变），避免大坐标法方程病态。
        if plane_force in (None, "planar3"):
            try:
                from app.core.transform2d import fit_3param2d
                f3p = fit_3param2d(s_xy, d_xy)
                pred = apply_4param(f3p.param, s_xy[:, 0], s_xy[:, 1])
                _xy_row("planar3", True, np.column_stack([pred[0] - d_xy[:, 0], pred[1] - d_xy[:, 1]]), f3p.note)
            except ValueError as e:
                _xy_row("planar3", False, None, str(e))
        if plane_force in (None, "seven2d") and has_h and n_xy >= 3:
            cx0 = float(np.mean(s_xy[:, 0])); cy0 = float(np.mean(s_xy[:, 1]))
            s3 = np.column_stack([s_xy[:, 0] - cx0, s_xy[:, 1] - cy0, np.asarray(src_h)[ixy]])
            d3 = np.column_stack([d_xy[:, 0] - cx0, d_xy[:, 1] - cy0, np.asarray(dst_h)[ixy]])
            try:
                f7 = fit_7param_refined(s3, d3)
                o = apply_7param(f7.param, s3[:, 0], s3[:, 1], s3[:, 2])
                _xy_row("seven2d", True, np.column_stack([o[0] - d3[:, 0], o[1] - d3[:, 1]]), f7.note)
            except ValueError as e:
                _xy_row("seven2d", False, None, str(e))
    elif src_kind == "lonlat" and dst_kind == "lonlat":
        xyz1 = np.column_stack(geodetic_to_ecef(src[ixy, 1], src[ixy, 0], np.zeros(n_xy), ell))
        xyz2 = np.column_stack(geodetic_to_ecef(dst[ixy, 1], dst[ixy, 0], np.zeros(n_xy), ell_t))
        if plane_force in (None, "three"):
            try:
                f3 = fit_3param(xyz1, xyz2)
                out3 = apply_3param(f3.param, xyz1[:, 0], xyz1[:, 1], xyz1[:, 2])
                from app.core.ellipsoid import ecef_to_geodetic
                lat3, lon3, _h3 = ecef_to_geodetic(out3[0], out3[1], out3[2], ell)
                res3 = np.column_stack([(np.asarray(lon3) - dst[ixy, 0]) * np.cos(np.radians(dst[ixy, 1])) * 111320.0,
                                        (np.asarray(lat3) - dst[ixy, 1]) * 110540.0])
                _xy_row("three", True, res3, f3.note)
            except ValueError as e:
                _xy_row("three", False, None, str(e))
        if plane_force in (None, "seven"):
            try:
                f7 = fit_7param(xyz1, xyz2)
                out = apply_7param(f7.param, xyz1[:, 0], xyz1[:, 1], xyz1[:, 2])
                from app.core.ellipsoid import ecef_to_geodetic
                lat2, lon2, _h2 = ecef_to_geodetic(out[0], out[1], out[2], ell)
                res = np.column_stack([(np.asarray(lon2) - dst[ixy, 0]) * np.cos(np.radians(dst[ixy, 1])) * 111320.0,
                                       (np.asarray(lat2) - dst[ixy, 1]) * 110540.0])
                _xy_row("seven", True, res, f7.note)
            except ValueError as e:
                _xy_row("seven", False, None, str(e))
        _xy_row("direct_gk", False, None, "目标为大地坐标，直接投影不适用")
    elif src_kind == "lonlat" and dst_kind == "planar":
        mean_lon = float(np.mean(src[ixy, 0]))
        if proj_l0 is not None:
            l0_cands = [proj_l0]                      # 用户指定投影参数：直接采用
        elif l0 is not None:
            l0_cands = [l0]
        else:
            # 自动判断：3°带 / 6°带 / 测区中央三候选择优（地方坐标系常为任意带）
            l0_cands = sorted({auto_central_meridian(mean_lon, 3),
                               auto_central_meridian(mean_lon, 6), round(mean_lon, 2)})
        if plane_force in (None, "direct_gk"):
            gx, gy = gauss_kruger(src[ixy, 1], src[ixy, 0], l0_cands[0], ell=ell_t,
                                  x0=proj_x0, y0=proj_y0)
            res_gk = np.column_stack([gx - dst[ixy, 0], gy - dst[ixy, 1]])
            _xy_row("direct_gk", True, res_gk, f"L0={float(l0_cands[0]):.6f}°")
        # chain74：直接投影 + 四参数吸收剩余差异（= 七参数+投影+四参数 的平面实现）
        if plane_force in (None, "chain74"):
            best74 = None
            for cand in l0_cands:
                try:
                    gx2, gy2 = gauss_kruger(src[ixy, 1], src[ixy, 0], cand, ell=ell_t,
                                            x0=proj_x0, y0=proj_y0)
                    f4 = fit_4param(np.column_stack([gx2, gy2]), d_xy)
                    px, py = apply_4param(f4.param, gx2, gy2)
                    res = np.column_stack([px - d_xy[:, 0], py - d_xy[:, 1]])
                    rms = _rms(np.hypot(res[:, 0], res[:, 1]))
                    if best74 is None or rms < best74[0]:
                        best74 = (rms, cand, f4, res)
                except ValueError:
                    continue
            if best74 is not None:
                _xy_row("chain74", True, best74[3], f"L0={float(best74[1]):.6f}°; " + best74[2].note)
            else:
                _xy_row("chain74", False, None, "无可行中央子午线")
        # 四参数（源经纬度按平面坐标直接拟合）：两组平面坐标转换，未过投影
        try:
            from app.core.transform2d import fit_4param as _f4d
            f4d = _f4d(src[ixy][:, [1, 0]], d_xy)       # (B,L)度 → (x,y)
            pdx, pdy = apply_4param(f4d.param, src[ixy, 1], src[ixy, 0])
            _xy_row("planar4deg", True,
                    np.column_stack([pdx - d_xy[:, 0], pdy - d_xy[:, 1]]),
                    "源经纬度按平面坐标直接拟合（未过投影）" + f4d.note)
        except ValueError as e:
            _xy_row("planar4deg", False, None, str(e))
        # 先投影再三参数（高精度坐标适用）
        try:
            from app.core.transform2d import fit_3param2d
            l0_c3 = proj_l0 if proj_l0 is not None else (
                l0 if l0 is not None else float(np.mean(src[ixy, 0])))
            gx3, gy3 = gauss_kruger(src[ixy, 1], src[ixy, 0], l0_c3, ell=ell_t,
                                    x0=proj_x0, y0=proj_y0)
            f3p = fit_3param2d(np.column_stack([gx3, gy3]), d_xy)
            p3x, p3y = apply_4param(f3p.param, gx3, gy3)
            _xy_row("chain73", True,
                    np.column_stack([p3x - d_xy[:, 0], p3y - d_xy[:, 1]]),
                    f"L0={float(l0_c3):.6f}°; 先投影再三参数（高精度坐标适用）")
        except ValueError as e:
            _xy_row("chain73", False, None, str(e))
        _xy_row("seven", False, None, "目标为平面坐标，纯七参数需配合投影")
    elif src_kind == "planar" and dst_kind == "lonlat":
        # 逆向：源工程平面坐标 --源侧投影反算--> 源大地 --三/七参数--> 目标大地坐标。
        # 源侧投影参数 proj["proj_src"]（用户填写）；源 y 含带号时自动识别带号并
        # 剥离（L0 由带号确定，优先于手填值）。
        from app.core.projection import detect_zone_on_column, inverse_gauss_points
        psrc = (proj or {}).get("proj_src") or {}
        zone = detect_zone_on_column(src[:, 1])
        l0_s = (float(zone["l0"]) if zone
                else (float(psrc["l0"]) if psrc.get("l0") is not None else None))
        if l0_s is None:
            _xy_row("inv_direct", False, None, "需源侧投影参数（或源 y 含带号可自动识别）")
            _xy_row("inv_chain3", False, None, "需源侧投影参数（或源 y 含带号可自动识别）")
            _xy_row("inv_chain", False, None, "需源侧投影参数（或源 y 含带号可自动识别）")
        else:
            ys = src[:, 1] - zone["zone"] * 1_000_000 if zone else src[:, 1]
            blh = inverse_gauss_points(
                src[:, 0], ys, l0_s, ell=ell,
                x0_m=float(psrc.get("x0_m", 0.0) or 0.0),
                y0_m=float(psrc.get("y0_m", 500000.0) or 500000.0),
                h0=float(psrc.get("h0", 0.0) or 0.0),
                rigorous=bool(psrc.get("rigorous", False)))
            b_s = np.asarray([b for b, _ in blh], dtype=float)[ixy]
            l_s = np.asarray([l for _, l in blh], dtype=float)[ixy]
            znote = (f"源含带号：{zone['band']} 带号{zone['zone']}，L0={l0_s:g}°"
                     if zone else f"源投影 L0={l0_s:g}°")
            # 同基准直接反算（基线）：反算结果与目标大地坐标直接对比
            res_d = np.column_stack([(l_s - dst[ixy, 0]) * np.cos(np.radians(dst[ixy, 1])) * 111320.0,
                                     (b_s - dst[ixy, 1]) * 110540.0])
            _xy_row("inv_direct", True, res_d, znote)
            # 空间拟合两侧用真实高程（源=工程坐标 h 列，目标=大地高列）；
            # 任一侧高程缺失时按 0（两点均在椭球面上），与点对数据保持一致
            h1 = (np.asarray(src_h, dtype=float)[ixy] if src_h is not None
                  else np.zeros(n_xy))
            h2 = (np.asarray(dst_h, dtype=float)[ixy] if dst_h is not None
                  else np.zeros(n_xy))
            xyz1 = np.column_stack(geodetic_to_ecef(b_s, l_s, h1, ell))
            xyz2 = np.column_stack(geodetic_to_ecef(dst[ixy, 1], dst[ixy, 0],
                                                    h2, ell_t))
            if plane_force in (None, "inv_chain3"):
                try:
                    f3 = fit_3param(xyz1, xyz2)
                    o3 = apply_3param(f3.param, xyz1[:, 0], xyz1[:, 1], xyz1[:, 2])
                    from app.core.ellipsoid import ecef_to_geodetic
                    lat3, lon3, _h3 = ecef_to_geodetic(o3[0], o3[1], o3[2], ell_t)
                    res3 = np.column_stack([(np.asarray(lon3) - dst[ixy, 0]) * np.cos(np.radians(dst[ixy, 1])) * 111320.0,
                                            (np.asarray(lat3) - dst[ixy, 1]) * 110540.0])
                    _xy_row("inv_chain3", True, res3, znote + "；" + f3.note)
                except ValueError as e:
                    _xy_row("inv_chain3", False, None, str(e))
            if plane_force in (None, "inv_chain"):
                try:
                    f7 = fit_7param(xyz1, xyz2)
                    o7 = apply_7param(f7.param, xyz1[:, 0], xyz1[:, 1], xyz1[:, 2])
                    from app.core.ellipsoid import ecef_to_geodetic
                    lat2, lon2, _h2 = ecef_to_geodetic(o7[0], o7[1], o7[2], ell_t)
                    res7 = np.column_stack([(np.asarray(lon2) - dst[ixy, 0]) * np.cos(np.radians(dst[ixy, 1])) * 111320.0,
                                            (np.asarray(lat2) - dst[ixy, 1]) * 110540.0])
                    _xy_row("inv_chain", True, res7, znote + "；" + f7.note)
                except ValueError as e:
                    _xy_row("inv_chain", False, None, str(e))
    else:
        raise ValueError(f"不支持的源/目标类型组合: {src_kind} → {dst_kind}")

    # ---- 高程方案评估（固定差/平面/曲面；height_modes 逐模式出行或 LOO 自动定阶）----
    if has_h:
        sh = np.asarray(src_h, dtype=float)[ih]
        dhh = np.asarray(dst_h, dtype=float)[ih]
        fcoords = src[ih]
        space = "planar" if src_kind == "planar" else "lonlat"
        try:
            from app.core.heightfit import fit_height, eval_height, select_best_fit
            if height_modes:
                for mode in height_modes:
                    try:
                        hf = fit_height(fcoords, dhh - sh, mode, "dh", space)
                        res_h = np.asarray(eval_height(hf, fcoords[:, 0], fcoords[:, 1]),
                                           dtype=float) - (dhh - sh)
                        rows.append({"method": f"heightfit:{hf.mode}", "feasible": True, "n": n_h,
                                     "rms_xy": None, "rms_h": hf.rms, "loo_rms_h": hf.loo_rms,
                                     "selected": False, "note": hf.note, "heightfit": hf,
                                     "ih": list(ih), "res_h": res_h})
                    except ValueError as e:
                        rows.append({"method": f"heightfit:{mode}", "feasible": False, "n": n_h,
                                     "rms_xy": None, "rms_h": None, "loo_rms_h": None,
                                     "selected": False, "note": str(e)})
            elif force_mode:
                hf = fit_height(fcoords, dhh - sh, force_mode, "dh", space)
                res_h = np.asarray(eval_height(hf, fcoords[:, 0], fcoords[:, 1]),
                                   dtype=float) - (dhh - sh)
                rows.append({"method": f"heightfit:{hf.mode}", "feasible": True, "n": n_h,
                             "rms_xy": None, "rms_h": hf.rms, "loo_rms_h": hf.loo_rms,
                             "selected": False, "note": hf.note, "heightfit": hf,
                             "ih": list(ih), "res_h": res_h})
            else:
                hf = select_best_fit(fcoords, dhh - sh, "dh", space)
                res_h = np.asarray(eval_height(hf, fcoords[:, 0], fcoords[:, 1]),
                                   dtype=float) - (dhh - sh)
                rows.append({"method": f"heightfit:{hf.mode}", "feasible": True, "n": n_h,
                             "rms_xy": None, "rms_h": hf.rms, "loo_rms_h": hf.loo_rms,
                             "selected": False, "note": hf.note, "heightfit": hf,
                             "ih": list(ih), "res_h": res_h})
        except ValueError as e:
            rows.append({"method": "heightfit", "feasible": False, "n": n_h, "rms_xy": None,
                         "rms_h": None, "loo_rms_h": None, "selected": False, "note": str(e)})

    # ---- 大地水准面格网残差行（格网 vs 拟合对比）----
    for gname, gmodel in (geoid_grids or []):
        if src_kind != "lonlat" or not has_h:
            continue
        try:
            dh_res = []
            used_idx = []
            for k in ih:
                xi = gmodel.try_undulation(float(src[k, 0]), float(src[k, 1]))
                if xi is None:
                    continue
                dh_res.append((dst_h[k] - src_h[k]) + xi)  # h正常 − (H − ξ)
                used_idx.append(k)
            if used_idx:
                # 串联行：格网 + 残差趋势项（格网管中长波，趋势项吸收系统差）
                rarr = np.asarray(dh_res, dtype=float)
                coords_used = src[used_idx]
                space = "lonlat" if src_kind == "lonlat" else "planar"
                for tag, mode, min_n in (("+平面趋势", "plane", 3), ("+曲面趋势", "quadratic", 6)):
                    if len(used_idx) < min_n:
                        continue
                    try:
                        from app.core.heightfit import fit_height, eval_height
                        hft = fit_height(coords_used, rarr.copy(), mode, "dh", space)
                        comb = rarr - np.asarray(eval_height(hft, coords_used[:, 0], coords_used[:, 1]), dtype=float)
                        rows.append({"method": f"格网:{gname}{tag}", "feasible": True,
                                     "n": len(used_idx), "rms_xy": None,
                                     "rms_h": float(np.sqrt(np.mean(np.square(comb)))),
                                     "loo_rms_h": hft.loo_rms, "selected": False,
                                     "note": f"格网插值 + 残差{ '平面' if mode == 'plane' else '曲面'}拟合"
                                             f"（串联：ξ=ξ格网+趋势；{len(used_idx)}/{n_h} 点在格网内）",
                                     "heightfit": hft, "grid_name": gname,
                                     "ih": list(used_idx), "res_h": comb})
                    except ValueError:
                        continue
                rms = float(np.sqrt(np.mean(np.square(rarr))))
                rows.append({"method": f"格网:{gname}", "feasible": True, "n": len(used_idx),
                             "rms_xy": None, "rms_h": rms, "loo_rms_h": None,
                             "selected": False,
                             "note": f"格网高程异常残差（{len(used_idx)}/{n_h} 点在范围内）",
                             "grid_name": gname, "ih": list(used_idx), "res_h": rarr})
            else:
                rows.append({"method": f"格网:{gname}", "feasible": False, "n": 0,
                             "rms_xy": None, "rms_h": None, "loo_rms_h": None,
                             "selected": False, "note": "全部点超出格网范围"})
        except Exception as e:  # noqa: BLE001
            rows.append({"method": f"格网:{gname}", "feasible": False, "n": 0,
                         "rms_xy": None, "rms_h": None, "loo_rms_h": None,
                         "selected": False, "note": str(e)})

    # ---- 选优 ----
    feasible_xy = [r for r in rows if r["feasible"] and r.get("rms_xy") is not None]
    if not plane_force and feasible_xy:
        best = min(feasible_xy, key=lambda r: r["rms_xy"])
        best["selected"] = True
    elif plane_force:
        for r in rows:
            r["selected"] = (r["method"] == plane_force and r["feasible"])
    return rows


def _ecef_to_latlon(xyz, ell):
    from app.core.ellipsoid import ecef_to_geodetic
    return ecef_to_geodetic(xyz[:, 0], xyz[:, 1], xyz[:, 2], ell)


def build_param_dict(name: str, row: dict, ell_name: str = "WGS84",
                     l0: float | None = None, heightfit: HeightFit | None = None,
                     note: str = "", points_used: list | None = None) -> dict:
    """把 compare_methods 的选中行打包为参数库 JSON（coordparam/1）。"""
    from datetime import datetime

    m = row["method"]
    param = {
        "schema": "coordparam/1",
        "name": name,
        "kind": m,
        "source_kind": None,
        "target_kind": None,
        "ellipsoid": ell_name,
        "projection": None,
        "planar4": None,
        "seven": None,
        "heightfit": None,
        "accuracy": {"rms_xy_m": row.get("rms_xy"), "max_xy_m": row.get("max_xy"),
                     "rms_h_m": (heightfit.rms if heightfit else None),
                     "loo_rms_h_m": (heightfit.loo_rms if heightfit else None),
                     "n_points": row.get("n"), "note": row.get("note", "")},
        "created": datetime.now().isoformat(timespec="seconds"),
        "points_used": points_used or [],
        "note": note,
    }
    if heightfit is not None:
        param["heightfit"] = {"mode": heightfit.mode, "space": heightfit.space,
                              "value_type": heightfit.value_type,
                              "coef": [float(c) for c in heightfit.coef],
                              "center": [float(heightfit.center[0]), float(heightfit.center[1])],
                              "loo_rms": heightfit.loo_rms}
    if m == "planar4" and row.get("_fit4") is not None:
        f4 = row["_fit4"]
        param["planar4"] = {"dx": f4.param.dx, "dy": f4.param.dy, "a": f4.param.a, "b": f4.param.b}
    if m == "chain74" and row.get("_fit4") is not None:
        f4 = row["_fit4"]
        param["kind"] = "chain74"
        param["projection"] = {"l0": l0, "x0": 0.0, "y0": 500000.0, "k": 1.0}
        param["planar4"] = {"dx": f4.param.dx, "dy": f4.param.dy, "a": f4.param.a, "b": f4.param.b}
    return param
