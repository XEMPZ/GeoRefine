"""点云模型转换（LAS）。

设计契约
--------
1. **只改顶点坐标（X/Y/Z），其余属性一律原样保留** —— 强度、回波、分类、
   GPS 时间、RGB、扫描角、点源 ID 等非坐标数据绝不改动。
2. LAS 读写用公开库 **laspy**（不自研格式解析）；坐标换算一律委托项目既有模块
   `app.logic.apply_params.apply_points` 与 `app.common.params.ParamApplier`。
3. **轴序**：LAS 头的 CRS 通常声明 AXIS[Easting,EAST], AXIS[Northing,NORTH]，
   即 **X=东、Y=北**；本项目测量约定是 **x=北、y=东**，故须换轴
   （复用 `app.core.transform2d.to_survey_xy`）。
4. **scale/offset 保持不变**：LAS 用「整数 × scale + offset」存坐标，
   scale 决定量化步长（常见 0.0001 m）。改写它等于改变精度口径，不属于坐标转换，
   因此不动 scale/offset，只按实际写出值重算 min/max。

依赖：laspy（pip install laspy）。缺失时给出明确提示，不静默降级。
"""
from __future__ import annotations

__all__ = ["las_io", "las_transform", "las_job"]
