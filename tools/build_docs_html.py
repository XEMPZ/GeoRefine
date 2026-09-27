"""把 docs/ 下的 Markdown 手册渲染为自包含 HTML（Stamen 地图学风格）。

用法: python tools/build_docs_html.py
依赖: markdown（pip install markdown）
"""
from __future__ import annotations

from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

CSS = """
body{font-family:'Microsoft YaHei UI','Microsoft YaHei',sans-serif;background:#f4f1ea;color:#1e2a32;
     margin:0;padding:36px 20px;line-height:1.85}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:26px;border-bottom:3px solid #2a6f8e;padding-bottom:10px;margin-top:0}
h2{font-size:19px;color:#2a6f8e;border-left:4px solid #2a6f8e;padding-left:10px;margin-top:38px}
h3{font-size:15px;color:#7a8b6f}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d8d2c4;margin:14px 0;font-size:13.5px}
th{background:#faf8f3;color:#6b7a83;padding:9px 10px;border-bottom:1px solid #d8d2c4;text-align:left}
td{padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top}
tr:nth-child(even) td{background:#faf8f3}
code{background:#e9efe8;border:1px solid #d8d2c4;border-radius:4px;padding:1px 6px;font-size:12.5px;
     font-family:Consolas,monospace;color:#2a6f8e}
pre{background:#1e2a32;color:#e8eef4;border-radius:8px;padding:14px 16px;overflow-x:auto}
pre code{background:transparent;color:#a8f0dc;border:none}
blockquote{border-left:4px solid #c96f4a;background:#fdf6f0;margin:12px 0;padding:8px 14px;color:#7a5b3f}
.meta{color:#6b7a83;font-size:12.5px;margin-bottom:26px}
ul li{margin:4px 0}
strong{color:#1e2a32}
"""


def build(md_path: Path, title: str):
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body><div class="wrap">
<div class="meta">GeoRefine v1.0 · 生成于交付打包 · 纸色地图学风格 · 自包含单文件</div>
{body}
</div></body></html>"""
    out = md_path.with_suffix(".html")
    out.write_text(html, encoding="utf-8")
    print(f"[ok] {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    build(DOCS / "用户手册.md", "GeoRefine 用户手册")
    build(DOCS / "交接文档.md", "GeoRefine 交接文档")
