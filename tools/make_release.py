# -*- coding: utf-8 -*-
"""组装 GeoRefine 免安装版发布包。

产出：
    dist/GeoRefine-免安装版-v1.1/      —— 整目录（可直接运行）
    dist/GeoRefine-免安装版-v1.1.zip   —— 发布用压缩包

结构：
    GeoRefine.exe        双击运行（无需 Python）
    _internal/           Python 运行时与依赖
    src/                 完整源代码（供用户自行修改；BUG 可用任意 AI 工具修）
    docs/                技术手册、用户手册、截图
    models/              大地水准面格网（用户自备）
    params/              转换参数（用户自备）
    logs/                操作台账
    config.json          可选项

用法： python tools/make_release.py [--skip-build]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.1"
NAME = "GeoRefine-免安装版-v" + VERSION
DIST = ROOT / "dist"
BUILT = DIST / "GeoRefine"
OUT = DIST / NAME

SRC_ITEMS = ["app", "tests", "tools", "docs", "main.py", "run_tests.py",
             "requirements.txt", "GeoRefine.spec", "README.md",
             "ARCHITECTURE.md", "LICENSE", "LICENSE.txt"]


def run_build():
    """按 spec 重新打包。"""
    print("[1/5] PyInstaller 打包 ...")
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "GeoRefine.spec",
                        "--noconfirm", "--clean"], cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit("打包失败")


def assemble():
    print("[2/5] 组装发布目录 ...")
    if not BUILT.exists():
        raise SystemExit("未找到打包产物 %s，请先运行打包" % BUILT)
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(BUILT, OUT)

    src = OUT / "src"
    src.mkdir(exist_ok=True)
    for item in SRC_ITEMS:
        s = ROOT / item
        if not s.exists():
            print("    跳过（不存在）：" + item)
            continue
        d = src / item
        if s.is_dir():
            shutil.copytree(s, d, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".pytest_cache"))
        else:
            shutil.copy2(s, d)
    print("    src/ 已放入 " + str(len(SRC_ITEMS)) + " 项")

    docdir = OUT / "docs"
    if docdir.exists():
        shutil.rmtree(docdir)
    shutil.copytree(ROOT / "docs", docdir,
                    ignore=shutil.ignore_patterns("__pycache__"))

    for sub, note in [("models", "大地水准面格网（gtx）放这里"),
                      ("params", "转换参数（JSON）放这里"),
                      ("logs", "操作台账自动写入这里"),
                      ("native", "Windows 原生 OSGB 转换器（请勿删除）")]:
        d = OUT / sub
        d.mkdir(exist_ok=True)
        if sub != "native":      # native/ 放的是程序文件，不需要说明占位
            (d / "放这里.txt").write_text(note + "\n", encoding="utf-8")

    # Windows 原生转换器（全静态，无需 WSL/OSG）——OSGB 页会自动识别
    native = ROOT / "app" / "osgb" / "cpp" / "native" / "osgb_vertex_transform.exe"
    if native.exists():
        shutil.copy2(native, OUT / "native" / native.name)
        print("    内置原生转换器：%.1f MB" % (native.stat().st_size / 1048576))

    # 内置全球 EGM96 格网（4 MB），开箱即可算高程异常；
    # 中国区域高精度格网请自行放入 models/
    builtin = ROOT / "models" / "egm96_15.gtx"
    if builtin.exists():
        shutil.copy2(builtin, OUT / "models" / builtin.name)
        print("    内置格网：" + builtin.name)

    (OUT / "config.json").write_text(chr(123) + chr(10) +
                                     "  " + chr(34) + "enable_poly3d" + chr(34) + ": false" + chr(10) +
                                     chr(125) + chr(10), encoding="utf-8")
    (OUT / "使用说明.txt").write_text(USAGE_TEXT.replace("{V}", VERSION), encoding="utf-8")
    # 许可证也放一份在根目录，用户解压就能看到
    for lic in ("LICENSE", "LICENSE.txt"):
        if (ROOT / lic).exists():
            shutil.copy2(ROOT / lic, OUT / lic)



USAGE_TEXT = """GeoRefine v{V} 免安装版

1. 双击 GeoRefine.exe 运行，无需安装 Python。
2. 大地水准面格网（gtx）放到 models\\；转换参数放到 params\\。
3. 技术手册见 docs\\技术手册.md。

源码
----
src\\ 目录里是完整源代码，与 exe 内的逻辑一致。
遇到 BUG，可直接把 src\\ 交给任意 AI 编程工具修改。
验证改动：pip install -r src\\requirements.txt 然后 python src\\main.py
重新打包：按 src\\GeoRefine.spec 执行 pyinstaller。

OSGB 模型转换
-------------
该功能需要 OpenSceneGraph 工具链，本包未内置。首次使用时界面会提示构建命令：
    sudo apt-get install -y libopenscenegraph-dev        (WSL/Linux)
    bash app/osgb/cpp/build.sh /tmp/osgb_vertex_transform
未构建不影响其它功能。LAS/LAZ 点云转换无需任何外部工具。

许可证：MIT（见 LICENSE）。第三方组件许可见 LICENSE 末尾。
"""

def zipit():
    print("[3/5] 压缩 ...")
    zp = DIST / (NAME + ".zip")
    if zp.exists():
        zp.unlink()
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(DIST))
    return zp


def verify():
    print("[4/5] 校验 ...")
    must = ["GeoRefine.exe", "_internal", "src/main.py", "src/app",
            "docs/技术手册.md", "LICENSE", "使用说明.txt",
            "models", "params", "logs", "config.json",
            "src/app/osgb/cpp/osgb_vertex_transform.cpp",
            "native/osgb_vertex_transform.exe", "docs/BUILD_WINDOWS_OSG.md"]
    bad = [m for m in must if not (OUT / m).exists()]
    if bad:
        raise SystemExit("缺少：%s" % bad)
    print("    关键文件齐全")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller")
    a = ap.parse_args()
    if not a.skip_build:
        run_build()
    else:
        print("[1/5] 跳过打包")
    assemble()
    zp = zipit()
    verify()
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print("[5/5] 完成")
    print("    目录：%s" % OUT)
    print("    压缩包：%s  (%.1f MB)" % (zp, zp.stat().st_size / 1048576))
    print("    解压后：%.1f MB" % (size / 1048576))


if __name__ == "__main__":
    main()
