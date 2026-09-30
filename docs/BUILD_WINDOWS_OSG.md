# 在 Windows 上构建 OSGB 转换器（零依赖）

免安装版自带的 `native/osgb_vertex_transform.exe` 是**完全静态链接**的原生 Windows
程序（约 25 MB，只依赖 Windows 系统 DLL），用户机器上**不需要 WSL、不需要 OpenSceneGraph**。

**为什么选静态链接**：OSG 在 Windows 上默认按动态库分发，需要随包提供十几个 DLL
并保证加载路径正确。本工具只用到 `osgDB` 的读写能力与少数插件，把所需的 OSG 静态库
一次性链进单个 exe（约 27 MB）后，用户机器上不需要任何附加依赖，也不需要配置 PATH。

本文记录从零完成该构建的完整过程，含必须处理的四处兼容问题。

## 一、前置条件

| 组件 | 版本（实测） | 说明 |
|---|---|---|
| MinGW-w64 | g++ 16.2.0（UCRT） | 必须是 UCRT 版；MSVCRT 版行为不同 |
| CMake | 4.4.2 | 需 `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` 兼容老工程 |
| Ninja | 1.13.2 | 构建器 |
| zlib | 1.3.1（源码） | 压缩器（`Compressor=zlib`），体积关键 |
| libjpeg-turbo | 3.0.4（源码） | 内嵌 JPEG 贴图的解码/重压 |
| nasm | 已随 MinGW | libjpeg-turbo 的 SIMD 需要 |
| OpenSceneGraph | 3.6.5（源码） | 稳定版；本项目只用其 osgDB 读写能力 |

**不需要** libpng / freetype / curl / Qt / GLib —— `.osgb` 是 OSG 的核心二进制格式，
不依赖这些。`.osgb` 里的贴图通常就是 **JPEG**，所以要加 libjpeg-turbo：没有它时每读一个
瓦片都会报 `readImage(): Unable to find a plugin for jpg`（几何结果仍然正确，只是贴图不重压）。

## 二、一键构建

```bash
bash app/osgb/cpp/build_windows_native.sh C:/osgbuild
```

脚本会依次：下载 zlib 与 OSG 源码 → 编 zlib 静态库 → 配置 OSG（最小依赖 + 全静态）
→ 编译 → 全静态链接出 `C:/osgbuild/tool/osgb_vertex_transform.exe`。

把该 exe 放到 `app/osgb/cpp/native/`，界面即自动识别（`_find_binary` 优先查该位置）。

## 三、构建过程中必须处理的三个坑

### 坑 1：中文路径会让 windres 失败

`E:\Agent测试\...` 这类路径下，zlib 的 `zlib1rc.obj` 生成会报
`No such file or directory`（windres 对非 ASCII 路径处理有缺陷）。
**解法：构建目录用纯 ASCII 路径**（脚本默认 `C:/osgbuild`）。
注意：构建路径与**运行时路径**是两回事——运行时已支持中文路径（见坑 2）。

### 坑 2：`std::byte` 与 Windows 头文件冲突（GCC 13+）

```
D:/dev/mingw64/.../include/rpcndr.h:94:11: error: reference to byte is ambiguous
```

C++17 引入 `std::byte`，而 Windows 的 `rpcndr.h`/`wtypes.h`/`objidl.h` 里有全局 `byte`，
在 `using namespace std` 下产生歧义。OSG 3.6.5 是 2020 年的代码。
**解法：用 C++14 编译**（`-DCMAKE_CXX_STANDARD=14`）。

### 坑 3：MinGW(UCRT) 的 `stat64` 与 `_wstat64` 类型不匹配

启用 `OSG_USE_UTF8_FILENAME=ON`（中文路径必需）后：

```
src/osgDB/FileUtils.cpp:163:59: error: cannot convert stat64* to _stat64*
```

MinGW 下 `struct stat64` 与 `_wstat64` 期望的 `struct _stat64` 是不同类型。
**解法**：`FileUtils.cpp` 里两处（`makeDirectory()` 与 `fileExists()`）改为条件声明：

```cpp
#if defined(_WIN32) && defined(OSG_USE_UTF8_FILENAME)
    struct _stat64 stbuf;
#else
    struct stat64 stbuf;
#endif
```

### 附带一处：osgViewer 的窄化转换

```
src/osgViewer/GraphicsWindowWin32.cpp:1115: error: narrowing conversion of -2 from int to unsigned int
```

`ChangeDisplaySettingsEx` 返回 `LONG`，而 `DISP_CHANGE_BADMODE`(-2)/`DISP_CHANGE_FAILED`(-1)
是含负值的常量；存进 `unsigned int` 后用于 `switch` 会触发 `-Wnarrowing`（GCC 下是错误）。
**解法：把 `unsigned int result` 改为 `LONG result`**。

