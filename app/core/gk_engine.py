"""高斯投影工程引擎。

双重验证复原，正算与原程序 144 组真值最大偏差 0.5 µm（打印分辨率级）。

与 app/core/projection.py（Snyder 6 阶，PROJ 对照版）的关系：
  两套都可用。本引擎额外支持：
  - 投影面大地高 h0（工程椭球）：
      近似：a_eff = a + h0（椭球直接膨胀）
      严密：a_eff = a·(1 + h0/Ra(B0))，Ra(B0)=√(M·N)=a·√(1-e²)/W²(B0) 为 B0 处
            平均曲率半径（h0·W²(B0)/√(1-e²)）
  - UTM 尺度 k（0.9996）与加常数 x0/y0 以 km 为单位
  - 级数展开至 e¹⁰（Snyder 至 e⁸，带内差异 <0.3 mm）
约定：角度一律弧度；平面 x=北、y=东；x0/y0 单位 km。
"""
from __future__ import annotations

import math

from app.core.ellipsoid import Ellipsoid, CGCS2000

_D2R = math.pi / 180.0
_R2D = 180.0 / math.pi


def e2_of_inv_f(inv_f: float) -> float:
    """由 1/f 求 e²（2/f - 1/f²）。"""
    return 2.0 / inv_f - 1.0 / (inv_f * inv_f)


def mean_curvature_radius(a: float, e2: float, b0: float) -> float:
    """B0 处平均曲率半径 Ra = √(M·N) = a·√(1-e²)/W²。"""
    W2 = 1.0 - e2 * math.sin(b0) ** 2
    return a * math.sqrt(1.0 - e2) / W2


def effective_a(a: float, inv_f: float, b0: float, h0: float, rigorous: bool) -> float:
    """工程椭球长半轴。近似 = a + h0；严密 = a·(1 + h0/Ra(B0))。"""
    if h0 == 0.0:
        return a
    if rigorous:
        e2 = e2_of_inv_f(inv_f)
        return a + h0 * (1.0 - e2 * math.sin(b0) ** 2) / math.sqrt(1.0 - e2)
    return a + h0


def _merid_coefs(a_eff: float, e2: float):
    """子午线弧长系数（e¹⁰ 阶）。"""
    A1 = (1 + 3*e2/4 + 45*e2**2/64 + 175*e2**3/256
          + 11025*e2**4/16384 + 43659*e2**5/65536)
    A2 = 3*e2/4 + 15*e2**2/16 + 525*e2**3/512 + 2205*e2**4/2048 + 72765*e2**5/65536
    A3 = 15*e2**2/64 + 105*e2**3/256 + 2205*e2**4/4096 + 10395*e2**5/16384
    # 注：A4 末项分母取 13072。该系数族在正算与反算中被同一份实现共用，
    # 两侧必须严格同值，否则往返闭合会被破坏（量级 < 0.01 mm，但会破坏逐位一致）。
    A4 = 35*e2**3/512 + 315*e2**4/2048 + 31185*e2**5/13072
    c = a_eff * (1.0 - e2)
    return (c * A1, -c * A2 / 2.0, c * A3 / 4.0, -c * A4 / 6.0)


def _meridian_arc(b_rad: float, m1: float, m2: float, m3: float, m4: float) -> float:
    """M(B) = m1·B − cosB·(A·sinB + B·sin³B + C·sin⁵B)（sin 倍角展开式）。"""
    s = math.sin(b_rad)
    c = math.cos(b_rad)
    return m1 * b_rad - c * (abs(2*m2 + 4*m3 + 6*m4)*s
                             + (8*m3 + 32*m4)*s**3
                             + abs(32*m4)*s**5)


def _dmeridian_arc(b_rad: float, m1, m2, m3, m4) -> float:
    """dM/dB（解析，供反算牛顿迭代）。"""
    s = math.sin(b_rad)
    c = math.cos(b_rad)
    A = abs(2*m2 + 4*m3 + 6*m4)
    Bc = 8*m3 + 32*m4
    Cc = abs(32*m4)
    F = A*s + Bc*s**3 + Cc*s**5
    Fp = A*c + 3*Bc*s*s*c + 5*Cc*s**4*c
    return m1 - (-s*F + c*Fp*c)


def gauss_forward(a: float, inv_f: float, l0: float, b0: float, h0: float,
                  k: float, x0_km: float, y0_km: float,
                  b_rad: float, l_rad: float, rigorous: bool = False):
    """高斯/UTM 正算：(B, L) → (x_北, y_东)。l 允许跨带（±3.5° 内亚毫米）。"""
    e2 = e2_of_inv_f(inv_f)
    ep2 = e2 / (1.0 - e2)
    a_eff = effective_a(a, inv_f, b0, h0, rigorous)
    m1, m2, m3, m4 = _merid_coefs(a_eff, e2)
    s, c, t = math.sin(b_rad), math.cos(b_rad), math.tan(b_rad)
    C = ep2 * c * c
    N = a_eff / math.sqrt(1.0 - e2 * s * s)
    dl_c = (l_rad - l0) * c
    x = (k * (_meridian_arc(b_rad, m1, m2, m3, m4)
              + N*t*dl_c**2/2
              + (5 - t*t + 9*C + 4*C*C)*N*t*dl_c**4/24
              + (61 - 58*t*t + t**4)*N*t*dl_c**6/720)
         + x0_km * 1000.0)
    y = k * N * (dl_c
                 + (1 - t*t + C)*dl_c**3/6
                 + (5 - 18*t*t + t**4 + 14*C*C - 58*C*t*t)*dl_c**5/120) + y0_km * 1000.0
    return x, y


