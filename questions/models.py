# -*- coding: utf-8 -*-
"""
questions.models —— 题目数据结构与题型/答案规范化

题型统一为：single(单选) / multi(多选) / judge(判断) / blank(填空) / short(简答) / unknown
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from utils.text import (
    fingerprint, one_line, strip_html, strip_option_prefix, strip_question_no, clean_text,
)

KIND_LABEL = {
    "single": "单选题", "multi": "多选题", "judge": "判断题", "blank": "填空题",
    "short": "简答题", "unknown": "未识别题型",
}
_KIND_ALIAS = {
    "radio": "single", "checkbox": "multi", "判断": "judge", "单选": "single", "多选": "multi",
    "填空": "blank", "简答": "short", "论述": "short", "计算": "short", "名词解释": "short",
    "问答": "short", "object": "single",
}
JUDGE_TRUE = {"对", "正确", "√", "T", "true", "yes", "y", "is_true", "1", "t"}
JUDGE_FALSE = {"错", "错误", "×", "x", "F", "false", "no", "n", "is_false", "0", "f"}


def normalize_kind(raw: str, text_hints: Iterable[str] = ()) -> str:
    s = str(raw or "").strip().lower()
    if s in KIND_LABEL and s != "unknown":
        return s          # unknown 不是结论，要继续靠题干文字推断
    if s in _KIND_ALIAS:
        return _KIND_ALIAS[s]
    blob = " ".join([s] + [one_line(str(t)) for t in text_hints])
    for key, val in _KIND_ALIAS.items():
        if key and key in blob:
            return val
    return "unknown"


def normalize_judge(text: str) -> str:
    t = one_line(text).strip("。.！! ").lower()
    if not t:
        return ""
    if t in JUDGE_TRUE or set(t) <= JUDGE_TRUE:
        return "正确"
    if t in JUDGE_FALSE or set(t) <= JUDGE_FALSE:
        return "错误"
    if any(x in t for x in ("正确", "对", "√", "true")):
        return "正确"
    if any(x in t for x in ("错误", "错", "×", "false")):
        return "错误"
    return one_line(text)


def letters_from(text: str) -> str:
    """从 “选 A、B” / “AB” / “（A）” 里抽出选项字母（大写、去重、排序）。"""
    t = one_line(text).upper()
    found = re.findall(r"\b([A-H])\b|[（(]([A-H])[)）]|([A-H])", t)
    letters = [x for tup in found for x in tup if x]
    return "".join(sorted(dict.fromkeys(letters)))


def answers_equal(kind: str, a: str, b: str) -> bool:
    """判断题是否等价（“对”=“正确”=“√”），选择题按字母集合比较。"""
    if a is None or b is None:
        return False
    if kind == "judge":
        return normalize_judge(a) == normalize_judge(b) and bool(normalize_judge(a))
    if kind in ("single", "multi"):
        la, lb = letters_from(a), letters_from(b)
        return bool(la) and la == lb
    norm = lambda s: re.sub(r"\s+", "", strip_html(str(s or "")))  # noqa: E731
    return norm(a).lower() == norm(b).lower() and bool(norm(a))


@dataclass
class Question:
    stem: str = ""
    kind: str = "unknown"
    no: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)
    my_answer: str = ""
    ref_answer: str = ""
    score: str = ""
    is_right: bool | None = None
    has_image: bool = False
    chapter_key: str = ""
    section_url: str = ""
    course_id: int | None = None
    analysis: str = ""
    knowledge: str = ""
    top: int = 0
    detect_by: str = ""
    in_view: bool = True           # 是否出现在当前视口（弹题判定用）
    modal: bool = False            # 是否位于弹窗/对话框容器内
    raw: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Question":
        opts: list[dict[str, Any]] = []
        for i, o in enumerate(raw.get("options") or []):
            if isinstance(o, dict):
                text = strip_option_prefix(o.get("text") or o.get("raw") or "")
                label = str(o.get("label") or chr(65 + i))
                opts.append({"label": label, "text": text, "checked": bool(o.get("checked")),
                             "value": str(o.get("value") or ""),
                             "id": str(o.get("id") or ""), "input_type": str(o.get("input_type") or ""),
                             "name": str(o.get("name") or ""),
                             "nidx": (int(o["nidx"]) if isinstance(o.get("nidx"), (int, float)) else -1),
                             "click_sel": str(o.get("click_sel") or "")})
            else:
                opts.append({"label": chr(65 + i), "text": strip_option_prefix(str(o)), "checked": False,
                             "value": "", "id": "", "input_type": "", "name": "", "nidx": -1})
        stem = clean_text(strip_html(raw.get("stem", "")))
        kind = normalize_kind(raw.get("kind", ""), [raw.get("kind_name", ""), stem[:60]])
        is_right = raw.get("is_right")
        return cls(
            stem=strip_question_no(stem) or stem,
            kind=kind,
            no=str(raw.get("no") or "").strip(),
            options=opts,
            my_answer=one_line(str(raw.get("my_answer") or "")),
            ref_answer=clean_text(str(raw.get("ref_answer") or "")),
            score=one_line(str(raw.get("score") or "")),
            is_right=None if is_right is None else bool(int(is_right)),
            has_image=bool(raw.get("has_image")),
            top=int(raw.get("top") or 0),
            detect_by=str(raw.get("detect_by") or ""),
            in_view=bool(raw.get("inView", True)),
            modal=bool(raw.get("modal", False)),
            raw=dict(raw),
        )

    # ------------------------------------------------------------ 属性/工具
    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def option_texts(self) -> list[str]:
        return [f"{o['label']}. {o['text']}".strip() for o in self.options]

    @property
    def fp(self) -> str:
        return fingerprint(self.stem, self.option_texts)

    @property
    def option_labels(self) -> str:
        return "/".join(o["label"] for o in self.options) or "-"

    @property
    def best_answer(self) -> str:
        """参考答案 > 我已选 > 空。"""
        return self.ref_answer or self.my_answer or ""

    @property
    def selected_labels(self) -> str:
        """当前页面上“已勾选”的选项字母集合(如 'AB'),用于幂等/自检比对。"""
        return "".join(o["label"] for o in self.options if o.get("checked"))

    def is_answered(self) -> bool:
        """这道题在当前页面上是否**可信地**已有作答(选项被勾 / 填空已填)——用于“跳过已作答”。

        防误判:若**所有**选项都被判“已选”(如类名含 checkbox 被误判),视为“未作答”——
        全选几乎不可能是真实答案,宁可重做也不要把未答题误跳过。
        """
        checked = [o for o in self.options if o.get("checked")]
        if self.options and checked and len(checked) < len(self.options):
            return True                       # 部分勾选=确实在作答
        # 全选项都“选中”是误判信号；或无选项时看填空
        for b in (self.raw or {}).get("blanks") or []:
            if str((b or {}).get("value") or "").strip():
                return True
        return False

    def prompt_options(self) -> str:
        return "\n".join(self.option_texts) if self.options else "（无选项）"

    def preview(self, width: int = 90) -> str:
        s = one_line(self.stem)
        return s if len(s) <= width else s[: width - 1] + "…"

    def to_row(self) -> dict[str, Any]:
        """写入数据库 questions 表所需的字典。"""
        return {
            "fp": self.fp, "kind": self.kind, "stem": self.stem, "options": self.option_texts,
            "answer": self.best_answer, "answer_source": "platform" if self.ref_answer else "",
            "analysis": self.analysis, "knowledge": self.knowledge,
            "chapter_key": self.chapter_key, "section_url": self.section_url,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "no": self.no, "kind": self.kind, "kind_name": self.kind_label, "stem": self.stem,
            "options": self.options, "option_texts": self.option_texts, "my_answer": self.my_answer,
            "ref_answer": self.ref_answer, "score": self.score, "fp": self.fp,
            "chapter_key": self.chapter_key, "section_url": self.section_url,
        }


# 答题前的“知情同意/承诺书”等不是题目；大题分组标题（如“二. 多选题（共1题）”）也不是题目。
NON_QUESTION_RE = re.compile(
    r"(我已完整知晓|我已知晓|我承诺|自愿遵守|我已阅读并同意|知情同意|诚信承诺书|承诺|开始学习|开始答题|同意并继续"
    r"|共\s*\d+\s*[题小问])")   # 末尾“共N题”是分组标题特征，真题分值是“N.N分”不会命中
QUESTION_LIKE_RE = re.compile(
    r"(？|（\s*）|\(\s*\)|_{2,}|以下|下列|关于|试述|简述|计算|求|判断|选择|填入|第.{1,4}题)")


def looks_like_question(stem: str, options: Sequence[str] = ()) -> bool:
    """题干是否像一道真题。"""
    s = one_line(stem)
    if not s or len(s) < 8:
        return False
    if NON_QUESTION_RE.search(s):
        return False
    if len(options) <= 1 and not QUESTION_LIKE_RE.search(s):
        return False
    return True

def dedupe_questions(items: Sequence[Question]) -> list[Question]:
    """同一次抓取里按指纹去重（跨 iframe 可能重复命中）。"""
    seen: set[str] = set()
    out: list[Question] = []
    for q in items:
        if not q.stem or q.fp in seen:
            continue
        if q.kind == "unknown":          # 整页正文/导航/侧边目录等被误框：未识别题型，直接丢
            continue
        if not looks_like_question(q.stem, q.option_texts):
            continue          # 承诺书/“开始学习”一类，丢弃
        seen.add(q.fp)
        out.append(q)
    return out