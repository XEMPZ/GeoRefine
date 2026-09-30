#!/bin/bash
# build_windows_native.sh —— 在 Windows 上用 MinGW-w64 构建完全静态的
#                            osgb_vertex_transform.exe（零 DLL 依赖、无需 WSL）。
#
# 为什么需要这个：交付给用户的免安装版不该要求装 WSL 或 OpenSceneGraph。
# OSGBLab 也是这么做的——它把 OpenSceneGraph 3.6.5 静态链接进自己的 exe
# （二进制里留有 OpenSceneGraph-3.6.5\src\... 的编译路径）。
#
# 前置：
#   MinGW-w64 (g++ >= 11, UCRT 版)  —— 本脚本实测 g++ 16.2.0
#   cmake, ninja, git, curl, tar
# 用法（Git Bash / MSYS / WSL 均可，注意路径改成本机的）：
#   bash build_windows_native.sh C:/osgbuild
#
# 产物： <WORK>/tool/osgb_vertex_transform.exe   （约 26 MB，全静态）
set -euo pipefail

WORK="${1:-C:/osgbuild}"
MINGW="${MINGW:-/d/dev/mingw64/bin}"          # 改成你的 MinGW bin 目录
export PATH="$MINGW:$PATH"
OSG_VER="OpenSceneGraph-3.6.5"
ZLIB_VER="1.3.1"
SRC="$WORK/OpenSceneGraph"
ZLIB="$WORK/zlib"
LIB="$SRC/build_win/lib"

echo "==== 1) 源码 ===="
mkdir -p "$WORK"
[ -f "$ZLIB/zlib.h" ] || { curl -sSL -o "$WORK/zlib.tar.gz" \
    "https://github.com/madler/zlib/archive/refs/tags/v$ZLIB_VER.tar.gz"; \
    tar -xzf "$WORK/zlib.tar.gz" -C "$WORK"; mv "$WORK/zlib-$ZLIB_VER" "$ZLIB"; }
[ -f "$SRC/CMakeLists.txt" ] || git clone --depth 1 --branch "$OSG_VER" \
    https://github.com/openscenegraph/OpenSceneGraph.git "$SRC"

echo "==== 2) zlib（静态） ===="
mkdir -p "$ZLIB/build" && cd "$ZLIB/build"
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=gcc \
      -DBUILD_SHARED_LIBS=OFF -DZLIB_BUILD_EXAMPLES=OFF .. > /dev/null
ninja > /dev/null

echo "==== 3) 给 OSG 打三个必要的兼容补丁 ===="
# (a) GCC 13+/C++17 的 std::byte 与 Windows rpcndr.h/wtypes.h 的 byte 冲突
#     —— 用 C++14 编译（下面 cmake 参数里 -DCMAKE_CXX_STANDARD=14）
# (b) MinGW(UCRT) 下 struct stat64 与 _wstat64 期望的 _stat64 不同型
#     —— 见 src/osgDB/FileUtils.cpp 里两处 [GeoRefine 修补]
# (c) osgViewer/GraphicsWindowWin32.cpp: ChangeDisplaySettingsEx 返回 LONG，
#     DISP_CHANGE_* 含负值，存入 unsigned int 触发 -Wnarrowing 错误
#     —— 改为 LONG（同文件 [GeoRefine 修补]）
echo "    （补丁已随源码提交；若用纯净上游源码请手工套用 docs/BUILD_WINDOWS_OSG.md 中的 diff）"

