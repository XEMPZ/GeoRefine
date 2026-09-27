"""下载公开大地水准面格网模型到 models/ 目录。

用法:  python tools/download_geoid_models.py
模型:  EGM96 (us_nga_egm96_15.gtx, ~2MB, 15分格网)
       EGM2008 (us_nga_egm08_25.tif, ~8MB, 2.5分格网, 读取需 tifffile)
来源:  https://cdn.proj.org （PROJ 官方 CDN，公开数据）
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

MODELS = {
    "us_nga_egm96_15.gtx": "https://cdn.proj.org/us_nga_egm96_15.gtx",
    "us_nga_egm08_25.tif": "https://cdn.proj.org/us_nga_egm08_25.tif",
}


def download(models_dir: Path):
    models_dir.mkdir(parents=True, exist_ok=True)
    ok = []
    for fname, url in MODELS.items():
        dest = models_dir / fname
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"[skip] {fname} 已存在")
            ok.append(fname)
            continue
        print(f"[down] {url}")
        try:
            urllib.request.urlretrieve(url, dest)
            print(f"[ok]   {fname} -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
            ok.append(fname)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {fname}: {e}")
    return ok


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    d = root / "models"
    if len(sys.argv) > 1:
        d = Path(sys.argv[1])
    download(d)
