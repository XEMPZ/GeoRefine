#!/bin/bash
# 构建 osgxform —— OSGB 顶点仿射变换工具（内存态，与 OSGBLab 同层）
#
# 依赖：libopenscenegraph-dev
#   WSL:  sudo apt-get install -y libopenscenegraph-dev
# 用法：bash build.sh [输出路径，默认同目录 osgxform]
set -e
SRC="$(dirname "$0")/osgxform.cpp"
OUT="${1:-$(dirname "$0")/osgxform}"
g++ -O2 -pthread -o "$OUT" "$SRC" -losgDB -losg -losgUtil
echo "built: $OUT ($(stat -c %s "$OUT") bytes)"
