# -*- coding: utf-8 -*-
"""utils.time_util —— 时间与耗时格式化。"""

from __future__ import annotations

import datetime as _dt
import re

_CLOCK_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})")
_CN_RE = re.compile(r"(\d{1,3})\s*(?:小时|h)\s*(\d{1,2})?\s*(?:分|m)?\s*(\d{1,2})?\s*秒?")


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return _dt.datetime.now().strftime(fmt)


def today_str() -> str:
    return _dt.date.today().isoformat()


def fmt_duration(seconds: float | None) -> str:
    """3661 -> 1小时1分1秒（用于展示真实学习时长）"""
    if seconds is None:
        return "--"
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def fmt_clock(seconds: float | None) -> str:
    """倒计时只读展示：1:20:05 / 45:12。"""
    if seconds is None:
        return "--:--"
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_clock(text: str | None) -> float | None:
    """
    把页面上的“剩余时间 45:12 / 1:20:05 / 1小时20分”解析成秒。

    仅用于**只读展示**，程序绝不写回、绝不修改考试倒计时。
    """
    if not text:
        return None
    s = str(text)
    m = _CLOCK_RE.search(s)
    if m:
        h = int(m.group(1)) if m.group(1) else 0
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m2 = _CN_RE.search(s)
    if m2:
        return int(m2.group(1)) * 3600 + int(m2.group(2) or 0) * 60 + int(m2.group(3) or 0)
    return None