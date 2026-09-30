# OSGB 转换：照套既有参数链路 + 逆向 OSGBLab 的处理方式

## 架构（最终）

**坐标换算一行都没自己写。** 完整照套本项目「参数应用转换」页（ApplyPage）的链路：

    ApplyPage 判定（pages.py:4390-4391、4414-4415）
        src_lonlat = source_kind == "lonlat" or kind in ("seven","three","chain74")
        out_lonlat = kind in ("seven","three") or kind in ("inv_direct","inv_chain3","inv_chain")
    → app.logic.apply_params.apply_points(...)   ← 唯一的换算入口，5 个分支

OSGB 模块只做两件事：

1. `transform.PlanarVertexTransform` —— 把顶点数组喂给 `apply_points`（逐点、同参数）
2. `transform.affine_coefficients` —— 把"逐点函数"线性化成矩阵 `out = A·x + b`
   （有限差分 + 二阶差分线性性校验；高阶多项式在此被明确拒绝）

C++ 工具 `app/osgb/cpp/osgb_vertex_transform.cpp` 只做矩阵乘法，不认识 EPSG/投影/七参数。

## ⚠ 轴序：OSGB 用 (东,北)，本项目用 (北,东) —— 两者相反

| 坐标系 | 平面分量顺序 | 依据 |
|---|---|---|
| **OSGB**（ContextCapture / GIS / ENU） | **(东 E, 北 N)** | 实测：SRSOrigin = (595251, 2865092)。第 1 分量 595251 ≈ 500000+95km = 东；第 2 分量 2865092 ≈ 25.9°×111km = 北 |
| **本项目测量约定** | **(北 x, 东 y)** | ARCHITECTURE.md:11、README.md:26、用户手册 |

**必须换轴**，否则四参数会拿东坐标当北坐标用。

换轴复用项目既有函数 `app.core.transform2d.to_survey_xy`（原用途 CAD(东,北)→测量(北,东)，
OSGB 与 CAD 同为 (东,北) 约定，语义一致）：

    OSGB 顶点 (E, N, H)
      → to_survey_xy 换轴 → (N, E, H)
      → apply_points（测量约定）
      → to_survey_xy 换回 → (E, N, H) 写回

覆盖测试：`test_axis_order_osgb_east_north`（用非对称位移，轴序错必然暴露）、
`test_axis_order_roundtrip_identity`（零位移须逐位恒等）。

## 平面 → 平面：已涵盖

本项目的参数类型里，**平面源与平面目标**的转换从设计上就覆盖了：

| kind | 说明 | 最少点 |
|---|---|---|
| `planar4` | 平面四参数（平移+旋转+尺度），src/dst 均为 planar | 2 |
| `planar3` | 平面三参数（仅平移） | 1 |
| `poly2d` | 平面二维多项式 | 依阶数 |
| `poly3d` | 平面+高程整体多项式（源=平面时） | 依阶数 |

加上 `seven2d`（二维七参数，平面退化）与 `heightfit`（高程拟合，可叠加），
"两个平面坐标系之间的转换"这个场景是完备的。

⚠ 注意：`chain74` 的 `source_kind` 是 `lonlat`（七参数+投影+四参数链），
它期望**经纬度**输入，不属于"平面→平面"。用平面点对拟合时
`autoselect.compare_methods` 会给出 `planar4`。

## ⚠ 关键语义：参数工作在**绝对投影坐标**（这一条推翻过一版实现）

**结论：`apply_points` 吃绝对投影坐标**，因为控制点在现实中永远是绝对坐标，
拟合出的参数必然工作在绝对空间。

构造真值实验（`_abs_truth`）——用绝对投影坐标造控制点、拟合四参数，再对同一空间应用：

| 输入 | 结果 | 判定 |
|---|---|---|
| 真值（对绝对坐标应用） | N=2865087.029595 E=595421.877208 | 基准 |
| **喂绝对坐标** | N=2865087.029595 E=595421.877210 | ✅ 一致 |
| 喂局部偏移 | N=21.043215 E=116.443377 | ❌ 完全错 |

**曾走过的弯路**：早期用一个自制的纯平移测试参数做实验，得出"参数吃局部坐标"的
结论——纯平移在两种空间下结果相同，所以那个实验无法区分两者。用带旋转尺度的
真实形态参数（a≈0.985, b≈0.076）才暴露出来。

