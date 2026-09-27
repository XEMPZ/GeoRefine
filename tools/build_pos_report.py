"""把 .cluster/pos-geoid-study/ 的 ch1/ch2/ch3 拼装为自包含 HTML 研究报告。

用法: python tools/build_pos_report.py
依赖: markdown
"""
from __future__ import annotations

from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / ".cluster" / "pos-geoid-study"
OUT = ROOT.parent / "DELIVERY"

CSS = """
body{font-family:'Microsoft YaHei UI','Microsoft YaHei',sans-serif;background:#f4f1ea;color:#1e2a32;
     margin:0;padding:40px 20px;line-height:1.9}
.wrap{max-width:980px;margin:0 auto}
header{border-bottom:3px solid #2a6f8e;padding-bottom:18px;margin-bottom:10px}
h1{font-size:26px;margin:0 0 6px}
.sub{color:#6b7a83;font-size:13px}
.metrics{display:flex;gap:14px;flex-wrap:wrap;margin:22px 0}
.metric{background:#fff;border:1px solid #d8d2c4;border-radius:10px;padding:14px 20px;min-width:170px}
.metric b{display:block;font-size:21px;color:#2a6f8e}
.metric span{font-size:12px;color:#6b7a83}
h2{font-size:19px;color:#2a6f8e;border-left:4px solid #2a6f8e;padding-left:11px;margin-top:44px}
h3{font-size:15.5px;color:#7a8b6f}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d8d2c4;margin:14px 0;font-size:13.5px}
th{background:#faf8f3;color:#6b7a83;padding:9px 11px;border-bottom:1px solid #d8d2c4;text-align:left}
td{padding:8px 11px;border-bottom:1px solid #eee;vertical-align:top}
tr:nth-child(even) td{background:#faf8f3}
code{background:#e9efe8;border:1px solid #d8d2c4;border-radius:4px;padding:1px 6px;font-family:Consolas,monospace;font-size:12.5px;color:#2a6f8e}
blockquote{border-left:4px solid #c96f4a;background:#fdf6f0;margin:12px 0;padding:8px 14px;color:#7a5b3f}
ul li,ol li{margin:5px 0}
.notice{border-left:4px solid #c96f4a;background:#fdf6f0;border-radius:0 8px 8px 0;padding:10px 16px;margin:16px 0;font-size:13.5px}
.foot{margin-top:44px;padding-top:14px;border-top:1px solid #d8d2c4;color:#6b7a83;font-size:12.5px}
"""

HEAD_METRICS = """
<div class="metrics">
  <div class="metric"><b>0.0135 m</b><span>移去-恢复检核 RMS（主线实验，≈纯格网 0.0138m）</span></div>
  <div class="metric"><b>0.028 m</b><span>无格网纯控制点拟合检核 RMS（对分布敏感）</span></div>
  <div class="metric"><b>0.28 m</b><span>单点固定差残留误差（ξ 面倾斜未吸收）</span></div>
  <div class="metric"><b>0.03~0.84 m</b><span>EGM96 vs EGM2008 中国 10 城市互差</span></div>
</div>
"""


def build():
    parts = []
    for ch, title in [("ch1", "一、可行性与原理分析"),
                      ("ch2", "二、精度对比与适用性分档"),
                      ("ch3", "三、风险、失败模式与实施建议")]:
        p = CH / f"{ch}.md"
        if not p.exists():
            raise FileNotFoundError(p)
        text = p.read_text(encoding="utf-8")
        # 去掉 writer 的完成标记行
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("<!-- ch"))
        body = markdown.markdown(text, extensions=["tables", "fenced_code"])
        parts.append(f"<h2>{title}</h2>\n{body}")
    body_all = "\n".join(parts)
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>似大地水准面精化法处理无人机照片 POS 高程：可行性与精度对比研究</title>
<style>{CSS}</style></head><body><div class="wrap">
<header>
<h1>似大地水准面精化法处理无人机照片 POS 高程<br>可行性研究与像控点方案精度对比</h1>
<div class="sub">2026-08-30 · 方法论证 + 文献调研（40 来源）+ 真实格网数值实验 · GeoRefine 工具链复现</div>
</header>
{HEAD_METRICS}
<div class="notice"><b>一句话结论：</b>技术上可行且是行业趋势，但"可行"有明确前提——
项目区有 cm 级精化似大地水准面格网（或以少量高程控制点做移去-恢复拟合），POS 为固定解并保留独立 RTK 检核点。
纯 POS + EGM2008 直转约 10~25 cm，只够 1:2000 级；有 cm 级格网后总误差回到 5~10 cm，与传统像控方案相当，
但方差更大；像控点吸收"端到端"误差的机制不可被 ξ 转换面替代，1:500 仍建议保留稀少高程像控。</div>
{body_all}
<div class="foot">实验可复现：geoid_suite/tools/exp_pos_height.py（EGM96/EGM2008 真实格网 + 样例数据）；
文献素材与 40 个来源：.cluster/pos-geoid-study/subagent_01.md；【经验口径】标注项为工程经验估计。</div>
</div></body></html>"""
    out = OUT / "POS高程似大地水准面精化研究报告.html"
    out.write_text(html, encoding="utf-8")
    print(f"[ok] {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    build()
