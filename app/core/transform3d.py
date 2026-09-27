"""空间七参数布尔莎（Bursa-Wolf）转换。

线性化模型（旋转小角，单位弧度；解算未知数 [dX, dY, dZ, m, Rx, Ry, Rz]）:
  X2 = dX + (1+m)X1 − Rz·Y1 + Ry·Z1
  Y2 = dY + (1+m)Y1 + Rz·X1 − Rx·Z1
  Z2 = dZ + (1+m)Z1 − Ry·X1 + Rx·Y1
输出：平移米；旋转角秒（×206264.8062）；尺度 ppm（m×1e6）。
最少 3 点（9 行 ≥ 7 未知数），少于 4 点提示无检核条件。
正算 apply 使用完整旋转矩阵形式，与线性化拟合在毫弧度以下自洽。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_SEC_PER_RAD = 206264.80624709636


@dataclass
class Param7:
    dx: float
    dy: float
    dz: float
    rx_s: float   # 角秒
    ry_s: float
    rz_s: float
    scale_ppm: float

    @property
    def rx_rad(self) -> float:
        return self.rx_s / _SEC_PER_RAD

    @property
    def ry_rad(self) -> float:
        return self.ry_s / _SEC_PER_RAD

    @property
    def rz_rad(self) -> float:
        return self.rz_s / _SEC_PER_RAD

    @property
    def scale(self) -> float:
        return self.scale_ppm * 1e-6


@dataclass
class FitResult7:
    param: Param7
    res_xyz: np.ndarray         # (n,3)
    rms_3d: float
    max_3d: float
    n: int
    note: str = field(default="")


def fit_7param(src_xyz, dst_xyz) -> FitResult7:
    src = np.asarray(src_xyz, dtype=float)
    dst = np.asarray(dst_xyz, dtype=float)
    if src.ndim != 2 or src.shape[1] != 3 or src.shape != dst.shape:
        raise ValueError("控制点必须为 (n,3) 的 XYZ 数组且源/目标形状一致")
    n = src.shape[0]
    if n < 3:
        raise ValueError(f"七参数至少需要 3 个控制点，当前 {n} 个")
    X, Y, Z = src[:, 0], src[:, 1], src[:, 2]
    A = np.zeros((3 * n, 7))
    b = np.zeros(3 * n)
    A[0::3, 0] = 1.0
    A[0::3, 3] = X
    A[0::3, 6] = -Y
    A[0::3, 5] = Z
    A[1::3, 1] = 1.0
    A[1::3, 3] = Y
    A[1::3, 4] = -Z
    A[1::3, 6] = X
    A[2::3, 2] = 1.0
    A[2::3, 3] = Z
    A[2::3, 4] = Y
    A[2::3, 5] = -X
    b[0::3] = dst[:, 0] - X
    b[1::3] = dst[:, 1] - Y
    b[2::3] = dst[:, 2] - Z
    k, *_ = np.linalg.lstsq(A, b, rcond=None)
    p = Param7(dx=float(k[0]), dy=float(k[1]), dz=float(k[2]),
               rx_s=float(k[4] * _SEC_PER_RAD), ry_s=float(k[5] * _SEC_PER_RAD),
               rz_s=float(k[6] * _SEC_PER_RAD), scale_ppm=float(k[3] * 1e6))
    res = apply_7param(p, X, Y, Z)
    res_xyz = np.column_stack([res[0] - dst[:, 0], res[1] - dst[:, 1], res[2] - dst[:, 2]])
    d = np.linalg.norm(res_xyz, axis=1)
    note = ""
    if n == 3:
        note = "恰好 3 点，无检核条件（解算自由度为 2，建议 ≥4 点）"
    return FitResult7(p, res_xyz, float(np.sqrt(np.mean(d ** 2))), float(np.max(d)), n, note)


def apply_7param(p: Param7, x, y, z):
    """七参数正算（完整旋转矩阵形式）。"""
    rx, ry, rz = p.rx_rad, p.ry_rad, p.rz_rad
    # R = Rz(−rz)? — 按模型定义: X2 = dX + (1+m)(X − Rz·Y + Ry·Z) 等，
    # 即 R = [[1, −rz, ry], [rz, 1, −rx], [−ry, rx, 1]]（小角一阶）
    R = np.array([
        [1.0, -rz, ry],
        [rz, 1.0, -rx],
        [-ry, rx, 1.0],
    ])
    s = 1.0 + p.scale
    pts = np.stack([np.atleast_1d(np.asarray(x, dtype=float)),
                    np.atleast_1d(np.asarray(y, dtype=float)),
                    np.atleast_1d(np.asarray(z, dtype=float))], axis=1)
    out = pts @ R.T * s + np.array([p.dx, p.dy, p.dz])
    scalar = np.isscalar(x) and np.isscalar(y) and np.isscalar(z)
    if scalar:
        return float(out[0, 0]), float(out[0, 1]), float(out[0, 2])
    return out[:, 0], out[:, 1], out[:, 2]


_SEC = 206264.80624709636


def fit_7param_refined(src_xyz, dst_xyz, iters: int = 8) -> FitResult7:
    """七参数解算 + 高斯-牛顿精化。

    fit_7param 为一阶线性化解算，旋转角较大（>1 mrad，如跨带换带固有的
    子午线收敛角差）时线性化与精确旋转矩阵存在二阶差；本函数以精确式
    apply_7param 为基准做高斯-牛顿迭代，收敛后与精确式自洽。
    """
    f = fit_7param(src_xyz, dst_xyz)
    p = f.param
    src = np.asarray(src_xyz, dtype=float)
    dst = np.asarray(dst_xyz, dtype=float)
    X, Y, Z = src[:, 0], src[:, 1], src[:, 2]
    A = np.zeros((3 * len(X), 7))
    A[0::3, 0] = 1; A[0::3, 3] = X; A[0::3, 6] = -Y; A[0::3, 5] = Z
    A[1::3, 1] = 1; A[1::3, 3] = Y; A[1::3, 4] = -Z; A[1::3, 6] = X
    A[2::3, 2] = 1; A[2::3, 3] = Z; A[2::3, 4] = Y; A[2::3, 5] = -X
    for _ in range(iters):
        ox, oy, oz = apply_7param(p, X, Y, Z)
        res = np.column_stack([ox - dst[:, 0], oy - dst[:, 1], oz - dst[:, 2]]).ravel()
        dk, *_ = np.linalg.lstsq(A, -res, rcond=None)
        p.dx += dk[0]; p.dy += dk[1]; p.dz += dk[2]
        p.scale_ppm += dk[3] * 1e6
        p.rx_s += dk[4] * _SEC; p.ry_s += dk[5] * _SEC; p.rz_s += dk[6] * _SEC
        if np.max(np.abs(dk[:3])) < 1e-13 and np.max(np.abs(dk[3:])) < 1e-12:
            break
    ox, oy, oz = apply_7param(p, X, Y, Z)
    res_xyz = np.column_stack([ox - dst[:, 0], oy - dst[:, 1], oz - dst[:, 2]])
    d = np.linalg.norm(res_xyz, axis=1)
    return FitResult7(p, res_xyz, float(np.sqrt(np.mean(d ** 2))), float(np.max(d)),
                      len(X), "高斯-牛顿精化（大旋转角自洽）")


def blh_to_xyz_arr(lat_arr, lon_arr, h_arr, ell):
    from app.core.ellipsoid import geodetic_to_ecef
    return geodetic_to_ecef(lat_arr, lon_arr, h_arr, ell)


def xyz_to_blh_arr(x_arr, y_arr, z_arr, ell):
    from app.core.ellipsoid import ecef_to_geodetic
    return ecef_to_geodetic(x_arr, y_arr, z_arr, ell)


# ================================================================ 三参数（3 平移）
@dataclass
class Param3:
    dx: float
    dy: float
    dz: float


@dataclass
class FitResult3:
    param: Param3
    res_xyz: np.ndarray         # (n,3)
    rms_3d: float
    max_3d: float
    n: int
    note: str = field(default="")


def fit_3param(src_xyz, dst_xyz) -> FitResult3:
    """空间三参数（仅平移 dX/dY/dZ，无旋转无尺度）：最小二乘解 = 坐标差均值。

    适用于同尺度同轴向、仅原点平移的小范围基准归算（如 WGS84→北京54 的粗略口径）。
    """
    src = np.asarray(src_xyz, dtype=float)
    dst = np.asarray(dst_xyz, dtype=float)
    if src.ndim != 2 or src.shape[1] != 3 or src.shape != dst.shape:
        raise ValueError("控制点必须为 (n,3) 的 XYZ 数组且源/目标形状一致")
    n = src.shape[0]
    if n < 1:
        raise ValueError("三参数至少需要 1 个控制点，当前 0 个")
    d = dst - src
    t = d.mean(axis=0)
    p = Param3(dx=float(t[0]), dy=float(t[1]), dz=float(t[2]))
    res = apply_3param(p, src[:, 0], src[:, 1], src[:, 2])
    res_xyz = np.column_stack([res[0] - dst[:, 0], res[1] - dst[:, 1], res[2] - dst[:, 2]])
    dd = np.linalg.norm(res_xyz, axis=1)
    note = "仅平移，无旋转/尺度；单点解算无检核" if n == 1 else "仅平移，无旋转/尺度"
    return FitResult3(p, res_xyz, float(np.sqrt(np.mean(dd ** 2))), float(np.max(dd)), n, note)


def apply_3param(p: Param3, x, y, z):
    """三参数正算：XYZ2 = XYZ1 + (dX, dY, dZ)。"""
    x = np.asarray(x, dtype=float) + p.dx
    y = np.asarray(y, dtype=float) + p.dy
    z = np.asarray(z, dtype=float) + p.dz
    scalar = np.isscalar(x) or (np.asarray(x).ndim == 0)
    if scalar:
        return float(x), float(y), float(z)
    return x, y, z
