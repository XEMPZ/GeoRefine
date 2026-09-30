r"""LAS 点云批量转换作业。

每个 LAS 文件：分块读 → 变换 X/Y/Z → 写出；**其他所有维度原样搬运**。
输出目录结构镜像输入，绝不覆盖源文件。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.pointcloud import las_io
from app.pointcloud.las_transform import LasVertexTransform

__all__ = ["LasFileResult", "LasJobResult", "find_las_files", "convert_las_file",
           "convert_pointcloud"]

# 源数据里只该处理"主数据"LAS；这些后缀是索引/中间产物，必须跳过
_SKIP_SUFFIX = ("_dasindex.las", "_dasindex.xlas")


def find_las_files(root, *, recursive: bool = True) -> list[Path]:
    """收集待转换的 LAS/LAZ，跳过索引与中间文件。"""
    root = Path(root)
    out = []
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        if not p.is_file():
            continue
        low = p.name.lower()
        if not low.endswith((".las", ".laz")):
            continue
        if any(low.endswith(s) for s in _SKIP_SUFFIX):
            continue
        out.append(p)
    out.sort()
    return out


@dataclass
class LasFileResult:
    src: str = ""
    dst: str = ""
    ok: bool = False
    reason: str = ""
    points: int = 0
    seconds: float = 0.0
    mins: tuple = (0.0, 0.0, 0.0)
    maxs: tuple = (0.0, 0.0, 0.0)


@dataclass
class LasJobResult:
    files: list = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok_count(self) -> int:
        return sum(1 for f in self.files if f.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.files if not f.ok)

    @property
    def point_total(self) -> int:
        return sum(f.points for f in self.files if f.ok)

    def summary(self) -> str:
        return ("文件 成功 %d / 失败 %d；点 %d；耗时 %.1fs"
                % (self.ok_count, self.fail_count, self.point_total, self.seconds))


def convert_las_file(tf: LasVertexTransform, src, dst, *, chunk_mb: float = 64.0,
                     progress=None, cancel=None) -> LasFileResult:
    """转换单个 LAS 文件。只改 X/Y/Z，其余维度与头部元数据原样搬运。"""
    res = LasFileResult(src=str(src), dst=str(dst))
    t0 = time.time()
    try:
        info = las_io.read_info(src)
    except Exception as exc:  # noqa: BLE001
        res.reason = "%s: %s" % (type(exc).__name__, exc)
        res.seconds = time.time() - t0
        return res

    def chunks():
        for start, pts in las_io.iter_chunks(src, target_mb=chunk_mb):
            if cancel is not None and cancel():
                raise RuntimeError("已取消")
            # 只改坐标：laspy 的 .x/.y/.z 是浮点世界坐标视图
            xe = np.asarray(pts.x, dtype=np.float64)
            yn = np.asarray(pts.y, dtype=np.float64)
            zz = np.asarray(pts.z, dtype=np.float64)
            ne, nn, nz = tf.apply(xe, yn, zz)
            pts.x = ne          # 写回：laspy 按 scale/offset 存为整数
            pts.y = nn
            pts.z = nz
            yield start, pts

    try:
        n, mn, mx = las_io.write_like(
            info, dst, chunks(),
            progress=(lambda done: progress(done, info.point_count)) if progress else None)
        res.ok = True
        res.points = n
        res.mins = mn
        res.maxs = mx
    except Exception as exc:  # noqa: BLE001
        res.reason = "%s: %s" % (type(exc).__name__, exc)
    res.seconds = time.time() - t0
    return res


def convert_pointcloud(src_root, dst_root, param_doc, *, mode: str = "xyz",
                       proj_override: dict | None = None, recursive: bool = True,
                       chunk_mb: float = 64.0, progress=None,
                       cancel=None) -> LasJobResult:
    """整个目录的 LAS 转换。输出目录镜像输入结构，绝不覆盖源文件。"""
    src_root = Path(src_root)
    dst_root = Path(dst_root)
    if src_root.resolve() == dst_root.resolve():
        raise ValueError("输出目录不能与输入目录相同（拒绝覆盖源数据）")

    files = find_las_files(src_root, recursive=recursive)
    tf = LasVertexTransform(param_doc, mode=mode, proj_override=proj_override)
    job = LasJobResult()
    t0 = time.time()
    for idx, src in enumerate(files):
        if cancel is not None and cancel():
            break
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        if progress:
            progress(idx, len(files), src.name)
        # 单个文件失败（被占用/损坏/磁盘满）**不得中断整批**，
        # 登记为该文件失败继续下一个——扫描仪软件常驻时会锁住正在写的 LAS。
        try:
            job.files.append(convert_las_file(tf, src, dst, chunk_mb=chunk_mb,
                                              cancel=cancel))
        except Exception as exc:  # noqa: BLE001
            job.files.append(LasFileResult(
                src=str(src), dst=str(dst), ok=False,
                reason="%s: %s" % (type(exc).__name__, exc)))
    job.seconds = time.time() - t0
    return job
