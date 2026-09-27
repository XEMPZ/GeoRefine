"""ESRI Personal Geodatabase (.mdb) 矢量转换（南方 iData 的 MDB 工程即此格式，ADO 直读）。

格式依据（实测解剖 E:\\IDATAJOBS\\国家基础地理500*.mdb，ACE OLEDB + ADOX）：
- 要素类 = 含 SHAPE (LongBinary) 字段的普通表；GDB_* 为 ESRI 系统表，*_SHAPE_INDEX 为索引。
- 几何 blob（与 shapefile 记录体同构）：
    点  2D: [int32 标记=9][X][Y]              (len=20)
    点  3D: [int32 标记=9][X][Y][Z]           (len=28)
    线/面   : [int32 标记=10][bbox×4d][numparts][numpoints][parts×i][points×2d]
              [Zmin,Zmax][Z×n]（3D 时；实测 44642 条线验证）
- 坐标为投影平面坐标（X=东, Y=北，与 DXF/SHP 同约定）。
输出：每个要素类写一个 SHP（属性全保留），不回写 MDB。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_PREFIX = ("GDB_", "MSys")
_SKIP_SUFFIX = "_SHAPE_INDEX"
_SKIP_TABLES = {"IDATA", "iDataSysTable"}
_TEXT_TYPES = (202, 203, 7, 72, 130, 201, 204)   # VarWChar/LongWChar/日期/GUID/备注/二进制文本


@dataclass
class PgdbResult:
    total: int = 0            # 要素类数
    transformed: int = 0      # 成功写出的要素类数
    skipped: int = 0
    features: int = 0         # 累计要素数
    out_paths: list = field(default_factory=list)
    out_path: str = ""        # 与 CadResult 兼容：首输出
    unsupported: dict = field(default_factory=dict)
    log: list = field(default_factory=list)


def _connect(path: str):
    import win32com.client

    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open(rf"Provider=Microsoft.ACE.OLEDB.12.0;Data Source={path}")
    return conn


def list_feature_classes(mdb_path) -> list[str]:
    """列出含 SHAPE 字段的用户要素类（跳过系统表与索引表）。"""
    conn = _connect(str(mdb_path))
    try:
        import win32com.client

        cat = win32com.client.Dispatch("ADOX.Catalog")
        cat.ActiveConnection = conn
        names = [t.Name for t in cat.Tables
                 if t.Type == "TABLE"
                 and not t.Name.startswith(_SKIP_PREFIX)
                 and not t.Name.endswith(_SKIP_SUFFIX)
                 and t.Name not in _SKIP_TABLES]
        out = []
        for nm in names:
            try:
                rs = win32com.client.Dispatch("ADODB.Recordset")
                rs.Open(f"SELECT TOP 1 * FROM [{nm}]", conn, 0, 1)
                if any(f.Name.upper() == "SHAPE" for f in rs.Fields):
                    out.append(nm)
                rs.Close()
            except Exception:  # noqa: BLE001
                continue
        return out
    finally:
        conn.Close()


def decode_blob(b: bytes) -> dict:
    """解码 ESRI 几何 blob → {kind, pts, parts, z}。kind ∈ point/poly。"""
    if len(b) < 4:
        raise ValueError("几何 blob 过短")
    # 标记 int32（实测 9=点、10=线/面），后接 shapefile 记录体
    if len(b) == 4 + 16:
        x, y = struct.unpack_from("<2d", b, 4)
        return {"kind": "point", "pts": [(x, y)], "parts": [0], "z": None}
    if len(b) == 4 + 24:
        x, y, z = struct.unpack_from("<3d", b, 4)
        return {"kind": "point", "pts": [(x, y)], "parts": [0], "z": [z]}
    off = 4
    struct.unpack_from("<4d", b, off)          # bbox（信息项，跳过）
    off += 32
    numparts, numpoints = struct.unpack_from("<2i", b, off)
    off += 8
    if numparts < 1 or numpoints < 1 or off + 4 * numparts + 16 * numpoints > len(b):
        raise ValueError(f"几何 blob 头异常 numparts={numparts} numpoints={numpoints}")
    parts = list(struct.unpack_from(f"<{numparts}i", b, off))
    off += 4 * numparts
    flat = struct.unpack_from(f"<{2 * numpoints}d", b, off)
    off += 16 * numpoints
    pts = [(flat[2 * i], flat[2 * i + 1]) for i in range(numpoints)]
    zs = None
    # 3D：[Zmin,Zmax][Z×numpoints]（实测线层验证）
    if len(b) - off >= 16 + 8 * numpoints:
        off += 16
        zs = list(struct.unpack_from(f"<{numpoints}d", b, off))
    return {"kind": "poly", "pts": pts, "parts": parts, "z": zs}


def _shape_kind(table_name: str) -> int:
    """表名后缀 → 基础 pyshp shapeType（PT=1 点, NT=5 面, 其余=3 线）。"""
    u = table_name.upper()
    if u.endswith("_PT") or u.endswith("PT"):
        return 1
    if u.endswith("_NT") or u.endswith("NT"):
        return 5
    return 3


def _split_parts(points: list, parts: list) -> list[list]:
    if not parts:
        return [points]
    segs = []
    for i, s in enumerate(parts):
        e = parts[i + 1] if i + 1 < len(parts) else len(points)
        segs.append(points[s:e])
    return segs


def transform_pgdb(mdb_path, out_dir, planar_fn=None, height_fn=None,
                   progress_cb=None, cancel=None) -> PgdbResult:
    """转换 PGDB：每个要素类 → 输出目录下 <库名>_<表名>_转换后.shp。

    planar_fn(X_east, Y_north) -> (X2, Y2)；height_fn(X, Y, z) -> (z_new, v, detail)。
    """
    import win32com.client

    import shapefile  # pyshp

    res = PgdbResult()
    conn = _connect(str(mdb_path))
    try:
        classes = list_feature_classes(str(mdb_path))
        res.total = len(classes)
        stem = Path(str(mdb_path)).stem
        for ci, tbl in enumerate(classes):
            if cancel is not None and cancel.is_set():
                break
            try:
                # ---- 窥探首个几何，确定是否 3D（Z 型 shapeType 须在 writer 构造时定死）----
                base = _shape_kind(tbl)
                rs0 = win32com.client.Dispatch("ADODB.Recordset")
                rs0.Open(f"SELECT TOP 1 SHAPE FROM [{tbl}] WHERE SHAPE IS NOT NULL",
                         conn, 0, 1)
                hasz = False
                if not rs0.EOF:
                    hasz = decode_blob(bytes(rs0.Fields("SHAPE").Value))["z"] is not None
                rs0.Close()
                st = base + (10 if hasz else 0)     # 1→11, 3→13, 5→15

                # ---- 流式读取并写出 ----
                rs = win32com.client.Dispatch("ADODB.Recordset")
                rs.Open(f"SELECT * FROM [{tbl}]", conn, 3, 1)   # adOpenStatic：空表时 GetRows 不抛"无当前记录"
                if rs.EOF:
                    rs.Close()
                    res.transformed += 1
                    res.out_paths.append("(空要素类，跳过写出)")
                    res.log.append(f"{tbl}: 0 要素")
                    if progress_cb:
                        progress_cb(ci + 1, res.total, tbl)
                    continue
                all_flds = list(rs.Fields)
                shape_idx = next(i for i, f in enumerate(all_flds)
                                 if f.Name.upper() == "SHAPE")
                attr_idx = [i for i in range(len(all_flds)) if i != shape_idx]
                seen: dict[str, int] = {}
                dbf_names = []
                for i in attr_idx:
                    nm = all_flds[i].Name[:10]
                    if nm in seen:
                        seen[nm] += 1
                        nm = f"{nm[:8]}_{seen[nm]}"
                    else:
                        seen[nm] = 0
                    dbf_names.append(nm)

                out_shp = Path(str(out_dir)) / f"{stem}_{tbl}_转换后.shp"
                w = shapefile.Writer(target=str(out_shp), shapeType=st, encoding="utf-8")
                n_feat = 0
                try:
                    for i, nm in zip(attr_idx, dbf_names):
                        if all_flds[i].Type in _TEXT_TYPES:
                            w.field(nm, "C", 254, 0)
                        else:
                            w.field(nm, "N", 19, 6)
                    while True:
                        batch = rs.GetRows(2000)          # batch[字段序][行序]
                        if not batch or len(batch[0]) == 0:
                            break
                        # EOF 上再取会抛"无当前记录"，不足一批即已读完
                        n_rows = len(batch[0])
                        for r in range(len(batch[0])):
                            if cancel is not None and cancel.is_set():
                                break
                            raw = batch[shape_idx][r]
                            if raw is None:
                                res.unsupported[f"{tbl}#{n_feat+1}"] = "空几何"
                                continue
                            try:
                                geom = decode_blob(bytes(raw))
                            except Exception as e:  # noqa: BLE001
                                res.unsupported[f"{tbl}#{n_feat+1}"] = str(e)
                                continue
                            pts, zs = geom["pts"], geom["z"]
                            n2 = []
                            for k, (X, Y) in enumerate(pts):
                                if planar_fn is not None:
                                    X, Y = planar_fn(X, Y)
                                z = None
                                if zs is not None:
                                    z = float(zs[k])
                                    if height_fn is not None:
                                        z_new, _v, _d = height_fn(X, Y, z)
                                        if z_new is not None:
                                            z = float(z_new)
                                n2.append([float(X), float(Y)] + ([z] if z is not None else []))
                            attrs = [batch[i][r] for i in attr_idx]
                            if base == 1:
                                p0 = n2[0]
                                (w.pointz(p0[0], p0[1], p0[2]) if hasz
                                 else w.point(p0[0], p0[1]))
                            else:
                                segs = _split_parts(n2, geom["parts"])
                                (w.linez(segs) if st == 13 else
                                 w.polyz(segs) if st == 15 else
                                 w.line(segs) if base == 3 else w.poly(segs))
                            w.record(*attrs)
                            n_feat += 1
                        if cancel is not None and cancel.is_set():
                            break
                        if n_rows < 2000:
                            break
                finally:
                    w.close()
                    try:
                        rs.Close()
                    except Exception:  # noqa: BLE001
                        pass
                res.features += n_feat
                res.transformed += 1
                res.out_paths.append(str(out_shp))
                res.log.append(f"{tbl}: {n_feat} 要素")
            except Exception as e:  # noqa: BLE001
                res.unsupported[tbl] = str(e)
                res.skipped += 1
            if progress_cb:
                progress_cb(ci + 1, res.total, tbl)
    finally:
        conn.Close()
    res.out_path = res.out_paths[0] if res.out_paths else ""
    res.log.append(f"要素类 {res.transformed}/{res.total}，要素 {res.features}")
    return res
