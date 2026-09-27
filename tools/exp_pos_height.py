"""POS 高程处理方案精度量化实验（用 GeoRefine 真实格网与工具链）。

实验 A：中国主要城市 EGM96 vs EGM2008 高程异常差异（全球模型互差 = 模型不确定度下界参考）
实验 B：移去-恢复实证（样例数据，H_norm 真值 = H_ell − ξ_局部格网 + 2cm 噪声）：
        方案1 纯 EGM96 / 方案2 纯 EGM2008 / 方案3 纯局部格网 /
        方案4 移去-恢复（EGM96 移去 + 10 点平面拟合残差 + 5 点检核）/
        方案5 纯控制点二次曲面拟合（无格网，10 拟合 5 检核）/ 方案6 单点固定差
输出：.cluster/pos-geoid-study/exp_results.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # geoid_suite/
sys.path.insert(0, str(ROOT))

from app.core.geoid import GridModel, load_grid  # noqa: E402
from app.core.heightfit import eval_height, fit_height  # noqa: E402
from app.common.io_csv import read_points_csv  # noqa: E402

OUT = ROOT.parent / ".cluster" / "pos-geoid-study"
OUT.mkdir(parents=True, exist_ok=True)

CITIES = [
    ("北京", 116.40, 39.90), ("上海", 121.47, 31.23), ("广州", 113.26, 23.13),
    ("武汉", 114.31, 30.59), ("西安", 108.94, 34.34), ("成都", 104.07, 30.57),
    ("乌鲁木齐", 87.62, 43.83), ("拉萨", 91.11, 29.66), ("哈尔滨", 126.63, 45.75),
    ("海口", 110.32, 20.03),
]


def main():
    lines = ["# POS 高程处理方案精度量化实验", ""]

    # ---------- 实验 A ----------
    g96 = GridModel(load_grid(ROOT / "models" / "egm96_15.gtx"))
    g08 = GridModel(load_grid(ROOT / "models" / "us_nga_egm08_25.tif"))
    lines += ["## 实验 A：中国主要城市 EGM96 vs EGM2008 高程异常（ξ）对比", "",
              "| 城市 | 经度 | 纬度 | ξ_EGM96 (m) | ξ_EGM2008 (m) | 互差 (m) |",
              "|---|---|---|---|---|---|"]
    diffs = []
    for name, lon, lat in CITIES:
        a = g96.undulation(lon, lat)
        b = g08.undulation(lon, lat)
        diffs.append(b - a)
        lines.append(f"| {name} | {lon:.2f} | {lat:.2f} | {a:+.3f} | {b:+.3f} | {b - a:+.3f} |")
    d = np.array(diffs)
    lines += ["", f"互差统计：均值 {d.mean():+.3f} m，范围 [{d.min():+.3f}, {d.max():+.3f}] m，"
              f"标准差 {d.std(ddof=1):.3f} m", ""]
    print("\n".join(lines[-14:]))

    # ---------- 实验 B ----------
    pts = read_points_csv(str(ROOT / "sample_data" / "精度对比样本.csv"))
    lon = np.array([p["L"] for p in pts])
    lat = np.array([p["B"] for p in pts])
    h_ell = np.array([p["H_ell"] for p in pts])
    h_norm = np.array([p["H"] for p in pts])
    n = len(pts)
    fit_idx = np.arange(0, 10)   # 前 10 点拟合
    chk_idx = np.arange(10, 15)  # 后 5 点检核

    xi96 = np.array([g96.undulation(lo, la) for lo, la in zip(lon, lat)])
    xi08 = np.array([g08.undulation(lo, la) for lo, la in zip(lon, lat)])

    local = load_grid(str(ROOT / "sample_data" / "局部似大地水准面格网_样例.csv"))
    mlocal = GridModel(local)
    xi_lo = np.array([mlocal.undulation(lo, la) for lo, la in zip(lon, lat)])

    def rms(res):
        r = np.asarray(res, dtype=float)
        return float(np.sqrt(np.mean(r ** 2))), float(np.max(np.abs(r)))

    # 检核点残差 = 转换后正常高 − 真值正常高
    schemes = {}
    schemes["方案1 纯 EGM96（无控制点）"] = (h_ell[chk_idx] - xi96[chk_idx]) - h_norm[chk_idx]
    schemes["方案2 纯 EGM2008（无控制点）"] = (h_ell[chk_idx] - xi08[chk_idx]) - h_norm[chk_idx]
    schemes["方案3 纯局部精化格网（无控制点）"] = (h_ell[chk_idx] - xi_lo[chk_idx]) - h_norm[chk_idx]
    # 方案4 移去-恢复：EGM96 移去 + 拟合点残差平面 + 恢复
    res_fit = (h_ell[fit_idx] - xi96[fit_idx]) - h_norm[fit_idx]   # = ξ96 − ξ真 + 噪声
    hf = fit_height(np.column_stack([lon[fit_idx], lat[fit_idx]]), res_fit,
                    "plane", "dh", "lonlat", "last")
    v_chk = eval_height(hf, lon[chk_idx], lat[chk_idx])
    schemes["方案4 移去-恢复（EGM96+10点平面拟合）"] = (h_ell[chk_idx] - xi96[chk_idx] - v_chk) - h_norm[chk_idx]
    # 方案5 纯控制点二次曲面拟合（无格网）
    xi_true_at_fit = h_ell[fit_idx] - h_norm[fit_idx]              # 真 ξ + 噪声
    hf5 = fit_height(np.column_stack([lon[fit_idx], lat[fit_idx]]), xi_true_at_fit,
                     "quadratic", "xi", "lonlat", "last")
    v5 = eval_height(hf5, lon[chk_idx], lat[chk_idx])
    schemes["方案5 纯控制点二次曲面拟合（10点拟合）"] = (h_ell[chk_idx] - v5) - h_norm[chk_idx]
    # 方案6 单点固定差
    schemes["方案6 单点固定差（1点）"] = ((h_ell[chk_idx] - xi96[chk_idx])
                                  - (h_ell[fit_idx[0]] - xi96[fit_idx[0]]) + h_norm[fit_idx[0]]) - h_norm[chk_idx]

    lines += ["## 实验 B：移去-恢复实证（15 点样例，10 点拟合 / 5 点检核，ξ 真值=局部格网+2cm 噪声）", "",
              "| 方案 | 检核点数 | 检核 RMS (m) | 检核最大误差 (m) |",
              "|---|---|---|---|"]
    for name, res in schemes.items():
        r, mx = rms(res)
        lines.append(f"| {name} | {len(res)} | {r:.4f} | {mx:.4f} |")
    # 拟合点内符合
    r_fit = res_fit - eval_height(hf, lon[fit_idx], lat[fit_idx])
    r5_fit = xi_true_at_fit - eval_height(hf5, lon[fit_idx], lat[fit_idx])
    lines += ["", f"方案4 拟合点内符合 RMS：{np.sqrt(np.mean(r_fit**2)):.4f} m",
              f"方案5 拟合点内符合 RMS：{np.sqrt(np.mean(r5_fit**2)):.4f} m（6 参数拟 10 点）",
              "", f"说明：检核 RMS ≈ max(模型与真值 ξ 面差异, 2cm 注入噪声)。局部格网方案 ≈2cm 即噪声下限；",
              "移去-恢复把全球模型的系统差吸收进拟合面后逼近噪声下限；纯全球模型残留 EGM96 与真值面的系统差。"]

    text = "\n".join(lines)
    (OUT / "exp_results.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\n[ok] 结果已写入 {OUT / 'exp_results.md'}")


if __name__ == "__main__":
    main()
