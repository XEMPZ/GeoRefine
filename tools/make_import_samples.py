# -*- coding: utf-8 -*-
"""生成样例导入数据：sample_data/样例导入数据/ 下按"分隔符 × 表头 × 点号列"变体组织。

数据由引擎自洽生成：平面坐标 = 高斯正算(L0=105, B0=30, h0=0, k=1, y0=500km)；
空间直角坐标 = WGS84 BLH→ECEF(H=100m)；BLH 角度以 d.ms 编码书写。
运行: $env:PYTHONIOENCODING='utf-8'; python tools/make_import_samples.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.core import anglefmt  # noqa: E402
from app.core.ellipsoid import WGS84, geodetic_to_ecef  # noqa: E402
from app.core.gk_engine import gauss_forward  # noqa: E402

D2R = math.pi / 180.0
OUT = _ROOT / "sample_data" / "样例导入数据"

# 固定测区点：B/L(十进制度)，L0=105
BLS = [(28.0, 103.5, "G1"), (30.26345678, 105.0, "G2"), (32.5, 107.9, "G3")]
L0 = 105.0
B0 = 30.0
H = 100.0


def _xy(b_deg, l_deg):
    x, y = gauss_forward(6378137.0, 298.257223563, L0 * D2R, B0 * D2R, 0.0, 1.0, 0.0, 500.0,
                         b_deg * D2R, l_deg * D2R, False)
    return f"{x:.6f}", f"{y:.6f}"


def _blh_dms(b_deg, l_deg):
    b = anglefmt.rad_to_dms(b_deg * D2R)
    l = anglefmt.rad_to_dms(l_deg * D2R)
    return f"{b:.8f}", f"{l:.8f}", f"{H:.1f}"


def _xyz(b_deg, l_deg):
    X, Y, Z = geodetic_to_ecef(b_deg, l_deg, H, WGS84)
    return f"{X:.4f}", f"{Y:.4f}", f"{Z:.4f}"


def w(rel: str, text: str):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    print("  ", rel)


def main():
    xy = [(*_xy(b, l), name) for b, l, name in BLS]          # (x, y, name)
    blh = [(*_blh_dms(b, l), name) for b, l, name in BLS]    # (Bdms, Ldms, H, name)
    xyz = [(*_xyz(b, l), name) for b, l, name in BLS]        # (X, Y, Z, name)

    print("生成样例导入数据 →", OUT)

    # 01 CSV 逗号分隔，有点号列
    w("01_csv_逗号分隔_有点号/平面坐标_xy.csv",
      "".join(f"{n},{x},{y}\n" for x, y, n in xy))
    w("01_csv_逗号分隔_有点号/大地坐标_BLH_dms.csv",
      "".join(f"{n},{b},{l},{h}\n" for b, l, h, n in blh))

    # 02 TXT 空格分隔，有点号列
    w("02_txt_空格分隔/平面坐标_xy.txt",
      "".join(f"{n} {x} {y}\n" for x, y, n in xy))
    w("02_txt_空格分隔/空间直角_XYZ.txt",
      "".join(f"{n} {x} {y} {z}\n" for x, y, z, n in xyz))

    # 03 TXT 制表符分隔，有点号列
    w("03_txt_制表符分隔/大地坐标_BLH_dms.txt",
      "".join(f"{n}\t{b}\t{l}\t{h}\n" for b, l, h, n in blh))

    # 04 CSV 分号分隔，有点号列
    w("04_csv_分号分隔/空间直角_XYZ.csv",
      "".join(f"{n};{x};{y};{z}\n" for x, y, z, n in xyz))

    # 05 无点号列（软件自动编号 P1..Pn）
    w("05_无点号列/平面坐标_xy_无点号.txt",
      "".join(f"{x} {y}\n" for x, y, _ in xy))
    w("05_无点号列/大地坐标_BLH_dms_无点号.csv",
      "".join(f"{b},{l},{h}\n" for b, l, h, _ in blh))

    # 06 带表头行（首行自动识别跳过）
    w("06_带表头行/平面坐标_xy_带表头.csv",
      "点号,x(m),y(m)\n" + "".join(f"{n},{x},{y}\n" for x, y, n in xy))
    w("06_带表头行/大地坐标_BLH_带表头.txt",
      "点号\tB(d.ms)\tL(d.ms)\tH(m)\n" + "".join(f"{n}\t{b}\t{l}\t{h}\n" for b, l, h, n in blh))

    # 07 XLSX（openpyxl）
    try:
        import openpyxl
    except ImportError:
        print("  [跳过 xlsx：未安装 openpyxl]")
        return
    (OUT / "07_xlsx").mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "平面坐标"
    ws.append(["点号", "x(m)", "y(m)"])
    for x, y, n in xy:
        ws.append([n, float(x), float(y)])
    wb.save(OUT / "07_xlsx" / "平面坐标_xy.xlsx")

    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "大地坐标"
    ws2.append(["点号", "B(d.ms)", "L(d.ms)", "H(m)"])
    for b, l, h, n in blh:
        ws2.append([n, float(b), float(l), float(h)])
    wb2.save(OUT / "07_xlsx" / "大地坐标_BLH_dms.xlsx")
    print("  ", "07_xlsx/平面坐标_xy.xlsx")
    print("  ", "07_xlsx/大地坐标_BLH_dms.xlsx")

    # README
    w("README.md", """# 样例导入数据

由 `tools/make_import_samples.py` 生成（固定测区点，数据自洽：平面坐标 = L0=105° 高斯正算；
空间直角坐标 = WGS84 BLH→ECEF，H=100 m；BLH 角度为 d.ms 编码）。可直接用于
"高斯投影换带"、"空间坐标转换"、"参数应用转换"三页的"读入源数据/源文件"。

| 文件夹 | 内容 | 适用页 |
|---|---|---|
| 01_csv_逗号分隔_有点号 | 逗号分隔，首列点号 | 换带(xy) / 空间转换·参数应用(BLH) |
| 02_txt_空格分隔 | 空格分隔 | 换带(xy) / 空间转换(XYZ) |
| 03_txt_制表符分隔 | 制表符分隔 | 空间转换·参数应用(BLH) |
| 04_csv_分号分隔 | 分号分隔 | 空间转换(XYZ) |
| 05_无点号列 | 仅坐标列，软件自动编号 P1..Pn | 换带(xy) / 空间转换·参数应用(BLH) |
| 06_带表头行 | 首行为表头，自动识别跳过 | 换带(xy) / 空间转换·参数应用(BLH) |
| 07_xlsx | Excel 工作簿 | 换带(xy) / 空间转换·参数应用(BLH) |

分隔符（逗号/空格/制表符/分号）、表头、点号列均自动识别，无需设置。
导入后坐标数值按页面当前的角度格式选择解释（d.ms 或 d）。
""")


if __name__ == "__main__":
    main()
