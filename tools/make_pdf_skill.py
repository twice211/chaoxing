# -*- coding: utf-8 -*-
"""
tools/make_pdf_skill.py —— 按已安装的 `pdf` 技能规范，把 Markdown 使用说明书生成 PDF

遵循 pdf 技能（~/.codex/skills/pdf/SKILL.md）的工作流：
  1) 用 reportlab 创建 PDF（本项目要求：中文字体嵌入，禁止 Unicode 上下标导致的黑块）；
  2) 用 pypdfium2 把页面渲染成 PNG 做视觉校验（技能要求"prefer visual checks"）；
  3) 用 pdfplumber/pypdf 做文本层抽查（不依赖它判断版式）。

用法：
    python tools/make_pdf_skill.py
    python tools/make_pdf_skill.py --md docs/使用说明.md --out docs/使用说明.pdf --preview 3
"""

from __future__ import annotations

import argparse
import html as _html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ----------------------------------------------------------------- 字体注册
FONT_CANDIDATES = {
    "regular": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\NotoSansSC-VF.ttf", r"C:\Windows\Fonts\simhei.ttf"],
    "bold": [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"],
    "mono": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc"],
}


def register_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    names = {}
    for role, paths in FONT_CANDIDATES.items():
        for path in paths:
            if not Path(path).exists():
                continue
            name = f"CN_{role}"
            try:
                kwargs = {"subfontIndex": 0} if Path(path).suffix.lower() == ".ttc" else {}
                pdfmetrics.registerFont(TTFont(name, path, **kwargs))
                names[role] = name
                break
            except Exception:
                continue
        else:
            names[role] = "Helvetica"      # 兜底：至少不崩溃
    return names


# ----------------------------------------------------------------- Markdown 解析
@dataclass
class Block:
    kind: str                      # h1|h2|h3|p|ul|ol|code|table|quote|hr
    payload: object


FENCE = re.compile(r"^\s*```")
TABLE_SEP = re.compile(r"^\s*\|?[\s:\-\|]+\|[\s:\-\|]*$")


def parse(md: str) -> list[Block]:
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if FENCE.match(raw):
            buf = []
            i += 1
            while i < len(lines) and not FENCE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(Block("code", "\n".join(buf)))
            continue
        if not line:
            i += 1
            continue
        if line in {"---", "***", "___"}:
            blocks.append(Block("hr", None))
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = min(len(m.group(1)), 3)
            blocks.append(Block(f"h{level}", m.group(2)))
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1].strip()):
            header = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells + [""] * (len(header) - len(cells)))
                i += 1
            blocks.append(Block("table", (header, [r[: len(header)] for r in rows])))
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(Block("quote", " ".join(b for b in buf if b)))
            continue
        m_ol = re.match(r"^\d+[\.、)]\s+(.*)$", line)
        m_ul = re.match(r"^[-*+]\s+(.*)$", line)
        if m_ol or m_ul:
            kind = "ol" if m_ol else "ul"
            items = []
            while i < len(lines):
                cur = lines[i].strip()
                mo = re.match(r"^\d+[\.、)]\s+(.*)$", cur)
                mu = re.match(r"^[-*+]\s+(.*)$", cur)
                hit = (mo or mu) if (mo if kind == "ol" else mu) else None
                if not hit:
                    break
                items.append((mo or mu).group(1))
                i += 1
            blocks.append(Block(kind, items))
            continue
        para = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or FENCE.match(lines[i]) or nxt.startswith(("#", ">", "|", "---"))
                    or re.match(r"^[-*+]\s", nxt) or re.match(r"^\d+[\.、)]\s", nxt)):
                break
            para.append(nxt)
            i += 1
        blocks.append(Block("p", " ".join(para)))
    return blocks


