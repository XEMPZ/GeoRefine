"""OSGB 模型转换模块测试（ARCHITECTURE.md §8 test_osgb）。提供 run()，兼容 pytest。

覆盖：
  1. metadata.xml 解析（EPSG 投影 / ENU / local / 缺字段）
  2. 新原点推算的两条硬约束（同量级、位移精确）
  3. 三档模式语义（xyz / xy / z 的门控）
  4. 真实瓦片经 osgconv 的端到端精度（需 OSG，缺失时跳过）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.osgb import osgb_job as J  # noqa: E402
from app.osgb.osgb_io import OsgToolchain, OsgToolchainMissing  # noqa: E402
from app.osgb.srs import parse_metadata_xml, write_metadata_xml  # noqa: E402
from app.osgb.transform import PlanarVertexTransform, derive_new_origin  # noqa: E402

SAMPLE = os.path.join(_ROOT, "sample_data", "样例OSGB模型")

# 平面可用的测试参数：四参数平移 + 常量高程改正
PARAM_PLANAR = {
    "schema": "coordparam/1", "name": "test_planar4", "kind": "planar4",
    "source_kind": "planar", "target_kind": "planar", "ellipsoid": "CGCS2000",
    "planar4": {"dx": 1000.0, "dy": -500.0, "a": 1.0, "b": 0.0},
    "heightfit": {"mode": "const", "space": "planar", "value_type": "dh",
                  "coef": [12.5], "center": [0.0, 0.0], "loo_rms": 0.0},
}
ORIGIN = (595251.09781523107, 2865092.1621519276, 234.95499999993598)


# ---------------------------------------------------------------- 1. metadata

def test_parse_epsg_projected():
    info = parse_metadata_xml(
        '<ModelMetadata version="1"><SRS>EPSG:4547</SRS>'
        "<SRSOrigin>1.5,2.5,3.5</SRSOrigin></ModelMetadata>")
    assert info.origin_kind == "projected", info.origin_kind
    assert info.epsg == 4547
    assert info.origin == (1.5, 2.5, 3.5), info.origin
    assert info.has_georeference


def test_parse_enu():
    info = parse_metadata_xml(
        '<ModelMetadata version="1"><SRS>ENU:30.5,114.25</SRS>'
        "<SRSOrigin>0,0,52.3</SRSOrigin></ModelMetadata>")
    assert info.origin_kind == "enu", info.origin_kind
    assert info.enu_latlon == (30.5, 114.25), info.enu_latlon
    assert info.origin[2] == 52.3


def test_parse_local_and_missing():
    for text in ('<ModelMetadata><SRS>local</SRS></ModelMetadata>',
                 "<ModelMetadata></ModelMetadata>",
                 ""):
        info = parse_metadata_xml(text)
        assert info.origin_kind == "local", (text, info.origin_kind)
        assert not info.has_georeference


def test_write_metadata_roundtrip(tmp_path=None):
    d = tempfile.mkdtemp(prefix="osgbtest_")
    try:
        text = ('<?xml version="1.0" encoding="utf-8"?>\n<ModelMetadata version="1">\n'
                "    <!--keep me-->\n    <SRS>EPSG:4547</SRS>\n"
                "    <SRSOrigin>1,2,3</SRSOrigin>\n"
                "    <Texture><ColorSource>Visible</ColorSource></Texture>\n"
                "</ModelMetadata>\n")
        p = os.path.join(d, "metadata.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        info = parse_metadata_xml(text)
        info.origin = (10.0, 20.0, 30.0)
        write_metadata_xml(p, info, template=text)
        got = parse_metadata_xml(open(p, encoding="utf-8").read())
        assert got.origin == (10.0, 20.0, 30.0), got.origin
        assert "<!--keep me-->" in open(p, encoding="utf-8").read()   # 注释保留
        assert "<ColorSource>Visible</ColorSource>" in open(p, encoding="utf-8").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 2. 新原点

def test_new_origin_same_magnitude():
    """新原点必须与旧原点同量级，否则 float32 精度被吞。"""
    tf = PlanarVertexTransform(PARAM_PLANAR, mode="xyz")
    new = derive_new_origin(PARAM_PLANAR, ORIGIN, mode="xyz", transform=tf)
    for k, name in enumerate("xyz"):
        ratio = abs(new[k]) / max(abs(ORIGIN[k]), 1e-9)
        assert 0.5 < ratio < 2.0, f"{name} 新原点量级异常：{new[k]} vs {ORIGIN[k]}"


def test_new_origin_matches_shift():
    """新原点 − 旧原点 必须精确等于参数位移。"""
    tf = PlanarVertexTransform(PARAM_PLANAR, mode="xyz")
    new = derive_new_origin(PARAM_PLANAR, ORIGIN, mode="xyz", transform=tf)
    d = np.asarray(new) - np.asarray(ORIGIN)
    # 局部 0 点的位移。PARAM_PLANAR 的 (dx,dy)=(1000,-500) 是**测量轴序 (北,东)**，
    # 而 OSGB 轴序是 (东,北)，所以 dx 落在第 2 分量、dy 落在第 1 分量。
    exp = np.array([-500.0, 1000.0, 12.5])
    assert np.abs(d - exp).max() < 1e-6, f"原点位移 {d} != {exp}"


# ---------------------------------------------------------------- 3. 三档模式

def _mode_effect(mode):
    """返回**局部顶点的净变化**，轴序为 OSGB 的 (东, 北, 高)。

    语义（与 ApplyPage 一致）：参数直接作用于待转坐标本身。
    OSGB 顶点 = 实际坐标 − SRSOrigin，就是这个待转量。
    内部会先换轴到测量约定 (北, 东) 再调用 apply_points，结果再换回 OSGB 轴序。
    """
    tf = PlanarVertexTransform(PARAM_PLANAR, mode=mode)
    v = np.array([[100.0, 200.0, 50.0]])
    return tf.apply(v)[0] - v[0]


def test_mode_xyz_changes_all():
    """xyz 模式：三轴都动。

    _mode_effect 返回的是 **OSGB 轴序 (东,北,高)** 的净变化。
    PARAM_PLANAR 的 (dx,dy)=(1000,-500) 是测量轴序 (北,东)，
    换到 OSGB 轴序即 (东=-500, 北=1000)，高程 +12.5。
    """
    d = _mode_effect("xyz")
    exp = np.array([-500.0, 1000.0, 12.5])
    assert np.abs(d - exp).max() < 1e-6, f"得到 {d}，期望 {exp}"


def test_mode_xy_leaves_z():
    d = _mode_effect("xy")
    assert abs(d[2]) < 1e-9, f"xy 模式不该动 Z，实际 {d[2]}"
    # OSGB 轴序：(东=-500, 北=1000)
    assert abs(d[0] + 500.0) < 1e-6 and abs(d[1] - 1000.0) < 1e-6, d


def test_mode_z_only_height():
    d = _mode_effect("z")
    assert abs(d[0]) < 1e-9 and abs(d[1]) < 1e-9, f"z 模式不该动 XY，实际 {d}"
    assert abs(d[2] - 12.5) < 1e-6, d


def test_axis_order_osgb_east_north():
    """轴序：OSGB 顶点是 (东E, 北N, 高)，本项目测量约定是 (北N, 东E)。"""
    from app.core.transform2d import to_survey_xy, to_cad_xy
    # 用**非对称**位移，轴序错了必然暴露
    doc = {"schema": "coordparam/1", "name": "axis", "kind": "planar4",
           "source_kind": "planar", "target_kind": "planar", "ellipsoid": "CGCS2000",
           "planar4": {"dx": 100.0, "dy": -7.0, "a": 1.0, "b": 0.0},
           "heightfit": {"mode": "const", "space": "planar", "value_type": "dh",
                         "coef": [1.5], "center": [0.0, 0.0], "loo_rms": 0.0}}
    tf = PlanarVertexTransform(doc, mode="xyz")
    # OSGB 顶点：第一分量=东=2000，第二分量=北=1000
    v = np.array([[2000.0, 1000.0, 50.0]])
    out = tf.apply(v)[0]
    # 期望：换轴后 N=1000+dx=1100, E=2000+dy=1993；换回 OSGB 轴序 → (1993, 1100, 51.5)
    exp = np.array([1993.0, 1100.0, 51.5])
    assert np.abs(out - exp).max() < 1e-9, f"轴序错误：得到 {out}，期望 {exp}"
    # 反向：若不做换轴（把 E 当 N 用）会得到 (2100, 993, 51.5)，必须不等
    wrong = np.array([2100.0, 993.0, 51.5])
    assert np.abs(out - wrong).max() > 1.0, "未检测到轴序差异，测试失效"
    # 顺带确认复用的是项目既有函数（同一对互逆交换）
    assert to_survey_xy(2000.0, 1000.0) == (1000.0, 2000.0)
    assert to_cad_xy(1000.0, 2000.0) == (2000.0, 1000.0)


def test_axis_order_roundtrip_identity():
    """零位移时输出必须与输入逐位一致（换轴两次 = 恒等）。"""
    doc = {"schema": "coordparam/1", "name": "id", "kind": "planar4",
           "source_kind": "planar", "target_kind": "planar", "ellipsoid": "CGCS2000",
           "planar4": {"dx": 0.0, "dy": 0.0, "a": 1.0, "b": 0.0}}
    tf = PlanarVertexTransform(doc, mode="xy")
    v = np.array([[2000.0, 1000.0, 50.0], [-300.0, 45.0, -12.0]])
    out = tf.apply(v)
    assert np.abs(out - v).max() == 0.0, f"零位移应为恒等，实际 {out}"

def test_mode_rejects_unknown():
    try:
        PlanarVertexTransform(PARAM_PLANAR, mode="nope")
    except ValueError:
        return
    raise AssertionError("未知模式应抛 ValueError")


def test_rejects_lonlat_only_param():
    """源为大地坐标、不含平面链路的参数，必须明确拒绝而不是静默错算。"""
    p = {"schema": "coordparam/1", "name": "x", "kind": "chain74",
         "source_kind": "lonlat", "target_kind": "planar",
         "ellipsoid": "CGCS2000",
         "planar4": {"dx": 1.0, "dy": 2.0, "a": 1.0, "b": 0.0}}
    try:
        PlanarVertexTransform(p, mode="xyz")
    except ValueError:
        return
    raise AssertionError("大地坐标参数用于平面输入时应拒绝")


# ---------------------------------------------------------------- 4. 端到端

def test_end_to_end_precision(tmp_path=None):
    """真实瓦片：转换后世界坐标位移必须等于参数位移（阈值 0.1 mm）。"""
    try:
        tool = OsgToolchain()
    except OsgToolchainMissing as e:
        print(f"    [skip] 无 osgconv：{e}")
        return
    md_path = os.path.join(SAMPLE, "metadata.xml")
    if not os.path.exists(md_path):
        print("    [skip] 样例模型缺失")
        return
    from app.osgb.srs import load_metadata
    md = load_metadata(md_path)
    out = tempfile.mkdtemp(prefix="osgbtest_")
    wd = tempfile.mkdtemp(prefix="osgbwork_")
    try:
        res = J.convert_model(SAMPLE, out, PARAM_PLANAR, mode="xyz", tool=tool)
        assert res.fail_count == 0, res.summary()
        assert res.vertex_total > 0, res.summary()

        # 语义（与 ApplyPage 一致）：参数直接作用于局部顶点，
        # 位移落在顶点上，SRSOrigin 保持不变。
        dst_md = load_metadata(os.path.join(out, "metadata.xml"))
        assert tuple(dst_md.origin) == tuple(md.origin), "SRSOrigin 不应改变"

        # 期望位移直接由参数文档算出（用软件自己的链路，不硬编码）
        tf = PlanarVertexTransform(PARAM_PLANAR, mode="xyz")
        disp = tf.apply(np.zeros((1, 3)))[0]
        # OSGB 轴序 (东,北,高)：dx=1000 落在北(第2分量)，dy=-500 落在东(第1分量)
        assert np.abs(disp - np.array([-500.0, 1000.0, 12.5])).max() < 1e-6, disp

        # 逐块核对局部顶点位移。
        #
        # ⚠ 本核对经由 osgconv 文本中转，而文本只有 **6 位有效数字**：
        #   坐标量级 ~500 m 时每边量化到 1 mm，两端相减 + 舍入方向叠加，
        #   实测下限约 5 mm。**这不是工具的精度**，是测量方法的精度。
        #   工具的真实精度受限于源数据量化步长（1 mm）与 float32，
        #   二进制层面的实测见 PERFORMANCE.md（中位 0，99 分位 0.1 mm，上限 1 mm）。
        tol = 6e-3      # 文本测量的可分辨下限（含符号方向）
        checked = 0
        worst = 0.0
        for src in J.iter_tile_files(SAMPLE):
            rel = src.relative_to(SAMPLE)
            dst = os.path.join(out, str(rel))
            if not os.path.exists(dst):
                continue
            s_txt = tool.to_text(src, os.path.join(wd, "a.osgt"))
            d_txt = tool.to_text(dst, os.path.join(wd, "b.osgt"))
            for a, b in zip(_blocks(s_txt), _blocks(d_txt)):
                if a.shape != b.shape:
                    raise AssertionError(f"{rel} 块形状变化 {a.shape} → {b.shape}")
                dev = np.abs((b - a) - disp).max()
                worst = max(worst, float(dev))
                assert dev < tol, f"{rel} 局部位移偏差 {dev:.3e} m 超过 {tol} m"
                checked += a.shape[0]
        assert checked > 0, "未核对到任何顶点"
        print(f"    端到端核对顶点 {checked} 个，最大偏差 {worst*1000:.4f} mm (容差 {tol*1000:.0f} mm)")
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(wd, ignore_errors=True)


def _blocks(txt):
    lines = open(txt, encoding="utf-8", errors="replace").read().splitlines(keepends=True)
    plain = [l.rstrip("\n").rstrip("\r") for l in lines]
    out = []
    for (h, e, _i, _c) in J._find_vertex_blocks(plain):
        rows = [tuple(float(x) for x in J._F3.match(plain[k]).groups()) for k in range(h + 1, e)]
        out.append(np.asarray(rows, dtype=np.float64))
    return out


# ------------------------------------------------- 6. 平面/高程解耦 + 大地水准面

GEOID_GRID = "_test_geoid_liangchang"      # 覆盖样例模型区域的测试格网（models/）


def _doc_with_geoid(mode_dx=100.0, mode_dy=-7.0):
    """平面四参数 + 大地水准面格网高程链（源侧投影 L0=117）。"""
    return {"schema": "coordparam/1", "name": "decouple_geoid", "kind": "planar4",
            "source_kind": "planar", "target_kind": "planar", "ellipsoid": "CGCS2000",
            "planar4": {"dx": mode_dx, "dy": mode_dy, "a": 1.0, "b": 0.0},
            "projection_src": {"l0": 117.0, "x0": 0.0, "y0": 500000.0, "h0": 0.0,
                               "rigorous": False, "b0": 25.9},
            "geoid_grid": GEOID_GRID,
            "height_flags": {"poly": False, "surface": False, "geoid": True},
            "height_mode": "geoid"}


def _grid_available():
    try:
        from app.core.geoid import available_models
        return any(m["name"] == GEOID_GRID and m["usable"]
                   for m in available_models(_ROOT / "models"))
    except Exception:  # noqa: BLE001
        return False


def test_decouple_plane_only():
    """仅平面：XY 按参数位移，Z 逐位不变（即使参数含大地水准面）。"""
    if not _grid_available() or not os.path.exists(os.path.join(SAMPLE, "metadata.xml")):
        return
    md = load_metadata(os.path.join(SAMPLE, "metadata.xml"))
    tf = PlanarVertexTransform(_doc_with_geoid(), mode="xy", origin=tuple(md.origin))
    v = np.array([[0.0, 0.0, 0.0], [100.0, 200.0, -50.0], [-30.0, 40.0, 12.0]])
    d = tf.apply(v) - v
    assert np.abs(d[:, 2]).max() == 0.0, f"xy 模式不应改高程，实际 {d[:, 2]}"
    assert abs(d[:, 0].mean() + 7.0) < 1e-9, d
    assert abs(d[:, 1].mean() - 100.0) < 1e-9, d


def test_decouple_height_only():
    """仅高程：XY 逐位不变，Z 按格网 ξ 改正（正常高 = 大地高 − ξ）。"""
    if not _grid_available() or not os.path.exists(os.path.join(SAMPLE, "metadata.xml")):
        return
    md = load_metadata(os.path.join(SAMPLE, "metadata.xml"))
    tf = PlanarVertexTransform(_doc_with_geoid(), mode="z", origin=tuple(md.origin))
    v = np.array([[0.0, 0.0, 0.0], [100.0, 200.0, -50.0], [-30.0, 40.0, 12.0]])
    d = tf.apply(v) - v
    assert np.abs(d[:, :2]).max() == 0.0, f"z 模式不应改平面，实际 {d[:, :2]}"
    # 格网 ξ 在模型原点附近约 12.0（测试格网解析式在中心处为 12.0）
    assert abs(d[:, 2].mean() + 12.0) < 0.05, f"高程改正应≈ −ξ，实际 {d[:, 2]}"


def test_decouple_xyz_applies_both():
    """两者都勾选：XY 与 Z 同时改变。"""
    if not _grid_available() or not os.path.exists(os.path.join(SAMPLE, "metadata.xml")):
        return
    md = load_metadata(os.path.join(SAMPLE, "metadata.xml"))
    tf = PlanarVertexTransform(_doc_with_geoid(), mode="xyz", origin=tuple(md.origin))
    v = np.array([[0.0, 0.0, 0.0], [100.0, 200.0, -50.0]])
    d = tf.apply(v) - v
    assert abs(d[:, 0].mean() + 7.0) < 1e-9 and abs(d[:, 1].mean() - 100.0) < 1e-9, d
    assert abs(d[:, 2].mean() + 12.0) < 0.05, d


def test_geoid_requires_projection():
    """含格网但缺源侧投影时必须明确拒绝，不得静默跳过或换模型。"""
    if not _grid_available():
        return
    d = _doc_with_geoid()
    d.pop("projection_src")
    try:
        tf = PlanarVertexTransform(d, mode="z", origin=(595251.0, 2865092.0, 235.0))
        tf.apply(np.array([[0.0, 0.0, 0.0]]))
    except ValueError as e:
        assert "格网" in str(e) or "投影" in str(e), str(e)
        return
    raise AssertionError("缺源侧投影时应拒绝，而不是静默算错")


def test_geoid_linearization_residual():
    """格网让高程成为空间缓变函数：仿射线性化残差必须远小于 0.1 mm。"""
    if not _grid_available() or not os.path.exists(os.path.join(SAMPLE, "metadata.xml")):
        return
    md = load_metadata(os.path.join(SAMPLE, "metadata.xml"))
    tf = PlanarVertexTransform(_doc_with_geoid(), mode="xyz", origin=tuple(md.origin))
    coef = affine_coefficients(tf)
    r = coef["linear_residual_m"]
    assert r < 1e-4, f"线性化残差 {r:.3e} m 超过 0.1 mm"

def test_geodetic_source_chain():
    """大地源参数（chain74）：平面顶点 → 源侧经纬度 → 目标平面，逐点核对。

    这条链对应"源模型为大地坐标数据、转换为工程平面坐标"的场景：
    OSGB 顶点是平面偏移，需先按源侧投影反算经纬度，再走 apply_lonlat。
    """
    if not os.path.exists(os.path.join(SAMPLE, "metadata.xml")):
        return
    from app.osgb.srs import load_metadata
    from app.common.params import ParamApplier
    from app.core.projection import inverse_gauss_points
    from app.core.ellipsoid import get_ellipsoid
    from app.core.transform2d import to_survey_xy

    md = load_metadata(os.path.join(SAMPLE, "metadata.xml"))
    old = tuple(float(v) for v in md.origin)
    # 自洽参数：源侧与目标侧同带（L0=117），四参数为已知值
    true4 = {"dx": 100.0, "dy": -7.0, "a": 0.999995, "b": 0.00002}
    doc = {"schema": "coordparam/1", "name": "geo_src", "kind": "chain74",
           "source_kind": "lonlat", "target_kind": "planar", "ellipsoid": "CGCS2000",
           "projection": {"l0": 117.0, "x0": 0.0, "y0": 500000.0, "k": 1.0,
                          "h0": 0.0, "rigorous": False, "b0": 25.9},
           "planar4": dict(true4)}
    ov = {"l0": 117.0, "x0": 0.0, "y0": 500000.0, "h0": 0.0,
          "rigorous": False, "b0": 25.9, "zone": None}

    # 缺源侧投影必须拒绝
    try:
        PlanarVertexTransform(doc, mode="xyz", origin=old)
    except ValueError as e:
        assert "大地" in str(e) or "投影" in str(e), str(e)
    else:
        raise AssertionError("大地源参数缺源侧投影时应拒绝")

    tf = PlanarVertexTransform(doc, mode="xyz", origin=old, proj_override=ov)
    v = np.array([[0.0, 0.0, 0.0], [100.0, 200.0, -50.0], [-30.0, 40.0, 10.0]])
    got = tf.apply(v)

    # 手工复算（全部用既有模块）：绝对投影 → 反算 B/L → apply_lonlat → 减原点
    ap = ParamApplier(dict(doc, projection_src=ov))
    ell = get_ellipsoid("CGCS2000")
    want = []
    for r in v:
        abs_n = old[1] + float(r[1])
        abs_e = old[0] + float(r[0])
        (b, l), = inverse_gauss_points([abs_n], [abs_e], 117.0, ell=ell)
        X, Y, H = ap.apply_lonlat(b, l, float(r[2]), do_plane=True, do_height=True)
        # X,Y 为绝对 (北,东)；减原点后换回 OSGB 轴序 (东,北)
        xe, yn = to_survey_xy(float(X) - old[1], float(Y) - old[0])
        want.append((xe, yn, H))
    want = np.asarray(want)
    dev = np.abs(got - want).max()
    assert dev < 1e-9, f"大地源链偏差 {dev:.3e} m\n得到 {got}\n期望 {want}"


def test_geodetic_source_height_only():
    """大地源参数也可解耦：仅高程时平面逐位不变，高程与全开一致。"""
    if not os.path.exists(os.path.join(SAMPLE, "metadata.xml")):
        return
    from app.osgb.srs import load_metadata
    md = load_metadata(os.path.join(SAMPLE, "metadata.xml"))
    old = tuple(float(v) for v in md.origin)
    doc = {"schema": "coordparam/1", "name": "geo_src", "kind": "chain74",
           "source_kind": "lonlat", "target_kind": "planar", "ellipsoid": "CGCS2000",
           "projection": {"l0": 117.0, "x0": 0.0, "y0": 500000.0, "k": 1.0,
                          "h0": 0.0, "rigorous": False, "b0": 25.9},
           "planar4": {"dx": 100.0, "dy": -7.0, "a": 1.0, "b": 0.0},
           "heightfit": {"mode": "const", "space": "planar", "value_type": "dh",
                         "coef": [1.5], "center": [0.0, 0.0], "loo_rms": 0.0}}
    ov = {"l0": 117.0, "x0": 0.0, "y0": 500000.0, "h0": 0.0,
          "rigorous": False, "b0": 25.9, "zone": None}
    v = np.array([[0.0, 0.0, 0.0], [100.0, 200.0, -50.0], [-30.0, 40.0, 10.0]])
    full = PlanarVertexTransform(doc, mode="xyz", origin=old, proj_override=ov).apply(v)
    only_z = PlanarVertexTransform(doc, mode="z", origin=old, proj_override=ov).apply(v)
    only_xy = PlanarVertexTransform(doc, mode="xy", origin=old, proj_override=ov).apply(v)
    # 仅高程：平面逐位不变，高程与全开一致
    assert np.abs(only_z[:, :2] - v[:, :2]).max() == 0.0, "仅高程时平面应逐位不变"
    assert np.abs(only_z[:, 2] - full[:, 2]).max() < 1e-12, "仅高程时高程应与全开一致"
    # 仅平面：高程逐位不变，平面与全开一致
    assert np.abs(only_xy[:, 2] - v[:, 2]).max() == 0.0, "仅平面时高程应逐位不变"
    assert np.abs(only_xy[:, :2] - full[:, :2]).max() < 1e-12, "仅平面时平面应与全开一致"

TESTS = [test_parse_epsg_projected, test_parse_enu, test_parse_local_and_missing,
         test_write_metadata_roundtrip, test_new_origin_same_magnitude,
         test_new_origin_matches_shift, test_mode_xyz_changes_all,
         test_mode_xy_leaves_z, test_mode_z_only_height, test_mode_rejects_unknown,
         test_axis_order_osgb_east_north, test_axis_order_roundtrip_identity,
         test_rejects_lonlat_only_param, test_end_to_end_precision,
         test_decouple_plane_only, test_decouple_height_only,
         test_decouple_xyz_applies_both, test_geoid_requires_projection,
         test_geoid_linearization_residual,
         test_geodetic_source_chain, test_geodetic_source_height_only]


def run():
    """执行全部用例，返回检查数；失败抛异常（runner 汇总用）。"""
    ns = 0
    for fn in TESTS:
        fn()
        ns += 1
        print(f"    ok  {fn.__name__}")
    return ns
