# -*- coding: utf-8 -*-
"""
exam.assistant —— 考试辅助编排（读题 → 检索本地资料 → AI 分析 → 展示，全程不改页面）

界面上始终展示 7 个区块：
  【当前题目】【AI 分析】【答案】【课程依据】【相关知识点】【相似题】【下一题】
并提供快速搜索框（走 knowledge_base.retriever 的统一搜索）。

重要：提交由你本人在学习通页面点击；本模块没有任何提交能力。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from browser import actions as A
from exam.guard import BANNER, ExamGuard
from exam.snapshot import ExamSnapshot
from exam.worker import BrowserWorker
from ui import report
from utils.console import err, info, ok, section, warn
from utils.logger import get_logger
from utils.retry import RateLimiter
from utils.text import one_line
from utils.time_util import fmt_clock

log = get_logger("exam.assistant")


@dataclass
class ExamState:
    url: str = ""
    title: str = ""
    countdown_text: str = ""
    countdown_sec: float | None = None
    total: int = 0
    index: int = -1
    questions: list[Any] = field(default_factory=list)
    current: Any = None
    analysis: str = ""
    evidence: str = ""
    knowledge: str = ""
    similar: str = ""
    search_text: str = ""
    rule_note: str = ""
    updated_at: float = 0.0
    review_flags: set = field(default_factory=set)   # AI 判定“需要人工确认”的题号

    def render(self) -> str:
        """终端/侧边栏共用的文本版界面（满足“侧边栏”布局要求）。"""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("开卷考试 AI 辅助（只读模式）  " + ("√ 已启用" if self.enabled_text else "× 未启用"))
        lines.append(f"页面：{one_line(self.title)[:40]}  ｜ 共 {self.total} 题 ｜ 当前 {self.index + 1}/{self.total}"
                     + (f" ｜ 剩余时间(只读)：{fmt_clock(self.countdown_sec)}" if self.countdown_sec else ""))
        lines.append("=" * 60)
        lines.append("【当前题目】")
        if self.current is not None:
            q = self.current
            lines.append(f"  题号 {q.no or '-'} ｜ {q.kind_label}"
                         + (f" ｜ 分值 {q.score}" if q.score else ""))
            for i, line in enumerate(one_line(q.stem).replace("；", "；\n").split("\n")):
                lines.append(f"  {line}")
            for opt in q.option_texts:
                lines.append(f"    {opt}")
            if q.has_image:
                lines.append("  ! 本题含图片，请在学习通页面人工查看图片内容（AI 未看到图片）")
        else:
            lines.append("  （尚未读取。按 A 读取当前题，或整卷读取）")
        lines.append("")
        lines.append("【AI 分析】")
        lines.append("  " + (self.analysis.replace("\n", "\n  ") if self.analysis else "（未分析）"))
        lines.append("")
        lines.append("【答案】 见上方 AI 分析中的【答案】行；提交前请你本人核对")
        lines.append("【课程依据】")
        lines.append("  " + (self.evidence.replace("\n", "\n  ") if self.evidence else "（未检索）"))
        lines.append("【相关知识点】 " + (self.knowledge or "（未标注）"))
        lines.append("【相似题】" + ("\n" + self.similar if self.similar else " （题库中无同类题）"))
        if self.search_text:
            lines.append("")
            lines.append("【快速搜索】")
            lines.append("  " + self.search_text.replace("\n", "\n  "))
        if self.rule_note:
            lines.append("")
            lines.append("【规则提示】 " + self.rule_note)
        lines.append("")
        lines.append("【下一题】 按 N 仅滚动页面一屏（不点击、不填写）；翻页后按 A 读取")
        lines.append("【提交前确认】 按 T 生成本人确认清单；提交必须由你亲自点击")
        lines.append("-" * 60)
        return "\n".join(lines)

    enabled_text: bool = False


class ExamAssistant:
    def __init__(self, cfg: Any, store: Any, worker: BrowserWorker, guard: ExamGuard,
                 kb: Any = None, engine: Any = None) -> None:
        self.cfg = cfg
        self.store = store
        self.worker = worker
        self.guard = guard
        self.kb = kb
        self.engine = engine
        self.snapshot = ExamSnapshot(browser=worker.browser, cfg=cfg, guard=guard)
        self.state = ExamState()
        self.session_id: int | None = None
        per_min = max(1, int(cfg.get("EXAM_AI_MAX_PER_MIN") or 6))
        self.ai_limiter = RateLimiter(min_interval=60.0 / per_min)

    # ------------------------------------------------------------ 生命周期
    def bootstrap(self, course_name: str = "", url: str = "") -> bool:
        """读取考试页说明 → 合规判定 → 需要时二次确认。"""
        page_info = self.worker.current_page() if self.worker.is_alive() else {}
        rule_text = str(page_info.get("title", ""))
        if url:
            rule_text += "\n" + self.worker.with_page(lambda p, ctx: self.snapshot.read_instructions(p), timeout=60)
        else:
            rule_text += "\n" + self.worker.with_page(lambda p, ctx: self.snapshot.read_instructions(p), timeout=60)
        decision = self.guard.ensure_enabled(page_rule_text=rule_text, interactive=True,
                                            course_name=course_name)
        if decision.blocked:
            err(f"考试 AI 模式未启用：{decision.reason}")
            self.state.rule_note = decision.reason
            return False
        self.session_id = self.store.start_session("exam", title=course_name or "考试辅助", url=str(page_info.get("url", "")))
        self.store.log_exam_event("session_start", f"课程={course_name}")
        self.state.enabled_text = True
        self.state.url = str(page_info.get("url", ""))
        self.state.title = str(page_info.get("title", ""))
        return True

    def refresh_status(self) -> str:
        def _job(page: Any, ctx: dict[str, Any]) -> dict[str, Any]:
            raw, secs = self.snapshot.countdown(page)
            return {"url": page.url, "title": page.title(), "cd_text": raw, "cd": secs}

        data = self.worker.with_page(lambda p, c: _job(p, c), timeout=30)
        self.state.url = data.get("url", "")
        self.state.title = data.get("title", "")
        self.state.countdown_text = data.get("cd_text", "")
        self.state.countdown_sec = data.get("cd")
        self.state.enabled_text = self.guard.enabled
        return f"{one_line(self.state.title)[:40]} ｜ 剩余 {fmt_clock(self.state.countdown_sec)}（只读）"

    # ------------------------------------------------------------ 读题
    def read_current(self, auto_next_check: bool = True) -> str:
        self._guard_active()
        def _job(page: Any, ctx: dict[str, Any]) -> dict[str, Any]:
            qs = self.snapshot.read_questions(page)
            cur, all_qs = self.snapshot.current_question(page, qs)
            rule = self.snapshot.read_instructions(page)
            raw, secs = self.snapshot.countdown(page)
            return {"questions": all_qs, "current": cur, "rule": rule, "cd_text": raw, "cd": secs,
                    "url": page.url, "title": page.title()}

        data = self.worker.with_page(_job, timeout=120)
        self.guard.runtime_check(data.get("rule", ""))
        self.state.enabled_text = self.guard.enabled
        self.state.questions = data.get("questions") or []
        self.state.total = len(self.state.questions)
        self.state.current = data.get("current")
        self.state.countdown_text, self.state.countdown_sec = data.get("cd_text", ""), data.get("cd")
        self.state.title, self.state.url = data.get("title", ""), data.get("url", "")
        try:
            self.state.index = self.state.questions.index(self.state.current) if self.state.current else -1
        except ValueError:
            self.state.index = -1
        self.state.updated_at = time.time()
        if self.state.current is None:
            warn("没有识别到题目：请确认已切到考试页面（程序不会替你翻页或点击）。")
            self.store.log_exam_event("read_question_failed", f"url={self.state.url}")
        else:
            self.store.log_exam_event("read_question",
                                      f"题号={self.state.current.no} 共{self.state.total}题 页面={self.state.url[:120]}")
        if auto_next_check and self.state.current is not None:
            self._persist_question(self.state.current)
        return report.render_question(self.state.current, self.state.index + 1, self.state.total) \
            if self.state.current else "未识别到题目"

    def read_all(self) -> str:
        self._guard_active()
        def _job(page: Any, ctx: dict[str, Any]) -> list[Any]:
            return self.snapshot.read_questions(page)

        qs = self.worker.with_page(_job, timeout=180) or []
        self.state.questions = qs
        self.state.total = len(qs)
        lines = [f"整卷共识别到 {len(qs)} 道题（只读概览，逐题分析请按 A/或输入题号）："]
        for i, q in enumerate(qs, 1):
            lines.append(f"{i:>3}. [{q.kind_label}] {q.preview(70)}" + (f"  分值 {q.score}" if q.score else ""))
        for q in qs:
            self._persist_question(q)
        self.store.log_exam_event("read_all", f"共{len(qs)}题")
        return "\n".join(lines)

    # ------------------------------------------------------------ AI 分析
    def analyze(self, question: Any = None) -> str:
        self._guard_active()
        q = question or self.state.current
        if q is None:
            return "请先读取当前题目（按 A）"
        course_name = self.guard.course_name or ""
        qid, dup = self._persist_question(q)
        evidence: dict[str, Any] = {"chunks": [], "questions": [], "wrongs": []}
        if self.kb is not None:
            try:
                evidence = self.kb.evidence_for_question(q.as_dict(),
                                                         course_id=self._course_id(), top_k=int(self.cfg.get("KB_TOP_K") or 6))
            except Exception as exc:
                log.warning("证据检索失败：%s", exc)
        self.ai_limiter.wait()
        ans = None
        if self.engine is not None:
            ans = self.engine.answer(q.as_dict(), evidence=evidence, mode="exam",
                                     course=course_name, chapter=q.chapter_key, qno=q.no)
        else:
            ans = _FallbackAnswer()
        body = report.render_answer(ans)
        ev_text = report.render_evidence(list(evidence.get("chunks") or []), title="课程依据")
        sim_text = report.render_wrong_refs(list(evidence.get("wrongs") or []))
        if getattr(ans, "needs_human", False):
            self.state.review_flags.add(str(q.no or self.state.index + 1))
        else:
            self.state.review_flags.discard(str(q.no or self.state.index + 1))
        self.state.analysis = body
        self.state.evidence = ev_text
        self.state.knowledge = getattr(ans, "knowledge", "") or ""
        self.state.similar = sim_text
        self.store.log_exam_event("ai_answer", f"题号={q.no} 可信度={getattr(ans, 'confidence', '—')}")
        card = "\n".join([body, ev_text, sim_text])
        if qid and getattr(ans, "answer", ""):
            self.store.set_question_ai_result(qid, answer=ans.answer, analysis=ans.analysis,
                                              knowledge=ans.knowledge, answer_source="ai")
        return card

    def analyze_number(self, which: str) -> str:
        q = self.snapshot.question_at(self.state.questions, which)
        if q is None:
            return f"未找到题号 {which}（可先按 O 整卷读取）"
        self.state.current = q
        try:
            self.state.index = self.state.questions.index(q)
        except ValueError:
            pass
        return self.analyze(q)

    # ------------------------------------------------------------ 快速搜索
    def search(self, query: str) -> str:
        self._guard_active(allow_without_read=True)
        if not query.strip():
            return "请输入搜索关键词，例如：戴维南定理 / 第二章电路分析 / 这个公式怎么用"
        if self.kb is None:
            return "知识库未初始化"
        result = self.kb.search(query, course_id=self._course_id())
        text = self.kb.render_hits(result, limit=6)
        summary = ""
        if self.engine is not None and getattr(self.engine, "ai", None) and self.engine.ai.enabled:
            self.ai_limiter.wait()
            summary = self.engine.summarize(query, list(result.get("chunks") or []))
            self.store.log_exam_event("search_ai", f"query={query[:80]}")
        self.state.search_text = text + ("\n\nAI 综合：\n" + summary if summary else "")
        self.store.log_exam_event("search", f"query={query[:80]} 命中={len(result.get('chunks') or [])}")
        return self.state.search_text

    # ------------------------------------------------------------ 翻页（仅滚动）
    def next_question(self, direction: int = 1) -> str:
        self._guard_active(allow_without_read=True)
        ok_scroll = self.worker.with_page(lambda p, ctx: self.snapshot.scroll_next(p, direction), timeout=30)
        if not ok_scroll:
            return "滚动已被禁用（EXAM_ALLOW_SCROLL=False）。请你自己翻页。"
        self.store.log_exam_event("scroll", f"direction={direction}")
        return f"已向下滚动一屏（仅滚动，未点击/未填写）。按 A 读取新的当前题。"

    # ------------------------------------------------------------ 提交前本人确认清单
    def pre_submit_report(self) -> str:
        """
        生成给你本人核对的“提交前确认清单”。程序没有提交能力，
        这份清单只用于提醒：先看一遍题号与未答题，再由你亲自点击提交。
        """
        from utils.time_util import fmt_clock

        self._guard_active(allow_without_read=True)
        if not self.state.questions:
            self.read_all()
        qs = self.state.questions
        unanswered = [str(q.no or i) for i, q in enumerate(qs, 1) if not (q.my_answer or "").strip()]
        with_image = [str(q.no or i) for i, q in enumerate(qs, 1) if q.has_image]
        low_conf = sorted(self.state.review_flags)
        _raw, secs = self.worker.with_page(lambda p, ctx: self.snapshot.countdown(p), timeout=30)
        lines = ["=" * 58, "提交前确认清单（请逐条自己核对，程序不会替你提交）", "=" * 58]
        lines.append(f"1. 本地识别题目数：{len(qs)} 题")
        lines.append(f"2. 页面上尚未选择/填写的题号：{('、'.join(unanswered)) or '无'}")
        lines.append(f"3. AI 判定“需要人工确认”的题号：{('、'.join(low_conf)) or '无'}")
        lines.append(f"4. 含图片、AI 看不到题面的题号：{('、'.join(with_image)) or '无'}")
        lines.append(f"5. 剩余时间（只读展示，未做任何修改）：{fmt_clock(secs)}")
        lines.append("6. 需要你本人完成：核对题号 → 检查未答题 → 逐题确认 → 亲自点击“提交答案”。")
        lines.append("-" * 58)
        lines.append("合规提醒：仅在“明确允许开卷 + 允许 AI 辅助”的考试中使用本功能；")
        lines.append("          AI 结果仅为思路参考，答案正确性由你本人负责。")
        self.store.log_exam_event("pre_submit_check",
                                  f"未答{len(unanswered)} 需复核{len(low_conf)} 含图{len(with_image)}")
        return "\n".join(lines)

    # ------------------------------------------------------------ 收尾
    def close(self) -> None:
        if self.session_id:
            self.store.end_session(self.session_id, total=self.state.total, note="考试辅助结束（未代为提交）")
        self.store.log_exam_event("disable", "考试 AI 模式关闭")
        self.guard.enabled = False
        self.state.enabled_text = False

    # ------------------------------------------------------------ 内部
    def _guard_active(self, allow_without_read: bool = False) -> None:
        if not self.guard.enabled:
            raise PermissionError("考试 AI 模式未启用或已自动关闭：请遵守考试规定，独立作答。")
        if not allow_without_read and not self.state.questions:
            info("提示：尚未读取题目，先按 A 读取当前题。")

    def _course_id(self) -> int | None:
        if not self.guard.course_name:
            return None
        course = self.store.get_course(self.guard.course_name)
        return int(course["id"]) if course else None

    def _persist_question(self, q: Any) -> tuple[int | None, bool]:
        try:
            course = self._course_id()
            qid, dup = self.store.upsert_question(q.to_row(), course_id=course)
            q.raw["db_id"] = qid
            return qid, dup
        except Exception as exc:
            log.debug("题目入库失败：%s", exc)
            return None, False


class _FallbackAnswer:
    """AI 不可用时的占位对象（保证界面结构一致，且不编造内容）。"""

    def __init__(self) -> None:
        self.answer = "（AI 未启用，无法生成答案）"
        self.analysis = "请先在 user_config.py 配置 AI_API_KEY，或用 `main.py search` 做纯本地资料检索。"
        self.knowledge = ""
        self.chapter = ""
        self.evidence = "课程资料中没有找到直接依据"
        self.confidence = "需要人工确认"
        self.doubt = "AI 未启用"
        self.error = "AI 未启用"
        self.fabricated_citations: list[str] = []
        self.needs_human = True
        self.ai_used = False


# ------------------------------------------------------------------ 终端版界面
def run_console_ui(assistant: ExamAssistant) -> None:
    HELPER = """
