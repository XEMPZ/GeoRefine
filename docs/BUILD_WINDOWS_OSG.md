# 在 Windows 上构建 OSGB 转换器（零依赖）

免安装版自带的 `native/osgb_vertex_transform.exe` 是**完全静态链接**的原生 Windows
程序（约 25 MB，只依赖 Windows 系统 DLL），用户机器上**不需要 WSL、不需要 OpenSceneGraph**。

这与 OSGBLab 的做法一致——它同样把 OpenSceneGraph 3.6.5 静态链接进自己的 exe：

```
OSGBLab.exe 内的编译路径字符串：
  OpenSceneGraph-OpenSceneGraph-3.6.5\src\osg\Uniform.cpp
  OpenSceneGraph-OpenSceneGraph-3.6.5\src\osgPlugins\ply\plyfile.cpp
```

本文记录从零复现该构建的完整过程，含必须打的三处兼容补丁。

## 一、前置条件

| 组件 | 版本（实测） | 说明 |
|---|---|---|
| MinGW-w64 | g++ 16.2.0（UCRT） | 必须是 UCRT 版；MSVCRT 版行为不同 |
| CMake | 4.4.2 | 需 `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` 兼容老工程 |
| Ninja | 1.13.2 | 构建器 |
| zlib | 1.3.1（源码） | 唯一外部依赖 |
| OpenSceneGraph | 3.6.5（源码） | 与 OSGBLab 同版本 |

**不需要** libpng / libjpeg / freetype / curl / Qt / GLib —— `.osgb` 是 OSG 的
核心二进制格式，不依赖这些。首次运行时可能看到
`readImage(): Unable to find a plugin for jpg` 警告，那是因为原始瓦片内嵌了 JPEG 贴图，
工具只改顶点、不碰贴图，该警告不影响结果。

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

性能（10 瓦片 / 212,266 顶点 / 8 线程）：**Windows 原生 0.73 s，WSL 版 0.47 s**，
顶点数与块数完全一致。中文路径（`E:\Agent测试\...`）正常工作。