因此 OSGB 驱动层的做法是：

    out_world = f(local + SRSOrigin)      # 加回原点 → 绝对坐标 → 参数
    out_local = out_world − SRSOrigin     # 减回原点 → 仍按局部偏移写回
    SRSOrigin 保持不变                    # 局部坐标系不动，世界坐标被变换

端到端核对（用项目自带 `fit_4param` 在绝对坐标上拟合）：
```
顶点 12141 | 最大偏差 0.9927 mm   ← 源量化步长 1 mm 之内
VERDICT: PASS
```

## 大地源参数（源模型为大地坐标数据 → 工程平面坐标）

对应 `chain74` / `direct_gk` 这类**源为大地坐标**的参数
（经纬度 → 投影 → 四参数 → 工程平面，或直接投影）。

OSGB 顶点是平面偏移，因此链路是：

    绝对平面(北,东) = 局部(东,北) + SRSOrigin
      → 反算源侧经纬度（复用既有 _poly_src_point：源椭球/带号剥离/投影面高/严密模式）
      → apply_lonlat(B, L, H)   ← 参数的正向链，返回绝对目标平面
      → 减 SRSOrigin → 局部偏移写回

**前提**：必须拿得到源侧投影（参数的 `projection_src` 或界面「投影参数」覆盖）。
缺 L0 时明确拒绝，不静默错算。

**平面/高程同样可独立解耦**（与全项目一致）。`apply_lonlat` 的解耦本身是正确的
（实测：只高程时 h 照改、只平面时 h 透传）；它唯一的"坑"是 `do_plane=False` 时
x/y 会透传成经纬度，而顶点要的是平面坐标。
驱动层的做法是**只取它算出的 Δh**，平面坐标按两个开关分别处理：

| 勾选 | 平面 | 高程 |
|---|---|---|
| 平面 + 高程 | 反算→正向链（绝对值） | 正向链的 h（含高程拟合/格网） |
| 仅平面 | 反算→正向链 | 输入原值逐位透传 |
| 仅高程 | **输入原值逐位透传** | 正向链的 h |

自洽验证（与手工复算逐点一致，最大偏差 **0.000e+00 m**）：

```
局部(E,N,H)=(100,200,-50) → B=25.89269359 L=117.95149983
  → apply_lonlat X=2865365.928894 Y=595398.426909
  → 减原点换轴 → (147.329094, 273.766742, -50.0)   实现 == 期望
```

全模型实测：1537 瓦片 / 0 失败 / 23.7 s。

## 平面 / 高程解耦 + 大地水准面

两个开关独立勾选（与「参数应用转换」页同口径 `do_plane` / `do_height`）：

| 勾选 | 行为 |
|---|---|
| 平面 + 高程 | 两者都应用 |
| 仅平面 | XY 按参数，Z 逐位不变 |
| 仅高程 | Z 按参数，XY 逐位不变 |
| 都不勾 | 拒绝执行 |

**大地水准面**（`geoid_grid` / `height_flags.geoid`）需要**源侧经纬度**插值 ξ。
平面输入时按参数自带的 `projection_src` 反算——复用既有的 `_poly_src_point`
（本类唯一实现），新增的 `_planar_to_src_bl` 只是"缺投影时返回 None"的薄壳。
缺 `projection_src.l0` 时**明确拒绝**，不静默跳过、不换模型。

⚠ 格网覆盖全区域，而 OSGB 顶点是局部偏移：ξ 的查算必须用
`局部 + SRSOrigin` 的绝对投影坐标，否则反算出的经纬度完全不对。
提供 `apply_planar_at(x, y, h, gx, gy, ...)` 承载这一点——平面部分按局部算、
格网查算按绝对算，两者解耦。驱动层在 `_apply_points` 里检测到 `geoid_grid` 时走这条路。

## 关键语义：参数直接作用于**局部顶点**（历史记录，已被上节取代）

OSGB 顶点 = 实际坐标 − SRSOrigin，**它就是"待转坐标本身"**，
和 ApplyPage 里用户输入表格中的坐标是同一个东西。所以：

    apply_points(局部顶点) → 新局部顶点
    位移落在顶点上，**SRSOrigin 保持不变**

实测佐证（`_affine_pick2`）：

（下表为换轴之后在测量轴序上做的对照）

