# -*- coding: utf-8 -*-
"""
exam.snapshot —— 考试页面“只读”快照

只做三件事：
  1) 读取考试页面说明文字（供 guard 判断规则是否允许 AI）；
  2) 读取题号/题目/选项/题型；
  3) 只读展示倒计时（绝不修改，也不隐藏）。
默认对考试页面只读；需要写操作时会被 ExamGuard.refuse 直接拒绝。
练习/演示模式（PRACTICE_MODE 且目标非真实考试域名）下，可用下面的
apply_practice_actions / submit_practice 显式写方法——真实考试域名永远被锁死。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from browser import actions as A
from course.selectors import SELECTORS
from questions.extractor import QuestionExtractor
from questions.models import Question
from utils.logger import get_logger
from utils.text import clean_text, one_line
from utils.time_util import fmt_clock, parse_clock

log = get_logger("exam.snapshot")

INSTRUCTION_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const bits = [];
  document.querySelectorAll('h1,h2,h3,.tips,.notice,.prompt,.exam-title,.test-title,.tit,.time').forEach((el) => {
    const t = norm(el.innerText);
    if (t && t.length < 600) bits.push(t);
  });
  return norm(document.title) + '\n' + bits.slice(0, 40).join('\n');
}
"""


@dataclass
class ExamSnapshot:
    browser: Any
    cfg: Any
    guard: Any = None
    extractor: QuestionExtractor = field(default_factory=QuestionExtractor)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extractor is None:
            self.extractor = QuestionExtractor(self.cfg)

    # ------------------------------------------------------------ 规则文本
    def read_instructions(self, page: Any) -> str:
        try:
            texts = [str(page.title() or "")]
        except Exception:
            texts = []
        for frame in A.frames_of(page):
            got = A.js_eval(frame, INSTRUCTION_JS)
            if got:
                texts.append(str(got))
        blob = clean_text("\n".join(texts))
        return blob[:6000]

    # ------------------------------------------------------------ 题目
    def read_questions(self, page: Any) -> list[Question]:
        qs = self.extractor.extract(page)
        for q in qs:
            q.section_url = q.section_url or getattr(page, "url", "")
        return qs

    def current_question(self, page: Any, questions: Sequence[Question] | None = None) -> tuple[Question | None, list[Question]]:
        """“当前题目”= 视口垂直中心最近的那道题（纯读取，不滚动、不点击）。"""
        qs = list(questions if questions is not None else self.read_questions(page))
        if not qs:
            return None, qs
        center = min(qs, key=lambda q: abs(float(q.raw.get("viewport_center_dist") or 0)))
        return center, qs

    def question_at(self, questions: Sequence[Question], which: str) -> Question | None:
        which = one_line(str(which))
        for q in questions:
            if str(q.no) == which:
                return q
        try:
            idx = int(which) - 1
            if 0 <= idx < len(questions):
                return questions[idx]
        except ValueError:
            return None
        return None

    # ------------------------------------------------------------ 倒计时（只读）
    def countdown(self, page: Any) -> tuple[str, float | None]:
        raws: list[str] = []
        for frame in A.frames_of(page):
            for sel in SELECTORS["exam_time_left"]:
                txt = A.text_of(frame, [sel])
                if txt:
                    raws.append(txt)
        for text in raws:
            seconds = parse_clock(text)
            if seconds is not None:
                return one_line(text)[:60], seconds
        return "", None

    # ------------------------------------------------------------ 页面指纹（判断是否换题）
    @staticmethod
    def signature(page: Any) -> str:
        try:
            body = A.js_eval(page, "() => (document.body.innerText||'').slice(0,4000)") or ""
        except Exception:
            body = ""
        return one_line(str(body))[:400]

    # ------------------------------------------------------------ 仅允许的页面动作：滚动翻页
    def scroll_next(self, page: Any, direction: int = 1) -> bool:
        """
        只做“向下/向上滚动一屏”，等价于你自己转动鼠标滚轮。
        不点击、不填写、不修改布局；受 EXAM_ALLOW_SCROLL 控制。
        """
        if self.guard is not None and not self.guard.enabled:
            # 未通过合规闸门时连“代为滚动”都不允许，避免任何隐式页面控制
            self.guard.refuse("scroll(考试 AI 模式未启用)")
            return False
        if not self.cfg.get("EXAM_ALLOW_SCROLL"):
            if self.guard is not None:
                self.guard.refuse("scroll(被 EXAM_ALLOW_SCROLL=False 禁止)")
            return False
        try:
            height = int(A.js_eval(page, "() => window.innerHeight") or 700)
            page.mouse.wheel(0, direction * int(height * 0.85))
            if self.guard is not None:
                self.guard._audit("scroll", f"direction={direction}")
            return True
        except Exception as exc:
            log.warning("滚动失败：%s", exc)
            return False

    # -------------------------------------------- 练习/演示模式：受闸门保护的写操作
    def _assert_writable(self, page: Any, action: str, want_submit: bool = False) -> None:
        """写操作前置闸门：仅当 guard 判定练习模式可写时放行，否则抛 PermissionError。"""
        if self.guard is None:
            return
        self.guard.require_page_write(getattr(page, "url", "") or "", action, want_submit=want_submit)

    def apply_practice_actions(self, page: Any, actions: Sequence[dict]) -> dict:
        """
        仅在“自己的练习/演示页面”生效（真实考试域名会被 _assert_writable 拒绝）。
        actions: [{"selector": <SELECTORS 键或 CSS>, "op": "click|check|fill|select", "value": str}]
        跨 iframe 定位逐条执行，返回 {"ok", "done", "failed"}。
        """
        self._assert_writable(page, "apply_practice_actions")
        done: list[dict] = []
        failed: list[dict] = []
        for spec in actions:
            sel = spec.get("selector")
            op = str(spec.get("op") or "click").lower()
            val = spec.get("value", "")
            if not sel:
                failed.append({"spec": spec, "reason": "缺少 selector"})
                continue
            _frame, el = A.in_frames(page, sel)
            if el is None:
                failed.append({"selector": sel, "op": op, "reason": "未找到元素"})
                continue
            try:
                if op in ("click", "check"):
                    # 学习通选项常被浮层(.subNav/.tkTopic_oper)遮挡，常规点击会被拦截 →
                    # 用原生 JS click（或 force）绕过命中测试。
                    try:
                        el.evaluate("node => node.click()")
                    except Exception:
                        el.click(force=True)
                elif op == "fill":
                    el.fill(str(val))
                elif op == "select":
                    el.select_option(str(val))
                else:
                    failed.append({"selector": sel, "op": op, "reason": "未知 op"})
                    continue
                done.append({"selector": sel, "op": op})
            except Exception as exc:
                failed.append({"selector": sel, "op": op, "reason": str(exc)})
        return {"ok": len(done), "done": done, "failed": failed}

    def submit_practice(self, page: Any, mode: str = "submit") -> bool:
        """练习/演示模式点击“提交答案”或“暂时保存”（需 PRACTICE_ALLOW_SUBMIT=True 且非真实考试域名）。

        mode="save" 点「暂时保存/自动保存」，只存答案不交卷；mode="submit"（默认）点「提交」。
        对视频随堂题：提交后会自动点“继续”，让播放接着进行。
        """
        saving = str(mode or "submit").lower() == "save"
        self._assert_writable(page, f"submit_practice({'save' if saving else 'submit'})", want_submit=True)
        _frame, el = A.in_frames(page, "exam_save_btn" if saving else "exam_submit_btn")
        clicked = False
        if el is not None:
            try:
                try:
                    el.evaluate("node => node.click()")
                except Exception:
                    el.scroll_into_view_if_needed()
                    el.click(force=True)
                clicked = True
            except Exception as exc:
                log.warning("提交点击失败：%s", exc)
        if clicked and not saving:
            # 交卷常有“确定要提交吗？”弹窗：出现就点「确定」
            import time as _t
            _t.sleep(0.8)
            _cf2, cf = A.in_frames(page, "exam_confirm_btn")
            if cf is not None:
                try:
                    if cf.is_visible():
                        cf.evaluate("node => node.click()")
                        log.info("已点击交卷确认「确定」")
                except Exception as exc:
                    log.debug("确认弹窗点击跳过：%s", exc)
        _cf, cont = A.in_frames(page, "video_quiz_continue")
        if cont is not None:
            try:
                if cont.is_visible():   # 只有视频弹题提交后自身出现的“继续”才点，避免误触页面其它按钮
                    cont.evaluate("node => node.click()")
            except Exception as exc:
                log.debug("“继续”点击跳过：%s", exc)
        return clicked

    # ------------------------------------------------------------ 兜底：任何裸写方法名一律拒绝
    def __getattr__(self, name: str) -> Any:
        # 防御式：万一将来有代码调用 fill/click/submit 类方法，直接拒绝并留痕
        if re.match(r"^(fill|click|submit|check|type|select|set_|drag|press)", name):
            def _denied(*a: Any, **k: Any) -> None:
                guard = getattr(self, "guard", None)
                detail = f"ExamSnapshot.{name}"
                if guard is not None:
                    guard.refuse(detail)
                raise PermissionError(f"拒绝执行考试页面写操作：{detail}")

            return _denied
        raise AttributeError(name)