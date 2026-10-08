# -*- coding: utf-8 -*-
"""
utils.retry —— 重试、限速、防死循环

提供三个核心工具：
1. retry_call / @with_retry：指数退避重试，支持异常过滤与回调。
2. RateLimiter：限制相邻操作的最小间隔（既避免高频请求，也避免异常行为）。
3. LoopGuard：给“自动学习/自动刷题”这类循环加上硬性上限 + 无进展检测，
   从根本上避免死循环（这是本项目的关键要求之一）。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Tuple

from .logger import get_logger

log = get_logger("utils.retry")


class RetryExhausted(RuntimeError):
    """重试次数耗尽。"""


def retry_call(
    func: Callable[..., Any],
    *args: Any,
    attempts: int = 3,
    delay: float = 1.5,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    exceptions: Tuple[type, ...] = (Exception,),
    on_retry: Callable[[int, Exception], None] | None = None,
    name: str = "",
    **kwargs: Any,
) -> Any:
    """带指数退避的重试执行。网络异常/页面加载失败时自动重试。"""
    attempts = max(1, int(attempts))
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except exceptions as exc:  # noqa: PERF203
            last = exc
            if i >= attempts:
                break
            wait = min(max_delay, delay * (backoff ** (i - 1))) * (0.8 + 0.4 * random.random())
            tag = name or getattr(func, "__name__", "call")
            log.warning("第 %s/%s 次执行 %s 失败：%s；%.1fs 后重试", i, attempts, tag, exc, wait)
            if on_retry:
                try:
                    on_retry(i, exc)
                except Exception:  # 回调异常不影响主流程
                    log.debug("on_retry 回调异常", exc_info=True)
            time.sleep(wait)
    assert last is not None
    raise RetryExhausted(f"重试 {attempts} 次仍失败: {name or getattr(func, '__name__', 'call')}") from last


def with_retry(**kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """装饰器版本：@with_retry(attempts=3, delay=1.0)"""

    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*a: Any, **k: Any) -> Any:
            return retry_call(func, *a, **k, **kwargs)

        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__doc__ = func.__doc__
        return wrapper

    return deco


@dataclass
class RateLimiter:
    """相邻操作之间的最小间隔（秒）。"""

    min_interval: float = 1.0
    _last: float = field(default=0.0, init=False)

    def wait(self) -> float:
        now = time.monotonic()
        delta = self._last + self.min_interval - now
        if delta > 0:
            time.sleep(delta)
            self._last = time.monotonic()
        else:
            self._last = now
        return max(0.0, delta)

    def set_interval(self, seconds: float) -> None:
        self.min_interval = max(0.0, float(seconds))


@dataclass
class LoopGuard:
    """
    防死循环护栏。

    用法：
        guard = LoopGuard(max_steps=200, stall_limit=5, name="自动学习")
        while guard.step(key=当前进展指纹):
            ...
    - 超过 max_steps 直接抛错退出；
    - 连续 stall_limit 次“进展指纹”没变化，判定卡住并抛错；
    - reset() 用于人工介入后继续。
    """

    max_steps: int = 500
    stall_limit: int = 6
    name: str = "loop"
    _steps: int = 0
    _last_key: Any = object()
    _stall: int = 0

    def step(self, key: Any = None) -> bool:
        self._steps += 1
        if self._steps > self.max_steps:
            raise LoopGuardTripped(f"[{self.name}] 达到最大步数 {self.max_steps}，为避免死循环已停止")
        if key is None:
            return True
        if key == self._last_key:
            self._stall += 1
            if self._stall >= self.stall_limit:
                raise LoopGuardTripped(f"[{self.name}] 连续 {self._stall} 次无进展（{key}），已停止")
        else:
            self._stall = 0
            self._last_key = key
        return True

    @property
    def steps(self) -> int:
        return self._steps

    def reset(self) -> None:
        self._steps = 0
        self._stall = 0
        self._last_key = object()


class LoopGuardTripped(RuntimeError):
    """循环护栏触发。"""


def human_join(items: Iterable[str], sep: str = "、") -> str:
    items = [str(x) for x in items if str(x).strip()]
    return sep.join(items) if items else ""