命令：
  A  读取当前题目（只读）           O  整卷读取（题号概览）
  N  下一题（仅滚动一屏）            P  上一题（仅向上滚动）
  G  按题号分析（如 G 7）             D  分析当前题（AI）
  S  快速搜索（如 S 戴维南定理）      K  刷新状态/倒计时（只读）
  T  提交前确认清单（未答题/需复核题）  Q  退出（关闭考试 AI 模式）
"""
    print(BANNER)
    print(assistant.state.render())
    while True:
        print(HELPER)
        try:
            cmd = input("考试辅助> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cmd:
            print(assistant.state.render())
            continue
        head, _, arg = cmd.partition(" ")
        key = head.upper()
        try:
            if key == "A":
                print(assistant.read_current())
            elif key == "O":
                print(assistant.read_all())
            elif key == "N":
                print(assistant.next_question(1))
            elif key == "P":
                print(assistant.next_question(-1))
            elif key == "G":
                print(assistant.analyze_number(arg.strip() or "1"))
            elif key == "D":
                print(assistant.analyze())
            elif key == "S":
                print(assistant.search(arg.strip()))
            elif key == "K":
                print(assistant.refresh_status())
            elif key == "T":
                print(assistant.pre_submit_report())
            elif key in ("Q", "EXIT"):
                break
            else:
                warn("未知命令")
        except PermissionError as exc:
            err(str(exc))
        except Exception as exc:
            log.exception("界面命令执行失败")
            err(f"操作失败：{exc}")
        print(assistant.state.render())
    assistant.close()
    ok("考试辅助已退出。请记得：**提交答案必须你本人点击**。")