echo "==== 4) 配置 + 编译 OSG（最小依赖，全静态） ===="
mkdir -p "$SRC/build_win" && cd "$SRC/build_win"
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=g++ -DCMAKE_C_COMPILER=gcc \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_CXX_STANDARD=14 -DCMAKE_CXX_STANDARD_REQUIRED=ON \
  -DCMAKE_CXX_FLAGS="-w -I$ZLIB -I$ZLIB/build" -DCMAKE_C_FLAGS="-w -I$ZLIB -I$ZLIB/build" \
  -DDYNAMIC_OPENTHREADS=OFF -DDYNAMIC_OPENSCENEGRAPH=OFF -DBUILD_SHARED_LIBS=OFF \
  -DZLIB_INCLUDE_DIR="$ZLIB" -DZLIB_LIBRARY="$ZLIB/build/libzlibstatic.a" \
  -DBUILD_OSG_APPLICATIONS=OFF -DBUILD_OSG_EXAMPLES=OFF -DBUILD_DOCUMENTATION=OFF \
  -DOSG_USE_UTF8_FILENAME=ON -DBUILD_OSG_DEPRECATED_SERIALIZERS=OFF \
  -DWITH_ZLIB=ON \
  -DWITH_PNG=OFF -DWITH_JPEG=OFF -DWITH_TIFF=OFF -DWITH_GDAL=OFF -DWITH_CURL=OFF \
  -DWITH_FREETYPE=OFF -DWITH_GLIB=OFF -DWITH_QT=OFF -DWITH_SDL=OFF -DWITH_XINE=OFF \
  -DWITH_OPENEXR=OFF -DWITH_GIFLIB=OFF -DWITH_JASPER=OFF -DWITH_LIBLAS=OFF \
  -DWITH_COLLADA=OFF -DWITH_3DS=OFF -DWITH_VTK=OFF -DWITH_POPINS=OFF \
  -DWITH_LIBVNCSERVER=OFF -DWITH_FFMPEG=OFF -DWITH_GSVG=OFF -DWITH_SVG=OFF .. > /dev/null
ninja

echo "==== 5) 编译转换器（全静态链接） ===="
OUT="$WORK/tool"; mkdir -p "$OUT"
HERE="$(cd "$(dirname "$0")" && pwd)"
g++ -O2 -std=c++17 -w -DOSG_LIBRARY_STATIC \
  -I"$SRC/include" -I"$SRC/build_win/include" -I"$ZLIB" -I"$ZLIB/build" \
  "$HERE/osgb_vertex_transform.cpp" -o "$OUT/osgb_vertex_transform.exe" \
  -static -static-libgcc -static-libstdc++ \
  -Wl,--whole-archive \
    "$LIB/libosgdb_osg.a" "$LIB/libosgdb_osgtgz.a" "$LIB/libosgdb_ive.a" \
    "$LIB/libosgdb_ply.a" \
    "$LIB/libosgdb_serializers_osg.a" "$LIB/libosgdb_serializers_osgutil.a" \
    "$LIB/libosgdb_serializers_osgtext.a" "$LIB/libosgdb_serializers_osgga.a" \
    "$LIB/libosgdb_serializers_osganimation.a" "$LIB/libosgdb_serializers_osgfx.a" \
    "$LIB/libosgdb_serializers_osgmanipulator.a" "$LIB/libosgdb_serializers_osgparticle.a" \
    "$LIB/libosgdb_serializers_osgshadow.a" "$LIB/libosgdb_serializers_osgsim.a" \
    "$LIB/libosgdb_serializers_osgterrain.a" "$LIB/libosgdb_serializers_osgviewer.a" \
    "$LIB/libosgdb_serializers_osgvolume.a" \
  -Wl,--no-whole-archive \
  -Wl,--start-group \
    "$LIB/libosgDB.a" "$LIB/libosgUtil.a" "$LIB/libosgGA.a" "$LIB/libosgText.a" \
    "$LIB/libosgViewer.a" "$LIB/libosgAnimation.a" "$LIB/libosgFX.a" \
    "$LIB/libosgManipulator.a" "$LIB/libosgParticle.a" "$LIB/libosgUI.a" \
    "$LIB/libosgVolume.a" "$LIB/libosgShadow.a" "$LIB/libosgSim.a" \
    "$LIB/libosgTerrain.a" "$LIB/libosg.a" "$LIB/libOpenThreads.a" \
    "$ZLIB/build/libzlibstatic.a" \
  -Wl,--end-group \
  -lws2_32 -lwinmm -lopengl32 -lglu32 -lgdi32 -luser32 -lkernel32

ls -la "$OUT/osgb_vertex_transform.exe"
echo "完成。把该 exe 放到 GeoRefine 的 native/ 目录即可被界面自动识别。"
