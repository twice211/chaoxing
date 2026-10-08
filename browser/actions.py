# -*- coding: utf-8 -*-
"""
browser.actions —— 页面操作安全封装（等待、重试、多候选选择器、限速）

对外只暴露“容错版”操作，避免业务代码里散落脆弱的 selector/timeout 处理。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

from playwright.sync_api import ElementHandle, Frame, Locator, Page, TimeoutError as PWTimeout

from course.selectors import SELECTORS, selectors
from utils.logger import get_logger
from utils.retry import LoopGuardTripped, RateLimiter

log = get_logger("browser.actions")

# 全局节流：所有页面动作共用，避免高频访问（对服务器友好，也符合平台使用规范）
LIMITER = RateLimiter(min_interval=1.2)


class ElementNotFound(RuntimeError):
    """候选选择器全部未命中。"""


def configure_limiter(min_interval: float) -> None:
    LIMITER.set_interval(min_interval)


def _alive(page: Page | None) -> bool:
    try:
        return bool(page) and not page.is_closed()  # type: ignore[union-attr]
    except Exception:
        return False


def wait_first(page: Page, candidates: Sequence[str] | str, timeout: int = 8000,
               state: str = "visible") -> ElementHandle | None:
    """按候选选择器顺序等待，命中即返回元素句柄。"""
    cands = _as_list(candidates)
    step = max(300, int(timeout) // max(1, len(cands)))
    for sel in cands:
        try:
            page.wait_for_selector(sel, timeout=step, state=state)  # type: ignore[arg-type]
            el = page.query_selector(sel)
            if el:
                return el
        except PWTimeout:
            continue
        except Exception as exc:
            log.debug("选择器 %s 等待异常：%s", sel, exc)
    return None


def find_all(page: Page | Frame, candidates: Sequence[str] | str, limit: int = 500) -> list[ElementHandle]:
    """在候选选择器中找到**第一个有结果**的选择器，返回其全部匹配元素。"""
    for sel in _as_list(candidates):
        try:
            els = page.query_selector_all(sel)
        except Exception as exc:
            log.debug("选择器 %s 查询失败：%s", sel, exc)
            continue
        if els:
            return list(els[:limit])
    return []


def first_element(page: Page | Frame, candidates: Sequence[str] | str) -> ElementHandle | None:
    els = find_all(page, candidates, limit=1)
    return els[0] if els else None


def text_of(page: Page | Frame, candidates: Sequence[str] | str, default: str = "") -> str:
    el = first_element(page, candidates)
    if not el:
        return default
    try:
        txt = el.inner_text() or el.get_attribute("title") or ""
    except Exception:
        return default
    return " ".join(str(txt).split()) or default


def click_first(page: Page, candidates: Sequence[str] | str, timeout: int = 8000,
                scroll: bool = True) -> bool:
    """带节流的点击（用于进入学习项等常规操作）。"""
    el = wait_first(page, candidates, timeout=timeout)
    if not el:
        return False
    LIMITER.wait()
    try:
        if scroll:
            el.scroll_into_view_if_needed()
        el.click()
        return True
    except Exception as exc:
        log.warning("点击失败：%s", exc)
        return False


def click_text(page: Page, texts: Sequence[str] | str, per_timeout: int = 2500) -> str | None:
    """跨 iframe 按**可见文本**点击第一个匹配元素（用于切到“章节检测/测验”这类标签）。

    仅用于导航类点击；命中即返回该文本，全部落空返回 None。返回 None 表示没点到，
    调用方应据此提示，而不是静默继续。
    """
    cands = texts if isinstance(texts, (list, tuple)) else [texts]
    cands = [str(t) for t in cands if t]
    if not cands:
        return None
    for frame in frames_of(page):
        for t in cands:
            try:
                loc = frame.locator(f"text={t}")
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=per_timeout)
                return t
            except Exception as exc:
                log.debug("按文本点击“%s”失败：%s", t, exc)
    return None


def safe_goto(page: Page, url: str, attempts: int = 3, wait_until: str = "domcontentloaded",
              settle: float = 1.0) -> bool:
    """带重试的跳转：网络异常/加载失败自动退避重试。"""
    last: Exception | None = None
    for i in range(1, max(1, attempts) + 1):
        LIMITER.wait()
        try:
            page.goto(url, wait_until=wait_until)  # type: ignore[arg-type]
            time.sleep(settle)
            return True
        except Exception as exc:
            last = exc
            wait = 1.5 * i
            log.warning("第 %s/%s 次打开页面失败：%s；%.1fs 后重试", i, attempts, exc, wait)
            if _alive(page):
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
            time.sleep(wait)
    log.error("打开页面最终失败：%s（%s）", url, last)
    return False


def close_popups(page: Page, max_close: int = 3) -> int:
    """关闭遮挡的提示弹层（仅关闭已知关闭按钮，不做其他交互）。"""
    closed = 0
    for sel in selectors("popup_close"):
        if closed >= max_close:
            break
        try:
            for el in page.query_selector_all(sel)[:1]:
                if el.is_visible():
                    el.click()
                    closed += 1
                    time.sleep(0.4)
        except Exception:
            continue
    return closed


def frames_of(page: Page) -> list[Frame]:
    """主 frame + 所有子 frame（学习通小节内容常在 iframe 内）。"""
    try:
        return [f for f in page.frames if f]
    except Exception:
        return [page.main_frame]


def in_frames(page: Page, candidates: Sequence[str] | str, per_frame_timeout: int = 4000
             ) -> tuple[Frame | None, ElementHandle | None]:
    """跨 iframe 查找元素。"""
    for frame in frames_of(page):
        try:
            el = wait_first_any_frame(frame, candidates, per_frame_timeout)
        except Exception:
            el = None
        if el:
            return frame, el
    return None, None


def wait_first_any_frame(frame: Frame, candidates: Sequence[str] | str, timeout: int = 4000
                         ) -> ElementHandle | None:
    cands = _as_list(candidates)
    step = max(200, int(timeout) // max(1, len(cands)))
    for sel in cands:
        try:
            el = frame.wait_for_selector(sel, timeout=step, state="attached")
            if el:
                return el
        except Exception:
            continue
    return None


def js_eval(context: Page | Frame, script: str, arg: Any = None) -> Any:
    """执行只读 JS 探针；异常时返回 None，由调用方降级处理。"""
    try:
        return context.evaluate(script, arg) if arg is not None else context.evaluate(script)
    except Exception as exc:
        log.debug("JS 执行失败：%s", exc)
        return None


def human_pause(seconds: float = 0.5) -> None:
    time.sleep(max(0.0, float(seconds)))


def guarded(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[bool, Any]:
    """执行并吞掉异常，返回 (是否成功, 结果/异常说明)。"""
    try:
        return True, fn(*args, **kwargs)
    except LoopGuardTripped:
        raise
    except Exception as exc:
        log.debug("受保护调用失败：%s", exc, exc_info=True)
        return False, exc


def _as_list(candidates: Sequence[str] | str) -> list[str]:
    if isinstance(candidates, str):
        return SELECTORS.get(candidates, [candidates]) if candidates in SELECTORS else [candidates]
    out: list[str] = []
    for c in candidates:
        if c in SELECTORS:
            out.extend(SELECTORS[c])
        elif isinstance(c, str):
            out.append(c)
    return [c for c in out if c]