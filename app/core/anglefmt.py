"""角度格式：测量 d.ms（度.分秒编码）与十进制度/弧度互转。

d.ms 约定（与常规手簿一致）：D.MMSSssss
  30°26′34.5678″ → 30.26345678
分固定两位，秒 = 余数（小数位随输入精度）。负角整体取负。
解析时兼容：d.ms 编码值、十进制度（无歧义时按 d.ms 处理为 30.2634→30°15′34″，
如需十进制请显式用 parse_deg）。输入输出均为"度"量纲的数值，弧度用 rad_* 包装。
"""
from __future__ import annotations

import math

_D2R = math.pi / 180.0
_R2D = 180.0 / math.pi


def rad_to_dms(rad: float) -> float:
    """弧度 → d.ms 编码值（D.MMSSssss）。"""
    deg = rad * _R2D
    # 吸附浮点噪声（~1e-11° ≈ 0.04µm），避免 27.99999999999° 编码成 27°59′60″
    deg = round(deg, 11)
    sign = -1.0 if deg < 0 else 1.0
    deg = abs(deg)
    d = int(deg)
    minutes = (deg - d) * 60.0
    m = int(minutes)
    sec = (minutes - m) * 60.0
    # 秒与整数差在 1e-6″（≈0.03mm）内先吸附为整秒：deg 的 11 位舍入会留下 ~1e-8″ 亏损，
    # 使精确到分/秒边界的值（如 d.ms 30.500000 = 30°50′）算出 59.99999988″ 而漏过下面的进位
    sec_i = round(sec)
    if abs(sec - sec_i) < 1e-6:
        sec = float(sec_i)
    # 秒按 1e-9 s 精度入格，处理 59.99999″ 进位
    sec_r = round(sec, 9)
    if sec_r >= 60.0:
        sec_r -= 60.0
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    return sign * (d + m / 100.0 + sec_r / 10000.0)


def dms_plausible(value: float) -> bool:
    """数值结构上是否可能是 d.ms 编码（分、秒两位均 <60）。

    用于自动识别：任一坐标的"分"或"秒"位 ≥60 时，该值不可能是 d.ms，
    必为十进制度；全部可行时仍存在歧义，由调用方按软件口径/参数溯源决定。
    """
    frac = abs(value) % 1.0
    mm = int(frac * 100)
    if mm >= 60:
        return False
    ss = int(round((frac * 100 - mm) * 100))
    return ss < 60


def dms_to_rad(value: float) -> float:
    """d.ms 编码值 → 弧度。"""
    sign = -1.0 if value < 0 else 1.0
    v = abs(value)
    d = int(v)
    frac = v - d
    total_sec = d * 3600.0 + frac * 100.0 * 60.0 + (frac * 100.0 - int(frac * 100.0)) * 100.0
    # frac*100 的整数部分是分，余数 *100 是秒（含小数）；先精确拆分再求和
    mm = int(frac * 100.0 + 1e-9)
    sec = (frac * 100.0 - mm) * 100.0
    if sec >= 59.9999995:
        sec = 0.0
        mm += 1
    if mm >= 60:
        mm -= 60
        d += 1
    total_sec = d * 3600.0 + mm * 60.0 + sec
    return sign * total_sec / 648000.0 * math.pi


def dms_to_deg(value: float) -> float:
    return dms_to_rad(value) * _R2D


def deg_to_dms(deg: float) -> float:
    return rad_to_dms(deg * _D2R)


def parse_flexible(text: str, unit: str = "dms") -> float:
    """GUI 输入解析：unit='dms' 按 d.ms 编码解析（也接受 '30 26 34.56' 空格分隔的度分秒）；
    unit='deg' 按十进制度。返回弧度。

    空格/°′″ 分隔形式优先于 d.ms 编码（避免 30 26 与 30.26 歧义）。
    """
    s = str(text).strip().replace("°", " ").replace("′", " ").replace("″", " ").replace(",", " ")
    parts = [p for p in s.split() if p]
    if len(parts) >= 2:
        d = float(parts[0])
        m = float(parts[1]) if len(parts) > 1 else 0.0
        sec = float(parts[2]) if len(parts) > 2 else 0.0
        sign = -1.0 if d < 0 else 1.0
        total = abs(d) * 3600.0 + m * 60.0 + sec
        return sign * total / 648000.0 * math.pi
    v = float(s)
    return dms_to_rad(v) if unit == "dms" else v * _D2R


def fmt_dms(rad: float, sec_digits: int = 4) -> str:
    """弧度 → 人类可读字符串 30°26′34.5678″。"""
    v = rad_to_dms(rad)
    sign = "-" if v < 0 else ""
    v = abs(v)
    d = int(v)
    mm = int(round((v - d) * 100.0))
    sec = (v - d) * 100.0 - mm
    sec *= 100.0  # 秒
    return f"{sign}{d}°{mm:02d}′{sec:0{3 + sec_digits}.{sec_digits}f}″"
