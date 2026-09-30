#!/bin/bash
# build_windows_native.sh —— 在 Windows 上用 MinGW-w64 构建完全静态的
#                            osgb_vertex_transform.exe（零 DLL 依赖、无需 WSL）。
#
# 为什么需要这个：交付给用户的免安装版不该要求装 WSL 或 OpenSceneGraph。
# 做法是把所需的 OSG 静态库一次性链进单个 exe（约 27 MB），
# 用户机器上不需要任何附加依赖，也不必配置 PATH。
#
# 依赖：zlib（压缩器）+ libjpeg-turbo（贴图重压），两者都从源码静态构建。
# 前置：MinGW-w64 (g++ >= 11, UCRT)、cmake、ninja、git、curl、tar、nasm
# 用法：bash build_windows_native.sh C:/osgbuild
# 产物：<WORK>/tool/osgb_vertex_transform.exe（约 27 MB，全静态）
set -euo pipefail

WORK="${1:-C:/osgbuild}"
MINGW="${MINGW:-/d/dev/mingw64/bin}"
export PATH="$MINGW:$PATH"
OSG_VER="OpenSceneGraph-3.6.5"
ZLIB_VER="1.3.1"
JPEG_VER="3.0.4"
SRC="$WORK/OpenSceneGraph"
ZLIB="$WORK/zlib"
JPEG="$WORK/libjpeg-turbo-$JPEG_VER"
LIB="$SRC/build_win/lib"
HERE="$(cd "$(dirname "$0")" && pwd)"

dl() {  # dl <url> <out>
  [ -s "$2" ] || curl -sSL --retry 3 --connect-timeout 20 --max-time 600 -o "$2" "$1"
}

echo "==== 1) 源码 ===="
mkdir -p "$WORK"
if [ ! -f "$ZLIB/zlib.h" ]; then
  dl "https://github.com/madler/zlib/archive/refs/tags/v$ZLIB_VER.tar.gz" "$WORK/zlib.tar.gz"
  tar -xzf "$WORK/zlib.tar.gz" -C "$WORK"; mv "$WORK/zlib-$ZLIB_VER" "$ZLIB"
fi
if [ ! -f "$JPEG/CMakeLists.txt" ]; then
  # 注意：SourceForge 的直链常 404，codeload 更稳（慢但可用）
  dl "https://codeload.github.com/libjpeg-turbo/libjpeg-turbo/tar.gz/refs/tags/$JPEG_VER" "$WORK/jpeg.tar.gz"
  tar -xzf "$WORK/jpeg.tar.gz" -C "$WORK"
fi
[ -f "$SRC/CMakeLists.txt" ] || git clone --depth 1 --branch "$OSG_VER" \
    https://github.com/openscenegraph/OpenSceneGraph.git "$SRC"

echo "==== 2) zlib（静态） ===="
mkdir -p "$ZLIB/build" && cd "$ZLIB/build"
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=gcc \
      -DBUILD_SHARED_LIBS=OFF -DZLIB_BUILD_EXAMPLES=OFF .. > /dev/null
ninja > /dev/null

echo "==== 3) libjpeg-turbo（静态，含 SIMD） ===="
mkdir -p "$JPEG/build" && cd "$JPEG/build"
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=gcc \
      -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DWITH_JPEG8=ON \
      -DWITH_TURBOJPEG=OFF -DWITH_SIMD=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5 .. > /dev/null
ninja > /dev/null