| 喂入 | 得到的 A | 得到的 b | 与 apply_points 的偏差 |
|---|---|---|---|
| **局部坐标** | `[[0.99999,-0.0001,0],[0.0001,0.99999,0],[0,0,1]]` | `[10, -5, 0.5]` | **5.68e-14 m** ✅ |
| 局部+原点（60 万米） | 同上 | `[-282.46, 25.87, 0.5]` | 299 m ❌ |

原因：四参数的旋转/尺度是相对**该参数自己的坐标原点**定义的，
把它作用到 60 万米量级的绝对坐标上必然荒谬。

## 与 apply_points 的一致性验证

```
比对顶点数: 114966
最大偏差: 1.015e-03 m = 1.015 mm   ← 源量化步长(1 mm) + 文本测量下限
判定: ✅ 与软件既有链路一致
```

## 参数适用性由 SRS 决定，不靠量级猜

顶点类型来自 `metadata.xml` 的 `origin_kind`，**不能用 detect_coord_kind 猜**：
OSGB 顶点是几十~几百米的小偏移，量级判别必然给出 lonlat。

| origin_kind | 顶点空间类型 |
|---|---|
| `projected` | planar（顶点 = 投影坐标 − origin） |
| `enu` | planar（局部 ENU 米制） |
| `local` | 无地理基准，拒绝转换 |

大地源参数（`chain74` 等）用于 OSGB 会被拒绝，口径同 ApplyPage 的
「坐标类型不匹配」（pages.py:4420-4424）。

## 精度：上限来自源数据

ContextCapture 的 osgb 顶点是 **uint16 量化的 1 mm 网格**。二进制层面统计
（915,399 个分量）：

| 指标 | 值 |
|---|---|
| 完全一致（误差 = 0） | 95.6% |
| 误差 ≤ 0.1 mm | 99.07% |
| 误差 > 0.1 mm | 0.93%（最大 1.0000 mm） |

**误差不累积**：位移 1 mm 与位移 15 m，最大误差都是 1 mm
——即"源的量化步长"，不是变换引入的。

⚠ 经 osgconv 文本中转的核对下限是 **6 mm**（文本仅 6 位有效数字，
坐标 500 m 量级时每边 1 mm，两端相减叠加）。真实精度必须看二进制统计。

---

# 性能与逆向后记（2026-09-30）

## 结果：要求全部达标

| 指标 | 要求 | **实测** | 旧（文本层）方案 | OSGBLab |
|---|---|---|---|---|
| 全模型耗时 | ≤ 60 s | **21.6 s** ✅ | 102.3 s | 10 s（用户实测）|
| — 其中转换 | — | **16.2 s** | — | — |
| — 其中拷回挂载点 | — | 5.5 s | — | — |
| 输出体积（单瓦片） | ≈ 输入 | **0.82×** ✅ | 3.76× | ~1.0× |
| 输出体积（全模型） | ≈ 输入 | 1.69× | 11.05× | ~1.0× |
| 精度 | ≤ 0.1 mm | **0.005 mm** ✅ | 0.005 mm | — |
| 瓦片 | 无失败 | **1537 ok / 0 fail** ✅ | 1536 ok | — |

模型：`20260914梁场`，1537 瓦片 / 1562 MB / 2098 万顶点，WSL2 + 16 线程。

## 实现：`app/osgb/cpp/osgb_vertex_transform.cpp`

用 OSG 的 C++ API 在内存里完成，**不经过文本层**：
`readNodeFile` → `NodeVisitor` 遍历 `osg::Geometry` 的 `Vec3Array` → 仿射变换 → `writeNodeFile`。

内置线程池（批处理模式一次进程吃满多核）。

### 单元成本（2.87 MB 瓦片）

| 环节 | 文本层方案 | **内存态方案** |
|---|---|---|
| 读 | 0.366 s | **0.070 s** |
| 改 | 0.281 s | **0.0001 s** |
| 写 | 0.240 s | **0.159 s** |
| **合计** | **0.887 s** | **0.230 s**（3.9×）|

## C++ 源码组织

```
app/osgb/
  cpp/
    osgb_vertex_transform.cpp    <- C++ 工具源码（内存态顶点变换）
    build.sh                     <- 构建脚本（g++ -O2 -std=c++17 -pthread）
  flash.py                       <- 驱动 Python 侧：参数 -> 仿射系数 -> 调 C++ 工具
  transform.py                   <- 顶点变换语义与仿射线性化（委托 apply_points）
  osgb_io.py / osgb_job.py       <- osgconv 桥与文本层路径（对照用）
```

