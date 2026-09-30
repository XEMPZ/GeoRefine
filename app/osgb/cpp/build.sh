#!/bin/bash
# build.sh —— 构建 OSGB 顶点变换工具（C++，内存态，与 OSGBLab 同层）
#
# 为什么用 C++：OSGB 转换的性能瓶颈在"解析 + 序列化"本身。走文本中间层
# （.osgt）要多两次序列化、慢 3~4 倍；本工具用 OSG 的 C++ API 直接在内存里
# 读场景图 → 改 Vec3Array → 写回，全模型 1537 瓦片约 23 秒（16 线程）。
#
# 依赖：libopenscenegraph-dev
#   Debian/Ubuntu:  sudo apt-get install -y libopenscenegraph-dev
#   Windows:        经 WSL 调用（本脚本在 WSL 内执行）
#
# 用法：bash build.sh [输出路径，默认同目录 osgb_vertex_transform]
set -e
SRC="$(dirname "$0")/osgb_vertex_transform.cpp"
OUT="${1:-$(dirname "$0")/osgb_vertex_transform}"
g++ -O2 -std=c++17 -pthread -o "$OUT" "$SRC" -losgDB -losg -losgUtil
echo "built: $OUT ($(stat -c %s "$OUT") bytes)"
