"""生成全套样例数据（真值可复算，固定随机种子）。

产出（sample_data/）:
  控制点样本_平面对.csv     12 点，真值四参数 + 平面高程拟合 + mm 级噪声
  控制点样本_空间对.csv     10 点，真值七参数 + 噪声
  样例测区.dxf              9 类实体 + INSERT，与平面对同测区
  样例照片POS/              8 张 DJI 风格 JPG（EXIF GPS + XMP，嵌套子目录）
  局部似大地水准面格网_样例.csv  0.01° 规则格网 21×21
  精度对比样本.csv          15 点（由样例格网 + 噪声合成）
  README.md                 真值参数与用法

真值四参数: dx=+102.3456, dy=−56.7890, scale=0.9999975, rot=+0.25″（x北,y东）
真值七参数: dx=+1.234, dy=−2.345, dz=+3.456 (m), rx=+0.30″, ry=−0.20″, rz=+0.15″, scale=2.5ppm
样例格网:   ξ(lon,lat) = 12.0 + 0.0008·(lon−114)·100 + 0.0005·(lat−30)·100
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.core.projection import gauss_kruger  # noqa: E402
from app.core.transform2d import Param4, apply_4param  # noqa: E402
from app.core.transform3d import Param7, apply_7param  # noqa: E402
from app.core.ellipsoid import geodetic_to_ecef  # noqa: E402

OUT = _ROOT / "sample_data"
TRUE_P4 = Param4(dx=102.3456, dy=-56.7890,
                 a=0.9999975 * np.cos(np.radians(0.25 / 3600)),
                 b=0.9999975 * np.sin(np.radians(0.25 / 3600)))
TRUE_P7 = Param7(dx=1.234, dy=-2.345, dz=3.456, rx_s=0.30, ry_s=-0.20, rz_s=0.15, scale_ppm=2.5)
L0 = 114.0


def xi_sample(lon, lat):
    return 12.0 + 0.0008 * (lon - 114.0) * 100.0 + 0.0005 * (lat - 30.0) * 100.0


def gen_planar_pairs():
    rng = np.random.default_rng(42)
    x = 3354000 + rng.uniform(-2000, 2000, 12)
    y = 500000 + rng.uniform(-2500, 2500, 12)
    h1 = rng.uniform(30, 60, 12)
    x2, y2 = apply_4param(TRUE_P4, x, y)
    # 高程差真值：平面（中心化于末点）
    dh = 0.05 + 2e-5 * (x2 - x2[-1]) - 1.5e-5 * (y2 - y2[-1])
    h2 = h1 + dh + rng.normal(0, 0.008, 12)
    x2 = x2 + rng.normal(0, 0.005, 12)
    y2 = y2 + rng.normal(0, 0.005, 12)
    lines = ["# 控制点样本_平面对（源=WGS84投影平面, 目标=地方坐标系; x北,y东）",
             f"# 真值四参数: dx={TRUE_P4.dx:.4f}, dy={TRUE_P4.dy:.4f}, scale={TRUE_P4.scale:.9f}, rot=+0.25″",
             "点名,源x,源y,源h,目标x,目标y,目标h"]
    for i in range(12):
        lines.append(f"P{i+1},{x[i]:.4f},{y[i]:.4f},{h1[i]:.4f},{x2[i]:.4f},{y2[i]:.4f},{h2[i]:.4f}")
    (OUT / "控制点样本_平面对.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def gen_spatial_pairs():
    rng = np.random.default_rng(43)
    lat = rng.uniform(29.9, 30.1, 10)
    lon = rng.uniform(113.9, 114.1, 10)
    h = rng.uniform(30, 60, 10)
    x, y, z = geodetic_to_ecef(lat, lon, h)
    x2, y2, z2 = apply_7param(TRUE_P7, x, y, z)
    from app.core.ellipsoid import ecef_to_geodetic
    lat2, lon2, h2 = ecef_to_geodetic(x2, y2, z2)
    lat2 = lat2 + rng.normal(0, 2e-9, 10)   # ≈ mm 级
    lon2 = lon2 + rng.normal(0, 2e-9, 10)
    h2 = h2 + rng.normal(0, 0.006, 10)
    lines = ["# 控制点样本_空间对（源=WGS84 BLH, 目标=转换后 BLH; 角度度, 高程米）",
             f"# 真值七参数: dx=+1.234, dy=−2.345, dz=+3.456 m; rx=+0.30″, ry=−0.20″, rz=+0.15″; scale=+2.5ppm",
             "点名,源B,源L,源H,目标B,目标L,目标H"]
    for i in range(10):
        lines.append(f"S{i+1},{lat[i]:.9f},{lon[i]:.9f},{h[i]:.4f},{lat2[i]:.9f},{lon2[i]:.9f},{h2[i]:.4f}")
    (OUT / "控制点样本_空间对.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def gen_dxf():
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer in ("地形点", "等高线", "建筑物", "注记"):
        doc.layers.add(layer)
    rng = np.random.default_rng(44)
    # 地形点：与平面对同测区（源坐标系）
    pts = [(3354000 + rng.uniform(-2000, 2000), 500000 + rng.uniform(-2500, 2500),
            35 + rng.uniform(0, 25)) for _ in range(20)]
    for i, (x, y, h) in enumerate(pts):
        msp.add_point((y, x, h), dxfattribs={"layer": "地形点"})   # CAD(X东,Y北)
        msp.add_text(f"D{i+1} H={h:.2f}", dxfattribs={"layer": "注记", "height": 8}).set_placement((y + 5, x + 5, h))
    # 等高线（3D 多段线）
    for k in range(3):
        zs = 38 + k * 4
        vs = [(500000 + 300 * i - 600, 3354000 + 80 * np.sin(i / 2) + 200 * k, zs + i * 0.2) for i in range(12)]
        msp.add_polyline3d(vs, dxfattribs={"layer": "等高线"})
    msp.add_lwpolyline([(499500, 3353500), (501000, 3353500), (501000, 3356500), (499500, 3356500)],
                       dxfattribs={"layer": "建筑物"})
    msp.add_circle((500500, 3355000, 42.0), radius=150, dxfattribs={"layer": "建筑物"})
    msp.add_arc((500000, 3355000, 40.0), radius=300, start_angle=30, end_angle=210, dxfattribs={"layer": "道路"})
    blk = doc.blocks.new(name="控标")
    blk.add_line((0, 0, 0), (20, 0, 0))
    blk.add_line((0, 0, 0), (0, 20, 0))
    msp.add_blockref("控标", (500200, 3354200, 39.0), dxfattribs={"layer": "地形点"})
    msp.add_mtext("样例测区 Demo\nGeoRefine", dxfattribs={"layer": "注记", "char_height": 20}).set_location((500600, 3356200, 45.0))
    doc.saveas(OUT / "样例测区.dxf")


def gen_photos():
    sys.path.insert(0, str(_ROOT / "tests"))
    from test_photo import make_photo  # 复用合成器
    rng = np.random.default_rng(45)
    subdirs = ["", "A", "A/B"]
    for i in range(8):
        lat = 29.9 + rng.uniform(0, 0.2)
        lon = 113.9 + rng.uniform(0, 0.2)
        h = 35 + rng.uniform(0, 25)
        sub = OUT / "样例照片POS" / subdirs[i % len(subdirs)]
        sub.mkdir(parents=True, exist_ok=True)
        make_photo(str(sub / f"IMG_{i+1:03d}.JPG"), lat, lon, h)


def gen_grid_csv():
    lons = np.arange(113.90, 114.101, 0.01)
    lats = np.arange(29.90, 30.101, 0.01)
    lines = ["# 局部似大地水准面格网样例（列: lon,lat,ξ m; 规则格网 0.01°）",
             "# ξ(lon,lat) = 12.0 + 0.0008*(lon-114)*100 + 0.0005*(lat-30)*100"]
    for la in lats:
        for lo in lons:
            lines.append(f"{lo:.2f},{la:.2f},{xi_sample(lo, la):.4f}")
    (OUT / "局部似大地水准面格网_样例.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def gen_accuracy_sample():
    rng = np.random.default_rng(7)
    lines = ["# 精度对比样本（列: 点名,lon,lat,H_ell,H_normal）",
             "# H_normal = H_ell − ξ(样例格网) + N(0,0.02) 种子7",
             "点名,lon,lat,H_ell,H_normal"]
    rows = []
    for i in range(15):
        lon = 113.92 + rng.uniform(0, 0.16)
        lat = 29.92 + rng.uniform(0, 0.16)
        h_ell = 35 + rng.uniform(0, 25)
        h_norm = h_ell - xi_sample(lon, lat) + rng.normal(0, 0.02)
        rows.append(f"G{i+1},{lon:.6f},{lat:.6f},{h_ell:.4f},{h_norm:.4f}")
    (OUT / "精度对比样本.csv").write_text("\n".join(lines + rows) + "\n", encoding="utf-8-sig")


def gen_readme():
    (OUT / "README.md").write_text(
        "# 样例数据说明\n\n"
        "| 文件 | 内容 | 真值/公式 |\n|---|---|---|\n"
        "| 控制点样本_平面对.csv | 12 点平面控制点对 | dx=+102.3456, dy=−56.7890, scale=0.9999975, rot=+0.25″；dh=平面(中心=末点)；噪声平面5mm/高程8mm（种子42） |\n"
        "| 控制点样本_空间对.csv | 10 点空间控制点对 | dx=+1.234, dy=−2.345, dz=+3.456 m; rx=+0.30″, ry=−0.20″, rz=+0.15″, scale=+2.5ppm（种子43） |\n"
        "| 样例测区.dxf | 9 类实体（POINT/TEXT/LWPOLYLINE/POLYLINE3D/CIRCLE/ARC/INSERT/MTEXT/LINE），4 图层 | 坐标为源系（CAD X东,Y北） |\n"
        "| 样例照片POS/ | 8 张 DJI 风格 JPG（EXIF GPS+XMP），嵌套 A/B 子目录 | 椭球高 35~60m |\n"
        "| 局部似大地水准面格网_样例.csv | 21×21 规则格网 0.01° | ξ = 12.0 + 0.0008·(lon−114)·100 + 0.0005·(lat−30)·100 |\n"
        "| 精度对比样本.csv | 15 点 lon/lat/H_ell/H_normal | H_normal = H_ell − ξ + N(0,0.02)（种子7） |\n\n"
        "重新生成: `python tools/make_sample_data.py`\n",
        encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gen_planar_pairs()
    gen_spatial_pairs()
    gen_dxf()
    gen_photos()
    gen_grid_csv()
    gen_accuracy_sample()
    gen_readme()
    print("[ok] sample_data 已生成:")
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(OUT)} ({p.stat().st_size} B)")


if __name__ == "__main__":
    main()
