r"""LAS 点云坐标变换 —— 与 app/osgb/transform.py 同一套链路，去掉原点那层。

与 OSGB 的差别只有一处
------------------------
OSGB 顶点是相对 SRSOrigin 的**局部偏移**，所以要加减原点；
LAS 的 X/Y/Z 存的就是**绝对投影坐标**，直接进引擎即可，无需任何原点补偿。

其余完全一致：

* **轴序**：LAS 惯例 X=东、Y=北；本项目测量约定 x=北、y=东。
  换轴复用 `app.core.transform2d.to_survey_xy`。
* **解耦**：平面与高程独立勾选，未勾部分原值逐位透传。
* **换算委托**：`app.common.params.ParamApplier`（平面源）/ `apply_lonlat`（大地源），
  本模块不含任何坐标公式。
* **大地水准面**：ξ 按绝对投影坐标反算的源侧经纬度插值（LAS 本身就是绝对的，天然满足）。
"""
from __future__ import annotations

import numpy as np

from app.osgb.transform import affine_gain_absolute

__all__ = ["LasVertexTransform"]


class LasVertexTransform:
    r"""把参数作用到 LAS 点云坐标上（绝对投影坐标）。

    用法：
        tf = LasVertexTransform(param_doc, mode="xyz"[, proj_override=...])
        new_xyz = tf.apply(x_east, y_north, z)      # 三个 (n,) 数组 → 三个新数组
    """

    def __init__(self, param_doc: dict, *, mode: str = "xyz",
                 proj_override: dict | None = None, point_kind: str = "planar"):
        from app.osgb.transform import MODE_TABLE
        if mode not in MODE_TABLE:
            raise ValueError("未知模式 %r，可选：%s"
                             % (mode, ", ".join(sorted(MODE_TABLE))))
        self.mode = MODE_TABLE[mode]
        self.param_doc = dict(param_doc)
        if proj_override:
            psrc = dict(self.param_doc.get("projection_src") or {})
            psrc.update({k: v for k, v in proj_override.items() if v is not None})
            self.param_doc["projection_src"] = psrc
        self.point_kind = point_kind
        self._ap = None
        self._check()
        # 供矩阵化复用的变换对象（构造时已把 proj_override 合并进 param_doc）
        from app.osgb.transform import PlanarVertexTransform
        self.tf = PlanarVertexTransform(self.param_doc, mode=mode,
                                        vertex_kind=point_kind)

    # ---- 前置判定（照抄 OSGB 页同口径）----

    def _check(self):
        d = self.param_doc
        kind = str(d.get("kind", ""))
        # GUI pages.py:4390 —— 原样照抄
        src_lonlat = (d.get("source_kind") == "lonlat"
                      or kind in ("seven", "three", "chain74"))
        self.src_lonlat = src_lonlat
        if src_lonlat:
            psrc = d.get("projection_src") or {}
            if not psrc.get("l0"):
                raise ValueError(
                    "参数「%s」（kind=%s）源为大地坐标，需要把点云平面坐标反算为经纬度，"
                    "但缺源侧投影参数。请在「投影参数」填中央子午线 L0。"
                    % (d.get("name", "?"), kind))

    @property
    def ap(self):
        if self._ap is None:
            from app.common.params import ParamApplier
            self._ap = ParamApplier(self.param_doc)
        return self._ap

    # ---- 核心 ----

    def apply(self, x_east, y_north, z):
        r"""变换点云坐标。

        x_east, y_north, z: 等长 (n,) 数组；x 为**东坐标**、y 为**北坐标**
        （即 LAS 的 X、Y 原义）。
        返回 (new_x_east, new_y_north, new_z)。
        """
        from app.core.transform2d import to_survey_xy

        xe = np.asarray(x_east, dtype=np.float64)
        yn = np.asarray(y_north, dtype=np.float64)
        zz = np.asarray(z, dtype=np.float64)
        if not (xe.shape == yn.shape == zz.shape):
            raise ValueError("X/Y/Z 长度不一致：%s %s %s"
                             % (xe.shape, yn.shape, zz.shape))
        n = xe.size
        out_e = xe.copy()
        out_n = yn.copy()
        out_z = zz.copy()
        if n == 0:
            return out_e, out_n, out_z

        ap = self.ap
        dp, dh = self.mode.do_plane, self.mode.do_height

        if self.src_lonlat:
            # 大地源：绝对平面 → 源侧经纬度 → 参数正向链（apply_lonlat）
            # 只用它算出的 Δh；平面按开关决定是否采用（同 OSGB 的做法）。
            for i in range(n):
                b, l, _ = ap._poly_src_point(float(yn[i]), float(xe[i]), float(zz[i]),
                                             src_is_lonlat=False)
                pl_, pn_, ph_ = ap.apply_lonlat(float(b), float(l), float(zz[i]),
                                                do_plane=True, do_height=dh)
                if dp:
                    out_e[i] = float(pn_)
                    out_n[i] = float(pl_)
                out_z[i] = float(ph_)
            return out_e, out_n, out_z

        # 平面源：变换是仿射的 → **整块矩阵化**（逐点 Python 调用约 8.6 万点/s，
        # 4913 万点的单个 LAS 要 9.5 分钟，不可接受；矩阵化后快数百倍）。
        # 线性化点取本块的实际中心，避免在 60 万米外的坐标上误判旋转/尺度。
        # 线性化点取本块中心（LAS 原序 东,北,高 —— PlanarVertexTransform.apply
        # 内部会换轴到 (北,东,高)，所以样本照 LAS 原序给即可）。
        sample = (float(xe.mean()), float(yn.mean()), float(zz.mean()))
        gain, const = affine_gain_absolute(self.tf, sample=sample)
        # 实测确定（见 _las_pick 四候选对照，胜出者偏差 9.3e-10 m）：
        # gain 的行序就是 **LAS 原序 (东, 北, 高)** —— 因为 PlanarVertexTransform
        # 内部对 (北,东) 换轴、affine_coefficients 又换回，净效果是 gain 直接作用
        # 在 LAS 的 (X, Y, Z) 上。**不要在此预换轴**，否则会得到差 85.9 m 的结果。
        na = np.column_stack([xe, yn, zz])                  # (E, N, H)
        res = na @ gain.T + const
        if dp:
            out_e = res[:, 0].copy()                        # 东 = 第 0 列
            out_n = res[:, 1].copy()                        # 北 = 第 1 列
        if dh:
            out_z = res[:, 2].copy()
        return out_e, out_n, out_z
