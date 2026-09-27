"""照片POS页的规格构建（转换方式描述 / 平面回调 / 校验）。纯逻辑，无 Qt 依赖。"""
from __future__ import annotations


def plane_fn_from_param(pd: dict | None):
    """从参数文档构造平面回调 (lon, lat) -> (x北, y东)。

    需参数源为大地坐标且含平面转换；返回 (fn, 参数名, 错误消息)。
    """
    if not pd:
        return None, "", "请先在“平面转换参数”下拉选择参数" \
                         "（需源为大地坐标且含平面转换，如链式/直接投影参数）。"
    ok = (pd.get("source_kind") == "lonlat") and (pd.get("projection") or pd.get("planar4")
                                                  or (pd.get("kind") == "seven2d"))
    if not ok:
        return None, "", "所选参数不含大地→平面转换（需源为大地坐标且含投影/平面转换）。"
    from app.common.params import ParamApplier
    ap = ParamApplier(pd)
    fn = lambda lon, lat: ap.apply_lonlat(lat, lon, 0.0)[:2]  # noqa: E731
    return fn, str(pd.get("name", "")), None


def xmp_gate(rows) -> tuple[int, bool]:
    """预览统计 XMP 可识别数 → (数量, 复选框是否可用)。"""
    n_xmp = sum(1 for r in rows if r.get("xmp_ok"))
    return n_xmp, n_xmp > 0