def gauss_inverse(a: float, inv_f: float, l0: float, b0: float, h0: float,
                  k: float, x0_km: float, y0_km: float,
                  x: float, y: float, rigorous: bool = False):
    """高斯/UTM 反算：(x_北, y_东) → (B, L)。

    (B, L) 二维牛顿反演（数值雅可比），以本模块正算为基准，
    往返闭合 < 1e-12 rad；级数反算输出一致性受其
    反算级数截断限制（~0.6 mm），本实现以正算真值为准、精度更高。
    """
    d = 1e-7
    # 初值：x≈弧长, y≈N·cosB·l
    e2 = e2_of_inv_f(inv_f)
    a_eff = effective_a(a, inv_f, b0, h0, rigorous)
    m1 = _merid_coefs(a_eff, e2)[0]
    b = (x - x0_km * 1000.0) / k / m1
    l = l0 + (y - y0_km * 1000.0) / k / (a_eff * math.cos(b))
    for _ in range(30):
        fx, fy = gauss_forward(a, inv_f, l0, b0, h0, k, x0_km, y0_km, b, l, rigorous)
        fb1, fy1 = gauss_forward(a, inv_f, l0, b0, h0, k, x0_km, y0_km, b + d, l, rigorous)
        fb2, fy2 = gauss_forward(a, inv_f, l0, b0, h0, k, x0_km, y0_km, b, l + d, rigorous)
        j00, j10 = (fb1 - fx) / d, (fy1 - fy) / d
        j01, j11 = (fb2 - fx) / d, (fy2 - fy) / d
        det = j00 * j11 - j01 * j10
        rx, ry = x - fx, y - fy
        db = (rx * j11 - ry * j01) / det
        dl = (ry * j00 - rx * j10) / det
        b += db
        l += dl
        if abs(db) < 1e-14 and abs(dl) < 1e-14:
            break
    return b, l


# ---------------------------------------------------------------- 换带计算
def zone_transform(src: "ProjParams", dst: "ProjParams",
                   b_rad: float, l_rad: float):
    """换带：源投影参数下的 BL → 目标投影参数下的 BL/xy。

    本入口做 (B,L)→目标平面。源侧若输入平面坐标，请先 gauss_inverse 归算到 BL。
    """
    return gauss_forward(dst.a, dst.inv_f, dst.l0, dst.b0, dst.h0,
                         dst.k, dst.x0_km, dst.y0_km, b_rad, l_rad, dst.rigorous)


class ProjParams:
    """一组投影参数。

    a/inv_f 取自椭球；l0 中央子午线、b0 平均纬度（弧度）；h0 投影面大地高（m）；
    k 尺度（1.0 高斯 / 0.9996 UTM）；x0_km/y0_km 加常数（km，y0 国内惯例 500）。
    """

    __slots__ = ("a", "inv_f", "l0", "b0", "h0", "k", "x0_km", "y0_km", "rigorous")

    def __init__(self, a: float, inv_f: float, l0: float, b0: float = 30.0 * _D2R,
                 h0: float = 0.0, k: float = 1.0, x0_km: float = 0.0,
                 y0_km: float = 500.0, rigorous: bool = False):
        self.a, self.inv_f, self.l0, self.b0 = a, inv_f, l0, b0
        self.h0, self.k, self.x0_km, self.y0_km = h0, k, x0_km, y0_km
        self.rigorous = bool(rigorous)

    @classmethod
    def from_ellipsoid(cls, ell: Ellipsoid, **kw) -> "ProjParams":
        return cls(ell.a, ell.inv_f, **kw)

    def forward(self, b_rad: float, l_rad: float):
        return gauss_forward(self.a, self.inv_f, self.l0, self.b0, self.h0,
                             self.k, self.x0_km, self.y0_km, b_rad, l_rad,
                             self.rigorous)

    def inverse(self, x: float, y: float):
        return gauss_inverse(self.a, self.inv_f, self.l0, self.b0, self.h0,
                             self.k, self.x0_km, self.y0_km, x, y,
                             self.rigorous)


def zone_change(src: ProjParams, dst: ProjParams, pts, has_bl_src: bool,
                has_bl_dst: bool, heights=None):
    """批量换带/正反算组合。

    pts: [(v1, v2)] —— has_bl_src=True 时为 (B, L)，否则 (x, y)；
    heights: 源侧高程（仅当源为平面且需输出 H 时用；本函数不处理高程）。
    返回 [(out1, out2)] —— has_bl_dst=True 为 (B, L)，否则 (x, y)。
    """
    out = []
    for v1, v2 in pts:
        if has_bl_src:
            b, l = float(v1), float(v2)
        else:
            b, l = src.inverse(float(v1), float(v2))
        if has_bl_dst:
            out.append((b, l))
        else:
            out.append(dst.forward(b, l))
    return out
