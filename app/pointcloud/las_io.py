r"""LAS 读写封装（基于公开库 laspy）。

**不自研 LAS 格式解析**——头部、VLR、点记录布局全部交给 laspy。本模块只做三件薄事：

1. `LasInfo`：把头文件里影响能否安全改写的字段提炼出来（点格式、scale/offset、
   min/max、CRS、是否有 RGB），供界面显示与校验。
2. `iter_chunks`：分块读取，避免数千万点一次性进内存。
3. `write_like`：按源文件的头与点格式写出，**除 X/Y/Z 外所有维度原样搬运**。

关键约束：**scale/offset 保持源文件的值**。LAS 坐标 = 整数 × scale + offset，
scale 即量化步长；改写它等于改变精度口径，不属于坐标转换。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["LasToolchainMissing", "LasInfo", "read_info", "iter_chunks", "write_like",
           "is_las", "has_laspy"]


class LasToolchainMissing(RuntimeError):
    """缺少 laspy。"""


def has_laspy() -> bool:
    try:
        import laspy  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _laspy():
    try:
        import laspy
    except Exception as exc:  # noqa: BLE001
        raise LasToolchainMissing(
            "缺少 laspy，无法读写 LAS。请安装：pip install laspy") from exc
    return laspy


def is_las(path) -> bool:
    return str(path).lower().endswith((".las", ".laz"))


@dataclass
class LasInfo:
    """LAS 头里与坐标改写相关的信息。"""

    path: str = ""
    version: str = ""
    point_format: int = 0
    point_count: int = 0
    point_size: int = 0
    scale: tuple = (0.01, 0.01, 0.01)
    offset: tuple = (0.0, 0.0, 0.0)
    mins: tuple = (0.0, 0.0, 0.0)
    maxs: tuple = (0.0, 0.0, 0.0)
    compression: bool = False
    has_color: bool = False
    dimensions: tuple = ()
    crs_wkt: str = ""
    system_id: str = ""
    software: str = ""

    @property
    def quantum(self) -> tuple:
        """量化步长 = scale（1 个整数单位对应的米数）。"""
        return tuple(float(s) for s in self.scale)

    def axis_hint(self) -> str:
        """按坐标量级推测 X/Y 的轴含义（LAS 惯例 X=东、Y=北）。

        不猜就明确说明，轴序判错会导致整份数据错位。
        """
        x_lo, x_hi = float(self.mins[0]), float(self.maxs[0])
        y_lo, y_hi = float(self.mins[1]), float(self.maxs[1])
        out = []
        if 0.0 <= x_lo and x_hi < 1e7:
            out.append("X 在东坐标量级")
        if y_lo > 1e5:
            out.append("Y 在北坐标量级")
        if out:
            return "；".join(out) + "（符合 LAS 惯例 X=东、Y=北）"
        return "量级不符合常见投影坐标，轴序需人工确认"

    def describe(self) -> str:
        z = self.quantum
        return ("LAS %s | 点格式 %d | %d 点 | %d 字节/点 | 压缩 %s\n"
                "scale=(%.6g, %.6g, %.6g) -> 量化步长 %.4g m\n"
                "offset=(%.3f, %.3f, %.3f)\n"
                "X %.3f~%.3f  Y %.3f~%.3f  Z %.3f~%.3f\n"
                "RGB %s | CRS %s\n%s"
                % (self.version, self.point_format, self.point_count, self.point_size,
                   "是" if self.compression else "否", z[0], z[1], z[2], z[0],
                   self.offset[0], self.offset[1], self.offset[2],
                   float(self.mins[0]), float(self.maxs[0]),
                   float(self.mins[1]), float(self.maxs[1]),
                   float(self.mins[2]), float(self.maxs[2]),
                   "有" if self.has_color else "无",
                   ("已定义" if self.crs_wkt else "未定义"),
                   self.axis_hint()))


def read_info(path) -> LasInfo:
    """读头部信息（不加载点数据）。"""
    laspy = _laspy()
    p = Path(path)
    with laspy.open(str(p)) as fh:
        h = fh.header
        info = LasInfo(
            path=str(p),
            version=str(h.version),
            point_format=int(h.point_format.id),
            point_count=int(h.point_count),
            point_size=int(h.point_format.size),
            scale=tuple(float(v) for v in h.scales),
            offset=tuple(float(v) for v in h.offsets),
            mins=tuple(float(v) for v in h.mins),
            maxs=tuple(float(v) for v in h.maxs),
            compression=bool(getattr(h, "are_points_compressed", False)),
            has_color=bool("red" in set(h.point_format.dimension_names)),
            dimensions=tuple(h.point_format.dimension_names),
            system_id=str(getattr(h, "system_identifier", "") or ""),
            software=str(getattr(h, "generating_software", "") or ""),
        )
        try:
            crs = h.parse_crs()
            info.crs_wkt = crs.to_wkt() if crs is not None else ""
        except Exception:  # noqa: BLE001
            info.crs_wkt = ""
    return info


def _chunk_size(point_size: int, target_mb: float = 64.0) -> int:
    """按目标内存占用估算每块点数（默认约 64 MB）。"""
    per = max(1, int(point_size))
    return max(1000, int(target_mb * 1024 * 1024 / per))


def iter_chunks(path, *, target_mb: float = 64.0, max_points=None):
    """分块读取，逐块产出 (start_index, laspy.LasData)。

    laspy 的 LasData 支持 .x/.y/.z 以浮点世界坐标读写，
    也能用 [维度名] 访问任意维度（intensity/classification/red 等）。
    """
    laspy = _laspy()
    p = Path(path)
    with laspy.open(str(p)) as fh:
        size = int(fh.header.point_format.size)
        n = _chunk_size(size, target_mb)
        done = 0
        while True:
            if max_points is not None and done >= max_points:
                return
            take = n if max_points is None else min(n, max_points - done)
            pts = fh.read_points(take)
            if len(pts) == 0:
                return
            yield done, pts
            done += len(pts)


def write_like(src_info: LasInfo, dst_path, chunks, *, progress=None):
    """按源头的点格式与 scale/offset 写出 LAS。

    chunks 产出 (start, LasData)。**除 X/Y/Z 外所有维度与头部元数据原样搬运**：
    点格式、scale/offset、VLR（含 CRS WKT）、各维度数值都取自源数据；
    仅按实际写出值重算 min/max。

    返回 (点数, mins, maxs)。
    """
    laspy = _laspy()
    import numpy as np

    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with laspy.open(str(src_info.path)) as sh:
        src_header = sh.header

    # 关键：**直接沿用源头的头对象**，而不是照 point_format.id 新建。
    # 原因：扫描仪导出的合并文件会带**自定义维度**（如 "Original cloud index"），
    # 此时 header.point_format.id 报的值与实际不符，按 id 新建头会因维度不匹配
    # 报 "Incompatible point formats"。用源头对象则自定义维度、VLR/CRS、
    # scale/offset 全部原样保留。
    # 点数与 min/max 由 laspy 在写完后自行统计（下面把 point_count 归零，
    # 让它不要沿用源头的旧值）。
    from copy import deepcopy
    new_header = deepcopy(src_header)
    try:
        new_header.point_count = 0
    except Exception:  # noqa: BLE001
        pass
    n_total = 0
    mn = None
    mx = None
    with laspy.open(str(dst), mode="w", header=new_header) as w:
        for _idx, pts in chunks:
            # 写入：laspy 按 scale/offset 把浮点世界坐标反算为整数存储
            w.write_points(pts)
            n_total += len(pts)
            xs = np.asarray(pts.x, dtype=float)
            ys = np.asarray(pts.y, dtype=float)
            zs = np.asarray(pts.z, dtype=float)
            bmn = np.array([xs.min(), ys.min(), zs.min()])
            bmx = np.array([xs.max(), ys.max(), zs.max()])
            mn = bmn if mn is None else np.minimum(mn, bmn)
            mx = bmx if mx is None else np.maximum(mx, bmx)
            if progress:
                progress(n_total)
    return (n_total,
            tuple(float(v) for v in (mn if mn is not None else (0.0, 0.0, 0.0))),
            tuple(float(v) for v in (mx if mx is not None else (0.0, 0.0, 0.0))))


def offset_to_int(srs_origin, info: LasInfo):
    """把 LAS 的 offset 折算成"该文件坐标的参考原点"，供转换层记录。

    LAS 存的是「整数 × scale + offset」，offset 即该文件的坐标基准。
    坐标转换在这一层之上进行，因此保留 offset 不变即可保证与源文件同口径。
    """
    return tuple(float(v) for v in info.offset)
