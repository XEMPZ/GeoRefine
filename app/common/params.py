"""参数库与参数应用（主线维护）。

参数 JSON schema 见 ARCHITECTURE.md §3（coordparam/1）。
坐标约定：planar=(x北, y东)；lonlat 一律 (lon, lat) 序。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np


class ParamLibrary:
    """参数库：params/ 目录下的 coordparam/1 JSON 文件集合。"""

    def __init__(self, dir_path: str | Path = "params"):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r'[\\/:*?"<>|\s]+', "_", str(name)).strip("_") or "未命名参数"

    def _path_of(self, name: str) -> Path:
        return self.dir / (self._safe_name(name) + ".json")

    def save(self, param: dict) -> str:
        param = dict(param)
        param.setdefault("created", datetime.now().isoformat(timespec="seconds"))
        p = self._path_of(param.get("name", "未命名参数"))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(param, f, ensure_ascii=False, indent=2)
        return str(p)

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                # 损坏的参数文件仍要出现在列表里（可见、可删除），而不是静默消失
                out.append({"name": p.stem + "（损坏）", "kind": "损坏", "created": "",
                            "rms_xy_m": None, "rms_h_m": None, "n_points": None,
                            "file": str(p)})
                continue
            try:
                d = d if isinstance(d, dict) else {}
                acc = d.get("accuracy", {}) or {}
                out.append({
                    "name": d.get("name", p.stem),
                    "kind": d.get("kind", "?"),
                    "created": d.get("created", ""),
                    "rms_xy_m": acc.get("rms_xy_m"),
                    "rms_h_m": acc.get("rms_h_m") or acc.get("loo_rms_h_m"),
                    "n_points": acc.get("n_points"),
                    "file": str(p),
                })
            except Exception:
                continue
        return out

    def load(self, name: str) -> dict:
        p = self._path_of(name)
        if not p.exists():
            # 允许直接传文件路径
            pp = Path(name)
            if pp.exists():
                p = pp
            else:
                raise FileNotFoundError(f"参数不存在: {name}")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"参数文件损坏（{p.name}）：{e}") from e
        if not isinstance(d, dict) or not d:
            raise ValueError(f"参数文件损坏（{p.name}）：内容为空或不是参数对象")
        return d

    def delete(self, name: str) -> bool:
        p = self._path_of(name)
        if p.exists():
            p.unlink()
            return True
        return False

    def import_file(self, path: str | Path) -> str:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.save(d)

    def export_file(self, name: str, path: str | Path) -> str:
        d = self.load(name)
        Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)


class ParamApplier:
    """按 coordparam/1 参数 dict 应用转换（依赖 app.core，由核心模块实现）。"""

    def __init__(self, param: dict):
        from app.core import ellipsoid as _ell  # 延迟导入

        if not isinstance(param, dict) or not param:
            raise ValueError("参数文档为空或损坏")
        self.param = param
        self.kind = param.get("kind")
        self.ell = (_ell.get_ellipsoid(param.get("ellipsoid", "CGCS2000"))
                    or _ell.CGCS2000)
        self.p4 = param.get("planar4")
        self.p3d = param.get("planar3")
        self.p7 = param.get("seven")
        self.p3 = param.get("three")
        self.proj = param.get("projection")
        self.hf = param.get("heightfit")
        # 参数完整性校验：声明了某类转换但缺数据 → 明确报错（防 KeyError 与静默错算）
        for key, piece in (("planar4", self.p4), ("planar3", self.p3d),
                           ("seven", self.p7), ("three", self.p3),
                           ("projection", self.proj), ("heightfit", self.hf)):
            if piece is not None and not isinstance(piece, dict):
                raise ValueError(f"参数不完整：{key} 数据损坏")
        if self.p7 is not None and not {"dx", "dy", "dz", "rx_s", "ry_s", "rz_s", "scale_ppm"} <= set(self.p7):
            raise ValueError("参数不完整：seven 缺少必要字段")
        if self.p4 is not None and not {"dx", "dy", "a", "b"} <= set(self.p4):
            raise ValueError("参数不完整：planar4 缺少必要字段")
        if self.p3d is not None and not {"dx", "dy"} <= set(self.p3d):
            raise ValueError("参数不完整：planar3 缺少必要字段")
        if self.p3 is not None and not {"dx", "dy", "dz"} <= set(self.p3):
            raise ValueError("参数不完整：three 缺少必要字段")
        if self.hf is not None and not {"mode", "coef"} <= set(self.hf):
            raise ValueError("参数不完整：heightfit 缺少必要字段")
        # 多项式参数（poly2d / poly3d）：poly 文档即模型本体，重建时即校验
        self.poly_doc = param.get("poly")
        self._poly = None
        if self.poly_doc is not None:
            if self.kind not in ("poly2d", "poly3d"):
                raise ValueError("参数不完整：poly 仅用于 poly2d/poly3d 类型")
            from app.logic.polynomial import PolyModel
            self._poly = PolyModel.from_doc(self.poly_doc)

    # ---- 内部 ----
    def _apply_planar4(self, x, y):
        from app.core.transform2d import apply_4param, Param4

        p = Param4(self.p4["dx"], self.p4["dy"], self.p4["a"], self.p4["b"])
        return apply_4param(p, x, y)

    def _apply_seven_blh(self, lat, lon, h):
        from app.core import ellipsoid as _ell
        from app.core.transform3d import Param7, apply_7param

        x, y, z = _ell.geodetic_to_ecef(lat, lon, h, self.ell)
        p = Param7(self.p7["dx"], self.p7["dy"], self.p7["dz"],
                   self.p7["rx_s"], self.p7["ry_s"], self.p7["rz_s"], self.p7["scale_ppm"])
        x2, y2, z2 = apply_7param(p, x, y, z)
        return _ell.ecef_to_geodetic(x2, y2, z2, self.ell)

    def _apply_three_blh(self, lat, lon, h):
        from app.core import ellipsoid as _ell
        from app.core.transform3d import Param3, apply_3param

        x, y, z = _ell.geodetic_to_ecef(lat, lon, h, self.ell)
        p = Param3(self.p3["dx"], self.p3["dy"], self.p3["dz"])
        x2, y2, z2 = apply_3param(p, x, y, z)
        return _ell.ecef_to_geodetic(x2, y2, z2, self.ell)

    def _apply_gk(self, lat, lon):
        import math as _m

        from app.core.gk_engine import ProjParams
        from app.core.projection import gauss_kruger

        pj = self.proj or {"l0": round(lon / 3) * 3, "x0": 0.0, "y0": 500000.0, "k": 1.0}
        h0 = float(pj.get("h0", 0.0) or 0.0)
        rig = bool(pj.get("rigorous", False))
        if h0 or rig:
            # 投影面大地高/严密椭球：走 gk_engine 的有效椭球路径（与控制点解算同源）
            pp = ProjParams(self.ell.a, self.ell.inv_f,
                            _m.radians(float(pj.get("l0"))),
                            _m.radians(float(pj.get("b0", 30.0))),
                            h0, float(pj.get("k", 1.0) or 1.0),
                            x0_km=float(pj.get("x0", 0.0)) / 1000.0,
                            y0_km=float(pj.get("y0", 500000.0)) / 1000.0,
                            rigorous=rig)
            return pp.forward(_m.radians(float(lat)), _m.radians(float(lon)))
        return gauss_kruger(lat, lon, pj.get("l0"), ell=self.ell,
                            x0=pj.get("x0", 0.0), y0=pj.get("y0", 500000.0), k=pj.get("k", 1.0))

    def _eval_hf(self, cx, cy):
        from app.core.heightfit import eval_height, HeightFit

        f = HeightFit(mode=self.hf["mode"], value_type=self.hf.get("value_type", "xi"),
                      space=self.hf.get("space", "planar"),
                      coef=np.asarray(self.hf["coef"], dtype=float),
                      center=tuple(self.hf.get("center", (0.0, 0.0))),
                      loo_rms=self.hf.get("loo_rms"), note=self.hf.get("note", ""))
        return float(eval_height(f, cx, cy))

    def _eval_poly2(self, pt):
        from app.logic.polynomial import eval_poly
        return eval_poly(pt, self._poly)[:2]

    def _eval_poly3(self, pt):
        from app.logic.polynomial import eval_poly
        vals = eval_poly(pt, self._poly)
        if len(vals) < 3:
            raise ValueError("多项式参数损坏：目标分量不足 3 个（三维模型）")
        return vals[:3]

    # ---- 对外 ----
    # ---- 多项式（poly2d/poly3d）----
    def _poly_ell(self, key):
        from app.core.ellipsoid import get_ellipsoid
        e = get_ellipsoid(self.param.get(key) or self.param.get("ellipsoid") or "CGCS2000")
        return e or self.ell

    def _poly_src_point(self, v1, v2, h, src_is_lonlat):
        """模型输入点：平面输入 + 模型空间=lonlat → 按源侧投影反算为大地（度）。

        含带号 y 在参数 projection_src.zone 里记录，此处剥除。
        """
        if src_is_lonlat or self.param.get("source_kind") != "lonlat":
            return float(v1), float(v2), float(h)
        psrc = self.param.get("projection_src") or {}
        if not psrc.get("l0"):
            raise ValueError("参数需源侧投影参数（中央子午线）才能把平面坐标反算为大地坐标")
        from app.core.projection import inverse_gauss_points
        ell_s = self._poly_ell("ellipsoid_src")
        zone = psrc.get("zone")
        y_in = float(v2) - float(zone) * 1_000_000 if zone else float(v2)
        (b, l), = inverse_gauss_points([float(v1)], [y_in], float(psrc["l0"]), ell_s,
                                       x0_m=float(psrc.get("x0", 0.0) or 0.0),
                                       y0_m=float(psrc.get("y0", 500000.0) or 500000.0),
                                       h0=float(psrc.get("h0", 0.0) or 0.0),
                                       rigorous=bool(psrc.get("rigorous", False)))
        return b, l, float(h)   # 度

    def _poly_proj_forward(self, b_deg, l_deg):
        """模型输出大地（度）→ 目标平面（按目标侧投影 + 目标椭球）。"""
        import math as _m

        from app.core.gk_engine import ProjParams
        pj = self.param.get("projection") or {}
        ell_d = self._poly_ell("ellipsoid_dst")
        pp = ProjParams(ell_d.a, ell_d.inv_f, _m.radians(float(pj["l0"])),
                        _m.radians(float(pj.get("b0", 30.0) or 30.0)),
                        float(pj.get("h0", 0.0) or 0.0), float(pj.get("k", 1.0) or 1.0),
                        x0_km=float(pj.get("x0", 0.0) or 0.0) / 1000.0,
                        y0_km=float(pj.get("y0", 500000.0) or 500000.0) / 1000.0,
                        rigorous=bool(pj.get("rigorous", False)))
        return pp.forward(_m.radians(float(b_deg)), _m.radians(float(l_deg)))

    def _poly_height(self, vals, s_b, s_l, s_x, s_y, h_in):
        """多项式高程链：格网先换算 ξ（基准高=源h−ξ），曲面拟合剩余趋势（两者可叠加）；
        多项式第三分量与其余互斥（保存端保证）；无勾选 → None（透传）。"""
        fl = self.param.get("height_flags")
        if not isinstance(fl, dict) or not fl:
            # 旧参数无字段：三维多项式默认高程=第三分量（历史行为）；二维模型无第三分量
            hm = self.param.get("height_mode",
                                "poly" if self.kind == "poly3d" else "none")
            fl = {"poly": hm == "poly",
                  "surface": hm in ("surface", "geoid+surface"),
                  "geoid": hm in ("geoid", "geoid+surface")}
        if fl.get("poly"):
            if len(vals) < 3:
                raise ValueError("参数损坏：勾选了多项式高程但模型无第三分量")
            return float(vals[2])
        base = float(h_in)
        used = False
        if fl.get("geoid"):
            grid = self._poly_grid()
            if grid is None:
                raise ValueError(f"大地水准面模型“{self.param.get('geoid_grid')}”不可用")
            if s_b is None:
                raise ValueError("大地水准面高程需要源侧经纬度（源为大地坐标或填写源侧投影）")
            xi = grid.try_undulation(float(s_l), float(s_b))
            if xi is None:
                g = grid.grid
                raise ValueError(f"源坐标超出格网 {g.name} 范围")
            base -= float(xi)                             # 基准高 = 源大地高 − ξ
            used = True
        if fl.get("surface") and self.hf:
            space = self.hf.get("space", "planar")
            cx, cy = (s_l, s_b) if space == "lonlat" else (s_x, s_y)
            v = self._eval_hf(cx, cy)
            if v is not None:
                base += v                                 # 剩余高差趋势（dh 相对基准高）
            used = True
        return base if used else None                    # 无勾选 → 透传

    def _require_grid(self, name):
        """取参数耦合绑定的大地水准面格网；不可用 → 明确报错拒算（绝不静默换模型/跳过）。"""
        from app.core.geoid import GridModel, available_models, load_grid
        models_dir = Path(__file__).resolve().parents[2] / "models"
        for m in available_models(models_dir):
            if m["name"] == name and m["usable"]:
                return GridModel(load_grid(m["path"]))
        raise ValueError(
            f"该参数与大地水准面模型「{name}」耦合绑定（高程=源大地高−ξ格网），"
            "models/ 目录中找不到此可用模型，无法按原口径计算，已停止。"
            "请把该 gtx 放入 models/ 目录，或改用不含格网的参数。")

    def _poly_grid(self):
        name = self.param.get("geoid_grid")
        if not name:
            return None
        if getattr(self, "_grid_cache", None) is not None:
            return self._grid_cache
        from app.core.geoid import GridModel, available_models, load_grid
        models_dir = Path(__file__).resolve().parents[2] / "models"
        grid = None
        for m in available_models(models_dir):
            if m["name"] == name and m["usable"]:
                grid = GridModel(load_grid(m["path"]))
                break
        self._grid_cache = grid
        return grid

    def _apply_poly(self, v1, v2, h, do_plane, do_height, src_is_lonlat):
        """多项式统一应用：源空间归算 → 模型求值 → 目标投影 → 高程模式。"""
        from app.logic.polynomial import eval_poly
        sb, sl, sh = self._poly_src_point(v1, v2, h, src_is_lonlat)
        pt = (sb, sl, sh)[:self._poly.ndim]
        vals = eval_poly(pt, self._poly)
        o1 = o2 = None
        t_b = t_l = None
        if not do_plane:
            o1, o2 = float(v1), float(v2)              # 平面不转换 → 原值透传
        elif self.param.get("target_kind") == "lonlat":
            t_b, t_l = float(vals[0]), float(vals[1])  # 模型输出大地（度）
            pj = self.param.get("projection") or {}
            o1, o2 = (self._poly_proj_forward(t_b, t_l) if pj.get("l0") is not None
                      else (t_b, t_l))                 # 无目标投影 → 输出度值
        else:
            o1, o2 = float(vals[0]), float(vals[1])
        h2 = None
        if do_height:
            h2 = self._poly_height(vals, sb, sl, o1, o2, float(h))
        if h2 is None:
            h2 = float(h)
        return o1, o2, h2

    def apply_lonlat(self, lat, lon, h, do_plane=True, do_height=True):
        """大地坐标 → 目标平面(正常高)。按 seven→投影→四参数→高程拟合 链式执行。

        do_plane/do_height：选择应用参数中的平面部分/高程部分；未勾选部分原值透传。
        poly（模型空间=大地）：一次求值，目标侧可再过投影。
        """
        if self.kind in ("poly2d", "poly3d") and self._poly is not None:
            if self.param.get("source_kind") != "lonlat":
                raise ValueError("该参数的多项式源为平面坐标，不能用于大地坐标输入")
            return self._apply_poly(lat, lon, h, do_plane, do_height, src_is_lonlat=True)
        lat2, lon2, h2 = lat, lon, h
        if do_plane:
            if self.p7:
                lat2, lon2, h2 = self._apply_seven_blh(lat, lon, h)
            elif self.p3:
                lat2, lon2, h2 = self._apply_three_blh(lat, lon, h)
            if self.param.get("direct_deg") and self.p4 and not self.p7:
                # 直接经纬度四参数（未投影）：以 (B,L) 度值作为平面输入
                x, y = self._apply_planar4(lat2, lon2)
            elif not (self.proj or self.p4):
                if self.kind == "geoid" or self.param.get("geoid_grid"):
                    x, y = lat, lon   # 纯高程参数（格网耦合）：平面无转换可做，原值透传
                else:
                    raise ValueError("参数不含平面转换（无投影/四参数），无法进行平面计算")
            else:
                x, y = self._apply_gk(lat2, lon2)
                if self.p4:
                    x, y = self._apply_planar4(x, y)
        else:
            x, y = lat, lon  # 透传（大地坐标原值）
        if do_height:
            # 格网耦合：保存口径为 目标h = 源大地高 − ξ(源经纬度) [+趋势]，基准是源侧 h
            gname = self.param.get("geoid_grid")
            if gname:
                grid = self._require_grid(gname)
                xi = grid.try_undulation(float(lon), float(lat))
                if xi is None:
                    g = grid.grid
                    raise ValueError(
                        f"点 ({float(lat):.4f}, {float(lon):.4f}) 超出格网「{gname}」覆盖范围"
                        f"（lat {g.lat_min:.2f}~{g.lat_max:.2f}, lon {g.lon_min:.2f}~{g.lon_max:.2f}）："
                        "该参数与此格网耦合绑定，不能换用其他模型或外推，已停止")
                h2 = float(h) - float(xi)     # 重建高程链（不经七参数的大地高平移）
            if self.hf:
                space = self.hf.get("space", "planar")
                # 趋势面自变量与拟合口径一致：lonlat 空间用源经纬度
                cx, cy = (lon, lat) if space == "lonlat" else (x, y)
                v = self._eval_hf(cx, cy)
                if v is not None:
                    h2 = h2 - v if self.hf.get("value_type", "xi") == "xi" else h2 + v
        return x, y, h2

    def apply_planar(self, x, y, h, do_plane=True, do_height=True):
        """平面(正常高) → 目标平面(正常高)。二维七参数/四参数 + 高程拟合（value_type=dh）。

        do_plane/do_height：选择应用参数中的平面部分/高程部分；未勾选部分原值透传。
        二维七参数（seven2d）为三维模型，不可拆分：强制平面+高程同时应用。
        poly2d：仅平面多项式，高程透传（可与 heightfit 叠加）。
        poly3d（源=平面）：平面+高程整体模型；高程由多项式给出，不再叠加 heightfit。
        """
        if self.kind in ("poly2d", "poly3d") and self._poly is not None:
            # 模型空间=大地 且填了源侧投影 → 平面输入先反算成源大地（自动链）
            if self.param.get("source_kind") == "lonlat" and not (self.param.get("projection_src") or {}).get("l0"):
                raise ValueError("该参数的多项式源为大地坐标且未配源侧投影，不能用于平面坐标输入")
            return self._apply_poly(x, y, h, do_plane, do_height, src_is_lonlat=False)
        if do_height and self.param.get("geoid_grid"):
            raise ValueError(
                f"该参数与大地水准面模型「{self.param.get('geoid_grid')}」耦合绑定，"
                "高程需要源大地坐标（经纬度）插值 ξ；平面坐标输入无法按原口径计算，已停止")
        seven2d = self.kind == "seven2d" and self.p7
        if seven2d:
            # 二维七参数（布尔莎平面退化）：参数在重心化坐标系上解算，应用时先减重心
            from app.core.transform3d import Param7, apply_7param
            p7 = Param7(self.p7["dx"], self.p7["dy"], self.p7["dz"],
                        self.p7["rx_s"], self.p7["ry_s"], self.p7["rz_s"], self.p7["scale_ppm"])
            cx, cy = (self.p7.get("center") or [0.0, 0.0])
            x3, y3, z3 = apply_7param(p7, x - cx, y - cy, h)
            x2, y2, h2 = x3 + cx, y3 + cy, z3
        elif self.p3d and do_plane:
            x2, y2 = x + float(self.p3d["dx"]), y + float(self.p3d["dy"])   # 三参数（仅平移）
            h2 = h
        elif do_plane and self.p4:
            x2, y2 = self._apply_planar4(x, y)
            h2 = h
        elif do_plane and not (self.p3d or self.p7):
            raise ValueError("参数不含平面转换（无四参数/二维七参数/三参数）")
        else:
            x2, y2 = self._apply_planar4(x, y) if (do_plane and self.p4) else (x, y)
            h2 = h
        if do_height and self.hf:
            space = self.hf.get("space", "planar")
            if space == "planar":
                v = self._eval_hf(x2, y2)
            else:  # lonlat 空间的拟合参数无法用于纯平面输入，跳过
                v = None
            if v is not None:
                h2 = h2 - v if self.hf.get("value_type", "xi") == "xi" else h2 + v
        return x2, y2, h2

    def apply_planar_to_lonlat(self, x, y, h, do_plane=True):
        """平面(工程坐标) → 目标大地坐标（逆向链 inv_direct/inv_chain3/inv_chain）。

        源投影反算（projection_src，含带号自动剥离/投影面高/严密椭球）→ 三/七参数。
        返回 (B, L, H)——B/L 为弧度（与 apply_points 的 rad_to_dms 输出衔接），H 为大地高。
        """
        import math as _m

        from app.core.projection import inverse_gauss_points
        if self.kind not in ("inv_direct", "inv_chain3", "inv_chain"):
            raise ValueError("该参数不含平面→大地的逆向转换链（inv_*）")
        psrc = self.param.get("projection_src") or {}
        if not isinstance(psrc, dict) or "l0" not in psrc:
            raise ValueError("参数不完整：projection_src 缺失或损坏")
        l0_s = float(psrc["l0"])
        zone = psrc.get("zone")
        y_in = float(y) - float(zone) * 1_000_000 if zone else float(y)
        (b, l), = inverse_gauss_points([float(x)], [y_in], l0_s, ell=self.ell,
                                       x0_m=float(psrc.get("x0", 0.0) or 0.0),
                                       y0_m=float(psrc.get("y0", 500000.0) or 500000.0),
                                       h0=float(psrc.get("h0", 0.0) or 0.0),
                                       rigorous=bool(psrc.get("rigorous", False)))
        # b/l 已是十进制度（inverse_gauss_points 口径）
        if self.kind == "inv_direct" or not do_plane:
            return _m.radians(b), _m.radians(l), float(h)
        from app.core.ellipsoid import geodetic_to_ecef, ecef_to_geodetic
        from app.core.transform3d import Param7, apply_7param, Param3, apply_3param
        X, Y, Z = geodetic_to_ecef(b, l, h, self.ell)
        if self.kind == "inv_chain":
            p7 = Param7(self.p7["dx"], self.p7["dy"], self.p7["dz"],
                        self.p7["rx_s"], self.p7["ry_s"], self.p7["rz_s"],
                        self.p7["scale_ppm"])
            X2, Y2, Z2 = apply_7param(p7, X, Y, Z)
        else:
            p3 = Param3(self.p3["dx"], self.p3["dy"], self.p3["dz"])
            X2, Y2, Z2 = apply_3param(p3, X, Y, Z)
        b2, l2, h2 = ecef_to_geodetic(X2, Y2, Z2, self.ell)   # 度
        return _m.radians(b2), _m.radians(l2), h2

    def eval_height_value(self, cx, cy):
        """求高程拟合值 v（无拟合参数返回 None）。cx,cy 顺序须与参数 space 匹配。"""
        if not self.hf:
            return None
        return self._eval_hf(cx, cy)


def seven2d_to_param4(param: dict, z: float = 0.0):
    """把 seven2d 参数换算为严格等价的四参数（供 CAD/SHP 的矩阵/回调路径使用）。

    seven2d = 布尔莎平面退化（含重心化），在 z=常数 时平面映射为严格仿射相似；
    系数通过对已验证的 apply_7param 三点数值采样取得，不引入任何新模型。
    返回 Param4(dx, dy, a, b)；非相似（理论上不可能）时抛 ValueError 防御。
    """
    import numpy as np

    from app.core.transform2d import Param4
    from app.core.transform3d import Param7, apply_7param

    p7d = param.get("seven") or {}
    p7 = Param7(p7d["dx"], p7d["dy"], p7d["dz"],
                p7d["rx_s"], p7d["ry_s"], p7d["rz_s"], p7d["scale_ppm"])
    cx, cy = (p7d.get("center") or [0.0, 0.0])
    # 在重心化坐标 ±10km 展布的 3 点上数值解仿射 X'=Axx·u+Axy·v+C：采样点拉开 4 个量级，
    # 把大坐标输出（~2.9e6）的浮点量化噪声对系数的影响压到 ~1e-14（1m 间距差分会到 1e-9）
    S = 10000.0
    us = np.array([-S, S, -S])
    vs = np.array([-S, -S, S])
    x2, y2, _z2 = apply_7param(p7, us, vs, np.full(3, float(z)))
    x2 = np.asarray(x2, dtype=float)
    y2 = np.asarray(y2, dtype=float)
    Axx = (x2[1] - x2[0]) / (2 * S)
    Axy = (x2[2] - x2[0]) / (2 * S)
    Ayx = (y2[1] - y2[0]) / (2 * S)
    Ayy = (y2[2] - y2[0]) / (2 * S)
    scale = max(abs(Axx), abs(Ayy), 1e-12)
    if abs(Axx - Ayy) > 1e-6 * scale or abs(Axy + Ayx) > 1e-6 * scale:
        raise ValueError("seven2d 未退化为平面相似变换（参数异常），拒绝换算为四参数")
    # 还原到原始坐标：X'(x,y) = Axx·(x-cx)+Axy·(y-cy)+C_x + cx
    Cx = x2[0] - Axx * us[0] - Axy * vs[0] + cx
    Cy = y2[0] - Ayx * us[0] - Ayy * vs[0] + cy
    # 测量系四参数：x2 = dx + a·x − b·y ; y2 = dy + b·x + a·y
    return Param4(float(Cx - Axx * cx - Axy * cy), float(Cy - Ayx * cx - Ayy * cy),
                  float(Axx), float(Ayx))
