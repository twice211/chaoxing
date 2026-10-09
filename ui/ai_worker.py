"""Bounded daemon workers: abandoned HTTP calls cannot block desktop shutdown."""
from __future__ import annotations

import copy
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Generic, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


@dataclass
class _Job(Generic[T]):
    future: Future[T]
    call: Callable[[], T]


class AIWorker:
    def __init__(self, workers: int = 4, pending: int = 16) -> None:
        self._jobs: queue.Queue[_Job[Any]] = queue.Queue(maxsize=pending)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._workers = workers

    def submit(self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("AI 工作线程已关闭")
            future: Future[T] = Future()
            try:
                self._jobs.put_nowait(_Job(future, partial(fn, *args, **kwargs)))
            except queue.Full as exc:
                raise RuntimeError("AI 请求仍在处理，请稍后重试") from exc
            if len(self._threads) < self._workers:
                thread = threading.Thread(target=self._run, name="desktop-ai", daemon=True)
                self._threads.append(thread)
                thread.start()
            return future

    def _run(self) -> None:
        while not self._closed.is_set() or not self._jobs.empty():
            try:
                job = self._jobs.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                if job.future.set_running_or_notify_cancel():
                    try:
                        job.future.set_result(job.call())
                    except BaseException as exc:
                        job.future.set_exception(exc)
            finally:
                self._jobs.task_done()
                del job

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        with self._lock:
            self._closed.set()
            while cancel_futures:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                job.future.cancel()
                self._jobs.task_done()
        if wait:
            for thread in self._threads:
                thread.join()


class CapturedConfig:
    """Copy settings and resolved environment overrides at job acceptance."""
    def __init__(self, cfg: Any) -> None:
        self._cfg = copy.copy(cfg)
        if hasattr(cfg, "values"):
            self._cfg.values = copy.deepcopy(cfg.values)
        self.ai_api_key = cfg.ai_api_key
        self.ai_model = cfg.ai_model
        self.ai_base_url = cfg.ai_base_url

    def get(self, key: str, default: Any = None) -> Any:
        return self._cfg.get(key, default)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cfg, name)


class CallLedger:
    """Plain call records; only the scheduler persists accepted completions."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, float, int, str]] = []

    def log_ai_call(self, purpose: str, model: str, status: str, ms: float, chars: int,
                    error: str = "") -> None:
        self.calls.append((purpose, model, status, ms, chars, error))


def capture_engine(engine: Any, cfg: Any, ledger: CallLedger,
                   cancelled: threading.Event | None = None) -> Any:
    from ai.client import AIClient
    from ai.responder import AnswerEngine
    if not isinstance(engine, AnswerEngine) or not isinstance(engine.ai, AIClient):
        return engine
    captured = CapturedConfig(cfg)
    return AnswerEngine(AIClient(captured, store=ledger, cancel_event=cancelled), captured)
