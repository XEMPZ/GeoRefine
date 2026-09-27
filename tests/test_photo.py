"""照片 POS 模块测试（ARCHITECTURE.md §8 test_photo）。提供 run()，兼容 pytest。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading

import numpy as np
from PySide6.QtCore import Qt  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import piexif  # noqa: E402
from PIL import Image  # noqa: E402

from app.photo.batch import process, rollback, scan_photos  # noqa: E402
from app.photo.pos_io import extract_pos, set_gps_altitude  # noqa: E402

XMP_TMPL = (b"http://ns.adobe.com/xap/1.0/\x00<?xpacket begin=\"\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>"
            b"<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
            b"<rdf:Description xmlns:drone-dji=\"http://www.dji.com/drone-dji/1.0/\" "
            b"drone-dji:AbsoluteAltitude=\"+52.36\" drone-dji:RelativeAltitude=\"+40.00\" "
            b"drone-dji:GpsLatitude=\"%.8f\" drone-dji:GpsLongitude=\"%.8f\"></rdf:Description>"
            b"</rdf:RDF></x:xmpmeta><?xpacket end?>")


def make_photo(path, lat, lon, h_ell, with_xmp=True):
    """合成 DJI 风格 JPG：EXIF GPS + XMP drone-dji。"""
    img = Image.new("RGB", (128, 96), (120, 140, 120))
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    data = buf.getvalue()

    def dms(v):
        v = abs(v)
        d = int(v)
        m_f = (v - d) * 60
        m = int(m_f)
        s = (m_f - m) * 60
        return [(d, 1), (m, 1), (int(round(s * 10000)), 10000)]

    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    exif_dict["0th"] = {
        piexif.ImageIFD.Make: b"DJI",
        piexif.ImageIFD.Model: b"TestDrone",
        piexif.ImageIFD.ImageWidth: 128,
        piexif.ImageIFD.ImageLength: 96,
    }
    exif_dict["GPS"] = {
        piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude: dms(lat),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude: dms(lon),
        piexif.GPSIFD.GPSAltitude: (int(round(h_ell * 100)), 100),
        piexif.GPSIFD.GPSAltitudeRef: 0,
    }
    exif_bytes = piexif.dump(exif_dict)   # dump 自带 "Exif\0\0" 前缀，直接作为 APP1 载荷
    exif_seg = b"\xff\xe1" + (len(exif_bytes) + 2).to_bytes(2, "big") + exif_bytes
    jpeg = data[:2] + exif_seg + data[2:]
    if with_xmp:
        xmp = XMP_TMPL % (lat, lon)
        seg = b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp
        jpeg = jpeg[:2] + seg + jpeg[2:]
    with open(path, "wb") as f:
        f.write(jpeg)


def _offset_fn(d):
    def fn(lon, lat, h):
        return h + d, d, f"常数偏移{d:+.2f}m"
    return fn


def test_scan_recursive():
    d = tempfile.mkdtemp()
    make_photo(os.path.join(d, "IMG_001.JPG"), 30.0, 114.0, 50.0)
    os.makedirs(os.path.join(d, "A", "B"))
    make_photo(os.path.join(d, "A", "IMG_002.jpg"), 30.001, 114.001, 51.0)
    make_photo(os.path.join(d, "A", "B", "IMG_003.jpeg"), 30.002, 114.002, 52.0)
    files = scan_photos(d)
    assert len(files) == 3, f"递归扫描应有 3 张, 实际 {len(files)}"
    shutil.rmtree(d)


def test_extract_and_roundtrip():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "IMG_001.JPG")
    make_photo(p, 30.123456, 114.654321, 52.36)
    pos = extract_pos(p)
    assert pos.source == "exif"
    assert abs(pos.lat - 30.123456) < 1e-6, pos.lat
    assert abs(pos.lon - 114.654321) < 1e-6
    assert abs(pos.h_ell - 52.36) < 0.01
    assert pos.abs_alt is not None and abs(pos.abs_alt - 52.36) < 0.01, "XMP AbsoluteAltitude 应可读"
    # 回写 + 恢复
    assert set_gps_altitude(p, 12.34) is True
    pos2 = extract_pos(p)
    assert abs(pos2.h_ell - 12.34) < 0.01, pos2.h_ell
    assert abs(pos2.lat - 30.123456) < 1e-6, "经纬度必须不动"
    # 负高程往返（低于基准面场景）：写入 |值|+Ref=1，读侧按 Ref 恢复符号
    assert set_gps_altitude(p, -12.34) is True
    pos3 = extract_pos(p)
    assert abs(pos3.h_ell - (-12.34)) < 0.01, f"负高程读回错误: {pos3.h_ell}"
    shutil.rmtree(d)


def test_process_batch_and_ledger():
    d = tempfile.mkdtemp()
    make_photo(os.path.join(d, "IMG_001.JPG"), 30.0, 114.0, 50.0)
    os.makedirs(os.path.join(d, "A"))
    make_photo(os.path.join(d, "A", "IMG_002.jpg"), 30.001, 114.001, 51.5)
    make_photo(os.path.join(d, "A", "IMG_003.jpg"), 30.002, 114.002, 55.0)  # 无POS对照
    p3 = os.path.join(d, "A", "IMG_003.jpg")
    Image.open(p3).convert("RGB").save(p3, "JPEG", quality=90)  # 抹掉 EXIF/XMP
    res = process(d, _offset_fn(-8.0), mode_name="offset")
    assert res["total"] == 3 and res["ok"] == 2 and res["failed"] == 1
    rows = res["rows"]
    r1 = [r for r in rows if r["relpath"].endswith("IMG_001.JPG")][0]
    assert r1["status"] == "ok"
    assert abs(r1["h_before"] - 50.0) < 0.01 and abs(r1["h_after"] - 42.0) < 0.01
    assert r1["v"] == -8.0
    assert os.path.exists(r1["backup"]), "备份必须存在"
    nopos = [r for r in rows if r["status"] == "no_pos"]
    assert len(nopos) == 1
    # 台账 CSV 存在且行数=照片数+表头
    assert os.path.exists(res["ledger_csv"])
    with open(res["ledger_csv"], encoding="utf-8-sig") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    assert len(lines) == 4, f"台账行数 {len(lines)}"
    # 处理后 EXIF 读回验证
    pos = extract_pos(os.path.join(d, "IMG_001.JPG"))
    assert abs(pos.h_ell - 42.0) < 0.01
    # 回滚
    rb = rollback(d)
    assert rb["restored"] == 2
    pos0 = extract_pos(os.path.join(d, "IMG_001.JPG"))
    assert abs(pos0.h_ell - 50.0) < 0.01, "回滚后应恢复原高程"
    shutil.rmtree(d)


def test_out_of_range_and_cancel():
    d = tempfile.mkdtemp()
    make_photo(os.path.join(d, "IMG_001.JPG"), 30.0, 114.0, 50.0)
    make_photo(os.path.join(d, "IMG_002.JPG"), 30.0, 114.0, 50.0)

    def fn(lon, lat, h):
        return None, None, "超出模型范围"

    res = process(d, fn)
    assert res["failed"] == 2 and all(r["status"] == "out_of_range" for r in res["rows"])
    # 取消：事件预设为已取消 → 全部 skipped
    ev = threading.Event()
    ev.set()
    res2 = process(d, _offset_fn(1.0), cancel=ev)
    assert res2["skipped"] == 2 and res2["ok"] == 0
    shutil.rmtree(d)


def test_batch_with_plane_fn():
    """勾选"同时转换平面坐标"：台账追加 转换后x/转换后y 两列。"""
    root = os.path.join(_ROOT, "sample_data", "样例照片POS")
    fn = lambda lon, lat, h: (h - 5.0, 5.0, "测试偏移")  # noqa: E731
    plane = lambda lon, lat: (lon * 10000.0, lat * 10000.0)  # noqa: E731
    res = process(root, fn, mode_name="平面测试", update_xmp=False,
                  plane_fn=plane, plane_name="测试平面")
    try:
        import csv
        with open(res["ledger_csv"], encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert "转换后x(m)" in rows[0] and "转换后y(m)" in rows[0]
        xi = rows[0].index("转换后x(m)")
        data = [r for r in rows[1:] if r[rows[0].index("状态")] == "ok"]
        assert data and all(float(r[xi]) > 0 for r in data)
    finally:
        rollback(root)
        shutil.rmtree(os.path.join(root, "_POS备份"), ignore_errors=True)
        import glob
        for f_ in glob.glob(os.path.join(root, "POS处理台账_*.csv")):
            os.remove(f_)


def test_worker_dry_run_then_apply():
    """照片页 计算预览/开始处理 分离：dry_run 不写盘，正式执行才备份回写。"""
    root = os.path.join(_ROOT, "sample_data", "样例照片POS")
    # QApplication 可能已被前序测试销毁（局部变量），跨线程信号投递必须有应用实例
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from app.gui.workers import PhotoBatchWorker
    results = []
    w1 = PhotoBatchWorker(root, {"mode": "offset"}, update_xmp=False, dry_run=True)
    # 槽必须持引用（PySide6 对无 QObject 接收者的 lambda 连接可能被 GC）
    _slot1 = lambda r: results.append(r)  # noqa: E731
    # 无 QObject 接收者的跨线程信号须显式直连（队列连接会因工作线程无事件循环而丢失）
    w1.finished_ok.connect(_slot1, type=Qt.DirectConnection)
    w1.start()
    w1.wait(30000)
    assert results and results[0].get("dry_run") is True
    assert results[0]["ledger_csv"] is None
    assert results[0]["ok"] == 8
    assert not os.path.isdir(os.path.join(root, "_POS备份")), "预览不应创建备份"
    w2 = PhotoBatchWorker(root, {"mode": "offset"}, update_xmp=False, dry_run=False)
    out = []
    _slot2 = lambda r: out.append(r)  # noqa: E731
    w2.finished_ok.connect(_slot2, type=Qt.DirectConnection)
    w2.start()
    w2.wait(30000)
    assert out and out[0]["ok"] == 8
    assert out[0]["ledger_csv"] and os.path.isfile(out[0]["ledger_csv"])
    assert os.path.isdir(os.path.join(root, "_POS备份")), "正式执行应有备份"
    rb = rollback(root)
    assert rb["restored"] == 8
    shutil.rmtree(os.path.join(root, "_POS备份"), ignore_errors=True)
    import glob
    for f_ in glob.glob(os.path.join(root, "POS处理台账_*.csv")):
        os.remove(f_)


def run():
    fns = [test_scan_recursive, test_extract_and_roundtrip,
           test_process_batch_and_ledger, test_out_of_range_and_cancel,
           test_worker_dry_run_then_apply]
    for fn in fns:
        fn()
    return len(fns)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed.")
    sys.exit(1 if failed else 0)
