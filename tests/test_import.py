"""通用导入测试（io_import + 样例数据全量回归）。

样例由 tools/make_import_samples.py 生成，覆盖 分隔符(逗号/空格/制表符/分号) ×
表头(有/无) × 点号列(有/无) × 格式(csv/txt/xlsx) 变体。
运行: $env:PYTHONIOENCODING='utf-8'; python tests\test_import.py
"""
from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

D2R = math.pi / 180.0
SAMPLE_DIR = os.path.join(_ROOT, "sample_data", "样例导入数据")

from app.common.io_import import read_points_table  # noqa: E402


def _expected_xy():
    from app.core.gk_engine import gauss_forward
    bls = [(28.0, 103.5, "G1"), (30.26345678, 105.0, "G2"), (32.5, 107.9, "G3")]
    return [(n,) + gauss_forward(6378137.0, 298.257223563, 105 * D2R, 30 * D2R, 0.0, 1.0,
                                 0.0, 500.0, b * D2R, l * D2R, False)
            for b, l, n in bls]


def _expected_blh_dms():
    from app.core import anglefmt
    bls = [(28.0, 103.5, "G1"), (30.26345678, 105.0, "G2"), (32.5, 107.9, "G3")]
    return [(n, anglefmt.rad_to_dms(b * D2R), anglefmt.rad_to_dms(l * D2R), 100.0)
            for b, l, n in bls]


def _expected_xyz():
    from app.core.ellipsoid import WGS84, geodetic_to_ecef
    bls = [(28.0, 103.5, "G1"), (30.26345678, 105.0, "G2"), (32.5, 107.9, "G3")]
    return [(n, *geodetic_to_ecef(b, l, 100.0, WGS84)) for b, l, n in bls]


def _check(rows, expected, named=True, atol=1e-6):
    assert len(rows) == 3, f"行数 {len(rows)}"
    for i, (exp, row) in enumerate(zip(expected, rows)):
        name, vals = row
        if named:
            assert name == exp[0], f"第{i}行点号 {name} != {exp[0]}"
        else:
            assert name == "", f"无点号文件不应有点号：{name}"
        for j, ev in enumerate(exp[1:]):
            assert abs(vals[j] - ev) < atol, f"第{i}行第{j}列 {vals[j]} != {ev}"


def test_delimiter_variants_xy():
    exp = _expected_xy()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "01_csv_逗号分隔_有点号", "平面坐标_xy.csv"), 2)
    _check(rl, exp)
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "02_txt_空格分隔", "平面坐标_xy.txt"), 2)
    _check(rl, exp)


def test_delimiter_variants_blh():
    exp = _expected_blh_dms()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "01_csv_逗号分隔_有点号", "大地坐标_BLH_dms.csv"), 3)
    _check(rl, exp)
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "03_txt_制表符分隔", "大地坐标_BLH_dms.txt"), 3)
    _check(rl, exp)


def test_delimiter_variants_xyz():
    exp = _expected_xyz()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "02_txt_空格分隔", "空间直角_XYZ.txt"), 3)
    _check(rl, exp, atol=1e-3)
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "04_csv_分号分隔", "空间直角_XYZ.csv"), 3)
    _check(rl, exp, atol=1e-3)


def test_no_name_column():
    exp = _expected_xy()
    rows_list, _notes = read_points_table(os.path.join(SAMPLE_DIR, "05_无点号列", "平面坐标_xy_无点号.txt"), 2)
    _check(rows_list, exp, named=False)
    exp3 = _expected_blh_dms()
    rows3, _n = read_points_table(os.path.join(SAMPLE_DIR, "05_无点号列", "大地坐标_BLH_dms_无点号.csv"), 3)
    _check(rows3, exp3, named=False)


def test_header_row_skipped():
    exp = _expected_xy()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "06_带表头行", "平面坐标_xy_带表头.csv"), 2)
    _check(rl, exp)
    exp3 = _expected_blh_dms()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "06_带表头行", "大地坐标_BLH_带表头.txt"), 3)
    _check(rl, exp3)


def test_xlsx():
    exp = _expected_xy()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "07_xlsx", "平面坐标_xy.xlsx"), 2)
    _check(rl, exp)
    exp3 = _expected_blh_dms()
    rl, _n = read_points_table(os.path.join(SAMPLE_DIR, "07_xlsx", "大地坐标_BLH_dms.xlsx"), 3)
    _check(rl, exp3)


def test_malformed_row_raises():
    import tempfile
    bad = os.path.join(tempfile.gettempdir(), "_bad_points.txt")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("G1 3099348.64467\n")  # 缺一列
    try:
        read_points_table(bad, coord_cols=2)
        assert False, "缺列应报错"
    except ValueError:
        pass
    finally:
        os.remove(bad)


def test_gbk_encoding():
    # 国产测量软件常导出 GBK：中文点号与中文表头
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "_gbk_points.txt")
    with open(p, "w", encoding="gbk") as f:
        f.write("点号\tx\ty\n")
        f.write("控制点1\t3099348.64467\t352447.704379\n")
        f.write("控制点2\t3098441.74740\t500000.00000\n")
    rows_gbk, _notes = read_points_table(p, coord_cols=2)
    assert len(rows_gbk) == 2
    assert rows_gbk[0][0] == "控制点1", rows_gbk[0][0]
    assert abs(rows_gbk[1][1][0] - 3098441.74740) < 1e-3
    os.remove(p)


def run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"{len(tests)}/{len(tests)} import tests passed.")
    return 1


if __name__ == "__main__":
    run()
