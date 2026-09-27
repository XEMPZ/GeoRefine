"""验证 models/ 下 EGM tif 格网可读，并与 pyproj(PROJ) 官方实现交叉核对。

用法: python tools/verify_models.py
输出: 每个 tif 的尺寸/范围/采样点 ξ，及与 PROJ 读数的偏差（应 < 1e-4 m）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
TEST_POINTS = [(114.0, 30.0), (116.4, 39.9), (104.0, 30.6), (113.95, 29.95), (0.5, 0.5)]


def read_tif_raw(path: Path):
    import tifffile

    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0]
        arr = page.asarray().astype(np.float64)
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
    dlon, dlat = float(scale[0]), float(scale[1])
    lon0, lat0 = float(tie[3]), float(tie[4])
    return arr, lon0, lat0, dlon, dlat


def sample_at(arr, lon0, lat0, dlon, dlat, lon, lat):
    """节点注册假设: arr[0,0] 对应 (lat0, lon0)，行向南通增。"""
    nlat, nlon = arr.shape
    i = (lat0 - lat) / dlat
    j = (lon - lon0) / dlon
    if i < 0 or j < 0 or i > nlat - 1 or j > nlon - 1:
        return None
    i0, j0 = int(np.floor(i)), int(np.floor(j))
    i1, j1 = min(i0 + 1, nlat - 1), min(j0 + 1, nlon - 1)
    fi, fj = i - i0, j - j0
    v = (arr[i0, j0] * (1 - fi) * (1 - fj) + arr[i1, j0] * fi * (1 - fj)
         + arr[i0, j1] * (1 - fi) * fj + arr[i1, j1] * fi * fj)
    return float(v)


def proj_ref(path: Path, lon, lat):
    from pyproj import Transformer

    tr = Transformer.from_pipeline(f"+proj=vgridshift +grids={path} +multiplier=1")
    return float(tr.transform(lon, lat, 0.0)[2])


def main():
    tifs = sorted(MODELS.glob("*.tif"))
    if not tifs:
        print("no tif models")
        return 1
    ok = True
    for p in tifs:
        print(f"== {p.name} ==")
        arr, lon0, lat0, dlon, dlat = read_tif_raw(p)
        print(f"  shape={arr.shape} origin=({lon0},{lat0}) step=({dlon},{dlat})")
        print(f"  value range: {np.nanmin(arr):.3f} .. {np.nanmax(arr):.3f} m")
        for lon, lat in TEST_POINTS:
            v_mine = sample_at(arr, lon0, lat0, dlon, dlat, lon, lat)
            try:
                v_ref = proj_ref(p, lon, lat)
            except Exception as e:  # noqa: BLE001
                print(f"  ({lon},{lat}) mine={v_mine} PROJ FAIL: {e}")
                continue
            diff = None if v_mine is None else abs(v_mine - v_ref)
            flag = "OK" if diff is not None and diff < 1e-4 else "MISMATCH"
            if flag != "OK":
                ok = False
            print(f"  ({lon},{lat}) mine={v_mine} proj={v_ref} diff={diff} [{flag}]")
    print("ALL OK" if ok else "HAS MISMATCH")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
