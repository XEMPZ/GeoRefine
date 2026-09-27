"""高斯-克吕格投影（横轴墨卡托）正反算，Snyder 6 阶级数。

约定：x=北坐标(N，含 x0 假北)，y=东坐标(E，含 y0 假东，国内惯例 500000)。
返回 (x_north, y_east)。正反算使用同一套级数，往返闭合 <0.1mm（3°带内）。
"""
from __future__ import annotations

import numpy as np

from app.core.ellipsoid import Ellipsoid, CGCS2000

_D2R = np.pi / 180.0
_R2D = 180.0 / np.pi


def _meridian_arc(lat_rad, ell: Ellipsoid):
    """子午线弧长（自赤道起），Snyder 级数。"""
    e2 = ell.e2
    a1 = 1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256 - 175 * e2 ** 4 / 16384
    a2 = 3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024 + 105 * e2 ** 4 / 32768
    a4 = 15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024 + 525 * e2 ** 4 / 16384
    a6 = 35 * e2 ** 3 / 3072 + 175 * e2 ** 4 / 12288
    a8 = 315 * e2 ** 4 / 131072
    return ell.a * (a1 * lat_rad - a2 * np.sin(2 * lat_rad) + a4 * np.sin(4 * lat_rad)
                    - a6 * np.sin(6 * lat_rad) + a8 * np.sin(8 * lat_rad))


def gauss_kruger(lat_deg, lon_deg, l0_deg, ell: Ellipsoid = CGCS2000,
                 x0: float = 0.0, y0: float = 500000.0, k: float = 1.0):
    """高斯正算: (B, L) → (x_北, y_东)。"""
    lat = np.asarray(lat_deg, dtype=float) * _D2R
    dlon = np.asarray(lon_deg, dtype=float) - l0_deg
    ep2 = ell.e2 / (1.0 - ell.e2)
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    tan_lat = np.tan(lat)
    N = ell.a / np.sqrt(1.0 - ell.e2 * sin_lat ** 2)
    T = tan_lat ** 2
    C = ep2 * cos_lat ** 2
    A = dlon * _D2R * cos_lat
    M = _meridian_arc(lat, ell)
    x = x0 + k * (M + N * tan_lat * (A ** 2 / 2
                  + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
                  + (61 - 58 * T + T ** 2 + 270 * C - 330 * C ** 2) * A ** 6 / 720))
    y = y0 + k * N * (
        A + (1 - T + C) * A ** 3 / 6
        + (5 - 18 * T + T ** 2 + 14 * C - 58 * C ** 2) * A ** 5 / 120)
    if np.isscalar(lat_deg) and np.isscalar(lon_deg):
        return float(x), float(y)
    return x, y


def gauss_kruger_inverse(x_north, y_east, l0_deg, ell: Ellipsoid = CGCS2000,
                         x0: float = 0.0, y0: float = 500000.0, k: float = 1.0):
    """高斯反算: (x_北, y_东) → (B, L)。

    级数反算做初值，牛顿迭代数值反演正算，往返闭合 < 1e-9°（实际 ~1e-12°）。
    正算绝对精度已与 PROJ 对照验证。
    """
    x = np.asarray(x_north, dtype=float)
    y = np.asarray(y_east, dtype=float)
    lat, lon = _series_inverse(x, y, l0_deg, ell, x0, y0, k)
    # 牛顿迭代精化（数值雅可比）
    d = 1e-6
    for _ in range(6):
        fx, fy = gauss_kruger(lat, lon, l0_deg, ell, x0, y0, k)
        fx1, fy1 = gauss_kruger(lat + d, lon, l0_deg, ell, x0, y0, k)
        fx2, fy2 = gauss_kruger(lat, lon + d, l0_deg, ell, x0, y0, k)
        j00, j01 = (fx1 - fx) / d, (fx2 - fx) / d
        j10, j11 = (fy1 - fy) / d, (fy2 - fy) / d
        det = j00 * j11 - j01 * j10
        rx = x - fx
        ry = y - fy
        dlat = (rx * j11 - ry * j01) / det
        dlon = (ry * j00 - rx * j10) / det
        lat = lat + dlat
        lon = lon + dlon
        if np.max(np.abs(dlat)) < 1e-12 and np.max(np.abs(dlon)) < 1e-12:
            break
    if np.isscalar(x_north) and np.isscalar(y_east):
        return float(lat), float(lon)
    return lat, lon


