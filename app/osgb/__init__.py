r"""OSGB 模型转换（坐标/高程）—— 内存态读写，不经文本中间层。

子模块：
  - `srs`       metadata.xml 的 SRS / SRSOrigin 解析与回写
  - `osgb_io`   osgconv 桥（OSGB 读写一律交给 OSG 官方实现）
  - `transform`  三档模式（XY+Z / 仅XY / 仅Z）的顶点变换语义
  - `osgb_job`   批量驱动：逐瓦片处理 + metadata 回写
"""

__all__ = ["srs", "osgb_io", "transform", "osgb_job"]