# ----------------------------------------------------------------- 样式与构建
def markup(text: str, fonts: dict) -> str:
    """把行内 `code`、**粗体** 转成 reportlab 的 XML 标签（先做 HTML 转义）。"""
    out = _html.escape(text)
    mono = fonts["mono"]
    out = re.sub(r"`([^`]+)`", lambda m: f'<font face="{mono}" color="#b02418">{m.group(1)}</font>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<i>\1</i>", out)
    return out


def build(blocks: list[Block], out: Path, fonts: dict, title: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, ListFlowable, ListItem, NextPageTemplate,
        PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    reg, bold, mono = fonts["regular"], fonts["bold"], fonts["mono"]
    brand = colors.HexColor("#0b3d91")
    S = {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=19, leading=25, textColor=brand,
                             spaceAfter=8, spaceBefore=0),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13.5, leading=19, textColor=brand,
                             spaceBefore=12, spaceAfter=5, leftIndent=0),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11.5, leading=16,
                             textColor=colors.HexColor("#1a56b8"), spaceBefore=9, spaceAfter=3),
        "p": ParagraphStyle("p", fontName=reg, fontSize=9.8, leading=15.4, alignment=TA_JUSTIFY,
                            spaceBefore=1.5, spaceAfter=4.5),
        "li": ParagraphStyle("li", fontName=reg, fontSize=9.8, leading=15.0, spaceAfter=2.5),
        "code": ParagraphStyle("code", fontName=mono, fontSize=8.4, leading=12.2,
                               textColor=colors.HexColor("#24292f")),
        "th": ParagraphStyle("th", fontName=bold, fontSize=9.0, leading=12.6, textColor=colors.white),
        "td": ParagraphStyle("td", fontName=reg, fontSize=8.8, leading=12.4),
        "quote": ParagraphStyle("quote", fontName=reg, fontSize=9.4, leading=14.4,
                                textColor=colors.HexColor("#4a3c00"), leftIndent=6, rightIndent=6,
                                spaceBefore=2, spaceAfter=2),
    }

    story = []
    for b in blocks:
        if b.kind == "h1":
            story.append(Paragraph(markup(str(b.payload), fonts), S["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.6, color=brand, spaceAfter=8))
        elif b.kind in ("h2", "h3"):
            pre = "▍" if b.kind == "h2" else ""
            story.append(Paragraph(f'{pre}{markup(str(b.payload), fonts)}', S[b.kind]))
            if b.kind == "h2":
                story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#c9d4e6"),
                                        spaceAfter=4))
        elif b.kind == "p":
            story.append(Paragraph(markup(str(b.payload), fonts), S["p"]))
        elif b.kind in ("ul", "ol"):
            items = [ListItem(Paragraph(markup(x, fonts), S["li"]), leftIndent=12) for x in b.payload]
            story.append(ListFlowable(items, bulletType="bullet" if b.kind == "ul" else "1",
                                      start="•" if b.kind == "ul" else 1, bulletFontName=reg,
                                      bulletFontSize=9, leftIndent=14, spaceAfter=5))
        elif b.kind == "code":
            text = _html.escape(str(b.payload)).replace("\n", "<br/>").replace("  ", "&nbsp; ")
            inner = Table([[Paragraph(text, S["code"])]], colWidths=[168 * mm])
            inner.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8fa")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dfe3e8")),
                ("LINEBEFORE", (0, 0), (0, -1), 2.6, brand),
                ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(inner)
            story.append(Spacer(1, 6))
        elif b.kind == "quote":
            inner = Table([[Paragraph(markup(str(b.payload), fonts), S["quote"])]], colWidths=[168 * mm])
            inner.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff8e6")),
                ("LINEBEFORE", (0, 0), (0, -1), 2.6, colors.HexColor("#e0a800")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(inner)
            story.append(Spacer(1, 5))
        elif b.kind == "table":
            header, rows = b.payload
            data = [[Paragraph(markup(c, fonts), S["th"]) for c in header]]
            for r in rows:
                data.append([Paragraph(markup(c, fonts), S["td"]) for c in r])
            total = 168.0
            n = len(header)
            weights = [1.0] * n
            if n == 3 and header[0].startswith("场景"):
                weights = [1.0, 2.0, 3.4]
            elif n == 2:
                weights = [1.15, 3.0]
            elif n == 3 and header[0] == "键":
                weights = [0.6, 2.2, 0.6, 2.2]
            elif n == 4 and header[0] == "键":
                weights = [0.55, 2.1, 0.55, 2.1]
            elif n == 3 and header[0] == "配置":
                weights = [1.5, 1.0, 3.1]
            elif n == 2 and header[0] == "路径":
                weights = [1.4, 3.0]
            elif n == 2:
                weights = [1.2, 3.0]
            s = sum(weights)
            widths = [total * w / s * mm for w in weights]
            tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), brand),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d4e6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 7))
        elif b.kind == "hr":
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#d8dee7"),
                                    spaceBefore=6, spaceAfter=6))

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont(reg, 7.8)
        canvas.setFillColor(colors.HexColor("#8a9099"))
        canvas.drawString(14 * mm, A4[1] - 10 * mm, _html.unescape(title))
        canvas.drawRightString(A4[0] - 14 * mm, A4[1] - 10 * mm, "学习通课程助手 · 使用说明")
        canvas.setStrokeColor(colors.HexColor("#dfe3e8"))
        canvas.line(14 * mm, A4[1] - 12 * mm, A4[0] - 14 * mm, A4[1] - 12 * mm)
        canvas.drawCentredString(A4[0] / 2, 9 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    doc = BaseDocTemplate(str(out), pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                          topMargin=17 * mm, bottomMargin=15 * mm, title=title,
                          author="study_helper", creator="tools/make_pdf_skill.py (reportlab)")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decorate)])
    doc.build(story)


