# -*- coding: utf-8 -*-
"""
ui.report —— 统一的“题目 + AI 回答 + 依据”卡片渲染

控制台刷题、错题库、考试侧边栏（tkinter/终端）共用这里的函数，
保证界面文案与格式一致，且**只输出文本，不写回网页**。
"""

from __future__ import annotations

from typing import Any, Sequence

LINE = "─" * 62


def render_question(q: Any, idx: int = 0, total: int = 0, dup: dict[str, Any] | None = None) -> str:
    head = f"第 {idx}/{total} 题" if total else f"题 {q.no or idx}"
    parts = [LINE, f"{head} ｜ {q.kind_label}",
             f"{q.stem}" + ("\n（含图片 {0} 张，图片内容需人工查看）".format(q.raw.get("img_count", "?"))
                            if q.has_image else "")]
    if q.option_texts:
        parts.append("\n".join("  " + t for t in q.option_texts))
    if getattr(q, "score", ""):
        parts.append(f"  分值：{q.score}")
    if dup:
        parts.append(f"  ! 重复题：题库中已有相似题（相似度 {dup.get('_similarity', '—')}，"
                     f"历史错 {dup.get('wrong_count', 0)} 次）")
    return "\n".join(p for p in parts if p)


def render_answer(ans: Any) -> str:
    """ans: ai.responder.Answer"""
    out = [
        f"【答案】 {ans.answer or '（未给出）'}",
        f"【解析】 {ans.analysis or '（未给出）'}",
        f"【知识点】 {ans.knowledge or '（未标注）'}",
    ]
    if ans.chapter:
        out.append(f"【所属章节】 {ans.chapter}")
    out.append(f"【资料依据】 {ans.evidence or '课程资料中没有找到直接依据'}")
    out.append(f"【可信程度】 {ans.confidence}")
    if ans.doubt:
        out.append(f"【需要人工检查】 {ans.doubt}")
    if ans.fabricated_citations:
        out.append(f"! 引用校验：AI 提到了不存在的资料编号 {ans.fabricated_citations}，已判定为无效引用")
    if ans.error:
        out.append(f"【AI 状态】 {ans.error}")
    return "\n".join(out)


def render_evidence(hits: Sequence[dict[str, Any]], title: str = "课程依据") -> str:
    if not hits:
        return f"【{title}】 本地课程资料中没有找到直接依据"
    lines = [f"【{title}】"]
    for i, h in enumerate(hits[:5], 1):
        text = (h.get("text") or "").strip().replace("\n", " ")
        if len(text) > 180:
            text = text[:180] + "…"
        lines.append(f"  [资料{i}] 《{h.get('title', '资料')}》{h.get('loc', '')}｜{h.get('kind', 'text')}｜{text}")
    return "\n".join(lines)


def render_wrong_refs(wrongs: Sequence[dict[str, Any]]) -> str:
    if not wrongs:
        return ""
    lines = ["【相似题/历史错题】"]
    for w in wrongs[:3]:
        lines.append(f"  · {w.get('stem', '')[:80]}  →  答案：{w.get('answer') or '未知'}"
                     f"（错 {w.get('wrong_count', 1)} 次，知识点：{w.get('knowledge') or '—'}）")
    return "\n".join(lines)


def full_card(q: Any, ans: Any, hits: Sequence[dict[str, Any]], wrongs: Sequence[dict[str, Any]],
              idx: int = 0, total: int = 0) -> str:
    blocks = [render_question(q, idx, total), render_answer(ans)]
    ev = render_evidence(hits)
    if ev:
        blocks.append(ev)
    wr = render_wrong_refs(wrongs)
    if wr:
        blocks.append(wr)
    return ("\n" + LINE + "\n").join(blocks) + f"\n{LINE}"