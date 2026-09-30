"""批处理编排：把整批 OSGB 转换交给一次 WSL 调用完成。

性能背景（实测，2.5 MB 瓦片）
------------------------------
| 方式 | 耗时 |
|---|---|
| 从 /mnt/e 直接让 osgconv 读写 | 53.9 s |
| WSL 内部先 cp 到 /tmp 再读 | 0.36 s（150 倍） |
| Windows 侧经 UNC 拷贝进 WSL | 52 s（同样被 DrvFs 惩罚） |

结论：**拷贝与转换都必须由 WSL 自己执行**，且**一次 wsl 调用处理整批**。
真正干活的脚本是同目录的 \`_wsl_batch.py\` 与 \`_wsl_rewrite.py\`（纯标准库）。

职责边界
--------
- Windows 侧（本项目）：解析参数、算好仿射系数与原点、组织任务清单、汇总结果
- WSL 侧：拷贝、osgconv、按系数改写顶点
两者之间只传一个 JSON 清单，**不做逐瓦片的跨系统往返**。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["BatchResult", "run_batch"]

_HERE = Path(__file__).resolve().parent


@dataclass
class BatchResult:
    """整批执行结果。"""

    ok: int = 0
    skipped: int = 0
    failed: int = 0
    blocks: int = 0
    vertices: int = 0
    bytes_out: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    details: list[tuple] = field(default_factory=list)
    raw: str = ""

    def summary(self) -> str:
        return (f"成功 {self.ok}，跳过 {self.skipped}，失败 {self.failed}；"
                f"顶点 {self.vertices}，输出 {self.bytes_out / 1048576:.1f} MB；"
                f"耗时 {self.seconds:.1f}s")


def win_to_wsl(path: str | Path) -> str:
    """E:\\a\\b → /mnt/e/a/b；已是 POSIX 路径则原样返回。"""
    p = str(path)
    if p.startswith("/"):
        return p
    ap = os.path.abspath(p)
    drive, rest = os.path.splitdrive(ap)
    if not drive:
        return ap.replace("\\", "/")
    return "/mnt/" + drive[0].lower() + rest.replace("\\", "/")


def run_batch(jobs: list[tuple[str, str]], params: dict, *,
              distro: str = "Ubuntu", timeout: float = 14400.0,
              osgconv: str = "/usr/bin/osgconv",
              workers: int = 8,
              progress=None) -> BatchResult:
    """执行整批转换。

    jobs:   [(源 .osgb 路径, 目标 .osgb 路径)]，Windows 或 WSL 路径均可
    params: {"origin": [x,y,z], "new_origin": [x,y,z], "A": 3x3, "b": [3]}
            由调用方（transform 层）算好
    progress: 可选回调，暂不支持逐瓦片（整批一次调用），仅用于提示开始/结束
    返回 BatchResult。
    """
    res = BatchResult()
    if not jobs:
        return res

    wsl_jobs = [[win_to_wsl(a), win_to_wsl(b)] for a, b in jobs]

    win_tmp = Path(tempfile.mkdtemp(prefix="osgbbatch_"))
    params_path = win_tmp / "params.json"
    cfg_path = win_tmp / "jobs.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")

    cfg = {
        "osgconv": osgconv,
        "python": "python3",
        "rewriter": win_to_wsl(_HERE / "_wsl_rewrite.py"),
        "workers": max(1, int(workers)),
        "params_path": win_to_wsl(params_path),
        "jobs": wsl_jobs,
    }
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    runner = win_to_wsl(_HERE / "_wsl_batch.py")
    t0 = time.time()
    try:
        p = subprocess.run(["wsl.exe", "-d", distro, "--", "python3", runner,
                            win_to_wsl(cfg_path)],
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        res.seconds = time.time() - t0
        out = p.stdout or ""
        res.raw = out[-4000:]
        if "=== SUMMARY ===" not in out:
            res.failed = len(jobs)
            res.failures.append(((p.stderr or out)[:500]).replace("\n", " "))
            return res
        _, _, tail = out.partition("=== SUMMARY ===")
        lines = [ln for ln in tail.strip().splitlines() if ln.strip()]
        nums = lines[0].split()
        if len(nums) >= 7:
            res.ok, res.skipped, res.failed = int(nums[0]), int(nums[1]), int(nums[2])
            res.blocks, res.vertices, res.bytes_out = int(nums[3]), int(nums[4]), int(nums[5])
            res.seconds = float(nums[6])
        for ln in lines[1:]:
            parts = ln.split("\t")
            if len(parts) >= 3:
                res.details.append(tuple(parts))
                if parts[1] == "failed":
                    res.failures.append(f"job {parts[0]}: {parts[2]}")
        if progress:
            progress(res)
    except subprocess.TimeoutExpired:
        res.failures.append(f"超时（{timeout}s）")
        res.failed = len(jobs)
    finally:
        for f in (params_path, cfg_path):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            win_tmp.rmdir()
        except OSError:
            pass
    return res
