r"""OSGB 模型的参考坐标系（SRS）与原点（SRSOrigin）解析/回写。

坐标语义（ContextCapture / Terra 导出约定，经实机 metadata.xml 核实）：

    OSGB 顶点 = 实际坐标 − SRSOrigin

- `SRS` 为投影坐标系（如 `EPSG:4547`）时，顶点是**投影平面坐标的局部偏移**，
  实际坐标 = 顶点 + SRSOrigin，三个分量分别是 (北 x, 东 y, 高 z)。
- `SRS` 为 `ENU:lat,lon` 时，顶点是**局部 ENU 米**，SRSOrigin 为 (0, 0, 高程)。
- `SRS` 为 `local` 或缺失时，顶点没有地理基准，只能做纯几何变换。

本模块只做解析与回写，不做任何坐标换算。

参考：OSGBLab 二进制中 `OSGBLab::ParseMetadata` 读取的键为
`ModelMetadata.SRS` / `ModelMetadata.SRSOrigin`（`.rodata` 实证），
并有 `"local"` 字符串分支。字段名与实机文件一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SrsInfo", "parse_metadata_xml", "load_metadata", "write_metadata_xml"]

_SRS_RE = re.compile(r"<SRS>(.*?)</SRS>", re.S)
_ORIGIN_RE = re.compile(r"<SRSOrigin>(.*?)</SRSOrigin>", re.S)
_EPSG_RE = re.compile(r"^EPSG:(\d+)$", re.I)
_ENU_RE = re.compile(r"^ENU:\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*$", re.I)


@dataclass
class SrsInfo:
    r"""一个 OSGB 模型的坐标系描述。

    origin_kind 取值：
      - `projected`：SRS 是 EPSG/投影定义，顶点 = 投影坐标 − origin
      - `enu`      ：SRS 是 `ENU:lat,lon`，顶点 = 局部 ENU 米，origin=(0,0,高程)
      - `local`    ：SRS 为 `local` 或缺失，无地理基准
    """

    srs: str = ""
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    origin_kind: str = "local"
    epsg: int | None = None
    enu_latlon: tuple[float, float] | None = None
    raw_xml: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def has_georeference(self) -> bool:
        """是否带有可用的地理基准（决定能否用大地坐标类参数）。"""
        return self.origin_kind in ("projected", "enu") and bool(self.srs)

    def describe(self) -> str:
        if self.origin_kind == "projected":
            return f"投影 {self.srs}，原点 ({self.origin[0]:.3f}, {self.origin[1]:.3f}, {self.origin[2]:.3f})"
        if self.origin_kind == "enu":
            lat, lon = self.enu_latlon or (0.0, 0.0)
            return f"局部 ENU（基准点 {lat:.8f}, {lon:.8f}），原点高程 {self.origin[2]:.3f}"
        return "无地理基准（local）"


def parse_metadata_xml(text: str) -> SrsInfo:
    r"""解析 metadata.xml 文本。缺失字段按 local 处理，不抛异常。"""
    info = SrsInfo(raw_xml=text)
    m = _SRS_RE.search(text or "")
    srs = (m.group(1).strip() if m else "")
    info.srs = srs

    mo = _ORIGIN_RE.search(text or "")
    if mo:
        parts = [p.strip() for p in mo.group(1).split(",")]
        vals: list[float] = []
        for p in parts[:3]:
            try:
                vals.append(float(p))
            except ValueError:
                vals.append(0.0)
        while len(vals) < 3:
            vals.append(0.0)
        info.origin = (vals[0], vals[1], vals[2])

    if not srs or srs.lower() == "local":
        info.origin_kind = "local"
        return info

    mp = _EPSG_RE.match(srs)
    if mp:
        info.origin_kind = "projected"
        info.epsg = int(mp.group(1))
        return info

    me = _ENU_RE.match(srs)
    if me:
        info.origin_kind = "enu"
        info.enu_latlon = (float(me.group(1)), float(me.group(2)))
        return info

    # 其余写法（wktext / PROJ 串 / 未知）一律按投影处理，保留原始串供上层判断
    info.origin_kind = "projected"
    return info


def load_metadata(path: str | Path) -> SrsInfo:
    r"""读取 metadata.xml（UTF-8，容忍 BOM）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    info = parse_metadata_xml(text)
    info.extra["path"] = str(p)
    return info


def write_metadata_xml(path: str | Path, info: SrsInfo, *, template: str | None = None) -> None:
    r"""按原文件模板回写 SRS / SRSOrigin，保持其余内容（注释、Texture 等）不变。"""
    text = template if template is not None else (info.raw_xml or _default_template(info))
    out = _SRS_RE.sub(lambda _m: f"<SRS>{info.srs}</SRS>", text, count=1)
    ox, oy, oz = info.origin
    repl = f"<SRSOrigin>{ox:.8f},{oy:.8f},{oz:.8f}</SRSOrigin>"
    out = _ORIGIN_RE.sub(lambda _m: repl, out, count=1)
    Path(path).write_text(out, encoding="utf-8")


def _default_template(info: SrsInfo) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ModelMetadata version="1">\n'
        f"    <SRS>{info.srs}</SRS>\n"
        "    <SRSOrigin>0,0,0</SRSOrigin>\n"
        "</ModelMetadata>\n"
    )
