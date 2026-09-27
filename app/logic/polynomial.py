"""通用多项式转换模型（二维/三维，完全项，最小二乘）。

模型：每个目标分量 = 完全多项式 f(源坐标)，阶数 degree（1~3）；
源坐标先重心化（减中心）以改善法方程条件，系数对应中心化坐标。
- 二维多项式（poly2d）：(x, y) → (x', y')，2 阶 6 项/分量（与南方 Polynomial 表
  A0-A5/B0-B5 的“完全二次多项式”项数一致；项序按 x,y,x²,xy,y² 标准 convention）。
- 三维多项式（poly3d）：(x, y, h) → (x', y', h')，2 阶 10 项/分量。

健壮性：点数不足/设计矩阵秩亏 → ValueError（明确中文说明）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _terms(ndim: int, degree: int) -> list[tuple]:
    """完全多项式的 (各维幂次) 项列表，按阶数升序排列。"""
    import itertools

    terms = []
    for d in range(degree + 1):
        for combo in itertools.combinations_with_replacement(range(ndim), d):
            pw = [0] * ndim
            for c in combo:
                pw[c] += 1
            terms.append(tuple(pw))
    return terms


def _design(pts: np.ndarray, terms: list[tuple], center: np.ndarray) -> np.ndarray:
    rel = np.asarray(pts, dtype=float) - center
    cols = [np.ones(len(rel))]
    for pw in terms:
        col = np.ones(len(rel))
        for ax, k in enumerate(pw):
            if k:
                col = col * rel[:, ax] ** k
        cols.append(col)
    return np.column_stack(cols)


@dataclass
class PolyModel:
    kind: str                    # poly2d / poly3d
    degree: int
    ndim: int                    # 源坐标维数（2/3）
    center: list                 # 重心（源坐标）
    terms: list
    coefs: list                  # 每个目标分量一组系数
    rms: float | None = None     # 全分量合并 RMS
    max_err: float | None = None
    note: str = ""

    def to_doc(self) -> dict:
        return {"kind": self.kind, "degree": self.degree, "ndim": self.ndim,
                "center": [float(c) for c in self.center],
                "terms": [list(t) for t in self.terms],
                "coefs": [[float(c) for c in coef] for coef in self.coefs],
                "rms": self.rms, "max_err": self.max_err, "note": self.note}

    @classmethod
    def from_doc(cls, doc: dict) -> "PolyModel":
        """从参数 JSON 重建模型；字段缺失/类型不对 → ValueError（防静默错算）。"""
        if not isinstance(doc, dict):
            raise ValueError("多项式参数损坏：不是对象")
        need = ("kind", "degree", "ndim", "center", "terms", "coefs")
        for k in need:
            if k not in doc:
                raise ValueError(f"多项式参数损坏：缺少 {k}")
        try:
            m = cls(kind=str(doc["kind"]), degree=int(doc["degree"]),
                    ndim=int(doc["ndim"]), center=[float(c) for c in doc["center"]],
                    terms=[tuple(int(i) for i in t) for t in doc["terms"]],
                    coefs=[[float(c) for c in coef] for coef in doc["coefs"]],
                    rms=doc.get("rms"), max_err=doc.get("max_err"),
                    note=str(doc.get("note", "")))
        except (TypeError, ValueError) as e:
            raise ValueError(f"多项式参数损坏：{e}") from e
        if m.ndim not in (2, 3) or not (1 <= m.degree <= 3):
            raise ValueError("多项式参数损坏：维数/阶数非法")
        if len(m.terms) < 1 or any(len(c) != len(m.terms) + 1 for c in m.coefs):
            raise ValueError("多项式参数损坏：项/系数长度不一致")
        return m


def fit_poly(src_pts, dst_pts, degree: int, kind: str) -> PolyModel:
    """完全多项式最小二乘拟合。src/dst: (n, ndim) 同维数组。"""
    src = np.asarray(src_pts, dtype=float)
    dst = np.asarray(dst_pts, dtype=float)
    if dst.ndim == 1:
        dst = dst[:, None]           # 单目标分量（如仅高程 dh）
    ndim = src.shape[1]
    if kind == "poly2d":
        if ndim != 2 or not (1 <= dst.shape[1] <= 2):
            raise ValueError("二维多项式需要 2 维源坐标、1~2 个目标分量")
    elif kind == "poly3d":
        if ndim != 3 or not (1 <= dst.shape[1] <= 3):
            raise ValueError("三维多项式需要 3 维源坐标、1~3 个目标分量（允许仅拟合高程）")
    else:
        raise ValueError(f"未知多项式类型：{kind}")
    n_terms = len(_terms(ndim, degree))
    if len(src) < n_terms:
        raise ValueError(f"{degree} 阶多项式需要至少 {n_terms} 个点（当前 {len(src)} 个）；"
                         "点数不足时转换精度显著降低，且不适用于外推")
    center = np.mean(src, axis=0)
    A = _design(src, _terms(ndim, degree), center)
    if np.linalg.matrix_rank(A) < n_terms:
        raise ValueError("设计矩阵秩亏：控制点分布使多项式系数无唯一解"
                         "（点共线或分布过于特殊），请调整点位或降低阶数")
    coefs = []
    res_all = []
    for c in range(dst.shape[1]):
        coef, *_ = np.linalg.lstsq(A, dst[:, c], rcond=None)
        res_all.append(dst[:, c] - A @ coef)
        coefs.append([float(v) for v in coef])
    res = np.column_stack(res_all)
    d = np.abs(res)
    return PolyModel(kind=kind, degree=degree, ndim=ndim,
                     center=[float(c) for c in center],
                     terms=_terms(ndim, degree), coefs=coefs,
                     rms=float(np.sqrt(np.mean(res ** 2))),
                     max_err=float(np.max(d)),
                     note=f"{degree} 阶完全多项式（重心化），{len(src)} 点")


def eval_poly(pt, model: PolyModel):
    """单点求值。pt: 长度 ndim 的序列；返回目标分量元组。"""
    rel = np.asarray(pt, dtype=float) - np.asarray(model.center)
    vals = []
    for coef in model.coefs:
        v = coef[0]
        for (pw, c) in zip(model.terms, coef[1:]):
            t = 1.0
            for ax, k in enumerate(pw):
                if k:
                    t *= rel[ax] ** k
            v += c * t
        vals.append(float(v))
    return tuple(vals)


def residual_table(src_pts, dst_pts, model: PolyModel) -> list[tuple]:
    """逐点残差 [(Δ分量1, Δ分量2, …)] = 模型输出 − 目标值。"""
    out = []
    for s, d in zip(np.asarray(src_pts, dtype=float), np.asarray(dst_pts, dtype=float)):
        out.append(tuple(np.asarray(eval_poly(s, model)) - np.asarray(d, dtype=float)))
    return out


def residual_summary(res: list[tuple]) -> dict:
    """残差汇总 {n, rms_xy_m, max_xy_m, rms_h_m, max_h_m}；目标分量不足时对应项为 None。

    rms_xy_m 按点位残差 √(Δx²+Δy²) 的均方根计。
    """
    r = np.asarray(res, dtype=float)
    d: dict = {"n": int(len(r))}
    if r.size == 0:
        return d
    if r.shape[1] >= 2:
        d["rms_xy_m"] = float(np.sqrt(np.mean(r[:, 0] ** 2 + r[:, 1] ** 2)))
        d["max_xy_m"] = float(np.max(np.hypot(r[:, 0], r[:, 1])))
    else:
        d["rms_xy_m"] = d["max_xy_m"] = None
    if r.shape[1] >= 3:
        d["rms_h_m"] = float(np.sqrt(np.mean(r[:, 2] ** 2)))
        d["max_h_m"] = float(np.max(np.abs(r[:, 2])))
    else:
        d["rms_h_m"] = d["max_h_m"] = None
    return d


def check_src_plausible(src_pts, source_kind: str) -> str | None:
    """源坐标量程合理性判别；返回错误说明，None 表示通过。

    仅做量程级别的硬拦截（|B|≤90°、|L|≤360°），防止把经纬度喂给平面模型
    （或反之）后静默得到天文数字；平面量程不做软提示以免误伤局部坐标系。
    """
    a = np.asarray(src_pts, dtype=float)
    if a.size == 0:
        return None
    if source_kind == "lonlat":
        if np.max(np.abs(a[:, 0])) > 90.5 or np.max(np.abs(a[:, 1])) > 360.5:
            return ("源坐标量程不像大地坐标（应 |B|≤90°、|L|≤360°）：请核对模型类型"
                    "（源=大地 / 源=平面）或点对数据")
    return None
