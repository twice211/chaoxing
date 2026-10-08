# -*- coding: utf-8 -*-
"""
tools/make_docs.py —— 把 Markdown 使用说明书转成 HTML 与 PDF

原理：
1. 先用内置的轻量 Markdown 解析（支持标题/表格/代码块/列表/引用/行内加粗与代码）
   生成带 A4 打印样式的 HTML；
2. 再用 Playwright 的无头 Chromium 打印成 PDF（中文使用系统微软雅黑，无需额外字体）。

用法：
    python tools/make_docs.py                     # 生成 docs/使用说明.html 与 docs/使用说明.pdf
    python tools/make_docs.py --md README.md --out docs/技术说明.pdf
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
       font-size: 10.5pt; line-height: 1.62; color: #1f2329; margin: 0; }
h1 { font-size: 21pt; color: #0b3d91; border-bottom: 3px solid #0b3d91; padding-bottom: 8px;
     margin: 0 0 14px; page-break-before: avoid; }
h2 { font-size: 14.5pt; color: #0b3d91; margin: 22px 0 8px; padding-left: 8px;
     border-left: 5px solid #0b3d91; page-break-after: avoid; }
h3 { font-size: 12pt; color: #1a56b8; margin: 16px 0 6px; page-break-after: avoid; }
p { margin: 6px 0; }
hr { border: 0; border-top: 1px dashed #c2c8d0; margin: 14px 0; }
ul, ol { margin: 6px 0 6px 22px; padding: 0; }
li { margin: 3px 0; }
code { font-family: Consolas, "Courier New", monospace; background: #f2f4f7; color: #b02418;
       padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }
pre { background: #f7f8fa; border: 1px solid #dfe3e8; border-left: 4px solid #0b3d91; border-radius: 4px;
      padding: 9px 11px; overflow-x: auto; page-break-inside: avoid; }
pre code { background: none; color: #24292f; padding: 0; font-size: 9pt; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9.5pt;
        page-break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 5px 8px; vertical-align: top; text-align: left; }
th { background: #eaf1fb; color: #0b3d91; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
blockquote { margin: 8px 0; padding: 8px 12px; background: #fff8e6; border-left: 4px solid #e0a800;
             color: #4a3c00; page-break-inside: avoid; }
blockquote p { margin: 2px 0; }
.tip { color: #6a737d; font-size: 9pt; }
.page-footer { text-align: center; color: #8a9099; font-size: 8.5pt; margin-top: 18px; }
"""

CODE_FENCE = re.compile(r"^\s*```(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?[\s:\-\|]+\|[\s:\-\|]*$")
INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*([^*\n]+)\*(?!\w)"), r"<em>\1</em>"),
]


def inline(text: str) -> str:
    out = html.escape(text)
    for pat, rep in INLINE:
        out = pat.sub(rep, out)
    return out


def md_to_html(md: str, title: str = "使用说明") -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    body: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    list_kind = ""          # 'ul' | 'ol' | ''

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            body.append(f"</{list_kind}>")
            list_kind = ""

    while i < len(lines):
        line = lines[i]
        fence = CODE_FENCE.match(line)
        if fence:
            if not in_code:
                close_list()
                in_code, code_buf = True, []
            else:
                body.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
                in_code = False
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        stripped = line.strip()
        if not stripped:
            close_list()
            i += 1
            continue

        if stripped in {"---", "***", "___"}:
            close_list()
            body.append("<hr/>")
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close_list()
            level = min(len(m.group(1)), 3)
            body.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1].strip()):
            close_list()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = ["<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in header) + "</tr></thead><tbody>"]
            for row in rows:
                cells = row + [""] * (len(header) - len(row))
                table.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells[: len(header)]) + "</tr>")
            table.append("</tbody></table>")
            body.append("".join(table))
            continue

        if stripped.startswith(">"):
            close_list()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            body.append("<blockquote><p>" + inline(" ".join(q for q in quote if q)) + "</p></blockquote>")
            continue

        m_ol = re.match(r"^(\d+)[\.、)]\s+(.*)$", stripped)
        m_ul = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m_ol or m_ul:
            kind = "ol" if m_ol else "ul"
            if list_kind != kind:
                close_list()
                body.append(f"<{kind}>")
                list_kind = kind
            text = (m_ol.group(2) if m_ol else m_ul.group(1)) or ""
            body.append(f"<li>{inline(text)}</li>")
            i += 1
            continue

        if stripped.startswith("*") and stripped.endswith("*") and len(stripped) > 2 and not stripped.startswith("**"):
            close_list()
            body.append(f"<p class='tip'>{inline(stripped.strip('*'))}</p>")
            i += 1
            continue

        close_list()
        para = [stripped]
        while i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if (not nxt or CODE_FENCE.match(lines[i + 1]) or nxt.startswith(("#", ">", "|", "- ", "* ", "---"))
                    or re.match(r"^\d+[\.、)]\s", nxt)):
                break
            para.append(nxt)
            i += 1
        body.append("<p>" + inline(" ".join(para)) + "</p>")
        i += 1

    if in_code and code_buf:
        body.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
    close_list()
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        + "\n".join(body)
        + "<div class='page-footer'>本文档由 tools/make_docs.py 自动生成，可用浏览器打开后自行打印</div>"
        "</body></html>"
    )


def to_pdf(html_path: Path, pdf_path: Path) -> None:
    """用 Playwright 无头 Chromium 打印 PDF（页脚带页码）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"未安装 Playwright：{exc}\n请先执行：python -m pip install playwright && python -m playwright install chromium")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.goto(html_path.as_uri(), wait_until="load")
        page.wait_for_timeout(250)
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                "<div style='width:100%;font-size:8px;color:#8a9099;text-align:center;"
                "font-family:Microsoft YaHei,sans-serif'>"
                "<span class='pageNumber'></span> / <span class='totalPages'></span></div>"
            ),
        )
        browser.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Markdown 说明书 → HTML/PDF")
    ap.add_argument("--md", default="docs/使用说明.md", help="源 Markdown 路径")
    ap.add_argument("--html", default="", help="输出 HTML 路径（默认同名 .html）")
    ap.add_argument("--out", default="", help="输出 PDF 路径（默认同名 .pdf）")
    ap.add_argument("--no-pdf", action="store_true", help="只生成 HTML，不调用 Chromium")
    args = ap.parse_args(argv)

    md_path = (ROOT / args.md) if not Path(args.md).is_absolute() else Path(args.md)
    if not md_path.exists():
        print(f"[错误] 找不到源文件：{md_path}")
        return 2
    md = md_path.read_text(encoding="utf-8")
    title = re.sub(r"^#\s*", "", md.splitlines()[0]).strip() if md.strip() else "使用说明"
    html_path = Path(args.html) if args.html else md_path.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(md_to_html(md, title), encoding="utf-8")
    print(f"[完成] HTML：{html_path}")

    if args.no_pdf:
        return 0
    pdf_path = Path(args.out) if args.out else md_path.with_suffix(".pdf")
    to_pdf(html_path, pdf_path)
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(str(pdf_path)).pages)
    except Exception:
        pages = -1
    size = pdf_path.stat().st_size
    print(f"[完成] PDF： {pdf_path}（{pages if pages > 0 else '?'} 页，{size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())