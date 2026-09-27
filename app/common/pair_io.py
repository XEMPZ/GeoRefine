"""控制点对文件解析：GPS 手簿 .cot 与南方 CASS 公共点文件。

两者均为 AutoClaw 时代逆向/实测确认的格式，样例见 sample_data/点对文件样例/。

.cot（中海达 GNSSTools 点对文件，逗号分隔，CRLF）：
  点名, x北, y东, h正常高, B(d.ms), L(d.ms), H大地高, 辅1, 辅2, 用平面(Y/N), 用高程(Y/N)
  - 列结构为两组三点：[施工平面 x,y + 正常高 h] 与 [WGS84 B,L + 大地高 H]；
  - B/L 为 d.ms 编码（度.分秒，与手簿显示一致），解析时解码为十进制度；
  - 末尾两个 Y/N：是否使用该点的经纬度（平面参数）、是否使用该点的高程（高程拟合）；
    缺省视为全 Y。源系统=WGS84 (B,L,H)，目标=地方平面 (x,y) + 正常高 h。
  实测校验（长赣A10 样例）：平面跨度 3.2×9.9km 与 B/L d.ms 解码跨度 3.2×9.2km 吻合；
  ζ = H−h ≈ −5.47m 与 EGM96 该区域 ξ≈−5.8m 一致。

CASS 公共点文件（南方 CASS 坐标转换参数计算）：
  旧x,旧y,旧h:新x,新y,新h     （冒号分隔源/目标，逗号分隔坐标）
  - 源/目标皆为平面坐标；h 全为 0 或前后相等时视为正常高（二维情形）；
  - 转换模型由用户选择：四参数，或七参数（平面坐标时为二维七参数）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.anglefmt import dms_to_deg


def _decode(path: Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass
class CotPoint:
    name: str
    x: float          # 目标平面 x北
    y: float          # 目标平面 y东
    h_normal: float   # 目标正常高
    b_deg: float      # 源 WGS84 纬度
    l_deg: float      # 源 WGS84 经度
    h_ell: float      # 源大地高
    use_pos: bool     # 是否用于平面参数
    use_h: bool       # 是否用于高程拟合


@dataclass
class CassPair:
    x1: float
    y1: float
    h1: float
    x2: float
    y2: float
    h2: float


def _yn(tok: str, default: bool = True) -> bool:
    t = (tok or "").strip().upper()
    if not t:
        return default
    return t.startswith("Y")


def parse_cot(path: str | Path) -> list[CotPoint]:
    """解析手簿 .cot 点对文件。"""
    text = _decode(Path(path))

    out: list[CotPoint] = []
    for no, ln in enumerate(text.splitlines(), start=1):
        s = ln.strip()
        if not s or s.startswith(("#", "//")):
            continue
        cells = [c.strip() for c in s.split(",")]
        if len(cells) < 7:
            raise ValueError(f".cot 第 {no} 行需要至少 7 列（点名,x,y,h,B,L,H），实际 {len(cells)} 列：{s[:60]}")
        try:
            name = cells[0]
            x, y, hn = float(cells[1]), float(cells[2]), float(cells[3])
            b, l, he = dms_to_deg(float(cells[4])), dms_to_deg(float(cells[5])), float(cells[6])
        except ValueError as e:
            raise ValueError(f".cot 第 {no} 行数值无法解析：{e}") from e
        use_pos = _yn(cells[9]) if len(cells) > 9 else True
        use_h = _yn(cells[10]) if len(cells) > 10 else True
        out.append(CotPoint(name, x, y, hn, b, l, he, use_pos, use_h))
    if not out:
        raise ValueError(f".cot 无有效数据行：{path}")
    return out


def parse_cass(path: str | Path) -> list[CassPair]:
    """解析南方 CASS 公共点文件（旧x,旧y,旧h:新x,新y,新h）。"""
    text = _decode(Path(path))

    out: list[CassPair] = []
    for no, ln in enumerate(text.splitlines(), start=1):
        s = ln.strip()
        if not s or s.startswith(("#", "//")):
            continue
        if ":" not in s:
            raise ValueError(f"CASS 第 {no} 行缺少源/目标分隔符“:”：{s[:60]}")
        left, right = s.split(":", 1)
        try:
            a = [float(c) for c in left.replace("，", ",").split(",")[:3]]
            b = [float(c) for c in right.replace("，", ",").split(",")[:3]]
        except ValueError as e:
            raise ValueError(f"CASS 第 {no} 行数值无法解析：{e}") from e
        while len(a) < 3:
            a.append(0.0)
        while len(b) < 3:
            b.append(0.0)
        out.append(CassPair(a[0], a[1], a[2], b[0], b[1], b[2]))
    if not out:
        raise ValueError(f"CASS 公共点文件无有效数据行：{p}")
    return out


@dataclass
class SouthPair:
    name: str
    x1: float; y1: float; h1: float
    x2: float; y2: float; h2: float


def _split_cells(ln: str) -> list:
    """逗号/制表/空白分隔的单元格（南方文本格式分隔符可配置，按宽容处理）。"""
    if "," in ln:
        return [c.strip() for c in ln.split(",")]
    return ln.split()


def looks_like_south_pairs(path: str | Path) -> bool:
    """嗅探南方公共点对文本：多数数据行 ≥6 列数值（点名可选），
    且第 4/5 列之间可构成“旧坐标→新坐标”结构（列 1-3 与 4-6 数值）。"""
    try:
        text = _decode(Path(path))
    except Exception:
        return False
    hits = 0
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", "//")):
            continue
        cells = _split_cells(s)
        if len(cells) < 6:
            continue
        off = 0 if _is_num(cells[0]) else 1          # 首列点名则偏移
        if len(cells) - off < 6:
            continue
        try:
            float(cells[off]); float(cells[off + 1]); float(cells[off + 2])
            float(cells[off + 3]); float(cells[off + 4]); float(cells[off + 5])
            hits += 1
        except ValueError:
            continue
    return hits >= 2 and hits * 2 >= _data_line_count(path)


def _is_num(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False


def _data_line_count(path: str | Path) -> int:
    try:
        text = _decode(Path(path))
    except Exception:
        return 0
    return sum(1 for ln in text.splitlines()
               if ln.strip() and not ln.strip().startswith(("#", "//")))


def parse_south_pairs(path: str | Path) -> list[SouthPair]:
    """解析南方坐标转换软件公共点对文本：点名,旧x,旧y,旧h,新x,新y,新h（7 列，
    点名可省略为 6 列；分隔符逗号/空白均可；# 注释行跳过）。"""
    text = _decode(Path(path))
    out: list[SouthPair] = []
    for no, ln in enumerate(text.splitlines(), start=1):
        s = ln.strip()
        if not s or s.startswith(("#", "//")):
            continue
        cells = _split_cells(s)
        off = 0 if _is_num(cells[0]) else 1
        if len(cells) - off < 6:
            raise ValueError(f"第 {no} 行需要至少 6 个数值列（点名可选），实际 {len(cells)} 列：{s[:60]}")
        try:
            name = cells[0] if off == 1 else f"P{no}"   # 首列非数值=点名在场
            x1, y1, h1 = float(cells[off]), float(cells[off + 1]), float(cells[off + 2])
            x2, y2, h2 = float(cells[off + 3]), float(cells[off + 4]), float(cells[off + 5])
        except ValueError as e:
            raise ValueError(f"第 {no} 行数值无法解析：{e}") from e
        out.append(SouthPair(name, x1, y1, h1, x2, y2, h2))
    if not out:
        raise ValueError(f"南方公共点对文件无有效数据行：{path}")
    return out