echo "==== 4) 配置 + 编译 OSG（全静态） ===="
echo "     --- 源码需带三处兼容补丁，见 docs/BUILD_WINDOWS_OSG.md ---"
mkdir -p "$SRC/build_win" && cd "$SRC/build_win"
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=g++ -DCMAKE_C_COMPILER=gcc \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_CXX_STANDARD=14 -DCMAKE_CXX_STANDARD_REQUIRED=ON \
  -DCMAKE_CXX_FLAGS="-w -DUSE_ZLIB -I$ZLIB -I$ZLIB/build -I$JPEG -I$JPEG/build" \
  -DCMAKE_C_FLAGS="-w -DUSE_ZLIB -I$ZLIB -I$ZLIB/build -I$JPEG -I$JPEG/build" \
  -DDYNAMIC_OPENTHREADS=OFF -DDYNAMIC_OPENSCENEGRAPH=OFF -DBUILD_SHARED_LIBS=OFF \
  -DZLIB_INCLUDE_DIR="$ZLIB" -DZLIB_LIBRARY="$ZLIB/build/libzlibstatic.a" \
  -DWITH_JPEG=ON -DJPEG_INCLUDE_DIR="$JPEG/build" -DJPEG_LIBRARY="$JPEG/build/libjpeg.a" \
  -DBUILD_OSG_APPLICATIONS=OFF -DBUILD_OSG_EXAMPLES=OFF -DBUILD_DOCUMENTATION=OFF \
  -DOSG_USE_UTF8_FILENAME=ON -DBUILD_OSG_DEPRECATED_SERIALIZERS=OFF \
  -DWITH_ZLIB=ON \
  -DWITH_PNG=OFF -DWITH_TIFF=OFF -DWITH_GDAL=OFF -DWITH_CURL=OFF \
  -DWITH_FREETYPE=OFF -DWITH_GLIB=OFF -DWITH_QT=OFF -DWITH_SDL=OFF -DWITH_XINE=OFF \
  -DWITH_OPENEXR=OFF -DWITH_GIFLIB=OFF -DWITH_JASPER=OFF -DWITH_LIBLAS=OFF \
  -DWITH_COLLADA=OFF -DWITH_3DS=OFF -DWITH_VTK=OFF -DWITH_POPINS=OFF \
  -DWITH_LIBVNCSERVER=OFF -DWITH_FFMPEG=OFF -DWITH_GSVG=OFF -DWITH_SVG=OFF .. > /dev/null
ninja

echo "==== 5) 编译转换器（全静态链接） ===="
OUT="$WORK/tool"; mkdir -p "$OUT"
# 关键三点：
#   -DOSG_LIBRARY_STATIC      否则按 dllimport 编译，报 __imp_ 未定义
#   libosgDB 必须进 --whole-archive  否则 Compressors.cpp.obj 被丢弃，
#                             运行时报 No such compressor zlib（输出体积翻倍）
#   --start-group/--end-group  解 osgUI/osgSim 与 osgUtil 的循环依赖
g++ -O2 -std=c++17 -w -DOSG_LIBRARY_STATIC -DUSE_ZLIB \
  -I"$SRC/include" -I"$SRC/build_win/include" -I"$ZLIB" -I"$ZLIB/build" -I"$JPEG" -I"$JPEG/build" \
  "$HERE/osgb_vertex_transform.cpp" -o "$OUT/osgb_vertex_transform.exe" \
  -static -static-libgcc -static-libstdc++ \
  -Wl,--allow-multiple-definition \
  -Wl,--whole-archive \
    "$LIB/libosgDB.a" \
    "$LIB/libosgdb_osg.a" "$LIB/libosgdb_osgtgz.a" "$LIB/libosgdb_ive.a" \
    "$LIB/libosgdb_ply.a" "$LIB/libosgdb_jpeg.a" "$LIB/libosgdb_gz.a" \
    "$LIB/libosgdb_serializers_osg.a" "$LIB/libosgdb_serializers_osgutil.a" \
    "$LIB/libosgdb_serializers_osgtext.a" "$LIB/libosgdb_serializers_osgga.a" \
    "$LIB/libosgdb_serializers_osganimation.a" "$LIB/libosgdb_serializers_osgfx.a" \
    "$LIB/libosgdb_serializers_osgmanipulator.a" "$LIB/libosgdb_serializers_osgparticle.a" \
    "$LIB/libosgdb_serializers_osgshadow.a" "$LIB/libosgdb_serializers_osgsim.a" \
    "$LIB/libosgdb_serializers_osgterrain.a" "$LIB/libosgdb_serializers_osgviewer.a" \
    "$LIB/libosgdb_serializers_osgvolume.a" \
  -Wl,--no-whole-archive \
  -Wl,--start-group \
    "$LIB/libosgUtil.a" "$LIB/libosgGA.a" "$LIB/libosgText.a" \
    "$LIB/libosgViewer.a" "$LIB/libosgAnimation.a" "$LIB/libosgFX.a" \
    "$LIB/libosgManipulator.a" "$LIB/libosgParticle.a" "$LIB/libosgUI.a" \
    "$LIB/libosgVolume.a" "$LIB/libosgShadow.a" "$LIB/libosgSim.a" \
    "$LIB/libosgTerrain.a" "$LIB/libosg.a" "$LIB/libOpenThreads.a" \
    "$ZLIB/build/libzlibstatic.a" "$JPEG/build/libjpeg.a" \
  -Wl,--end-group \
  -lws2_32 -lwinmm -lopengl32 -lglu32 -lgdi32 -luser32 -lkernel32

ls -la "$OUT/osgb_vertex_transform.exe"
echo "完成。把该 exe 放到 GeoRefine 的 native/ 目录即可被界面自动识别。"