# ----------------------------------------------------------------- 视觉校验（技能要求）
def preview(pdf: Path, prefix: Path, pages: int) -> list[Path]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    out = []
    for idx in range(min(pages, len(doc))):
        bmp = doc[idx].render(scale=1.9)
        path = prefix.parent / f"{prefix.stem}_p{idx + 1}.png"
        bmp.to_pil().save(str(path))
        out.append(path)
    return out


def text_check(pdf: Path) -> str:
    import pdfplumber

    with pdfplumber.open(str(pdf)) as book:
        first = (book.pages[0].extract_text() or "")[:120].replace("\n", " / ")
        return f"{len(book.pages)} 页；首页文本：{first}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Markdown → PDF（reportlab，按 pdf 技能规范）")
    ap.add_argument("--md", default="docs/使用说明.md")
    ap.add_argument("--out", default="docs/使用说明.pdf")
    ap.add_argument("--preview", type=int, default=2, help="渲染前 N 页为 PNG 做视觉校验，0 表示跳过")
    args = ap.parse_args(argv)

    md_path = (ROOT / args.md) if not Path(args.md).is_absolute() else Path(args.md)
    out_path = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    if not md_path.exists():
        print(f"[错误] 找不到 {md_path}")
        return 2
    md = md_path.read_text(encoding="utf-8")
    title = re.sub(r"^#\s*", "", md.splitlines()[0]).strip() or "使用说明"

    fonts = register_fonts()
    print(f"[字体] 正文={fonts['regular']} 粗体={fonts['bold']} 等宽={fonts['mono']}")
    blocks = parse(md)
    kinds: dict[str, int] = {}
    for b in blocks:
        kinds[b.kind] = kinds.get(b.kind, 0) + 1
    print(f"[解析] {len(blocks)} 个内容块：{kinds}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    build(blocks, out_path, fonts, title)
    print(f"[完成] PDF： {out_path}（{out_path.stat().st_size / 1024:.1f} KB）")
    print(f"[文本] {text_check(out_path)}")

    if args.preview > 0:
        pngs = preview(out_path, out_path.with_suffix("").parent / f"preview_{out_path.stem}", args.preview)
        for p in pngs:
            print(f"[校验] 渲染页面图：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())