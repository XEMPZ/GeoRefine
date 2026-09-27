"""大地水准面（似大地水准面）格网模型：多格式读取 + 双线性内插。

内部统一 GridData：values[i,j] ↔ (lat = lat_min + i*dlat, lon = lon_min + j*dlon)，
行序自南向北，无效格点标记 999。

支持格式:
  .gtx  PROJ GTX（大端；头 40B = 4×double[minlat,minlon,dlat,dlon] + 2×int32[rows,cols]；
        数据 float32 大端行主序，第 0 行=最南端；nodata=-88.8888 → 999）
  .tif/.tiff  GeoTIFF（需 tifffile；第 0 行=最北 → 翻转；节点注册按 ModelTiepoint/PixelScale）
  .csv  文本格网: 列 lon,lat,undulation（# 注释；utf-8/gbk 自动；须规则格网，否则报错）
  .zgf/.ggf/.grd/.bin  中海达/天宾/Surfer 风格二进制（小端，布局按《算法规格书01》第五节）
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

INVALID = 999.0
_GTX_NODATA = -88.8888


@dataclass
class GridData:
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    dlat: float
    dlon: float
    values: np.ndarray      # (nlat, nlon)，无效=999
    fmt: str = "?"
    path: str = ""


# ---------------------------------------------------------------- GTX
def read_gtx(path) -> GridData:
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(40)
        if len(head) < 40:
            raise ValueError(f"GTX 文件头损坏: {path}")
        lat_min, lon_min, dlat, dlon, nlat, nlon = struct.unpack(">ddddii", head)
        raw = np.frombuffer(f.read(), dtype=">f4")
    if raw.size < nlat * nlon:
        raise ValueError(f"GTX 数据不足: 期望 {nlat}x{nlon}, 实际 {raw.size}")
    vals = raw[: nlat * nlon].reshape(nlat, nlon).astype(np.float64)
    vals[np.abs(vals - _GTX_NODATA) < 0.01] = INVALID
    vals[~np.isfinite(vals)] = INVALID
    return GridData(path.stem, float(lat_min), float(lat_min + dlat * (nlat - 1)),
                    float(lon_min), float(lon_min + dlon * (nlon - 1)),
                    float(dlat), float(dlon), vals, "gtx", str(path))


# ---------------------------------------------------------------- GeoTIFF
def read_tif_grid(path) -> GridData:
    try:
        import tifffile
    except ImportError as e:
        raise ImportError("读取 GeoTIFF 需要安装 tifffile：pip install tifffile imagecodecs") from e
    path = Path(path)
    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0]
        arr = page.asarray().astype(np.float64)
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
    dlon, dlat = float(scale[0]), float(scale[1])
    lon0, lat0 = float(tie[3]), float(tie[4])   # 左上角节点（北）
    nlat, nlon = arr.shape
    arr = arr[::-1, :]                          # → 南在前
    arr[~np.isfinite(arr)] = INVALID
    arr[np.abs(arr) > 1e5] = INVALID
    return GridData(path.stem, lat0 - dlat * (nlat - 1), lat0, lon0,
                    lon0 + dlon * (nlon - 1), dlat, dlon, arr, "tif", str(path))


# ---------------------------------------------------------------- CSV
def read_csv_grid(path) -> GridData:
    path = Path(path)
    raw = path.read_bytes()
    enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else (
        "utf-8" if _decodable(raw, "utf-8") else "gbk")
    lats: dict[float, dict[float, float]] = {}
    for line in raw.decode(enc).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("\t", ",").replace(";", ",").split(",")
        if len(parts) < 3:
            continue
        try:
            lon, lat, v = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            continue  # 表头行
        lats.setdefault(lat, {})[lon] = v
    if not lats:
        raise ValueError(f"CSV 格网无有效数据: {path}")
    lat_list = sorted(lats)
    lon_list = sorted({lon for row in lats.values() for lon in row})
    nlat, nlon = len(lat_list), len(lon_list)
    if nlat < 2 or nlon < 2:
        raise ValueError(f"CSV 格网至少需要 2x2 格点: {path}")
    dlat = _uniform_step(lat_list, "lat")
    dlon = _uniform_step(lon_list, "lon")
    vals = np.full((nlat, nlon), INVALID)
    for i, la in enumerate(lat_list):
        for j, lo in enumerate(lon_list):
            v = lats[la].get(lo)
            if v is not None:
                vals[i, j] = v
    return GridData(path.stem, lat_list[0], lat_list[-1], lon_list[0], lon_list[-1],
                    dlat, dlon, vals, "csv", str(path))


def _decodable(raw: bytes, enc: str) -> bool:
    try:
        raw.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


def _uniform_step(axis: list[float], name: str) -> float:
    d = np.diff(np.asarray(axis))
    step = float(np.median(d))
    if np.max(np.abs(d - step)) > step * 0.5:
        raise ValueError(f"CSV 格网 {name} 方向步长不均匀，暂不支持不规则格网")
    return step


# ---------------------------------------------------------------- 中海达系二进制（小端）
def _f32(buf, off, n):
    return np.frombuffer(buf, dtype="<f4", count=n, offset=off).astype(np.float64)


def read_zgf(path) -> GridData:
    """中海达 ZGF：seek(8) 6×double(glamn,glamx,glomn,glomx,dla,dlo) → 5×int(nla,nlo,ikind,indeximodel,imove)
    → seek(86) 10B 名称 → 数据 96 起 float32×nla×nlo，行序南→北（imove=1 时翻转）。"""
    path = Path(path)
    buf = path.read_bytes()
    glamn, glamx, glomn, glomx, dla, dlo = struct.unpack_from("<6d", buf, 8)
    nla, nlo, _ikind, _imodel, imove = struct.unpack_from("<5i", buf, 8 + 48)
    vals = _f32(buf, 96, nla * nlo).reshape(nla, nlo).copy()
    if imove == 1:
        vals = vals[::-1, :]   # 原北在前 → 翻转为南在前
    vals[np.abs(vals - INVALID) < 0.001] = INVALID
    return GridData(path.stem, glamn, glamx, glomn, glomx, dla, dlo, vals, "zgf", str(path))


def read_ggf(path) -> GridData:
    """天宝 GGF：seek(16) 32B 名称 → 6×double(glamn..dlo) → 2×int(nla,nlo) → seek(136) short model
    → 146 起 float32×nla×nlo，行序北→南。"""
    path = Path(path)
    buf = path.read_bytes()
    name = buf[16:48].split(b"\x00")[0].decode("ascii", "ignore")
    glamn, glamx, glomn, glomx, dla, dlo = struct.unpack_from("<6d", buf, 48)
    nla, nlo = struct.unpack_from("<2i", buf, 48 + 48)
    vals = _f32(buf, 146, nla * nlo).reshape(nla, nlo).copy()
    vals = vals[::-1, :]   # 北在前 → 南在前
    vals[np.abs(vals - INVALID) < 0.001] = INVALID
    return GridData(path.stem, glamn, glamx, glomn, glomx, dla, dlo, vals, "ggf", str(path))


def read_geoid99(path) -> GridData:
    """Geoid99 bin：4×double(glamn,glomn,dla,dlo) → 3×int(nla,nlo,ikind) → 数据 44 起 float32。"""
    path = Path(path)
    buf = path.read_bytes()
    glamn, glomn, dla, dlo = struct.unpack_from("<4d", buf, 0)
    nla, nlo, _ikind = struct.unpack_from("<3i", buf, 32)
    vals = _f32(buf, 44, nla * nlo).reshape(nla, nlo).copy()
    vals = vals[::-1, :]  # Geoid99 北在前 → 南在前
    vals[np.abs(vals - INVALID) < 0.001] = INVALID
    lat_max = glamn + dla * (nla - 1)
    lon_max = glomn + dlo * (nlo - 1)
    return GridData(path.stem, glamn, lat_max, glomn, lon_max, dla, dlo, vals, "geoid99", str(path))


def read_grd(path) -> GridData:
    """Surfer 风格 GRD：6×double(glomn,glomx,glamn,glamx,dlo,dla) → nlo/nla 由范围/步长推算
    → 数据 48 起 float32（i3==1）或 float64（i3==2）。"""
    path = Path(path)
    buf = path.read_bytes()
    glomn, glomx, glamn, glamx, dlo, dla = struct.unpack_from("<6d", buf, 0)
    nlo = int(round((glomx - glomn) / dlo)) + 1
    nla = int(round((glamx - glamn) / dla)) + 1
    n = nla * nlo
    try:
        vals = _f32(buf, 48, n).reshape(nla, nlo).copy()
        fmt = "grd-f32"
    except ValueError:
        vals = np.frombuffer(buf, dtype="<f8", count=n, offset=48).reshape(nla, nlo).copy()
        fmt = "grd-f64"
    # GRD 行序为南→北（Surfer 惯例自 y最小 起）
    vals[np.abs(vals - INVALID) < 0.001] = INVALID
    return GridData(path.stem, glamn, glamx, glomn, glomx, dla, dlo, vals, fmt, str(path))


_READERS = {".gtx": read_gtx, ".tif": read_tif_grid, ".tiff": read_tif_grid,
            ".csv": read_csv_grid, ".zgf": read_zgf, ".ggf": read_ggf,
            ".geoid99": read_geoid99, ".bin": read_geoid99, ".grd": read_grd}

_CACHE: dict = {}   # (path, mtime_ns, size) -> GridData，避免 GUI 重复全量解码大 tif


def load_grid(path) -> GridData:
    path = Path(path)
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _CACHE:
        return _CACHE[key]
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"不支持的格网格式: {path.suffix}（支持 gtx/tif/csv/zgf/ggf/grd/bin）")
    g = reader(path)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = g
    return g


class GridModel:
    """格网高程异常查询器（双线性内插，节点注册）。"""

    def __init__(self, grid: GridData, method: str = "bilinear"):
        if method != "bilinear":
            raise ValueError("当前仅支持 bilinear 内插")
        self.grid = grid
        self._vals = grid.values

    def undulation(self, lon_deg, lat_deg) -> float:
        xi = self.try_undulation(lon_deg, lat_deg)
        if xi is None:
            g = self.grid
            raise ValueError(
                f"坐标 ({lon_deg:.5f}, {lat_deg:.5f}) 超出模型 {g.name} 范围: "
                f"lon [{g.lon_min:.4f}, {g.lon_max:.4f}], lat [{g.lat_min:.4f}, {g.lat_max:.4f}]")
        return xi

    def try_undulation(self, lon_deg, lat_deg):
        g = self.grid
        fi = (lat_deg - g.lat_min) / g.dlat
        fj = (lon_deg - g.lon_min) / g.dlon
        nlat, nlon = self._vals.shape
        if fi < 0 or fj < 0 or fi > nlat - 1 or fj > nlon - 1:
            return None
        i0, j0 = int(fi), int(fj)
        i1, j1 = min(i0 + 1, nlat - 1), min(j0 + 1, nlon - 1)
        di, dj = fi - i0, fj - j0
        v00, v10 = self._vals[i0, j0], self._vals[i1, j0]
        v01, v11 = self._vals[i0, j1], self._vals[i1, j1]
        for v in (v00, v10, v01, v11):
            if abs(v - INVALID) < 0.001:
                return None
        return float(v00 * (1 - di) * (1 - dj) + v10 * di * (1 - dj)
                     + v01 * (1 - di) * dj + v11 * di * dj)


def _probe_grid(path: Path) -> dict:
    """轻量探测（不全量解码大文件）：gtx 读头，tif 读标签，其余直接加载（小文件）。"""
    ext = path.suffix.lower()
    if ext == ".gtx":
        import struct as _s
        with open(path, "rb") as f:
            head = f.read(40)
        if len(head) < 40:
            raise ValueError("GTX 头损坏")
        lat_min, lon_min, dlat, dlon, nlat, nlon = _s.unpack(">ddddii", head)
        return {"lat_min": lat_min, "lat_max": lat_min + dlat * (nlat - 1),
                "lon_min": lon_min, "lon_max": lon_min + dlon * (nlon - 1),
                "fmt": "gtx", "usable": True}
    if ext in (".tif", ".tiff"):
        try:
            import tifffile
        except ImportError as e:
            raise ImportError("读取 GeoTIFF 需要安装 tifffile：pip install tifffile imagecodecs") from e
        with tifffile.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            scale = page.tags["ModelPixelScaleTag"].value
            tie = page.tags["ModelTiepointTag"].value
            nlat, nlon = page.shape
        dlon, dlat = float(scale[0]), float(scale[1])
        lon0, lat0 = float(tie[3]), float(tie[4])
        return {"lat_min": lat0 - dlat * (nlat - 1), "lat_max": lat0,
                "lon_min": lon0, "lon_max": lon0 + dlon * (nlon - 1),
                "fmt": "tif", "usable": True}
    g = load_grid(path)
    return {"lat_min": g.lat_min, "lat_max": g.lat_max, "lon_min": g.lon_min,
            "lon_max": g.lon_max, "fmt": g.fmt, "usable": True}


def available_models(models_dir="models") -> list[dict]:
    """扫描格网目录（轻量探测，不全量解码），返回 [{name, path, fmt, coverage, usable}]。"""
    out = []
    p = Path(models_dir)
    if not p.exists():
        return out
    for f in sorted(p.iterdir()):
        if f.suffix.lower() not in _READERS:
            continue
        try:
            info = _probe_grid(f)
            out.append({"name": f.stem, "path": str(f), "fmt": info["fmt"],
                        "lat_min": info["lat_min"], "lat_max": info["lat_max"],
                        "lon_min": info["lon_min"], "lon_max": info["lon_max"],
                        "usable": True})
        except Exception as e:  # noqa: BLE001
            out.append({"name": f.stem, "path": str(f), "fmt": f.suffix,
                        "lat_min": None, "lat_max": None, "lon_min": None,
                        "lon_max": None, "usable": False, "error": str(e)})
    return out