### 坑 4：`No such compressor zlib` —— 输出体积翻倍

两个独立原因，都踩过：

**(a) `USE_ZLIB` 宏没传到 osgDB。** `src/osgDB/CMakeLists.txt:162` 里：

```cmake
IF( ZLIB_FOUND )
    ADD_DEFINITIONS( -DUSE_ZLIB )
```

手工指定 `-DZLIB_LIBRARY` 时 `FindZLIB` 可能不置位 `ZLIB_FOUND`，宏就丢了。
**解法：在 `CMAKE_CXX_FLAGS` 里显式加 `-DUSE_ZLIB`。**

**(b) `libosgDB.a` 不在 `--whole-archive` 里，压缩器被链接器丢弃。**
`Compressors.cpp.obj` 里的 `REGISTER_COMPRESSOR("zlib", ...)` 靠静态初始化注册，
但如果没有任何代码引用该目标文件里的符号，归档成员就不会被提取。

排查手法（比推断可靠）：

```bash
nm --defined-only libosgDB.a | grep ZLibCompressor     # 有
nm osgb_vertex_transform.exe  | grep ZLibCompressor     # 没有 -> 就是被丢了
```

**解法：把 `libosgDB.a` 也放进 `--whole-archive`。**

两个都修好后，输出体积从 1.32× 回到 **0.87×**，且读取耗时从 3026 ms 降到 113 ms。

## 四、必须保留完整模块与序列化器

容易踩的误区：以为"只改顶点"就可以只编 `osg` + `osgDB`。**不行。**

.osgb 序列化的是**整个场景图**——根 `osg::Group`、`PagedLOD`、`MatrixTransform`、
`StateSet`、`Geometry`… 缺任何一个读写器，读取时就会报：

```
InputStream::readObject(): Unsupported wrapper class osg::Group
```

因此 `src/CMakeLists.txt` 与 `src/osgWrappers/serializers/CMakeLists.txt` 的模块列表
**保持完整**，链接时把 14 个 `libosgdb_serializers_*.a` 全部以 `--whole-archive` 纳入
（静态库的自动注册依赖这一点）。

## 五、链接要点

```bash
g++ -O2 -std=c++17 -DOSG_LIBRARY_STATIC ... \
  -static -static-libgcc -static-libstdc++ \
  -Wl,--whole-archive <插件与序列化器> -Wl,--no-whole-archive \
  -Wl,--start-group <osg 各模块 + zlib> -Wl,--end-group \
  -lws2_32 -lwinmm -lopengl32 -lglu32 -lgdi32 -luser32 -lkernel32
```

| 要点 | 原因 |
|---|---|
| `-DOSG_LIBRARY_STATIC` | 缺它会按 `dllimport` 编译，报一堆 `undefined reference to __imp_...` |
| `-Wl,--whole-archive` | 静态库的格式读写器靠静态初始化注册，不加会被链接器丢弃 |
| `-Wl,--start-group/--end-group` | osgUI/osgSim 与 osgUtil 之间存在循环依赖 |
| `-static-libgcc -static-libstdc++` | 否则运行时报缺少 `libgcc_s_*.dll` / `libstdc++-6.dll` |
| 排除 `libosgdb_serializers_osgui.a` | 它与 `serializers_osgga` 重复定义 `Widget` 包装器 |
| 排除 `libosgdb_zip.a` | 其内置 minizip 与 zlib 的 `inflate_copyright` 重复定义 |

## 六、验证结果（实测）

取真实模型的 10 个瓦片（`E:\GIS model\20260914梁场`）做往返验证：

```
正变换 (北+100, 东-7, 高+1.5)  ->  ok=6  fail=0  verts=133266
反向平移 (北-100, 东+7, 高-1.5) ->  ok=6  fail=0  verts=133266

原始模型      局部X 253.4780~817.2950   Y -386.7560~366.6120
往返输出      局部X 253.4780~817.2950   Y -386.7560~366.6120   <- 逐位相同
Windows 直出  局部X 246.4780~810.2950   Y -286.7560~466.6120   <- 正是 -7 / +100

原始 vs 往返 最大差异 1.0000 mm（.osgb 顶点量化步长，非工具误差）
```

性能（12 瓦片 / 378,121 顶点 / 8 线程）：**0.25 s**，输出 **0.87×**，
stderr **完全干净**（无任何插件警告）。中文路径（`E:\Agent测试\...`）正常工作。

| 构建阶段 | 体积倍率 | stderr |
|---|---|---|
| 最小集（无压缩器、无 jpeg） | 0.82×（zlib 生效时） | jpg 插件警告 |
| 加了 jpeg，但丢了 zlib 压缩器 | **1.32×** | `No such compressor zlib` |
| **最终（jpeg + zlib 压缩器）** | **0.87×** | **干净** |
