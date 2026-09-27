"""椭球参数与大地坐标↔空间直角坐标转换。

公式（标准大地测量）:
  N = a / sqrt(1 - e² sin²B)
  X = (N + H) cosB cosL ;  Y = (N + H) cosB sinL ;  Z = (N(1-e²) + H) sinB
反算用迭代法，收敛 1e-12 rad。全部接口角度用度，支持标量与 numpy 数组。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Ellipsoid:
    name: str
    a: float      # 长半轴
    inv_f: float  # 1/f

    @property
    def f(self) -> float:
        return 1.0 / self.inv_f

    @property
    def e2(self) -> float:
        f = self.f
        return 2 * f - f * f

    @property
    def b(self) -> float:
        return self.a * (1 - self.f)


WGS84 = Ellipsoid("WGS84", 6378137.0, 298.257223563)
CGCS2000 = Ellipsoid("CGCS2000", 6378137.0, 298.257222101)
BEIJING54 = Ellipsoid("Beijing54", 6378245.0, 298.3)
XIAN80 = Ellipsoid("Xian80", 6378140.0, 298.257)
ELLIPSOIDS = {"WGS84": WGS84, "CGCS2000": CGCS2000, "Beijing54": BEIJING54, "Xian80": XIAN80}

# ---- 自定义椭球注册表（ellipsoids/*.json，参数库页维护；同名覆盖内置） ----
import json as _json
import re as _re
from pathlib import Path as _Path

_ELL_DIR = _Path(__file__).resolve().parents[2] / "ellipsoids"


def ellipsoid_dir() -> _Path:
    """自定义椭球存放目录。"""
    return _ELL_DIR


def custom_ellipsoids() -> dict:
    """读取全部自定义椭球 {名称: Ellipsoid}。"""
    out = {}
    if _ELL_DIR.exists():
        for f in sorted(_ELL_DIR.glob("*.json")):
            try:
                d = _json.loads(f.read_text(encoding="utf-8"))
                out[d["name"]] = Ellipsoid(d["name"], float(d["a"]), float(d["inv_f"]))
            except Exception:  # noqa: BLE001
                continue
    return out


def all_ellipsoids() -> dict:
    """内置 + 自定义（同名时自定义覆盖）。"""
    return {**ELLIPSOIDS, **custom_ellipsoids()}


def get_ellipsoid(name: str):
    """按名称取椭球（内置/自定义），不存在返回 None。"""
    return custom_ellipsoids().get(name) or ELLIPSOIDS.get(name)


def save_custom_ellipsoid(name: str, a, inv_f) -> _Path:
    """新增/更新自定义椭球（含校验），返回保存路径。"""
    name = str(name).strip()
    if not name:
        raise ValueError("椭球名称不能为空")
    if name in ELLIPSOIDS:
        raise ValueError(f"名称“{name}”与内置椭球重名")
    try:
        a, inv_f = float(a), float(inv_f)
    except (TypeError, ValueError) as e:
        raise ValueError(f"椭球参数需为数值：{e}") from e
    if not (1e6 < a < 1e7 and 100 < inv_f < 500):
        raise ValueError(f"椭球参数超出合理范围：a={a}, 1/f={inv_f}")
    _ELL_DIR.mkdir(parents=True, exist_ok=True)
    fn = _re.sub(r'[\\/:*?"<>|]', "_", name)
    p = _ELL_DIR / f"{fn}.json"
    p.write_text(_json.dumps({"name": name, "a": a, "inv_f": inv_f},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def delete_custom_ellipsoid(name: str) -> bool:
    """删除自定义椭球（内置不可删）。"""
    fn = _re.sub(r'[\\/:*?"<>|]', "_", name)
    p = _ELL_DIR / f"{fn}.json"
    if p.exists():
        p.unlink()
        return True
    return False

_D2R = np.pi / 180.0
_R2D = 180.0 / np.pi


def geodetic_to_ecef(lat_deg, lon_deg, h, ell: Ellipsoid = WGS84):
    """大地坐标 (B, L, H) → 空间直角 (X, Y, Z)。角度度，高程米。向量化。"""
    lat = np.asarray(lat_deg, dtype=float) * _D2R
    lon = np.asarray(lon_deg, dtype=float) * _D2R
    h = np.asarray(h, dtype=float)
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    N = ell.a / np.sqrt(1.0 - ell.e2 * sin_lat ** 2)
    x = (N + h) * cos_lat * np.cos(lon)
    y = (N + h) * cos_lat * np.sin(lon)
    z = (N * (1.0 - ell.e2) + h) * sin_lat
    scalar = np.isscalar(lat_deg) and np.isscalar(lon_deg) and np.isscalar(h)
    return (float(x), float(y), float(z)) if scalar else (x, y, z)


def ecef_to_geodetic(x, y, z, ell: Ellipsoid = WGS84):
    """空间直角 (X, Y, Z) → 大地坐标 (B, L, H)。迭代法，收敛 1e-12 rad。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    # 初值
    lat = np.arctan2(z, p * (1.0 - ell.e2))
    for _ in range(60):
        sin_lat = np.sin(lat)
        N = ell.a / np.sqrt(1.0 - ell.e2 * sin_lat ** 2)
        lat_new = np.arctan2(z + ell.e2 * N * sin_lat, p)
        if np.max(np.abs(lat_new - lat)) < 1e-12:
            lat = lat_new
            break
        lat = lat_new
    sin_lat = np.sin(lat)
    N = ell.a / np.sqrt(1.0 - ell.e2 * sin_lat ** 2)
    h = p / np.cos(lat) - N
    # 极区保护
    near_pole = p < 1e-9
    if np.any(near_pole):
        lat = np.where(near_pole, np.sign(z) * np.pi / 2, lat)
        h = np.where(near_pole, np.abs(z) - ell.a * (1 - ell.e2), h)
    scalar = np.isscalar(x) and np.isscalar(y) and np.isscalar(z)
    if scalar:
        return float(lat) * _R2D, float(lon) * _R2D, float(h)
    return lat * _R2D, lon * _R2D, h