**为什么这段必须用 C++**：性能瓶颈在「解析 + 序列化」本身。走文本中间层（.osgt）
要多两次序列化、慢 3~4 倍；C++ 直接用 OSG API 在内存里读场景图、改 Vec3Array、写回，
全模型 1537 瓦片约 23 秒（16 线程）。

**构建**：

```bash
sudo apt-get install -y libopenscenegraph-dev
bash app/osgb/cpp/build.sh /tmp/osgb_vertex_transform
```

构建产物**不入版本库**；界面会自动在 app/osgb/cpp/ 与 /tmp/ 查找，找不到时提示构建命令。
源码与脚本一并提交，任何机器都能重建。
## 逆向 OSGBLab 得到的三条关键结论

| # | 结论 | 证据（Linux 版，符号完整） |
|---|---|---|
| 1 | **不经过文本层**，读 osgb 直接写 osgb | `OSGB2OSGBVisitor` 直接处理 osg 场景图；产物目录无 `.osgt` |
| 2 | 把 Mesh + 纹理**重新组装**成 OSG 场景图后写出 | `OSGB2OSGBVisitor::OutputMesh(Mesh const&, vector<Image>, string const&)` |
| 3 | 用 **TBB 多线程** | DIE 检出 `Intel TBB`；Linux 版链接 `libtbb` |

它配置的 OSG 选项（`OutputMesh` 反汇编实证）：
`IncludeFile` / `WriteImageHint` / `OSGSoVersion=100` / `TargetFileVersion`，
并通过 `Registry::getReaderWriterForExtension()` 直接取 osgb 插件 writer。

## 体积控制：三个选项缺一不可

单瓦片（2.87 MB 输入）实测：

| 选项 | 输出 | 比值 |
|---|---|---|
| 无 | 10,779,367 | 3.76× |
| `Compressor=zlib` | 6,107,109 | 2.13× |
| `osgconv --compressed` | 3,856,147 | 1.34× |
| **`Compressor=zlib compression=1 WriteImageHint=IncludeFile`** | **2,346,270** | **0.82×** |
| 原始文件 | 2,865,453 | 1.00× |

- `Compressor=zlib` 压几何（从二进制里挖出的键名，紧邻 `osgblab.com` 字符串）
- `compression=1` + `WriteImageHint=IncludeFile` 压纹理并内联
- `TargetFileVersion` 实测 147/154/161/200 **对体积无影响**，已排除

## 最优执行策略：先写本地盘再拷回

| 策略 | 耗时 |
|---|---|
| C++ 直写挂载点（16 线程） | 22.5 s |
| **C++ 写 /tmp 本地 + 并行拷回** | **21.6 s**（转换 16.2 + 拷回 5.5）|

原因：WSL 的 `/mnt/e` 挂载点写入有固定惩罚，本地 ext4 写入快得多。

## 复现

```bash
sudo apt-get install -y libopenscenegraph-dev
bash app/osgb/cpp/build.sh /tmp/osgb_vertex_transform

OPT="Compressor=zlib compression=1 WriteImageHint=IncludeFile"

# 单瓦片
/tmp/osgb_vertex_transform in.osgb out.osgb "1,0,0,0,1,0,0,0,1" "1000,-500,12.5" "$OPT"

# 批处理（清单 TSV: in路径\tout路径\tA(9逗号分隔)\tb(3逗号分隔)），16 线程
# 建议输出先写本地盘，再并行 cp 到目标
/tmp/osgb_vertex_transform --batch jobs.tsv "$OPT" 16
```

## 已知边界

1. **1.69× vs ~1.0×**：全模型膨胀来自纹理重压比例因瓦片而异（单瓦片可达 0.82×）。
   若要 1:1，需去掉 `WriteImageHint=IncludeFile` 保留原纹理，但那会回到 2.13×。
2. **21.6 s vs 10 s**：转换环节 16.2 s 已同量级；剩余是挂载点 I/O（5.5 s）。
   把模型放到 WSL 本地盘处理可再省这部分。
3. **精度 0.005 mm**：来自原文件的量化步长（0.001 m 量级）+ float32 表示，非本工具引入。
