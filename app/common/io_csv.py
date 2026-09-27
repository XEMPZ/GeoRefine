"""控制点 CSV/TXT 读取（主线维护）。

- 分隔符自动：逗号 / 制表符 / 连续空白
- 编码自动：utf-8-sig → gbk
- '#' 开头为注释行；首行若含字母/中文按表头解析，否则按位置推断
- 返回统一列名: name, x, y, h, B, L, H_ell, H
"""
from __future__ import annotations

import csv
from pathlib import Path

_HEADERS = {
    "name": ["点名", "name", "point", "id", "点号"],
    "x": ["x", "北", "北坐标", "n", "northing", "x(北)", "x1", "源x"],
    "y": ["y", "东", "东坐标", "e", "easting", "y(东)", "y1", "源y"],
    "h": ["h", "正常高", "水准高", "海拔", "h1", "源h"],
    "B": ["b", "纬度", "lat", "latitude", "b1", "源b"],
    "L": ["l", "经度", "lon", "lng", "longitude", "l1", "源l"],
    "H_ell": ["大地高", "h_ell", "hell", "ellipsoidal", "大地高h"],
    "H": ["目标正常高", "h2", "目标h", "h_normal", "目标正常高h"],
    "x2": ["目标x", "x2", "目标北", "targetx", "目标x坐标"],
    "y2": ["目标y", "y2", "目标东", "targety", "目标y坐标"],
    "B2": ["目标b", "b2", "目标纬度"],
    "L2": ["目标l", "l2", "目标经度"],
}


def _detect_encoding(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def _split_line(line: str) -> list[str]:
    if "," in line:
        return [c.strip() for c in line.split(",")]
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    return line.split()


def _norm(s: str) -> str:
    return str(s).strip().lower().replace(" ", "").replace("（", "(").replace("）", ")")


def _match_header(cell: str) -> str | None:
    c = _norm(cell)
    for key, aliases in _HEADERS.items():
        if c in [_norm(a) for a in aliases]:
            return key
    return None


def read_points_csv(path: str | Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        # Excel 工作簿：委托 openpyxl 逐行读出后与文本路径共用同一套表头识别
        from app.common.io_import import _read_xlsx_rows

        rows_raw, _nos = _read_xlsx_rows(path)
        rows_raw = [r for r in rows_raw if r]
    else:
        enc = _detect_encoding(path)
        lines = path.read_text(encoding=enc).splitlines()
        rows_raw = []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            rows_raw.append(_split_line(ln))
    header_map: dict[int, str] | None = None
    kept: list[list] = []
    for cells in rows_raw:
        if header_map is None:
            # 判断首数据行是否表头：含非数值单元格且能匹配到已知列名
            hits = [_match_header(c) for c in cells]
            named = [h for h in hits if h]
            if named and len(named) >= max(1, len(cells) // 2):
                header_map = {i: h for i, h in enumerate(hits) if h}
                continue
        kept.append(cells)
    rows_raw = kept

    out: list[dict] = []
    for cells in rows_raw:
        rec: dict = {"name": None, "x": None, "y": None, "h": None,
                     "B": None, "L": None, "H_ell": None, "H": None,
                     "x2": None, "y2": None, "B2": None, "L2": None}
        for i, cell in enumerate(cells):
            key = header_map.get(i) if header_map else None
            if key is None:
                # 无表头时按常见位置推断: 点名,x,y,h,x2,y2,H（7列）或 点名,x,y,h（4列）
                key = {0: "name", 1: "x", 2: "y", 3: "h", 4: "x2", 5: "y2", 6: "H"}.get(i)
                if key in ("x2", "y2", "H") and len(cells) <= 4:
                    key = None
            if key is None:
                continue
            try:
                rec[key] = float(cell) if key != "name" else cell.strip()
            except ValueError:
                rec[key] = cell.strip()
        # 若 x/y 为空但 B/L 有值，不强行迁移，调用方按 kind 判断
        if rec.get("name") is None:
            rec["name"] = f"P{len(out) + 1}"
        out.append(rec)
    return out


def write_csv_utf8bom(path: str | Path, header: list, rows: list[list]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return str(path)
