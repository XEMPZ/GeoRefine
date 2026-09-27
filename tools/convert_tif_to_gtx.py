"""把 EGM tif 格网转换为 PROJ 兼容 GTX 二进制（可随软件分发、无第三方依赖读取）。

GTX 格式（已核实 PROJ 源码 grids.cpp）:
  头 40 字节全大端: minlat(double) minlon(double) dlat(double) dlon(double) rows(int32) cols(int32)
  数据 float32 大端，行主序，第 0 行 = 最南端；nodata = -88.8888
用法: python tools/convert_tif_to_gtx.py [输入.tif] [输出.gtx]
不传参数则转换 models/us_nga_egm96_15.tif -> models/egm96_15.gtx
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

NODATA = -88.8888


def tif_to_grid(path: Path):
    """读 tif（第0行=最北，节点注册）→ 返回南在前数组与格网参数。"""
    import tifffile

    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0]
        arr = page.asarray().astype(np.float64)
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
    dlon, dlat = float(scale[0]), float(scale[1])
    lon0, lat0 = float(tie[3]), float(tie[4])  # (-180, 90)
    nlat, nlon = arr.shape
    lat_min = lat0 - dlat * (nlat - 1)
    arr_south_first = arr[::-1, :].copy()
    bad = ~np.isfinite(arr_south_first) | (np.abs(arr_south_first) > 1e5) | (np.abs(arr_south_first - NODATA) < 0.01)
    arr_south_first[bad] = NODATA
    return arr_south_first.astype(np.float32), lat_min, lon0, dlat, dlon


def write_gtx(path: Path, arr: np.ndarray, lat_min: float, lon_min: float, dlat: float, dlon: float):
    nlat, nlon = arr.shape
    with open(path, "wb") as f:
        f.write(struct.pack(">ddddii", lat_min, lon_min, dlat, dlon, nlat, nlon))
        f.write(arr.astype(">f4").tobytes())


def read_gtx(path: Path):
    with open(path, "rb") as f:
        head = f.read(40)
        lat_min, lon_min, dlat, dlon, nlat, nlon = struct.unpack(">ddddii", head)
        data = np.frombuffer(f.read(), dtype=">f4").reshape(nlat, nlon).astype(np.float64)
    return data, lat_min, lon_min, dlat, dlon


def main():
    root = Path(__file__).resolve().parent.parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "models" / "us_nga_egm96_15.tif"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "models" / (src.stem.replace("us_nga_", "") + ".gtx")

    arr, lat_min, lon_min, dlat, dlon = tif_to_grid(src)
    write_gtx(dst, arr, lat_min, lon_min, dlat, dlon)

    # 严格自检：回读 GTX 与源 tif（南在前）逐节点比对 500 个随机点
    import tifffile
    with tifffile.TiffFile(str(src)) as tf:
        tif_arr = tf.pages[0].asarray().astype(np.float64)[::-1, :]  # 南在前
    chk, la, lo, dla, dlo = read_gtx(dst)
    rng = np.random.default_rng(7)
    nlat, nlon = chk.shape
    ok = True
    for _ in range(500):
        i = int(rng.integers(0, nlat))
        j = int(rng.integers(0, nlon))
        a = float(chk[i, j])
        b = float(tif_arr[i, j])
        if abs(a - b) > 1e-4:
            print(f"[MISMATCH] node({i},{j}) gtx={a} tif={b}")
            ok = False
            break
    print(f"[selfcheck] 500 random nodes: {'ALL MATCH' if ok else 'FAILED'}")
    print(f"[ok] {dst} ({dst.stat().st_size / 1e6:.1f} MB) rows={nlat} cols={nlon} "
          f"lat {lat_min:.2f}..{lat_min + dlat * (nlat - 1):.2f} step {dlat}°")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
