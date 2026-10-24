# -*- coding: utf-8 -*-
"""组装「开发交接包」：把接手开发（或交给别的 AI 工具）需要的东西打成一个 zip。

产出：dist/GeoRefine-开发交接包-v{VERSION}.zip

包含：源码(app/)、测试(tests/)、工具(tools/)、文档(docs/含截图)、样例数据(sample_data/)、
     自定义椭球(ellipsoids/)、打包配置(GeoRefine.spec)、依赖清单、README/ARCHITECTURE/读我先。
不含：models/（格网太大，附下载脚本）、params/logs（用户数据）、dist/build/.git、编译好的 exe。

用法： python tools/make_devkit.py
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2"
DIST = ROOT / "dist"
OUT = DIST / f"GeoRefine-开发交接包-v{VERSION}.zip"

INCLUDE_DIRS = ["app", "tests", "tools", "docs", "sample_data", "ellipsoids"]
INCLUDE_FILES = ["main.py", "run_tests.py", "requirements.txt", "requirements-lock.txt",
                 "GeoRefine.spec", "README.md", "ARCHITECTURE.md", "LICENSE",
                 "THIRD_PARTY_LICENSES.md", ".gitignore", "读我先.md"]
SKIP_PATTERNS = ["__pycache__", "*.pyc", "*.pyo", ".pytest_cache", "*.exe", "*.pdb", "*.ilk"]
SKIP_DIRS = {"build", "dist", ".git", "node_modules"}


def _skip(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in SKIP_PATTERNS)


def collect() -> list[Path]:
    files: list[Path] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            print(f"    跳过（不存在）：{d}")
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or _skip(p.name):
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
                continue
            files.append(p)
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.exists() and not _skip(p.name):
            files.append(p)
    return files


def write_lock() -> None:
    """把当前环境的依赖版本固定下来，方便复现。"""
    try:
        out = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True)
        lines = [ln for ln in out.stdout.splitlines()
                 if not ln.startswith(("#", "-e ", "pyinstaller", "PyInstaller"))]
        (ROOT / "requirements-lock.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[ok] requirements-lock.txt（{len(lines)} 行）")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 生成 requirements-lock.txt 失败：{e}")


def main() -> int:
    if not (ROOT / "读我先.md").exists():
        print("[x] 缺少 读我先.md")
        return 1
    write_lock()
    files = collect()
    size = sum(p.stat().st_size for p in files)
    DIST.mkdir(exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            z.write(p, p.relative_to(ROOT))
    print(f"[ok] {OUT.name}  {OUT.stat().st_size / 1048576:.1f} MB（{len(files)} 个文件，原始 {size / 1048576:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
