# -*- coding: utf-8 -*-
"""Shared single-thread browser scheduler for desktop interfaces."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from ui.events import COURSE_RESULTS, WorkerEvent, is_course_action
from utils.logger import get_logger
from utils.text import one_line
from utils.time_util import fmt_duration

log = get_logger("ui.workbench")

RequestStatus = Literal["succeeded", "failed", "rejected", "cancelled"]


@dataclass
class RequestContext:
    request_id: str
    action: str
    cancel_version: int
    error: str = ""
    course_id: int | None = None


class Scheduler(threading.Thread):
    """单一浏览器线程：交替处理“快捷命令”和“一个播放心跳/长任务步”，谁都不独占线程。"""

    # 会驱动页面/耗时的命令：有协作式任务在跑时先拒绝，避免与连做互相干扰（停止/开关类不在此列）
    _NEEDS_IDLE = {"login", "courses", "catalog", "select_course", "open_item", "play",
                   "task_tab", "parse", "auto", "auto_chain", "dump", "search", "wrong_list", "ai_check",
                   "grades", "discuss_preview", "discuss_fill_next", "discuss_auto",
                   "discuss_auto_confirm", "discuss_confirm", "discuss_discard", "discuss_not_published", "read"}

    AUTO_READ_COURSES_AFTER_LOGIN = True      # 登录成功后自动“读取课程”，形成一步接一步的流程

    def __init__(self, cfg: Any, out: "queue.Queue[tuple[str, object]]") -> None:
        super().__init__(name="workbench-sched", daemon=True)
        self.cfg = cfg
        self.out = out
        self.jobs: "queue.Queue[tuple[str, dict[str, Any]] | None]" = queue.Queue()
        self.stop_event = threading.Event()      # 仅用于关闭整条调度线程
        self.task_stop = threading.Event()       # 仅用于取消当前长任务（连做/自动作答）
        self.login_cancel = threading.Event()
        self.login_pending = threading.Event()   # 登录等待是否正在进行（供“取消登录”判断走哪条路径）
        self.login_state: Literal["unknown", "waiting", "signed_in", "signed_out"] = "unknown"
        self.task: Any = None                    # 协作式长任务（生成器）
        self.active_action = ""
        self._active_request: RequestContext | None = None
        self._task_request: RequestContext | None = None
        self._task_course_id: int | None = None
        self._requests: dict[str, RequestContext] = {}
        self._request_lock = threading.RLock()
        self._cancel_version = 0
        self._command_cancel_version: int | None = None
        self._wake_at = 0.0                      # 任务被 _wait 挂起时的唤醒时间
        self.app: Any = None
        self.browser: Any = None
        self.crawler: Any = None
        self.kb: Any = None
        self.engine: Any = None
        self.practice: Any = None
        self.popup: Any = None
        self.vwatcher: Any = None
        self.guard: Any = None
        self.discuss: Any = None           # DiscussionRunner
        self._discuss_drafts: dict = {"new_post": [], "reply": []}   # 发表/回复两个模块各自草稿
        self._discuss_cursor: dict = {"new_post": 0, "reply": 0}
        self._discuss_pending: dict = {}
        self._discuss_confirmation: dict | None = None
        self._discuss_confirmation_id = 0
        self._discuss_summary: str = ""
        self.session: Any = None          # VideoWatcher.Session
        self._session_tick_at = 0.0
        self.course: dict[str, Any] | None = None
        self.current_item: dict[str, Any] | None = None
        self.auto_next = False
        self._deferred_next: tuple[int | None, object, int] | None = None
        self.ready = threading.Event()
        self.error = ""
        from ui.ai_worker import AIWorker
        self._ai_workers = AIWorker()

    # ------------------------------------------------------------ 生命周期
    def run(self) -> None:
        initialized = False
        try:
            import main as entry
            try:
                self.app = entry.build_app()
                self.kb = self.app.need_kb()
                cid = self.app.store.get_meta("current_course", "")
                if cid:
                    self.course = self.app.store.get_course(cid)
                initialized = True
            except Exception as exc:
                self.error = str(exc)
                self._emit(("err", f"初始化失败：{exc}"))
                return
            finally:
                self.ready.set()
            while not self.stop_event.is_set():
                self._pump_once()
        finally:
            self.stop_event.set()
            self.task_stop.set()
            self._finish_task("cancelled", "桌面服务已关闭", close=True)
            self._cancel_pending_requests("cancelled" if initialized else "failed",
                                          "桌面服务已关闭" if initialized else f"初始化失败：{self.error}")
            self._ai_workers.shutdown()

    def _emit(self, event: tuple[str, object]) -> None:
        level, payload = event
        request = self._active_request
        if level == "err" and request is not None:
            request.error = str(payload)
        if request is not None:
            course_id = request.course_id
        elif self.task is not None:
            course_id = self._task_course_id
        else:
            course_id = self._cid() if level in COURSE_RESULTS else None
        self.out.put(WorkerEvent(level, payload, course_id=course_id,
                                 request_id=request.request_id if request is not None else None))

    def _finish_request(self, request: RequestContext | None, status: RequestStatus,
                        message: str = "") -> None:
        if request is None:
            return
        with self._request_lock:
            if self._requests.pop(request.request_id, None) is None:
                return
            if status == "succeeded" and request.error:
                status, message = "failed", request.error
            if not message:
                message = {"succeeded": "操作已完成", "failed": "操作未完成",
                           "rejected": "操作已拒绝", "cancelled": "操作已取消"}[status]
            self.out.put(WorkerEvent("request_finished", {"request_id": request.request_id,
                                               "action": request.action, "status": status,
                                               "message": message}, course_id=request.course_id,
                                      request_id=request.request_id))

    def _cancel_pending_requests(self, status: RequestStatus = "cancelled",
                                 message: str = "桌面服务已关闭") -> None:
        with self._request_lock:
            for request in list(self._requests.values()):
                if request is not self._active_request and request is not self._task_request:
                    self._finish_request(request, status, message)

    def _pump_once(self) -> bool:
        """跑一小步：优先消费快捷命令，其次推进协作式任务/播放心跳。谁都不独占线程。"""
        if self.stop_event.is_set():
            self._finish_task("cancelled", "桌面服务已关闭", close=True)
            self._cancel_pending_requests()
            return True
        job = None
        try:
            job = self.jobs.get(timeout=self._job_timeout())
        except queue.Empty:
            pass
        if job is not None:
            action, kw = job
            kw = dict(kw)
            request = kw.pop("_request_context", None)
            if action == "quit":
                self.shutdown()
                self._finish_task("cancelled", "桌面服务已关闭", close=True)
                return True
            with self._request_lock:
                if self.stop_event.is_set():
                    self._finish_request(request, "cancelled", "桌面服务已关闭")
                    return True
                self._active_request = request
                if request is not None and is_course_action(action):
                    target = kw.get("course_id") if action == "select_course" else self._cid()
                    request.course_id = target if isinstance(target, int) and not isinstance(target, bool) else None
                self._command_cancel_version = self._cancel_version
            if action != "stop_play" and (request is None or request.cancel_version == self._cancel_version):
                self.login_cancel.clear()
            if self.task is not None and action in self._NEEDS_IDLE:
                message = "有作答任务正在进行，请先点「停止」再操作（避免与连做互相干扰）。"
                self._emit(("warn", message))
                self._finish_request(request, "rejected", message)
                self._active_request = None
                self._command_cancel_version = None
            else:
                try:
                    self.active_action = action
                    previous_task = self.task
                    getattr(self, "do_" + action)(**kw)
                except Exception as exc:
                    log.exception("任务失败：%s", action)
                    self._emit(("err", f"{action} 没完成：{exc}"))
                    if self.task is not previous_task and self.task is not None:
                        self._finish_task("failed", str(exc), close=True)
                    self._finish_request(request, "failed", str(exc))
                finally:
                    if self._task_request is not request or self.task is None:
                        cancelled = self.stop_event.is_set() or (action == "login"
                                    and self.login_cancel.is_set() and self.login_state != "signed_in")
                        self._finish_request(request, "cancelled" if cancelled else "succeeded",
                                             "操作已取消" if cancelled else "")
                    self._active_request = None
                    self._command_cancel_version = None
                    self.active_action = ""
        if self.stop_event.is_set():
            self._finish_task("cancelled", "桌面服务已关闭", close=True)
            self._cancel_pending_requests()
            return True
        self._step_task()
        self._continue_auto_next()
        self._tick_session()
        return False

    def _start_task(self, gen: Any, *, preserve_session: bool = False) -> None:
        """登记协作式长任务；保留已接受的队列命令，由调度器逐条执行或明确拒绝。"""
        if self.task is not None:
            raise RuntimeError("当前长任务尚未结束，请先停止")
        with self._request_lock:
            cancel_version = (self._active_request.cancel_version if self._active_request is not None
                              else self._command_cancel_version)
            if cancel_version is None or cancel_version == self._cancel_version:
                self.task_stop.clear()
        self._wake_at = 0.0
        if self.session is not None and not preserve_session:
            self.session = None
            self._emit(("info", "开始作答：已停止当前视频播放（二者互斥）。"))
        self.task = gen
        self._task_request = self._active_request
        self._task_course_id = self._cid()

    def _ai_wait(self, fn: Any, *args: Any, cancelled: threading.Event | None = None,
                 **kwargs: Any) -> Any:
        """Await plain-data work without entering the browser from another thread."""
        version = self._cancel_version
        course_id = self._cid()
        item_id = (self.current_item or {}).get("id")
        cancelled = cancelled or threading.Event()
        future = self._ai_workers.submit(fn, *args, **kwargs)
        try:
            while not future.done():
                yield from self._wait(.05)
            if (self.stop_event.is_set() or self.task_stop.is_set() or version != self._cancel_version
                    or course_id != self._cid() or item_id != (self.current_item or {}).get("id")):
                return None
            return future.result()
        finally:
            cancelled.set()
            future.cancel()

    def _ai_answer(self, question: dict, **kwargs: Any) -> Any:
        from ui.ai_worker import CallLedger, capture_engine
        ledger = CallLedger()
        cancelled = threading.Event()
        engine = capture_engine(self.engine, self.cfg, ledger, cancelled)
        answer = yield from self._ai_wait(engine.answer, question, cancelled=cancelled, **kwargs)
        if answer is None:
            return None
        for call in ledger.calls:
            self._store().log_ai_call(*call)
        if answer is not None and getattr(answer, "error", ""):
            self._emit(("err", answer.error))
        return answer

    def _ai_vision(self, ai: Any, image: str) -> Any:
        from ai.client import AIClient
        from ui.ai_worker import CapturedConfig
        from utils.visocr import recognize_image
        cancelled = threading.Event()
        client = AIClient(CapturedConfig(ai.cfg), cancel_event=cancelled) if isinstance(ai, AIClient) else ai
        return (yield from self._ai_wait(recognize_image, client, image, cancelled=cancelled))

    def _extract_questions(self, extractor: Any, page: Any) -> Any:
        if hasattr(extractor, "extract_gen"):
            return (yield from extractor.extract_gen(page, vision_wait=self._ai_vision))
        return extractor.extract(page)

    def _queue_popup_check(self, page: Any) -> int:
        if (self.task is None and not self.stop_event.is_set() and self.session is not None
                and not self.session.done and time.time() - self.popup._last >= self.popup.min_interval_sec):
            self._start_task(self.popup.check_gen(page, answer_wait=self._ai_answer,
                                                 extract_wait=self._extract_questions), preserve_session=True)
        return 0

    def _popup_gen(self, question: Any, page: Any) -> Any:
        yield from self.popup._handle_gen(question, page, answer_wait=self._ai_answer,
                                         extract_wait=self._extract_questions)

    def _finish_task(self, status: RequestStatus, message: str = "", *, close: bool = False) -> None:
        generator, request = self.task, self._task_request
        previous = self._active_request
        self._active_request = request
        try:
            if close and generator is not None:
                generator.close()
        except Exception as exc:
            log.exception("长任务收尾失败")
            status, message = "failed", f"任务收尾失败：{exc}"
            self._emit(("err", message))
        finally:
            self.task = None
            self._task_request = None
            self._task_course_id = None
            self._wake_at = 0.0
            self._finish_request(request, status, message)
            self._active_request = previous

    def _step_task(self) -> None:
        g = self.task
        if g is None:
            return
        if self.task_stop.is_set():
            self._finish_task("cancelled", "任务已取消", close=True)
            return
        if time.monotonic() < self._wake_at:
            return
        previous = self._active_request
        self._active_request = self._task_request
        try:
            rv = next(g)
        except StopIteration:
            self._finish_task("cancelled" if self.task_stop.is_set() else "succeeded",
                              "任务已取消" if self.task_stop.is_set() else "")
            return
        except Exception as exc:
            log.exception("长任务步进异常")
            self._emit(("err", f"任务异常中止：{exc}"))
            self._finish_task("failed", str(exc))
            return
        finally:
            self._active_request = previous
        if self.task_stop.is_set():
            self._finish_task("cancelled", "任务已取消", close=True)
            return
        if isinstance(rv, float) and rv > 0:      # _wait 挂起：约定 rv = 唤醒时刻
            self._wake_at = rv
            return
        self._wake_at = 0.0                        # 已 yield 让出，继续下一拍

    def _wait(self, sec: float = 0.0) -> float:
        """任务内的“可取消等待”：挂起任务并把控制权交回主循环（期间快捷命令/停止仍可秒级响应）。

        调用方 `yield from self._wait(n)` 后应检查 `self.task_stop.is_set()` 决定是否跳出。
        """
        self._wake_at = time.monotonic() + max(0.0, sec)
        yield self._wake_at
        return self._wake_at

    def _job_timeout(self) -> float:
        """主循环取命令的等待时长：有协作式任务时 0.05s 快速步进；仅播放或空闲时 0.7s 心跳。"""
        return 0.05 if self.task is not None else 0.7

    def submit(self, action: str, **kw: Any) -> None:
        self.jobs.put((action, kw))

    def submit_request(self, action: str, **kw: Any) -> str:
        with self._request_lock:
            if action in ("cancel", "stop", "stop_play", "logout"):
                self.interrupt_task()
            if action in ("cancel", "logout"):
                self.login_cancel.set()
            request = RequestContext(uuid4().hex, action, self._cancel_version)
            self._requests[request.request_id] = request
            if self.stop_event.is_set():
                self._finish_request(request, "cancelled", "桌面服务已关闭")
            else:
                self.jobs.put((action, {**kw, "_request_context": request}))
        return request.request_id

    def interrupt_task(self) -> None:
        """可跨线程请求停止；版本号防止随后启动的旧请求清掉这次取消。"""
        with self._request_lock:
            self._cancel_version += 1
            self.task_stop.set()

    def shutdown(self) -> None:
        with self._request_lock:
            self.stop_event.set()
            self.login_cancel.set()
            self.task_stop.set()
            self._cancel_pending_requests()
            self.jobs.put(("quit", {}))
            self._ai_workers.shutdown()

    def stop_task(self) -> None:
        """取消当前长任务（连做/自动作答），但保留调度线程——之后按钮仍可点。"""
        if self.task is None:
            return
        if self._active_request is None:
            self.interrupt_task()
        else:
            self.task_stop.set()
        self._emit(("warn", "已请求停止：当前这步完成后中止（线程仍在，可继续操作）。"))

    def _tick_session(self) -> None:
        s = self.session
        if s is None or s.done:
            return
        now = time.monotonic()
        if now < self._session_tick_at:
            return
        self._session_tick_at = now + .7
        try:
            status = s.tick()
        except Exception as exc:
            log.debug("tick 异常", exc_info=True)
            self._emit(("err", f"播放心跳异常：{exc}"))
            self.session = None
            return
        title = one_line((self.current_item or {}).get("title", ""))[:24]
        pct = f"{s.progress * 100:.0f}%"
        self._emit(("playstatus", f"{title} ｜ {status} ｜ {pct} ｜ {s.notice}"))
        if s.done:
            self._on_session_end(s)
            self.session = None

    def _on_session_end(self, s: Any) -> None:
        if s.status == "done":
            self._emit(("ok", f"本节完成：{one_line((self.current_item or {}).get('title',''))[:30]}"))
            self._refresh_row()
            if self.auto_next:
                if self.task is None:
                    self._queue_auto_next()
                else:
                    self._deferred_next = (self._cid(), (self.current_item or {}).get("id"), self._cancel_version)
        else:
            self._emit(("warn", f"本节未确认完成（{s.notice}）。可继续手动或再点播放。"))

    def _continue_auto_next(self) -> None:
        if self._deferred_next is None or self.task is not None:
            return
        course_id, item_id, version = self._deferred_next
        self._deferred_next = None
        if (not self.auto_next or self.stop_event.is_set() or self.task_stop.is_set()
                or version != self._cancel_version or course_id != self._cid()
                or item_id != (self.current_item or {}).get("id")):
            return
        self._queue_auto_next()

    def _queue_auto_next(self) -> None:
        nxt = self._next_unfinished()
        if nxt:
            self._emit(("info", "自动进入下一节…"))
            self.submit("play", item_id=int(nxt["id"]))
        else:
            self._emit(("ok", "没有更多未完成小节了。"))

    # ------------------------------------------------------------ 依赖装配（复用既有模块）
    def _ensure_browser(self) -> Any:
        from browser.driver import Browser

        if self.browser is None:
            self.browser = Browser(self.cfg, self.app.store, headless=bool(getattr(self.app, "headless", False)))
            self.app.browser = self.browser
        self.browser.start()
        page = self.browser.start().page
        if page is not None:
            try:
                url = str(page.url or "")
                if not url or url.startswith("about:"):
                    from browser.actions import safe_goto
                    dest = (self.course or {}).get("url") or self.cfg.get("COURSE_LIST_URL") or self.cfg.get("CHAOXING_HOME_URL")
                    if dest:
                        safe_goto(page, dest, attempts=1)
            except Exception:
                log.debug("落地页跳转失败", exc_info=True)
        return page

    def _ensure_crawler(self) -> None:
        self._ensure_browser()
        if self.crawler is None:
            from course.crawler import CourseCrawler
            self.crawler = CourseCrawler(browser=self.browser, cfg=self.cfg, store=self.app.store)

    def _ensure_video(self) -> Any:
        self._ensure_crawler()
        self._ensure_ai()
        from questions.popup import PopupWatcher
        from video.player import VideoWatcher

        if self.popup is None:
            self.popup = PopupWatcher(cfg=self.cfg, store=self.app.store, engine=self.engine, kb=self.kb,
                                      min_interval_sec=float(self.cfg.get("POPUP_MIN_INTERVAL_SEC") or 5.0))
            self.popup.on_out = lambda level, text: self._emit((level, text))
            self.popup.on_check = self._queue_popup_check
        if self.vwatcher is None:
            self.vwatcher = VideoWatcher(browser=self.browser, cfg=self.cfg, store=self.app.store, popup=self.popup)
        return self.vwatcher

    def _ensure_ai(self) -> None:
        if self.engine is not None:
            return
        from ai.client import AIClient
        from ai.responder import AnswerEngine
        from questions.extractor import QuestionExtractor
        from questions.practice import PracticeRunner

        self.app.ai = getattr(self.app, "ai", None) or AIClient(self.cfg, store=self.app.store)
        self.engine = AnswerEngine(ai=self.app.ai, cfg=self.cfg, store=self.app.store)
        self.practice = PracticeRunner(browser=self.browser, cfg=self.cfg, store=self.app.store,
                                       engine=self.engine, extractor=QuestionExtractor(self.cfg),
                                       kb=self.kb, wrongbook=getattr(self.app, "wrongbook", None))
        self.app.engine = self.engine

    def _store(self):
        return self.app.store

    def _cid(self):
        return int(self.course["id"]) if self.course else None

    def _refresh_row(self) -> None:
        if self.current_item:
            self._emit(("row", f"{self.current_item['id']}|{one_line(self.current_item.get('title',''))[:30]}"))

    def _next_unfinished(self):
        if not self.course:
            return None
        items = self._store().list_items(self._cid(), only_unfinished=True, limit=1000)
        cur = (self.current_item or {}).get("id")
        for it in items:
            if cur is None or int(it["id"]) != int(cur):
                return it
        return items[0] if items else None

    # ------------------------------------------------------------ 登录/课程/目录
    def do_login(self) -> None:
        self.login_state = "waiting"
        try:
            self._do_login()
        finally:
            if self.login_state == "waiting":
                self.login_state = "unknown"

    def _do_login(self) -> None:
        self._emit(("info", "正在打开登录浏览器…"))
        self._ensure_browser()
        self._emit(("focus", "browser"))
        self._emit(("info", "浏览器已打开，请在其中完成登录（账号/验证码由你本人操作）。点顶部「✕取消」可中止等待：若已登录则记住登录，若未登录则退出登录。"))
        self.login_pending.set()
        try:
            ok = self.browser.wait_for_manual_login(on_prompt=lambda: self._emit(("alert", "请在浏览器完成登录")),
                                                    should_cancel=self.login_cancel.is_set)
        finally:
            self.login_pending.clear()
        self._emit(("focus", "panel"))
        # 无论自动检测是否识别成功，以“浏览器实时 Cookie 是否已登录”为准兜底，避免误判/探活抽风导致漏记
        try:
            logged_now = bool(self.browser._cookie_logged_in())
        except Exception:
            logged_now = False
        if ok or logged_now:
            self.login_state = "signed_in"
            n = 0
            try:
                n = self.browser.save_cookies()
            except Exception as exc:
                self._emit(("err", f"登录已识别但保存登录态失败：{exc}"))
            if ok:
                self._emit(("ok", f"登录已确认，已记住登录态（{n} 条 Cookie，重启免重复登录）。"))
            else:
                self._emit(("ok", f"检测到浏览器已登录（自动等待没识别到，已补记 {n} 条 Cookie 并记住）。"))
            if self.AUTO_READ_COURSES_AFTER_LOGIN:      # 登录成功→自动进入下一步：读取课程列表
                self._emit(("info", "正在自动读取课程列表…"))
                self.do_courses()

        elif self.login_cancel.is_set():
            # 主动取消且浏览器里确实没有登录 Cookie → 按“取消登录=退出登录”清空
            try:
                self.browser.logout()
                cleared = self._forget_courses_local()
                self.login_state = "signed_out"
                self._emit(("warn", f"已取消登录：未检测到登录 Cookie，已退出并清空记住的登录态，本地课程数据已清除{f'（{cleared} 门）' if cleared else ''}（下次需重新登录）。"))
            except Exception as exc:
                self._emit(("err", f"取消登录失败：{exc}"))
        else:
            self._emit(("warn", "未检测到登录（可稍后再点登录，或在浏览器登录好后再点一次「登录」）。"))

    def _forget_courses_local(self) -> int:
        """退出登录时清空本地课程数据（“谁登录就是谁的”）；store 不可用(离线自检)时静默跳过。"""
        try:
            return self._store().clear_all_courses()
        except Exception:
            return 0

    def do_logout(self) -> None:
        """退出登录：打断登录等待 + 清空持久化登录态 + 清空本地课程数据（谁登录就是谁的）。"""
        self.login_cancel.set()
        self.login_state = "unknown"
        try:
            if self.browser is not None:
                self.browser.logout()
            elif self.cfg is not None:
                from browser.driver import cookie_snapshot_path
                snap = cookie_snapshot_path(self.cfg)
                if snap.exists():
                    snap.unlink()
            cleared = self._forget_courses_local()
            self.login_state = "signed_out"
            self._emit(("warn", f"已退出登录：清空记住的登录态，本地课程数据已清除{f'（{cleared} 门）' if cleared else ''}（下次需重新登录）。"))
        except Exception as exc:
            self._emit(("err", f"退出登录失败：{exc}"))


    def do_courses(self) -> None:
        if not self._login_ok():
            return
        self._ensure_crawler()
        self._emit(("info", "读取课程列表…"))
        try:
            courses = self.crawler.sync_courses()
        except Exception as exc:
            self._emit(("err", f"读取课程失败：{exc}"))
            return
        rows = self._store().list_courses()
        self._emit(("courses", "|".join(f"{c['id']}::{c['name']}" for c in rows)))
        self._emit(("ok", f"课程共 {len(rows)} 门，可在上方下拉选择"))

    def do_select_course(self, course_id: int) -> None:
        c = self._store().get_course(str(course_id))
        if not c:
            self._emit(("warn", "该课程不在本地库，请先点“读取课程”。"))
            return
        if not self.course or int(self.course["id"]) != int(course_id):
            self._discuss_drafts = {"new_post": [], "reply": []}
            self._discuss_cursor = {"new_post": 0, "reply": 0}
            self._discuss_pending.clear()
            self._discuss_confirmation = None
            self._emit(("discuss_post", ""))
            self._emit(("discuss_reply", ""))
            self._emit(("discuss_rec", "推荐 —"))
        self.course = c
        self._store().set_meta("current_course", str(course_id))
        self._emit(("course", f"{c['id']}|{c['name']}"))

    def do_catalog(self) -> None:
        if not self.course:
            self._emit(("warn", "先在上拉选择一门课（或读取课程）。"))
            return
        if not self._login_ok():
            return
        self._ensure_crawler()
        from course.models import Course
        c = Course(course_key=self.course["course_key"], name=self.course["name"],
                   url=self.course.get("url", ""), cpi=self.course.get("cpi", ""),
                   clazzid=self.course.get("clazzid", ""), id=int(self.course["id"]))
        self._emit(("info", "读取章节目录…"))
        try:
            self.crawler.sync_catalog(c)
        except Exception as exc:
            self._emit(("err", f"读取目录失败：{exc}"))
            return
        self._publish_sections()

    def _publish_sections(self) -> None:
        if not self.course:
            return
        items = self._store().list_items(self._cid(), only_unfinished=False, limit=2000)
        rows = [f"{it['id']}|{'✓' if it.get('done') else ('▶' if (it.get('progress') or 0) > 0 else '·')}"
                f"|{it.get('kind','')}|{one_line(it.get('title',''))[:28]}" for it in items]
        self._emit(("sections", "\n".join(rows)))
        st = self._store().stats(self._cid())
        self._emit(("progress", f"{st.get('finished')}/{st.get('total')}"))

    def _login_ok(self) -> bool:
        return True   # 登录由具体操作前触发；此处不阻塞

    # ------------------------------------------------------------ 小节工作台：播放
    def do_open_item(self, item_id: int) -> None:
        items = {int(i["id"]): i for i in (self._store().list_items(self._cid(), only_unfinished=False, limit=5000) if self.course else [])}
        it = items.get(item_id)
        if not it:
            self._emit(("warn", "没找到这个小节，请先读取目录。"))
            return
        self.current_item = it
        self._emit(("item", f"{it.get('kind','')}|{one_line(it.get('title',''))[:40]}|{float(it.get('progress') or 0)*100:.0f}%"))

    def do_play(self, item_id: int) -> None:
        if not self.course:
            self._emit(("warn", "先选课程、读取目录，再点某一节。"))
            return
        vw = self._ensure_video()
        page = self._ensure_browser()
        it = next((i for i in self._store().list_items(self._cid(), only_unfinished=False, limit=5000)
                   if int(i["id"]) == int(item_id)), None)
        if not it:
            self._emit(("warn", "找不到该小节。"))
            return
        self.current_item = it
        catalog_url = str(self._store().get_meta(f"catalog_url:{self.course['id']}", "") or "")
        if not self.crawler.open_item(page, {**it, "course_id": self.course["id"]}, catalog_url):
            self._emit(("err", "打开该小节失败。"))
            return
        if self.popup is not None:
            self.popup.reset()
            self.popup.course_id = self._cid()
            self.popup.chapter_key = str(it.get("chapter_key") or "")
        self.session = vw.start_session(page, it)
        self._session_tick_at = 0.0
        self._emit(("playstatus", f"{one_line(it.get('title',''))[:24]} ｜ {self.session.status} ｜ 0% ｜ 开始…"))

    def do_pause(self) -> None:
        if self.session and not self.session.done:
            self.session.pause()
            self._emit(("info", "已暂停播放（再点“继续播放”）。"))

    def do_resume(self) -> None:
        if self.session and not self.session.done:
            self.session.resume()
            self._emit(("info", "已继续播放。"))

    def do_stop_play(self) -> None:
        if self.session and not self.session.done:
            self.session.stop()
        self.session = None
        self._emit(("warn", "已停止本节播放（进度已保存，下次可续）。"))

    def do_cancel(self) -> None:
        """全局取消：停止协作式长任务 + 停止播放器/清会话。任何状态都可安全调用，线程不被杀。"""
        if self._active_request is None:
            self.interrupt_task()
        else:
            self.task_stop.set()             # 已接受请求在入队时递增取消版本
        s = self.session
        if s is not None and not s.done:
            try:
                s.stop()                     # 真正暂停播放器
            except Exception:
                pass
        self.session = None
        self._discuss_confirmation = None
        self._emit(("warn", "已取消：停止了当前任务与播放（线程仍在，可继续其它操作）。"))

    def do_auto_next(self, on: bool) -> None:
        self.auto_next = bool(on)
        self._emit(("info", f"自动下一节：{'开' if on else '关'}"))

    # ------------------------------------------------------------ 章节检测 / 解析 / 自动作答
    def do_task_tab(self) -> None:
        self._ensure_crawler()
        page = self._ensure_browser()
        if not self.crawler.open_section_task_tab(page):
            self._emit(("warn", "没找到“章节检测/测验”入口；也可直接在学习通点该标签后再“解析本页”。"))
            return
        self._emit(("ok", "已切到章节检测。点“解析本页”读题。"))

    def do_parse(self) -> None:
        self._ensure_ai()
        page = self._ensure_browser()
        self._start_task(self._parse_page_gen(page))

    def _parse_page_gen(self, page: Any) -> Any:
        qs = yield from self._extract_questions(self.practice.extractor, page)
        if not qs:
            frames_info = []
            try:
                for fr in page.frames:
                    try:
                        n = fr.evaluate("()=>document.querySelectorAll('.ans-videoquiz,.TiMu,.questionLi,"
                                        "input[type=radio],input[type=checkbox],textarea').length")
                    except Exception:
                        n = "err"
                    try:
                        frames_info.append(f"{(fr.url or '(空)')[:60]}={n}")
                    except Exception:
                        frames_info.append("?")
            except Exception:
                frames_info = ["读frame失败"]
            self._emit(("err", f"没读到题目｜url={getattr(page,'url','')[:80]}｜各frame答题元素数: {frames_info}"))
            return
        from ui import report
        self._emit(("ok", f"读到 {len(qs)} 道题"))
        yield from self._parse_gen(qs)

    def _parse_gen(self, qs: list) -> Any:
        from ui import report
        for i, q in enumerate(qs, 1):
            if self.task_stop.is_set():
                self._emit(("warn", "已停止解析。"))
                return
            qid, _ = self._store().upsert_question(q.to_row(), course_id=self._cid())
            self._emit(("raw", report.render_question(q, i, len(qs))))
            yield from self._answer(q, qid, i, len(qs))
            yield None

    def _answer(self, q: Any, qid: int, i: int, total: int) -> Any:
        from ui import report
        if not (self.engine and getattr(self.engine, "ai", None) and self.engine.ai.enabled):
            self._emit(("warn", "AI 未启用：仅列题目。"))
            return
        ev = {"chunks": [], "questions": [], "wrongs": []}
        try:
            e = self.kb.evidence_for_question(q.as_dict(), course_id=self._cid(), top_k=int(self.cfg.get("KB_TOP_K") or 6))
            ev = {"chunks": list(e.get("chunks") or []), "questions": list(e.get("questions") or []),
                  "wrongs": list(e.get("wrongs") or [])}
        except Exception:
            pass
        ans = yield from self._ai_answer({**q.as_dict(), "id": qid}, evidence=ev, mode="practice",
                                        course=(self.course or {}).get("name", ""), qno=q.no or str(i))
        if ans is None:
            return
        self._emit(("ai", report.render_answer(ans)))
        if ans.answer:
            self._store().set_question_ai_result(qid, answer=ans.answer, analysis=ans.analysis,
                                                 knowledge=ans.knowledge, answer_source="ai")

    def do_auto(self, submit: bool = False) -> None:
        self._ensure_ai()
        page = self._ensure_browser()
        self._start_task(self._answer_page_gen(page, bool(submit)))

    def _answer_page_gen(self, page: Any, submit: bool) -> Any:
        """协作式：逐题作答当前页面并（按开关）交卷。返回 (已作答数, 题目数, 已交卷)。

        每答一题 yield 一次，把控制权交回调度循环——期间快捷命令/停止可秒级响应。
        """
        from exam.guard import ExamGuard
        from exam.snapshot import ExamSnapshot
        from questions.autofill import plan_answer_actions
        from questions.models import letters_from
        original_url = str(getattr(page, "url", "") or "")
        self.guard = self.guard or ExamGuard(self.cfg, self._store())
        if not self._writable(page, bool(submit)):
            if not self.cfg.get("PRACTICE_MODE"):
                self._emit(("err", "未开启“练习/演示模式”（更多→勾选）。"))
            elif submit and not self.cfg.get("PRACTICE_ALLOW_SUBMIT"):
                self._emit(("err", "要自动提交请再勾“允许自动提交”。"))
            else:
                self._emit(("err", "当前页是真实考试域名，按约定只读、不自动作答。"))
            return 0, 0, False
        qs = []
        for _ in range(3):
            qs = yield from self._extract_questions(self.practice.extractor, page)
            if qs:
                break
            if (yield from self._wait(1.5)) and self.task_stop.is_set():
                return 0, 0, False
        if not qs:
            self._emit(("err", "当前页面没有题目。"))
            return 0, 0, False
        mode = str(self.cfg.get("PRACTICE_SUBMIT_ACTION") or "submit")
        lab = "暂时保存" if mode.lower() == "save" else "提交"
        skip_ans = bool(self.cfg.get("PRACTICE_SKIP_ANSWERED"))
        self._emit(("info", f"读到 {len(qs)} 题，逐题作答中（答完:{lab}"
                               + ("、跳过已作答" if skip_ans else "") + "）…"))
        snap = ExamSnapshot(browser=self.browser, cfg=self.cfg, guard=self.guard)
        filled = skipped = 0
        verify: list[tuple[str, str, set]] = []        # (题号, 描述, 目标已选字母集合)
        for i, q in enumerate(qs, 1):
            if self.task_stop.is_set():
                break
            yield None
            if skip_ans and q.is_answered():
                skipped += 1
                self._emit(("info", f"第 {i}/{len(qs)} 题：已作答（{q.selected_labels or q.my_answer or '已填'}），跳过不改动"))
                continue
            qid, _ = self._store().upsert_question(q.to_row(), course_id=self._cid())
            ev = {"chunks": [], "questions": [], "wrongs": []}
            try:
                e = self.kb.evidence_for_question(q.as_dict(), course_id=self._cid())
                ev = {"chunks": list(e.get("chunks") or []), "questions": list(e.get("questions") or []),
                      "wrongs": list(e.get("wrongs") or [])}
            except Exception:
                pass
            self._emit(("info", f"AI 作答第 {i}/{len(qs)} 题…"))
            try:
                ans = yield from self._ai_answer({**q.as_dict(), "id": qid}, evidence=ev, mode="practice",
                                                course=(self.course or {}).get("name", ""), qno=q.no or str(i))
            except Exception as exc:
                self._emit(("err", f"第{i}题解析失败：{exc}"))
                continue
            if self.task_stop.is_set() or str(getattr(page, "url", "") or "") != original_url:
                return filled, len(qs), False
            if not (ans and ans.answer and (self.cfg.get("PRACTICE_FORCE_ANSWER") or not ans.needs_human) and not q.has_image):
                continue
            live_questions = yield from self._extract_questions(self.practice.extractor, page)
            current = next((item for item in live_questions if item.fp == q.fp), None)
            if current is None or str(getattr(page, "url", "") or "") != original_url:
                self._emit(("warn", "页面题目已改变，已停止本轮作答。"))
                return filled, len(qs), False
            if skip_ans and current.is_answered():
                self._emit(("warn", f"第{i}题已改变或已由本人作答，未应用旧的 AI 答案。"))
                continue
            if not self._writable(page, bool(submit)):
                self._emit(("err", "作答权限已改变，已停止本轮作答。"))
                return filled, len(qs), False
            if self.task_stop.is_set() or self.stop_event.is_set():
                return filled, len(qs), False
            q = current
            if q.has_image:
                continue
            acts = plan_answer_actions(q, ans.answer)
            if not acts:
                continue
            try:
                res = snap.apply_practice_actions(page, acts)
                if res.get("ok"):
                    filled += 1
                    want = set(letters_from(ans.answer))
                    if q.kind in ("single", "multi") and want:
                        verify.append((q.no or str(i), one_line(q.stem)[:20], want))
            except PermissionError as exc:
                self._emit(("err", f"被安全闸门拒绝：{exc}"))
                break
        self._emit(("ok", f"本轮新作答 {filled} 题" + (f"、跳过已作答 {skipped} 题" if skipped else "")
                            + f"（共 {len(qs)} 题）"))
        if verify:
            self._emit(("info", "回读页面核对作答是否生效…"))
            yield from self._wait(0.6)
            after_questions = yield from self._extract_questions(self.practice.extractor, page)
            after = {oo.no or "": oo for oo in (after_questions or [])}
            bad = 0
            for qno, desc, want in verify:
                got = after.get(qno)
                have = set(letters_from(got.selected_labels)) if got else set()
                if want and want.issubset(have):
                    pass
                else:
                    bad += 1
                    self._emit(("warn", f"第{qno}题{desc}：应选 {''.join(sorted(want))}，"
                                          f"实际 {''.join(sorted(have)) or '未选中'} → 请手动检查"))
            if not bad:
                self._emit(("ok", f"已核对 {len(verify)} 题：页面勾选与目标答案一致 ✓"))
        submitted = False
        if submit and not self.task_stop.is_set():
            submitted = bool(snap.submit_practice(page, mode=mode))
            self._emit(("ok", f"已自动点击「{lab}」") if submitted else ("warn", f"没找到「{lab}」按钮，请手动处理"))
        return filled, len(qs), submitted

    def do_stop(self) -> None:
        self.stop_task()

    def do_auto_chain(self, limit: int = 5, skip_done: bool = True, restart: bool = False,
                      start_id: int | None = None) -> None:
        """自动答题连做：逐节「打开小节→进章节检测→AI 作答→交卷→下一节」。

        改为协作式：每次只推进一小步并让出线程，期间「停止」秒级生效、其它按钮即时反馈。
        - 默认跳过已完成（本地标记秒跳；本地没有的看平台角标），并**从上次进度续跑**；
        - 交卷成功后回写本地完成标记，下次不再重开这节；
        - 想重新全量扫描：勾「从头重连」。
        """
        if not self.course:
            self._emit(("warn", "先选择课程并「读取目录」。"))
            return
        self._ensure_ai()
        self._ensure_crawler()
        limit = max(1, min(int(limit or 5), 50))
        cid = self._cid()
        if restart:
            try:
                self._store().set_resume_cursor(cid, 0)
            except Exception:
                pass
        resume = 0 if restart else int(self._store().resume_cursor(cid) or 0)
        all_items = self._store().list_items(cid, only_unfinished=False, limit=2000)
        items = [it for it in all_items if int(it["id"]) > resume] if resume else all_items
        if start_id:
            ids = [int(i["id"]) for i in all_items]
            if int(start_id) in ids:
                items = all_items[ids.index(int(start_id)):]
                self._emit(("info", f"从选中小节「{one_line(items[0].get('title',''))[:24]}」开始。"))
        if resume and not start_id and len(items) < len(all_items):
            self._emit(("info", f"从上次进度继续（前面 {len(all_items) - len(items)} 节不再重开；重扫请勾「从头重连」）。"))
        if not items:
            self._emit(("ok", "目录内小节都已处理过。勾「从头重连」可重扫。"))
            return
        catalog_url = str(self._store().get_meta(f"catalog_url:{self.course['id']}", "") or "")
        self._emit(("info", f"自动答题连做开始：最多 {limit} 节（随时点「停止」）。"))
        self._start_task(self._chain_gen(items, cid, catalog_url, limit, skip_done))

    def _chain_gen(self, items: list, cid: int, catalog_url: str, limit: int, skip_done: bool) -> Any:
        done = ans_total = zeros = 0
        for idx, it in enumerate(items, 1):
            if self.task_stop.is_set() or done >= limit:
                break
            yield None
            title = one_line(it.get("title", ""))[:26]
            page = self._ensure_browser()
            self._emit(("info", f"[{idx}] {title}：打开小节…"))
            if not self.crawler.open_item(page, {**it, "course_id": self.course["id"]}, catalog_url):
                self._emit(("warn", f"[{idx}] {title}：打不开小节，跳过"))
                continue
            if skip_done:
                count = self.crawler.section_unfinished_count(page)
                if count == 0:
                    try:
                        self._store().finish_item(int(it["id"]), "平台标注任务点已完成（连做检测）")
                        self._store().set_resume_cursor(cid, int(it["id"]))
                    except Exception:
                        pass
                    self._emit(("info", f"[{idx}] {title}：任务点已完成，跳过"))
                    continue
            self._emit(("info", f"[{idx}] {title}：进入章节检测…"))
            if not self.crawler.open_section_task_tab(page):
                self._emit(("info", f"[{idx}] {title}：本节无章节测验入口，跳过"))
                continue
            if skip_done and self.crawler.section_task_pending(page) is False:
                try:
                    self._store().set_resume_cursor(cid, int(it["id"]))
                except Exception:
                    pass
                self._emit(("info", f"[{idx}] {title}：测验已完成（页面标注），跳过"))
                continue
            self.current_item = it
            self._emit(("item", f"{it.get('kind','')}|{title}|连做中"))
            filled, total, submitted = yield from self._answer_page_gen(page, submit=True)
            if self.task_stop.is_set():
                self._emit(("warn", f"[{idx}] {title}：已停止连做（本节部分作答，未自动交卷，请手动检查）。"))
                break
            if filled and not submitted:
                self._emit(("err", f"[{idx}] {title}：已作答 {filled} 题但**未能交卷**（没找到提交按钮）。"
                                     "已停止连做，避免漏交——请在该页手动点「提交」，或把提交按钮样式反馈给我。"))
                break
            try:
                self._store().set_resume_cursor(cid, int(it["id"]))
            except Exception:
                pass
            done += 1
            ans_total += filled
            if total == 0:
                zeros += 1
                if zeros >= 2:
                    self._emit(("err", "连续两节都读不到题目：可能未登录或页面改版，已停止连做。"))
                    break
            else:
                zeros = 0
                self._store().log_study(int(it["id"]), "auto_chain",
                                        f"作答{filled}/{total} 交卷={'是' if submitted else '否'}", None)
                if submitted:
                    try:
                        self._store().finish_item(int(it["id"]), "连做已交卷")
                    except Exception:
                        pass
                self._emit(("ok", f"[{idx}] {title}：{filled}/{total} 题" + ("，已交卷" if submitted else "")))
            yield from self._wait(2)
        stopped = self.task_stop.is_set()
        self._emit(("ok" if not stopped else "warn",
                      ("连做结束" if not stopped else "连做已停止") + f"：处理 {done} 节，共作答 {ans_total} 题。"))
        self._emit(("refresh", ""))

    def _writable(self, page: Any, want_submit: bool) -> bool:
        from exam.guard import ExamGuard
        self.guard = self.guard or ExamGuard(self.cfg, self._store())
        try:
            return bool(self.guard.allow_page_write(getattr(page, "url", "") or "", want_submit=want_submit))
        except Exception:
            return False

    # ------------------------------------------------------------ 成绩（只读查看）
    def do_grades(self, save: bool = True) -> None:
        """只读查看课程成绩:save=True 重新抓取入库后展示;否则展示本地缓存。"""
        if not self.course:
            self._emit(("warn", "先选择课程。"))
            return
        if save:
            self._ensure_crawler()
            page = self._ensure_browser()
            self._emit(("info", "读取课程成绩(只读)…"))
            try:
                self.crawler.fetch_grades(page, self.course)
            except Exception as exc:
                self._emit(("err", f"成绩抓取失败：{exc}"))
                return
        from course.grades import render_grades
        rows = self._store().list_grades(self._cid())
        self._emit(("grades", render_grades(rows, overview=self._store().get_grade_overview(self._cid()))))

    # ------------------------------------------------------------ 讨论(按分值规划发帖/回复；可选自动提交)
    def _ensure_discuss(self) -> Any:
        from exam.guard import ExamGuard
        from exam.snapshot import ExamSnapshot
        from course.discussion import DiscussionRunner
        self._ensure_ai()
        self._ensure_crawler()
        self.guard = self.guard or ExamGuard(self.cfg, self._store())
        snap = ExamSnapshot(browser=self.browser, cfg=self.cfg, guard=self.guard)
        self.discuss = DiscussionRunner(browser=self.browser, cfg=self.cfg, store=self._store(),
                                        crawler=self.crawler, engine=self.engine, snap=snap)
        return self.discuss

    def _discuss_render(self, kind: str, drafts: list, summary: str, note: str = "") -> None:
        lines = [summary, ""]
        label = "发表（围绕课程提问）" if kind == "new_post" else "回复（回应他人话题）"
        lines.append(f"◆ {label}，{len(drafts)} 条" + (f"（{note}）" if note else ""))
        for i, d in enumerate(drafts, 1):
            head = (one_line(d["title"]) or "（无标题）")[:24] if kind == "new_post" \
                else f"回复《{one_line(d['title'])[:24]}》"
            lines.append(f"[{i}] {head}\n{d['text']}\n")
        self._emit(("discuss_" + ("post" if kind == "new_post" else "reply"), "\n".join(lines)))

    def do_discuss_preview(self, kind: str = "new_post") -> None:
        """按计分规则规划 + 协作式逐条生成该模块 AI 草稿(不写页面;期间按钮/取消秒级响应)。"""
        kind = "new_post" if kind == "new_post" else "reply"
        if not self.course:
            self._emit(("warn", "先选择课程。"))
            return
        if self._discuss_pending:
            self._emit(("warn", "请先核对当前草稿并点“确认已发布”或“放弃草稿”，再生成新草稿。"))
            return
        self._discuss_confirmation = None
        from course.discussion import graded_discussion_targets
        if not graded_discussion_targets(self._store().list_grades(self._cid())):
            self._emit(("warn", "成绩里没有“计分的讨论”;请先在「✎ 成绩」刷新成绩(且仅处理计分讨论)。"))
            return
        runner = self._ensure_discuss()
        page = self._ensure_browser()
        label = "发表（提问）" if kind == "new_post" else "回复（约150字）"
        self._emit(("info", f"进入讨论区并规划{label}草稿…"))
        try:
            runner.open_board(page, self.course)
            replied = self._store().replied_topic_keys(self._cid())
            acts, summary, ctx = runner.compute_plan(page, self.course, replied, kind=kind)
        except Exception as exc:
            self._emit(("err", f"讨论草稿规划失败：{exc}"))
            return
        self._discuss_summary = summary
        self._discuss_drafts[kind] = []
        self._discuss_cursor[kind] = 0
        est = getattr(runner, "last_estimate", None) or {}
        rec = int(est.get("rec") or 0)
        cur_max = int(self.cfg.get("DISCUSS_MAX") or 5)
        self._emit(("discuss_rec", (f"推荐 {rec} 条（还差约{est.get('remaining', 0):g}分）" if rec > 0
                                      else "已达标，无需再发")))
        if rec > cur_max:
            self._emit(("warn", f"本轮规划被“每轮上限 {cur_max}”截断——按剩余分推荐 {rec} 条，可改大后重新生成。"))
        if not acts:
            n_post_all = int(ctx.get("n_post") or 0)
            n_reply_all = int(ctx.get("n_reply") or 0)
            if ctx.get("reached") or (n_post_all == 0 and n_reply_all == 0):
                self._emit(("info", (summary or "本轮没有讨论任务") + "——目标分已达成或已够,该模块无需草稿。"))
            elif kind == "new_post" and n_post_all == 0:
                self._emit(("info", f"本轮计划 0 条发帖：回复 +2×{n_reply_all} 已能覆盖全部分差（自由组合凑分）——请到「模块二·回复」生成草稿并执行。"))
                self._emit(("info", "想强制发几条提问帖：在成绩规则里写明“发表≥X”即可（规划会预留 X 条发帖），或告诉我加一个“发帖最少条数”设置。"))
            elif kind == "reply" and n_reply_all == 0:
                self._emit(("info", f"本轮计划 0 条回复：发帖 +1×{n_post_all} 已覆盖分差（未回复话题不足或回复上限）——请到「模块一·发表」生成草稿并执行。"))
            else:
                self._emit(("warn", (summary or "没有可生成的草稿") + "（该模块本轮无任务或 AI 未启用）。"))
            self._discuss_render(kind, [], summary)
            return
        self._emit(("info", f"本轮{label}共 {len(acts)} 条,即将并行生成（可「✕取消」）。"))
        self._start_task(self._discuss_preview_gen(kind, acts, ctx, summary))

    def _discuss_preview_gen(self, kind: str, acts: list, ctx: dict, summary: str) -> Any:
        n = len(acts)
        batches = None
        try:
            from course.discussion import build_discuss_batches
            batches = build_discuss_batches(acts, ctx.get("topic_by_key", {}))
        except Exception:
            pass
        par = int(self.cfg.get("DISCUSS_AI_PARALLEL") or 4)
        self._emit(("info", f"批量并行生成 {n} 条草稿（{len(batches or [])} 次调用 × 并行{min(par, len(batches or [1]))}路）…可「✕取消」。"))

        import copy
        from ui.ai_worker import AIWorker, CallLedger, CapturedConfig, capture_engine
        cancelled = threading.Event()
        version, cid = self._cancel_version, self._cid()
        partials: queue.Queue = queue.Queue()
        ledger = CallLedger()
        runner = copy.copy(self.discuss)
        if hasattr(runner, "engine"):
            runner.engine = capture_engine(runner.engine, self.cfg, ledger, cancelled)
            runner.cfg = CapturedConfig(self.cfg)
            # Generation needs only the plain plan and AI engine.
            runner.browser = runner.crawler = runner.store = runner.snap = None
        acts, ctx, course = copy.deepcopy(acts), copy.deepcopy(ctx), copy.deepcopy(self.course or {})

        def _partial(drafts, total):
            if not cancelled.is_set():
                partials.put((copy.deepcopy(drafts), total))

        def consume_partials():
            while not partials.empty():
                drafts, total = partials.get_nowait()
                self._discuss_drafts[kind] = drafts
                self._discuss_render(kind, drafts, summary, note=f"已生成 {len(drafts)}/{total}·批量并行")

        fut = self._ai_workers.submit(runner.generate_drafts, acts, ctx, course,
                                      on_partial=_partial, should_stop=cancelled.is_set,
                                      worker_factory=AIWorker)
        try:
            while not fut.done():
                if self.task_stop.is_set() or version != self._cancel_version or cid != self._cid():
                    return
                consume_partials()
                yield from self._wait(.05)
            if self.task_stop.is_set() or version != self._cancel_version or cid != self._cid():
                return
            drafts = fut.result()
            consume_partials()
            for call in ledger.calls:
                self._store().log_ai_call(*call)
        finally:
            cancelled.set()
            fut.cancel()
        self._discuss_drafts[kind] = drafts
        self._discuss_cursor[kind] = 0
        if not drafts:
            errs = getattr(runner, "last_errors", None) or []
            for e in errs[:4]:
                self._emit(("err", "生成失败详情：" + e))
            self._emit(("warn", "没有生成出草稿——见上方详情。批量格式不合会**自动转单条**;若单条也失败,请检查 AI 密钥/额度/网络(logs/ 有完整日志)。"))
            return
        self._discuss_render(kind, drafts, summary)
        auto = bool(self.cfg.get("PRACTICE_ALLOW_DISCUSS_SUBMIT"))
        label = "发表" if kind == "new_post" else "回复"
        self._emit(("ok", f"{label}草稿 {len(drafts)} 条就绪。"
                            + ("点本模块「▶ 自动完成」即可（已勾自动提交）。" if auto
                               else "可“填入下一条”自己发布；或勾“自动点发表/回复”后用「▶ 自动完成」。")))

    def do_discuss_fill_next(self, kind: str = "new_post") -> None:
        """只填入当前草稿；用户确认发布后才推进游标，自动提交另有明确入口。"""
        kind = "new_post" if kind == "new_post" else "reply"
        drafts = (self._discuss_drafts or {}).get(kind) or []
        cur = (self._discuss_cursor or {}).get(kind, 0)
        if not drafts or cur >= len(drafts):
            self._emit(("warn", "该模块没有更多草稿,请先点本模块的“生成草稿”。"))
            return
        if self._discuss_pending or self._discuss_has_unverified():
            self._emit(("warn", "当前有草稿等待发布确认，请先核验并点“确认已发布”。"))
            return
        self._discuss_confirmation = None
        from course.discussion import discuss_write_allowed
        page = self._ensure_browser()
        can, why = discuss_write_allowed(self.cfg, self.guard, str(getattr(page, "url", "") or ""))
        if not can:
            self._emit(("warn", "不会填入：" + why + "(需在“更多/练习”勾选允许自动发帖回复)。"))
            return
        runner = getattr(self, "discuss", None) or self._ensure_discuss()
        d = drafts[cur]
        if self._store().is_discussed(self._cid(), d["fp"]):
            self._emit(("warn", "这条内容已有本地记录，未再填入，避免重复发布；请先核验旧记录或重新生成草稿。"))
            return
        title = ""
        if kind == "new_post":
            opened = runner.open_new_post(page)
            if not opened and runner._pick_editor(page)[0] is None:
                self._emit(("warn", "未能打开新建话题表单，请先手动打开后再填入。"))
                return
            import time as _t
            _t.sleep(1.2)
            title = (d["title"] or d["text"][:20]).strip()
        elif not runner.open_topic_reply(page, d.get("topic_key", "")):
            self._emit(("warn", "未能确认目标话题的回复页，未填入；请打开目标话题后重试。"))
            return
        target_page = runner.reply_page(page) if kind == "reply" else page
        if target_page is None:
            self._emit(("warn", "目标话题页或回复框已变化，未填入回复；请重新打开目标话题。"))
            return
        res = runner.fill_editor(target_page, d["text"], title=title)
        tag = "发帖" if kind == "new_post" else f"回复《{one_line(d['title'])[:20]}》"
        if not res.get("body"):
            self._emit(("warn", f"[{cur + 1}] {tag} 未找到可填的编辑器:请先在页面点开“新建话题/回复”的正文框,再点“填入下一条”。"))
            return
        if not self._store().mark_discussed(self._cid(), d["type"], d.get("topic_key", ""), d["fp"],
                                            d.get("title", ""), status="draft"):
            self._emit(("warn", "填入时发现这条内容已有记录，未关联新草稿；请勿重复发布。"))
            return
        self._discuss_pending[kind] = {"course_id": self._cid(), "index": cur, "draft": dict(d)}
        self._emit(("ok", f"已填入 [{cur + 1}/{len(drafts)}] {tag}。请在浏览器核对并发布，"
                            "然后点本模块“确认已发布”；“填入下一条”只填入。"))

    def _discuss_has_unverified(self) -> bool:
        return self._store().q1("SELECT 1 FROM discussions WHERE course_id=? AND status='pending_verify' LIMIT 1",
                               (self._cid(),)) is not None

    def do_discuss_confirm(self, kind: str = "new_post") -> None:
        """用户在页面确认发布后，计入完成数并推进相应草稿游标。"""
        pending = self._discuss_pending.get(kind)
        if pending is None:
            # 重启或切课后仍可确认唯一一条未决记录。
            rows = [r for r in self._store().list_discussions(self._cid(), limit=10000)
                    if r["kind"] == kind and r["status"] in ("draft", "pending_verify")]
            if len(rows) != 1:
                self._emit(("warn", "没有唯一的待确认记录，请先填入本模块草稿。"))
                return
            fp = rows[0]["fp"]
        else:
            if pending["course_id"] != self._cid():
                return
            fp = pending["draft"]["fp"]
        if self._store().confirm_discuss_published(self._cid(), fp):
            if pending is not None:
                self._discuss_cursor[kind] = pending["index"] + 1
                self._discuss_pending.pop(kind, None)
            self._emit(("ok", "已按你的确认记录为已发布，可继续下一条。"))

    def do_discuss_discard(self, kind: str = "new_post") -> None:
        pending = self._discuss_pending.get(kind)
        if pending is not None:
            if pending["course_id"] != self._cid():
                return
            fp = pending["draft"]["fp"]
        else:
            rows = [r for r in self._store().list_discussions(self._cid(), limit=10000)
                    if r["kind"] == kind and r["status"] == "draft"]
            if len(rows) != 1:
                self._emit(("warn", "没有唯一的可放弃草稿。"))
                return
            fp = rows[0]["fp"]
        row = self._store().q1("SELECT status FROM discussions WHERE course_id=? AND fp=?", (self._cid(), fp))
        if row is not None and row["status"] == "pending_verify":
            self._emit(("warn", "该条已尝试提交，请先在页面核验发布结果；不能直接放弃并自动重发。"))
            return
        self._store().exec("DELETE FROM discussions WHERE course_id=? AND fp=? AND status='draft'", (self._cid(), fp))
        self._discuss_pending.pop(kind, None)
        self._emit(("info", "已放弃本地草稿记录。请在浏览器关闭或清空编辑框后继续；该条可再次填入。"))

    def do_discuss_not_published(self, kind: str = "new_post") -> None:
        """用户明确核对未发布后解除待核验状态，保留当前条供再次手动处理。"""
        pending = self._discuss_pending.get(kind)
        if pending is not None:
            if pending["course_id"] != self._cid():
                return
            fp = pending["draft"]["fp"]
        else:
            rows = [r for r in self._store().list_discussions(self._cid(), limit=10000)
                    if r["kind"] == kind and r["status"] == "pending_verify"]
            if len(rows) != 1:
                self._emit(("warn", "没有唯一的待核验记录。"))
                return
            fp = rows[0]["fp"]
        self._store().exec("UPDATE discussions SET status='draft' WHERE course_id=? AND fp=? AND status='pending_verify'",
                           (self._cid(), fp))
        self._discuss_pending.pop(kind, None)
        self._emit(("info", "已按你的确认标为未发布，当前条保留；可手动检查后重新填入或重新确认自动批次。"))

    def _auto_reconcile_pending_posts(self) -> None:
        """只读核对当前课程讨论列表；有唯一正文证据才解除新话题的待核验状态。"""
        store = self._store()
        cid = self._cid()
        for kind, pending in list(self._discuss_pending.items()):
            if pending.get("course_id") != cid:
                continue
            fp = pending["draft"]["fp"]
            row = store.q1("SELECT status FROM discussions WHERE course_id=? AND fp=?", (cid, fp))
            if row is not None and row["status"] == "posted":
                self._discuss_cursor[kind] = max(self._discuss_cursor.get(kind, 0), pending["index"] + 1)
                self._discuss_pending.pop(kind, None)
        rows = [row for row in store.list_discussions(cid, limit=10000)
                if row["kind"] == "new_post" and row["status"] == "pending_verify"]
        if not rows:
            return
        runner = getattr(self, "discuss", None) or self._ensure_discuss()
        page = self._ensure_browser()
        for row in rows:
            try:
                verified = runner.published_post_visible(page, self.course, row["title"], row["fp"])
            except Exception as exc:
                log.debug("自动核验讨论话题失败：%s", exc)
                verified = False
            if not verified or not store.confirm_discuss_published(cid, row["fp"]):
                continue
            pending = self._discuss_pending.get("new_post")
            if pending and pending["draft"]["fp"] == row["fp"]:
                self._discuss_cursor["new_post"] = max(
                    self._discuss_cursor.get("new_post", 0), pending["index"] + 1)
                self._discuss_pending.pop("new_post", None)
            self._emit(("ok", "已在当前课程讨论列表核验到已发表话题，自动解除待核验状态；未重复提交。"))

    def do_discuss_auto(self, kind: str = "new_post") -> None:
        """该模块草稿逐条“填入+提交”(需四道门全过)。协作式步进,随时「✕取消」即停。"""
        kind = "new_post" if kind == "new_post" else "reply"
        if self.course:
            self._auto_reconcile_pending_posts()
        if self._discuss_pending or (self.course and self._discuss_has_unverified()):
            self._emit(("warn", "有发布结果待核验：请先在讨论区确认是否已发布，再点“确认已发布”或“确认未发布”；勿直接重发。"))
            return
        drafts = (self._discuss_drafts or {}).get(kind) or []
        cur = (self._discuss_cursor or {}).get(kind, 0)
        label = "发表" if kind == "new_post" else "回复"
        if not drafts:
            other_kind = "reply" if kind == "new_post" else "new_post"
            other_label = "回复" if kind == "new_post" else "发表"
            other_count = max(0, len(self._discuss_drafts.get(other_kind) or [])
                              - self._discuss_cursor.get(other_kind, 0))
            if other_count:
                self._emit(("warn", f"{label}模块没有草稿；{other_label}模块已有 {other_count} 条，请点对应模块的“自动完成”。"))
            else:
                self._emit(("warn", f"{label}模块没有可用草稿。草稿仅保存在当前工作台，切换课程或重启后需重新生成；请等“草稿就绪”提示。"))
            return
        if cur >= len(drafts):
            self._emit(("warn", f"{label}模块的 {len(drafts)} 条草稿已处理完；如需继续，请重新生成。"))
            return
        from course.discussion import discuss_submit_allowed
        page = self._ensure_browser()
        can, why = discuss_submit_allowed(self.cfg, self.guard, str(getattr(page, "url", "") or ""))
        if not can:
            self._emit(("warn", "不会自动完成：" + why + "（需同时勾选“允许自动发帖回复”+“自动点发表/回复”）。"))
            return
        self._discuss_confirmation_id += 1
        token = self._discuss_confirmation_id
        batch = [dict(d) for d in drafts[cur:]]
        self._discuss_confirmation = {"token": token, "kind": kind, "course_id": self._cid(),
                                      "cursor": cur, "drafts": batch}
        from course.discussion import publish_batch_preview
        self._emit(("discuss_confirm", {"token": token, "text": publish_batch_preview(self.course, batch)}))

    def do_discuss_auto_confirm(self, token: int, approved: bool = False) -> None:
        request = self._discuss_confirmation
        if not request or request["token"] != token:
            return
        self._discuss_confirmation = None
        if not approved:
            self._emit(("info", "已取消本轮发布，草稿保留。"))
            return
        kind = request["kind"]
        cur = self._discuss_cursor[kind]
        if (request["course_id"] != self._cid() or request["cursor"] != cur
                or request["drafts"] != self._discuss_drafts[kind][cur:]):
            self._emit(("warn", "课程或草稿已变更，请重新点“自动完成”确认本轮内容。"))
            return
        if self._discuss_pending or self._discuss_has_unverified():
            self._emit(("warn", "有待确认发布记录，请先核验。"))
            return
        from course.discussion import discuss_submit_allowed
        page = self._ensure_browser()
        can, why = discuss_submit_allowed(self.cfg, self.guard, str(page.url or ""))
        if not can:
            self._emit(("warn", why))
            return
        self._start_task(self._discuss_auto_gen(kind))

    def _discuss_auto_gen(self, kind: str) -> Any:
        label = "发表" if kind == "new_post" else "回复"
        runner = getattr(self, "discuss", None) or self._ensure_discuss()
        page = self._ensure_browser()
        from course.discussion import discuss_submit_allowed
        while True:
            drafts = self._discuss_drafts.get(kind) or []
            idx = self._discuss_cursor.get(kind, 0)
            if idx >= len(drafts):
                break
            if self.task_stop.is_set():
                self._emit(("warn", f"已停止{label}自动任务（已完成的会记录，不重发）。"))
                return
            yield None
            if self.task_stop.is_set():
                return
            d = drafts[idx]
            if self._store().is_discussed(self._cid(), d["fp"]):
                row = self._store().q1("SELECT status FROM discussions WHERE course_id=? AND fp=?", (self._cid(), d["fp"]))
                if row["status"] == "posted":
                    self._discuss_cursor[kind] = idx + 1
                    continue
                if row["status"] == "pending_verify":
                    self._discuss_pending[kind] = {"course_id": self._cid(), "index": idx, "draft": dict(d)}
                    self._emit(("warn", "该草稿发布结果待核验，本轮停止。"))
                    return
                if row["status"] == "draft":
                    self._emit(("warn", "这条草稿此前已填入但未确认是否发布，本轮停止；请先核验或放弃旧草稿。"))
                    return
            if kind == "reply" and d.get("topic_key") in self._store().replied_topic_keys(self._cid()):
                self._emit(("warn", f"《{one_line(d.get('title', ''))[:20]}》已有已发布回复，跳过过期草稿以免重复。"))
                self._discuss_cursor[kind] = idx + 1
                continue
            can, why = discuss_submit_allowed(self.cfg, self.guard, str(page.url or ""))
            if not can:
                self._emit(("warn", why))
                return
            tag = "发帖" if kind == "new_post" else f"回复《{one_line(d.get('title', ''))[:20]}》"
            title = ""
            if kind == "new_post":
                if not runner.open_new_post(page):
                    self._emit(("warn", "未能打开新建话题表单，本轮停止，请手动打开后填入当前条。"))
                    return
                yield from self._wait(1.2)
                title = (d.get("title") or d["text"][:20]).strip()
            elif not runner.open_topic_reply(page, d.get("topic_key", "")):
                self._emit(("warn", f"{tag}：未能打开回复框，本轮停止，请手动打开目标话题再填入当前条。"))
                return
            else:
                yield from self._wait(0.5)
            if self.task_stop.is_set():
                return
            target_page = runner.reply_page(page) if kind == "reply" else page
            if target_page is None:
                self._emit(("warn", f"{tag}：目标话题页或回复框已变化，本轮停止，未填入或提交。"))
                return
            can, why = discuss_submit_allowed(self.cfg, self.guard, str(target_page.url or ""))
            if not can:
                self._emit(("warn", why))
                return
            res = runner.fill_editor(target_page, d["text"], title=title)
            if not res.get("body"):
                self._emit(("warn", f"{tag}：没找到可见编辑框，本轮停止，请手动打开后填入当前条。"))
                return
            if kind == "new_post" and not res.get("title"):
                self._emit(("warn", "标题未填入，本轮停止，请手动检查表单。"))
                return
            self._store().mark_discussed(self._cid(), kind, d.get("topic_key", ""), d["fp"],
                                         title or d.get("title", ""), status="draft")
            self._emit(("warn", f"⚠ 将以你的账号自动提交{tag}到讨论区（公开、不可撤回）…"))
            self._discuss_pending[kind] = {"course_id": self._cid(), "index": idx, "draft": dict(d)}
            self._store().update_discuss_status(self._cid(), d["fp"], "pending_verify")
            state, why2 = yield from runner.publish_gen(target_page, kind, d["text"], self.guard,
                                                        stop_event=self.task_stop)
            if state == "pending_verify" and kind == "new_post":
                try:
                    if runner.published_post_visible(page, self.course, title, d["fp"]):
                        state, why2 = "posted", "已在当前课程讨论列表核验到发表内容"
                except Exception as exc:
                    log.debug("自动核验新话题失败：%s", exc)
            self._store().update_discuss_status(self._cid(), d["fp"], state)
            if state != "posted":
                self._emit(("warn", f"{tag}：{why2}。本轮停止，请核验后点“确认已发布”。"))
                return
            self._discuss_pending.pop(kind, None)
            self._emit(("ok", f"{tag}：发布已核验 ✓（{idx + 1}/{len(drafts)}）"))
            self._discuss_cursor[kind] = idx + 1
            yield from self._wait(1.2)
        self._emit(("info", f"{label}模块本轮结束（够目标分即停;要继续可再点“生成草稿”）。"))

    def do_discuss_set_max(self, value: int) -> None:
        """设置讨论每轮条数上限(DISCUSS_MAX,防刷屏)并持久化;够目标分仍会自动停。"""
        try:
            v = int(value)
        except (TypeError, ValueError):
            return
        v = max(1, min(v, 200))
        self.cfg.values["DISCUSS_MAX"] = v
        try:
            from utils import settings_io as S
            cur = S.load_current(self.cfg)
            cur["DISCUSS_MAX"] = v
            S.save(cur)
        except Exception as exc:
            log.debug("写入 DISCUSS_MAX 失败：%s", exc)
        self._emit(("info", f"讨论每轮上限已设为 {v} 条（发帖+回复合计;达成目标分自动停）。"))

    # ------------------------------------------------------------ 阅读/文档任务点（可设时长·协作式停留）
    def _read_text_len(self, page: Any) -> int:
        from browser import actions as A
        best = 0
        for frame in [page, *A.frames_of(page)]:
            try:
                n = A.js_eval(frame, "() => ((document.body && document.body.innerText) || '').length") or 0
                best = max(best, int(n))
            except Exception:
                continue
        return best

    def do_read(self, item_id: int, min_seconds: int | None = None) -> None:
        """阅读/文档类小节：打开→按正文估时(可用 min_seconds 覆盖下限)→真实停留→平台标注才算完成。"""
        if not self.course:
            self._emit(("warn", "先选课程、读取目录，再选一个阅读/文档小节。"))
            return
        from course.reading import estimate_read_seconds
        self._ensure_crawler()
        vw = self._ensure_video()
        it = next((i for i in self._store().list_items(self._cid(), only_unfinished=False, limit=5000)
                   if int(i["id"]) == int(item_id)), None)
        if not it:
            self._emit(("warn", "找不到该小节。"))
            return
        page = self._ensure_browser()
        catalog_url = str(self._store().get_meta(f"catalog_url:{self.course['id']}", "") or "")
        self.current_item = it
        self._emit(("info", f"打开阅读小节：{one_line(it.get('title',''))[:24]}…"))
        if not self.crawler.open_item(page, {**it, "course_id": self.course["id"]}, catalog_url):
            self._emit(("err", "打开该小节失败。"))
            return
        import time as _t
        _t.sleep(1.5)
        lo = int(min_seconds) if min_seconds and int(min_seconds) > 0 else int(self.cfg.get("READ_MIN_SECONDS") or 45)
        text_len = self._read_text_len(page)
        need = estimate_read_seconds(text_len, lo, self.cfg.get("READ_MAX_SECONDS") or 240,
                                     self.cfg.get("READ_CHARS_PER_SEC") or 12)
        vw_session_ok = getattr(vw, "popup", None)  # 触发装配无副作用
        self._emit(("info", f"正文约 {text_len} 字，计划真实停留 {need} 秒（随时点“停止”）。"))
        self._start_task(self._read_gen(int(item_id), need))

    def _read_gen(self, item_id: int, need: int) -> Any:
        from exam.guard import ExamGuard  # noqa: F401  (与 _writable 共用装配)
        vw = self._ensure_video()
        page = self._ensure_browser()
        waited = 0.0
        step = 5.0
        while waited < need:
            if self.task_stop.is_set():
                break
            chunk = min(step, need - waited)
            yield from self._wait(chunk)
            if self.task_stop.is_set():
                break
            waited += chunk
            try:
                self._store().update_progress(item_id, min(0.99, waited / need), waited)
            except Exception:
                pass
            self._emit(("playstatus",
                          f"{one_line((self.current_item or {}).get('title',''))[:20]} ｜ 阅读 ｜ {waited:.0f}/{need}s"))
        if self.task_stop.is_set():
            self._emit(("warn", "已停止阅读计时（进度已保存）。"))
            return
        try:
            done = bool(vw.platform_says_done(page))
        except Exception:
            done = False
        if done:
            try:
                self._store().update_progress(item_id, 1.0, waited)
                self._store().finish_item(item_id, f"阅读停留 {waited:.0f} 秒后平台标注完成")
            except Exception:
                pass
            self._emit(("ok", f"阅读任务完成（真实停留 {waited:.0f} 秒，平台已标注）。"))
            self._refresh_row()
        else:
            self._emit(("info", f"已停留 {waited:.0f} 秒，但平台“已完成”标记未检测到；"
                                  "如页面确已完成请你自己点一下该任务点。（本工具不伪造完成）"))
        self._emit(("refresh", ""))

    # ------------------------------------------------------------ 更多：练习开关 / 错题 / 搜索
    def do_dump(self) -> None:
        page = self._ensure_browser()
        try:
            path = self.browser.dump_html(page, "workbench_dump")
            self._emit(("ok", f"已导出本页结构：{path}"))
        except Exception as exc:
            self._emit(("err", f"导出失败：{exc}"))

    def do_practice_set(self, mode: bool = False, submit: bool = False, force: bool = False,
                        retry: bool = False, action: str | None = None, discuss: bool | None = None,
                        skip: bool | None = None, discuss_submit: bool | None = None) -> None:
        self.cfg.values["PRACTICE_MODE"] = bool(mode)
        self.cfg.values["PRACTICE_ALLOW_SUBMIT"] = bool(submit)
        self.cfg.values["PRACTICE_FORCE_ANSWER"] = bool(force)
        self.cfg.values["PRACTICE_RETRY_ON_WRONG"] = bool(retry)
        if discuss is not None:
            self.cfg.values["PRACTICE_ALLOW_DISCUSS_POST"] = bool(discuss)
        if discuss_submit is not None:
            self.cfg.values["PRACTICE_ALLOW_DISCUSS_SUBMIT"] = bool(discuss_submit)
        if skip is not None:
            self.cfg.values["PRACTICE_SKIP_ANSWERED"] = bool(skip)
        if str(action or "").lower() in ("submit", "save"):
            self.cfg.values["PRACTICE_SUBMIT_ACTION"] = str(action).lower()
        try:
            from utils import settings_io as S
            cur = S.load_current(self.cfg)
            cur["PRACTICE_MODE"] = bool(mode)
            cur["PRACTICE_ALLOW_SUBMIT"] = bool(submit)
            cur["PRACTICE_FORCE_ANSWER"] = bool(force)
            cur["PRACTICE_RETRY_ON_WRONG"] = bool(retry)
            cur["PRACTICE_SUBMIT_ACTION"] = str(self.cfg.values.get("PRACTICE_SUBMIT_ACTION") or "submit")
            cur["PRACTICE_ALLOW_DISCUSS_POST"] = bool(self.cfg.values.get("PRACTICE_ALLOW_DISCUSS_POST"))
            cur["PRACTICE_ALLOW_DISCUSS_SUBMIT"] = bool(self.cfg.values.get("PRACTICE_ALLOW_DISCUSS_SUBMIT"))
            cur["PRACTICE_SKIP_ANSWERED"] = bool(self.cfg.values.get("PRACTICE_SKIP_ANSWERED"))
            S.save(cur)
        except Exception as exc:
            log.debug("写入练习开关失败：%s", exc)
        act = str(self.cfg.values.get("PRACTICE_SUBMIT_ACTION") or "submit").lower()
        log.info("练习设置已保存：自动作答=%s、自动提交=%s、答完后=%s",
                 "开" if mode else "关", "开" if submit else "关",
                 "暂时保存(不交卷)" if act == "save" else "提交交卷")

    def do_wrong_list(self) -> None:
        rows = self._store().list_wrong(course_id=self._cid(), limit=40)
        if not rows:
            self._emit(("info", "错题本是空的。"))
            return
        for i, r in enumerate(rows, 1):
            self._emit(("raw", f"{i}. 错{r['wrong_count']}次 ｜ {one_line(r['stem'])[:50]} ｜ 答案 {r.get('answer') or '—'}"))

    def do_search(self, text: str) -> None:
        if not text.strip():
            return
        try:
            res = self.kb.search(text, course_id=self._cid())
            self._emit(("raw", self.kb.render_hits(res, limit=6)))
        except Exception as exc:
            self._emit(("err", f"搜索失败：{exc}"))

    def do_ai_check(self) -> None:
        self._ensure_ai()
        try:
            if self.engine and getattr(self.engine, "ai", None) and self.engine.ai.enabled:
                self._emit(("ok", f"AI 可用：模型 {self.cfg.ai_model} ｜ 接口 {self.cfg.ai_base_url}"))
            else:
                self._emit(("err", "AI 未启用：点顶部「⚙ 设置(AI)」填 API 密钥/模型/地址并开 AI_ENABLE。"))
        except Exception as exc:
            self._emit(("err", f"AI 检查失败：{exc}"))
