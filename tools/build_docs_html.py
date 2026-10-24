"""把 docs/ 下的 Markdown 手册渲染为自包含 HTML（纸色地图学风格）。

用法: python tools/build_docs_html.py
依赖: 无（装了 markdown 就用它，没装走内置的轻量渲染器，够本项目的文档用）
"""
from __future__ import annotations

import html
import re
from pathlib import Path

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


def _inline(text: str) -> str:
    """行内语法：先转义，再按顺序还原 `code` / **bold** / [链接](url)。"""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", lambda m: "<code>" + m.group(1) + "</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return out


def render(md: str) -> str:
    """极简 Markdown → HTML：标题/表格/代码块/列表/引用/分隔线/段落。"""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, _inline(m.group(2)), lvl))
            i += 1
            continue
        if s.startswith("---") and set(s) <= {"-"}:
            out.append("<hr>")
            i += 1
            continue
        if s.startswith("|") and i + 1 < n and set(lines[i + 1].strip().replace("|", "").replace(" ", "")) <= {"-", ":"}:
            head = [c.strip() for c in s.strip("|").split("|")]
            out.append("<table><thead><tr>" + "".join("<th>%s</th>" % _inline(c) for c in head) + "</tr></thead><tbody>")
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" + "".join("<td>%s</td>" % _inline(c) for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        if s.startswith("> "):
            buf = []
            while i < n and lines[i].strip().startswith("> "):
                buf.append(lines[i].strip()[2:])
                i += 1
            out.append("<blockquote>%s</blockquote>" % _inline(" ".join(buf)))
            continue
        if re.match(r"^[-*]\s+", s):
            buf = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                buf.append("<li>%s</li>" % _inline(re.sub(r"^[-*]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ul>" + "".join(buf) + "</ul>")
            continue
        buf = [s]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(#{1,4}\s|\||>|[-*]\s|```|---)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % _inline(" ".join(buf)))
    return "\n".join(out)


def build(md_path: Path, title: str) -> None:
    text = md_path.read_text(encoding="utf-8")
    try:
        import markdown  # 装了就用，渲染更完整
        body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        body = render(text)
    page = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<div class="meta">GeoRefine v1.2 · 由 tools/build_docs_html.py 生成 · 纸色地图学风格 · 自包含单文件</div>
{body}
</div></body></html>"""
    out = md_path.with_suffix(".html")
    out.write_text(page, encoding="utf-8")
    print(f"[ok] {out.name} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    build(DOCS / "用户手册.md", "GeoRefine 用户手册")
    build(DOCS / "交接文档.md", "GeoRefine 交接文档")
    build(DOCS / "技术手册.md", "GeoRefine 技术手册")
    build(DOCS / "审查报告-v1.2.md", "GeoRefine v1.2 代码审查报告")
