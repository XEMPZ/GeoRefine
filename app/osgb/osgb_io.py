r"""OSGB 模型读写 —— 以 OpenSceneGraph（osgconv）为引擎。

为什么用 OSG 而不是自己解析字节
--------------------------------
实测（2026-09-30，样本 `BlockAB/BlockAB_L17_4.osgb`）：OSGB 里的顶点**不是 float**，
而是**按块量化的整数**（count 字段之后出现的字节形如 `65 66 fe 3e 25 77 20 3f …`）。
`osgconv tile.osgb tile.osgt` 能读出 `324.502 104.057 -75.2659`，是 OSG 依据量化
参数反算的结果——**量化是 OSG 的内部实现，不是文件格式的一部分**。

因此本模块复用 OSG 官方读写器（链接 OSG 后由
`osgDB::readNodeFile/writeNodeFile` 读写，不自行解析字节）。

调用方式
--------
osgconv 位于 WSL（`apt-get install -y openscenegraph`）。Windows 侧 Python 经
`wsl.exe` interop 调用，路径自动在 `E:\x\y` ↔ `/mnt/e/x/y` 之间转换。

    tool = OsgToolchain()                     # 自动探测
    tool.to_text("tile.osgb", "tile.osgt")    # 二进制 → 文本（顶点为十进制）
    tool.to_binary("tile.osgt", "out.osgb")   # 文本 → 二进制

已验证：osgb→osgt 0.38 s / 2.9MB 瓦片；osgt→osgb 0.13 s；往返结构完整。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "OsgToolchain", "OsgToolchainMissing", "VertexBlock",
    "win_to_wsl_path", "iter_geo_vertices",
]

_VECTOR_RE = re.compile(r"^(\s*)vector (\d+) \{\s*$")
_FLOAT3_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?) "
                        r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?) "
                        r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")


class OsgToolchainMissing(RuntimeError):
    r"""找不到可用的 osgconv —— OSGB 读写不可用。"""


def win_to_wsl_path(path: str | Path) -> str:
    r"""`E:\a\b` → `/mnt/e/a/b`；已是 POSIX 路径则原样返回。"""
    p = str(path)
    if p.startswith("/"):
        return p
    ap = os.path.abspath(p)
    drive, rest = os.path.splitdrive(ap)
    if not drive:
        return ap.replace("\\", "/")
    return "/mnt/" + drive[0].lower() + rest.replace("\\", "/")


@dataclass(frozen=True)
class VertexBlock:
    r"""osgt 文本里一个 `vector N { … }` 块的位置。"""

    count: int
    start_line: int
    end_line: int


class OsgToolchain:
    r"""osgconv 封装。支持两种运行形态：

    1. Windows 本地已装 OSG → 直接调用（快）
    2. 本机无 OSG、WSL 内有 → 经 `wsl.exe` interop 调用（默认回退）

    构造时按 1→2 顺序探测；两种都不可用则抛 OsgToolchainMissing。
    """

    def __init__(self, osgconv: str | None = None, *,
                 distro: str = "Ubuntu", timeout: float = 1800.0,
                 prefer: str = "auto"):
        self.timeout = timeout
        self.distro = distro
        self._local: str | None = None
        self._wsl = False

        if osgconv:
            self._local = osgconv
            self._wsl = str(osgconv).startswith("/")
            return

        if prefer in ("auto", "local"):
            found = shutil.which("osgconv") or shutil.which("osgconv.exe")
            if found:
                self._local = found
                return

        if prefer in ("auto", "wsl"):
            # 在 WSL 里找（sh -lc 以便带上 PATH）
            p = subprocess.run(["wsl.exe", "-d", distro, "--", "sh", "-lc", "command -v osgconv"],
                               capture_output=True, text=True, timeout=120,
                               encoding="utf-8", errors="replace")
            path = (p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else ""
            if p.returncode == 0 and path.startswith("/"):
                self._local = path
                self._wsl = True
                return

        raise OsgToolchainMissing(
            "未找到 osgconv。二选一：\n"
            "  a) 在 WSL 安装：wsl -d Ubuntu -- sudo apt-get install -y openscenegraph\n"
            "  b) 在 Windows 安装 OSG 并确保 osgconv.exe 在 PATH 中"
        )

    # ---- 调用 ----

    @staticmethod
    def _to_backend_path(a: str) -> str:
        r"""把参数里的路径转成后端可解析的形式。

        - Windows 绝对/盘符路径（`E:\x`）→ `/mnt/e/x`
        - 已是 POSIX 路径（`/tmp/x` 或 `rg/in.osgb` 这类 WSL 本地相对路径）→ 原样
        - 非路径参数（如 `--mode`）→ 原样
        """
        if a.startswith("/"):
            return a
        if len(a) > 1 and a[1] == ":":
            return win_to_wsl_path(a)
        # 兜底：相对路径若含 Windows 反斜杠，转成 POSIX 分隔符
        # （否则 "rg\\in.osgb" 会被 osgconv 解析成 "rgin.osgb"）
        if "\\" in a:
            return a.replace("\\", "/")
        return a

    def _run(self, args: list[str]) -> str:
        if self._wsl:
            cmd = ["wsl.exe", "-d", self.distro, "--", str(self._local),
                   *[self._to_backend_path(a) for a in args]]
        else:
            cmd = [str(self._local), *args]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            msg = ((p.stderr or "") + (p.stdout or "")).strip()
            raise RuntimeError(f"osgconv 失败（{p.returncode}）：{msg[:400]}")
        return (p.stdout or "").strip()

    # ---- WSL 本地暂存（避开 DrvFs 的 9P 小 I/O 惩罚）----
    #
    # 实测（2026-09-30）：同一个 2.5 MB 瓦片
    #   从 /mnt/e 直接读 → 53.9 s；先拷到 WSL 本地 /tmp 再读 → 0.36 s（差 150 倍）
    # 原因：OSG 读取器在 DrvFs 挂载点上做大量小随机读，每次都要走 9P 协议。
    # 因此凡走 WSL 后端时，一律「先拷入本地 → 本地转换 → 再拷出」。

    def _wsl_local_stage(self, local_tmp: str | Path | None = None) -> "Path | None":
        """返回一个可用的 WSL 本地暂存目录的 Windows 句柄，或 None。"""
        if not self._wsl:
            return None
        base = Path(local_tmp) if local_tmp else Path(tempfile.mkdtemp(prefix="osgblocal_"))
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _wsl_temp_on_windows(self, stage: Path) -> str:
        """把 WSL 本地暂存目录映射成 Windows 可见路径（经 \\wsl.localhost）。"""
        return f"\\\\wsl.localhost\\{self.distro}" + str(stage).replace("/", "\\")

    def convert_staged(self, src: str | Path, dst: str | Path,
                       stage: str | Path, *, to_text: bool) -> Path:
        r"""在 WSL 本地暂存目录中完成转换，仅把结果写回 dst（输出侧一次拷贝）。

        src 从 dst 所在文件系统读入会走 DrvFs → 先拷进 stage（WSL 本地），
        转换在本地完成，最后把产物拷回 dst。可把单瓦片耗时从 ~54 s 降到 ~0.4 s。
        """
        import shutil as _sh
        stage = Path(stage)
        if stage.parent != Path("."):      # 允许传 "/tmp/xxx"
            stage.mkdir(parents=True, exist_ok=True)
        else:
            stage.mkdir(exist_ok=True)
        src, dst = Path(src), Path(dst)
        local_src = Path(stage.name) / ("in" + src.suffix)      # WSL 侧相对路径
        local_dst = Path(stage.name) / ("out" + dst.suffix)
        _sh.copyfile(src, local_src)              # Windows 侧拷贝，机制同普通文件
        self._run([str(local_src), str(local_dst)])
        if not local_dst.exists():
            raise RuntimeError(f"osgconv 未生成输出：{local_dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.copyfile(local_dst, dst)
        return dst

    def to_text(self, src: str | Path, dst: str | Path) -> Path:
        """二进制 → 文本（osgt）。顶点为十进制真实坐标。"""
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        self._run([str(src), str(dst)])
        if not Path(dst).exists():
            raise RuntimeError(f"osgconv 未生成输出文件：{dst}")
        return Path(dst)

    def to_binary(self, src: str | Path, dst: str | Path) -> Path:
        """文本 → 二进制（osgb）。"""
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        self._run([str(src), str(dst)])
        if not Path(dst).exists():
            raise RuntimeError(f"osgconv 未生成输出文件：{dst}")
        return Path(dst)

    def version(self) -> str:
        try:
            if self._wsl:
                p = subprocess.run(["wsl.exe", "-d", self.distro, "--", "sh", "-lc",
                                    "command -v osgversion >/dev/null && osgversion || true"],
                                   capture_output=True, text=True, timeout=60,
                                   encoding="utf-8", errors="replace")
                return (p.stdout or "").strip() or "unknown"
            exe = Path(str(self._local)).with_name("osgversion.exe" if os.name == "nt" else "osgversion")
            if exe.exists():
                return subprocess.run([str(exe)], capture_output=True, text=True,
                                      timeout=60).stdout.strip().splitlines()[0]
        except Exception:  # noqa: BLE001 —— 版本串仅为诊断信息
            pass
        return "unknown"

    @property
    def backend(self) -> str:
        return f"wsl:{self.distro}" if self._wsl else "local"


def iter_geo_vertices(text_path: str | Path):
    r"""流式扫描 osgt 文本，逐个产出 (VertexBlock, (K,3) ndarray)。

    只识别形如 `vector N {` 且其后恰好 N 行三元浮点的块（几何顶点/法线/纹理数组）。
    """
    import numpy as np

    with open(text_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    i, n = 0, len(lines)
    while i < n:
        m = _VECTOR_RE.match(lines[i])
        if not m:
            i += 1
            continue
        count = int(m.group(2))
        j = i + 1
        rows = []
        while j < n and len(rows) < count:
            mm = _FLOAT3_RE.match(lines[j])
            if not mm:
                break
            rows.append((float(mm.group(1)), float(mm.group(2)), float(mm.group(3))))
            j += 1
        if len(rows) == count and count > 0:
            yield VertexBlock(count=count, start_line=i, end_line=j), \
                np.asarray(rows, dtype=np.float64)
            i = j
        else:
            i += 1
