"""精度对比：点对文件加载 + 转换参数残差评估（纯逻辑，无 Qt 依赖）。

点对来源：
- 手簿 .cot：源=GNSS 大地(L,B,H)，目标=施工平面(x,y,正常高)；
- CASS .txt：旧(x,y,h) → 新(x,y,h)，两组平面；
- 对比点 .csv：点名,经度,纬度,H_ell,H_normal —— 无平面目标，仅高程对比（格网/拟合 vs 已知 ζ）。

评估：所选转换参数把"源"变换为预测值，残差 = 预测 − 目标（平面 dx/dy；高程 dh）。
参数可含平面转换、高程拟合、大地水准面格网（geoid_grid，可无高程拟合=纯格网参数）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PairSet:
    names: list
    src: np.ndarray            # (n,2) 源坐标：lonlat=(经度,纬度) / planar=(x北,y东)
    src_h: np.ndarray          # 源高程（大地高或正常高，随点对文件）
    dst: np.ndarray | None     # (n,2) 目标平面坐标；无则 None
    dst_h: np.ndarray          # 目标高程
    src_kind: str              # lonlat / planar
    dst_kind: str | None       # planar / None
    source: str = ""
    plane_idx: list | None = None   # 参与平面对比的点下标（cot use_pos）；None=全部
    h_idx: list | None = None       # 参与高程对比的点下标（cot use_h）；None=全部


def load_pairs(path) -> PairSet:
    """按扩展名/内容加载点对文件（cot / CASS txt / 对比点 csv）。"""
    from pathlib import Path

    from app.common import pair_io
    from app.common.io_csv import read_points_csv

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{p}")
    if p.suffix.lower() == ".cot":
        pts = pair_io.parse_cot(p)
        if not pts:
            raise ValueError("cot 文件中没有有效点对")
        return PairSet(
            names=[q.name for q in pts],
            src=np.array([[q.l_deg, q.b_deg] for q in pts], dtype=float),
            src_h=np.array([q.h_ell for q in pts], dtype=float),
            dst=np.array([[q.x, q.y] for q in pts], dtype=float),
            dst_h=np.array([q.h_normal for q in pts], dtype=float),
            src_kind="lonlat", dst_kind="planar",
            source="手簿 .cot（源=GNSS 大地坐标，目标=施工平面坐标）",
            plane_idx=[i for i, q in enumerate(pts) if q.use_pos] or None,
            h_idx=[i for i, q in enumerate(pts) if q.use_h] or None)
    if pair_io.looks_like_cass(p):
        pairs = pair_io.parse_cass(p)
        if not pairs:
            raise ValueError("CASS 文件中没有有效点对")
        return PairSet(
            names=[f"P{i+1}" for i in range(len(pairs))],
            src=np.array([[q.x1, q.y1] for q in pairs], dtype=float),
            src_h=np.array([q.h1 for q in pairs], dtype=float),
            dst=np.array([[q.x2, q.y2] for q in pairs], dtype=float),
            dst_h=np.array([q.h2 for q in pairs], dtype=float),
            src_kind="planar", dst_kind="planar",
            source="南方 CASS 公共点文件（旧平面 → 新平面）")
    if pair_io.looks_like_south_pairs(p):
        sp = pair_io.parse_south_pairs(p)
        if not sp:
            raise ValueError("南方公共点对文件中没有有效数据行")
        return PairSet(
            names=[q.name for q in sp],
            src=np.array([[q.x1, q.y1] for q in sp], dtype=float),
            src_h=np.array([q.h1 for q in sp], dtype=float),
            dst=np.array([[q.x2, q.y2] for q in sp], dtype=float),
            dst_h=np.array([q.h2 for q in sp], dtype=float),
            src_kind="planar", dst_kind="planar",
            source="南方坐标转换软件公共点对文件（旧x,y,h → 新x,y,h）")
    # 对比点 CSV：点名,经度,纬度,H_ell,H_normal
    rows = read_points_csv(str(p))
    pts = [(r.get("name"), float(r["L"]), float(r["B"]), float(r["H_ell"]), float(r["H"]))
           for r in rows if r.get("L") is not None and r.get("B") is not None]
    if not pts:
        raise ValueError("CSV 中没有有效对比点（需列：点名,经度(L),纬度(B),H_ell,H_normal）")
    return PairSet(
        names=[n or f"P{i+1}" for i, (n, _l, _b, _he, _hn) in enumerate(pts)],
        src=np.array([[l, b] for _n, l, b, _he, _hn in pts], dtype=float),
        src_h=np.array([he for _n, _l, _b, he, _hn in pts], dtype=float),
        dst=None,
        dst_h=np.array([hn for _n, _l, _b, _he, hn in pts], dtype=float),
        src_kind="lonlat", dst_kind=None,
        source="对比点 CSV（源=大地坐标+大地高，目标=正常高；仅高程对比）")


def _stats(res: np.ndarray) -> dict:
    a = np.abs(res)
    return {"rms": float(np.sqrt(np.mean(res ** 2))),
            "mean": float(np.mean(a)),
            "max": float(np.max(a))}


def _load_grid_by_name(name: str, models_dir, cache: dict):
    if name in cache:
        return cache[name]
    from app.core.geoid import GridModel, available_models, load_grid

    model = None
    for m in available_models(models_dir):
        if m["name"] == name and m["usable"]:
            model = GridModel(load_grid(m["path"]))
            break
    cache[name] = model
    return model


def evaluate(pairs: PairSet, doc: dict, do_plane: bool, do_height: bool,
             models_dir, grid_cache: dict | None = None) -> dict:
    """单个转换参数对点对集的残差评估。"""
    from app.common.params import ParamApplier

    grid_cache = grid_cache if grid_cache is not None else {}
    name = doc.get("name", "?")
    row = {"name": name, "kind": doc.get("kind", "?"),
           "feasible_plane": False, "feasible_h": False, "n_plane": 0, "n_h": 0,
           "res_xy": None, "res_h": None, "fails_plane": 0, "fails_h": 0,
           "rms_xy": None, "mean_xy": None, "max_xy": None,
           "rms_h": None, "mean_h": None, "max_h": None, "note": ""}
    notes = []
    ap = ParamApplier(doc)
    grid = None
    if doc.get("geoid_grid"):
        grid = _load_grid_by_name(doc["geoid_grid"], models_dir, grid_cache)
        if grid is None:
            notes.append(f"格网模型“{doc['geoid_grid']}”不可用")

    # ---- 平面残差 ----
    if do_plane:
        if pairs.dst is None:
            notes.append("点对文件无目标平面坐标，无法对比平面")
        elif doc.get("source_kind") != pairs.src_kind:
            notes.append(f"参数源类型({doc.get('source_kind')})与点对源({pairs.src_kind})不符")
        elif pairs.dst_kind == "planar" and doc.get("target_kind") != "planar":
            notes.append("参数不含到平面坐标的转换")
        else:
            idx = pairs.plane_idx or list(range(len(pairs.src)))
            res, used, fails = [], 0, 0
            for i in idx:
                try:
                    if pairs.src_kind == "lonlat":
                        x2, y2, _h2 = ap.apply_lonlat(float(pairs.src[i, 1]),
                                                      float(pairs.src[i, 0]),
                                                      float(pairs.src_h[i]),
                                                      do_plane=True, do_height=False)
                    else:
                        x2, y2, _h2 = ap.apply_planar(float(pairs.src[i, 0]),
                                                      float(pairs.src[i, 1]),
                                                      float(pairs.src_h[i]),
                                                      do_plane=True, do_height=False)
                    res.append((x2 - pairs.dst[i, 0], y2 - pairs.dst[i, 1]))
                    used += 1
                except Exception:  # noqa: BLE001
                    fails += 1
            if used == 0:
                notes.append("平面转换全部点失败")
            else:
                r = np.array(res, dtype=float)
                d = np.hypot(r[:, 0], r[:, 1])
                row.update(feasible_plane=True, n_plane=used, res_xy=r, fails_plane=fails,
                           rms_xy=float(np.sqrt(np.mean(d ** 2))),
                           mean_xy=float(np.mean(d)),
                           max_xy=float(np.max(d)))
                if fails:
                    notes.append(f"{fails} 点平面转换失败")

    # ---- 高程残差 ----
    if do_height:
        hf = doc.get("heightfit")
        has_grid = bool(doc.get("geoid_grid")) and grid is not None
        if doc.get("geoid_grid") and grid is None:
            notes.append(f"格网模型“{doc['geoid_grid']}”不可用，高程无法按该参数计算")
        elif hf:
            space = hf.get("space", "planar")
            if space != pairs.src_kind:
                notes.append(f"参数高程拟合空间({space})与点对源({pairs.src_kind})不符")
            elif pairs.dst_h is None:
                notes.append("点对文件无目标高程")
            else:
                idx = pairs.h_idx or list(range(len(pairs.src)))
                res, used, fails = [], 0, 0
                for i in idx:
                    try:
                        if pairs.src_kind == "lonlat":
                            v = ap.eval_height_value(float(pairs.src[i, 0]), float(pairs.src[i, 1]))
                        else:
                            v = ap.eval_height_value(float(pairs.src[i, 0]), float(pairs.src[i, 1]))
                        base = (pairs.src_h[i] + v if hf.get("value_type", "xi") == "dh"
                                else pairs.src_h[i] - v)
                        if has_grid:
                            xi = grid.try_undulation(float(pairs.src[i, 0]), float(pairs.src[i, 1]))
                            if xi is None:
                                fails += 1
                                continue
                            base -= xi
                        res.append(base - pairs.dst_h[i])
                        used += 1
                    except Exception:  # noqa: BLE001
                        fails += 1
                if used == 0:
                    notes.append("高程转换全部点失败")
                else:
                    r = np.array(res, dtype=float)
                    row.update(feasible_h=True, n_h=used, res_h=r, fails_h=fails,
                               rms_h=float(np.sqrt(np.mean(r ** 2))),
                               mean_h=float(np.mean(np.abs(r))),
                               max_h=float(np.max(np.abs(r))))
                    if fails:
                        notes.append(f"{fails} 点高程转换失败")
        elif has_grid:
            if pairs.src_kind != "lonlat":
                notes.append("大地水准面格网需源为大地坐标")
            elif pairs.dst_h is None:
                notes.append("点对文件无目标高程")
            else:
                idx = pairs.h_idx or list(range(len(pairs.src)))
                res, used, fails = [], 0, 0
                for i in idx:
                    xi = grid.try_undulation(float(pairs.src[i, 0]), float(pairs.src[i, 1]))
                    if xi is None:
                        fails += 1
                        continue
                    res.append((pairs.src_h[i] - xi) - pairs.dst_h[i])
                    used += 1
                if used == 0:
                    notes.append("高程转换全部点超出格网范围")
                else:
                    r = np.array(res, dtype=float)
                    row.update(feasible_h=True, n_h=used, res_h=r, fails_h=fails,
                               rms_h=float(np.sqrt(np.mean(r ** 2))),
                               mean_h=float(np.mean(np.abs(r))),
                               max_h=float(np.max(np.abs(r))))
                    if fails:
                        notes.append(f"{fails} 点超出格网范围")
        else:
            notes.append("参数不含高程转换（无高程拟合/大地水准面）")

    if notes:
        row["note"] = "；".join(notes)
    return row


def compare(pairs: PairSet, docs: list[tuple[str, dict]], do_plane: bool, do_height: bool,
            models_dir) -> tuple[list[dict], str]:
    """多参数对比，返回 (行列表, 结论文本)。损坏/异常参数不会抛出，按不可用行呈现。"""
    rows, grid_cache = [], {}
    for name, doc in docs:
        try:
            if not isinstance(doc, dict) or not doc:
                raise ValueError("参数内容为空或损坏")
            rows.append(evaluate(pairs, doc, do_plane, do_height, models_dir, grid_cache))
        except Exception as e:  # noqa: BLE001
            rows.append({"name": name, "kind": "?", "feasible_plane": False,
                         "feasible_h": False, "n_plane": 0, "n_h": 0,
                         "res_xy": None, "res_h": None, "fails_plane": 0, "fails_h": 0,
                         "rms_xy": None, "mean_xy": None, "max_xy": None,
                         "rms_h": None, "mean_h": None, "max_h": None,
                         "note": f"参数无法使用：{e}"})
    return rows, verdict(rows, do_plane, do_height)


def verdict(rows: list[dict], do_plane: bool, do_height: bool) -> str:
    """结论：按勾选的对比项给出更优参数与建议。"""
    texts = []
    if len({r["name"] for r in rows}) < len(rows):
        texts.append("注意：选择了相同的参数，对比无意义")
    if do_plane:
        feas = [r for r in rows if r["feasible_plane"]]
        if len(feas) >= 2:
            best = min(feas, key=lambda r: r["rms_xy"])
            others = [r for r in feas if r is not best]
            gap = min(r["rms_xy"] - best["rms_xy"] for r in others)
            texts.append(f"坐标对比：{best['name']} 更优（RMS平面 {best['rms_xy']:.4f} m，"
                         f"领先 {gap:.4f} m）")
        elif len(feas) == 1:
            texts.append(f"坐标对比：仅 {feas[0]['name']} 可用（RMS平面 {feas[0]['rms_xy']:.4f} m）")
        else:
            texts.append("坐标对比：无可用的平面转换参数")
    if do_height:
        feas = [r for r in rows if r["feasible_h"]]
        if len(feas) >= 2:
            best = min(feas, key=lambda r: r["rms_h"])
            others = [r for r in feas if r is not best]
            gap = min(r["rms_h"] - best["rms_h"] for r in others)
            texts.append(f"高程对比：{best['name']} 更优（RMS高程 {best['rms_h']:.4f} m，"
                         f"领先 {gap:.4f} m）")
        elif len(feas) == 1:
            texts.append(f"高程对比：仅 {feas[0]['name']} 可用（RMS高程 {feas[0]['rms_h']:.4f} m）")
        else:
            texts.append("高程对比：无可用的高程转换参数")
    # 综合建议
    if do_plane and do_height:
        feas_p = [r for r in rows if r["feasible_plane"]]
        feas_h = [r for r in rows if r["feasible_h"]]
        if feas_p and feas_h:
            bp = min(feas_p, key=lambda r: r["rms_xy"])
            bh = min(feas_h, key=lambda r: r["rms_h"])
            if bp is bh:
                texts.append(f"综合建议：使用「{bp['name']}」（坐标与高程均更优）")
            else:
                texts.append(f"综合建议：平面用「{bp['name']}」、高程用「{bh['name']}」"
                             "（两参数各有所长，可分别取用）")
    return "；".join(texts) if texts else "无对比结果"
