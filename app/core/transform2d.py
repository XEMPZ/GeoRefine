"""平面四参数转换（x=北, y=东）。

模型:
  x2 = dx + a·x1 − b·y1
  y2 = dy + b·x1 + a·y1
其中 a=(1+m)cosα, b=(1+m)sinα；scale=hypot(a,b)，旋转角 α=atan2(b,a)。
最小二乘解算（每点 2 行设计矩阵，未知数 [a, b, dx, dy]），最少 2 点。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_SEC_PER_RAD = 206264.80624709636


@dataclass
class Param4:
    dx: float
    dy: float
    a: float
    b: float

    @property
    def scale(self) -> float:
        return float(np.hypot(self.a, self.b))

    @property
    def rot_rad(self) -> float:
        return float(np.arctan2(self.b, self.a))

    @property
    def rot_sec(self) -> float:
        return self.rot_rad * _SEC_PER_RAD


@dataclass
class FitResult4:
    param: Param4
    res_xy: np.ndarray          # (n,2) 残差 [dx2, dy2]
    rms_xy: float
    max_xy: float
    n: int
    note: str = field(default="")


def fit_4param(src_xy, dst_xy) -> FitResult4:
    """平面四参数最小二乘拟合。src_xy/dst_xy: (n,2)，x=北、y=东。"""
    src = np.asarray(src_xy, dtype=float)
    dst = np.asarray(dst_xy, dtype=float)
    if src.ndim != 2 or src.shape[1] != 2 or src.shape != dst.shape:
        raise ValueError("控制点必须为 (n,2) 数组且源/目标形状一致")
    n = src.shape[0]
    if n < 2:
        raise ValueError(f"四参数至少需要 2 个控制点，当前 {n} 个")
    # 重心化：绝对坐标大而相对散布小时，平移与尺度/旋转强相关（米级误差），
    # 先减去重心解算再回换（与手簿内部做法一致）
    cx, cy = float(src[:, 0].mean()), float(src[:, 1].mean())
    xs, ys = src[:, 0] - cx, src[:, 1] - cy
    x1, y1 = xs, ys
    x2, y2 = dst[:, 0], dst[:, 1]
    # 未知数 k = [a, b, dx, dy]
    A = np.zeros((2 * n, 4))
    A[0::2, 0] = x1
    A[0::2, 1] = -y1
    A[0::2, 2] = 1.0
    A[1::2, 0] = y1
    A[1::2, 1] = x1
    A[1::2, 3] = 1.0
    b = np.zeros(2 * n)
    b[0::2] = x2
    b[1::2] = y2
    k, *_ = np.linalg.lstsq(A, b, rcond=None)
    a_, b_, dx_, dy_ = float(k[0]), float(k[1]), float(k[2]), float(k[3])
    # 回换平移: dx = dx' − a·cx + b·cy ; dy = dy' − b·cx − a·cy
    p = Param4(dx=dx_ - a_ * cx + b_ * cy, dy=dy_ - b_ * cx - a_ * cy, a=a_, b=b_)
    res = apply_4param(p, src[:, 0], src[:, 1])
    res_xy = np.column_stack([res[0] - x2, res[1] - y2])
    d = np.hypot(res_xy[:, 0], res_xy[:, 1])
    note = ""
    if n == 2:
        note = "恰好 2 点，无检核条件（残差恒为 0）"
    return FitResult4(p, res_xy, float(np.sqrt(np.mean(d ** 2))), float(np.max(d)), n, note)


def apply_4param(p: Param4, x, y):
    """四参数正算。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x2 = p.dx + p.a * x - p.b * y
    y2 = p.dy + p.b * x + p.a * y
    if np.isscalar(x) and np.isscalar(y):
        return float(x2), float(y2)
    return x2, y2


def to_survey_xy(X_cad, Y_cad):
    """CAD 坐标 (X=东, Y=北) → 测量坐标 (x=北, y=东)。"""
    return Y_cad, X_cad


def fit_3param2d(src_xy, dst_xy) -> FitResult4:
    """三参数（仅平移，无尺度/旋转）最小二乘。

    适用于：同椭球同基准的精确三维坐标归算（高精度坐标对）——理论上不存在
    尺度与旋转，四参数会把毫米级噪声吸进 K/α 造成过拟合。
    返回 FitResult4（param.a=1, param.b=0，可直接用 apply_4param 应用）。
    """
    s = np.asarray(src_xy, dtype=float)
    d = np.asarray(dst_xy, dtype=float)
    if s.shape[0] < 1:
        raise ValueError("三参数至少需要 1 个点")
    dd = d - s
    dx, dy = float(np.mean(dd[:, 0])), float(np.mean(dd[:, 1]))
    res = dd - np.array([dx, dy])
    rms = float(np.sqrt(np.mean(np.sum(res ** 2, axis=1)))) if len(res) else 0.0
    return FitResult4(Param4(dx, dy, 1.0, 0.0), res, rms,
                      float(np.max(np.hypot(res[:, 0], res[:, 1]))) if len(res) else 0.0,
                      len(s), "三参数（仅平移，无尺度/旋转）")


def fit_3param2d(src_xy, dst_xy) -> FitResult4:
    """三参数（仅平移，无尺度/旋转）最小二乘。

    适用于：同椭球同基准的精确三维坐标归算（高精度坐标对）——理论上不存在
    尺度与旋转，四参数会把毫米级噪声吸进 K/α 造成过拟合。
    返回 FitResult4（param.a=1, param.b=0，可直接用 apply_4param 应用）。
    """
    s = np.asarray(src_xy, dtype=float)
    d = np.asarray(dst_xy, dtype=float)
    if s.shape[0] < 1:
        raise ValueError("三参数至少需要 1 个点")
    dd = d - s
    dx, dy = float(np.mean(dd[:, 0])), float(np.mean(dd[:, 1]))
    res = dd - np.array([dx, dy])
    rms = float(np.sqrt(np.mean(np.sum(res ** 2, axis=1)))) if len(res) else 0.0
    return FitResult4(Param4(dx, dy, 1.0, 0.0), res, rms,
                      float(np.max(np.hypot(res[:, 0], res[:, 1]))) if len(res) else 0.0,
                      len(s), "三参数（仅平移，无尺度/旋转）")


def to_cad_xy(x_north, y_east):
    """测量坐标 (x=北, y=东) → CAD 坐标 (X=东, Y=北)。"""
    return y_east, x_north
