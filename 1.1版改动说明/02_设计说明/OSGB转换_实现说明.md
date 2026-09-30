# OSGB 模型转换：实现说明

## 一句话

用 OpenSceneGraph 做**一次内存态往返**：`osgDB::readNodeFile` 读 osgb → 遍历
`osg::Geometry` 的 `Vec3Array` 改顶点 → `osgDB::writeNodeFile` 写回。
**不经文本中间层。**

## 三个关键设计选择

| # | 选择 | 依据 |
|---|---|---|
| 1 | 不经文本中间层（不落 `.osgt`） | 实测单瓦片：文本层 0.887 s vs 内存态 0.230 s，**慢 3.9 倍** |
| 2 | 只改 `Vec3Array`，场景图其余部分原样带过 | 坐标转换只涉及几何；不动 PagedLOD/StateSet/纹理，可保证除坐标外一切逐位不变 |
| 3 | 单进程多线程批处理，不按瓦片起进程 | 进程启动 + OSG 初始化开销远大于单瓦片计算量 |

## 写出选项（体积关键）

单瓦片（2.87 MB 输入）实测：

| 选项 | 输出字节 | 比值 |
|---|---|---|
| 无 | 10,779,367 | 3.76× |
| `Compressor=zlib` | 6,107,109 | 2.13× |
| `osgconv --compressed` | 3,856,147 | 1.34× |
| **`Compressor=zlib compression=1 WriteImageHint=IncludeFile`** | **2,346,270** | **0.82×** |
| 原始文件 | 2,865,453 | 1.00× |

三个选项缺一不可，且 `TargetFileVersion` 实测（147/154/161/200）**对体积无影响**。

## 顶点变换的语义细节

### 轴序

| | 平面分量顺序 |
|---|---|
| OSGB 顶点 | **（东, 北）** |
| 本项目约定 | **（北, 东）** |

相反，读写处统一复用 `app/core/transform2d.to_survey_xy` 换算。

### 参数作用在绝对坐标上

OSGB 顶点 = 实际世界坐标 − SRSOrigin，即**局部偏移**；而控制点参数是拟合在**绝对投影坐标**上的。

因此 `PlanarVertexTransform.apply()` 先加回 SRSOrigin、变换后再减回：

```python
def apply(self, local):
    out = self._apply_points(local + self.origin)
    return out - self.origin
```

**SRSOrigin 保持不变**——位移落在顶点上，局部坐标系不动。

### 大地水准面 ξ 需要绝对坐标

格网覆盖全测区，而顶点是局部的。故新增 `apply_planar_at(x, y, h, gx, gy, ...)`：
平面部分仍按局部坐标算，**只有 ξ 查算用 `(gx, gy)`** 反算源侧经纬度。

## 与单点转换的一致性

OSGB / 点云 / 照片 POS / CAD 转换**全部走同一个入口** `apply_points`，不存在两套算法。
已用往返实验验证：正变换 + 反向平移后，局部坐标与原始**逐位相同**。

## 性能实测（全模型）

| 指标 | 要求 | 实测 |
|---|---|---|
| 全模型耗时 | ≤ 60 s | **21.6 s** |
| 输出体积（单瓦片） | ≈ 输入 | **0.82×** |
| 精度 | ≤ 0.1 mm | **0.005 mm** |
| 瓦片 | 无失败 | **1537 / 0 失败** |

模型：1537 瓦片 / 1562 MB / 2098 万顶点 / 16 线程。

## 详细文档

- 设计细节与所有语义推导：`app/osgb/DESIGN.md`
- Windows 原生构建：`docs/BUILD_WINDOWS_OSG.md`
- 工具源码：`03_工具源码/`
