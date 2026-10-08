# -*- coding: utf-8 -*-
"""
utils.text —— 文本规范、指纹去重、中英文混合分词

题库去重和知识库检索都依赖这里的两个基础能力：
1. fingerprint：把题干+选项压成“标准化指纹”，用于检测重复题。
2. tokenize：中文用字符二元组（可选 jieba），英文/数字用单词，供 BM25 使用。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Sequence

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v\u3000]+")
_PUNCT_RE = re.compile(
    r"[，。、；：！？,.;:!?'\"“”‘’（）()\[\]【】{}<>《》|/\\~`@#$%^&*_+=—…—\-\u2014]+"
)
_NUM_PREFIX_RE = re.compile(r"^\s*(?:\d+[\.、)）]|[（(][一二三四五六七八九十\d]+[)）])\s*")
_OPTION_PREFIX_RE = re.compile(r"^\s*(?:[A-Ha-h][\.、\)）]|[（(][ABCDEFGHabcdefgh][)）])\s*")

_JIEBA = None


def _jieba():
    """可选依赖：装了 jieba 就用，没装就退回二元组。"""
    global _JIEBA
    if _JIEBA is None:
        try:
            import jieba  # type: ignore

            _JIEBA = jieba
        except Exception:
            _JIEBA = False
    return _JIEBA


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return clean_text(text)


def clean_text(text: str | None) -> str:
    """统一全角/半角、去多余空白、去 HTML 残留。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def one_line(text: str | None) -> str:
    return re.sub(r"\s+", " ", clean_text(text)).strip()


def strip_question_no(text: str | None) -> str:
    """去掉 “1.” “(2)” 这类题号前缀，便于跨页面识别同一道题。"""
    return _NUM_PREFIX_RE.sub("", clean_text(text))


def strip_option_prefix(text: str | None) -> str:
    """去掉选项前的 A. / （A） 标记，只保留选项正文。"""
    return _OPTION_PREFIX_RE.sub("", clean_text(text))


def normalize_for_match(text: str | None) -> str:
    """检索/去重用的极简体：去标点、去空白、转小写。"""
    t = strip_question_no(text).lower()
    t = _PUNCT_RE.sub("", t)
    return re.sub(r"\s+", "", t)


def fingerprint(stem: str, options: Sequence[str] = (), with_options: bool = True) -> str:
    """题目指纹（sha1 前 16 位）。选项顺序无关，避免“选项打乱”被判为新题。"""
    parts = [normalize_for_match(stem)]
    if with_options and options:
        parts.append("|".join(sorted(normalize_for_match(o) for o in options if o)))
    raw = "###".join(parts).encode("utf-8", "ignore")
    return hashlib.sha1(raw).hexdigest()[:16]


def tokenize(text: str | None, bigram: bool = True) -> List[str]:
    """混合分词：ASCII 词 + 中文词（jieba 优先，否则字符/二元组）。"""
    if not text:
        return []
    s = normalize_for_match(text)
    tokens: List[str] = []
    for m in re.finditer(r"[a-z_][a-z_0-9]*|\d+(?:\.\d+)?", s):
        tokens.append(m.group(0))
    zh_blocks = re.findall(r"[\u4e00-\u9fff]+", s)
    jb = _jieba()
    for block in zh_blocks:
        if jb:
            tokens.extend(t for t in jb.lcut(block) if t.strip())
        else:
            tokens.extend(list(block))
            if bigram:
                tokens.extend(block[i : i + 2] for i in range(len(block) - 1))
    return tokens


def similarity(a: str, b: str) -> float:
    """0~1 相似度，用于近似重复题检测。"""
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(tokenize(na)), set(tokenize(nb))
    inter = len(ta & tb)
    union = len(ta | tb) or 1
    jaccard = inter / union
    seq = SequenceMatcher(None, na, nb).ratio()
    return max(jaccard, seq * 0.9)


def truncate(text: str, limit: int = 120) -> str:
    text = one_line(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_marked_answer(text: str | None) -> str:
    """把 “【答案】 A” / “答案: 正确” 之类的输出规整成纯答案串。"""
    if not text:
        return ""
    line = one_line(text)
    return re.sub(r"^(?:【答案】|答案[:：]?|Answer[:：]?)\s*", "", line).strip()


def dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        key = str(it).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out