def _series_inverse(x, y, l0_deg, ell: Ellipsoid, x0, y0, k):
    """Snyder 6 阶级数反算（作牛顿迭代初值）。"""
    e2 = ell.e2
    ep2 = e2 / (1.0 - e2)
    M = (x - x0) / k
    mu = M / (ell.a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256 - 175 * e2 ** 4 / 16384))
    e1 = (1 - np.sqrt(1 - e2)) / (1 + np.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * np.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * np.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * np.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * np.sin(8 * mu))
    sin1 = np.sin(phi1)
    cos1 = np.cos(phi1)
    tan1 = np.tan(phi1)
    C1 = ep2 * cos1 ** 2
    T1 = tan1 ** 2
    N1 = ell.a / np.sqrt(1 - e2 * sin1 ** 2)
    R1 = ell.a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5
    D = (y - y0) / (N1 * k)
    lat = phi1 - (N1 * tan1 / R1) * (D ** 2 / 2
          - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
          + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = l0_deg * _D2R + (D - (1 + 2 * T1 + C1) * D ** 3 / 6
          + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120) / cos1
    return lat * _R2D, lon * _R2D


def auto_central_meridian(lon_deg: float, band: int = 3) -> float:
    """按经度自动定中央子午线。band=3: round(L/3)*3；band=6: 6n-3。"""
    if band == 3:
        return float(round(lon_deg / 3.0) * 3.0)
    if band == 6:
        return float((int(lon_deg // 6) * 6 + 3))
    raise ValueError("band 仅支持 3 或 6")


# ---------------------------------------------------------------- 带号识别与逆向反算
def detect_gauss_zone(y_east) -> dict | None:
    """识别含带号的高斯平面 y（东坐标）；非带号坐标返回 None。

    中国惯例：y = 带号×1e6 + (自然y + 500000)。带号 13~23 → 6°带（L0=6n−3），
    24~45 → 3°带（L0=3n）；其余带号不属于中国常用范围，不识别（交由人工填写）。
    返回 {"zone", "band", "l0", "y_stripped"}。
    """
    y = float(y_east)
    if y < 1.0e7:          # 未含带号：自然 y+500000 至多 7 位（<1e7 不会误伤加常数后的大 y）
        return None
    zone = int(y // 1_000_000)
    if 13 <= zone <= 23:
        band, l0 = "6度带", float(zone * 6 - 3)
    elif 24 <= zone <= 45:
        band, l0 = "3度带", float(zone * 3)
    else:
        return None
    return {"zone": zone, "band": band, "l0": l0,
            "y_stripped": y - zone * 1_000_000}


def detect_zone_on_column(ys) -> dict | None:
    """对整列 y 做带号识别（逐点，取首个命中；坐标应同带）。"""
    for y in np.asarray(ys, dtype=float):
        z = detect_gauss_zone(y)
        if z is not None:
            return z
    return None


def inverse_gauss_points(xs, ys, l0_deg, ell: Ellipsoid = CGCS2000,
                         x0_m: float = 0.0, y0_m: float = 500000.0,
                         h0: float = 0.0, rigorous: bool = False):
    """批量高斯反算（含投影面大地高 h0 与严密/近似工程椭球，与控制点解算同口径）。

    xs/ys: 平面 (x北, y东)（m）；l0_deg 中央子午线（度）。
    严密椭球的 Ra(B0) 依赖平均纬度，做两遍迭代：粗估 b0 → 反算 → 以平均纬度重算。
    返回 [(B_deg, L_deg), ...]。
    """
    import math as _m

    from app.core.gk_engine import ProjParams

    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    b0 = float(np.mean(xs)) / 111132.0 if len(xs) else 30.0     # 纬度粗估
    out = []
    for _ in range(2):
        pp = ProjParams(ell.a, ell.inv_f, _m.radians(float(l0_deg)), _m.radians(b0),
                        float(h0 or 0.0), 1.0,
                        x0_km=float(x0_m) / 1000.0, y0_km=float(y0_m) / 1000.0,
                        rigorous=bool(rigorous))
        out = [pp.inverse(float(x), float(y)) for x, y in zip(xs, ys)]
        b0 = float(np.degrees(np.mean([o[0] for o in out])))
    return [(_m.degrees(b), _m.degrees(l)) for b, l in out]
