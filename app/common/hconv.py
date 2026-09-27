"""高程转换统一入口（主线维护）。照片/CAD/精度对比共用。

mode:
  photo: 'geoid'（GridModel，按 lon,lat 查 ξ，正常高=大地高-ξ）| 'param'（heightfit, space=lonlat）| 'offset'
  cad:   'param'（heightfit, space=planar）| 'offset' | 'none'
"""
from __future__ import annotations

from typing import Callable


def make_photo_height_fn(mode: str, model=None, param=None, offset: float = 0.0
                         ) -> Callable[[float, float, float], tuple]:
    """返回 fn(lon, lat, h_ell) -> (h_new, v, detail)。h_new 为 None 表示不可转换。"""

    def _from_param(lon: float, lat: float, h: float):
        from app.common.params import ParamApplier

        hf = (param or {}).get("heightfit")
        if not hf:
            return h, None, "参数中无高程拟合"
        ap = ParamApplier(param)
        space = hf.get("space", "planar")
        cx, cy = (lon, lat) if space == "lonlat" else (None, None)
        if cx is None:
            return None, None, "拟合参数为平面空间，无法用于照片经纬度"
        v = ap.eval_height_value(cx, cy)
        if v is None:
            return None, None, "拟合求值失败"
        h_new = h - v if hf.get("value_type", "xi") == "xi" else h + v
        return h_new, v, f"拟合参数({hf.get('mode')})"

    if mode == "geoid":
        if model is None:
            raise ValueError("geoid 模式需要 GridModel")

        def fn(lon, lat, h):
            xi = model.try_undulation(lon, lat)
            if xi is None:
                return None, None, f"超出模型范围({model.grid.name})"
            return h - xi, xi, f"格网模型:{model.grid.name}"
        return fn

    if mode == "param":
        return _from_param

    if mode == "offset":
        return lambda lon, lat, h: (h + offset, offset, f"常数偏移{offset:+.4f}m")

    raise ValueError(f"未知照片高程转换模式: {mode}")


def make_cad_height_fn(mode: str, param=None, model=None, offset: float = 0.0,
                       apply_2d: bool = False) -> Callable[[float, float, float], tuple]:
    """返回 fn(X_cad, Y_cad, z) -> (z_new, v, detail)。z_new 为 None 表示该点不可转换。"""

    if mode == "none":
        return lambda X, Y, z: (z, None, "不转换高程")

    def _from_param(X, Y, z):
        from app.common.params import ParamApplier

        hf = (param or {}).get("heightfit")
        if not hf:
            return z, None, "参数中无高程拟合"
        ap = ParamApplier(param)
        space = hf.get("space", "planar")
        if space != "planar":
            # 经纬度空间拟合：用参数自带投影把平面坐标反算回 (B,L) 再求拟合值
            if not ap.proj:
                return None, None, "拟合参数为经纬度空间且无投影参数，无法用于 CAD 平面坐标"
            from app.core.projection import gauss_kruger_inverse

            pj = ap.proj
            lat, lon = gauss_kruger_inverse(Y, X, pj.get("l0"), ell=ap.ell,
                                            x0=pj.get("x0", 0.0), y0=pj.get("y0", 500000.0),
                                            k=pj.get("k", 1.0))
            v = ap.eval_height_value(float(lon), float(lat))
        else:
            # CAD(X东,Y北) → 测量(x北,y东)
            v = ap.eval_height_value(Y, X)
        if v is None:
            return None, None, "拟合求值失败"
        z_new = z - v if hf.get("value_type", "xi") == "xi" else z + v
        return z_new, v, f"拟合参数({hf.get('mode')})"

    if mode == "param":
        return _from_param

    if mode == "offset":
        return lambda X, Y, z: (z + offset, offset, f"常数偏移{offset:+.4f}m")

    raise ValueError(f"未知 CAD 高程转换模式: {mode}")
