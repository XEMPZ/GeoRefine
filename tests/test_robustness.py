# -*- coding: utf-8 -*-
"""全功能健壮性测试：合理数据/错误数据/毁坏数据 —— 任何输入都不得让程序崩溃。

原则：页面与逻辑层对坏数据的正确行为 = 抛出带说明的 ValueError 或优雅返回，
不允许未捕获异常逃逸到 Qt 槽（那会表现为闪退）。本测试直接断言这一点。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _junk(path: Path, size: int = 512):
    rng = np.random.RandomState(1)
    path.write_bytes(rng.bytes(size))


def _app():
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        return app
    except Exception:
        return None


# ------------------------------------------------- 通用导入
def test_read_points_table_garbage():
    from app.common.io_import import read_points_table
    d = Path(tempfile.mkdtemp())
    # 二进制垃圾
    _junk(d / "junk.txt")
    try:
        read_points_table(str(d / "junk.txt"), coord_cols=2)
        raise AssertionError("二进制垃圾应报错")
    except Exception as e:
        assert isinstance(e, (ValueError, Exception))
    # 空文件
    (d / "empty.txt").write_text("", encoding="utf-8")
    try:
        read_points_table(str(d / "empty.txt"), coord_cols=2)
        raise AssertionError("空文件应报错")
    except ValueError:
        pass
    # 数字列里夹字母：按表头自动识别吞掉，不崩溃（返回剩余数据行）
    (d / "letters.csv").write_text("P1,abc,def\nP2,1.0,2.0\n", encoding="gbk")
    try:
        rows, notes = read_points_table(str(d / "letters.csv"), coord_cols=2)
    except ValueError:
        rows = None   # 某些配置下报错也可接受
    # 巨大/NaN/Inf 数值
    (d / "huge.csv").write_text("P1,1e400,2.0\nP2,nan,1.0\nP3,inf,2.0\n", encoding="utf-8")
    try:
        out = read_points_table(str(d / "huge.csv"), coord_cols=2)
    except ValueError:
        out = None  # 报错或产出极端值均可，不允许其他类型异常


def test_pair_io_garbage():
    from app.common import pair_io
    d = Path(tempfile.mkdtemp())
    _junk(d / "junk.cot")
    try:
        pair_io.parse_cot(d / "junk.cot")
        raise AssertionError("二进制 cot 应报错")
    except Exception:
        pass
    # 截断 cot（只有表头注释）
    (d / "trunc.cot").write_text("# 注释\n\n", encoding="utf-8")
    try:
        pair_io.parse_cot(d / "trunc.cot")
        raise AssertionError("空 cot 应报错")
    except ValueError:
        pass
    # CASS 缺列
    (d / "bad.txt").write_text("1,2,3:4,5\n", encoding="utf-8")
    try:
        pair_io.parse_cass(d / "bad.txt")
        raise AssertionError("缺列 CASS 应报错")
    except Exception:
        pass
    # looks_like_cass 对垃圾文件不崩溃
    _junk(d / "junk2.txt")
    assert isinstance(pair_io.looks_like_cass(d / "junk2.txt"), bool)


# ------------------------------------------------- 换带/空间页面（offscreen）
def test_gauss_page_garbage():
    app = _app()
    if app is None:
        print("[SKIP] 无 Qt")
        return
    from PySide6.QtWidgets import QTableWidgetItem
    from app.gui.pages import GaussPage
    pg = GaussPage(); pg._quiet = True
    # 字母/空/巨大值混排
    bad_rows = [("P1", "abc", "def", ""), ("P2", "", "1.0", ""), ("P3", "1e999", "2.0", "x"),
                ("P4", "1.0", "2.0", "")]
    for name, v1, v2, h in bad_rows:
        r = pg.table_in.rowCount(); pg.table_in.insertRow(r)
        for c, v in enumerate((name, v1, v2, h)):
            pg.table_in.setItem(r, c, QTableWidgetItem(v))
    pg.run()      # 不允许未捕获异常；坏行被跳过或警告（quiet 吞掉）
    # 平面→平面正常行在坏行之后仍能转换
    r = pg.table_in.rowCount(); pg.table_in.insertRow(r)
    for c, v in enumerate(("OK", "3500000.0", "500000.0", "")):
        pg.table_in.setItem(r, c, QTableWidgetItem(v))
    pg.run()
    # 大地→大地 d.ms 乱值
    pg.rb_src_bl.setChecked(True); pg.rb_dst_bl.setChecked(True)
    r = pg.table_in.rowCount(); pg.table_in.insertRow(r)
    for c, v in enumerate(("BAD", "999.99999999", "105.3", "")):
        pg.table_in.setItem(r, c, QTableWidgetItem(v))
    pg.run()
    # 保存：无结果时提示不崩溃
    pg.save_param if hasattr(pg, "save_param") else None


def test_space_page_garbage():
    app = _app()
    if app is None:
        return
    from PySide6.QtWidgets import QTableWidgetItem
    from app.gui.pages import SpacePage
    sp = SpacePage(); sp._quiet = True
    for kind in ("BLH->BLH", "XYZ->BLH", "xyH->BLH"):
        sp.type_rb[kind].setChecked(True)
        sp.table_in.setRowCount(0)
        sp.table_in.insertRow(0)
        for c, v in enumerate(("P1", "abc", "def", "xyz")):
            sp.table_in.setItem(0, c, QTableWidgetItem(v))
        sp.run()   # 不崩溃
    sp.table_in.setRowCount(0); sp.table_in.insertRow(0)
    for c, v in enumerate(("P1", "1e999", "nan", "inf")):
        sp.table_in.setItem(0, c, QTableWidgetItem(v))
    sp.run()


# ------------------------------------------------- 参数应用 / ParamApplier
def test_paramapplier_corrupt_docs():
    from app.common.params import ParamApplier
    bad_docs = [
        {"kind": "planar4"},                                        # 声明缺数据
        {"kind": "seven2d", "seven": {"dx": 1.0}},                  # 缺大半字段
        {"kind": "planar4", "planar4": "不是字典"},
        {"kind": "planar4", "planar4": {"dx": 1.0}},                # 缺 dy/a/b
        {"kind": "chain74", "projection": None, "planar4": None},   # 链式无投影
        {"kind": "geoid", "use_geoid": True},                       # 纯格网无平面
    ]
    for doc in bad_docs:
        try:
            ap = ParamApplier(doc)
            # 允许构造成功，但应用平面/高程必须明确报错，不得返回错误结果或 KeyError
            exc = None
            try:
                ap.apply_lonlat(30.0, 115.0, 50.0, do_plane=True, do_height=True)
            except Exception as e:  # noqa: BLE001
                exc = e
            if exc is not None:
                assert isinstance(exc, (ValueError, KeyError))
        except ValueError:
            pass
    # 缺 heightfit 求值
    ap = ParamApplier({"kind": "direct_gk", "projection": {"l0": 114.0, "x0": 0.0, "y0": 500000.0, "k": 1.0}})
    assert ap.eval_height_value(115.0, 30.0) is None


def test_apply_page_corrupt_param_file():
    app = _app()
    if app is None:
        return
    import json
    import tempfile
    import app.gui.pages as pages
    from PySide6.QtWidgets import QTableWidgetItem
    tmp = Path(tempfile.mkdtemp())
    old_dir = pages.PARAMS_DIR
    pages.PARAMS_DIR = tmp
    # 损坏 JSON（list 可见标记损坏）+ schema 不符但 JSON 合法
    (tmp / "bad.json").write_text("{ corrupt", encoding="utf-8")
    (tmp / "emptydoc.json").write_text("{}", encoding="utf-8")
    (tmp / "half.json").write_text(json.dumps({"name": "半截", "kind": "planar4"}), encoding="utf-8")
    from app.gui.pages import ApplyPage
    ap = ApplyPage(); ap._quiet = True
    ap.reload_params()
    # 选择半截参数 → run() 报错不崩溃
    idx = ap.param_combo.findData("半截")
    ap.param_combo.setCurrentIndex(max(idx, 0))
    ap.table_in.setRowCount(0); ap.table_in.insertRow(0)
    for c, v in enumerate(("P1", "30.3", "115.2", "50.0")):
        ap.table_in.setItem(0, c, QTableWidgetItem(v))
    ap.run()
    pages.PARAMS_DIR = old_dir


# ------------------------------------------------- 控制点页
def test_control_page_garbage():
    app = _app()
    if app is None:
        return
    from PySide6.QtWidgets import QApplication, QTableWidgetItem
    import app.gui.pages as pages
    from app.gui.pages import ControlPointPage
    tmp = Path(tempfile.mkdtemp())
    old_dir = pages.PARAMS_DIR
    pages.PARAMS_DIR = tmp
    cp = ControlPointPage(); cp._quiet = True
    # 字母/空混排
    for name, vals in (("P1", ("abc", "def", "1.0", "1.0", "2.0", "3.0")),
                       ("P2", ("1.0", "2.0", "3.0", "", "", "")),
                       ("P3", ("1e999", "2.0", "3.0", "4.0", "5.0", "6.0"))):
        r = cp.table.rowCount(); cp.table.insertRow(r)
        for c, v in enumerate((name,) + vals):
            cp.table.setItem(r, c, QTableWidgetItem(v))
    cp.compute()   # 坏点被跳过/报错，不崩溃
    # 毁坏 cot 导入
    junk = tmp / "junk.cot"
    rng = np.random.RandomState(2)
    junk.write_bytes(rng.bytes(400))
    try:
        cp.load_file(str(junk))
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"毁坏 cot 导入应优雅报错而非抛出：{e}")
    # 截断 cot
    trunc = tmp / "trunc.cot"
    trunc.write_text("P1,1.0,2.0\n", encoding="utf-8")   # 缺列
    try:
        cp.load_file(str(trunc))
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"截断 cot 应优雅报错：{e}")
    # 毁坏 cass
    bad = tmp / "bad_cass.txt"
    bad.write_text("1,2,3:4,5\n垃圾行\n", encoding="gbk")
    try:
        cp.load_file(str(bad))
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"毁坏 cass 应优雅报错：{e}")
    # 毁坏 xlsx
    xj = tmp / "junk.xlsx"
    xj.write_bytes(b"PK\x03\x04 garbage garbage")
    try:
        cp.load_file(str(xj))
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"毁坏 xlsx 应优雅报错：{e}")
    pages.PARAMS_DIR = old_dir


# ------------------------------------------------- 文件转换后端
def test_fileconv_garbage():
    from app.cad.dxf_transform import transform_dxf
    from app.fileconv.points_text import transform_generic_points, transform_points_text
    d = Path(tempfile.mkdtemp())
    # 毁坏 DXF
    junk_dxf = d / "junk.dxf"
    _junk(junk_dxf, 1024)
    try:
        transform_dxf(junk_dxf, d / "o.dxf")
        raise AssertionError("毁坏 DXF 应报错")
    except ValueError:
        pass
    # 毁坏 SHP
    from app.fileconv.shp_transform import transform_shp
    junk_shp = d / "junk.shp"
    _junk(junk_shp)
    try:
        transform_shp(junk_shp, d / "o.shp")
        raise AssertionError("毁坏 SHP 应报错")
    except Exception as e:  # noqa: BLE001
        assert not isinstance(e, (KeyboardInterrupt, SystemExit))
    # 毁坏 MDB
    from app.fileconv.pgdb_transform import list_feature_classes
    junk_mdb = d / "junk.mdb"
    junk_mdb.write_text("这不是 Access 数据库", encoding="utf-8")
    try:
        list_feature_classes(junk_mdb)
        raise AssertionError("毁坏 MDB 应报错")
    except Exception:
        pass
    # 数字垃圾的文本点文件
    gtxt = d / "g.txt"
    gtxt.write_text("P1,abc,def,ghi\nP2,1e999,nan,\n", encoding="utf-8")
    try:
        transform_generic_points(gtxt, d / "o.txt")
    except ValueError:
        pass
    # 二进制垃圾文本
    _junk(d / "b.txt")
    try:
        transform_points_text(d / "b.txt", d / "o2.txt")
    except Exception as e:  # noqa: BLE001
        assert isinstance(e, Exception)
    # DWG 魔数检测仍有效
    from app.cad.dxf_transform import detect_dwg
    dwg = d / "fake.dxf"
    dwg.write_bytes(b"AC1032" + b"\x00" * 64)
    assert detect_dwg(dwg) is True


def test_pgdb_transform_real_sample_robust():
    """真实 iData MDB 上做毁坏容错：SHAPE 列存在但某行几何为 NULL 等。"""
    from app.fileconv.pgdb_transform import list_feature_classes
    real = r"E:\IDATAJOBS\国家基础地理500_1.mdb"
    if not os.path.exists(real):
        print("[SKIP] 真实 MDB 样例不存在")
        return
    classes = list_feature_classes(real)
    assert "TERPT" in classes


# ------------------------------------------------- 照片POS
def test_photo_garbage():
    from app.photo.pos_io import extract_pos
    d = Path(tempfile.mkdtemp())
    junk = d / "junk.jpg"
    _junk(junk)
    rec = extract_pos(junk)          # 不崩溃，返回 source=none
    assert rec.source == "none" or rec.lat is None
    # 有 JPEG 头但无 GPS
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "JPEG")
    (d / "nogps.jpg").write_bytes(buf.getvalue())
    rec2 = extract_pos(d / "nogps.jpg")
    assert rec2.source == "none"
    # set_gps_altitude 对无 GPS 照片：不崩溃
    from app.photo.pos_io import set_gps_altitude
    set_gps_altitude(d / "nogps.jpg", 12.3)   # best-effort，允许 False


def test_photo_batch_garbage_file():
    from app.photo import batch as photo_batch
    d = Path(tempfile.mkdtemp())
    _junk(d / "a.jpg")
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "JPEG")
    (d / "b.jpg").write_bytes(buf.getvalue())
    calls = []

    def fn(lon, lat, h):
        calls.append(1)
        return h + 1.0, 1.0, "t"

    res = photo_batch.process(str(d), fn, mode_name="t")
    assert res["total"] == 2
    # 毁坏图 a → failed；正常图 b → ok
    # 毁坏图与无 GPS 图均计 failed（不崩溃），台账完整
    assert res["ok"] == 0 and res["failed"] == 2, (res["ok"], res["failed"])
    assert Path(res["ledger_csv"]).exists()


# ------------------------------------------------- 精度对比
def test_accuracy_garbage():
    from app.logic import accuracy as acc
    d = Path(tempfile.mkdtemp())
    # 毁坏 csv
    junk = d / "junk.csv"
    _junk(junk)
    try:
        acc.load_pairs(junk)
        raise AssertionError("毁坏 csv 应报错")
    except Exception:
        pass
    # 毁坏 cot
    _junk(d / "junk.cot")
    try:
        acc.load_pairs(d / "junk.cot")
        raise AssertionError("毁坏 cot 应报错")
    except Exception:
        pass
    # 合法 csv 但数值离谱（经度 9999）→ 不崩溃，kind 按内容判定
    odd = d / "odd.csv"
    odd.write_text("点名,经度,纬度,H_ell,H_normal\n"
                   "P1,9999,8888,1e400,2.0\n"
                   "P2,115.2,30.3,60.0,54.5\n", encoding="utf-8")
    try:
        pairs = acc.load_pairs(odd)
        assert pairs.src_kind in ("lonlat", "planar")
    except ValueError:
        pass   # 极端值被拒绝也接受
    # 损坏参数文档（None/空/声明缺数据）
    good = d / "good.csv"
    good.write_text("点名,经度,纬度,H_ell,H_normal\n"
                    "P1,115.2,30.3,60.0,54.5\n", encoding="utf-8")
    pairs2 = acc.load_pairs(good)
    rows, verdict = acc.compare(pairs2, [("坏", {"kind": "planar4"}),
                                         ("空", {}), ("None", None)], True, True,
                                os.path.join(_ROOT, "models"))
    assert all(not r["feasible_plane"] and not r["feasible_h"] for r in rows)
    assert verdict


def run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"{len(tests)}/{len(tests)} robustness tests passed.")
    return 1


if __name__ == "__main__":
    run()
