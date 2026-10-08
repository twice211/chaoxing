# -*- coding: utf-8 -*-
"""
ai.responder —— 把题目 + 本地证据交给 AI，并**校验输出结构**

为什么要校验：大模型可能凭空引用不存在的资料。本模块会
1) 检查【资料依据】里引用的编号是否真实存在于本次提供的证据集合中；
2) 引用不到证据 → 强制降级为“需要人工确认”，并写明“课程资料中没有找到直接依据”；
3) 缺失【可信程度】→ 自动补“需要人工确认”。

这样“禁止编造资料来源”就是代码级保证，而不是只写在提示词里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ai.client import AIClient, AIUnavailable
from ai import prompts as P
from utils.logger import get_logger
from utils.text import clean_text, one_line

log = get_logger("ai.responder")

SECTION_RE = re.compile(r"【\s*(答案|解析|知识点|所属章节|资料依据|可信程度|存疑|相似题)\s*】\s*")
VALID_CONFIDENCE = ("高", "中", "需要人工确认")
NO_EVIDENCE_TEXT = "课程资料中没有找到直接依据"


@dataclass
class Answer:
    qno: str = ""
    kind: str = "unknown"
    kind_name: str = ""
    answer: str = ""
    analysis: str = ""
    knowledge: str = ""
    chapter: str = ""
    evidence: str = ""
    confidence: str = "需要人工确认"
    doubt: str = ""
    raw: str = ""
    cited_ids: list[str] = field(default_factory=list)
    fabricated_citations: list[str] = field(default_factory=list)
    needs_human: bool = True
    ai_used: bool = False
    error: str = ""

    @property
    def display(self) -> str:
        parts = [
            f"【答案】{self.answer or '（未给出）'}",
            f"【解析】{self.analysis or '（未给出）'}",
            f"【知识点】{self.knowledge or '（未标注）'}",
        ]
        if self.chapter:
            parts.append(f"【所属章节】{self.chapter}")
        parts.append(f"【资料依据】{self.evidence or NO_EVIDENCE_TEXT}")
        parts.append(f"【可信程度】{self.confidence}")
        if self.doubt:
            parts.append(f"【需要人工检查】{self.doubt}")
        if self.error:
            parts.append(f"【AI 状态】{self.error}")
        return "\n".join(parts)


@dataclass
class AnswerEngine:
    ai: AIClient
    cfg: Any
    store: Any | None = None

    # ------------------------------------------------------------ 主流程
    def answer(self, question: dict[str, Any], evidence: dict[str, Sequence[dict[str, Any]]] | None = None,
               mode: str = "practice", course: str = "", chapter: str = "", qno: str = "") -> Answer:
        """
        mode: practice（练习辅导）| exam（开卷考试辅助）
        evidence: {"chunks": [...], "questions": [...], "wrongs": [...]}
        """
        evidence = evidence or {}
        chunks = list(evidence.get("chunks") or [])
        questions = list(evidence.get("questions") or [])
        wrongs = list(evidence.get("wrongs") or [])
        valid_ids = (
            [f"资料{i}" for i in range(1, len(chunks) + 1)]
            + [f"题目{i}" for i in range(1, len(questions) + 1)]
            + [f"错题{i}" for i in range(1, len(wrongs) + 1)]
        )
        kind = str(question.get("kind") or "unknown")
        template = P.USER_QUESTION_EXAM if mode == "exam" else P.USER_QUESTION_PRACTICE
        user = template.format(
            course=course or "未指定",
            chapter=chapter or "未指定",
            kind_name=P.KIND_NAMES.get(kind, kind),
            stem=clean_text(question.get("stem", "")),
            options=P.format_options(question.get("options") or []),
            evidence_block=P.build_evidence_block(chunks, questions, wrongs),
            qno=qno or question.get("no", ""),
        )
        system = P.SYSTEM_EXAM if mode == "exam" else P.SYSTEM_STUDY
        ans = Answer(qno=str(qno or question.get("no", "")), kind=kind,
                     kind_name=P.KIND_NAMES.get(kind, kind))
        if not self.ai.enabled:
            ans.error = "AI 未启用/未配置密钥：仅完成本地资料检索，未生成答案"
            ans.evidence = P.build_evidence_block(chunks, questions, wrongs).splitlines()[0] if chunks else NO_EVIDENCE_TEXT
            return ans
        try:
            raw = self.ai.ask(system, user, purpose=f"{mode}_answer" if mode else "answer")
        except AIUnavailable as exc:
            ans.error = str(exc)
            return ans
        except Exception as exc:  # 兜底：任何异常都不应中断主流程
            log.exception("AI 调用异常")
            ans.error = f"AI 调用异常：{exc}"
            return ans
        ans.raw = raw
        ans.ai_used = True
        self._parse(raw, ans)
        self._validate_evidence(ans, valid_ids, full_text=raw)
        self._finalize(ans, bool(valid_ids))
        return ans

    # ------------------------------------------------------------ 解析
    @staticmethod
    def _parse(raw: str, ans: Answer) -> None:
        text = clean_text(raw)
        matches = list(SECTION_RE.finditer(text))
        if not matches:
            ans.answer = text[:600]
            return
        for i, m in enumerate(matches):
            name = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip(" ：:\n")
            if name == "答案":
                ans.answer = body
            elif name == "解析":
                ans.analysis = body
            elif name == "知识点":
                ans.knowledge = one_line(body)
            elif name == "所属章节":
                ans.chapter = one_line(body)
            elif name == "资料依据":
                ans.evidence = body
            elif name == "可信程度":
                low = body.lower()
                ans.confidence = ("高" if "高" in body else "中" if "中" in body or "mid" in low else "需要人工确认")
            elif name == "存疑":
                ans.doubt = body
        # 主观题的 ①②③④ 结构通常整体留在【答案】里，供用户直接誊抄到答题纸

    # ------------------------------------------------------------ 校验
    @staticmethod
    def _validate_evidence(ans: Answer, valid_ids: Sequence[str], full_text: str = "") -> None:
        # 引用可能出现在【解析】【答案】等任意栏目里，因此按全文收集再判定
        cited = set(re.findall(r"\[([^\]\[]{1,12})\]", f"{ans.evidence or ''}\n{full_text or ''}"))
        ans.cited_ids = sorted(c for c in cited if any(c.startswith(v) or c == v for v in valid_ids))
        ans.fabricated_citations = sorted(
            c for c in cited if c not in ans.cited_ids and not re.fullmatch(r"[\d年月日\.\-]+", c)
        )
        if ans.fabricated_citations:
            log.warning("AI 引用了不存在的资料编号：%s", ans.fabricated_citations)
            ans.evidence = (ans.evidence or "") + "\n（注意：以上引用编号 " + "、".join(
                ans.fabricated_citations) + " 不在本地资料中，视为无效引用）"
        if not ans.cited_ids:
            # 区分两种情况：本地确实没给资料 vs 给了资料但 AI 没标编号
            ans.evidence = NO_EVIDENCE_TEXT if not valid_ids else (
                f"AI 未标注引用编号；本地检索到 {len(valid_ids)} 条相关片段（见下方【课程依据】），请自行核对"
            )
        elif not (ans.evidence or "").strip() or NO_EVIDENCE_TEXT in (ans.evidence or ""):
            ans.evidence = "引用本地资料：" + "、".join(f"[{c}]" for c in ans.cited_ids)

    @staticmethod
    def _finalize(ans: Answer, has_evidence: bool) -> None:
        if ans.doubt:
            ans.confidence = "需要人工确认"
        if ans.confidence not in VALID_CONFIDENCE:
            ans.confidence = "需要人工确认"
        if not ans.answer:
            ans.confidence = "需要人工确认"
        if ans.fabricated_citations:
            # 出现无法核对的引用编号：不允许以“高可信”呈现，一律要求人工确认
            ans.confidence = "需要人工确认"
            ans.needs_human = True
            ans.doubt = (ans.doubt + "；" if ans.doubt else "") + (
                "AI 引用了本地不存在的资料编号 " + "、".join(ans.fabricated_citations) + "，请人工核对"
            )
        ans.needs_human = ans.confidence == "需要人工确认" or not ans.answer

    # ------------------------------------------------------------ 判题（练习用）
    def grade(self, question: dict[str, Any], user_answer: str, ref_answer: str = "") -> tuple[bool | None, str]:
        if not self.ai.enabled:
            return None, "AI 未启用，无法自动判断"
        user = (
            f"题型：{P.KIND_NAMES.get(str(question.get('kind')), '')}\n"
            f"题干：{one_line(question.get('stem', ''))[:600]}\n"
            f"选项：{P.format_options(question.get('options') or [])}\n"
            f"参考答案：{ref_answer or question.get('answer') or '（题内未给出）'}\n"
            f"学生答案：{user_answer}\n请判断学生答案是否正确。"
        )
        try:
            data = self.ai.ask_json(P.SYSTEM_GRADE, user, purpose="grade")
        except Exception as exc:
            log.warning("判题失败：%s", exc)
            return None, f"判题失败：{exc}"
        if isinstance(data, dict):
            val = data.get("correct")
            reason = str(data.get("reason", ""))
            if val is None:
                return None, reason or "需要人工评分"
            return bool(val), reason
        return None, "判题返回格式异常"

    # ------------------------------------------------------------ 生成相似题
    def make_similar(self, question: dict[str, Any], n: int = 2) -> list[dict[str, Any]]:
        if not self.ai.enabled:
            return []
        user = P.USER_SIMILAR.format(
            n=int(n),
            kind_name=P.KIND_NAMES.get(str(question.get("kind")), ""),
            stem=clean_text(question.get("stem", "")),
            options=P.format_options(question.get("options") or []),
            answer=question.get("answer", "") or "（未知）",
            knowledge=question.get("knowledge", "") or "（未标注）",
        )
        try:
            data = self.ai.ask_json(P.SYSTEM_SIMILAR, user, purpose="similar", max_tokens=2048)
        except Exception as exc:
            log.warning("生成相似题失败：%s", exc)
            return []
        items = data.get("items") if isinstance(data, dict) else data
        out: list[dict[str, Any]] = []
        for it in items or []:
            if not isinstance(it, dict) or not one_line(str(it.get("stem", ""))):
                continue
            out.append(
                {
                    "kind": str(it.get("kind") or question.get("kind") or "unknown"),
                    "stem": clean_text(str(it.get("stem", ""))),
                    "options": [str(x) for x in (it.get("options") or [])],
                    "answer": one_line(str(it.get("answer", ""))),
                    "analysis": clean_text(str(it.get("analysis", ""))),
                    "knowledge": one_line(str(it.get("knowledge", ""))),
                    "is_ai_similar": 1,
                    "origin_qid": question.get("id"),
                    "chapter_key": question.get("chapter_key", ""),
                }
            )
        return out[: max(1, int(n))]

    # ------------------------------------------------------------ 资料总结（搜索用）
    def summarize(self, query: str, chunks: Sequence[dict[str, Any]]) -> str:
        if not self.ai.enabled or not chunks:
            return ""
        user = P.USER_SUMMARY.format(
            query=query,
            chunks="\n".join(
                f"[资料{i}]《{c.get('title', '资料')}》{c.get('loc', '')}：{(c.get('text') or '')[:600]}"
                for i, c in enumerate(chunks[:8], 1)
            ),
        )
        try:
            return clean_text(self.ai.ask(P.SYSTEM_STUDY, user, purpose="summarize"))
        except Exception as exc:
            log.warning("AI 总结失败：%s", exc)
            return f"（AI 总结失败：{exc}）"

    def discussion_text(self, topic: str, course: str = "", chapter: str = "",
                        excerpt: str = "", kind: str = "reply") -> str:
        """生成讨论区草稿,两类分流:
        kind="reply" → 约150字、针对该话题内容的回帖；kind="new_post" → 围绕课程提出一个问题
        （首行问题标题+正文,由调用方 split_question_post 拆分）。
        失败/未启用/AI 明显没内容 → 返回 ""（调用方据此跳过，不空发不灌水）。
        """
        if not getattr(self.ai, "enabled", False):
            return ""
        if kind == "new_post":
            user = P.USER_DISCUSSION_POST.format(course=course or "未指定")
        else:
            user = P.USER_DISCUSSION_REPLY.format(
                course=course or "未指定",
                topic=one_line(topic)[:200] or "（无标题）",
                excerpt=clean_text(excerpt)[:1200] or "（无正文）",
            )
        try:
            text = clean_text(self.ai.ask(P.SYSTEM_DISCUSSION, user, purpose="discussion"))
        except Exception as exc:
            log.warning("AI 讨论生成失败：%s", exc)
            return ""
        if not text or len(text) < 20:
            return ""
        return text[:800] if kind != "new_post" else text[:1500]

    def discussion_batch_text(self, kind: str, course: str, count: int,
                              listing: str = "", angles: str = "") -> str:
        """一次调用生成多条讨论草稿的原始文本（###N### 分隔，由调用方拆分）。未启用/失败 → ""。"""
        if not getattr(self.ai, "enabled", False) or int(count or 0) <= 0:
            return ""
        if kind == "new_post":
            user = P.USER_DISCUSSION_POST_BATCH.format(course=course or "未指定", count=count, angles=angles)
        else:
            user = P.USER_DISCUSSION_REPLY_BATCH.format(course=course or "未指定", count=count, listing=listing)
        try:
            return clean_text(self.ai.ask(P.SYSTEM_DISCUSSION, user, purpose="discussion_batch"))
        except Exception as exc:
            log.warning("AI 批量讨论生成失败：%s", exc)
            return ""