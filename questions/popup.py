# -*- coding: utf-8 -*-
"""
questions.popup —— 随堂弹题探测（只读显示 + AI 解析，不点选、不提交、不跳过）

用途：视频播放过程中学习通会弹出随堂题，此时你没法切到终端打字，
所以由程序主动把题目与 AI 解析打印到终端，你只需在浏览器里自己点选、提交。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from exam.guard import ExamGuard
from exam.snapshot import ExamSnapshot
from questions.autofill import plan_answer_actions
from questions.extractor import QuestionExtractor
from ui import report
from utils.console import err, ok, warn
from utils.logger import get_logger

log = get_logger("questions.popup")

BANNER = "!" * 64


@dataclass
class PopupWatcher:
    """每轮播放进度检查一次页面是否出现新题目。"""

    cfg: Any
    store: Any
    engine: Any = None                       # ai.responder.AnswerEngine
    kb: Any = None                           # knowledge_base 门面（可选）
    course_id: int | None = None
    chapter_key: str = ""
    extractor: QuestionExtractor = field(default=None)   # type: ignore[assignment]
    on_event: Callable[[str], None] | None = None        # 通知上层（如暂停播放）
    on_out: Callable[[str, str], None] | None = None     # 把事件回流到界面：(level, text)
    min_interval_sec: float = 5.0
    _seen: set = field(default_factory=set)
    _last: float = 0.0
    awaiting: bool = False          # 出现弹题、等用户本人作答中
    awaiting_since: float = 0.0
    guard: Any = None               # exam.guard.ExamGuard（练习模式写闸门）
    snapshot: Any = None            # exam.snapshot.ExamSnapshot（受闸门保护的写操作）
    quiz_attempts: dict = field(default_factory=dict)   # {题目指纹: [已试过的选项下标]}

    def __post_init__(self) -> None:
        if self.extractor is None:
            self.extractor = QuestionExtractor(self.cfg)

    def _emit(self, level: str, text: str) -> None:
        """把关键事件送到界面输出流；未设 on_out 时静默（不影响 CLI 的 print）。"""
        if self.on_out is not None:
            try:
                self.on_out(level, text)
            except Exception:
                log.debug("on_out 回调异常", exc_info=True)

    def _ai_choice_indices(self, q: Any, answer: str) -> list[int]:
        """把 AI 答案映射成选项下标（单选/多选按字母，判断按 对/错 语义）。"""
        from questions.models import letters_from, normalize_judge
        idxs: list[int] = []
        if q.kind in ("single", "multi"):
            want = set(letters_from(answer or ""))
            for i, o in enumerate(q.options):
                if str(o.get("label", "")).upper() in want:
                    idxs.append(i)
        elif q.kind == "judge":
            # 判断题 AI 可能答“对/错/正确/错误”，也可能带字母如“A. 对”——先按字母精确定位，再回退语义
            want = set(letters_from(answer or ""))
            if want:
                for i, o in enumerate(q.options):
                    if str(o.get("label", "")).upper() in want:
                        idxs.append(i)
                        return idxs
            want_true = normalize_judge(answer or "") == "正确"
            for i, o in enumerate(q.options):
                if (normalize_judge(str(o.get("text", ""))) == "正确") == want_true:
                    idxs.append(i)
                    break
        return idxs

    def _auto_answer_quiz(self, page: Any, q: Any, ans: Any, submit_ok: bool) -> None:
        """填 AI 答案并提交；若 PRACTICE_RETRY_ON_WRONG 开启，则答错自动换未试过的选项直到答对。"""
        import time
        from browser import quiz
        try:
            inputs = quiz.find_quiz_inputs(page)
        except Exception:
            inputs = []
        retry_on = bool(self.cfg.get("PRACTICE_RETRY_ON_WRONG"))
        if not inputs:
            # 非“ans-videoquiz”结构：退回通用快照写入一次（不重试），同样播报成功/失败
            acts = plan_answer_actions(q, ans.answer)
            if not acts:
                self._finish_quiz(False, "未能定位可作答的选项/输入框")
                return
            res = self._snap().apply_practice_actions(page, acts)
            log.info("随堂题写入结果：res=%s", res)
            if not res.get("ok"):
                self._finish_quiz(False, "选项/输入框定位失败，未能作答")
                return
            if submit_ok:
                _m = str(self.cfg.get("PRACTICE_SUBMIT_ACTION") or "submit")
                if self._snap().submit_practice(page, mode=_m):
                    self._finish_quiz(True, f"已作答并{'暂时保存' if _m.lower() == 'save' else '提交'}（{ans.answer}）")
                else:
                    self._finish_quiz(False, "已作答，但没找到提交/保存按钮，请手动处理")
            else:
                self._finish_quiz(True, f"已作答，未自动提交（{ans.answer}）")
            return

        n = len(inputs)
        # 视频弹题：不看“答完:提交/暂时保存”下拉——必须点「提交」才会出现「继续」、恢复播放，故总是提交
        first = self._ai_choice_indices(q, ans.answer) or ([0] if n else [])
        order = [i for i in first if 0 <= i < n] + [i for i in range(n) if i not in first]
        tried = set(self.quiz_attempts.get(q.fp, []))
        for k in order:
            if k in tried:
                continue
            tried.add(k)
            self.quiz_attempts[q.fp] = sorted(tried)
            try:
                quiz.pick_option(inputs, k)
                # 提交前复查勾选是否还在（平台可能重置/反选），没勾上就再点一次
                if not quiz.is_checked(inputs, k):
                    quiz.pick_option(inputs, k)
                    time.sleep(0.3)
                if not quiz.is_checked(inputs, k):
                    self._finish_quiz(False, "选项未能保持勾选（平台已重置），请手动作答")
                    return
                if not submit_ok:
                    self._finish_quiz(True, "已勾选答案，但未开启「允许自动提交」——请在窗口点「提交」→「继续」恢复播放")
                    return
                if not quiz.submit_quiz(page):
                    self._finish_quiz(False, "没找到弹题提交按钮")
                    return
                # 提交后只等“回答错误”：出现=答错；一段时间没出现=答对(平台只让视频续播、无文字)
                wrong = ""
                for _ in range(16):
                    time.sleep(0.3)
                    if quiz.read_result(page) == "wrong":
                        wrong = "wrong"
                        break
                res = wrong or "correct"
            except Exception as exc:
                self._finish_quiz(False, f"作答出错：{exc}")
                return
            log.info("视频弹题作答：选第 %s 项 结果=%s", k, res)
            self.awaiting = False
            if res == "correct":
                quiz.click_continue(page)
                self.quiz_attempts.pop(q.fp, None)
                self._finish_quiz(True, f"选中 {chr(65 + k)}，回答正确（视频继续播放）")
                return
            if retry_on:
                self._emit("warn", f"弹题答错（选项 {chr(65 + k)}），自动换下一个…")
                continue
            self._finish_quiz(False, f"选项 {chr(65 + k)} 错误（未开“答错自动重试”）")
            return
            return
        self._finish_quiz(False, f"{n} 个选项都已试，仍未答对")

    def _finish_quiz(self, success: bool, detail: str) -> None:
        """每条随堂题处理完，在消息框给出统一的 ✅成功 / ❌失败 结论。"""
        self.awaiting = False
        if success:
            ok(f"视频弹题作答成功：{detail}")
            self._emit("ok", f"✅ 随堂题【成功】{detail}")
        else:
            warn(f"视频弹题作答失败：{detail}")
            self._emit("err", f"❌ 随堂题【失败】{detail}")

    # ------------------------------------------------------------ 练习模式写闸门
    def _writable(self, page: Any, want_submit: bool = False) -> bool:
        """仅当 PRACTICE_MODE 开且弹题所在页非真实考试域名（提交还需 PRACTICE_ALLOW_SUBMIT）时可写。"""
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

    def reset(self) -> None:
        self._seen.clear()
        self._last = 0.0
        self.awaiting = False
        self.awaiting_since = 0.0
        self.quiz_attempts.clear()

    def notify_progress(self) -> None:
        """播放恢复推进 = 用户已作答，解除等待状态。"""
        if self.awaiting:
            self.awaiting = False
            log.info("用户已作答，播放恢复")

    # ------------------------------------------------------------ 主入口
    def check(self, page: Any) -> int:
        """
        检查页面是否出现新题目；有则打印题目 + AI 解析。
        返回新发现并处理的题目数。任何异常都不影响播放主流程。
        """
        now = time.time()
        if now - self._last < float(self.min_interval_sec or 0):
            return 0
        self._last = now
        try:
            # 视频弹题优先：逐 .tkItem 抽干净题（题干短、只含本问选项、不被字幕/设置污染），
            # 避免通用抽取把整块揉成“超长题干+杂选项+带图”的假题而被严格判定挡掉。
            from browser import quiz as _quiz
            raws = _quiz.extract_items(page)
        except Exception as exc:
            log.debug("视频弹题逐题解析失败：%s", exc)
            raws = []
        if raws:
            questions = []
            from questions.models import Question
            for r in raws:
                try:
                    q = Question.from_raw(r)
                    q.section_url = r.get("_url", "") or (getattr(page, "url", "") or "")
                    questions.append(q)
                except Exception:
                    log.debug("视频弹题行转换失败", exc_info=True)
        else:
            try:
                questions = self.extractor.extract(page)
            except Exception as exc:
                log.debug("弹题探测失败：%s", exc)
                return 0
        # 只把“当前真的出现在视口里的题”当作弹题事件：
        # 题目常常预先存在于 DOM（不可见），按内容去重会导致真正弹出时不再提示。
        # 弹窗容器（modal）即便视口判定异常也认；其余要求在视口内
        shown = [q for q in questions if q and (getattr(q, "in_view", True) or getattr(q, "modal", False))]
        visible = [q for q in shown if self._is_real_question(q)]
        live_fps = {q.fp for q in visible}
        # 诊断：每个被丢弃的候选只报一次原因（不刷屏），便于定位卡在“可见/真题/选项/含图”哪一环
        if not hasattr(self, "_drop_logged"):
            self._drop_logged = set()
        vis_set = {q.fp for q in visible}
        for q in questions:
            if not q or q.fp in vis_set or q.fp in self._drop_logged:
                continue
            self._drop_logged.add(q.fp)
            if q not in shown:
                reason = "不在视口且非弹窗容器"
            else:
                reason = "判为非真题/选项未解析"
            nopt = len([o for o in (getattr(q, "options", None) or []) if str(o.get("text", "")).strip()])
            log.info("随堂题候选被丢弃(%s)：kind=%s inView=%s modal=%s hasImage=%s 选项数=%s 题干=%s",
                     reason, q.kind, getattr(q, "in_view", None), getattr(q, "modal", None),
                     getattr(q, "has_image", None), nopt, q.preview(30))
        # 已消失的题目从“见过”集合里移除，下次再弹出仍会提示
        self._seen -= {fp for fp in self._seen if fp not in live_fps}
        fresh = [q for q in visible if q.fp not in self._seen]
        if not fresh:
            return 0
        for q in fresh:
            self._seen.add(q.fp)
            self.awaiting = True
            self.awaiting_since = time.time()
            log.info("检测到随堂题（inView=%s modal=%s）：%s", getattr(q, "in_view", None),
                     getattr(q, "modal", None), q.preview(50))
            try:
                self._handle(q, page)
            except Exception as exc:
                log.exception("弹题处理失败")
                err(f"弹题处理失败：{exc}")
        return len(fresh)

    NAV_WORDS = ("返回课程", "章节详情", "下一节", "上一节", "待完成任务点", "已完成任务点",
                 "目录", "讨论", "笔记", "标注", "章节测验", "重播", "全屏")

    @classmethod
    def _is_real_question(cls, q: Any) -> bool:
        """
        只认真题。三道关卡，任一不过就丢弃：

        1. 页面上必须有作答控件（选项或作答输入框）——页面搜索框不算，
           所以额外要求输入框所在容器是题目容器（有选项或有题干编号）；
        2. 题干不能是目录/导航文本（复用知识库的噪声判定 + 导航词 + 小节编号密度）；
        3. 题干长度与题型必须合理（真题不会是一整页目录，也不会是 unknown）。
        """
        import re

        from questions.models import looks_like_question

        stem = str(getattr(q, "stem", "") or "")
        if not stem or len(stem) > 320:            # 整页目录会被拼成超长“题干”
            return False
        if any(w in stem for w in cls.NAV_WORDS):
            return False
        if len(re.findall(r"\d+\.\d+\s*\S", stem)) >= 3:   # 三条以上“x.y 标题”=目录
            return False
        try:
            from knowledge_base.extractors import is_noise

            if is_noise(stem):
                return False
        except Exception:
            pass
        opts = [o for o in (getattr(q, "options", None) or []) if str(o.get("text", "")).strip()]
        blanks = [b for b in ((q.raw or {}).get("blanks") or []) if str(b.get("value", "")).strip()
                  or int(b.get("wide") or 0) > 300]
        has_widget = bool(opts) or bool(blanks)
        if not has_widget:
            return False
        if q.kind == "unknown":
            return False
        return looks_like_question(stem, getattr(q, "option_texts", []))

    # ------------------------------------------------------------ 单题处理
    def _handle(self, q: Any, page: Any = None) -> None:
        writable = page is not None and self._writable(page)
        submit_ok = writable and self._writable(page, want_submit=True)
        print("\n" + BANNER)
        if writable:
            warn(f"出现随堂题目（{q.kind_label}）—— 练习/演示模式：将尝试自动作答"
                 + ("并提交，视频继续" if submit_ok else "，请你核对后提交"))
        else:
            warn(f"出现随堂题目（{q.kind_label}）—— 请你本人在学习通窗口点选/填写并提交，本程序不会代答")
        print(BANNER)
        print(report.render_question(q, 1, 1))
        self._emit("alert", f"检测到随堂题（{q.kind_label}）：{q.preview(40)}")
        self._emit("raw", report.render_question(q, 1, 1))
        qid, _dup = self.store.upsert_question(q.to_row(), course_id=self.course_id)
        self.store.log_study(None, "popup", f"{q.kind_label}｜{q.preview(60)}", None, self.course_id)
        if self.on_event:
            try:
                self.on_event("popup")
            except Exception:
                log.debug("on_event 回调异常", exc_info=True)
        if self.engine is None or getattr(self.engine, "ai", None) is None or not self.engine.ai.enabled:
            err("未启用 AI：仅显示题目，不生成解析。")
            self._emit("warn", "未启用 AI：仅显示随堂题，不自动作答")
            return
        evidence = {"chunks": [], "questions": [], "wrongs": []}
        if self.kb is not None:
            try:
                ev = self.kb.evidence_for_question(q.as_dict(), course_id=self.course_id,
                                                   top_k=int(self.cfg.get("KB_TOP_K") or 5))
                evidence = {"chunks": list(ev.get("chunks") or []),
                            "questions": list(ev.get("questions") or []),
                            "wrongs": list(ev.get("wrongs") or [])}
            except Exception as exc:
                log.warning("弹题资料检索失败：%s", exc)
        ans = None
        try:
            ans = self.engine.answer({**q.as_dict(), "id": qid}, evidence=evidence, mode="practice",
                                     chapter=self.chapter_key, qno=q.no)
            log.info("弹题作答判定：answer=%r needs_human=%s has_image=%s writable=%s submit_ok=%s",
                     (ans.answer or "")[:12], ans.needs_human, q.has_image, writable, submit_ok)
            print("\n" + report.render_answer(ans))
            self._emit("ai", report.render_answer(ans))
            if ans.answer:
                self.store.set_question_ai_result(qid, answer=ans.answer, analysis=ans.analysis,
                                                  knowledge=ans.knowledge, answer_source="ai")
            if ans.needs_human:
                warn("该题被判定“需要人工确认”，请自己核对后再选。")
                self._emit("warn", "该题需人工确认，未自动作答")
        except Exception as exc:
            log.exception("弹题 AI 解析失败")
            err(f"AI 解析失败：{exc}")
            self._emit("err", f"AI 解析失败：{exc}")
        # 练习/演示模式：自动作答随堂弹题；若开启“答错重试”，答错自动换未试过的选项直到答对
        force = bool(self.cfg.get("PRACTICE_FORCE_ANSWER"))
        retry_on = bool(self.cfg.get("PRACTICE_RETRY_ON_WRONG"))
        if (writable and ans is not None and ans.answer and not q.has_image
                and (force or retry_on or not ans.needs_human)):
            try:
                self._auto_answer_quiz(page, q, ans, submit_ok)
            except PermissionError as exc:
                warn(f"自动作答被安全闸门拒绝：{exc}")
                self._emit("warn", f"自动作答被闸门拒绝：{exc}")
            except Exception as exc:
                warn(f"随堂题自动作答失败：{exc}")
                self._emit("err", f"随堂题自动作答失败：{exc}")
        print(BANNER + "\n")