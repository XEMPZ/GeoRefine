"""文件转换链路测试：seven2d→四参数等价、SHP、CASS dat、通用文本、南方 ipj 导入、lonlat 高程。"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.common.hconv import make_cad_height_fn  # noqa: E402
from app.common.params import ParamApplier, ParamLibrary, seven2d_to_param4  # noqa: E402
from app.core.transform2d import Param4, apply_4param  # noqa: E402
from app.core.transform3d import Param7, apply_7param  # noqa: E402


def _mk_p4(a=1.0001, b=2.0e-5, dx=10.0, dy=-5.0):
    return Param4(dx, dy, a, b)


def test_seven2d_to_param4_equivalence():
    rng = np.random.RandomState(7)
    cx, cy = 2860000.0, 523000.0
    p7 = Param7(dx=1.2, dy=-0.7, dz=0.3, rx_s=0.0, ry_s=0.0, rz_s=12.5, scale_ppm=4.0)
    param = {"kind": "seven2d", "seven": {"dx": p7.dx, "dy": p7.dy, "dz": p7.dz,
                                          "rx_s": p7.rx_s, "ry_s": p7.ry_s, "rz_s": p7.rz_s,
                                          "scale_ppm": p7.scale_ppm, "center": [cx, cy]}}
    p4 = seven2d_to_param4(param)
    ap = ParamApplier(param)
    for x, y in [(cx, cy), (cx + 1000, cy + 500), (cx - 800, cy + 2000), (cx + 3500, cy - 2700),
                 (cx + rng.uniform(-5000, 5000), cy + rng.uniform(-5000, 5000))]:
        x7, y7, _ = ap.apply_planar(x, y, 0.0)
        x4, y4 = apply_4param(p4, x, y)
        assert abs(x7 - x4) < 1e-6 and abs(y7 - y4) < 1e-6, (x, y, x7 - x4, y7 - y4)


def test_shp_roundtrip(tmp=None):
    import shapefile
    from app.fileconv.shp_transform import transform_shp

    d = Path(tempfile.mkdtemp()) if tmp is None else Path(tmp)
    p4 = _mk_p4()
    # 写点 + 线样例
    wp = shapefile.Writer(target=str(d / "pts.shp"), shapeType=1)
    wp.field("名称", "C", 20)
    pts = [(500000.0, 3000000.0), (500100.5, 3000250.25), (499900.1, 2999800.75)]
    for i, (X, Y) in enumerate(pts):
        wp.point(X, Y)
        wp.record(f"P{i}")
    wp.close()
    wl = shapefile.Writer(target=str(d / "ln.shp"), shapeType=3)
    wl.field("编码", "C", 10)
    wl.line([[[500000.0, 3000000.0], [501000.0, 3001000.0], [502000.0, 3000500.0]]])
    wl.record("L1")
    wl.close()
    fn = lambda X, Y: apply_4param(p4, Y, X)  # noqa: E731  (X东,Y北)→测量
    for name, n in (("pts.shp", 3), ("ln.shp", 1)):
        res = transform_shp(d / name, d / f"{name[:-4]}_转换后.shp", planar_fn=fn)
        assert res.transformed == n and res.total == n, (name, res)
    r = shapefile.Reader(str(d / "pts_转换后.shp"))
    assert len(r) == 3
    recs = [r.record(i)[0] for i in range(3)]
    assert recs == ["P0", "P1", "P2"], recs
    for i, (X, Y) in enumerate(pts):
        x2, y2 = apply_4param(p4, Y, X)
        got = r.shape(i).points[0]
        assert abs(got[0] - x2) < 1e-6 and abs(got[1] - y2) < 1e-6, (i, got, x2, y2)
    rl = shapefile.Reader(str(d / "ln_转换后.shp"))
    seg = rl.shape(0).points
    assert len(seg) == 3 and all(abs(p[0]) + abs(p[1]) > 0 for p in seg)


def test_cass_dat(tmp=None):
    from app.fileconv.points_text import read_cass_dat, transform_cass_dat

    d = Path(tempfile.mkdtemp()) if tmp is None else Path(tmp)
    p4 = _mk_p4()
    src = d / "a.dat"
    src.write_text("P1,DC,500000.0000,3000000.0000,50.0000\n"
                   "P2,,500100.0000,3000100.0000,\n", encoding="utf-8")
    hfn = make_cad_height_fn("offset", offset=2.5)
    fn = lambda x, y: apply_4param(p4, x, y)  # noqa: E731  测量系
    res = transform_cass_dat(src, d / "b.dat", planar_fn=fn,
                             height_fn=lambda x, y, z: hfn(y, x, z))  # CAD 系适配
    assert res.transformed == 2
    rows = read_cass_dat(d / "b.dat")
    x2, y2 = apply_4param(p4, 3000000.0, 500000.0)
    assert abs(rows[0]["x"] - x2) < 1e-3 and abs(rows[0]["y"] - y2) < 1e-3, rows[0]
    assert rows[0]["code"] == "DC" and rows[0]["name"] == "P1"
    assert abs(rows[0]["h"] - 52.5) < 1e-6          # 50 + 2.5
    assert rows[1]["h"] is None                      # 无高程透传为空


def test_generic_points(tmp=None):
    from app.fileconv.points_text import transform_generic_points

    d = Path(tempfile.mkdtemp()) if tmp is None else Path(tmp)
    p4 = _mk_p4()
    src = d / "pts.csv"
    src.write_text("点名,x,y,h\nA,100.0,200.0,10.0\nB,300.0,400.0,\n", encoding="utf-8")
    res = transform_generic_points(src, d / "out.csv", planar_fn=lambda x, y: apply_4param(p4, x, y))
    assert res.transformed == 2
    lines = (d / "out.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    x2, y2 = apply_4param(p4, 100.0, 200.0)
    assert lines[0].startswith("A,") and f"{x2:.4f}" in lines[0] and "10.0000" in lines[0], lines
    assert lines[1].startswith("B,") and lines[1].count(",") == 2, lines  # 无高程列


class _TmpParams:
    """临时参数目录上下文（避免污染真实 params/）。"""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp())

    def __enter__(self):
        return str(self.path)

    def __exit__(self, *a):
        pass


def test_ipj_import():
    from app.fileconv.south_ipj import import_ipj, read_ipj

    p4 = _mk_p4(a=1.00005, b=3.0e-5, dx=12.0, dy=-8.0)
    rng = np.random.RandomState(11)
    with _TmpParams() as pdir:
        db = Path(pdir) / "sample.ipj"
        con = sqlite3.connect(str(db))
        c = con.cursor()
        c.execute("CREATE TABLE Ellipsoid (ID INT, Name VARCHAR, A VARCHAR, E1 VARCHAR)")
        c.execute("INSERT INTO Ellipsoid VALUES (4,'CGCS2000','6378137.0','298.257222101')")
        c.execute("CREATE TABLE Projection (ID INTEGER, EsID INT, Name VARCHAR, CentralMeridian VARCHAR,"
                  " Yardstick VARCHAR, EastDeclination VARCHAR, NorthDeclination VARCHAR,"
                  " BandNum VARCHAR, BandType VARCHAR, Scale VARCHAR, OutputBandNum BOOL)")
        c.execute("INSERT INTO Projection VALUES (3,4,'XIAN80_3_Degree_GK_Zone_113','113.0000000000',"
                  "NULL,'500000.0','0.0','','-1',NULL,0)")
        c.execute("CREATE TABLE Parm4 (ID INT, Name VARCHAR, North VARCHAR, East VARCHAR,"
                  " Rotate VARCHAR, Scale VARCHAR)")
        c.execute("INSERT INTO Parm4 VALUES (0,'X','10000','10000','0','1')")
        c.execute("CREATE TABLE Parm7 (ID INT, Name VARCHAR, X VARCHAR, Y VARCHAR, Z VARCHAR,"
                  " K VARCHAR, A VARCHAR, B VARCHAR, RMS VARCHAR, R VARCHAR)")
        c.execute("INSERT INTO Parm7 VALUES (0,'t','18.85','-26.90','18.88','0.000003',"
                  "'0.0000009','0.0000098','','-0.0000174')")
        c.execute("CREATE TABLE IdenticalPoint (Name VARCHAR2, X1 DOUBLE, Y1 DOUBLE, Z1 DOUBLE,"
                  " X2 DOUBLE, Y2 DOUBLE, Z2 DOUBLE, Rms DOUBLE)")
        base = np.array([2860000.0, 523000.0])
        for i in range(5):
            x, y = base + rng.uniform(-3000, 3000, 2)
            x2, y2 = apply_4param(p4, x, y)
            c.execute("INSERT INTO IdenticalPoint VALUES (?,?,?,?,?,?,?,?)",
                      (f"P{i}", float(x), float(y), 50.0, float(x2), float(y2), 52.0, None))
        con.commit()
        con.close()

        d = read_ipj(db)
        assert d["Projection"] and d["Parm4"] and len(d["IdenticalPoint"]) == 5
        r = import_ipj(db, pdir)
        assert any("投影参数" in k for k in r["counts"]), r
        lib = ParamLibrary(pdir)
        names = [p["name"] for p in lib.list()]
        # 投影参数：字段语义验证
        gk = [n for n in names if "Zone_113" in n][0]
        doc = lib.load(gk)
        assert doc["kind"] == "direct_gk"
        assert abs(doc["projection"]["l0"] - 113.0) < 1e-9
        assert abs(doc["projection"]["y0"] - 500000.0) < 1e-9
        assert abs(doc["projection"]["x0"]) < 1e-9
        assert doc["ellipsoid"] == "CGCS2000"
        # 公共点拟合：无论择优 planar4 还是 seven2d，应用后须还原真值相似变换
        fit = [n for n in names if "公共点拟合" in n][0]
        doc2 = lib.load(fit)
        assert doc2["kind"] in ("planar4", "seven2d"), doc2["kind"]
        ap = ParamApplier(doc2)
        for x, y in [(2860000.0, 523000.0), (2861234.5, 522987.6), (2859123.0, 525432.1)]:
            gx, gy, _h = ap.apply_planar(x, y, 0.0)
            ex, ey = apply_4param(p4, x, y)
            assert abs(gx - ex) < 1e-3 and abs(gy - ey) < 1e-3, (x, y, gx - ex, gy - ey)
        # Parm7 参考项
        assert any("Parm7_参考" in n for n in names), names


def test_hconv_lonlat_heightfit():
    """经纬度空间拟合 + 投影参数：CAD 平面 → 反算 BL → 求常数 ξ。"""
    param = {"kind": "direct_gk", "ellipsoid": "CGCS2000",
             "projection": {"l0": 113.0, "x0": 0.0, "y0": 500000.0, "k": 1.0},
             "heightfit": {"mode": "const", "value_type": "xi", "space": "lonlat",
                           "coef": [5.0], "center": [113.0, 30.0], "loo_rms": 0.0}}
    fn = make_cad_height_fn("param", param=param)
    from app.core.projection import gauss_kruger
    x, y = gauss_kruger(30.0, 113.0, 113.0)   # CM 上的点 → (x北, y东)
    z_new, v, detail = fn(float(y), float(x), 100.0)   # CAD(X东=y, Y北=x)
    assert abs(z_new - 95.0) < 1e-6, (z_new, v, detail)
    # 无投影参数的 lonlat 拟合应明确拒绝
    param2 = dict(param)
    param2["projection"] = None
    fn2 = make_cad_height_fn("param", param=param2)
    z2, v2, d2 = fn2(float(y), float(x), 100.0)
    assert z2 is None and "投影" in d2


def run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"{len(tests)}/{len(tests)} fileconv tests passed.")
    return 1



# --------------------------------------------------------------- PGDB (MDB)
def _blob_point3d(x, y, z):
    import struct
    return struct.pack("<i3d", 9, x, y, z)


def _blob_line3d(pts, zs, parts=None):
    import struct
    parts = parts or [0]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    out = struct.pack("<i", 10)
    out += struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
    out += struct.pack("<2i", len(parts), len(pts))
    out += struct.pack("<%di" % len(parts), *parts)
    for x, y in pts:
        out += struct.pack("<2d", x, y)
    out += struct.pack("<2d", min(zs), max(zs))
    for z in zs:
        out += struct.pack("<d", z)
    return out


def _make_pgdb(path):
    """ADOX 造真实 Access MDB + 要素类（按实测 blob 布局写入）。"""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    if os.path.exists(path):
        os.remove(path)
    cat = win32com.client.Dispatch("ADOX.Catalog")
    cat.Create(r"Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" + path)
    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open(r"Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" + path)
    conn.Execute("CREATE TABLE DEMO_PT (OBJECTID INT, SHAPE LONGBINARY, NAME VARCHAR(50))")
    conn.Execute("CREATE TABLE DEMO_LK (OBJECTID INT, SHAPE LONGBINARY, CODE VARCHAR(20))")
    rs = win32com.client.Dispatch("ADODB.Recordset")
    rs.Open("DEMO_PT", conn, 1, 3)      # adOpenKeyset, adLockOptimistic
    pts3d = [(500000.0, 3000000.0, 50.5),
             (500100.25, 3000050.75, 51.25),
             (499900.5, 2999950.0, 49.0)]
    for i, (x, y, z) in enumerate(pts3d):
        rs.AddNew()
        rs.Fields("OBJECTID").Value = i + 1
        rs.Fields("SHAPE").Value = _blob_point3d(x, y, z)
        rs.Fields("NAME").Value = "P%d" % i
        rs.Update()
    rs.Close()
    rs.Open("DEMO_LK", conn, 1, 3)
    line_pts = [(500000.0, 3000000.0), (500100.0, 3000020.0),
                (500200.0, 3000010.0), (500200.0, 3000100.0)]
    rs.AddNew()
    rs.Fields("OBJECTID").Value = 1
    rs.Fields("SHAPE").Value = _blob_line3d(line_pts, [10.0, 11.0, 12.0, 13.0], [0, 2])
    rs.Fields("CODE").Value = "L1"
    rs.Update()
    rs.Close()
    conn.Close()
    return pts3d, line_pts


def test_pgdb_transform():
    import shapefile
    from app.fileconv.pgdb_transform import decode_blob, list_feature_classes, transform_pgdb

    d = Path(tempfile.mkdtemp())
    mdb = str(Path(d) / "demo.mdb")
    src_pts, src_line = _make_pgdb(mdb)
    assert set(list_feature_classes(mdb)) == {"DEMO_PT", "DEMO_LK"}
    g = decode_blob(_blob_line3d(src_line, [10.0, 11.0, 12.0, 13.0], [0, 2]))
    assert g["z"] == [10.0, 11.0, 12.0, 13.0] and g["parts"] == [0, 2]
    p4 = _mk_p4()
    hfn = make_cad_height_fn("offset", offset=-1.0)
    res = transform_pgdb(mdb, str(d), planar_fn=lambda X, Y: apply_4param(p4, Y, X),
                         height_fn=lambda X, Y, z: hfn(Y, X, z))
    assert res.transformed == 2 and res.features == 4, res.log
    # 点层
    r = shapefile.Reader(str(Path(d) / "demo_DEMO_PT_转换后.shp"))
    assert len(r) == 3 and r.shapeType == 11, (len(r), r.shapeType)   # PointZ
    for i, (x, y, z) in enumerate(src_pts):
        ex, ey = apply_4param(p4, y, x)           # (X东,Y北)→测量(x北=Y, y东=X)
        got = r.shape(i).points[0]
        assert abs(got[0] - ex) < 1e-6 and abs(got[1] - ey) < 1e-6, (i, got, ex, ey)
        assert abs(r.shape(i).z[0] - (z - 1.0)) < 1e-9
    assert [r.record(i)[1] for i in range(3)] == ["P0", "P1", "P2"]
    # 线层（两段 parts + Z）
    rl = shapefile.Reader(str(Path(d) / "demo_DEMO_LK_转换后.shp"))
    assert len(rl) == 1 and rl.shapeType == 13    # PolyLineZ
    sh = rl.shape(0)
    assert len(sh.points) == 4 and len(sh.parts) == 2, (len(sh.points), len(sh.parts))
    ex, ey = apply_4param(p4, src_line[0][1], src_line[0][0])
    assert abs(sh.points[0][0] - ex) < 1e-6 and abs(sh.points[0][1] - ey) < 1e-6
    assert abs(sh.z[0] - 9.0) < 1e-9              # 10 + offset(-1)
    assert rl.record(0)[1] == "L1"


def test_real_pgdb_sample():
    """真实 iData MDB（国家基础地理500）：只读验证，文件缺失则跳过。"""
    from app.fileconv.pgdb_transform import list_feature_classes, transform_pgdb

    real = r"E:\IDATAJOBS\国家基础地理500_1.mdb"
    if not os.path.exists(real):
        print("[SKIP] 真实 MDB 样例不存在")
        return
    classes = list_feature_classes(real)
    assert "TERPT" in classes, classes
    d = Path(tempfile.mkdtemp())
    res = transform_pgdb(real, str(d))
    assert res.transformed >= 1 and res.features >= 443, res.log
    import shapefile
    r = shapefile.Reader(str(Path(d) / "国家基础地理500_1_TERPT_转换后.shp"))
    assert len(r) == 443, len(r)
    got = r.shape(0).points[0]
    assert 4e5 < got[0] < 7e5 and 2e6 < got[1] < 4e6, got    # 合理投影坐标量级


def test_dwg_convert():
    """DWG→DXF（本机 AutoCAD COM）；无 AutoCAD 或无样例则跳过。"""
    from app.fileconv.dwg_convert import dwg_to_dxf, find_acad

    if find_acad() is None:
        print("[SKIP] 本机无 AutoCAD")
        return
    src = r"D:\Program Files\CASS11 For AutoCAD 2023\BLOCKS\Accheng.dwg"
    if not os.path.exists(src):
        print("[SKIP] 无 DWG 样例")
        return
    d = Path(tempfile.mkdtemp())
    try:
        out = dwg_to_dxf(src, str(Path(d) / "o.dxf"))
    except Exception as e:
        if "忙" in str(e):
            print("[SKIP] AutoCAD 正忙（用户 CASS 会话占用中）")
            return
        raise
    import ezdxf
    doc = ezdxf.readfile(out)
    assert len(list(doc.modelspace())) >= 1

def test_lonlat_text_with_projection_param():
    """大地坐标文本文件 + 投影参数（大地→平面）：build_jobs/转换/类型不匹配拦截。"""
    from app.logic.file_jobs import build_jobs
    from app.core.projection import gauss_kruger

    d = Path(tempfile.mkdtemp())
    src = [(115.2, 30.3), (115.25, 30.32)]
    txt = Path(d) / "bl.txt"
    with open(txt, "w", encoding="utf-8") as f:
        for i, (lon, lat) in enumerate(src):
            gx, gy = gauss_kruger(lat, lon, 115.0)
            f.write(f"P{i+1},{lon:.8f},{lat:.8f},{gx:.4f},{gy:.4f}\n")
    doc = {"kind": "direct_gk", "ellipsoid": "CGCS2000", "source_kind": "lonlat",
           "projection": {"l0": 115.0, "x0": 0.0, "y0": 500000.0, "k": 1.0}}
    jobs, err = build_jobs([str(txt)], str(d), doc, {"mode": "none"}, False)
    assert err is None and jobs[0]["input_lonlat"] is True
    from app.fileconv.points_text import transform_generic_points
    transform_generic_points(txt, Path(d) / "out.txt", jobs[0]["planar_fn"],
                             jobs[0]["height_fn"], input_lonlat=True)
    line = (Path(d) / "out.txt").read_text(encoding="utf-8-sig").splitlines()[0]
    gx, gy = gauss_kruger(src[0][1], src[0][0], 115.0)
    assert f"{gx:.4f}" in line and f"{gy:.4f}" in line, line
    # 平面文件 + 大地参数 → 内容级拦截
    planar_txt = Path(d) / "xy.txt"
    planar_txt.write_text("P1,2860000.0,500000.0\n", encoding="utf-8")
    try:
        transform_generic_points(planar_txt, Path(d) / "o2.txt", jobs[0]["planar_fn"],
                                 jobs[0]["height_fn"], input_lonlat=True)
        raise AssertionError("应报类型不匹配")
    except ValueError:
        pass
    # 大地参数 + 非文本文件 → 任务级拦截
    shp_like = Path(d) / "a.dxf"
    shp_like.write_text("0\n", encoding="utf-8")
    jobs2, err2 = build_jobs([str(shp_like)], str(d), doc, {"mode": "none"}, False)
    assert err2 and "仅支持文本点文件" in err2, err2


def test_custom_ellipsoid_registry():
    """自定义椭球：保存/读取/删除；各页下拉与 ParamApplier 可用。"""
    from app.core.ellipsoid import (all_ellipsoids, delete_custom_ellipsoid,
                                    get_ellipsoid, save_custom_ellipsoid)

    save_custom_ellipsoid("测试椭球XY", 6378245.0, 295.0)
    try:
        assert abs(get_ellipsoid("测试椭球XY").inv_f - 295.0) < 1e-9
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])
        except Exception:
            print("[SKIP] 无 Qt")
            return
        from app.gui.pages import GaussPage
        gp = GaussPage()
        assert "测试椭球XY" in [gp.ell_combo.itemText(i) for i in range(gp.ell_combo.count())]
        from app.common.params import ParamApplier
        ap = ParamApplier({"kind": "planar4", "planar4": {"dx": 1, "dy": 2, "a": 1, "b": 0},
                           "ellipsoid": "测试椭球XY"})
        assert abs(ap.ell.inv_f - 295.0) < 1e-9
    finally:
        delete_custom_ellipsoid("测试椭球XY")
    assert "测试椭球XY" not in all_ellipsoids()


if __name__ == "__main__":
    run()
