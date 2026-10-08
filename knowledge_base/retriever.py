# -*- coding: utf-8 -*-
"""
knowledge_base.retriever —— 本地检索（BM25，纯 Python，无外部服务依赖）

支持的自然语言搜索：
    搜索：戴维南定理
    搜索：第二章电路分析
    搜索：这个公式怎么用
返回：课件片段 + 匹配章节 + 历史题目 + 错题解析（考试快速定位用）。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from utils.logger import get_logger
from utils.text import normalize_for_match, one_line, strip_question_no, tokenize

log = get_logger("knowledge_base.retriever")

CHAPTER_HINT_RE = re.compile(r"(第\s*[0-9一二三四五六七八九十百]+\s*[章节讲单元]|[Uu]nit\s*\d+|[Mm]odule\s*\d+)")
USAGE_HINT_RE = re.compile(r"(怎么用|如何用|怎样用|用法|应用|适用|步骤|方法|怎么算|如何计算|什么时候用)")
FORMULA_HINT_RE = re.compile(r"(公式|定理|定律|表达式|推导|等式)")
DEF_HINT_RE = re.compile(r"(是什么|定义|概念|含义|区别|对比)")
SEARCH_PREFIX_RE = re.compile(r"^\s*(搜索|查一下|查找|搜|search)\s*[:：]?\s*", re.I)


@dataclass
class ParsedQuery:
    raw: str = ""
    keyword: str = ""
    chapter_hint: str = ""
    want_usage: bool = False
    want_formula: bool = False
    want_definition: bool = False
    kinds: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [f"关键词：{self.keyword or '(空)'}"]
        if self.chapter_hint:
            bits.append(f"章节：{self.chapter_hint}")
        if self.kinds:
            bits.append("限定：" + "/".join({"formula": "公式", "definition": "定义"}.get(k, k) for k in self.kinds))
        return " ｜ ".join(bits)


class BM25Index:
    """轻量 BM25：对中文使用 jieba（可选）+ 字符二元组，对英文/数字使用单词。"""

    def __init__(self, k1: float = 1.6, b: float = 0.72) -> None:
        self.k1, self.b = k1, b
        self.docs: list[dict[str, Any]] = []
        self.tf: list[Counter] = []
        self.df: Counter = Counter()
        self.lengths: list[int] = []
        self.avg_len: float = 1.0
        self.ready = False

    def build(self, docs: Sequence[dict[str, Any]]) -> "BM25Index":
        self.docs, self.tf, self.df, self.lengths = [], [], Counter(), []
        for doc in docs:
            text = f"{doc.get('title', '')} {doc.get('terms', '')} {doc.get('text', '')}"
            toks = tokenize(text)
            if not toks:
                continue
            counts = Counter(toks)
            self.tf.append(counts)
            self.lengths.append(len(toks))
            for term in counts:
                self.df[term] += 1
            self.docs.append(dict(doc))
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        self.ready = bool(self.docs)
        log.info("检索索引就绪：%s 个知识块", len(self.docs))
        return self

    def search(self, query: str, top_k: int = 6, filter_kinds: Sequence[str] = (),
               chapter_key: str = "") -> list[dict[str, Any]]:
        if not self.ready:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        q_counts = Counter(q_tokens)
        n = len(self.docs)
        scores: list[tuple[float, int]] = []
        for idx, counts in enumerate(self.tf):
            doc = self.docs[idx]
            if filter_kinds and doc.get("kind") not in filter_kinds:
                continue
            if chapter_key and doc.get("chapter_key") and doc["chapter_key"] != chapter_key:
                continue
            score = 0.0
            dl = self.lengths[idx] or 1
            for term, qf in q_counts.items():
                f = counts.get(term, 0)
                if not f:
                    continue
                df = self.df.get(term, 0) or 1
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg_len))
                score += idf * norm * (1 + 0.35 * math.log(1 + qf))
            if score > 0:
                # 术语命中/公式定义块轻微加权，让“知识点”更容易被顶上来
                if doc.get("kind") in ("formula", "definition"):
                    score *= 1.12
                scores.append((score, idx))
        scores.sort(reverse=True)
        out = []
        for score, idx in scores[: max(1, top_k)]:
            item = dict(self.docs[idx])
            item["score"] = round(score, 3)
            out.append(item)
        return out

    def snippet(self, text: str, query: str, width: int = 220) -> str:
        """返回包含关键词的上下文片段（比从头截取更有帮助）。"""
        toks = [t for t in tokenize(query) if len(t) > 1]
        low = text or ""
        best = 0
        for tok in toks:
            pos = low.find(tok)
            if pos >= 0:
                best = pos
                break
        start = max(0, best - 40)
        frag = low[start : start + width]
        return ("…" if start > 0 else "") + one_line(frag) + ("…" if start + width < len(low) else "")


class Retriever:
    def __init__(self, store: Any, cfg: Any) -> None:
        self.store = store
        self.cfg = cfg
        self._index: BM25Index | None = None
        self._stamp: tuple[Any, ...] = ()

    # ------------------------------------------------------------ 索引维护
    def _ensure_index(self, course_id: int | None = None, force: bool = False) -> BM25Index:
        row = self.store.q1("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM kb_chunks")
        stamp = (course_id, int(row["n"]) if row else 0, int(row["m"]) if row else 0)
        if self._index is not None and not force and stamp == self._stamp:
            return self._index
        docs = self.store.kb_all_chunks(course_id=course_id)
        self._index = BM25Index().build(docs)
        self._stamp = stamp
        return self._index

    # ------------------------------------------------------------ 查询解析
    def parse_query(self, raw: str) -> ParsedQuery:
        text = SEARCH_PREFIX_RE.sub("", str(raw or "").strip())
        parsed = ParsedQuery(raw=text, keyword=strip_question_no(text))
        m = CHAPTER_HINT_RE.search(text)
        if m:
            parsed.chapter_hint = one_line(m.group(1))
        if USAGE_HINT_RE.search(text):
            parsed.want_usage = True
            parsed.kinds += ["formula", "definition"]
        if FORMULA_HINT_RE.search(text):
            parsed.want_formula = True
            parsed.kinds.append("formula")
        if DEF_HINT_RE.search(text):
            parsed.want_definition = True
            parsed.kinds += ["definition", "text"]
        parsed.kinds = list(dict.fromkeys(parsed.kinds))
        return parsed

    def resolve_chapter(self, query: ParsedQuery, course_id: int | None) -> dict[str, Any] | None:
        if not course_id:
            return None
        if query.chapter_hint:
            ch = self.store.find_chapter(course_id, query.chapter_hint)
            if ch:
                return ch
            # 章号 + 标题模糊：如“第二章电路分析” → 先按章号，再按标题关键词
            num = re.search(r"(\d+|[一二三四五六七八九十]+)", query.chapter_hint)
            if num:
                ch = self.store.q1(
                    "SELECT * FROM chapters WHERE course_id=? AND (no LIKE ? OR title LIKE ?) LIMIT 1",
                    (course_id, f"%{num.group(1)}%", f"%{query.chapter_hint[3:]}%"),
                )
                return dict(ch) if ch else None
        return None

    # ------------------------------------------------------------ 主搜索
    def search(self, raw_query: str, course_id: int | None = None, top_k: int | None = None,
               force: bool = False) -> dict[str, Any]:
        """统一搜索：资料 + 章节 + 历史题目 + 错题（考试搜索框直接用这个）。"""
        q = self.parse_query(raw_query)
        top_k = int(top_k or self.cfg.get("KB_TOP_K") or 6)
        chapter = self.resolve_chapter(q, course_id)
        index = self._ensure_index(course_id=course_id, force=force)
        keyword = q.keyword
        if chapter and chapter.get("title") and chapter["title"] not in keyword:
            keyword = f"{keyword} {chapter['title']}"
        hits = index.search(keyword, top_k=top_k, filter_kinds=q.kinds,
                            chapter_key=(chapter or {}).get("chap_key", ""))
        if not hits and q.kinds:
            hits = index.search(keyword, top_k=top_k)      # 放宽限定再试一次
        questions = self.search_questions(q.keyword, course_id=course_id, top_k=4)
        wrongs = [w for w in self.store.list_wrong(course_id=course_id, only_unresolved=False, limit=300)]
        wrong_terms = tokenize(q.keyword)
        wrong_hits = [
            w for w in wrongs
            if set(tokenize(w.get("stem", "") + " " + (w.get("knowledge") or ""))) & set(wrong_terms)
        ][:4]
        result = {
            "query": q, "chapter": chapter, "chunks": hits, "questions": questions,
            "wrongs": wrong_hits, "has_evidence": bool(hits or questions or wrong_hits),
        }
        try:
            self.store.log_search("kb", q.raw, len(hits) + len(questions) + len(wrong_hits))
        except Exception:
            log.debug("搜索日志写入失败", exc_info=True)
        return result

    def search_questions(self, text: str, course_id: int | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        """历史题目匹配：用指纹/关键词在题库里找“做过的同类题”。"""
        terms = [t for t in tokenize(text) if len(t) > 1]
        if not terms:
            return []
        sql, params = "SELECT * FROM questions WHERE 1=1", []
        if course_id:
            sql += " AND (course_id=? OR course_id IS NULL)"
            params.append(course_id)
        rows = self.store.q(sql + " ORDER BY updated_at DESC LIMIT 800", params)
        scored = []
        for row in rows:
            doc = set(tokenize(row["stem"] + " " + (row["knowledge"] or "")))
            inter = len(doc & set(terms))
            if inter:
                d = dict(row)
                d["options"] = []
                d["_match"] = round(inter / max(1, len(terms)), 3)
                d["kind_name"] = {"single": "单选题", "multi": "多选题", "judge": "判断题",
                                  "blank": "填空题", "short": "简答题"}.get(row["kind"], row["kind"] or "")
                scored.append(d)
        scored.sort(key=lambda x: (-x["_match"], -(x.get("wrong_count") or 0)))
        return scored[:top_k]

    def evidence_for_question(self, question: dict[str, Any], course_id: int | None = None,
                              top_k: int = 5) -> dict[str, Sequence[dict[str, Any]]]:
        """给 AI 的“可引用证据”：资料片段 + 历史题 + 错题。"""
        blob = f"{question.get('stem', '')} " + " ".join(
            o.get("text", "") if isinstance(o, dict) else str(o) for o in (question.get("options") or [])
        )
        result = self.search(blob, course_id=course_id, top_k=top_k)
        return {
            "chunks": result["chunks"],
            "questions": [q for q in result["questions"] if normalize_for_match(q["stem"]) !=
                          normalize_for_match(question.get("stem", ""))][:3],
            "wrongs": result["wrongs"],
        }

    # ------------------------------------------------------------ 渲染
    def render_hits(self, result: dict[str, Any], limit: int = 5) -> str:
        q: ParsedQuery = result["query"]
        lines = [f"搜索：{q.raw}", f"解析 → {q.describe()}"]
        if result.get("chapter"):
            lines.append(f"匹配章节：{result['chapter'].get('no', '')} {result['chapter'].get('title', '')}")
        chunks = result.get("chunks") or []
        if chunks:
            lines.append("— 课程资料 —")
            for i, c in enumerate(chunks[:limit], 1):
                text = (c.get("text") or "").replace("\n", " ")
                lines.append(f"[资料{i}]《{c.get('title', '')}》{c.get('loc', '')}（{c.get('kind')}，"
                             f"相关度 {c.get('score')}）\n      {text[:200]}")
        else:
            lines.append("— 课程资料：未找到匹配片段（建议先执行 kb build 建库）—")
        qs = result.get("questions") or []
        if qs:
            lines.append("— 历史题目 —")
            for q2 in qs[:3]:
                lines.append(f"· {q2.get('kind_name', '')} {one_line(q2.get('stem', ''))[:90]} → "
                             f"答案：{q2.get('answer') or '待补充'}（错 {q2.get('wrong_count', 0)} 次）")
        ws = result.get("wrongs") or []
        if ws:
            lines.append("— 相关错题 —")
            for w in ws[:3]:
                lines.append(f"· {one_line(w.get('stem', ''))[:90]} → 答案：{w.get('answer') or '待补充'}"
                             f"（错 {w.get('wrong_count', 1)} 次，知识点：{w.get('knowledge') or '—'}）")
        return "\n".join(lines)