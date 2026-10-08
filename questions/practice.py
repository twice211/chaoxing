# -*- coding: utf-8 -*-
"""
questions.practice —— 刷题辅助流程（AI 分析 + 人工作答 + 错题自动整理）

流程：
  打开练习页 → 提取题目 → 入库/重复题检测 → 本地资料检索 → AI 分析（答案+解析+知识点）
  → 用户在**学习通页面上自己作答、自己提交** → 回填对错 → 错题入库 → 生成强化题

红线：
  * 程序绝不点击“提交答案”，绝不代替用户在考试/练习页输入；
  * 每完成一题只提示你“请在页面上自行选择并提交”。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

from browser import actions as A
from course.selectors import SELECTORS
from exam.guard import ExamGuard
from exam.snapshot import ExamSnapshot
from questions.autofill import plan_answer_actions
from questions.extractor import QuestionExtractor
from questions.models import Question, answers_equal
from ui import report
from utils.console import ask, confirm, err, info, ok, section, warn
from utils.logger import get_logger
from utils.retry import LoopGuard, LoopGuardTripped
from utils.text import one_line

log = get_logger("questions.practice")


@dataclass
class PracticeRunner:
    browser: Any
    cfg: Any
    store: Any
    engine: Any = None                 # ai.responder.AnswerEngine
    extractor: QuestionExtractor = field(default=None)  # type: ignore[assignment]
    kb: Any = None                     # knowledge_base.retriever.Retriever
    wrongbook: Any = None              # questions.wrongbook.WrongBook
    guard: Any = None                  # exam.guard.ExamGuard（练习模式写闸门）
    snapshot: Any = None               # exam.snapshot.ExamSnapshot（受闸门保护的写操作）

    def __post_init__(self) -> None:
        if self.extractor is None:
            self.extractor = QuestionExtractor(self.cfg)

    # ------------------------------------------------------------ 练习模式写闸门
    def _writable(self, page: Any, want_submit: bool = False) -> bool:
        """仅当 PRACTICE_MODE 开且目标页非真实考试域名（提交还需 PRACTICE_ALLOW_SUBMIT）时可写。"""
        try:
            if self.guard is None:
                self.guard = ExamGuard(self.cfg, self.store)
            return bool(self.guard.allow_page_write(getattr(page, "url", "") or "", want_submit=want_submit))
        except Exception as exc:
            log.debug("写闸门判定失败：%s", exc)
            return False

    def _snap(self) -> Any:
        if self.guard is None:
            self.guard = ExamGuard(self.cfg, self.store)
        if self.snapshot is None:
            self.snapshot = ExamSnapshot(browser=None, cfg=self.cfg, guard=self.guard)
        return self.snapshot

    # ------------------------------------------------------------ 入口
    def run(self, course: dict[str, Any], url: str = "", chapter_key: str = "",
            limit: int = 0, page: Any = None, ask_import: bool = True,
            auto_answer: bool = False, auto_submit: bool = False) -> dict[str, int]:
        """
        course: 数据库课程字典；url: 练习/测验页面地址（为空则使用当前页面）。
        """
        own_page = False
        if page is None:
            page = self.browser.start().page
            own_page = True
            if url and not A.safe_goto(page, url, attempts=int(self.cfg.get("MAX_RETRY_PER_ITEM") or 3)):
                err("练习页面打开失败，已跳过本轮刷题")
                return {}
        A.close_popups(page)
        questions = self.extractor.extract(page)
        if not questions:
            warn("没有识别到题目。可能原因：① 页面还没进入答题态；② 学习通改版导致选择器失效。")
            info("可运行 `python main.py dump --url <该页面地址>` 导出页面结构，再在 user_config.py 里补充选择器。")
            self.browser.dump_html(page, "practice_no_question")
            return {}
        stat = {"total": len(questions), "new": 0, "dup": 0, "ai": 0, "wrong": 0, "right": 0, "skip": 0}
        session_id = self.store.start_session("practice", course_id=course.get("id"),
                                              title=f"{course.get('name', '')} 练习", url=page.url)
        section(f"刷题辅助 ｜ {course.get('name', '')} ｜ 共 {len(questions)} 题")
        if auto_answer or auto_submit:
            note = "练习/演示模式：将按 AI 答案自动填入页面" + ("，结束后自动点击提交" if auto_submit else "")
            print(f"说明：{note}（仅对**非真实考试**页面生效；请自行核对）。\n")
        else:
            print("说明：AI 只负责“给思路 + 给依据”。作答与提交必须由你本人在学习通页面完成。\n")
        cap = int(limit or self.cfg.get("MAX_ITEMS_PER_RUN") or 100)
        guard = LoopGuard(max_steps=cap + 10, stall_limit=cap + 5, name="刷题")
        for idx, q in enumerate(questions[:cap], 1):
            try:
                guard.step(key=q.fp)
            except LoopGuardTripped as exc:
                warn(str(exc))
                break
            q.chapter_key = q.chapter_key or chapter_key
            q.section_url = q.section_url or page.url
            try:
                self._handle_one(q, idx, len(questions), course, session_id, stat, chapter_key,
                                 page=page, auto_answer=auto_answer)
            except KeyboardInterrupt:
                warn("已按你的要求停止本轮刷题（Ctrl+C）。进度已保存，可稍后继续。")
                break
            except Exception as exc:
                log.exception("处理题目失败")
                err(f"第 {idx} 题处理异常：{exc}（已跳过，不影响后续）")
                stat["skip"] += 1
        if auto_submit and page is not None and self._writable(page, want_submit=True):
            try:
                if self._snap().submit_practice(page):
                    ok("练习模式：已自动点击“提交答案”")
                else:
                    warn("练习模式：未找到提交按钮，请手动提交")
            except PermissionError as exc:
                warn(f"自动提交被安全闸门拒绝（非练习模式或真实考试域名）：{exc}")
        self.store.end_session(session_id, total=stat["total"], ai_called=stat["ai"],
                               note=f"对{stat['right']} 错{stat['wrong']} 跳过{stat['skip']}")
        section("本轮小结")
        info(f"题目 {stat['total']} 道 ｜ 新入库 {stat['new']} ｜ 重复题 {stat['dup']} ｜ "
             f"AI 调用 {stat['ai']} ｜ 答对 {stat['right']} ｜ 记入错题本 {stat['wrong']}")
        try:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            interactive = False
        if interactive and confirm("是否现在导入平台判分结果（需要你先自行提交并进入“查看成绩”页）？",
                                   default_no=True):
            self.import_grading(course, page=page)
        elif not interactive:
            info("非交互环境：跳过判分导入。稍后可执行 `python main.py practice --course <课程名>` 完成导入。")
        elif not ask_import:
            info("如需导入判分结果，请在“查看成绩”页面再次运行 practice。")
        if own_page:
            pass
        return stat

    # ------------------------------------------------------------ 单题处理
    def _handle_one(self, q: Question, idx: int, total: int, course: dict[str, Any],
                    session_id: int, stat: dict[str, int], chapter_key: str = "",
                    page: Any = None, auto_answer: bool = False) -> None:
        qid, dup = self.store.upsert_question(q.to_row(), course_id=course.get("id"))
        stat["new" if not dup else "dup"] += 1
        similar = None
        if not dup:
            similar = self.store.exists_similar(q.stem, q.option_texts, threshold=0.93,
                                                course_id=course.get("id"))
        print("\n" + report.render_question(q, idx, total, dup=similar or ({"_similarity": 1.0} if dup else None)))

        hits: list[dict[str, Any]] = []
        wrongs: list[dict[str, Any]] = []
        past: list[dict[str, Any]] = []
        if self.kb is not None:
            try:
                ev = self.kb.evidence_for_question(q.as_dict(), course_id=course.get("id"),
                                                   top_k=int(self.cfg.get("KB_TOP_K") or 6))
                hits = list(ev.get("chunks") or [])
                past = list(ev.get("questions") or [])
                wrongs = list(ev.get("wrongs") or [])
            except Exception as exc:
                log.warning("本地资料检索失败：%s", exc)
        if self.store is not None:
            try:
                wrongs = [w for w in self.store.list_wrong(course_id=course.get("id"), limit=200)
                          if one_line(w.get("knowledge", "")) and any(
                              k in q.stem for k in one_line(w.get("knowledge", "")).split("；")[:1])]
            except Exception:
                log.debug("错题检索失败", exc_info=True)

        ans = None
        if self.engine is not None:
            ans = self.engine.answer(
                {**q.as_dict(), "id": qid},
                evidence={"chunks": hits, "questions": past, "wrongs": wrongs},
                mode="practice", course=course.get("name", ""), chapter=chapter_key, qno=q.no or str(idx),
            )
            stat["ai"] += 1
            print("\n" + report.render_answer(ans))
            print("\n" + report.render_evidence(hits, title="课程依据（本地资料）"))
            if ans.answer and not q.ref_answer:
                self.store.set_question_ai_result(
                    qid, answer=ans.answer, analysis=ans.analysis, knowledge=ans.knowledge,
                    answer_source="ai",
                )
            if ans.knowledge:
                self.store.exec("UPDATE questions SET knowledge=? WHERE id=?", (ans.knowledge, qid))
            if ans.needs_human:
                warn("该题 AI 判定为“需要人工确认”，请务必自己核对（题干歧义/资料不足/图片未识别等）。")
        else:
            info("AI 未启用：仅展示题目与本地资料检索结果。")

        filled = False
        if auto_answer and ans and ans.answer and not ans.needs_human and not q.has_image \
                and page is not None and self._writable(page):
            acts = plan_answer_actions(q, ans.answer)
            if acts:
                try:
                    res = self._snap().apply_practice_actions(page, acts)
                    filled = bool(res.get("ok"))
                    if filled:
                        ok(f"练习模式：已按 AI 答案自动作答（{ans.answer}）")
                except PermissionError as exc:
                    warn(f"自动作答被安全闸门拒绝（非练习模式或真实考试域名）：{exc}")
                except Exception as exc:
                    warn(f"自动作答失败：{exc}")
        if filled:
            user = (ans.answer if ans else "").strip()
        else:
            user = ask("你在页面上选择的答案（回车=稍后自行处理，w=我答错了要进错题本，q=结束）").strip()
        if user.lower() == "q":
            raise KeyboardInterrupt
        if user.lower() == "w":
            if self.wrongbook:
                self.wrongbook.record(qid, reason="练习时自认不会", course_id=course.get("id"),
                                      chapter_key=chapter_key)
            else:
                self.store.mark_wrong(qid, reason="练习时自认不会", course_id=course.get("id"),
                                      chapter_key=chapter_key)
            stat["wrong"] += 1
            return
        if not user:
            stat["skip"] += 1
            return
        correct: bool | None = None
        ref = q.ref_answer or (ans.answer if ans else "")
        if q.ref_answer:
            correct = answers_equal(q.kind, user, q.ref_answer)
        elif self.engine is not None and self.engine.ai.enabled:
            correct, why = self.engine.grade(q.as_dict(), user, ref)
            if correct is None:
                info(f"该题需人工判分：{why}")
        else:
            info("未配置 AI，且题内无参考答案，请自行核对。")
        self.store.add_answer(session_id, qid, q.no or str(idx), user, (ans.answer if ans else ""),
                              (ans.confidence if ans else "需要人工确认"),
                              None if correct is None else int(correct))
        if correct is True:
            stat["right"] += 1
            self.store.mark_correct(qid)
            ok("回答正确")
        elif correct is False:
            stat["wrong"] += 1
            if self.wrongbook:
                self.wrongbook.record(qid, reason="练习答错", course_id=course.get("id"),
                                      chapter_key=chapter_key)
            else:
                self.store.mark_wrong(qid, reason="练习答错", course_id=course.get("id"),
                                      chapter_key=chapter_key)
            err(f"回答错误，已记入错题库（参考：{ref or '待补充'}）")
        if not filled:
            info("提示：请在**学习通答题页面**自行勾选并提交，本程序不会代你提交。")

    # ------------------------------------------------------------ 判分导入
    def import_grading(self, course: dict[str, Any], page: Any = None) -> dict[str, int]:
        page = page or self.browser.start().page
        rows = self.extractor.read_grading(page)
        if not rows:
            warn("当前页面没有读到批改结果（需要在“查看成绩/答题结果”页面执行）。")
            return {}
        if self.wrongbook is None:
            err("错题库未初始化")
            return {}
        return self.wrongbook.import_grading(rows, course_id=course.get("id"))

    # ------------------------------------------------------------ 强化练习
    def drill_wrong(self, course: dict[str, Any], limit: int = 10) -> dict[str, int]:
        if not self.wrongbook:
            err("错题库未初始化")
            return {}
        return self.wrongbook.practice_loop(course_id=course.get("id"), limit=limit, kb=self.kb)