r"""OSGB 模型转换的批量驱动。

单个瓦片的处理流程（两遍，内存受控）：

    pass1  osgb --> osgt(临时)   用 ParamApplier 对前若干顶点试算，定出新 SRSOrigin
    pass2  读 osgt，逐块改写顶点 --> 新 osgt --> osgb（输出目录）
    清理   删除临时 osgt

不做的事（有意为之）
--------------------
- **不合并/重划瓦片**：逐瓦片独立处理，保持原目录结构与 LOD 层次。
  （OSGBLab 会重算瓦片划分，本项目不需要——坐标变换不依赖分块方式。）
- **不改纹理、材质、LOD 范围**：只动顶点数组。
- **不覆盖输入**：输出一律写到独立目录；同名文件冲突时报错而不是静默覆盖。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.osgb.osgb_io import OsgToolchain
from app.osgb.srs import SrsInfo, load_metadata, write_metadata_xml
from app.osgb.transform import PlanarVertexTransform, derive_new_origin

__all__ = ["TileResult", "ModelConvertResult", "convert_tile", "convert_model", "find_model_root"]

_VEC = re.compile(r"^(\s*)vector (\d+) \{\s*$")
_VEC_ANCHOR = re.compile(r"^\s*osg::Vec3Array\s*\{")
_F3 = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
                 r"\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
                 r"\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")


@dataclass
class TileResult:
    r"""一个瓦片的处理结果。"""

    src: str
    dst: str | None
    blocks: int = 0
    vertices: int = 0
    status: str = "ok"          # ok | skipped | failed
    message: str = ""


@dataclass
class ModelConvertResult:
    r"""整模型转换结果。"""

    src_root: str
    dst_root: str
    tiles: list[TileResult] = field(default_factory=list)
    old_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    new_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def ok_count(self) -> int:
        return sum(1 for t in self.tiles if t.status == "ok")

    @property
    def skip_count(self) -> int:
        return sum(1 for t in self.tiles if t.status == "skipped")

    @property
    def fail_count(self) -> int:
        return sum(1 for t in self.tiles if t.status == "failed")

    @property
    def vertex_total(self) -> int:
        return sum(t.vertices for t in self.tiles)

    def summary(self) -> str:
        return (f"瓦片 {len(self.tiles)}：成功 {self.ok_count}，"
                f"跳过 {self.skip_count}，失败 {self.fail_count}；"
                f"顶点 {self.vertex_total}；原点 {self.old_origin} → "
                f"{tuple(round(v, 4) for v in self.new_origin)}")


def find_model_root(path: str | Path) -> Path:
    r"""定位模型根目录（含 metadata.xml 的那一层）。

    传入 metadata.xml 本身、或任意子目录、或根目录都可。找不到则抛 FileNotFoundError。
    """
    p = Path(path)
    if p.is_file():
        if p.name.lower() == "metadata.xml":
            return p.parent
        raise FileNotFoundError(f"不是 metadata.xml：{p}")
    if (p / "metadata.xml").is_file():
        return p
    # 一层子目录里找（用户常选到模型目录的上一级）
    cands = [d for d in p.iterdir() if d.is_dir() and (d / "metadata.xml").is_file()]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        raise FileNotFoundError(f"{p} 下有多个含 metadata.xml 的目录，请直接指定其中之一")
    raise FileNotFoundError(f"在 {p} 及下一层未找到 metadata.xml")


def iter_tile_files(root: str | Path) -> list[Path]:
    r"""枚举模型下所有 .osgb（稳定排序，便于进度与复现）。"""
    root = Path(root)
    out = [Path(dp) / f for dp, _dn, fn in os.walk(root) for f in fn
           if f.lower().endswith(".osgb")]
    out.sort()
    return out


def _find_vertex_blocks(lines: list[str]) -> list[tuple[int, int, str, int]]:
    """定位所有 `osg::Vec3Array` 对象内部的 `vector N {` 块。

    返回 [(header_idx, end_idx_exclusive, indent, count)]。

    为什么用"上下文锚定"而不是"形状猜测"：osgt 里除顶点外还有
    `osg::DrawElementsUInt` 的索引块（每行 4 个整数）、纹理坐标块（每行 2 个浮点）。
    早期实现按"每行 3 个浮点"判断块形状，遇到索引块时跳转错误，导致后续顶点块
    丢失、甚至把索引数据当顶点变换。锚定 `osg::Vec3Array` 后，索引块天然被排除：
    ```
    VertexArray TRUE {
      osg::Vec3Array {          ← 锚点
        UniqueID 12
        BufferObject TRUE { osg::VertexBufferObject { UniqueID 13 } }
        Binding BIND_PER_VERTEX
        vector 5168 {           ← 目标块（每行 3 个浮点）
    ```
    """
    out: list[tuple[int, int, str, int]] = []
    i, n = 0, len(lines)
    while i < n:
        if not _VEC_ANCHOR.match(lines[i]):
            i += 1
            continue
        # 锚点之后找第一个 vector N { （限 200 行内，避免跨对象误配）
        j, limit = i + 1, min(n, i + 200)
        while j < limit:
            m = _VEC.match(lines[j])
            if m:
                indent, count = m.group(1), int(m.group(2))
                k, taken = j + 1, 0
                while k < n and taken < count:
                    if not _F3.match(lines[k]):
                        break
                    k += 1
                    taken += 1
                if taken == count and count > 0:
                    out.append((j, k, indent, count))
                i = k if taken == count else j + 1
                break
            j += 1
        else:
            i += 1
    return out


def _read_vertices_pass1(txt: str | Path, probe_blocks: int = 4) -> np.ndarray:
    """采样前若干 Vec3Array 块的顶点，用于外部推算新原点。"""
    lines = Path(txt).read_text(encoding="utf-8", errors="replace").splitlines()
    got: list[np.ndarray] = []
    for (h, e, _ind, _cnt) in _find_vertex_blocks(lines):
        rows = []
        for k in range(h + 1, e):
            mm = _F3.match(lines[k])
            if mm:
                rows.append((float(mm.group(1)), float(mm.group(2)), float(mm.group(3))))
        if rows:
            got.append(np.asarray(rows, dtype=np.float64))
            if len(got) >= probe_blocks:
                break
    return np.vstack(got) if got else np.empty((0, 3), dtype=np.float64)


def _rewrite_text(src_txt: str | Path, dst_txt: str | Path, transform) -> tuple[int, int]:
    """只改 Vec3Array 顶点块，其余内容逐字保留。返回 (块数, 顶点数)。"""
    lines = Path(src_txt).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    plain = [ln.rstrip("\n").rstrip("\r") for ln in lines]
    blocks = _find_vertex_blocks(plain)
    out: list[str] = []
    cur = nblk = nvert = 0
    for (h, e, indent, _count) in blocks:
        out.extend(lines[cur:h + 1])
        rows = []
        for k in range(h + 1, e):
            mm = _F3.match(plain[k])
            rows.append((float(mm.group(1)), float(mm.group(2)), float(mm.group(3))))
        arr = np.asarray(rows, dtype=np.float64)
        new = transform.apply(arr)
        nl = "\n" if lines[h].endswith("\n") else ""
        for r in new:
            out.append(f"{indent}  {r[0]:.10f} {r[1]:.10f} {r[2]:.10f}{nl}")
        cur = e
        nblk += 1
        nvert += len(rows)
    out.extend(lines[cur:])
    Path(dst_txt).write_text("".join(out), encoding="utf-8")
    return nblk, nvert


def convert_tile(tool: OsgToolchain, src_osgb: str | Path, dst_osgb: str | Path,
                 srs: SrsInfo, transform: PlanarVertexTransform, *,
                 work_dir: str | Path | None = None) -> TileResult:
    """转换单个瓦片。返回 TileResult（失败时 status='failed'，不抛异常）。

    两遍处理：
      pass1  osgb → osgt，采样 vec3 块顶点（用于外部推算新原点）
      pass2  上下文感知改写 vec3 块 → 新 osgt → osgb
    临时文件默认放系统临时目录，处理完即清理。
    """
    src_osgb = Path(src_osgb)
    dst_osgb = Path(dst_osgb)
    res = TileResult(src=str(src_osgb), dst=str(dst_osgb))
    tmp_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="osgbconv_"))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_txt = tmp_dir / (src_osgb.stem + ".osgt")
    out_txt = tmp_dir / (src_osgb.stem + ".out.osgt")
    try:
        tool.to_text(src_osgb, tmp_txt)
        if _read_vertices_pass1(tmp_txt).shape[0] == 0:
            res.status = "skipped"
            res.message = "无顶点数组（纯节点文件）"
            return res
        # ---------- pass2：逐块改写（上下文感知，非 vec3 块原样保留）----------
        dst_osgb.parent.mkdir(parents=True, exist_ok=True)
        res.blocks, res.vertices = _rewrite_text(tmp_txt, out_txt, transform)
        if res.blocks == 0:
            res.status = "skipped"
            res.message = "未找到可变换的顶点数组"
            return res
        tool.to_binary(out_txt, dst_osgb)
        res.status = "ok"
    except Exception as e:  # noqa: BLE001 —— 单瓦片失败不应中断整模型
        res.status = "failed"
        res.message = f"{type(e).__name__}: {e}"[:300]
    finally:
        if work_dir is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            for p in (tmp_txt, out_txt):
                try:
                    p.unlink()
                except OSError:
                    pass
    return res


def convert_model(model_dir: str | Path, out_dir: str | Path, param_doc: dict, *,
                  mode: str = "xyz",
                  tool: OsgToolchain | None = None,
                  progress=None,
                  cancel=None,
                  max_tiles: int | None = None) -> ModelConvertResult:
    r"""转换整个 OSGB 模型。

    model_dir: 含 metadata.xml 的模型根目录（或可直接被 find_model_root 解析的路径）
    out_dir:   输出根目录（自动创建；不得与 model_dir 相同）
    param_doc: 参数库中的参数文档（必须可用于平面输入）
    mode:      xyz / xy / z
    progress:  可选回调 (done, total, tile_result)
    cancel:    可选回调 () -> bool，返回 True 时中止并返回已完成部分

    返回 ModelConvertResult。整模型级的错误（缺 metadata、参数不可用、输出目录非法）
    直接抛异常；单瓦片错误记入 TileResult.status='failed'。
    """
    root = find_model_root(model_dir)
    out_root = Path(out_dir)
    if out_root.resolve() == root.resolve():
        raise ValueError("输出目录不能与模型目录相同（拒绝覆盖输入）")
    out_root.mkdir(parents=True, exist_ok=True)

    srs = load_metadata(root / "metadata.xml")
    if srs.origin_kind == "local":
        raise ValueError("该模型 SRS 为 local，无地理基准，无法做坐标/高程转换")

    tool = tool or OsgToolchain()
    # 语义与 ApplyPage 一致：参数直接作用于局部顶点（OSGB 顶点 = 实际坐标 − SRSOrigin）
    transform = PlanarVertexTransform(param_doc, mode=mode)
    new_origin = derive_new_origin(param_doc, srs.origin, mode=mode, transform=transform)

    result = ModelConvertResult(src_root=str(root), dst_root=str(out_root),
                               old_origin=tuple(srs.origin),
                               new_origin=tuple(float(v) for v in new_origin))

    tiles = iter_tile_files(root)
    if max_tiles:
        tiles = tiles[:max_tiles]
    total = len(tiles)
    for k, src in enumerate(tiles):
        if cancel and cancel():
            break
        rel = src.relative_to(root)
        dst = out_root / rel
        # 直接用原始字节复制非几何文件由外部负责；这里只处理 .osgb
        r = convert_tile(tool, src, dst, srs, transform)
        result.tiles.append(r)
        if progress:
            progress(k + 1, total, r)

    # metadata.xml 原样写出：位移落在顶点上，SRSOrigin 与 SRS 都不变
    (out_root / "metadata.xml").write_text(srs.raw_xml, encoding="utf-8")
    return result
