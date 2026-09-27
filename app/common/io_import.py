"""通用坐标数据文件导入：txt / csv / xlsx，分隔符、表头、点号列三重自动识别。

识别规则
--------
1. 分隔符：逐候选（逗号 → 制表符 → 分号 → 连续空白）检查全文件是否"每行列数一致"，
   取第一个一致且列数 ≥2 的候选；引号内的字符不参与计数，单元格两端引号自动剥除。
2. 表头：首个"数值单元格数 ≥ 坐标列数"的行视为数据首行，其前的行（点号/x(m)/Y(m) 等）
   一律视为表头跳过；# 与 // 开头的行视为注释。
3. 点号列：数据行恰为 coord_cols 列 → 无点号（自动编号 P1..Pn）；
   多出一列 → 首列视为点号（测量文件惯例点号在前），首列为纯数字时同样按点号处理；
   更多列 → 首列点号 + 前 coord_cols 个坐标列，多余列忽略。
4. 编码：utf-8(BOM) → gb18030 依次尝试，兼容国产测量软件的 GBK 导出。

接口：read_points_table(path, coord_cols) -> [(点名, [v1, v2, ...]), ...]
角度列（d.ms 编码或十进制度）均以数值返回，由调用页当前的格式选择解释。
"""
from __future__ import annotations

from pathlib import Path


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _split_line(ln: str, delim: str | None) -> list[str]:
    if delim is None:  # 连续空白（空格/制表符混排）
        return ln.split()
    cells = [c.strip().strip('"').strip("'") for c in ln.split(delim)]
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _detect_delim(lines: list[str]) -> str | None:
    for cand in (",", "\t", ";"):
        counts = [ln.count(cand) for ln in lines]
        if min(counts) >= 1 and len(set(counts)) == 1:
            return cand
    return None  # 连续空白


def _read_text_rows(path: Path) -> tuple[list[list[str]], list[int]]:
    text = _decode(path)
    raw_lines = [(no, ln) for no, ln in enumerate(text.splitlines(), start=1) if ln.strip()]
    lines = []
    nos = []
    for no, ln in raw_lines:
        s = ln.strip()
        if s.startswith(("#", "//")):
            continue
        lines.append(ln)
        nos.append(no)
    if not lines:
        return [], []
    delim = _detect_delim(lines)
    rows = [_split_line(ln, delim) for ln in lines]
    rows = [r for r in rows if r]
    return rows, nos


def _read_xlsx_rows(path: Path) -> tuple[list[list[str]], list[int]]:
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover
        raise ImportError("读取 xlsx 需要 openpyxl：pip install openpyxl") from e
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows: list[list[str]] = []
        nos: list[int] = []
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = ["" if c is None else str(c).strip() for c in row]
            while cells and cells[-1] == "":
                cells.pop()
            if cells:
                rows.append(cells)
                nos.append(i)
        return rows, nos
    finally:
        wb.close()


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def read_points_table(path: str | Path, coord_cols: int, allow_pad: bool = False,
                      keep_extra: bool = False):
    """读取坐标点表，返回 (rows, notes)。

    rows: [(点名, [坐标...]), ...]（点名可能为空串；坐标数恒为 coord_cols）。
    notes: 解析提示（如"高程列缺失按 0 处理"），由调用页面展示。

    识别规则（位置法，测量文件惯例点号在前）：
      len == coord_cols 且全数值            → 无点号
      len == coord_cols+1 且首列非数值      → 首列点号
      len == coord_cols+1 且首列为整数      → 首列点号（整数点号文件）
      len == coord_cols+1 且全数值          → 无点号，多余列忽略
      len == coord_cols-1 且全数值          → 末列（高程）缺失：allow_pad 时补 0，
                                              否则报错
    数据行之前的非数据行视为表头跳过；# 与 // 开头为注释。
    """
    if coord_cols < 1:
        raise ValueError("coord_cols 必须为正")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{p}")
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        rows, nos = _read_xlsx_rows(p)
    else:
        rows, nos = _read_text_rows(p)
    if not rows:
        raise ValueError(f"文件为空或无有效数据行：{p}")

    import re as _re
    int_like = _re.compile(r"-?\d+$")

    def classify(cells):
        """返回 (kind, name, vals, msg)；kind: 'data'/'header'/'error'。

        keep_extra=True 时保留坐标列之后的附加数值列（如换带页透传高程）。
        """
        numeric = [_is_float(c) for c in cells]
        m = len(cells)
        if m == coord_cols and all(numeric):
            return "data", "", [float(c) for c in cells], ""
        if m == coord_cols + 1:
            if not numeric[0] and all(numeric[1:]):
                return "data", cells[0], [float(c) for c in cells[1:]], ""
            if int_like.match(cells[0]) and all(numeric[1:]):
                return "data", cells[0], [float(c) for c in cells[1:]], ""
            if all(numeric):
                vals = [float(c) for c in cells]
                return "data", "", (vals if keep_extra else vals[:coord_cols]), ""
        if m > coord_cols + 1 and keep_extra:
            if not numeric[0] and all(numeric[1:]):
                return "data", cells[0], [float(c) for c in cells[1:]], ""
            if all(numeric):
                return "data", "", [float(c) for c in cells], ""
        if m == coord_cols - 1 and all(numeric):
            if allow_pad:
                return "data", "", [float(c) for c in cells] + [0.0], "该行缺高程列，按 0 处理"
            return "error", "", [], f"需要 {coord_cols} 个坐标列，实际 {m} 列（如需可允许缺失高程列补 0）"
        return "header", "", [], ""

    out: list[tuple[str, list[float]]] = []
    notes: list[str] = []
    started = False
    for cells, no in zip(rows, nos):
        kind, name, vals, msg = classify(cells)
        if kind == "header":
            if not started:
                continue
            raise ValueError(f"第 {no} 行无法解析为坐标数据：{' '.join(cells)}")
        if kind == "error":
            raise ValueError(f"第 {no} 行 {msg}：{' '.join(cells)}")
        started = True
        if len(vals) < coord_cols and not keep_extra:
            vals = vals + [0.0] * (coord_cols - len(vals))
        if msg:
            notes.append(f"第 {no} 行 {msg}")
        out.append((name, vals))
    if not out:
        raise ValueError(f"未识别到数据行（首行被当作表头且无后续数据）：{p}")
    return out, notes


