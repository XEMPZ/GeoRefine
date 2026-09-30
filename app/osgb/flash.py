"""把 GeoRefine 参数文档接到 osgb_vertex_transform（C++ 内存态工具）上。

轴序（致命细节）
----------------
| 坐标系 | 平面分量顺序 |
|---|---|
| OSGB（ContextCapture / GIS / ENU） | **(东 E, 北 N)** |
| 本项目测量约定 | **(北 x, 东 y)** |

两者**相反**。OSGB 顶点与 `SRSOrigin` 都是 (东, 北, 高)；
`apply_points` 期望 (北, 东, 高)。
换轴由 `app.osgb.transform` 统一处理，复用项目既有函数
`app.core.transform2d.to_survey_xy`（CAD(东,北)→测量(北,东)），不另写。

设计要点：**变换顶点，保持 SRSOrigin 不变**
-------------------------------------------
OSGB 顶点 = 实际坐标 − SRSOrigin。有两种等价的实现方式：

  甲) 顶点置为"新坐标系下的局部偏移"，同时改写 SRSOrigin
  乙) 顶点直接承载世界坐标的位移量，SRSOrigin 不动

本模块采用**甲**：新原点 = 旧原点经同一变换后的位置。好处是：

  - 与"局部坐标系"语义一致（原点跟着模型走）
  - 顶点数值仍是小量级偏移，float32 精度充足（实测偏差 0.005 mm）
  - 输出可以直接和其它 OSGB 工具链对接，不需要额外说明

C++ 工具的输入是仿射形式 `out = A·local + b`，因此：

  b = A·OLD_origin + c − NEW_origin
  A = 变换的线性部分（由 app.osgb.transform.affine_coefficients 提取）

其中 c 是 ParamApplier 在世界坐标上的常数项。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.osgb.osgb_io import OsgToolchain, OsgToolchainMissing
from app.osgb.srs import SrsInfo, load_metadata, write_metadata_xml
from app.osgb.transform import PlanarVertexTransform, affine_coefficients

__all__ = ["FlashResult", "coeffs_for_document", "build_job_list", "run_vertex_transform_batch",
           "convert_model_fast", "DEFAULT_OPTIONS"]

# 体积控制的推荐选项（实测 2.87 MB 瓦片 → 0.82×；缺任一项都会显著变大）
DEFAULT_OPTIONS = "Compressor=zlib compression=1 WriteImageHint=IncludeFile"


@dataclass
class FlashResult:
    """快速转换结果。"""

    ok: int = 0
    failed: int = 0
    vertices: int = 0
    blocks: int = 0
    seconds: float = 0.0
    threads: int = 0
    raw: str = ""
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"瓦片 成功 {self.ok} / 失败 {self.failed}；顶点 {self.vertices}；"
                f"耗时 {self.seconds:.1f}s（{self.threads} 线程）")


def coeffs_for_document(param_doc: dict, srs: SrsInfo, *,
                        mode: str = "xyz",
                        proj_override: dict | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r"""把参数文档转成 C++ 工具需要的 (A, b, new_origin)。

    返回 (A, b, new_origin)，满足
        out_local = A @ local + b

    **本函数不实现任何坐标换算**：线性化一律委托
    \`app.osgb.transform.affine_coefficients\`（该函数亦只对 ParamApplier 做有限差分）。
    参数是否可用于平面输入，由 \`PlanarVertexTransform\` 构造时的检查裁决。

    new_origin 取旧原点本身 —— "局部坐标系不变，顶点承载世界位移"，SRSOrigin 保持不动。
    """
    old = np.asarray(srs.origin, dtype=np.float64)
    # 顶点空间类型来自 SRS：projected/enu 的顶点都是平面偏移；local 无基准、不走这里
    vkind = "planar" if srs.origin_kind in ("projected", "enu") else "planar"
    tf = PlanarVertexTransform(param_doc, mode=mode, vertex_kind=vkind,
                               origin=tuple(float(v) for v in old),
                               proj_override=proj_override)
    coef = affine_coefficients(tf)
    A = np.asarray(coef["A"], dtype=np.float64)
    b = np.asarray(coef["b"], dtype=np.float64)
    return A, b, old


def to_backend_path(p: str | Path, backend: str) -> str:
    r"""把本地路径转成目标后端能读的形式。

    backend == "wsl" 时把 `E:\a\b` 转成 `/mnt/e/a/b`；已是 POSIX 路径则原样返回。
    """
    s = str(p)
    if backend != "wsl":
        return s
    if s.startswith("/"):
        return s
    ap = os.path.abspath(s)
    drive, rest = os.path.splitdrive(ap)
    if not drive:
        return ap.replace("\\", "/")
    return "/mnt/" + drive[0].lower() + rest.replace("\\", "/")


def backend_of(binary: str | Path) -> str:
    """按可执行文件路径判断它跑在哪一侧。"""
    return "wsl" if str(binary).startswith("/") else "win"


def _fmt(a: np.ndarray) -> str:
    return ",".join(repr(float(v)) for v in np.asarray(a).ravel())


def build_job_list(jobs, A: np.ndarray, b: np.ndarray, path: str | Path, *,
                   backend: str = "win") -> Path:
    """生成 osgb_vertex_transform 的批处理清单（TSV）。jobs 为 [(src, dst)]。

    backend="wsl" 时把清单里的路径转成 /mnt/... 形式——**清单路径本身也要转**，
    因为 C++ 工具在 WSL 内解析这些行。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    as_ = _fmt(A)
    bs = _fmt(b)
    with open(p, "w", encoding="utf-8") as f:
        for src, dst in jobs:
            f.write(f"{to_backend_path(src, backend)}\t{to_backend_path(dst, backend)}\t{as_}\t{bs}\n")
    return p


def run_vertex_transform_batch(binary: str | Path, job_list: str | Path, *,
                       options: str = DEFAULT_OPTIONS, threads: int = 16,
                       timeout: float = 7200.0, cancel=None) -> FlashResult:
    """调用 C++ 工具执行批处理。

    binary 可以是：
      - Windows 可执行文件路径（直接调用）
      - WSL 内路径（经 wsl.exe 调用，自动做路径转换）
    """
    res = FlashResult(threads=threads)
    bin_s = str(binary)
    be = backend_of(binary)

    if be == "wsl":
        cmd = ["wsl.exe", "--", bin_s, "--batch",
               to_backend_path(job_list, "wsl"), options, str(threads)]
    else:
        cmd = [bin_s, "--batch", str(job_list), options, str(threads)]

    if cancel is not None:
        # 边跑边看取消标志：C++ 工具一次调用处理整批，无法中途插入，
        # 因此用"跑完后如果想取消就丢弃结果"的语义，并允许调用方提前中止等待
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
        if cancel():
            res.raw = "已取消"
            res.failures.append("用户取消")
            return res
    else:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out, err = p.stdout or "", p.stderr or ""
    res.raw = (out + err)[-4000:]
    for line in out.splitlines():
        if line.startswith("=== SUMMARY ==="):
            # ok=.. fail=.. verts=.. blocks=.. wall=.. threads=.. read=.. xform=.. write=..
            def num(key: str, default: float = 0.0) -> float:
                """取 SUMMARY 里的数值；容忍 's'/'ms' 等后缀。"""
                raw = kv.get(key)
                if raw is None:
                    return default
                return float(re.sub(r"[^0-9eE.+-]", "", raw) or default)

            kv = {}
            for tok in line[len("=== SUMMARY ==="):].split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k] = v
            res.ok = int(num("ok"))
            res.failed = int(num("fail"))
            res.vertices = int(num("verts"))
            res.blocks = int(num("blocks"))
            res.seconds = num("wall")
            res.threads = int(num("threads", threads))
            break
    if not res.ok and not res.failed:
        res.failures.append(res.raw[-300:] or "osgb_vertex_transform 无输出")
    return res


def convert_model_fast(model_dir: str | Path, out_dir: str | Path, param_doc: dict, *,
                       mode: str = "xyz",
                       binary: str | Path,
                       options: str = DEFAULT_OPTIONS,
                       threads: int = 16,
                       keep_origin: bool = True,
                       stage_local: bool = True,
                       progress=None,
                       cancel=None,
                       proj_override: dict | None = None) -> FlashResult:
    """用 osgb_vertex_transform 整模型转换（快速路径）。

    model_dir: 含 metadata.xml 的模型根目录
    param_doc: GeoRefine 参数文档（必须可用于平面输入）
    binary:    osgb_vertex_transform 可执行文件（Windows 路径或 WSL 路径）
    keep_origin: True 时 SRSOrigin 不变（局部坐标系不变）
    stage_local: True 时先写 WSL 本地盘再拷回（挂载点 I/O 优化）
    """
    from app.osgb.osgb_job import find_model_root, iter_tile_files

    root = find_model_root(model_dir)
    out_root = Path(out_dir)
    if out_root.resolve() == root.resolve():
        raise ValueError("输出目录不能与模型目录相同（拒绝覆盖输入）")
    out_root.mkdir(parents=True, exist_ok=True)

    srs = load_metadata(root / "metadata.xml")
    if srs.origin_kind == "local":
        raise ValueError("该模型 SRS 为 local，无地理基准，无法做坐标/高程转换")

    A, b, new_origin = coeffs_for_document(param_doc, srs, mode=mode,
                                           proj_override=proj_override)
    # (A, b) 已满足 out_local = A @ local + b，b 即参数给出的位移。
    # new_origin 本实现不使用：顶点承载位移，SRSOrigin 保持不变。

    tiles = iter_tile_files(root)
    jobs = []
    for src in tiles:
        rel = src.relative_to(root)
        jobs.append((str(src), str(out_root / rel)))

    # 任务清单 + 结果目录
    tmp = Path(tempfile.mkdtemp(prefix="osgbflash_"))
    lst = build_job_list(jobs, A, b, tmp / "jobs.tsv", backend=backend_of(binary))
    res = run_vertex_transform_batch(binary, lst, options=options, threads=threads, cancel=cancel)

    if not keep_origin:
        # 需要改写 SRSOrigin
        info = SrsInfo(srs=srs.srs, origin=tuple(float(v) for v in new_origin),
                       origin_kind=srs.origin_kind, epsg=srs.epsg,
                       enu_latlon=srs.enu_latlon, raw_xml=srs.raw_xml)
        write_metadata_xml(out_root / "metadata.xml", info, template=srs.raw_xml)
    else:
        # SRSOrigin 不变，但输出目录要有一份 metadata.xml
        (out_root / "metadata.xml").write_text(srs.raw_xml, encoding="utf-8")

    if progress:
        progress(res)
    return res
