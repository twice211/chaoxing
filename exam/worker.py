# -*- coding: utf-8 -*-
"""
exam.worker —— 把 Playwright 放在独立线程里跑，主线程交给界面（tkinter 侧边栏）

原因：Playwright 同步 API 有线程亲和性（对象必须在创建它的线程使用），
而 tkinter 必须在主线程运行 mainloop。因此这里实现一个“命令队列 + 结果回传”的
工作者线程：界面线程只调用 worker.request(fn)，fn 在浏览器线程里执行。
"""

from __future__ import annotations

import queue
import threading
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from browser.driver import Browser
from utils.logger import get_logger

log = get_logger("exam.worker")


class WorkerTimeout(RuntimeError):
    pass


@dataclass
class _Job:
    job_id: str
    fn: Callable[[dict[str, Any]], Any]
    reply: queue.Queue


class BrowserWorker(threading.Thread):
    """浏览器线程：持有 Browser 会话与共享上下文（store/kb/engine 等）。"""

    def __init__(self, cfg: Any, context: dict[str, Any] | None = None) -> None:
        super().__init__(name="browser-worker", daemon=True)
        self.cfg = cfg
        self.context: dict[str, Any] = dict(context or {})
        self.browser: Browser | None = None
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._replies: dict[str, queue.Queue] = {}
        self._stop = threading.Event()
        self.ready = threading.Event()
        self.error: str = ""

    # ------------------------------------------------------------ 生命周期
    def run(self) -> None:  # noqa: D401 线程入口
        try:
            self.browser = Browser(self.cfg, self.context.get("store"))
            self.browser.start()
            self.context["browser"] = self.browser
            self.ready.set()
        except Exception as exc:
            log.error("浏览器线程启动失败：%s", exc)
            self.error = str(exc)
            self.ready.set()
            return
        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                result = job.fn(self.context)
                job.reply.put(("ok", result))
            except Exception as exc:
                log.debug("任务执行异常：%s", exc)
                job.reply.put(("error", f"{exc}\n{traceback.format_exc(limit=3)}"))
        self._shutdown()

    def _shutdown(self) -> None:
        try:
            if self.browser:
                self.browser.stop()
        except Exception:
            log.debug("浏览器关闭异常", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        self._jobs.put(None)
        if self.is_alive():
            self.join(timeout=8)

    # ------------------------------------------------------------ 调用
    def request(self, fn: Callable[[dict[str, Any]], Any], timeout: float = 90.0) -> Any:
        """在浏览器线程执行 fn(context)，阻塞拿结果（界面线程调用）。"""
        if not self.ready.wait(timeout=5):
            raise WorkerTimeout("浏览器线程未就绪" + (f"：{self.error}" if self.error else ""))
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put(_Job(str(uuid.uuid4()), fn, reply))
        try:
            status, payload = reply.get(timeout=timeout)
        except queue.Empty as exc:
            raise WorkerTimeout(f"浏览器任务超时（>{timeout:.0f}s）") from exc
        if status == "error":
            raise RuntimeError(str(payload))
        return payload

    # ------------------------------------------------------------ 便捷封装
    def with_page(self, fn: Callable[[Any, dict[str, Any]], Any], timeout: float = 90.0) -> Any:
        def _wrapped(ctx: dict[str, Any]) -> Any:
            browser: Browser = ctx["browser"]
            page = ctx.get("page") or browser.page
            if page is None or page.is_closed():
                page = browser.new_tab(self.cfg.get("CHAOXING_HOME_URL"))
                ctx["page"] = page
            return fn(page, ctx)

        return self.request(_wrapped, timeout=timeout)

    def current_page(self) -> Any:
        return self.with_page(lambda page, ctx: {"url": page.url, "title": page.title()}, timeout=20)