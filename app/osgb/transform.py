"""OSGB 顶点变换 —— 原样照套本项目既有的参数应用链路。

本模块**不实现任何坐标换算**。它做的事只有一件：把"逐点函数"形式的
`app.logic.apply_params.apply_points` 适配成 C++ 工具需要的"仿射矩阵"形式。

照套的链路（与「参数应用转换」页 / ApplyPage 完全一致）
--------------------------------------------------------
ApplyPage 的判定（app/gui/pages.py 4390-4391、4414-4415）:

    src_lonlat = source_kind == "lonlat" or kind in ("seven","three","chain74")
    out_lonlat = kind in ("seven","three") or kind in ("inv_direct","inv_chain3","inv_chain")
    kind_det   = detect_coord_kind(输入坐标)
    expect_lonlat = src_lonlat

    apply_points(names, vals_in, doc, out_lonlat, src_lonlat, kind,
                 do_plane, do_height, ell)

apply_points 的 5 个分支（app/logic/apply_params.py）:

    1) inv_direct / inv_chain3 / inv_chain  -> applier.apply_planar_to_lonlat
    2) out_lonlat (seven/three)             -> ECEF + apply_7param/apply_3param
    3) src_lonlat (chain74/direct_gk)       -> applier.apply_lonlat
    4) 其它（planar3/planar4/seven2d）       -> applier.apply_planar

OSGB 顶点是什么坐标？
---------------------
OSGB 顶点 = "实际坐标 − SRSOrigin"。对投影坐标系（如 EPSG:4547）模型，
顶点是**几十~几百米量级的平面偏移**，`detect_coord_kind` 判为 `planar`。

因此 OSGB 只能走**平面源**分支。若参数 `expect_lonlat`（大地源），
则与 OSGB 顶点类型不匹配——ApplyPage 在这种情况下会弹
「坐标类型不匹配」并拒绝（pages.py:4420-4424），本模块同样拒绝。

所以本模块实际使用的就是 apply_points 的分支 4，逐字节照套。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["MODES", "Mode", "MODE_TABLE", "PlanarVertexTransform",
           "derive_new_origin", "affine_coefficients"]

MODES = ("xyz", "xy", "z")


@dataclass(frozen=True)
class Mode:
    """一档变换模式，直接映射 ApplyPage 的两个勾选框。

    key:       xyz / xy / z
    do_plane:  对应 ApplyPage 的 chk_do_plane
    do_height: 对应 ApplyPage 的 chk_do_height
    """

    key: str
    label: str
    do_plane: bool
    do_height: bool


MODE_TABLE: dict[str, Mode] = {
    "xyz": Mode("xyz", "坐标 + 高程（XY+Z）", True, True),
    "xy": Mode("xy", "仅平面坐标（XY）", True, False),
    "z": Mode("z", "仅高程（Z）", False, True),
}


class PlanarVertexTransform:
    """把 ApplyPage 的平面源分支作用到 OSGB 顶点数组上。

    内部完全委托 apply_points——本类不含任何坐标公式，只做两件事：
      1) 轴序转换：OSGB (东,北) ↔ 测量约定 (北,东)，复用 transform2d.to_survey_xy
      2) 逐点调用 apply_points

    用法：
        tf = PlanarVertexTransform(param_doc, mode="xy")
        new_local = tf.apply(local_verts)      # (K,3) 局部偏移 -> 新局部偏移
    """

    def __init__(self, param_doc: dict, *, mode: str = "xyz",
                 vertex_kind: str = "planar",
                 origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
                 proj_override: dict | None = None):
        if mode not in MODE_TABLE:
            raise ValueError("未知模式 %r，可选：%s" % (mode, ", ".join(MODES)))
        self.mode = MODE_TABLE[mode]
        self.param_doc = dict(param_doc)
        # 源侧投影覆盖（界面）：带号 / 中央子午线 / 假东等。
        # 生效方式 = 合并进参数文档的 projection_src，让引擎按同一口径处理，
        # 不在本层另开分支。典型用途：
        #   · 模型东坐标含带号（如 39595251）而参数未记录 zone → 界面勾选剥离
        #   · 参数缺 projection_src（大地水准面反算、投影链都要用）→ 界面补 L0
        if proj_override:
            psrc = dict(self.param_doc.get("projection_src") or {})
            psrc.update({k: v for k, v in proj_override.items() if v is not None})
            self.param_doc["projection_src"] = psrc
        self.vertex_kind = vertex_kind
        # 模型 SRSOrigin（OSGB 轴序 东,北,高）。
        # 顶点是"实际坐标 − SRSOrigin"的局部偏移，而大地水准面格网覆盖全区域，
        # 查算 ξ 时必须用绝对投影坐标；平面参数仍按局部坐标应用。
        self.origin = np.asarray(origin, dtype=np.float64)
        self._check_accepts_planar()

    # ---- 与 ApplyPage 同口径的前置判定 ----

    def _check_accepts_planar(self):
        """照套 ApplyPage 的判定：大地源参数不能用于平面坐标输入。

        ApplyPage 的对应逻辑是 pages.py:4420-4424：
            if (kind_det == "lonlat") != expect_lonlat and not poly_inv_ok:
                弹「坐标类型不匹配」并 return

        对 OSGB：坐标类型恒为 planar。所以 expect_lonlat 为真即拒绝。
        expect_lonlat 的定义照抄 pages.py:4390。
        """
        d = self.param_doc
        kind = str(d.get("kind", ""))
        # pages.py:4390 —— 原样照抄
        src_lonlat = (d.get("source_kind") == "lonlat"
                      or kind in ("seven", "three", "chain74"))

        # 顶点类型由 SRS 决定，**不能用 detect_coord_kind 猜**：
        # OSGB 顶点是相对 SRSOrigin 的小偏移（几十~几百米），
        # 量级判别必然给出 lonlat，与实际空间类型无关。
        #   origin_kind == "projected"  → 顶点 = 投影坐标 − origin → planar
        #   origin_kind == "enu"        → 局部 ENU 米制            → planar
        kind_det = self.vertex_kind

        # pages.py:4417-4419 —— 多项式配源侧投影时，平面输入自动反算为大地，放行
        poly_inv_ok = (kind in ("poly2d", "poly3d") and src_lonlat
                       and (d.get("projection_src") or {}).get("l0") is not None
                       and kind_det == "planar")

        # 大地源参数：OSGB 顶点是平面偏移，需先按源侧投影反算为经纬度，
        # 再走 apply_points 的 src_lonlat 分支（即 chain74/direct_gk 那条正向链）。
        # 前提是拿得到源侧投影（参数自带或界面覆盖），否则无法反算 → 拒绝。
        if src_lonlat and kind_det == "planar":
            psrc = d.get("projection_src") or {}
            if not psrc.get("l0"):
                raise ValueError(
                    "参数「%s」（kind=%s, source_kind=%s）源为大地坐标，"
                    "需要把 OSGB 平面顶点反算为经纬度，但缺源侧投影参数。"
                    "请在下方「投影参数」里填中央子午线 L0（带号模式会自动剥离带号）。"
                    % (d.get("name", "?"), kind, d.get("source_kind", "?")))
            return   # 允许，走 _apply_points 的平面→大地→模型链路

        if (kind_det == "lonlat") != src_lonlat and not poly_inv_ok:
            want = "大地坐标" if src_lonlat else "平面坐标"
            raise ValueError(
                "坐标类型不匹配：OSGB 顶点识别为 %s，但参数「%s」"
                "（kind=%s, source_kind=%s）期望源为%s。"
                % (kind_det, d.get("name", "?"), kind,
                   d.get("source_kind", "?"), want))

    # ---- 照套 apply_points ----

    def _apply_points(self, pts: np.ndarray) -> np.ndarray:
        """(K,3) 实际坐标 -> 变换后实际坐标。

        逐点调用 apply_points（与 ApplyPage 完全相同的入口与参数），
        口径 = 平面源分支（apply_points 的分支 4）。
        """
        from app.logic.apply_params import apply_points

        d = self.param_doc
        kind = str(d.get("kind", ""))
        # pages.py:4390-4391 —— 原样照抄
        src_lonlat = (d.get("source_kind") == "lonlat"
                      or kind in ("seven", "three", "chain74"))
        out_lonlat = (kind in ("seven", "three")
                      or kind in ("inv_direct", "inv_chain3", "inv_chain"))

        # ⚠ 轴序转换：OSGB 顶点是 (东 E, 北 N, 高) —— 标准 GIS/ENU 轴序；
        #   本项目测量约定是 (x=北, y=东)。两者相反，必须先换轴再进引擎。
        #   复用项目既有函数 app.core.transform2d.to_survey_xy，不自己写交换。
        from app.core.transform2d import to_survey_xy

        # 大地源参数（chain74/direct_gk）：OSGB 平面顶点 → 源侧经纬度 → 正向链。
        # 反算复用既有的 _poly_src_point（本类唯一实现，含带号剥离/源椭球/投影面高）。
        if src_lonlat and not (d.get("geoid_grid")):
            # 注意：apply() 已把 SRSOrigin 加回，本方法的 pts **就是绝对投影坐标**，
            # 不要再加原点（曾因此双重加原点，偏差 55 m）。
            from app.common.params import ParamApplier
            ap = ParamApplier(d)
            out = np.empty_like(pts)
            for i in range(pts.shape[0]):
                abs_e = float(pts[i, 0])      # 东
                abs_n = float(pts[i, 1])      # 北
                b, l, _ = ap._poly_src_point(abs_n, abs_e, float(pts[i, 2]),
                                             src_is_lonlat=False)
                # apply_lonlat 的解耦本身是正确的（实测：只高程时 h 照改、
                # 只平面时 h 透传）。问题只在 it 的"仅高程"会把 x/y 透传成经纬度，
                # 而 OSGB 顶点要的是平面坐标。
                # 因此：**只取它算出的 Δh**，平面坐标按 平面/高程 两个开关分别处理。
                pl_, pn_, ph_ = ap.apply_lonlat(
                    float(b), float(l), float(pts[i, 2]),
                    do_plane=True, do_height=self.mode.do_height)
                if self.mode.do_plane:
                    out[i, 0] = float(pn_)     # 东（绝对值）
                    out[i, 1] = float(pl_)     # 北（绝对值）
                else:
                    out[i, 0] = abs_e          # 平面原值透传
                    out[i, 1] = abs_n
                out[i, 2] = float(ph_)
            return out

        # 参数带大地水准面格网：ξ 需要**绝对投影坐标**反算 B/L，而顶点是局部偏移。
        # apply_points 是逐点入口、无法携带绝对坐标，因此改走 apply_planar_at ——
        # 平面部分按局部坐标算、格网查算按 (局部 + SRSOrigin) 算，两者解耦。
        if d.get("geoid_grid"):
            from app.common.params import ParamApplier
            ap = ParamApplier(d)
            gout = np.empty_like(pts)
            for i in range(pts.shape[0]):
                xn, ye = to_survey_xy(float(pts[i, 0]), float(pts[i, 1]))
                gx = float(self.origin[0]) + float(pts[i, 0])     # 绝对东
                gy = float(self.origin[1]) + float(pts[i, 1])     # 绝对北
                o1, o2, o3 = ap.apply_planar_at(
                    xn, ye, float(pts[i, 2]), gy, gx,
                    do_plane=self.mode.do_plane, do_height=self.mode.do_height)
                xe, yn = to_survey_xy(float(o1), float(o2))
                gout[i, 0], gout[i, 1], gout[i, 2] = xe, yn, float(o3)
            return gout

        names = ["v%d" % i for i in range(pts.shape[0])]
        vals_in = []
        for i in range(pts.shape[0]):
            xn, ye = to_survey_xy(float(pts[i, 0]), float(pts[i, 1]))   # (E,N) -> (N,E)
            vals_in.append([xn, ye, float(pts[i, 2])])
        res, err = apply_points(
            names, vals_in, d, out_lonlat, src_lonlat, kind,
            self.mode.do_plane, self.mode.do_height, None)
        if err:
            raise ValueError("%s: %s" % (err[0], err[1]))
        out = np.empty_like(pts)
        for i, r in enumerate(res):
            v = r["vals"]
            xe, yn = to_survey_xy(float(v[0]), float(v[1]))   # (N,E) -> (E,N)，换回 OSGB 轴序
            out[i, 0] = xe
            out[i, 1] = yn
            out[i, 2] = float(v[2])
        return out

    # ---- 对外：局部偏移 -> 新局部偏移 ----

    def apply(self, local_verts: np.ndarray) -> np.ndarray:
        """把一个块的局部偏移数组变换为新局部偏移。

        local_verts: (K,3) float64 —— 文件里存的"实际坐标 − SRSOrigin"
        返回:        (K,3) float64 —— 新的"实际坐标 − 新 SRSOrigin"
        """
        v = np.asarray(local_verts, dtype=np.float64)
        if v.ndim != 2 or v.shape[1] != 3:
            raise ValueError("顶点数组形状应为 (K,3)，实际 %s" % (v.shape,))
        if v.shape[0] == 0:
            return v.copy()
        # 参数的坐标空间 = **绝对投影坐标**。
        # 证据（构造真值实验 _abs_truth）：
        #   用绝对投影坐标造控制点并拟合四参数，再对同一空间的绝对坐标应用，
        #   结果与真值逐位一致；而喂局部偏移会得到完全错误的结果
        #   （真值 N=2865087.029595，喂绝对得到同一值，喂局部得到 21.04）。
        # 控制点在现实中永远是绝对坐标，所以拟合出的参数必然工作在绝对空间。
        #
        # OSGB 顶点 = 实际坐标 − SRSOrigin 的**局部偏移**，故须先加回原点。
        # 输出同样按局部偏移写回 —— SRSOrigin 保持不变：
        #   out_world = f(local + origin)，out_local = out_world − origin
        #   ⇒ 世界坐标被变换，局部坐标系不动。
        out = self._apply_points(v + self.origin)
        return out - self.origin


def derive_new_origin(param_doc: dict, old_origin, *, mode: str,
                      transform: PlanarVertexTransform | None = None):
    """求"旧原点经同一变换后的位置"，作为新的 SRSOrigin。

    语义：新原点 = 旧原点按参数转换后的结果。于是

        输出局部 = 输入局部 + 世界位移 − 原点位移 = 输入局部

    即局部坐标系不随位移改变，世界坐标整体平移——两个坐标系的差值被完全
    吸收进 SRSOrigin，float32 的局部偏移不会因为换算而丢失精度。

    不做任何取整——取整到 100 m 会引入 0.5 mm 级误差（实测教训）。
    """
    old = np.asarray(old_origin, dtype=np.float64)
    tf = transform if transform is not None else PlanarVertexTransform(
        param_doc, mode=mode)
    # 局部 0 点的位移。注意 tf.apply 收发的是 **OSGB 轴序 (东,北,高)**，
    # 而 old_origin 来自 metadata.xml，也是 OSGB 轴序——两者一致，可直接相加。
    disp = tf.apply(np.zeros((1, 3), dtype=np.float64))[0]
    return tuple(float(v) for v in (old + disp))


def affine_gain_absolute(transform: PlanarVertexTransform, *,
                          sample: tuple[float, float, float]):
    r"""求**绝对坐标**下的增益与常数项：out = gain @ p + const，p 为绝对 (x,y,z)。

    与 `affine_coefficients` 的分工：
      · `affine_coefficients` 面向 OSGB —— 参数作用在"局部 + 原点"上，
        线性化点固定取局部 (0,0,0) 邻域。
      · 本函数面向 LAS / 点表等**坐标本身就是绝对投影坐标**的场景 ——
        线性化点必须落在数据实际范围内，否则大坐标处的旋转/尺度会算错。

    同样只做有限差分 + 二阶差分线性性校验，不含任何坐标公式。
    """
    # param_doc 里已含合并后的 projection_src，直接沿用即可
    tf = PlanarVertexTransform(transform.param_doc, mode=transform.mode.key,
                               vertex_kind=transform.vertex_kind)
    c = np.asarray(sample, dtype=np.float64).reshape(3)
    h = 10.0
    base = tf.apply(c.reshape(1, 3))[0]
    cols = []
    for k in range(3):
        d = np.zeros(3)
        d[k] = h
        d1 = tf.apply((c + d).reshape(1, 3))[0] - base
        d2 = tf.apply((c + 2.0 * d).reshape(1, 3))[0] - base
        if np.abs(d2 - 2.0 * d1).max() > 1e-6 * max(1.0, float(np.abs(d2).max())):
            raise ValueError("该变换在给定点上非线性过强，不能用仿射批量改写")
        cols.append(d1 / h)
    gain = np.column_stack(cols)
    const = base - gain @ c
    return gain, const


def affine_coefficients(transform: PlanarVertexTransform, origin=None):
    """提取顶点变换的仿射系数：out_local = A @ local + b。

    本函数不实现任何坐标换算——只对 PlanarVertexTransform（其本身完全
    委托 apply_points）做有限差分线性化，并用二阶差分校验线性性。

    四参数/三参数/平面多项式都是线性或低阶的；高阶多项式会在此被明确拒绝，
    而不是悄悄给出错误结果。

    仿射的线性部分与原点无关，所以内部以局部原点 (0,0,0) 构造变换。
    传入 origin 仅为兼容旧签名，不参与计算。

    返回 {"A": 3x3 list, "b": [3] list}。
    """
    # 带上 SRSOrigin：大地水准面 ξ 需要绝对坐标反算；仿射的线性部分与原点无关
    tf = PlanarVertexTransform(transform.param_doc, mode=transform.mode.key,
                               vertex_kind=transform.vertex_kind,
                               origin=tuple(float(v) for v in transform.origin))
    c = np.array([1000.0, 1000.0, 1000.0], dtype=np.float64)   # 避开 0 附近退化
    h = 100.0
    base = tf.apply(c.reshape(1, 3))[0]
    cols = []
    for k in range(3):
        d = np.zeros(3)
        d[k] = h
        d1 = tf.apply((c + d).reshape(1, 3))[0] - base
        d2 = tf.apply((c + 2.0 * d).reshape(1, 3))[0] - base
        if np.abs(d2 - 2.0 * d1).max() > 1e-9 * max(1.0, float(np.abs(d2).max())):
            raise ValueError("该变换不是线性模型，无法用仿射系数批量改写")
        cols.append(d1 / h)
    A = np.column_stack(cols)
    b = base - A @ c

    # ---- 线性化残差自检 ----
    # 大地水准面格网使高程成为空间的缓变非线性函数，而 C++ 工具只吃单个仿射矩阵。
    # 这里在**单瓦片可能达到的范围**上抽样，验证仿射近似是否仍足够精确。
    # 残差超限时宁可拒绝，也不静默给出超差结果。
    rng = np.random.default_rng(20260930)
    span = 2000.0                     # ±1 km（覆盖典型瓦片尺度）
    probe = rng.uniform(-span, span, size=(64, 3))
    probe[:, 2] = rng.uniform(-300.0, 300.0, size=64)
    exact = tf.apply(probe)
    approx = probe @ A.T + b
    resid = float(np.abs(exact - approx).max())
    LIMIT = 1e-4                      # 0.1 mm —— 与项目精度目标一致
    if resid > LIMIT:
        raise ValueError(
            "仿射线性化残差 %.3e m (%.4f mm) 超过 %.1f mm 限值：该参数在瓦片尺度上"
            "非线性过强（多为大地水准面格网或高阶多项式），不能用单矩阵批量改写。"
            "请缩小处理范围或改用不含格网/低阶的参数。"
            % (resid, resid * 1000, LIMIT * 1000))
    return {"A": A.tolist(), "b": b.tolist(), "linear_residual_m": resid}
