"""参数应用批量转换（ApplyPage 的三支路计算）。纯逻辑，无 Qt 依赖。"""
from __future__ import annotations


def apply_points(names, vals_in, param_doc: dict, out_lonlat: bool, src_lonlat: bool,
                 kind: str, do_plane: bool, do_height: bool, ell):
    """按参数文档批量转换。返回 (results, error)。

    results: [{"name", "vals", "fmts"}]；error: (name, msg)。
    kind: "seven"/"three"（out_lonlat 分支使用）。
    """
    import numpy as np
    from app.core import anglefmt
    from app.core.ellipsoid import geodetic_to_ecef, ecef_to_geodetic
    from app.core.transform3d import Param7, apply_7param, Param3, apply_3param
    from app.common.params import ParamApplier

    applier = ParamApplier(param_doc)
    results = []
    for name, v in zip(names, vals_in):
        a1, a2, h = v
        try:
            if kind in ("inv_direct", "inv_chain3", "inv_chain"):
                # 平面（工程坐标）→ 大地（逆向链）：源投影反算 + 三/七参数
                b2, l2, h2 = applier.apply_planar_to_lonlat(a1, a2, h)
                o1, o2 = anglefmt.rad_to_dms(b2), anglefmt.rad_to_dms(l2)
                vals, fmts = [o1, o2, h2], ["%.8f", "%.8f", "%.4f"]
            elif out_lonlat:  # seven / three：BLH → BLH（三维模型，平面高程同时转换）
                X, Y, Z = geodetic_to_ecef(a1, a2, h, ell)
                if kind == "seven":
                    p7 = Param7(param_doc["seven"]["dx"], param_doc["seven"]["dy"],
                                param_doc["seven"]["dz"], param_doc["seven"]["rx_s"],
                                param_doc["seven"]["ry_s"], param_doc["seven"]["rz_s"],
                                param_doc["seven"]["scale_ppm"])
                    X2, Y2, Z2 = apply_7param(p7, X, Y, Z)
                else:
                    p3 = Param3(param_doc["three"]["dx"], param_doc["three"]["dy"],
                                param_doc["three"]["dz"])
                    X2, Y2, Z2 = apply_3param(p3, X, Y, Z)
                b2, l2, h2 = ecef_to_geodetic(X2, Y2, Z2, ell)
                o1, o2 = anglefmt.rad_to_dms(b2), anglefmt.rad_to_dms(l2)
                vals, fmts = [o1, o2, h2], ["%.8f", "%.8f", "%.4f"]
            elif src_lonlat:  # chain74 / direct_gk：大地 → 平面(正常高)
                x2, y2, h2 = applier.apply_lonlat(a1, a2, h,
                                                  do_plane=do_plane, do_height=do_height)
                vals, fmts = [x2, y2, h2], ["%.4f", "%.4f", "%.4f"]
            else:  # planar3 / planar4 / seven2d：平面 → 平面(正常高)
                x2, y2, h2 = applier.apply_planar(a1, a2, h,
                                                  do_plane=do_plane, do_height=do_height)
                vals, fmts = [x2, y2, h2], ["%.4f", "%.4f", "%.4f"]
        except Exception as e:  # noqa: BLE001
            return results, (name, str(e))
        results.append({"name": name, "vals": vals, "fmts": fmts})
    return results, None