def looks_like_cass(path: str | Path) -> bool:
    """按内容嗅探是否为 CASS 公共点文件（行内含“数值,数值,数值:数值,数值,数值”结构）。"""
    try:
        text = _decode(Path(path))
    except Exception:  # noqa: BLE001
        return False
    hits = 0
    for ln in text.splitlines():
        s = ln.strip()
        if not s or ":" not in s:
            continue
        left, _, right = s.partition(":")
        try:
            la = [float(c) for c in left.split(",")]
            lb = [float(c) for c in right.split(",")]
        except ValueError:
            continue
        if len(la) >= 2 and len(lb) >= 2:
            hits += 1
            if hits >= 2:
                return True
    return False


# ---------------------------------------------------------------- 导出
def export_pairs(path: str | Path, fmt: str, rows, flags=None, src_kind: str = "planar") -> str:
    """把控制点对导出为指定格式，返回实际写出路径。

    rows: [(点名, 源1, 源2, 源h, 目标1, 目标2, 目标h)]——与控制点页表格列序一致
          （源为大地坐标时 源1=L 经度、源2=B 纬度）；flags: [(用平面, 用高程)]。
    fmt: "cot"（GNSS手簿，源须为大地坐标）/ "cass"（南方CASS）/ "south"（南方坐标
         转换软件 7 列）/ "xlsx" / "csv"。各格式与对应解析器互为镜像，可读回。
    """
    p = Path(path)
    rows = [tuple(r) for r in rows]
    flags = list(flags or [])
    if not rows:
        raise ValueError("没有可导出的点对")
    if fmt == "cot":
        from app.core.anglefmt import deg_to_dms
        if src_kind != "lonlat":
            raise ValueError("手簿 .cot 格式的源侧为大地坐标（B,L,H）；"
                             "当前点对源为平面坐标，请改用南方CASS/南方坐标转换/xlsx 格式")
        lines = []
        for i, (name, s1, s2, sh, d1, d2, dh) in enumerate(rows):
            upos, uh = flags[i] if i < len(flags) else (True, True)
            lines.append(",".join([
                str(name), f"{d1:.4f}", f"{d2:.4f}", f"{dh:.4f}",
                f"{deg_to_dms(float(s2)):.6f}", f"{deg_to_dms(float(s1)):.6f}",
                f"{sh:.4f}", "", "", "Y" if upos else "N", "Y" if uh else "N"]))
        text = "\r\n".join(lines) + "\r\n"
        p.write_text(text, encoding="gb18030")
    elif fmt == "cass":
        # 南方 CASS 公共点：旧x,旧y,旧h:新x,新y,新h（无点名列，点名导出不保留）
        lines = [f"{s1:.4f},{s2:.4f},{sh:.4f}:{d1:.4f},{d2:.4f},{dh:.4f}"
                 for _n, s1, s2, sh, d1, d2, dh in rows]
        p.write_text("\n".join(lines) + "\n", encoding="gb18030")
    elif fmt == "south":
        lines = [f"{name},{s1:.4f},{s2:.4f},{sh:.4f},{d1:.4f},{d2:.4f},{dh:.4f}"
                 for name, s1, s2, sh, d1, d2, dh in rows]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif fmt in ("xlsx", "csv"):
        header = ["点名", "源x", "源y", "源h", "目标x", "目标y", "目标h", "用平面", "用高程"]
        body = []
        for i, (name, s1, s2, sh, d1, d2, dh) in enumerate(rows):
            upos, uh = flags[i] if i < len(flags) else (True, True)
            body.append([name, s1, s2, sh, d1, d2, dh, "Y" if upos else "N",
                         "Y" if uh else "N"])
        if fmt == "xlsx":
            try:
                import openpyxl
            except ImportError as e:  # pragma: no cover
                raise ImportError("导出 xlsx 需要 openpyxl：pip install openpyxl") from e
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "控制点对"
            ws.append(header)
            for r in body:
                ws.append([str(v) if isinstance(v, str) else (None if v is None else float(v))
                           for v in r])
            wb.save(str(p))
        else:
            import csv as _csv
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                w = _csv.writer(f)
                w.writerow(header)
                for r in body:
                    w.writerow([("" if v is None else v) for v in r])
    else:
        raise ValueError(f"未知导出格式：{fmt}")
    return str(p)
