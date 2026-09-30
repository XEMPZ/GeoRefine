# -*- coding: utf-8 -*-
"""多项式转换的数值自校验。

模型定义（本项目采用的口径）：
- 2D：完全二次 6 项 [1,x,y,x²,y²,xy]，最少 6 点；
- 3D：完全二次 10 项 [1,x,y,z,x²,y²,z²,xy,xz,yz]，最少 10 点；
- 两侧坐标均重心化（减均值）；观测值 = 改正数（新−旧）；
- 求解 = 正规方程 AᵀA·k=AᵀL。

本实现的取法：拟合绝对目标值（常数项吸收改正数，数学等价）、
项序显式存于参数（求值按幂次配对，与项序无关）、lstsq（SVD，较裸求逆稳健）。
本测试用同一模型的独立参考实现，断言两者转换输出一致：良态 <1e-9，
工程大坐标下差异属法方程求解噪声（<0.1mm）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _south_fit_predict(src, dst):
    """按上述模型定义的独立参考实现，返回预测函数。"""
    S = np.asarray(src, float)
    D = np.asarray(dst, float)
    c = S.mean(axis=0)
    R = S - c
    if S.shape[1] == 2:
        A = np.column_stack([np.ones(len(R)), R[:, 0], R[:, 1],
                             R[:, 0] ** 2, R[:, 1] ** 2, R[:, 0] * R[:, 1]])
        terms = [(0, 0), (1, 0), (0, 1), (2, 0), (0, 2), (1, 1)]
    else:
        x, y, z = R[:, 0], R[:, 1], R[:, 2]
        A = np.column_stack([np.ones(len(R)), x, y, z,
                             x * x, y * y, z * z, x * y, x * z, y * z])
        terms = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (2, 0, 0),
                 (0, 2, 0), (0, 0, 2), (1, 1, 0), (1, 0, 1), (0, 1, 1)]
    N = A.T @ A
    ks = [np.linalg.solve(N, A.T @ (D[:, j] - S[:, j])) for j in range(D.shape[1])]

    def pred(p):
        r = np.asarray(p, float) - c
        out = []
        for j, kk in enumerate(ks):
            val = 0.0
            for coef, pw in zip(kk, terms):
                term = 1.0
                for ax, k in enumerate(pw):
                    if k:
                        term *= r[ax] ** k
                val += coef * term
            out.append(p[j] + val)   # 改正数加回源坐标
        return tuple(out)
    return pred


def test_south_crosscheck_2d():
    from app.logic.polynomial import eval_poly, fit_poly
    rng = np.random.RandomState(12)
    src = rng.uniform(-50, 50, (12, 2))
    dst = np.column_stack([1.0002 * src[:, 0] - 0.0001 * src[:, 1] + 12.0 + 3e-6 * src[:, 0] ** 2,
                           0.9998 * src[:, 1] + 8.0 + 2e-6 * src[:, 1] ** 2])
    m = fit_poly(src, dst, 2, "poly2d")
    pred = _south_fit_predict(src, dst)
    diff = max(max(abs(np.array(pred(p)) - np.array(eval_poly(p, m)))) for p in src)
    assert diff < 1e-9, diff


def test_south_crosscheck_3d():
    from app.logic.polynomial import eval_poly, fit_poly
    rng = np.random.RandomState(12)
    src = rng.uniform(-50, 50, (12, 2))
    src3 = np.column_stack([src, rng.uniform(-40, 40, 12)])
    dst3 = np.column_stack([1.0002 * src3[:, 0] + 2e-6 * src3[:, 0] ** 2 + 1e-5 * src3[:, 0] * src3[:, 2],
                            0.9998 * src3[:, 1] + 2e-6 * src3[:, 1] ** 2,
                            0.98 * src3[:, 2] + 1e-6 * src3[:, 2] ** 2])
    m3 = fit_poly(src3, dst3, 2, "poly3d")
    pred = _south_fit_predict(src3, dst3)
    diff = max(max(abs(np.array(pred(p)) - np.array(eval_poly(p, m3)))) for p in src3)
    assert diff < 1e-9, diff


def test_south_crosscheck_engineering_scale():
    """工程大坐标（x~2.5e4）：两解算差异 = 法方程病态的求解噪声，应远小于测量精度。"""
    from app.logic.polynomial import eval_poly, fit_poly
    rng = np.random.RandomState(12)
    src = rng.uniform(20000, 26000, (12, 2))
    dst = np.column_stack([1.0002 * src[:, 0] + 12.0 + 3e-11 * src[:, 0] ** 2,
                           0.9998 * src[:, 1] + 8.0 + 2e-11 * src[:, 1] ** 2])
    m = fit_poly(src, dst, 2, "poly2d")
    pred = _south_fit_predict(src, dst)
    diff = max(max(abs(np.array(pred(p)) - np.array(eval_poly(p, m)))) for p in src)
    assert diff < 1e-4, diff   # <0.1 mm
