"""空间坐标转换计算（XYZ/BLH/xyH 七型互转）。纯逻辑，无 Qt 依赖。"""
from __future__ import annotations

import math


def column_headers(kind: str, u_in: str, u_out: str) -> tuple[list[str], list[str]]:
    """按转换类型与角度格式给出输入/输出表头（不含点号列）。"""
    s, _, d = kind.partition("->")
    col = {"XYZ": ["X(m)", "Y(m)", "Z(m)"],
           "BLH": None,
           "xyH": ["x(m)", "y(m)", "H(m)"]}
    col_in = ["B(%s)" % u_in, "L(%s)" % u_in, "H(m)"] if s == "BLH" else col[s]
    col_out = ["B(%s)" % u_out, "L(%s)" % u_out, "H(m)"] if d == "BLH" else col[d]
    return col_in, col_out


def convert(points: list[tuple], src_t: str, dst_t: str, ell, pj,
            in_dms: bool, out_dms: bool) -> tuple[list[dict], tuple[str, str] | None]:
    """批量转换。

    points: [(name, v1, v2, v3)]；返回 (results, error)。
    results: [{"name", "vals": [o1,o2,o3], "fmts": [f1,f2,f3]}]；
    error: 首个失败点 (name, msg)，无失败为 None。
    """
    from app.core import anglefmt
    from app.core.ellipsoid import geodetic_to_ecef, ecef_to_geodetic

    def get_blh(v1, v2, v3):
        if src_t == "BLH":
            if in_dms:
                return (anglefmt.parse_flexible(v1, "dms"), anglefmt.parse_flexible(v2, "dms"),
                        float(v3))
            return (float(v1) * math.pi / 180.0, float(v2) * math.pi / 180.0, float(v3))
        if src_t == "XYZ":
            return ecef_to_geodetic(float(v1), float(v2), float(v3), ell)
        b, l = pj.inverse(float(v1), float(v2))
        return b, l, float(v3)

    def fmt_bl(b, l):
        if out_dms:
            return anglefmt.rad_to_dms(b), anglefmt.rad_to_dms(l), "%.8f"
        return b * 180 / math.pi, l * 180 / math.pi, "%.9f"

    results = []
    for name, v1, v2, v3 in points:
        try:
            b, l, h = get_blh(v1, v2, v3)
            if dst_t == "BLH":
                o1, o2, f23 = fmt_bl(b, l)
                vals, fmts = [o1, o2, h], [f23, f23, "%.4f"]
            elif dst_t == "xyH":
                x, y = pj.forward(b, l)
                vals, fmts = [x, y, h], ["%.4f", "%.4f", "%.4f"]
            else:
                X, Y, Z = geodetic_to_ecef(b * 180 / math.pi, l * 180 / math.pi, h, ell)
                vals, fmts = [X, Y, Z], ["%.4f", "%.4f", "%.4f"]
        except Exception as e:  # noqa: BLE001
            return results, (name, str(e))
        results.append({"name": name, "vals": vals, "fmts": fmts})
    return results, None
