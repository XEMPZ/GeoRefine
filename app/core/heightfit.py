"""高程拟合（兼容中海达 GNSSTools 手簿约定）。

观测值 v：value_type='xi' 时 v = 大地高 − 正常高（高程异常）；'dh' 时 v = 目标高 − 源高。
模式（对应 GNSSTools GetHFixPar mode 1/2/3）:
  const     固定差   v = A                                  最少 1 点
  plane     平面     v = A + B·Δx + C·Δy                    最少 3 点
  quadratic 二次曲面 v = A + B·Δx + C·Δy + D·Δx² + E·Δy² + F·Δx·Δy   最少 6 点
中心点约定 center_ref='last'：取最后一个控制点坐标（与手簿逐点一致）；'centroid' 取重心。
点数不足抛 ValueError，不静默降级。loo_rms = 留一交叉验证 RMS（自动定阶依据）。

坐标空间 space：'planar' → coords=(x北, y东)；'lonlat' → coords=(lon, lat)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_POINTS = {"const": 1, "plane": 3, "quadratic": 6}
_DESIGN = {
    "const": lambda dx, dy: np.column_stack([np.ones_like(dx)]),
    "plane": lambda dx, dy: np.column_stack([np.ones_like(dx), dx, dy]),
    "quadratic": lambda dx, dy: np.column_stack([np.ones_like(dx), dx, dy, dx ** 2, dy ** 2, dx * dy]),
}


@dataclass
class HeightFit:
    mode: str                 # const | plane | quadratic
    value_type: str           # xi | dh
    space: str                # planar | lonlat
    coef: np.ndarray
    center: tuple             # (cx, cy)
    loo_rms: float | None = None
    note: str = field(default="")
    rms: float | None = None  # 内符合 RMS


def _design(mode: str, coords: np.ndarray, center: tuple) -> np.ndarray:
    dx = coords[:, 0] - center[0]
    dy = coords[:, 1] - center[1]
    return _DESIGN[mode](dx, dy)


def fit_height(coords, values, mode: str, value_type: str, space: str,
               center_ref: str = "last") -> HeightFit:
    """拟合高程转换面。coords:(n,2) values:(n,)"""
    coords = np.asarray(coords, dtype=float)
    values = np.asarray(values, dtype=float).ravel()   # 允许 (n,1) 列向量
    if coords.ndim != 2 or coords.shape[1] != 2 or coords.shape[0] != values.shape[0]:
        raise ValueError("coords 必须为 (n,2)，values 为 (n,) 且行数一致")
    if mode not in MIN_POINTS:
        raise ValueError(f"未知拟合模式: {mode}")
    n = coords.shape[0]
    if n < MIN_POINTS[mode]:
        raise ValueError(f"{mode} 拟合至少需要 {MIN_POINTS[mode]} 个点，当前 {n} 个")
    if center_ref == "last":
        center = (float(coords[-1, 0]), float(coords[-1, 1]))
    elif center_ref == "centroid":
        center = (float(coords[:, 0].mean()), float(coords[:, 1].mean()))
    else:
        raise ValueError("center_ref 仅支持 'last' 或 'centroid'")
    A = _design(mode, coords, center)
    if np.linalg.matrix_rank(A) < A.shape[1]:
        raise ValueError(
            f"设计矩阵秩亏（rank={np.linalg.matrix_rank(A)} < {A.shape[1]}）：控制点可能共线/重合，"
            f"{mode} 拟合无唯一解。请调整控制点分布或降低拟合阶数")
    coef, *_ = np.linalg.lstsq(A, values, rcond=None)
    res = A @ coef - values
    rms = float(np.sqrt(np.mean(res ** 2)))
    loo = _loo_rms(mode, coords, values, center)
    return HeightFit(mode=mode, value_type=value_type, space=space,
                     coef=coef, center=center, loo_rms=loo, rms=rms,
                     note=f"center_ref={center_ref}, n={n}")


def eval_height(f: HeightFit, x, y):
    """求拟合值 v。x,y 顺序须与 f.space 匹配（planar: x北,y东；lonlat: lon,lat）。"""
    dx = np.asarray(x, dtype=float) - f.center[0]
    dy = np.asarray(y, dtype=float) - f.center[1]
    scalar = (dx.ndim == 0)
    if scalar:
        dx, dy = dx[None], dy[None]
    row = _DESIGN[f.mode](dx, dy)
    v = row @ f.coef
    if scalar:
        return float(v[0])
    return v


def _loo_rms(mode: str, coords: np.ndarray, values: np.ndarray, center: tuple) -> float:
    """留一交叉验证 RMS。"""
    n = coords.shape[0]
    if n <= MIN_POINTS[mode]:
        return float("nan")
    errs = []
    for i in range(n):
        m = np.ones(n, dtype=bool)
        m[i] = False
        A = _design(mode, coords[m], center)
        coef, *_ = np.linalg.lstsq(A, values[m], rcond=None)
        A1 = _design(mode, coords[i:i + 1], center)
        errs.append(float((A1 @ coef)[0] - values[i]))
    return float(np.sqrt(np.mean(np.square(errs))))


def select_best_fit(coords, values, value_type: str, space: str,
                    center_ref: str = "last") -> HeightFit:
    """自动选阶：可行模式全部拟合，取 LOO RMS 最小者（LOO 相近取低阶防过拟合）。"""
    candidates = []
    for mode in ("const", "plane", "quadratic"):
        try:
            f = fit_height(coords, values, mode, value_type, space, center_ref)
        except ValueError:
            continue
        loo = f.loo_rms
        candidates.append((loo if loo == loo else float("inf"), mode, f))
    if not candidates:
        raise ValueError("无可用拟合模式（点数不足）")
    candidates.sort(key=lambda t: (t[0], MIN_POINTS[t[1]]))
    best_loo, best_mode, best = candidates[0]
    parts = [f"{m}: loo={loo:.4f}" if loo == loo else f"{m}: loo=n/a" for loo, m, _ in candidates]
    best.note += "; LOO对比[" + ", ".join(parts) + "]"
    return best
